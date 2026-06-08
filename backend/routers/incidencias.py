"""CRUD de incidencias (devoluciones, productos equivocados, etc.)."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from database import get_db
from models import IncidenciaCreate, UserInfo
from routers.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/incidencias", tags=["incidencias"])


@router.get("")
def listar(
    user: UserInfo = Depends(get_current_user),
    proveedor_id: Optional[int] = None,
    estado: Optional[str] = None,
):
    if user.rol == "proveedor":
        proveedor_id = user.proveedor_id

    where = []
    params: list = []
    if proveedor_id:
        where.append("i.proveedor_id = ?")
        params.append(proveedor_id)
    if estado:
        where.append("i.estado = ?")
        params.append(estado)

    sql = f"""
        SELECT i.*, p.nombre as proveedor_nombre, v.titulo as venta_titulo, v.sku
        FROM incidencias i
        LEFT JOIN proveedores p ON p.id = i.proveedor_id
        LEFT JOIN ventas_ml v ON v.num_venta = i.num_venta
        {('WHERE ' + ' AND '.join(where)) if where else ''}
        ORDER BY i.created_at DESC
        LIMIT 200
    """
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


@router.post("", dependencies=[Depends(require_admin)])
def crear(data: IncidenciaCreate, user: UserInfo = Depends(get_current_user)):
    with get_db() as conn:
        envio = conn.execute(
            "SELECT proveedor_id FROM envios_colecta WHERE num_venta_ml = ?", (data.num_venta,)
        ).fetchone()
        proveedor_id = envio["proveedor_id"] if envio else None

        cur = conn.execute(
            "INSERT INTO incidencias (num_venta, proveedor_id, tipo, descripcion, creada_por) VALUES (?, ?, ?, ?, ?)",
            (data.num_venta, proveedor_id, data.tipo, data.descripcion, user.user_id),
        )
        incidencia_id = cur.lastrowid
        row = conn.execute("SELECT * FROM incidencias WHERE id = ?", (incidencia_id,)).fetchone()
    return dict(row)


@router.patch("/{incidencia_id}/resolver", dependencies=[Depends(require_admin)])
def resolver(incidencia_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT id FROM incidencias WHERE id = ?", (incidencia_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Incidencia no encontrada")
        conn.execute(
            "UPDATE incidencias SET estado = 'resuelta', resolved_at = ? WHERE id = ?",
            (datetime.now().isoformat(), incidencia_id),
        )
    return {"ok": True}
