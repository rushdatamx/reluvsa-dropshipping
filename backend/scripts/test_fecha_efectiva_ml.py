#!/usr/bin/env python3
"""Regresión de date_closed como fecha efectiva y corrección puntual."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from scripts import corregir_fecha_venta_ml  # noqa: E402
from services import sync_ml  # noqa: E402


def ok(cond, msg):
    print(("✅" if cond else "❌") + " " + msg)
    if not cond:
        raise SystemExit(1)


STORES = {"por_store": {}, "por_node": {}}


def orden(oid, creada, cerrada=None):
    return {
        "id": oid,
        "date_created": creada,
        "date_closed": cerrada,
        "status": "paid",
        "order_items": [{"item": {"seller_sku": "SKU", "title": "Producto"}, "quantity": 1}],
        "shipping": {},
        "payments": [],
    }


database.init_database()

# La migración puede correrse repetidamente y no rellena filas legacy.
database.init_database()
with database.get_db() as conn:
    columnas = {r["name"] for r in conn.execute("PRAGMA table_info(ventas_ml)")}
ok("fecha_creacion_ml" in columnas, "migración idempotente agrega fecha_creacion_ml")

with database.get_db() as conn:
    sync_ml._upsert_venta_api(
        conn,
        orden("NUEVA-CERRADA", "2026-09-02T12:00:00-04:00", "2026-09-02T12:56:40-04:00"),
        STORES,
        None,
    )
    fila = conn.execute("SELECT * FROM ventas_ml WHERE num_venta='NUEVA-CERRADA'").fetchone()
ok(str(fila["fecha_creacion_ml"]) == "2026-09-02 10:00:00", "date_created se conserva en hora México")
ok(str(fila["fecha_venta"]) == "2026-09-02 10:56:40", "date_closed tiene prioridad y convierte -04:00 a México")

with database.get_db() as conn:
    sync_ml._upsert_venta_api(
        conn, orden("PENDIENTE", "2026-09-03T08:15:00-06:00"), STORES, None
    )
    antes = conn.execute("SELECT * FROM ventas_ml WHERE num_venta='PENDIENTE'").fetchone()
ok(antes["fecha_venta"] == antes["fecha_creacion_ml"], "sin date_closed usa date_created como fallback")

with database.get_db() as conn:
    sync_ml._upsert_venta_api(
        conn,
        orden("PENDIENTE", "2026-09-03T08:15:00-06:00", "2026-09-03T09:45:00-06:00"),
        STORES,
        None,
    )
    despues = conn.execute("SELECT * FROM ventas_ml WHERE num_venta='PENDIENTE'").fetchone()
ok(str(despues["fecha_venta"]) == "2026-09-03 09:45:00", "venta pendiente adopta date_closed cuando aparece")

with database.get_db() as conn:
    conn.execute(
        "INSERT INTO ventas_ml(num_venta,fecha_venta,titulo) VALUES('LEGACY','2026-08-01 07:00:00','Vieja')"
    )
    sync_ml._upsert_venta_api(
        conn,
        orden("LEGACY", "2026-08-01T14:00:00-06:00", "2026-08-01T15:00:00-06:00"),
        STORES,
        None,
    )
    legacy = conn.execute("SELECT * FROM ventas_ml WHERE num_venta='LEGACY'").fetchone()
ok(str(legacy["fecha_venta"]) == "2026-08-01 07:00:00", "venta histórica conserva fecha_venta al reconsultarse")
ok(legacy["fecha_creacion_ml"] is None, "venta histórica queda marcada NULL y no recibe backfill parcial")

# La herramienta simula por defecto y el modo ejecutar toca sólo las dos fechas.
with database.get_db() as conn:
    conn.execute(
        """INSERT INTO ventas_ml(num_venta,sku,fecha_venta,estado,titulo,total)
           VALUES('2000018209888406','NO-TOCAR','2026-09-02 10:55:01','Pagado','Original',123.45)"""
    )

respuesta = orden(
    "2000018209888406", "2026-09-02T12:55:01-04:00", "2026-09-02T12:56:40-04:00"
)
original_get = corregir_fecha_venta_ml.ml_client.get
corregir_fecha_venta_ml.ml_client.get = lambda path: respuesta
try:
    cambio = corregir_fecha_venta_ml.preparar_cambio("2000018209888406")
    with database.get_db() as conn:
        sim = dict(conn.execute("SELECT * FROM ventas_ml WHERE num_venta='2000018209888406'").fetchone())
    ok(sim["fecha_creacion_ml"] is None and sim["fecha_venta"] == "2026-09-02 10:55:01",
       "preparar/simular no modifica la base")

    corregir_fecha_venta_ml.aplicar_cambio(cambio)
    corregir_fecha_venta_ml.aplicar_cambio(cambio)
    with database.get_db() as conn:
        aplicada = dict(conn.execute("SELECT * FROM ventas_ml WHERE num_venta='2000018209888406'").fetchone())
    ok(aplicada["fecha_venta"] == "2026-09-02 10:56:40", "corrección puntual produce la fecha esperada")
    ok(aplicada["sku"] == "NO-TOCAR" and aplicada["titulo"] == "Original" and aplicada["total"] == 123.45,
       "corrección idempotente no altera columnas ajenas")

    corregir_fecha_venta_ml.ml_client.get = lambda path: {**respuesta, "id": "OTRA"}
    try:
        corregir_fecha_venta_ml.preparar_cambio("2000018209888406")
        ok(False, "rechaza respuesta de otra orden")
    except ValueError:
        ok(True, "rechaza respuesta de otra orden")
finally:
    corregir_fecha_venta_ml.ml_client.get = original_get

print("\nTodas las pruebas de fecha efectiva pasaron.")
