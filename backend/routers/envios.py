"""Reasignación manual de bodega para envíos."""
from fastapi import APIRouter, Depends, HTTPException

from database import get_db
from models import ReasignarBodegaRequest
from routers.auth import require_admin

router = APIRouter(prefix="/api/envios", tags=["envios"])


@router.patch("/{num_envio}/reasignar", dependencies=[Depends(require_admin)])
def reasignar(num_envio: str, data: ReasignarBodegaRequest):
    with get_db() as conn:
        row = conn.execute(
            "SELECT num_envio FROM envios_colecta WHERE num_envio = ?", (num_envio,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Envío no encontrado")

        proveedor_id = data.proveedor_id
        if proveedor_id is None:
            p = conn.execute(
                "SELECT id FROM proveedores WHERE codigo_bodega = ?", (data.lugar_override,)
            ).fetchone()
            if p:
                proveedor_id = p["id"]

        conn.execute(
            "UPDATE envios_colecta SET lugar_override = ?, proveedor_id = ? WHERE num_envio = ?",
            (data.lugar_override, proveedor_id, num_envio),
        )

    return {"ok": True, "num_envio": num_envio, "lugar_override": data.lugar_override, "proveedor_id": proveedor_id}
