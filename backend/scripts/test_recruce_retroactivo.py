"""E2E del cruce retroactivo: factura subida ANTES que la venta.

Reproduce la duda de Gaby: si el proveedor sube la factura antes de que ella suba
el Excel de ventas, ¿se cruza después? Con recruzar_conceptos_sin_match: SÍ.

Uso: backend/.venv/bin/python backend/scripts/test_recruce_retroactivo.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
sys.path.insert(0, str(Path(__file__).parent.parent))

import database  # noqa: E402
from services.matcher import match_conceptos_a_ventas, recruzar_conceptos_sin_match  # noqa: E402


def ok(cond, msg):
    print(("✅" if cond else "❌") + " " + msg)
    if not cond:
        raise SystemExit(1)


def main():
    database.init_database()

    with database.get_db() as conn:
        kim = conn.execute("SELECT id FROM proveedores WHERE codigo_bodega='KIM'").fetchone()["id"]

        # --- PASO 1: el proveedor sube la factura ANTES de que exista la venta ---
        cur = conn.execute(
            """INSERT INTO facturas (proveedor_id, uuid_cfdi, serie, folio, rfc_emisor, rfc_receptor)
               VALUES (?, 'UUID-TEST-1', 'K', '27957', 'KAC1601193F6', 'GPE230915JWA')""",
            (kim,),
        )
        fid = cur.lastrowid
        concepto = {"codigo": "9025125-Z", "descripcion": "Anillo Reluctor Cigueñal Aveo"}
        # En este momento NO hay venta ni envío -> el match da None
        m0 = match_conceptos_a_ventas(conn, kim, concepto)
        ok(m0 is None, "al subir la factura (sin venta aún) el concepto NO cruza")
        conn.execute(
            """INSERT INTO factura_conceptos (factura_id, codigo_prov, descripcion, num_venta_match)
               VALUES (?, ?, ?, NULL)""",
            (fid, concepto["codigo"], concepto["descripcion"]),
        )

    # --- PASO 2: Gaby sube la venta + su envío con proveedor KIM (después) ---
    with database.get_db() as conn:
        conn.execute(
            "INSERT INTO ventas_ml (num_venta, sku, titulo, fecha_venta) VALUES (?,?,?,?)",
            ("2000016904476644", "9025125-Z", "Anillo Reluctor Cigueñal Aveo 1.5", "2026-06-12 10:00"),
        )
        conn.execute(
            "INSERT INTO envios_colecta (num_envio, num_venta_ml, proveedor_id, cumplio_sla) VALUES (?,?,?,?)",
            ("ENV-1", "2000016904476644", kim, 1),
        )

    # --- PASO 3: el re-cruce (lo dispara parse_ventas_ml/parse_colecta) ---
    with database.get_db() as conn:
        before = conn.execute(
            "SELECT num_venta_match FROM factura_conceptos WHERE factura_id=?", (fid,)
        ).fetchone()["num_venta_match"]
        ok(before is None, "antes del re-cruce el concepto sigue sin venta (NULL)")

        res = recruzar_conceptos_sin_match(conn)
        print("   recruce:", res)
        ok(res["conceptos_recruzados"] == 1, f"1 concepto recruzado (got {res['conceptos_recruzados']})")

    # --- PASO 4: verificar que el concepto ya quedó cruzado ---
    with database.get_db() as conn:
        row = conn.execute(
            "SELECT num_venta_match, match_method FROM factura_conceptos WHERE factura_id=?", (fid,)
        ).fetchone()
    ok(row["num_venta_match"] == "2000016904476644", "el concepto ahora cruza a la venta correcta")
    ok(row["match_method"] == "codigo_exact", f"method=codigo_exact (got {row['match_method']})")

    # --- PASO 5: idempotencia: re-correr no rompe ni duplica ---
    with database.get_db() as conn:
        res2 = recruzar_conceptos_sin_match(conn)
    ok(res2["conceptos_recruzados"] == 0 and res2["conceptos_sin_match"] == 0,
       "re-correr el re-cruce no toca nada (idempotente)")

    print("\n🎉 CRUCE RETROACTIVO OK")


if __name__ == "__main__":
    try:
        main()
    finally:
        Path(_tmpdb).unlink(missing_ok=True)
