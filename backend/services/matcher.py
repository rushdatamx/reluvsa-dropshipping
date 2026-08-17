"""
Matcher de conceptos de factura ↔ ventas ML.

Estrategia (en orden, el primero que acierta gana):
0. ⭐ **# de venta impreso en el PDF (SÓLO KIM).** KIM escribe en su factura a qué venta
   corresponde. Eso es EVIDENCIA del proveedor; los pasos 1-4 son inferencias nuestras,
   así que cuando el número existe manda sobre todos ellos. Ver `services/num_venta_pdf`.
   🔴 Si KIM puso un número y no resuelve, NO se cruza: el dato está corrupto y adivinar
   por fecha sería contradecir lo que el propio proveedor declaró.
1. Match exacto por código: SKU de la venta == NoIdentificacion del concepto, o substring.
2. Match por ID interno normalizado: cada proveedor usa su propio esquema de SKU y el
   código de la factura no es idéntico al SKU de ML. Ej. CAUPLAS vende 'CAU2692' pero
   factura '2692  M2626339' — el ID interno '2692' es la llave común. Extraemos los
   tokens (numéricos y código M) de ambos lados y cruzamos por intersección. Sin esto
   CAUPLAS daba 0 matches (sube a ~14/28 en datos reales).
3. Match por componente de kit: si la venta es un kit (su SKU está en kit_componentes),
   el proveedor NO factura el SKU-kit sino sus componentes reales. Cruzamos el código del
   concepto contra los componentes del kit, primero por texto (exacto/substring, tolera
   sufijos -K) y luego por ID interno normalizado — el Excel de kits trae 'CAU11370' donde
   la factura dice '11370 M2650963'. Si el componente vive en varios kits, desempata la
   descripción contra el título de la venta.
4. Fuzzy por descripción contra el título de la venta (>= 0.6 = aceptamos con confidence).
"""
import re
from typing import Optional

from rapidfuzz import fuzz, process

from database import UPLOADS_DIR
from services.envio_pack import ENVIO_CUBRE_VENTA
from services.num_venta_pdf import (
    CODIGO_BODEGA_KIM,
    CONFIANZA as CONFIANZA_NUM_IMPRESO,
    METODO as METODO_NUM_IMPRESO,
    extraer_numeros_pdf,
    resolver_num_venta_impreso,
    resolver_pdf,
)

# Misma carpeta que usa `routers/facturas.py` (volumen persistente). Se deriva de
# UPLOADS_DIR y no se importa del router: un service que importa de un router invierte
# la dependencia y crea un import circular en cuanto el router importe el matcher.
FACTURAS_DIR = UPLOADS_DIR / "facturas"

CONFIDENCE_MIN_FUZZY = 0.6

# ⭐ Ventana de facturación: cuántos días DESPUÉS de la venta puede emitirse su factura.
#
# El filtro que de verdad importa es el otro extremo (una venta POSTERIOR a la factura no
# puede ser la facturada); este tope sólo evita colgarle a una factura una venta de hace
# medio año cuando la correcta ya se consumió.
#
# Medido contra el cruce manual de Gaby (2026-08-07, 103 pares correctos): CAUPLAS factura
# entre 0 y 3 días después de la venta, KIM entre 0 y 5. Ni un solo caso por encima de 5.
# 45 deja holgura de sobra sin abrir la puerta a cruces absurdos. Medido: el resultado es
# IDÉNTICO con cualquier valor entre 5 y 30 — lo que decide es la regla de no-futuras, no
# este número, así que no hace falta afinarlo.
VENTANA_FACTURACION_DIAS = 45

# Términos distintivos (ver `_afinidad_titulo`) que el mejor título debe sacarle al
# segundo para desempatar entre varias ventas-kit que comparten el mismo componente.
# No es un umbral de parecido: es la distancia mínima para que la elección no sea azar.
# Con 1 basta — un solo término propio ('VENTO' vs 'CHEVY') ya identifica el vehículo.
MARGEN_DESEMPATE_KIT = 1


def _filtro_fecha(fecha_factura: Optional[str]):
    """Devuelve (sql, params) para acotar las ventas candidatas por la fecha de la factura.

    ⭐ EL ARREGLO DE FONDO (2026-08-07). Antes, los 4 pasos elegían candidata sólo con
    `ORDER BY v.fecha_venta DESC LIMIT 1`, sin mirar nunca la fecha de la FACTURA. Cuando
    un SKU se vendía varias veces, la factura se iba a una venta arbitraria — medido contra
    el cruce manual de Gaby, KIM acertaba el 27%, y en 17 casos el portal marcaba
    "✓ Facturado" sobre mercancía que ni siquiera había salido.

    Dos condiciones, en orden de importancia:

    1. **La venta no puede ser POSTERIOR a la factura.** Nadie factura lo que todavía no ha
       vendido. Es la regla que hace el trabajo: por sí sola sube los aciertos de 26 a 76
       sobre los 143 casos de Gaby. En la BD de prod había 237 conceptos KIM cruzados a una
       venta hasta 45 días posterior a su propia factura — imposibles, todos falsos.
    2. **Ni más vieja que `VENTANA_FACTURACION_DIAS`**, para no rescatar una venta de hace
       meses cuando la correcta ya se consumió.

    Con `fecha_factura=None` no filtra nada y el comportamiento es el de siempre. Eso
    mantiene compatibles a los tests existentes y a cualquier llamador que no tenga la
    fecha a mano; el filtro es una mejora aditiva, nunca un requisito.

    Se compara con `date()` (no el timestamp) porque `fecha_venta` viene de ML con hora y
    `fecha_factura` del XML: una factura emitida a las 10:00 del mismo día en que se vendió
    a las 14:00 es legítima, y comparar timestamps la descartaría.
    """
    if not fecha_factura:
        return "", []
    return (
        " AND date(v.fecha_venta) <= date(?) "
        " AND date(v.fecha_venta) >= date(?, '-' || ? || ' days') ",
        [fecha_factura, fecha_factura, VENTANA_FACTURACION_DIAS],
    )


def _tokens_codigo(s: str) -> set:
    """Extrae los tokens significativos de un código de SKU o NoIdentificacion para
    comparar entre esquemas distintos. Toma el ID interno numérico (>=3 dígitos, para
    no confundir con cantidades) y los códigos tipo 'M2626339'. Ignora prefijos
    alfabéticos de bodega (CAU, VAZLO-, etc.).

    'CAU2692'         -> {'2692'}
    '2692  M2626339'  -> {'2692', 'M2626339'}
    '23530559-Z'      -> {'23530559'}
    'VAZLO-30-257'    -> {'30', '257'}  (números de 2 díg. se incluyen pero rara vez chocan)
    """
    if not s:
        return set()
    up = s.upper()
    toks = set()
    # Códigos tipo M2626339 (letra + >=5 dígitos): identificador de pieza CAUPLAS.
    toks.update(re.findall(r"[A-Z]\d{5,}", up))
    # IDs numéricos largos (>=3 dígitos) — el ID interno de la pieza.
    toks.update(re.findall(r"\d{3,}", up))
    return toks


def _match_por_id_interno(conn, proveedor_id: int, codigo: str,
                          fecha_factura: Optional[str] = None) -> Optional[dict]:
    """Cruza el código de la factura contra los SKU de las ventas del proveedor
    comparando tokens de ID interno (no string completo)."""
    cod_tokens = _tokens_codigo(codigo)
    if not cod_tokens:
        return None
    f_sql, f_par = _filtro_fecha(fecha_factura)
    candidates = conn.execute(
        f"""SELECT v.num_venta, v.sku
           FROM ventas_ml v
           JOIN envios_colecta e ON {ENVIO_CUBRE_VENTA}
           LEFT JOIN factura_conceptos fc ON fc.num_venta_match = v.num_venta
           WHERE e.proveedor_id = ?
             AND fc.id IS NULL
             AND v.sku IS NOT NULL
             {f_sql}
           ORDER BY v.fecha_venta DESC, v.num_venta DESC
           LIMIT 1000""",
        (proveedor_id, *f_par),
    ).fetchall()
    for c in candidates:
        sku_tokens = _tokens_codigo(c["sku"])
        if cod_tokens & sku_tokens:
            return {"num_venta": c["num_venta"], "method": "codigo_id_interno", "confidence": 0.9}
    return None


def _tokens_componente(s: str) -> set:
    """Tokens de un componente del Excel de kits, quitando el prefijo de bodega.

    ⚠️ No se puede usar `_tokens_codigo` tal cual: su regex de códigos-M (`[A-Z]\\d{5,}`)
    se come la última letra del prefijo y saca un token fantasma —
    'CAU11370' -> {'11370', 'U11370'} — que la factura nunca trae, así que la prueba de
    subconjunto fallaría siempre. Aquí se recorta el prefijo alfabético de bodega ANTES
    de tokenizar ('CAU11370' -> '11370'), que es justo el esquema del Excel de Gaby.

    `_tokens_codigo` NO se toca: lo comparten los pasos 1 y 2, ya en producción.
    """
    if not s:
        return set()
    limpio = re.sub(r"^[A-Z]{2,6}(?=\d)", "", s.strip().upper())
    return _tokens_codigo(limpio)


def _componente_cruza(comp_tokens: set, cod_tokens: set) -> bool:
    """¿El componente de un kit corresponde al código de este concepto de factura?

    Compara por ID interno normalizado, igual que `_match_por_id_interno`, porque el
    Excel de kits de Gaby y la factura del proveedor usan esquemas distintos para la
    MISMA pieza. El caso real que motivó esto (CAUPLAS, factura 970096331):

        Excel de Gaby   -> 'CAU11370'          tokens {'11370'}
        Factura CAUPLAS -> '11370  M2650963'   tokens {'11370', 'M2650963'}

    Dos guardas contra falsos positivos, que aquí importan más que en el paso 2 porque
    un kit tiene varios componentes y por tanto más superficie de choque:

    1. **Subconjunto, no intersección**: TODOS los tokens del componente deben estar en
       el código del concepto. Con intersección, 'VAZLO-30-257' (tokens 30, 257) cruzaría
       con cualquier concepto que mencione un 30 suelto.
    2. **Al menos un token de >= 4 caracteres**: un componente cuyo único token sea '409'
       es demasiado genérico — medido en prod, ese token solo aparece en 21 kits distintos.
       Sin esta guarda se cruzarían facturas a kits equivocados, que es peor que dejarlas
       pendientes: un cruce falso le dice a Gaby que algo está facturado cuando no lo está.
    """
    if not comp_tokens or not cod_tokens:
        return False
    if not any(len(t) >= 4 for t in comp_tokens):
        return False
    return comp_tokens <= cod_tokens


# Palabras que aparecen en casi todos los títulos/descripciones de refacciones y por
# tanto no distinguen un kit de otro. Se ignoran al desempatar.
_PALABRAS_GENERICAS = frozenset({
    "KIT", "KITS", "DE", "DEL", "LA", "EL", "LOS", "LAS", "P", "PARA", "CON", "Y",
    "COMPATIBLE", "JUEGO", "SET", "PZA", "PZAS", "PIEZAS", "MANGUERA", "MANGUERAS",
    "RAD", "SUP", "INF", "CALEF", "PASO", "S", "T", "M", "L", "A",
})


def _terminos_distintivos(texto: str) -> set:
    """Palabras que de verdad identifican el vehículo/pieza: sin genéricas ni medidas.

    'Kit Mangueras Radiador Inf/sup P/ Vw Vento 1.6' -> {'RADIADOR', 'VW', 'VENTO'}
    """
    if not texto:
        return set()
    palabras = re.split(r"[^A-Za-z0-9]+", texto.upper())
    return {
        p for p in palabras
        if len(p) >= 2 and p not in _PALABRAS_GENERICAS and not p.isdigit()
    }


def _afinidad_titulo(descripcion: str, titulo: str) -> int:
    """Cuántos términos distintivos comparten la descripción de la factura y el título.

    ⚠️ Sustituye a `token_set_ratio` SÓLO para desempatar entre kits candidatos. Medido
    con datos reales, el ratio difuso diluye la señal entre las palabras genéricas
    ('Kit', 'mangueras', '1.6') y deja márgenes de ~2 puntos aunque el ganador sea
    evidente: 'NSN PLATINA ... RAD SUP' daba 27.3 contra el kit de Platina y 25.0 contra
    el de un Clio. Contar términos distintivos da 1 vs 0 — una señal limpia.
    """
    return len(_terminos_distintivos(descripcion) & _terminos_distintivos(titulo))


def _match_por_kit(conn, proveedor_id: int, codigo: str,
                   descripcion: str = "",
                   fecha_factura: Optional[str] = None) -> Optional[dict]:
    """Cruza el código del concepto contra los COMPONENTES de un kit.

    Una venta-kit tiene un SKU sintético ('KIT0337') que el proveedor nunca factura:
    factura los componentes ('KDTL-057'...). Buscamos una venta del proveedor cuyo SKU
    sea un kit que tenga este código como componente.

    Dos formas de cruzar el código, en orden:
      a) **Texto**: exacto o substring en ambos sentidos. Tolera que la factura traiga
         'KDTL-057' y el kit 'KDTL-057-K' (el sufijo -K del Excel; sólo 8 de 1859
         relaciones en prod, pero es gratis conservarlo).
      b) **ID interno normalizado** (`_componente_cruza`): el caso masivo — 798 de 1859
         componentes usan el formato 'CAU11370' contra el '11370 M2650963' de la factura.

    ⚠️ Un mismo componente pertenece legítimamente a VARIOS kits (una manguera va en
    muchos kits), así que (b) puede devolver más de un kit candidato. Medido en prod:
    39 conceptos resuelven a un kit único y 36 quedan entre 2. Cuando hay empate se
    desempata por **descripción** contra el título de la venta, que es justo el dato que
    distingue 'VW VENTO' de 'HYUNDAI GRAND i10'. Si ni así hay un ganador claro se
    devuelve None: ante la duda NO se cruza (ver el comentario del final de la función).
    """
    if not codigo:
        return None

    # (a) Cruce por texto — barato y el más específico; se resuelve en SQL.
    #     ⚠️ El componente debe medir >= 4 caracteres para entrar por substring. Sin esa
    #     guarda, un componente corto como '409' cruza contra CUALQUIER concepto que lo
    #     contenga ('409  M2650963'), y en prod ese código vive en 21 kits distintos.
    f_sql, f_par = _filtro_fecha(fecha_factura)
    row = conn.execute(
        f"""SELECT v.num_venta
           FROM ventas_ml v
           JOIN envios_colecta e ON {ENVIO_CUBRE_VENTA}
           JOIN kit_componentes kc ON kc.kit_sku = UPPER(TRIM(v.sku))
           LEFT JOIN factura_conceptos fc ON fc.num_venta_match = v.num_venta
           WHERE e.proveedor_id = ?
             AND fc.id IS NULL
             AND ( kc.componente_codigo = ?
                   OR ( LENGTH(TRIM(kc.componente_codigo)) >= 4
                        AND ( ? LIKE '%' || kc.componente_codigo || '%'
                              OR kc.componente_codigo LIKE '%' || ? || '%' ) ) )
             {f_sql}
           ORDER BY v.fecha_venta DESC, v.num_venta DESC
           LIMIT 1""",
        (proveedor_id, codigo, codigo, codigo, *f_par),
    ).fetchone()
    if row:
        return {"num_venta": row["num_venta"], "method": "kit_componente", "confidence": 0.95}

    # (b) Cruce por ID interno normalizado.
    cod_tokens = _tokens_codigo(codigo)
    if not cod_tokens:
        return None

    candidatos = conn.execute(
        f"""SELECT v.num_venta, v.titulo, v.sku, kc.componente_codigo
           FROM ventas_ml v
           JOIN envios_colecta e ON {ENVIO_CUBRE_VENTA}
           JOIN kit_componentes kc ON kc.kit_sku = UPPER(TRIM(v.sku))
           LEFT JOIN factura_conceptos fc ON fc.num_venta_match = v.num_venta
           WHERE e.proveedor_id = ?
             AND fc.id IS NULL
             {f_sql}
           ORDER BY v.fecha_venta DESC, v.num_venta DESC
           LIMIT 2000""",
        (proveedor_id, *f_par),
    ).fetchall()

    # Ya vienen ordenados por fecha desc: el primero de cada venta gana el desempate
    # por antigüedad sin necesidad de reordenar.
    vistos = set()
    matches = []
    for c in candidatos:
        if c["num_venta"] in vistos:
            continue
        if _componente_cruza(_tokens_componente(c["componente_codigo"]), cod_tokens):
            vistos.add(c["num_venta"])
            matches.append(c)

    if not matches:
        return None

    # ¿La ambigüedad es entre KITS DISTINTOS, o es el mismo kit vendido varias veces?
    # Son casos muy diferentes:
    #   - Mismo kit repetido (lo habitual: 6 ventas de 'KIT0454' de Chevy): el producto no
    #     está en duda, sólo a cuál de las ventas idénticas aplicar esta factura. La más
    #     reciente sin facturar es una respuesta válida — misma heurística que los pasos 1 y 2.
    #   - Kits distintos ('KIT-PLAT' vs 'KIT-CLIO'): aquí sí se puede cruzar la factura al
    #     producto equivocado, y hace falta que la descripción lo decida.
    kits_distintos = {(c["sku"] or "").strip().upper() for c in matches}

    if len(matches) == 1 or len(kits_distintos) == 1:
        # El kit es el correcto; sólo falta a cuál de sus ventas aplicar la factura.
        # RELUVSA publica el MISMO kit con títulos distintos según el coche compatible
        # (visto en prod: 'KIT03565' aparece como Platina, Clio y Kangoo — comparten
        # motor Renault). Si la descripción apunta a uno de esos títulos, se prefiere ésa
        # antes que la más reciente; así la factura queda en la venta que le corresponde.
        elegido = matches[0]
        if descripcion and len(matches) > 1:
            mejor = max(matches, key=lambda c: _afinidad_titulo(descripcion, c["titulo"]))
            if _afinidad_titulo(descripcion, mejor["titulo"]) > 0:
                elegido = mejor
        return {"num_venta": elegido["num_venta"],
                "method": "kit_componente", "confidence": 0.95}

    # Empate: varias ventas-kit del proveedor contienen este componente. La descripción
    # del concepto ('VW VENTO 1.6L RAD INF') distingue contra el título de la venta.
    #
    # ⚠️ Aquí NO aplica CONFIDENCE_MIN_FUZZY (0.6). Ese umbral existe para decidir si una
    # descripción identifica una venta entre CIENTOS de candidatas (paso 4). En este punto
    # el código ya acotó a 2-3 ventas que contienen el componente, así que la pregunta no
    # es "¿se parece lo bastante?" sino "¿cuál de estas dos?". Los títulos de ML son
    # ruidosos ('Kit Mangueras Radiador Inf/sup P/ Vw Vento 1.6') y contra una descripción
    # de factura ('VW VENTO 1.6L 2014-2021 RAD SUP T/M') el token_set_ratio ronda 30 aunque
    # sea el correcto — con el umbral de 0.6 el desempate no se usaría nunca.
    # Lo que sí se exige es un MARGEN sobre el segundo mejor: sin margen, elegir sería azar.
    if descripcion and len(matches) > 1:
        puntajes = sorted(
            ((_afinidad_titulo(descripcion, c["titulo"]), i)
             for i, c in enumerate(matches)),
            reverse=True,
        )
        (mejor, idx), (segundo, _) = puntajes[0], puntajes[1]
        if mejor > 0 and (mejor - segundo) >= MARGEN_DESEMPATE_KIT:
            elegido = matches[idx]
            return {"num_venta": elegido["num_venta"],
                    "method": "kit_componente", "confidence": 0.9}

    # KITS DISTINTOS y la descripción no decide: NO se elige. Una versión previa devolvía
    # aquí la venta más reciente, y medido contra la BD de prod producía un falso positivo
    # real — un concepto 'NSN PLATINA 1.6L RAD SUP' cruzado al kit de un Clio (comparten
    # plataforma, y por eso comparten componentes; el código solo no los distingue).
    #
    # Colgarle a Gaby una factura equivocada es PEOR que dejar el concepto pendiente: el
    # pendiente se ve y se corrige, el cruce falso dice "ya está facturado" y nadie lo
    # vuelve a mirar. Se prefiere no adivinar.
    return None


def _match_por_num_impreso(conn, proveedor_id: int, factura: dict,
                           fecha_factura: Optional[str]) -> Optional[dict]:
    """Paso 0: el # de venta que KIM imprime en el PDF. Devuelve dict, "CORRUPTO" o None.

    Tres desenlaces, y la diferencia entre los dos últimos es la clave del diseño:

    - dict          -> el número resuelve a UNA venta con evidencia. Se cruza ahí.
    - "CORRUPTO"    -> KIM SÍ puso número pero no resuelve (ceros irrecuperables, SKU que
                       no cuadra, o varias candidatas). 🔴 El llamador NO debe seguir a
                       los pasos 1-4: el proveedor ya declaró cuál es la venta y ese dato
                       no cuadra, así que cruzar por fecha sería adivinar CONTRA la
                       evidencia. Queda pendiente y visible.
    - None          -> no hay número que leer (sin PDF, PDF ilegible, o KIM no lo puso en
                       esta factura). Se sigue normal: hoy ~45% de sus facturas están así
                       y dejarlas pendientes le crearía a Gaby trabajo manual que no tiene.
    """
    if not factura:
        return None
    # ⚠️ EXCLUSIVO DE KIM: es el único proveedor que imprime el número, y la tolerancia a
    # ceros es un error de captura observado sólo en él (mandato de Mario).
    if (factura.get("codigo_bodega") or "").strip().upper() != CODIGO_BODEGA_KIM:
        return None

    # Varios números distintos en un PDF es ambiguo: no sabemos cuál es la venta. Se
    # trata como ausencia, no como corrupción — puede ser que la plantilla de KIM traiga
    # otro número largo que aún no conocemos, y no queremos bloquear la factura por eso.
    impreso = _num_impreso_cacheado(conn, factura.get("id"), factura.get("pdf_path"))
    if not impreso:
        return None

    codigos = factura.get("codigos") or []
    num_venta = resolver_num_venta_impreso(
        conn, proveedor_id, impreso, codigos, fecha_factura
    )
    if not num_venta:
        return "CORRUPTO"

    return {"num_venta": num_venta, "method": METODO_NUM_IMPRESO,
            "confidence": CONFIANZA_NUM_IMPRESO}


def match_conceptos_a_ventas(conn, proveedor_id: int, concepto: dict,
                             fecha_factura: Optional[str] = None,
                             factura: Optional[dict] = None) -> Optional[dict]:
    """Cruza un concepto de factura a la venta que le corresponde.

    `fecha_factura` (opcional, ISO) acota las candidatas a las ventas que ya existían
    cuando se emitió la factura — ver `_filtro_fecha`. Es lo que impide que la factura se
    vaya a una venta arbitraria cuando el mismo SKU se vendió varias veces. Se pasa desde
    `routers/facturas.py` y desde el recruce; omitirla conserva el comportamiento anterior.

    `factura` (opcional) habilita el PASO 0 — el # de venta impreso en el PDF de KIM. Es
    un dict `{codigo_bodega, pdf_path, codigos}`. Omitirlo conserva el comportamiento
    anterior exacto, por eso los tests y scripts viejos siguen valiendo sin tocarse.
    """
    codigo = (concepto.get("codigo") or "").strip()
    descripcion = (concepto.get("descripcion") or "").strip()
    f_sql, f_par = _filtro_fecha(fecha_factura)

    # 0) El # de venta impreso por el proveedor gana sobre cualquier inferencia nuestra.
    por_impreso = _match_por_num_impreso(conn, proveedor_id, factura, fecha_factura)
    if por_impreso == "CORRUPTO":
        return None
    if por_impreso:
        return por_impreso

    # 1) Match exacto por código contra SKU
    if codigo:
        row = conn.execute(
            f"""SELECT v.num_venta
               FROM ventas_ml v
               JOIN envios_colecta e ON {ENVIO_CUBRE_VENTA}
               LEFT JOIN factura_conceptos fc ON fc.num_venta_match = v.num_venta
               WHERE e.proveedor_id = ?
                 AND fc.id IS NULL
                 AND (v.sku = ? OR v.sku LIKE ?)
                 {f_sql}
               ORDER BY v.fecha_venta DESC, v.num_venta DESC
               LIMIT 1""",
            (proveedor_id, codigo, f"%{codigo}%", *f_par),
        ).fetchone()
        if row:
            return {"num_venta": row["num_venta"], "method": "codigo_exact", "confidence": 1.0}

        # 2) Match por ID interno normalizado (esquemas de SKU distintos por proveedor)
        por_id = _match_por_id_interno(conn, proveedor_id, codigo, fecha_factura)
        if por_id:
            return por_id

        # 3) Match por componente de kit (la venta-kit factura sus componentes, no el SKU-kit).
        #    La descripción va como desempate cuando el componente vive en varios kits.
        por_kit = _match_por_kit(conn, proveedor_id, codigo, descripcion, fecha_factura)
        if por_kit:
            return por_kit

    if not descripcion:
        return None

    # 4) Fuzzy match contra títulos de ventas del proveedor aún sin facturar
    candidates = conn.execute(
        f"""SELECT v.num_venta, v.titulo
           FROM ventas_ml v
           JOIN envios_colecta e ON {ENVIO_CUBRE_VENTA}
           LEFT JOIN factura_conceptos fc ON fc.num_venta_match = v.num_venta
           WHERE e.proveedor_id = ?
             AND fc.id IS NULL
             AND v.titulo IS NOT NULL
             {f_sql}
           ORDER BY v.fecha_venta DESC, v.num_venta DESC
           LIMIT 500""",
        (proveedor_id, *f_par),
    ).fetchall()

    if not candidates:
        return None

    titles = [c["titulo"] for c in candidates]
    best = process.extractOne(descripcion, titles, scorer=fuzz.token_set_ratio)
    if not best:
        return None

    title, score, idx = best
    conf = score / 100.0
    if conf < CONFIDENCE_MIN_FUZZY:
        return None

    return {
        "num_venta": candidates[idx]["num_venta"],
        "method": "fuzzy_titulo",
        "confidence": round(conf, 3),
    }


def recruzar_conceptos_sin_match(conn) -> dict:
    """Re-intenta el match de los conceptos de factura que quedaron SIN cruzar.

    El match concepto->venta se calcula una sola vez, al subir la factura. Si la
    factura entró ANTES que su venta (el proveedor factura rápido; Gaby sube el
    reporte de ventas por lotes), o antes de que la colecta le asignara proveedor al
    envío, el concepto quedó con num_venta_match = NULL aunque su venta ya exista.

    Esta función corre tras subir ventas/colecta: toma cada concepto sin cruzar,
    reconstruye su dict y reintenta match_conceptos_a_ventas con el proveedor de su
    factura. Si ahora cruza, actualiza el concepto. Idempotente: lo que sigue sin
    cruzar se queda en NULL para el siguiente intento.

    ⭐ ADEMÁS (2026-08-12) CORRIGE los cruces por INFERENCIA de KIM cuando aparece el
    # de venta impreso. Hasta hoy esta función "sólo enriquecía, nunca rompía un match
    existente", y esa regla dejaba abierto el hueco que motivó todo esto: si el
    proveedor sube el XML hoy y el PDF mañana, el concepto YA cruzó por fecha —
    posiblemente a la venta equivocada y con confianza 1.0 — y nadie lo reintentaba.
    Justo el estado que produjo los 216 cruces falsos que se corrigieron a mano.

    🔴 Lo que se corrige está acotado a propósito: SÓLO se reescribe un cruce cuyo
    método NO sea ya `num_venta_proveedor`, y SÓLO si el # impreso resuelve con las 3
    reglas. O sea, la evidencia del proveedor le gana a nuestra inferencia, pero jamás
    se pisa una evidencia con otra ni se degrada un cruce a algo más débil.
    """
    pendientes = conn.execute(
        """SELECT fc.id, fc.codigo_prov, fc.descripcion, f.proveedor_id, f.fecha_factura
           FROM factura_conceptos fc
           JOIN facturas f ON f.id = fc.factura_id
           WHERE fc.num_venta_match IS NULL"""
    ).fetchall()

    recruzados = 0
    for c in pendientes:
        if c["proveedor_id"] is None:
            continue
        # La fecha de la factura acota las candidatas igual que en el alta. Sin ella, el
        # recruce reintroduciría por la puerta de atrás los cruces que el filtro evita:
        # corre tras cada carga de ventas/colecta, justo cuando entran ventas NUEVAS que
        # son posteriores a facturas viejas y por tanto no pueden ser suyas.
        match = match_conceptos_a_ventas(
            conn, c["proveedor_id"],
            {"codigo": c["codigo_prov"], "descripcion": c["descripcion"]},
            fecha_factura=c["fecha_factura"],
            factura=_ctx_factura(conn, c["id"]),
        )
        if match:
            conn.execute(
                """UPDATE factura_conceptos
                   SET num_venta_match = ?, match_method = ?, match_confidence = ?
                   WHERE id = ?""",
                (match["num_venta"], match["method"], match["confidence"], c["id"]),
            )
            recruzados += 1

    corregidos = _corregir_por_num_impreso(conn)

    return {"conceptos_sin_match": len(pendientes), "conceptos_recruzados": recruzados,
            "conceptos_corregidos_por_pdf": corregidos}


def _ctx_factura(conn, concepto_id: int) -> Optional[dict]:
    """Arma el contexto del paso 0 para un concepto: bodega, PDF y códigos hermanos.

    Devuelve None ante cualquier ausencia — sin PDF, sin proveedor, o un esquema que no
    tenga las columnas. El paso 0 es un ENRIQUECIMIENTO: si no se puede armar, el cruce
    sigue por los pasos 1-4 como siempre. Nunca debe tumbar el recruce, que corre dentro
    de la transacción de subir ventas/colecta.
    """
    try:
        f = conn.execute(
            """SELECT f.id, f.pdf_path, p.codigo_bodega
               FROM factura_conceptos fc
               JOIN facturas f ON f.id = fc.factura_id
               LEFT JOIN proveedores p ON p.id = f.proveedor_id
               WHERE fc.id = ?""",
            (concepto_id,),
        ).fetchone()
    except Exception:
        return None
    if not f or not f["pdf_path"]:
        return None
    codigos = [r["codigo_prov"] for r in conn.execute(
        "SELECT codigo_prov FROM factura_conceptos WHERE factura_id = ?", (f["id"],)
    ) if r["codigo_prov"]]
    return {"id": f["id"], "codigo_bodega": f["codigo_bodega"],
            "pdf_path": f["pdf_path"], "codigos": codigos}


def _num_impreso_cacheado(conn, factura_id: Optional[int], pdf_path: Optional[str]
                          ) -> Optional[str]:
    """Devuelve el # impreso de una factura, leyendo el PDF UNA sola vez en la vida.

    Abrir un PDF cuesta ~200 ms y el recruce corre tras cada carga de ventas/colecta,
    dentro de su transacción. Sin caché serían cientos de aperturas por corrida (medido
    en prod: 548 facturas), y la mayoría sin número — trabajo puro, repetido para
    siempre. Se persiste en `facturas.num_venta_pdf`.

    Distingue "" (leído, no trae número) de NULL (sin leer): ver la migración. Devuelve
    None cuando no hay número utilizable.
    """
    if factura_id is not None:
        try:
            row = conn.execute(
                "SELECT num_venta_pdf FROM facturas WHERE id = ?", (factura_id,)
            ).fetchone()
        except Exception:
            row = None
        if row is not None and row["num_venta_pdf"] is not None:
            return row["num_venta_pdf"] or None       # '' -> None, sin releer el PDF

    ruta = resolver_pdf(pdf_path, str(FACTURAS_DIR))
    if not ruta:
        return None                                    # no se cachea: el PDF puede llegar

    numeros = extraer_numeros_pdf(ruta)
    # Varios números distintos es ambiguo: se trata como ausencia (puede ser otro dato
    # largo de la plantilla), pero SÍ se cachea — releerlo daría lo mismo.
    valor = numeros[0] if len(numeros) == 1 else ""
    if factura_id is not None:
        try:
            conn.execute("UPDATE facturas SET num_venta_pdf = ? WHERE id = ?",
                         (valor, factura_id))
        except Exception:
            pass
    return valor or None


def _corregir_por_num_impreso(conn) -> int:
    """Reescribe los cruces por inferencia de KIM cuando el PDF trae el # de venta.

    Cierra el hueco del PDF que llega DESPUÉS del XML. Sólo mira facturas de KIM con PDF
    que tengan algún concepto cruzado por un método distinto de `num_venta_proveedor`;
    una vez corregida, la factura deja de entrar en el conjunto (idempotente).

    ⚠️ Y se excluyen las que ya se leyeron y NO traen número (`num_venta_pdf = ''`). Sin
    eso el recruce reabriría cientos de PDF en cada corrida para no corregir nada: en
    prod entrarían 548 facturas de KIM y ~45% no trae número. Ése es el trabajo que la
    caché elimina — no el de las que sí lo traen, que se corrigen una vez y salen solas.

    ⚠️ Un concepto cuyo cruce ya coincide con la venta impresa se ACTUALIZA igual (para
    sellar el método y la confianza), pero no se cuenta como corregido: contar ahí haría
    que el contador nunca llegara a cero y pareciera que el recruce no converge.
    """
    try:
        facturas = conn.execute(
            """SELECT DISTINCT f.id, f.pdf_path, f.fecha_factura, f.proveedor_id
                FROM facturas f
                JOIN proveedores p ON p.id = f.proveedor_id
                JOIN factura_conceptos fc ON fc.factura_id = f.id
                WHERE p.codigo_bodega = ?
                  AND f.pdf_path IS NOT NULL
                  AND COALESCE(f.num_venta_pdf, 'x') != ''
                  AND fc.num_venta_match IS NOT NULL
                  AND COALESCE(fc.match_method, '') != ?""",
            (CODIGO_BODEGA_KIM, METODO_NUM_IMPRESO),
        ).fetchall()
    except Exception:
        # Esquema sin las columnas (tests con tablas mínimas): no hay nada que corregir.
        return 0

    corregidos = 0
    for f in facturas:
        impreso = _num_impreso_cacheado(conn, f["id"], f["pdf_path"])
        if not impreso:
            continue

        conceptos = conn.execute(
            """SELECT id, codigo_prov, num_venta_match, match_method
               FROM factura_conceptos WHERE factura_id = ?""",
            (f["id"],),
        ).fetchall()
        codigos = [c["codigo_prov"] for c in conceptos if c["codigo_prov"]]

        real = resolver_num_venta_impreso(
            conn, f["proveedor_id"], impreso, codigos, f["fecha_factura"]
        )
        if not real:
            # KIM puso un número que no resuelve. NO se toca lo que ya está: aquí, a
            # diferencia del alta, ya existe un cruce previo y borrarlo destruiría
            # información sin poner nada mejor en su lugar.
            continue

        for c in conceptos:
            if c["match_method"] == METODO_NUM_IMPRESO:
                continue
            if c["num_venta_match"] is None:
                continue
            cambia = c["num_venta_match"] != real
            conn.execute(
                """UPDATE factura_conceptos
                   SET num_venta_match = ?, match_method = ?, match_confidence = ?
                   WHERE id = ?""",
                (real, METODO_NUM_IMPRESO, CONFIANZA_NUM_IMPRESO, c["id"]),
            )
            if cambia:
                corregidos += 1

    return corregidos
