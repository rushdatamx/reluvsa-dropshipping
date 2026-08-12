"""
Corrige los cruces factura↔venta de KIM usando el # de venta impreso en el PDF,
TOLERANDO los ceros de más que KIM teclea de vez en cuando.

⚠️ EXCLUSIVO DE KIM (`codigo_bodega = 'KIM'`). Ningún otro proveedor imprime el # de
venta en su factura, y la normalización de ceros de aquí abajo es un error de captura
observado SÓLO en KIM. Aplicarla a otro proveedor sería inventar datos.

POR QUÉ EXISTE ESTE SCRIPT SI YA HAY UNO (2026-08-12)
-----------------------------------------------------
`corregir_cruces_num_venta_pdf.py` (2026-08-07) sólo reconoce números de EXACTAMENTE
16 dígitos (`\\b(2[0-9]{15})\\b`). Por eso vio 144 de 748 PDF (19.3%).

Gaby reportó el 2026-08-12 que "en la factura viene 6 ceros y en la venta correcta es
4 ceros". Tenía razón, y explica el 19%. Medido sobre los 864 PDF de KIM legibles en
prod:

    con # impreso : 473 (54.5%)   <- casi el TRIPLE de lo que veía el script anterior
    longitudes    : 15 (2) · 16 (146) · 17 (245) · 18 (73) · 19 (7)

O sea: sólo 146 de 473 traen los 16 dígitos correctos. **El 69% viene con ceros de más**,
tecleados por KIM al capturar. Ejemplos reales:

    20000016910060548   (17) -> 2000016910060548
    2000000017634639548 (19) -> 2000017634639548

ESTADO QUE CORRIGE (medido en prod 2026-08-12, sobre las 473 con # legible)
---------------------------------------------------------------------------
    ya correctas       : 201
    MAL CRUZADAS       : 216   <- todas con confianza 1.0
    sin cruce hoy      :  26
    no resolubles      :  29

Las 216 son el daño real: le dicen a Gaby "✓ Facturado" con la máxima confianza sobre
la venta que no es. Es justo lo que ella vio en su foto (folios corridos un lugar).

🔴 POR QUÉ NO BASTA CON "QUITAR LOS CEROS DE ADELANTE"
------------------------------------------------------
Fue la primera regla que se probó (colapsar la racha de ceros que sigue al `2`) y
FALLA en ~8 de los 29 no resolubles, porque KIM no sólo mete ceros al principio:

    K29628  impreso 20000117582609224  -> colapsar da 2000117582609224 (NO existe)
                                          el real es 2000017582609224 (cero EN MEDIO)
    K30109  impreso 2000017791201470   -> el real es 2000017797201470 (¡un DÍGITO
                                          distinto, no un cero!)

Y lo peligroso: esa regla PRODUCE un número de 16 dígitos con pinta de válido. Si por
casualidad existiera como venta de otro, escribiría un cruce falso con confianza 1.0 —
el mismo bug que venimos arreglando, pero peor porque nadie lo revisaría.

Por eso aquí NO se reconstruye el número. Se BUSCA la venta y se EXIGE evidencia:

LAS 3 REGLAS DE ESTE SCRIPT (además de las 5 garantías heredadas)
-----------------------------------------------------------------
R1. Candidatas = ventas cuyo num_venta se obtiene del impreso BORRANDO ceros en
    CUALQUIER posición (no sólo al principio). Nunca se agregan ni cambian dígitos:
    borrar es recuperable, inventar no.
R2. La candidata debe ser de KIM: su envío debe tener proveedor_id de KIM. Una venta
    de otro proveedor jamás la factura KIM.
R3. El SKU de la venta debe cuadrar con el código del concepto (mismos tokens, la
    misma normalización que usa el matcher). Doble verificación: número + pieza.
    Si hay >1 candidata o el SKU no cuadra -> NO SE TOCA.

    R3 es lo que vuelve seguro tolerar los ceros: un número mal reconstruido casi
    nunca cae en una venta del mismo proveedor Y de la misma pieza.

Ante cualquier duda se prefiere el PENDIENTE al cruce inventado (regla del proyecto:
un pendiente se ve y se corrige; un falso "✓ Facturado" nadie lo vuelve a mirar).

USO
---
    python corregir_cruces_num_venta_kim_ceros.py --db <ruta>             # simula
    python corregir_cruces_num_venta_kim_ceros.py --db <ruta> --ejecutar  # escribe

Idempotente: una segunda corrida no encuentra nada que corregir.
"""
import argparse
import json
import os
import re
import sqlite3
import sys

# El # de venta impreso: empieza con 2 y trae entre 15 y 22 dígitos (KIM teclea ceros
# de más). El rango es amplio A PROPÓSITO: la validación real la hacen R1/R2/R3, no la
# longitud. Hoy ningún concepto del portal trae números de 12+ dígitos, así que hallar
# uno en el PDF sigue siendo señal inequívoca de que el proveedor lo puso.
PATRON_NUM_IMPRESO = re.compile(r"#\s*(2\d{14,21})\b")

CODIGO_BODEGA = "KIM"          # ⚠️ EXCLUSIVO DE KIM — ver encabezado
METODO = "num_venta_proveedor"
CONFIANZA = 1.0
LARGO_ORDER_ID = 16            # los order.id de ML son de 16 dígitos


def extraer_numeros_pdf(ruta: str) -> list:
    """Devuelve los # de venta impresos en un PDF. [] si es ilegible."""
    try:
        import pdfplumber
        with pdfplumber.open(ruta) as pdf:
            texto = " ".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return []
    return sorted(set(PATRON_NUM_IMPRESO.findall(texto)))


def resolver_pdf(ruta: str, dir_facturas: str):
    """El path en BD puede ser de un contenedor viejo; se resuelve por nombre."""
    if not ruta:
        return None
    if os.path.exists(ruta):
        return ruta
    alt = os.path.join(dir_facturas, os.path.basename(ruta))
    return alt if os.path.exists(alt) else None


def candidatos_por_ceros(impreso: str) -> set:
    """
    R1: todos los números de 16 dígitos que se obtienen BORRANDO ceros del impreso.

    Sólo se BORRA — nunca se agrega ni se sustituye un dígito. Borrar un cero de más es
    deshacer un error de tecleo observado; cambiar un dígito sería inventar un número.

    Se enumeran las posiciones de los ceros y se prueban todas las combinaciones de
    borrado que dejen 16 dígitos. Con <= 6 ceros sobrantes el espacio es chico.
    """
    if len(impreso) == LARGO_ORDER_ID:
        return {impreso}
    sobran = len(impreso) - LARGO_ORDER_ID
    if sobran <= 0 or sobran > 6:
        return set()

    pos_ceros = [i for i, ch in enumerate(impreso) if ch == "0"]
    if len(pos_ceros) < sobran:
        return set()

    from itertools import combinations
    out = set()
    for quitar in combinations(pos_ceros, sobran):
        quitar = set(quitar)
        v = "".join(ch for i, ch in enumerate(impreso) if i not in quitar)
        if len(v) == LARGO_ORDER_ID and v.startswith("2"):
            out.add(v)
    return out


def _tokens(texto: str) -> set:
    """Tokens alfanuméricos significativos de un código/SKU (>=3 chars)."""
    if not texto:
        return set()
    return {t for t in re.split(r"[^A-Za-z0-9]+", texto.upper()) if len(t) >= 3}


def sku_cuadra(codigo_concepto: str, sku_venta: str) -> bool:
    """
    R3: el código facturado y el SKU de la venta deben ser la misma pieza.

    KIM publica en ML con sufijo (`96413748-Z`) y factura sin él (`96413748`), así que
    se comparan TOKENS, no texto crudo — la misma idea que `codigo_id_interno` del
    matcher. Basta que compartan un token de >= 3 caracteres.
    """
    a, b = _tokens(codigo_concepto), _tokens(sku_venta)
    if not a or not b:
        return False
    if a & b:
        return True
    # tolera el sufijo pegado: '96413748Z' vs '96413748'
    for x in a:
        for y in b:
            if len(x) >= 5 and len(y) >= 5 and (x.startswith(y) or y.startswith(x)):
                return True
    return False


def construir_plan(conn, dir_facturas: str, cache_nums: dict = None) -> dict:
    """Calcula qué hay que cambiar. NO escribe nada."""
    conn.row_factory = sqlite3.Row

    prov = conn.execute(
        "SELECT id FROM proveedores WHERE codigo_bodega = ?", (CODIGO_BODEGA,)
    ).fetchone()
    if not prov:
        raise SystemExit(f"no existe el proveedor {CODIGO_BODEGA}")
    prov_id = prov["id"]

    facturas = list(conn.execute(
        """SELECT f.id, f.serie, f.folio, f.pdf_path, date(f.fecha_factura) AS ff
           FROM facturas f
           WHERE f.proveedor_id = ? AND f.pdf_path IS NOT NULL""",
        (prov_id,),
    ))

    def nums_de(f):
        # El cache se consulta ANTES de tocar el disco: un PDF ya extraído sigue siendo
        # utilizable aunque el archivo haya desaparecido del contenedor (los paths en BD
        # pueden ser de un contenedor viejo). Resolver primero el archivo haría que esos
        # casos se contaran como "sin número" teniendo el dato a la mano.
        clave = os.path.basename(f["pdf_path"] or "")
        if cache_nums is not None and clave in cache_nums:
            return cache_nums[clave]
        ruta = resolver_pdf(f["pdf_path"], dir_facturas)
        if not ruta:
            return None
        return extraer_numeros_pdf(ruta)

    # ⚠️ Garantía heredada: un mismo # en DOS facturas distintas es ambiguo (KIM factura
    # por separado dos piezas de la misma venta). No se corrige ninguna.
    numeros_por_factura = {}
    for f in facturas:
        nums = nums_de(f)
        if nums and len(nums) == 1:
            numeros_por_factura.setdefault(nums[0], set()).add(f["id"])
    numeros_ambiguos = {n for n, fs in numeros_por_factura.items() if len(fs) > 1}

    correcciones = []
    est = {"ya_ok": 0, "sin_numero": 0, "sin_venta": 0, "ambiguos": 0,
           "multi_candidata": 0, "sku_no_cuadra": 0, "no_es_kim": 0,
           "venta_posterior": 0}
    rechazos = []

    for f in facturas:
        nums = nums_de(f)
        if not nums or len(nums) != 1:
            est["sin_numero"] += 1
            continue

        impreso = nums[0]
        if impreso in numeros_ambiguos:
            est["ambiguos"] += 1
            continue

        conceptos = list(conn.execute(
            """SELECT id, codigo_prov, num_venta_match, match_method, match_confidence
               FROM factura_conceptos WHERE factura_id = ?""",
            (f["id"],),
        ))
        if not conceptos:
            continue

        # R1: candidatas por borrado de ceros.
        posibles = candidatos_por_ceros(impreso)
        if not posibles:
            est["sin_venta"] += 1
            rechazos.append((f["folio"], impreso, "sin variante de 16 dígitos"))
            continue

        # R2: sólo ventas de KIM (su envío debe tener proveedor KIM).
        marcadores = ",".join("?" for _ in posibles)
        ventas_kim = list(conn.execute(
            f"""SELECT DISTINCT v.num_venta, v.sku, date(v.fecha_venta) AS fv
                FROM ventas_ml v
                JOIN envios_colecta e ON e.num_venta_ml = v.num_venta
                WHERE v.num_venta IN ({marcadores})
                  AND COALESCE(e.proveedor_id, -1) = ?""",
            (*sorted(posibles), prov_id),
        ))
        if not ventas_kim:
            existe_alguna = conn.execute(
                f"SELECT COUNT(*) FROM ventas_ml WHERE num_venta IN ({marcadores})",
                tuple(sorted(posibles)),
            ).fetchone()[0]
            if existe_alguna:
                est["no_es_kim"] += 1
                rechazos.append((f["folio"], impreso, "la venta no es de KIM"))
            else:
                est["sin_venta"] += 1
                rechazos.append((f["folio"], impreso, "ninguna variante existe"))
            continue

        # R3: el SKU debe cuadrar con algún concepto de la factura.
        validas = [v for v in ventas_kim
                   if any(sku_cuadra(c["codigo_prov"], v["sku"]) for c in conceptos)]
        if not validas:
            est["sku_no_cuadra"] += 1
            rechazos.append((f["folio"], impreso,
                             f"SKU no cuadra (venta={ventas_kim[0]['sku']})"))
            continue
        if len(validas) > 1:
            est["multi_candidata"] += 1
            rechazos.append((f["folio"], impreso, "más de una candidata válida"))
            continue

        venta = validas[0]

        # Garantía heredada: la venta no puede ser POSTERIOR a su propia factura.
        if f["ff"] and venta["fv"] and venta["fv"] > f["ff"]:
            est["venta_posterior"] += 1
            rechazos.append((f["folio"], impreso, "venta posterior a la factura"))
            continue

        real = venta["num_venta"]
        for c in conceptos:
            if c["num_venta_match"] == real:
                est["ya_ok"] += 1
                continue
            correcciones.append({
                "concepto_id": c["id"],
                "factura_id": f["id"],
                "folio": f["folio"],
                "codigo": c["codigo_prov"],
                "impreso": impreso,
                "de": c["num_venta_match"],
                "a": real,
                "sku_venta": venta["sku"],
                "metodo_previo": c["match_method"],
                "conf_previa": c["match_confidence"],
            })

    # Garantía heredada: ocupantes ajenos de las ventas destino -> se liberan a pendiente.
    # ⚠️ "Ajeno" es POR FACTURA, no por concepto: dos conceptos de la MISMA factura pueden
    # apuntar legítimamente a la misma venta. Comparar por concepto hace que el script se
    # pise a sí mismo y nunca converja.
    ids_corregidos = {c["concepto_id"] for c in correcciones}
    facturas_destino = {}
    for c in correcciones:
        facturas_destino.setdefault(c["a"], set()).add(c["factura_id"])

    liberar = []
    for destino, fact_ids in facturas_destino.items():
        for r in conn.execute(
            """SELECT id, factura_id, num_venta_match, match_method, match_confidence
               FROM factura_conceptos WHERE num_venta_match = ?""",
            (destino,),
        ):
            if r["id"] in ids_corregidos or r["factura_id"] in fact_ids:
                continue
            liberar.append({
                "concepto_id": r["id"],
                "factura_id": r["factura_id"],
                "de": r["num_venta_match"],
                "metodo_previo": r["match_method"],
                "conf_previa": r["match_confidence"],
            })

    return {
        "correcciones": correcciones,
        "liberar": liberar,
        "rechazos": rechazos,
        "facturas_revisadas": len(facturas),
        **est,
    }


def aplicar(conn, plan: dict):
    """Aplica el plan en UNA sola transacción (o todo, o nada)."""
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        for l in plan["liberar"]:
            cur.execute(
                """UPDATE factura_conceptos
                   SET num_venta_match = NULL, match_method = NULL, match_confidence = NULL
                   WHERE id = ?""",
                (l["concepto_id"],),
            )
        for c in plan["correcciones"]:
            cur.execute(
                """UPDATE factura_conceptos
                   SET num_venta_match = ?, match_method = ?, match_confidence = ?
                   WHERE id = ?""",
                (c["a"], METODO, CONFIANZA, c["concepto_id"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def verificar(conn, plan: dict) -> list:
    """Comprueba las garantías DESPUÉS de aplicar. Devuelve lista de fallas."""
    fallas = []
    for c in plan["correcciones"]:
        r = conn.execute(
            "SELECT num_venta_match FROM factura_conceptos WHERE id = ?",
            (c["concepto_id"],),
        ).fetchone()
        if not r or r[0] != c["a"]:
            fallas.append(f"concepto {c['concepto_id']} no quedó en {c['a']}")
    for l in plan["liberar"]:
        r = conn.execute(
            "SELECT num_venta_match FROM factura_conceptos WHERE id = ?",
            (l["concepto_id"],),
        ).fetchone()
        if not r or r[0] is not None:
            fallas.append(f"concepto {l['concepto_id']} debía quedar pendiente")
    return fallas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--uploads", default="/data/uploads/facturas")
    ap.add_argument("--cache", help="JSON {archivo_pdf: [numeros]} para no releer los PDF")
    ap.add_argument("--ejecutar", action="store_true", help="sin esto sólo simula")
    ap.add_argument("--detalle", type=int, default=10)
    args = ap.parse_args()

    cache = json.load(open(args.cache)) if args.cache else None
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    total_antes = conn.execute("SELECT COUNT(*) FROM factura_conceptos").fetchone()[0]
    cruz_antes = conn.execute(
        "SELECT COUNT(*) FROM factura_conceptos WHERE num_venta_match IS NOT NULL"
    ).fetchone()[0]

    plan = construir_plan(conn, args.uploads, cache)

    print(f"facturas {CODIGO_BODEGA} con PDF revisadas   : {plan['facturas_revisadas']}")
    print(f"  sin # de venta legible             : {plan['sin_numero']}")
    print(f"  # ambiguo (en 2 facturas)          : {plan['ambiguos']}   <- no se tocan")
    print(f"  ninguna variante existe como venta : {plan['sin_venta']}")
    print(f"  la venta no es de KIM (R2)         : {plan['no_es_kim']}")
    print(f"  SKU no cuadra (R3)                 : {plan['sku_no_cuadra']}")
    print(f"  más de una candidata (R3)          : {plan['multi_candidata']}")
    print(f"  venta posterior a la factura       : {plan['venta_posterior']}")
    print(f"  conceptos YA correctos             : {plan['ya_ok']}")
    print()
    reasignar = [c for c in plan["correcciones"] if c["de"]]
    pendientes = [c for c in plan["correcciones"] if not c["de"]]
    conf_alta = [c for c in reasignar if (c["conf_previa"] or 0) >= 1.0]
    print(f"A CORREGIR: {len(plan['correcciones'])} conceptos")
    print(f"  cruzados a la venta EQUIVOCADA -> se reasignan : {len(reasignar)}"
          f"   (de ellos con confianza 1.0: {len(conf_alta)})")
    print(f"  pendientes -> se cruzan                        : {len(pendientes)}")
    print(f"OCUPANTES AJENOS a liberar (quedan pendientes)   : {len(plan['liberar'])}")

    if not args.ejecutar:
        print("\n[SIMULACIÓN] no se escribió nada. Usa --ejecutar para aplicar.")
        print(f"\n--- muestra de reasignaciones (código | SKU venta) ---")
        for c in reasignar[:args.detalle]:
            print(f"   K{c['folio']}: impreso {c['impreso']}")
            print(f"        {c['de']} -> {c['a']}  "
                  f"(era {c['metodo_previo']} conf {c['conf_previa']})")
            print(f"        concepto '{c['codigo']}'  vs  SKU venta '{c['sku_venta']}'")
        if plan["rechazos"]:
            print(f"\n--- muestra de NO TOCADOS ({len(plan['rechazos'])}) ---")
            for folio, imp, motivo in plan["rechazos"][:args.detalle]:
                print(f"   K{folio}: {imp}  -> {motivo}")
        return 0

    aplicar(conn, plan)

    total_desp = conn.execute("SELECT COUNT(*) FROM factura_conceptos").fetchone()[0]
    cruz_desp = conn.execute(
        "SELECT COUNT(*) FROM factura_conceptos WHERE num_venta_match IS NOT NULL"
    ).fetchone()[0]
    fallas = verificar(conn, plan)

    print(f"\nAPLICADO. filas {total_antes} -> {total_desp} "
          f"({'OK, ninguna borrada' if total_antes == total_desp else '🔴 CAMBIÓ'})")
    print(f"conceptos cruzados: {cruz_antes} -> {cruz_desp}")
    print("verificación:", "OK, todas las garantías se cumplen" if not fallas else fallas)
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
