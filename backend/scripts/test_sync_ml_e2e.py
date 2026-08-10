"""E2E del job de sincronización con la API de ML (sin red: httpx.MockTransport).

Simula la cuenta RELUVSA multi-origen con 6 depósitos y 3 órdenes (KIM, MATRIZ y
una sin envío) servidas por /orders/search en 2 páginas. Verifica:
- backfill por ventanas de fecha + paginación por offset.
- upserts en ventas_ml (estado mapeado, unidades, depósito, comprador del envío).
- upserts en envios_colecta: cruce DIRECTO conf 1.0, proveedor vía store→bodega,
  MATRIZ → proveedor NULL, cumplio_sla desde /sla (404 tolerado).
- lugar_override de Gaby respetado al re-sincronizar; re-corrida idempotente.
- factura huérfana pre-sembrada queda cruzada (recruzar_conceptos_sin_match).
- run 'completado' + ultima_sync escrita; 2a corrida incremental funciona.
- incremental sin ultima_sync degrada a backfill.

Uso: backend/.venv/bin/python backend/scripts/test_sync_ml_e2e.py
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["ML_CLIENT_ID"] = "TEST_APP_ID"
os.environ["ML_CLIENT_SECRET"] = "TEST_APP_SECRET"
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx  # noqa: E402

import database  # noqa: E402
from services import ml_client, sync_ml  # noqa: E402


def ok(cond, msg):
    print(("✅" if cond else "❌") + " " + msg)
    if not cond:
        raise SystemExit(1)


SELLER = "999888777"
# Fecha de las órdenes: hace 10 días (cae en la última ventana del backfill).
ORDER_DT = datetime.now(timezone.utc) - timedelta(days=10)
ORDER_DT_STR = ORDER_DT.astimezone(timezone(timedelta(hours=-6))).isoformat(timespec="milliseconds")

STORES = [
    {"id": "100", "description": "MATRIZ", "network_node_id": "NODE-MATRIZ"},
    {"id": "101", "description": "KIM", "network_node_id": "NODE-KIM"},
    {"id": "102", "description": "CAUPLAS", "network_node_id": "NODE-CAUPLAS"},
    {"id": "103", "description": "VAZLO", "network_node_id": "NODE-VAZLO"},
    {"id": "104", "description": "AG", "network_node_id": "NODE-AG"},
    {"id": "105", "description": "KG", "network_node_id": "NODE-KG"},
]


def _order(oid, sku, title, store_id, ship_id, status="paid", tags=None, qty=1, total=100.0,
           pagos=None):
    return {
        "id": oid,
        "status": status,
        "tags": tags or [],
        "date_created": ORDER_DT_STR,
        "total_amount": total,
        "order_items": [{
            "item": {"seller_sku": sku, "title": title},
            "quantity": qty,
            "stock": {"store_id": store_id},
        }],
        "shipping": {"id": ship_id} if ship_id else {},
        "payments": [{"id": p} for p in (pagos or [])],
    }


ORDEN_A = _order("3000001", "9025125-Z", "Anillo Reluctor Cigueñal Aveo", "101", "SHIP-A", qty=2, total=350.5,
                 pagos=["PAY-1"])
ORDEN_B = _order("3000002", "MTZ-001", "Filtro de aire genérico", "100", "SHIP-B", tags=["delivered"],
                 pagos=["PAY-2A", "PAY-2B"])   # venta en mensualidades: los netos se SUMAN
ORDEN_C = _order("3000003", "KR-1095WP", "Bomba de agua KeepOnGreen", "105", None,
                 pagos=["PAY-3"])              # su collection no trae neto → NULL, no 0.0

# El "Total (MXN)" que ve Gaby: net_received_amount de /collections/{payment_id}.
# NO se calcula restando cargos: los impuestos no vienen en la Orders API.
COLLECTIONS = {
    "PAY-1": {"net_received_amount": 253.62},
    "PAY-2A": {"net_received_amount": 40.0},
    "PAY-2B": {"net_received_amount": 30.25},
    "PAY-3": {"net_received_amount": None},
}

SHIPMENTS = {
    # SHIP-A: cross_docking (COLECTA) = dropshipping real, con bodega de proveedor.
    "3000001": {"id": "SHIP-A", "origin": {"node": {"node_id": "NODE-KIM"}},
                "logistic_type": "cross_docking",
                "receiver_address": {"receiver_name": "Juan Pérez"}},
    # SHIP-B: fulfillment (FULL) = lo despacha ML desde su bodega, no es dropshipping.
    "3000002": {"id": "SHIP-B", "origin": {"node": {"node_id": "NODE-MATRIZ"}},
                "logistic_type": "fulfillment",
                "receiver_address": {"receiver_name": "Ana López"}},
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    params = dict(request.url.params)

    if path == "/users/me":
        return httpx.Response(200, json={
            "id": int(SELLER), "nickname": "RELUVSA",
            "tags": ["warehouse_management", "multiwarehouse"],
        })
    if path == f"/users/{SELLER}/stores/search":
        return httpx.Response(200, json={"results": STORES})
    if path == "/orders/search":
        # Backfill: solo la ventana que contiene ORDER_DT trae resultados.
        f, t = params.get("order.date_created.from"), params.get("order.date_created.to")
        if f and t:
            f_dt = datetime.fromisoformat(f)
            t_dt = datetime.fromisoformat(t)
            if not (f_dt <= ORDER_DT < t_dt):
                return httpx.Response(200, json={"results": [], "paging": {"total": 0}})
        # 2 páginas: offset 0 → A y B; offset 50 → C (total 52 fuerza la 2a página).
        offset = int(params.get("offset") or 0)
        if offset == 0:
            resultados = [ORDEN_A, ORDEN_B]
        elif offset == 50:
            resultados = [ORDEN_C]
        else:
            resultados = []
        return httpx.Response(200, json={"results": resultados, "paging": {"total": 52}})
    if path.startswith("/orders/") and path.endswith("/shipments"):
        oid = path.split("/")[2]
        ship = SHIPMENTS.get(oid)
        return httpx.Response(200, json=ship) if ship else httpx.Response(404, json={})
    if path.startswith("/collections/"):
        pid = path.split("/")[2]
        col = COLLECTIONS.get(pid)
        if col == "BOOM":   # fallo duro de ML (get_opcional NO lo absorbe: lanza MLError)
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(200, json=col) if col is not None else httpx.Response(404, json={})
    if path == "/shipments/SHIP-A/sla":
        return httpx.Response(200, json={"status": "on_time", "expected_date": ORDER_DT_STR})
    if path == "/shipments/SHIP-B/sla":
        return httpx.Response(404, json={"message": "sla not available"})
    return httpx.Response(404, json={"message": f"sin fixture para {path}"})


def _instalar_mock():
    transport = httpx.MockTransport(_handler)
    ml_client._http = lambda: httpx.Client(transport=transport, timeout=30.0)


def _sembrar_token():
    ahora = datetime.utcnow()
    with database.get_db() as conn:
        conn.execute("DELETE FROM ml_tokens")
        conn.execute(
            """INSERT INTO ml_tokens (id, access_token, refresh_token, token_type, scope,
                                      ml_user_id, expira_en, obtenido_en, actualizado_en)
               VALUES (1, 'ACCESS_TEST', 'REFRESH_TEST', 'Bearer', 'read', ?, ?, ?, ?)""",
            (SELLER, (ahora + timedelta(hours=5)).isoformat(timespec="seconds"),
             ahora.isoformat(timespec="seconds"), ahora.isoformat(timespec="seconds")),
        )


def _correr_sync(tipo):
    res = sync_ml.iniciar_sync(tipo)
    # La sync corre en un thread daemon: esperar a que la run cierre.
    for _ in range(300):
        with database.get_db() as conn:
            run = conn.execute("SELECT * FROM ml_sync_runs WHERE id=?", (res["run_id"],)).fetchone()
        if run and run["estado"] != "en_curso":
            return res, dict(run)
        time.sleep(0.1)
    raise SystemExit("timeout esperando la sync")


def main():
    database.init_database()
    _instalar_mock()
    _sembrar_token()

    with database.get_db() as conn:
        kim = conn.execute("SELECT id FROM proveedores WHERE codigo_bodega='KIM'").fetchone()["id"]
        cauplas = conn.execute("SELECT id FROM proveedores WHERE codigo_bodega='CAUPLAS'").fetchone()["id"]
        # Factura huérfana ANTES de la sync (cruce retroactivo).
        cur = conn.execute(
            """INSERT INTO facturas (proveedor_id, uuid_cfdi, serie, folio, rfc_emisor, rfc_receptor)
               VALUES (?, 'UUID-SYNC-1', 'K', '99001', 'KAC1601193F6', 'GPE230915JWA')""",
            (kim,),
        )
        conn.execute(
            """INSERT INTO factura_conceptos (factura_id, codigo_prov, descripcion, num_venta_match)
               VALUES (?, '9025125-Z', 'Anillo Reluctor Cigueñal Aveo', NULL)""",
            (cur.lastrowid,),
        )

    # --- 1a corrida: pedimos incremental; sin ultima_sync debe degradar a backfill ---
    res, run = _correr_sync("incremental")
    ok(res["tipo"] == "backfill", "incremental sin ultima_sync degrada a backfill")
    ok(run["estado"] == "completado", f"la corrida terminó 'completado' (detalle: {run['detalle'][:120]}...)")
    ok(run["ordenes_vistas"] == 3 and run["ventas_upsert"] == 3 and run["envios_upsert"] == 2,
       "contadores: 3 órdenes vistas, 3 ventas, 2 envíos")
    ok(run["errores"] == 0, "0 errores en la corrida")

    with database.get_db() as conn:
        va = conn.execute("SELECT * FROM ventas_ml WHERE num_venta='3000001'").fetchone()
        vb = conn.execute("SELECT * FROM ventas_ml WHERE num_venta='3000002'").fetchone()
        vc = conn.execute("SELECT * FROM ventas_ml WHERE num_venta='3000003'").fetchone()
        ea = conn.execute("SELECT * FROM envios_colecta WHERE num_envio='SHIP-A'").fetchone()
        eb = conn.execute("SELECT * FROM envios_colecta WHERE num_envio='SHIP-B'").fetchone()

    ok(va and va["sku"] == "9025125-Z" and va["unidades"] == 2 and va["total"] == 350.5,
       "venta KIM: sku/unidades/total correctos")
    ok(va["estado"] == "Pagado" and vb["estado"] == "Entregado",
       "estado mapeado: paid→Pagado, tag delivered→Entregado")
    ok(va["deposito"] == "KIM" and vb["deposito"] == "MATRIZ",
       "depósito desde stock.store_id → catálogo de stores")
    ok(va["comprador"] == "Juan Pérez", "comprador persistido desde receiver_name del envío")
    ok(va["fecha_venta"] is not None, "fecha de venta ISO parseada")
    ok(vc is not None and eb is not None, "venta sin envío y envío MATRIZ existen")

    # --- "Total (MXN)": el neto que ML deposita (pedido de Gaby 2026-08-10) ---
    ok(va["total_neto"] == 253.62,
       "total_neto = net_received_amount de /collections (NO se calcula restando cargos)")
    ok(va["total"] == 350.5 and va["total_neto"] == 253.62,
       "`total` (ingresos por productos) se CONSERVA junto al neto, no se sustituye")
    ok(vb["total_neto"] == 70.25,
       "venta con 2 pagos (mensualidades): los netos se SUMAN (40.0 + 30.25)")
    ok(vc["total_neto"] is None,
       "collection sin neto → NULL, NUNCA 0.0 (un 0.0 se leería como 'no dejó nada')")

    ok(ea["num_venta_ml"] == "3000001" and ea["match_cruce_confianza"] == 1.0,
       "cruce venta↔envío DIRECTO por ID con confianza 1.0")
    ok(ea["proveedor_id"] == kim and ea["lugar_indicado"] == "KIM",
       "proveedor del envío resuelto vía origin.node_id → store → LUGAR_A_BODEGA")
    ok(eb["proveedor_id"] is None and eb["lugar_indicado"] == "MATRIZ",
       "MATRIZ (bodega propia) queda SIN proveedor — correcto, no es dropshipping")
    ok(ea["cumplio_sla"] == 1 and eb["cumplio_sla"] is None,
       "cumplio_sla: on_time→1; /sla 404 → NULL (no inventamos SLA)")

    with database.get_db() as conn:
        concepto = conn.execute(
            "SELECT num_venta_match, match_method FROM factura_conceptos WHERE codigo_prov='9025125-Z'"
        ).fetchone()
        stores_bd = conn.execute("SELECT COUNT(*) c FROM ml_stores").fetchone()["c"]
        bodega_kim = conn.execute(
            "SELECT codigo_bodega FROM ml_stores WHERE description='KIM'"
        ).fetchone()["codigo_bodega"]
        matriz = conn.execute(
            "SELECT codigo_bodega FROM ml_stores WHERE description='MATRIZ'"
        ).fetchone()["codigo_bodega"]
        ultima_sync = sync_ml.get_config(conn, "ultima_sync")
        nickname = sync_ml.get_config(conn, "nickname")

    ok(concepto["num_venta_match"] == "3000001",
       f"factura huérfana pre-sembrada quedó CRUZADA por la sync (method={concepto['match_method']})")
    ok(stores_bd == 6 and bodega_kim == "KIM" and matriz is None,
       "catálogo ml_stores: 6 depósitos, KIM→bodega KIM, MATRIZ→NULL")
    ok(ultima_sync is not None and nickname == "RELUVSA", "ultima_sync y nickname persistidos")

    # --- Override de Gaby + re-corrida: el override MANDA sobre la API ---
    with database.get_db() as conn:
        conn.execute(
            "UPDATE envios_colecta SET lugar_override='CAUPLAS', proveedor_id=? WHERE num_envio='SHIP-A'",
            (cauplas,),
        )
        antes = {
            "ventas": conn.execute("SELECT COUNT(*) c FROM ventas_ml").fetchone()["c"],
            "envios": conn.execute("SELECT COUNT(*) c FROM envios_colecta").fetchone()["c"],
        }

    res2, run2 = _correr_sync("incremental")
    ok(res2["tipo"] == "incremental" and run2["estado"] == "completado",
       "2a corrida: incremental real (con ultima_sync) y completada")

    with database.get_db() as conn:
        ea2 = conn.execute("SELECT * FROM envios_colecta WHERE num_envio='SHIP-A'").fetchone()
        despues = {
            "ventas": conn.execute("SELECT COUNT(*) c FROM ventas_ml").fetchone()["c"],
            "envios": conn.execute("SELECT COUNT(*) c FROM envios_colecta").fetchone()["c"],
        }
    ok(ea2["proveedor_id"] == cauplas and ea2["lugar_override"] == "CAUPLAS",
       "lugar_override de Gaby RESPETADO al re-sincronizar (manda sobre la API)")
    ok(antes == despues, f"re-corrida idempotente: mismos conteos {despues}")

    # --- El COALESCE: un neto bueno ya guardado NO se pisa con NULL ---
    # Si ML deja de devolver el dato en una corrida posterior (403/500, o un pago
    # archivado), el monto que Gaby ya veía debe SEGUIR ahí. Sin COALESCE, una sola
    # corrida degradada vaciaría la columna en toda la tabla.
    with database.get_db() as conn:
        neto_previo = conn.execute(
            "SELECT total_neto FROM ventas_ml WHERE num_venta='3000001'").fetchone()["total_neto"]
    guardado = COLLECTIONS["PAY-1"]
    COLLECTIONS["PAY-1"] = {"net_received_amount": None}   # ML deja de mandarlo
    try:
        _correr_sync("incremental")
        with database.get_db() as conn:
            v = conn.execute("SELECT total_neto FROM ventas_ml WHERE num_venta='3000001'").fetchone()
        ok(neto_previo == 253.62 and v["total_neto"] == 253.62,
           "COALESCE: un neto ya guardado NO se pisa con NULL en una corrida posterior")
    finally:
        COLLECTIONS["PAY-1"] = guardado

    # --- Un fallo del neto NO puede costar la venta ni el envío ---
    # El neto es un dato de reporte; venta y envío son el dato de negocio. Si
    # /collections responde 403/500, la orden debe guardarse igual con neto NULL.
    with database.get_db() as conn:
        conn.execute("UPDATE ventas_ml SET total_neto=NULL WHERE num_venta='3000001'")
        conn.execute("DELETE FROM envios_colecta WHERE num_envio='SHIP-A'")
    original = COLLECTIONS.pop("PAY-1")
    try:
        # Sin fixture, el handler responde 404 → get_opcional devuelve None.
        # Se fuerza el caso duro (500) para ejercitar el try/except del sync.
        COLLECTIONS["PAY-1"] = "BOOM"
        _correr_sync("incremental")
        with database.get_db() as conn:
            v = conn.execute("SELECT * FROM ventas_ml WHERE num_venta='3000001'").fetchone()
            e = conn.execute("SELECT * FROM envios_colecta WHERE num_envio='SHIP-A'").fetchone()
        ok(v is not None and v["total"] == 350.5 and v["total_neto"] is None,
           "si /collections falla: la VENTA se guarda igual, con total_neto NULL")
        ok(e is not None, "si /collections falla: el ENVÍO tampoco se pierde")
    finally:
        COLLECTIONS["PAY-1"] = original

    print("\n🎉 E2E DEL SYNC ML COMPLETO — TODO PASÓ")


if __name__ == "__main__":
    try:
        main()
    finally:
        Path(_tmpdb).unlink(missing_ok=True)
