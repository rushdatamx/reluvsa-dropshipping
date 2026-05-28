"""Listado y consulta de ventas ML (admin) y pedidos pendientes (proveedor)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from database import get_db
from models import UserInfo
from routers.auth import get_current_user

router = APIRouter(prefix="/api/ventas", tags=["ventas"])


@router.get("")
def listar(
    user: UserInfo = Depends(get_current_user),
    proveedor_id: Optional[int] = None,
    sin_factura: bool = False,
    estado: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
):
    if user.rol == "proveedor":
        proveedor_id = user.proveedor_id

    where = ["1=1"]
    params: list = []
    join_factura = ""

    if proveedor_id:
        where.append("e.proveedor_id = ?")
        params.append(proveedor_id)

    if estado:
        where.append("v.estado = ?")
        params.append(estado)

    if q:
        where.append("(v.num_venta LIKE ? OR v.sku LIKE ? OR v.titulo LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])

    if sin_factura:
        join_factura = "LEFT JOIN factura_conceptos fc ON fc.num_venta_match = v.num_venta"
        where.append("fc.id IS NULL")

    offset = (page - 1) * limit
    sql = f"""
        SELECT v.num_venta, v.sku, v.fecha_venta, v.estado, v.titulo, v.unidades,
               v.total, v.comprador_estado, v.forma_entrega,
               e.num_envio, e.lugar_real, e.lugar_override, e.cumplio_sla,
               e.proveedor_id, p.nombre as proveedor_nombre,
               (SELECT COUNT(*) FROM factura_conceptos fc2 WHERE fc2.num_venta_match = v.num_venta) as facturas_count
        FROM ventas_ml v
        LEFT JOIN envios_colecta e ON e.num_venta = v.num_venta
        LEFT JOIN proveedores p ON p.id = e.proveedor_id
        {join_factura}
        WHERE {' AND '.join(where)}
        ORDER BY v.fecha_venta DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
        count_sql = f"""
            SELECT COUNT(*) as c
            FROM ventas_ml v
            LEFT JOIN envios_colecta e ON e.num_venta = v.num_venta
            {join_factura}
            WHERE {' AND '.join(where)}
        """
        total = conn.execute(count_sql, params[:-2]).fetchone()["c"]

    return {
        "page": page,
        "limit": limit,
        "total": total,
        "items": [dict(r) for r in rows],
    }


@router.get("/{num_venta}")
def detalle(num_venta: str, user: UserInfo = Depends(get_current_user)):
    with get_db() as conn:
        venta = conn.execute(
            "SELECT * FROM ventas_ml WHERE num_venta = ?", (num_venta,)
        ).fetchone()
        envio = conn.execute(
            "SELECT * FROM envios_colecta WHERE num_venta = ?", (num_venta,)
        ).fetchone()
        conceptos = conn.execute(
            """SELECT fc.*, f.uuid_cfdi, f.folio, f.fecha_factura
               FROM factura_conceptos fc
               JOIN facturas f ON f.id = fc.factura_id
               WHERE fc.num_venta_match = ?""",
            (num_venta,),
        ).fetchall()
        incidencias = conn.execute(
            "SELECT * FROM incidencias WHERE num_venta = ? ORDER BY created_at DESC",
            (num_venta,),
        ).fetchall()

    return {
        "venta": dict(venta) if venta else None,
        "envio": dict(envio) if envio else None,
        "conceptos_factura": [dict(c) for c in conceptos],
        "incidencias": [dict(i) for i in incidencias],
    }
