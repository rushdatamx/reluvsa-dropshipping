"""Endpoints CRUD básicos de proveedores."""
from fastapi import APIRouter, Depends, HTTPException

from database import get_db
from models import Proveedor, ProveedorCreate, UserInfo
from routers.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/proveedores", tags=["proveedores"])


@router.get("", response_model=list[Proveedor])
def listar(user: UserInfo = Depends(get_current_user)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, nombre, rfc, codigo_bodega, contacto_email, contacto_nombre, activo FROM proveedores ORDER BY nombre"
        ).fetchall()
    return [Proveedor(**dict(r), activo=bool(r["activo"])) for r in rows]


@router.post("", response_model=Proveedor, dependencies=[Depends(require_admin)])
def crear(data: ProveedorCreate):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO proveedores (nombre, rfc, codigo_bodega, contacto_email, contacto_nombre) VALUES (?, ?, ?, ?, ?)",
            (data.nombre, data.rfc, data.codigo_bodega, data.contacto_email, data.contacto_nombre),
        )
        pid = cur.lastrowid
        row = conn.execute(
            "SELECT id, nombre, rfc, codigo_bodega, contacto_email, contacto_nombre, activo FROM proveedores WHERE id = ?",
            (pid,),
        ).fetchone()
    return Proveedor(**dict(row), activo=bool(row["activo"]))


@router.get("/{proveedor_id}", response_model=Proveedor)
def obtener(proveedor_id: int, user: UserInfo = Depends(get_current_user)):
    if user.rol == "proveedor" and user.proveedor_id != proveedor_id:
        raise HTTPException(status_code=403, detail="Sin acceso a este proveedor")
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, nombre, rfc, codigo_bodega, contacto_email, contacto_nombre, activo FROM proveedores WHERE id = ?",
            (proveedor_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado")
    return Proveedor(**dict(row), activo=bool(row["activo"]))
