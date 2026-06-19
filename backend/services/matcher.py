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
   concepto contra los componentes del kit (exacto o substring, para tolerar sufijos -K).
4. Fuzzy por descripción contra el título de la venta (>= 0.6 = aceptamos con confidence).
"""
import re
from typing import Optional

from rapidfuzz import fuzz, process

CONFIDENCE_MIN_FUZZY = 0.6


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


def _match_por_kit(conn, proveedor_id: int, codigo: str) -> Optional[dict]:
    """Cruza el código del concepto contra los COMPONENTES de un kit.

    Una venta-kit tiene un SKU sintético ('KIT0337') que el proveedor nunca factura:
    factura los componentes ('KDTL-057'...). Buscamos una venta del proveedor cuyo SKU
    sea un kit que tenga este código como componente. Cruce exacto o por substring en
    ambos sentidos (tolera que la factura traiga 'KDTL-057' y el kit 'KDTL-057-K').
    """
    if not codigo:
        return None
    row = conn.execute(
        """SELECT v.num_venta
           FROM ventas_ml v
           JOIN envios_colecta e ON e.num_venta_ml = v.num_venta
           JOIN kit_componentes kc ON kc.kit_sku = UPPER(TRIM(v.sku))
           LEFT JOIN factura_conceptos fc ON fc.num_venta_match = v.num_venta
           WHERE e.proveedor_id = ?
             AND fc.id IS NULL
             AND ( kc.componente_codigo = ?
                   OR ? LIKE '%' || kc.componente_codigo || '%'
                   OR kc.componente_codigo LIKE '%' || ? || '%' )
           ORDER BY v.fecha_venta DESC
           LIMIT 1""",
        (proveedor_id, codigo, codigo, codigo),
    ).fetchone()
    if row:
        return {"num_venta": row["num_venta"], "method": "kit_componente", "confidence": 0.95}
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

        # 3) Match por componente de kit (la venta-kit factura sus componentes, no el SKU-kit)
        por_kit = _match_por_kit(conn, proveedor_id, codigo)
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
