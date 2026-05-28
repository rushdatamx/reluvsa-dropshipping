"""
Crear usuarios (admin o proveedor) desde la línea de comandos.

Uso:
    python3 scripts/crear_usuario.py admin gaby@reluvsa.com "Cambiar123!"
    python3 scripts/crear_usuario.py proveedor VAZLO contacto@vazlo.com "PassVazlo!"

El segundo argumento para proveedor es el codigo_bodega (AG | CAUPLAS | KG | KIM | VAZLO).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import get_db, init_database
from routers.auth import hash_password


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    rol = sys.argv[1]
    if rol not in ("admin", "proveedor"):
        print("rol debe ser 'admin' o 'proveedor'")
        sys.exit(1)

    init_database()

    if rol == "admin":
        email = sys.argv[2].strip().lower()
        password = sys.argv[3]
        with get_db() as conn:
            existing = conn.execute("SELECT id FROM usuarios WHERE email = ?", (email,)).fetchone()
            if existing:
                print(f"Usuario {email} ya existe (id {existing['id']})")
                sys.exit(1)
            conn.execute(
                "INSERT INTO usuarios (email, password_hash, rol) VALUES (?, ?, ?)",
                (email, hash_password(password), "admin"),
            )
        print(f"Admin {email} creado")
        return

    codigo_bodega = sys.argv[2].strip().upper()
    email = sys.argv[3].strip().lower()
    password = sys.argv[4] if len(sys.argv) > 4 else None
    if not password:
        print("Falta password")
        sys.exit(1)

    with get_db() as conn:
        prov = conn.execute(
            "SELECT id, nombre FROM proveedores WHERE codigo_bodega = ?", (codigo_bodega,)
        ).fetchone()
        if not prov:
            print(f"No existe proveedor con codigo_bodega={codigo_bodega}")
            sys.exit(1)

        existing = conn.execute("SELECT id FROM usuarios WHERE email = ?", (email,)).fetchone()
        if existing:
            print(f"Usuario {email} ya existe (id {existing['id']})")
            sys.exit(1)

        conn.execute(
            "INSERT INTO usuarios (email, password_hash, rol, proveedor_id) VALUES (?, ?, 'proveedor', ?)",
            (email, hash_password(password), prov["id"]),
        )
    print(f"Proveedor user {email} creado para {prov['nombre']} ({codigo_bodega})")


if __name__ == "__main__":
    main()
