"""
Matcher de conceptos de factura ↔ ventas ML.

Estrategia:
1. Si el concepto trae 'codigo' y la venta tiene SKU que coincide (igualdad o substring), match.
2. Si no, fuzzy match contra titulos de ventas del proveedor sin facturar (>= 0.6 = aceptamos
   con confidence; < 0.6 = no match).
"""
from typing import Optional

from rapidfuzz import fuzz, process

CONFIDENCE_MIN_FUZZY = 0.6


def match_conceptos_a_ventas(conn, proveedor_id: int, concepto: dict) -> Optional[dict]:
    codigo = (concepto.get("codigo") or "").strip()
    descripcion = (concepto.get("descripcion") or "").strip()

    # 1) Match exacto por código contra SKU
    if codigo:
        row = conn.execute(
            """SELECT v.num_venta
               FROM ventas_ml v
               JOIN envios_colecta e ON e.num_venta = v.num_venta
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

    if not descripcion:
        return None

    # 2) Fuzzy match contra títulos de ventas del proveedor aún sin facturar
    candidates = conn.execute(
        """SELECT v.num_venta, v.titulo
           FROM ventas_ml v
           JOIN envios_colecta e ON e.num_venta = v.num_venta
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
