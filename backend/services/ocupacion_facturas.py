"""Arbitraje final de ocupación factura -> venta.

Este módulo no decide qué venta corresponde a un concepto. Recibe el resultado ya
calculado por el matcher y, justo antes de persistirlo, garantiza que una venta sólo
pertenezca a una factura. Todos los cambios son locales a SQLite.
"""
from typing import Optional


METODOS_NUMERO_EXPLICITO = {
    "num_venta_proveedor_cauplas",
    "num_venta_proveedor",
}

CONFLICTO_DESPLAZADA = "desplazada_por_numero_explicito"
CONFLICTO_EXPLICITO_DUPLICADO = "numero_explicito_duplicado"
CONFLICTO_VENTA_OCUPADA = "venta_ocupada_por_otra_factura"


def es_numero_explicito(metodo: Optional[str]) -> bool:
    return (metodo or "") in METODOS_NUMERO_EXPLICITO


def _tiene_columna_conflicto(conn) -> bool:
    return any(r[1] == "conflicto_factura" for r in conn.execute(
        "PRAGMA table_info(factura_conceptos)"
    ).fetchall())


def arbitrar_ocupacion(conn, factura_id: int, match: Optional[dict],
                       concepto_id: Optional[int] = None) -> dict:
    """Acepta/rechaza un match y desplaza inferencias si llega evidencia explícita.

    El llamador persiste el dict ``match`` devuelto. Si se desplaza una factura previa,
    todos sus conceptos sobre esa venta quedan pendientes y marcados; nunca se borran.
    """
    if not match or not match.get("num_venta"):
        return {"match": None, "conflicto": None}

    venta = match["num_venta"]
    params = [venta, factura_id]
    excluir = ""
    if concepto_id is not None:
        excluir = " AND fc.id != ?"
        params.append(concepto_id)
    ocupantes = conn.execute(
        f"""SELECT fc.id, fc.factura_id, fc.match_method
              FROM factura_conceptos fc
             WHERE fc.num_venta_match = ? AND fc.factura_id != ?{excluir}
             ORDER BY fc.id""",
        tuple(params),
    ).fetchall()
    if not ocupantes:
        return {"match": match, "conflicto": None}

    nuevo_explicito = es_numero_explicito(match.get("method"))
    ocupado_explicito = any(es_numero_explicito(r["match_method"]) for r in ocupantes)
    tiene_columna = _tiene_columna_conflicto(conn)

    if nuevo_explicito and not ocupado_explicito:
        conflicto_sql = ", conflicto_factura = ?" if tiene_columna else ""
        valores = ([CONFLICTO_DESPLAZADA] if tiene_columna else [])
        ids = [r["id"] for r in ocupantes]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"""UPDATE factura_conceptos
                   SET num_venta_match = NULL, match_method = NULL,
                       match_confidence = NULL{conflicto_sql}
                 WHERE id IN ({placeholders})""",
            (*valores, *ids),
        )
        return {"match": match, "conflicto": None}

    conflicto = (CONFLICTO_EXPLICITO_DUPLICADO
                  if nuevo_explicito and ocupado_explicito
                  else CONFLICTO_VENTA_OCUPADA)
    return {"match": None, "conflicto": conflicto}
