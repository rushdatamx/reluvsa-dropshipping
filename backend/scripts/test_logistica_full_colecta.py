"""Test E2E de la etiqueta FULL/COLECTA (envios_colecta.logistic_type) y del
nuevo default de la pestaña Ventas (mostrar MATRIZ).

Contexto (2026-08-03): el cliente pidió 2 cosas — ver las ventas de MATRIZ y
saber si una venta fue FULL o COLECTA. Al investigar la segunda resultó que
explicaba el "94% de envíos sin bodega" que se creía un bug de mapeo: en FULL la
mercancía sale de la bodega de Mercado Libre, así que ML manda origin=null y esos
envíos NUNCA van a tener bodega de proveedor. Verificado contra la API real de
producción (muestra aleatoria n=60 de 2026):
    fulfillment   + SIN bodega -> 34
    cross_docking + CON bodega -> 22
    cross_docking + SIN bodega ->  4   <- el único caso que sí es problema

Lo que este test fija (que un refactor NO debe romper):
  1. La migración es idempotente y crea columna + índice.
  2. El sync persiste logistic_type desde el shipment (sin llamadas extra a ML).
  3. El UPDATE usa COALESCE: un re-sync sin el campo NO borra la etiqueta buena.
  4. lugar_override de Gaby se sigue respetando (no hay regresión).
  5. El default de `deposito` ahora es "todos" (MATRIZ visible) — era "proveedores".
  6. El filtro `logistica` separa FULL de COLECTA correctamente.
  7. El CSV trae la columna Logística con la etiqueta legible.

Corre sin red (BD desechable en tmp). Uso: python3 backend/scripts/test_logistica_full_colecta.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="reluvsa_logistica_")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test.db")

import database  # noqa: E402
from database import get_db, init_database  # noqa: E402

FALLOS = []


def ok(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" | {msg}")
    if not cond:
        FALLOS.append(msg)


def _sembrar_envio(conn, num_envio, num_venta, lugar, logistic, deposito, sku="X-1"):
    """Crea una venta + su envío ya cruzado, como los deja el sync."""
    conn.execute(
        "INSERT INTO ventas_ml (num_venta, sku, deposito, fecha_venta, titulo) VALUES (?,?,?,?,?)",
        (num_venta, sku, deposito, "2026-07-15 10:00:00", f"Producto {sku}"),
    )
    prov = None
    if lugar:
        row = conn.execute(
            "SELECT id FROM proveedores WHERE codigo_bodega = ?", (lugar,)
        ).fetchone()
        prov = row["id"] if row else None
    conn.execute(
        """INSERT INTO envios_colecta
           (num_envio, num_venta, num_venta_ml, match_cruce_confianza, fecha_venta,
            titulo, lugar_indicado, proveedor_id, logistic_type)
           VALUES (?,?,?,1.0,?,?,?,?,?)""",
        (num_envio, num_venta, num_venta, "2026-07-15 10:00:00", f"Producto {sku}",
         lugar, prov, logistic),
    )


def main():
    print("\n=== 1. Migración: columna + índice, idempotente ===")
    init_database()
    init_database()  # 2a vez: debe ser no-op, no reventar
    with get_db() as conn:
        cols = {c["name"] for c in conn.execute("PRAGMA table_info(envios_colecta)").fetchall()}
        ok("logistic_type" in cols, "envios_colecta.logistic_type existe")
        idx = {r["name"] for r in conn.execute("PRAGMA index_list(envios_colecta)").fetchall()}
        ok("idx_envios_logistic_type" in idx, "índice idx_envios_logistic_type creado")
    print("  (init_database() corrió 2 veces sin error → idempotente)")

    print("\n=== 2. El sync persiste logistic_type desde el shipment ===")
    from services import sync_ml
    stores = {"por_node": {"NODE-KIM": "KIM"}, "por_store": {}}
    order = {
        "id": "9000001",
        "date_created": "2026-07-15T10:00:00.000-06:00",
        "order_items": [{"item": {"title": "Bomba de agua", "seller_sku": "KR-1"}}],
    }
    ship_colecta = {"id": "SHIP-CD", "origin": {"node": {"node_id": "NODE-KIM"}},
                    "logistic_type": "cross_docking"}
    ship_full = {"id": "SHIP-FF", "origin": None, "logistic_type": "fulfillment"}

    with get_db() as conn:
        sync_ml._upsert_envio_api(conn, order, ship_colecta, None, stores)
        order_full = dict(order, id="9000002")
        sync_ml._upsert_envio_api(conn, order_full, ship_full, None, stores)

    with get_db() as conn:
        cd = conn.execute("SELECT * FROM envios_colecta WHERE num_envio='SHIP-CD'").fetchone()
        ff = conn.execute("SELECT * FROM envios_colecta WHERE num_envio='SHIP-FF'").fetchone()
    ok(cd["logistic_type"] == "cross_docking", "COLECTA persistida como cross_docking")
    ok(cd["lugar_indicado"] == "KIM", "COLECTA conserva su bodega de origen (KIM)")
    ok(ff["logistic_type"] == "fulfillment", "FULL persistida como fulfillment")
    ok(ff["lugar_indicado"] is None,
       "FULL sin bodega (origin=null) — es lo CORRECTO, no un bug de mapeo")

    print("\n=== 3. Re-sync sin el campo NO borra la etiqueta (COALESCE) ===")
    ship_sin_campo = {"id": "SHIP-CD", "origin": {"node": {"node_id": "NODE-KIM"}}}
    with get_db() as conn:
        sync_ml._upsert_envio_api(conn, order, ship_sin_campo, None, stores)
        cd2 = conn.execute("SELECT * FROM envios_colecta WHERE num_envio='SHIP-CD'").fetchone()
    ok(cd2["logistic_type"] == "cross_docking",
       "un shipment sin logistic_type NO pisa el valor bueno con NULL")

    print("\n=== 4. Regresión: lugar_override de Gaby se sigue respetando ===")
    with get_db() as conn:
        conn.execute("UPDATE envios_colecta SET lugar_override='VAZLO' WHERE num_envio='SHIP-CD'")
    with get_db() as conn:
        sync_ml._upsert_envio_api(conn, order, ship_colecta, None, stores)
        cd3 = conn.execute(
            """SELECT e.lugar_override, p.codigo_bodega FROM envios_colecta e
               LEFT JOIN proveedores p ON p.id = e.proveedor_id
               WHERE e.num_envio='SHIP-CD'"""
        ).fetchone()
    ok(cd3["lugar_override"] == "VAZLO" and cd3["codigo_bodega"] == "VAZLO",
       "el override manual manda sobre lo que diga la API (sin regresión)")

    print("\n=== 5. Filtros del listado: default MATRIZ visible + logistica ===")
    from routers.ventas import _construir_filtros, _logistica_txt
    from models import UserInfo

    admin = UserInfo(user_id=1, email="a@b.c", rol="admin", proveedor_id=None)

    def _contar(**kw):
        base = dict(proveedor_id=None, estado=None, q=None, facturada=None, sla=None,
                    cruce=None, fecha_desde=None, fecha_hasta=None)
        base.update(kw)
        where, params, jf = _construir_filtros(admin, **base)
        sql = f"""SELECT COUNT(*) c FROM ventas_ml v
                  LEFT JOIN envios_colecta e ON e.num_venta_ml = v.num_venta
                  {jf} WHERE {' AND '.join(where)}"""
        with get_db() as conn:
            return conn.execute(sql, params).fetchone()["c"]

    with get_db() as conn:
        conn.execute("DELETE FROM envios_colecta")
        conn.execute("DELETE FROM ventas_ml")
        _sembrar_envio(conn, "E1", "V1", "KIM", "cross_docking", "KIM")
        _sembrar_envio(conn, "E2", "V2", "CAUPLAS", "cross_docking", "CAUPLAS")
        _sembrar_envio(conn, "E3", "V3", None, "fulfillment", "MATRIZ")
        _sembrar_envio(conn, "E4", "V4", None, "fulfillment", None)
        _sembrar_envio(conn, "E5", "V5", None, "self_service", "MATRIZ")

    ok(_contar(deposito=None, logistica=None) == 5,
       "default (sin deposito) muestra TODO incluida MATRIZ = 5  [antes ocultaba MATRIZ]")
    ok(_contar(deposito="todos", logistica=None) == 5, "deposito='todos' = 5")
    ok(_contar(deposito="proveedores", logistica=None) == 3,
       "deposito='proveedores' sigue ocultando MATRIZ = 3 (Gaby recupera la vista limpia)")
    ok(_contar(deposito="matriz", logistica=None) == 2, "deposito='matriz' = 2")

    ok(_contar(deposito=None, logistica="colecta") == 2, "logistica='colecta' = 2 (E1,E2)")
    ok(_contar(deposito=None, logistica="full") == 2, "logistica='full' = 2 (E3,E4)")
    ok(_contar(deposito=None, logistica="otros") == 1, "logistica='otros' = 1 (E5 Flex)")
    ok(_contar(deposito=None, logistica=None) == 5, "sin filtro de logística = las 5")

    print("\n=== 6. Combinación real: COLECTA sin bodega = el problema que SÍ queda ===")
    with get_db() as conn:
        _sembrar_envio(conn, "E6", "V6", None, "cross_docking", "KIM")
        fila = conn.execute(
            """SELECT COUNT(*) c FROM envios_colecta
               WHERE logistic_type='cross_docking' AND proveedor_id IS NULL"""
        ).fetchone()
    ok(fila["c"] == 1,
       "se puede aislar COLECTA-sin-bodega (el residuo real, ~7%) de las FULL")

    print("\n=== 7. Etiquetas legibles del CSV ===")
    ok(_logistica_txt("fulfillment") == "FULL", "fulfillment -> 'FULL'")
    ok(_logistica_txt("cross_docking") == "COLECTA", "cross_docking -> 'COLECTA'")
    ok(_logistica_txt(None) == "", "NULL -> '' (histórico sin backfill)")
    ok(_logistica_txt("tipo_nuevo_de_ml") == "tipo_nuevo_de_ml",
       "un tipo desconocido se muestra crudo, no se esconde")

    print("\n" + "=" * 62)
    total = 21
    if FALLOS:
        print(f"RESULTADO: {total - len(FALLOS)}/{total} — {len(FALLOS)} FALLA(S)")
        for f in FALLOS:
            print("  - " + f)
        return 1
    print(f"RESULTADO: {total}/{total} OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
