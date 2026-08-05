"""
Matcher de conceptos de factura ↔ ventas ML.

Estrategia (en orden, el primero que acierta gana):
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

CONFIDENCE_MIN_FUZZY = 0.6

# Términos distintivos (ver `_afinidad_titulo`) que el mejor título debe sacarle al
# segundo para desempatar entre varias ventas-kit que comparten el mismo componente.
# No es un umbral de parecido: es la distancia mínima para que la elección no sea azar.
# Con 1 basta — un solo término propio ('VENTO' vs 'CHEVY') ya identifica el vehículo.
MARGEN_DESEMPATE_KIT = 1


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


def _match_por_id_interno(conn, proveedor_id: int, codigo: str) -> Optional[dict]:
    """Cruza el código de la factura contra los SKU de las ventas del proveedor
    comparando tokens de ID interno (no string completo)."""
    cod_tokens = _tokens_codigo(codigo)
    if not cod_tokens:
        return None
    candidates = conn.execute(
        """SELECT v.num_venta, v.sku
           FROM ventas_ml v
           JOIN envios_colecta e ON e.num_venta_ml = v.num_venta
           LEFT JOIN factura_conceptos fc ON fc.num_venta_match = v.num_venta
           WHERE e.proveedor_id = ?
             AND fc.id IS NULL
             AND v.sku IS NOT NULL
           ORDER BY v.fecha_venta DESC
           LIMIT 1000""",
        (proveedor_id,),
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
                   descripcion: str = "") -> Optional[dict]:
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
    row = conn.execute(
        """SELECT v.num_venta
           FROM ventas_ml v
           JOIN envios_colecta e ON e.num_venta_ml = v.num_venta
           JOIN kit_componentes kc ON kc.kit_sku = UPPER(TRIM(v.sku))
           LEFT JOIN factura_conceptos fc ON fc.num_venta_match = v.num_venta
           WHERE e.proveedor_id = ?
             AND fc.id IS NULL
             AND ( kc.componente_codigo = ?
                   OR ( LENGTH(TRIM(kc.componente_codigo)) >= 4
                        AND ( ? LIKE '%' || kc.componente_codigo || '%'
                              OR kc.componente_codigo LIKE '%' || ? || '%' ) ) )
           ORDER BY v.fecha_venta DESC
           LIMIT 1""",
        (proveedor_id, codigo, codigo, codigo),
    ).fetchone()
    if row:
        return {"num_venta": row["num_venta"], "method": "kit_componente", "confidence": 0.95}

    # (b) Cruce por ID interno normalizado.
    cod_tokens = _tokens_codigo(codigo)
    if not cod_tokens:
        return None

    candidatos = conn.execute(
        """SELECT v.num_venta, v.titulo, v.sku, kc.componente_codigo
           FROM ventas_ml v
           JOIN envios_colecta e ON e.num_venta_ml = v.num_venta
           JOIN kit_componentes kc ON kc.kit_sku = UPPER(TRIM(v.sku))
           LEFT JOIN factura_conceptos fc ON fc.num_venta_match = v.num_venta
           WHERE e.proveedor_id = ?
             AND fc.id IS NULL
           ORDER BY v.fecha_venta DESC
           LIMIT 2000""",
        (proveedor_id,),
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


def match_conceptos_a_ventas(conn, proveedor_id: int, concepto: dict) -> Optional[dict]:
    codigo = (concepto.get("codigo") or "").strip()
    descripcion = (concepto.get("descripcion") or "").strip()

    # 1) Match exacto por código contra SKU
    if codigo:
        row = conn.execute(
            """SELECT v.num_venta
               FROM ventas_ml v
               JOIN envios_colecta e ON e.num_venta_ml = v.num_venta
               LEFT JOIN factura_conceptos fc ON fc.num_venta_match = v.num_venta
               WHERE e.proveedor_id = ?
                 AND fc.id IS NULL
                 AND (v.sku = ? OR v.sku LIKE ?)
               ORDER BY v.fecha_venta DESC
               LIMIT 1""",
            (proveedor_id, codigo, f"%{codigo}%"),
        ).fetchone()
        if row:
            return {"num_venta": row["num_venta"], "method": "codigo_exact", "confidence": 1.0}

        # 2) Match por ID interno normalizado (esquemas de SKU distintos por proveedor)
        por_id = _match_por_id_interno(conn, proveedor_id, codigo)
        if por_id:
            return por_id

        # 3) Match por componente de kit (la venta-kit factura sus componentes, no el SKU-kit).
        #    La descripción va como desempate cuando el componente vive en varios kits.
        por_kit = _match_por_kit(conn, proveedor_id, codigo, descripcion)
        if por_kit:
            return por_kit

    if not descripcion:
        return None

    # 4) Fuzzy match contra títulos de ventas del proveedor aún sin facturar
    candidates = conn.execute(
        """SELECT v.num_venta, v.titulo
           FROM ventas_ml v
           JOIN envios_colecta e ON e.num_venta_ml = v.num_venta
           LEFT JOIN factura_conceptos fc ON fc.num_venta_match = v.num_venta
           WHERE e.proveedor_id = ?
             AND fc.id IS NULL
             AND v.titulo IS NOT NULL
           ORDER BY v.fecha_venta DESC
           LIMIT 500""",
        (proveedor_id,),
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
    cruzar se queda en NULL para el siguiente intento. Solo enriquece, nunca rompe
    un match existente.
    """
    pendientes = conn.execute(
        """SELECT fc.id, fc.codigo_prov, fc.descripcion, f.proveedor_id
           FROM factura_conceptos fc
           JOIN facturas f ON f.id = fc.factura_id
           WHERE fc.num_venta_match IS NULL"""
    ).fetchall()

    recruzados = 0
    for c in pendientes:
        if c["proveedor_id"] is None:
            continue
        match = match_conceptos_a_ventas(
            conn, c["proveedor_id"],
            {"codigo": c["codigo_prov"], "descripcion": c["descripcion"]},
        )
        if match:
            conn.execute(
                """UPDATE factura_conceptos
                   SET num_venta_match = ?, match_method = ?, match_confidence = ?
                   WHERE id = ?""",
                (match["num_venta"], match["method"], match["confidence"], c["id"]),
            )
            recruzados += 1

    return {"conceptos_sin_match": len(pendientes), "conceptos_recruzados": recruzados}
