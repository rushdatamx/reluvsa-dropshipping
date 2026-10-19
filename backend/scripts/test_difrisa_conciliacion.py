"""Prueba local del cruce DIFIRSA: referencias secundarias, split y exclusividad."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(prefix="test_difrisa_"), "test.db")

from database import get_db, init_database  # noqa: E402
from models import UserInfo  # noqa: E402
from routers.facturas import (  # noqa: E402
    corregir_referencias_difrisa, proponer_difrisa, confirmar_difrisa,
)

init_database()
admin = UserInfo(user_id=1, email="admin@test.local", rol="admin", proveedor_id=None)

with get_db() as conn:
    difrisa = conn.execute("SELECT id FROM proveedores WHERE codigo_bodega='DIFIRSA'").fetchone()["id"]
    conn.execute("INSERT INTO usuarios (id,email,password_hash,rol) VALUES (1,'admin@test.local','x','admin')")
    referencias = ["2000018000000001", "2000018000000002"]
    ventas = [
        ("SEC-1", referencias[0], "SKU-A", 1),
        ("SEC-2", referencias[1], "SKU-A", 2),
        # Coincide en número, pero es de la cuenta principal y no debe aparecer.
        ("PRI-1", referencias[0], "SKU-A", 99),
    ]
    conn.executemany(
        """INSERT INTO ventas_ml (num_venta,num_venta_origen,cuenta_ml,sku,unidades,
                                  fecha_venta,titulo)
           VALUES (?,?,?, ?,?, '2026-10-01','Pieza A')""",
        [(n, origen, "secundaria" if n.startswith("SEC") else "principal", sku, qty)
         for n, origen, sku, qty in ventas],
    )
    conn.executemany(
        """INSERT INTO envios_colecta (num_envio,cuenta_ml,num_venta_ml,proveedor_id)
           VALUES (?, 'secundaria', ?, ?)""",
        [("ENV-" + n, n, difrisa) for n in ("SEC-1", "SEC-2")],
    )
    conn.execute(
        """INSERT INTO facturas (id,proveedor_id,uuid_cfdi,fecha_factura,total)
           VALUES (1,?,'uuid-test','2026-10-02',300)""", (difrisa,),
    )
    conn.execute(
        """INSERT INTO factura_conceptos (id,factura_id,codigo_prov,descripcion,cantidad,importe)
           VALUES (1,1,'SKU-A','Pieza A',3,300)"""
    )

# El prefijo 20000 se conserva y las dos ventas secundarias quedan como candidatas.
conciliacion = corregir_referencias_difrisa(1, {"referencias": [r[-11:] for r in referencias]}, admin)
assert [r["num_venta_origen"] for r in conciliacion["referencias"]] == referencias
assert {v["num_venta"] for v in conciliacion["conceptos"][0]["candidatas"]} == {"SEC-1", "SEC-2"}

propuesta = proponer_difrisa(1, admin)
assert {(p["num_venta"], p["cantidad"]) for p in propuesta["propuestas"]} == {("SEC-1", 1.0), ("SEC-2", 2.0)}
confirmada = confirmar_difrisa(1, {"asignaciones": propuesta["propuestas"]}, admin)
assert sum(a["cantidad"] for a in confirmada["conceptos"][0]["asignaciones"]) == 3

with get_db() as conn:
    relaciones = conn.execute(
        "SELECT num_venta_match,cantidad_asignada FROM factura_concepto_ventas WHERE factura_id=1"
    ).fetchall()
    assert {(r["num_venta_match"], r["cantidad_asignada"]) for r in relaciones} == {
        ("SEC-1", 1.0), ("SEC-2", 2.0)
    }
    assert conn.execute("SELECT num_venta_match,importe FROM factura_conceptos WHERE id=1").fetchone()["num_venta_match"] is None
    conn.execute("INSERT INTO facturas (id,proveedor_id,uuid_cfdi) VALUES (2,?,'uuid-test-2')", (difrisa,))
    conn.execute("INSERT INTO factura_conceptos (id,factura_id,cantidad) VALUES (2,2,1)")
    try:
        conn.execute(
            """INSERT INTO factura_asignaciones (factura_id,concepto_id,num_venta_match,cantidad,estado)
               VALUES (2,2,'SEC-1',1,'confirmada')"""
        )
        raise AssertionError("el trigger permitió ocupar SEC-1 en una segunda factura")
    except Exception as exc:
        assert "ya está asociada" in str(exc) or "ocupada" in str(exc)

print("✅ DIFIRSA: 20000 restaurado, sólo secundaria, split completo, vista efectiva y exclusividad")
