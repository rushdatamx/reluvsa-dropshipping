"""Endpoints administrativos (solo rol admin).

Pensado para operaciones de mantenimiento que de otro modo requerirían la
Console de Railway (que rompe el formato al pegar). Hoy: crear/resetear la
password de un usuario proveedor sin depender del bootstrap por env var.
"""
from fastapi import APIRouter, Depends, HTTPException

from database import get_db, username_a_email
from models import ProveedorUsuarioUpsert
from routers.auth import hash_password, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/proveedor-password")
def upsert_proveedor_password(data: ProveedorUsuarioUpsert):
    """Crea el usuario de login del proveedor si no existe, o resetea su
    password si ya existe. Idempotente y seguro de re-ejecutar. El username
    es el codigo_bodega en minúsculas (login expande a <user>@reluvsa.local).
    """
    codigo = data.codigo_bodega.strip().upper()
    if not codigo or not data.password:
        raise HTTPException(status_code=400, detail="codigo_bodega y password son requeridos")

    with get_db() as conn:
        prov = conn.execute(
            "SELECT id, nombre FROM proveedores WHERE codigo_bodega = ?", (codigo,)
        ).fetchone()
        if not prov:
            raise HTTPException(status_code=404, detail=f"No existe proveedor con codigo_bodega={codigo}")

        username = codigo.lower()
        email = username_a_email(username)
        pw_hash = hash_password(data.password)

        existing = conn.execute("SELECT id FROM usuarios WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE usuarios SET password_hash = ?, rol = 'proveedor', proveedor_id = ?, activo = 1 WHERE id = ?",
                (pw_hash, prov["id"], existing["id"]),
            )
            accion = "reseteada"
        else:
            conn.execute(
                "INSERT INTO usuarios (email, password_hash, rol, proveedor_id) VALUES (?, ?, 'proveedor', ?)",
                (email, pw_hash, prov["id"]),
            )
            accion = "creada"

    return {
        "ok": True,
        "accion": accion,
        "username": username,
        "proveedor": prov["nombre"],
        "codigo_bodega": codigo,
    }
