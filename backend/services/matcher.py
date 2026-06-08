"""
Matcher de conceptos de factura ↔ ventas ML.

Estrategia (en orden, el primero que acierta gana):
1. Match exacto por código: SKU de la venta == NoIdentificacion del concepto, o substring.
2. Match por ID interno normalizado: cada proveedor usa su propio esquema de SKU y el
   código de la factura no es idéntico al SKU de ML. Ej. CAUPLAS vende 'CAU2692' pero
   factura '2692  M2626339' — el ID interno '2692' es la llave común. Extraemos los
   tokens (numéricos y código M) de ambos lados y cruzamos por intersección. Sin esto
   CAUPLAS daba 0 matches (sube a ~14/28 en datos reales).
3. Fuzzy por descripción contra el título de la venta (>= 0.6 = aceptamos con confidence).
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

    if not descripcion:
        return None

    # 3) Fuzzy match contra títulos de ventas del proveedor aún sin facturar
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
