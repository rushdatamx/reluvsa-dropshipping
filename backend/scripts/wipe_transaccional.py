"""Vacía las tablas transaccionales de la BD, conservando proveedores y usuarios.

Sirve para entregar el portal a Gaby con la BD EN BLANCO: borra ventas, envíos,
facturas, conceptos e incidencias (datos de validación interna / prueba-junio),
pero NUNCA toca `proveedores` ni `usuarios` (configuración que debe sobrevivir).

Seguridad:
  - Hace un backup consistente (`VACUUM INTO`) antes de borrar nada.
  - Sin WIPE_CONFIRM=SI corre en modo DRY-RUN: solo reporta qué haría.
  - Borra en una transacción, orden hijo->padre, idempotente (DELETE sobre tabla
    vacía es no-op).

Uso:
    # Dry-run (no borra, solo reporta):
    DATABASE_PATH=/data/dropshipping.db python3 scripts/wipe_transaccional.py

    # Borrado real (con backup automático):
    WIPE_CONFIRM=SI DATABASE_PATH=/data/dropshipping.db python3 scripts/wipe_transaccional.py
"""
import os
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from database import DATABASE_PATH, get_db  # noqa: E402

# Orden hijo -> padre para respetar FKs.
TABLAS_A_VACIAR = [
    "incidencias",
    "factura_conceptos",
    "facturas",
    "envios_colecta",
    "ventas_ml",
]
TABLAS_CONSERVADAS = ["proveedores", "usuarios"]
# Reiniciar autoincremento de las tablas con PK INTEGER AUTOINCREMENT.
TABLAS_AUTOINC = ["facturas", "factura_conceptos", "incidencias"]


def _contar(conn):
    out = {}
    for t in TABLAS_A_VACIAR + TABLAS_CONSERVADAS:
        out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return out


def _print_conteos(titulo, conteos):
    print(f"\n{titulo}")
    for t in TABLAS_A_VACIAR:
        print(f"  {t:<20} {conteos[t]:>8}")
    print("  " + "-" * 28)
    for t in TABLAS_CONSERVADAS:
        print(f"  {t:<20} {conteos[t]:>8}   (se conserva)")


def main():
    confirmado = os.getenv("WIPE_CONFIRM") == "SI"
    print(f"BD objetivo: {DATABASE_PATH}")

    with get_db() as conn:
        antes = _contar(conn)
    _print_conteos("Conteos ANTES:", antes)

    if not confirmado:
        print(
            "\n[DRY-RUN] No se borró nada. Para ejecutar el borrado real, vuelve a "
            "correr con WIPE_CONFIRM=SI."
        )
        return

    # Backup consistente antes de tocar nada. VACUUM INTO no puede correr dentro
    # de una transacción, por eso usamos una conexión directa con autocommit.
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{DATABASE_PATH}.bak-{ts}"
    bconn = sqlite3.connect(DATABASE_PATH, isolation_level=None)
    try:
        bconn.execute("VACUUM INTO ?", (backup_path,))
    finally:
        bconn.close()
    print(f"\nBackup creado: {backup_path}")

    with get_db() as conn:
        for t in TABLAS_A_VACIAR:
            conn.execute(f"DELETE FROM {t}")
        # Reiniciar autoinc (la tabla puede no existir si nunca hubo inserts).
        for t in TABLAS_AUTOINC:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (t,))

    with get_db() as conn:
        despues = _contar(conn)
    _print_conteos("Conteos DESPUÉS:", despues)

    transaccionales_ok = all(despues[t] == 0 for t in TABLAS_A_VACIAR)
    conservadas_ok = despues["proveedores"] > 0 and despues["usuarios"] > 0
    if transaccionales_ok and conservadas_ok:
        print("\n✅ BD en blanco: transaccionales en 0, proveedores y usuarios intactos.")
    else:
        print(
            "\n⚠️  Revisar: las tablas no quedaron como se esperaba "
            "(transaccionales deben estar en 0; proveedores/usuarios > 0)."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
