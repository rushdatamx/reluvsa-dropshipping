"""E2E de la feature kits -> componentes con BD desechable.

Verifica:
1. parse_kits carga el Excel real (656 kits / 1853 filas) y es idempotente al re-subir.
2. El detector clasifica el Excel de kits como "kits" (y rechaza cruces).
3. El matcher cruza un concepto-componente a la venta-kit por method=kit_componente.
4. El listado de ventas expone kit_componentes para la venta-kit.

Uso: backend/.venv/bin/python backend/scripts/test_kits_e2e.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# BD desechable ANTES de importar database (lee DATABASE_PATH al importar).
_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
sys.path.insert(0, str(Path(__file__).parent.parent))

print("SQLite version:", sqlite3.sqlite_version)

import database  # noqa: E402
from services.parser_kits import parse_kits  # noqa: E402
from services.detector_archivo import detectar_tipo_xlsx  # noqa: E402
from services.matcher import match_conceptos_a_ventas  # noqa: E402

EXCEL = Path(__file__).parent.parent.parent / "kits" / "relacion-kits-componentes.xlsx"


def ok(cond, msg):
    print(("✅" if cond else "❌") + " " + msg)
    if not cond:
        raise SystemExit(1)


def main():
    database.init_database()

    # 1) Detector
    tipo = detectar_tipo_xlsx(EXCEL)
    ok(tipo == "kits", f"detector clasifica el Excel como 'kits' (got {tipo})")

    # 2) Parser carga
    res = parse_kits(EXCEL)
    print("   parse_kits:", res)
    ok(res["kits"] == 656, f"656 kits cargados (got {res['kits']})")
    ok(res["filas"] == 1853, f"1853 filas leídas (got {res['filas']})")
    # El Excel trae 6 pares (kit,componente) duplicados → 1847 relaciones únicas; el
    # upsert colapsa esos 6 a 'actualizados' ya en la primera pasada.
    ok(res["nuevos"] == 1847 and res["actualizados"] == 6,
       f"primera carga: 1847 nuevos + 6 dups colapsados (got {res['nuevos']}/{res['actualizados']})")

    # 3) Idempotencia: re-subir actualiza, no duplica
    res2 = parse_kits(EXCEL)
    ok(res2["nuevos"] == 0 and res2["actualizados"] == 1853, "re-subida: todo actualizado, 0 nuevos")
    with database.get_db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM kit_componentes").fetchone()["c"]
    ok(total == 1847, f"1847 relaciones únicas tras re-subir, sin duplicados (got {total})")

    # Confirmar KIT0337 -> KDTL-057-K, KDTL-058-K
    with database.get_db() as conn:
        comps = [r["componente_codigo"] for r in conn.execute(
            "SELECT componente_codigo FROM kit_componentes WHERE kit_sku='KIT0337' ORDER BY 1"
        ).fetchall()]
    ok(comps == ["KDTL-057-K", "KDTL-058-K"], f"KIT0337 -> {comps}")

    # 4) Sembrar venta-kit KIT0337 + envío con proveedor KIM
    with database.get_db() as conn:
        kim = conn.execute("SELECT id FROM proveedores WHERE codigo_bodega='KIM'").fetchone()["id"]
        conn.execute(
            "INSERT INTO ventas_ml (num_venta, sku, titulo, fecha_venta) VALUES (?,?,?,?)",
            ("2000016762751980", "KIT0337", "Par Calavera Trasera Der/Izq P/ Matiz 1.0 2015 Rojo", "2026-05-13 23:43"),
        )
        conn.execute(
            "INSERT INTO envios_colecta (num_envio, num_venta_ml, proveedor_id, cumplio_sla) VALUES (?,?,?,?)",
            ("ENV-KIT-1", "2000016762751980", kim, 1),
        )

    # 5) Matcher: concepto con código componente SIN sufijo -K (como vendría en la factura)
    with database.get_db() as conn:
        m = match_conceptos_a_ventas(conn, kim, {"codigo": "KDTL-057", "descripcion": "Calavera trasera"})
    print("   match (KDTL-057, sin -K):", m)
    ok(m is not None, "concepto componente cruza a la venta-kit")
    ok(m["num_venta"] == "2000016762751980", "cruza a la venta correcta")
    ok(m["method"] == "kit_componente", f"method=kit_componente (got {m['method']})")

    # 6) Otro proveedor NO debe cruzar (el matcher filtra por proveedor del envío)
    with database.get_db() as conn:
        cau = conn.execute("SELECT id FROM proveedores WHERE codigo_bodega='CAUPLAS'").fetchone()["id"]
        m_otro = match_conceptos_a_ventas(conn, cau, {"codigo": "KDTL-057", "descripcion": "x"})
    ok(m_otro is None, "no cruza si el proveedor del envío es otro")

    # 7) Listado de ventas expone kit_componentes
    import routers.ventas as ventas_router

    class FakeUser:
        rol = "admin"; proveedor_id = None
    out = ventas_router.listar(FakeUser(), deposito="todos")
    venta = next((i for i in out["items"] if i["num_venta"] == "2000016762751980"), None)
    ok(venta is not None, "la venta-kit aparece en el listado")
    comps_out = {(c["codigo"], c["cantidad"]) for c in venta["kit_componentes"]}
    ok(comps_out == {("KDTL-057-K", 1), ("KDTL-058-K", 1)}, f"listado trae componentes: {comps_out}")

    print("\n🎉 TODO OK")


if __name__ == "__main__":
    try:
        main()
    finally:
        Path(_tmpdb).unlink(missing_ok=True)
