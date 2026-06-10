"""Endpoints administrativos (solo rol admin).

Pensado para operaciones de mantenimiento que de otro modo requerirían la
Console de Railway (que rompe el formato al pegar). Hoy: crear/resetear la
password de un usuario proveedor sin depender del bootstrap por env var.
"""
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException

from database import DATABASE_PATH, get_db, username_a_email
from models import ProveedorUsuarioUpsert
from routers.auth import hash_password, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

# Orden hijo -> padre para respetar FKs. Conserva proveedores y usuarios.
_WIPE_TABLAS = ["incidencias", "factura_conceptos", "facturas", "envios_colecta", "ventas_ml"]
_WIPE_AUTOINC = ["facturas", "factura_conceptos", "incidencias"]


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


@router.post("/wipe-transaccional")
def wipe_transaccional(confirmar: str = Body(..., embed=True)):
    """Deja la BD en blanco: vacía ventas, envíos, facturas, conceptos e
    incidencias, CONSERVANDO proveedores y usuarios. Hace un backup consistente
    (VACUUM INTO) antes de borrar. Para entregar el portal con la BD limpia.

    Requiere body {"confirmar": "VACIAR"} como salvaguarda. Idempotente.
    """
    if confirmar != "VACIAR":
        raise HTTPException(
            status_code=400,
            detail='Para confirmar el borrado envía {"confirmar": "VACIAR"}.',
        )

    def _contar(conn):
        out = {}
        for t in _WIPE_TABLAS + ["proveedores", "usuarios"]:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return out

    with get_db() as conn:
        antes = _contar(conn)

    # Backup consistente antes de tocar nada (VACUUM INTO no corre en transacción).
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{DATABASE_PATH}.bak-{ts}"
    bconn = sqlite3.connect(DATABASE_PATH, isolation_level=None)
    try:
        bconn.execute("VACUUM INTO ?", (backup_path,))
    finally:
        bconn.close()

    with get_db() as conn:
        for t in _WIPE_TABLAS:
            conn.execute(f"DELETE FROM {t}")
        for t in _WIPE_AUTOINC:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (t,))

    with get_db() as conn:
        despues = _contar(conn)

    ok = all(despues[t] == 0 for t in _WIPE_TABLAS) and despues["proveedores"] > 0 and despues["usuarios"] > 0
    return {
        "ok": ok,
        "backup": backup_path,
        "antes": antes,
        "despues": despues,
    }
