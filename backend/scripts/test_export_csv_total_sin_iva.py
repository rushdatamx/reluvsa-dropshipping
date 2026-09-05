"""Regresión de la columna derivada ``Total sin IVA (MXN)`` del CSV de Ventas."""
import asyncio
import csv
import io
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ["DATABASE_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="test_export_csv_total_sin_iva_"), "test.db"
)

import database  # noqa: E402
from database import get_db  # noqa: E402
from models import UserInfo  # noqa: E402
from routers.ventas import export_csv  # noqa: E402

ok = True


def chk(cond, msg):
    global ok
    print(("✅ " if cond else "❌ ") + msg)
    ok = ok and bool(cond)


async def cuerpo_csv(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


def exportar(**filtros):
    response = export_csv(user=ADMIN, **filtros)
    return list(csv.DictReader(io.StringIO(asyncio.run(cuerpo_csv(response)))))


database.init_database()
ADMIN = UserInfo(user_id=1, email="admin@test.local", rol="admin", proveedor_id=None)

with get_db() as conn:
    conn.execute(
        "INSERT INTO proveedores (id,nombre,rfc,codigo_bodega,activo) VALUES (91,'Proveedor CSV','AAA010101AAA','CSV',1)"
    )
    conn.execute(
        "INSERT INTO ventas_ml (num_venta,sku,fecha_venta,estado,titulo,total,total_neto) "
        "VALUES ('V-219','SKU-219','2026-09-01 10:00:00','Entregado','Producto 219',250,219)"
    )
    conn.execute(
        "INSERT INTO ventas_ml (num_venta,sku,fecha_venta,estado,titulo,total,total_neto) "
        "VALUES ('V-NULL','SKU-NULL','2026-09-01 11:00:00','Entregado','Producto sin neto',250,NULL)"
    )
    conn.execute(
        "INSERT INTO ventas_ml (num_venta,sku,fecha_venta,estado,titulo,total,total_neto) "
        "VALUES ('V-CANCELADA','SKU-C','2026-09-01 12:00:00','Cancelada','No debe exportarse',250,116)"
    )
    conn.execute(
        "INSERT INTO envios_colecta "
        "(num_envio,num_venta_ml,lugar_indicado,lugar_override,proveedor_id,cumplio_sla,logistic_type) "
        "VALUES ('ENV-219','V-219','Bodega original','Bodega asignada',91,1,'cross_docking')"
    )
    # El listado pagina por defecto; el export debe conservar todas estas filas.
    for i in range(51):
        conn.execute(
            "INSERT INTO ventas_ml (num_venta,sku,fecha_venta,estado,titulo,total,total_neto) "
            "VALUES (?,?,?,?,?,?,?)",
            (f"V-EXTRA-{i}", "SKU-EXTRA", "2026-09-02 10:00:00", "Entregado",
             "Fila adicional", 100, 116),
        )
    conn.commit()


filas = exportar(estado="Entregado")
encabezado = list(filas[0].keys())
idx_total = encabezado.index("Total (MXN)")
chk(
    encabezado[idx_total + 1] == "Total sin IVA (MXN)"
    and encabezado[idx_total + 2] == "Num envio",
    "El encabezado Total sin IVA queda entre Total y Num envio",
)

por_venta = {fila["Num venta interno"]: fila for fila in filas}
fila_219 = por_venta["V-219"]
chk(fila_219["Total (MXN)"] == "219.0" and fila_219["Total sin IVA (MXN)"] == "188.79",
    "219 genera Total sin IVA de 188.79 con dos decimales")
chk(
    por_venta["V-NULL"]["Total (MXN)"] == ""
    and por_venta["V-NULL"]["Total sin IVA (MXN)"] == "",
    "Un Total (MXN) NULL conserva ambas celdas vacías",
)
chk(
    fila_219["Num envio"] == "ENV-219"
    and fila_219["Logistica"] == "COLECTA"
    and fila_219["Lugar indicado"] == "Bodega original"
    and fila_219["Bodega override"] == "Bodega asignada"
    and fila_219["Proveedor"] == "Proveedor CSV"
    and fila_219["SLA"] == "A tiempo",
    "Las columnas posteriores conservan sus valores tras insertar la nueva columna",
)
chk("V-CANCELADA" not in por_venta and len(filas) == 53,
    "El CSV respeta filtros y exporta todas las 53 filas Entregado sin paginación")

print("\nRESULTADO:", "TODO OK ✅" if ok else "HAY FALLAS ❌")
sys.exit(0 if ok else 1)
