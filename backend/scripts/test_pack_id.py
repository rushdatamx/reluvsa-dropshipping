"""
Regresión del `pack_id` — el número de venta que Gaby ve en el portal de Mercado Libre.

Contexto (junta con el cliente, 2026-07-31): el portal de ML le muestra a Gaby un número
distinto al que mostrábamos nosotros. Verificado contra la API real:

    order.id = 2000017689165588   ← lo que guardamos como num_venta (llave primaria)
    pack_id  = 2000014293955049   ← lo que ML le muestra a ella

Gaby trabaja TODOS LOS DÍAS con el número de ML, así que debe mostrarse y ser buscable.

⚠️ Lo que este test protege (y que un refactor puede romper en silencio):

1. `num_venta` (order.id) SIGUE siendo la llave: es lo que amarra factura, albarán y
   envío. El pack_id se AGREGA, nunca sustituye. Si alguien "simplifica" cambiando la
   PK, las ventas ya facturadas volverían a salir "Pendiente".
2. Sólo ~la MITAD de las órdenes trae pack_id (medido sobre 50 reales: 25 sí / 25 no).
   Por eso la UI y el CSV caen a num_venta cuando es NULL — si no, media tabla saldría
   vacía. En esas ventas ML muestra el propio order.id, así que el número coincide igual.
3. Se puede BUSCAR por el número de ML (es como lo van a usar a diario).
4. El upsert del sync no pisa un pack_id bueno con NULL (mismo criterio que `comprador`).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test_pack.db")

import database  # noqa: E402
from database import get_db  # noqa: E402
from services.sync_ml import _upsert_venta_api  # noqa: E402

ok = True


def chk(cond: bool, msg: str) -> None:
    global ok
    print(("✅ " if cond else "❌ ") + msg)
    ok = ok and bool(cond)


database.init_database()

ORDER_ID = "2000017689165588"   # caso real del cliente
PACK_ID = "2000014293955049"
SIN_PACK = "2000017691997376"   # caso real: venta de 1 ítem, ML no manda pack

STORES = {"por_store": {}, "por_node": {}}


def _orden(order_id, pack_id, sku, titulo):
    o = {
        "id": order_id,
        "status": "paid",
        "date_created": "2026-07-31T17:02:13.000-04:00",
        "total_amount": 319.96,
        "order_items": [{"quantity": 1, "item": {"seller_sku": sku, "title": titulo}}],
    }
    if pack_id:
        o["pack_id"] = pack_id
    return o


print("\n── El pack_id se guarda, sin tocar la llave ──")
with get_db() as conn:
    _upsert_venta_api(conn, _orden(ORDER_ID, PACK_ID, "R95390894",
                                   "Manguera Radiador Inferior Salida Cruze 1.8"), STORES, None)
    r = conn.execute("SELECT num_venta, pack_id FROM ventas_ml WHERE num_venta=?", (ORDER_ID,)).fetchone()

chk(r is not None, "la venta se guardó")
chk(r["num_venta"] == ORDER_ID, "num_venta SIGUE siendo el order.id (llave de factura/albarán/envío)")
chk(r["pack_id"] == PACK_ID, "pack_id guardado = el número que Gaby ve en ML")

print("\n── Venta sin pack (la mitad de los casos reales) ──")
with get_db() as conn:
    _upsert_venta_api(conn, _orden(SIN_PACK, None, "512195", "Producto de un solo ítem"), STORES, None)
    r2 = conn.execute("SELECT num_venta, pack_id FROM ventas_ml WHERE num_venta=?", (SIN_PACK,)).fetchone()
chk(r2["pack_id"] is None, "sin pack_id en la API → queda NULL (no se inventa)")
mostrado = r2["pack_id"] or r2["num_venta"]
chk(mostrado == SIN_PACK, "la UI/CSV muestran el num_venta → nunca una celda vacía")

print("\n── El upsert no pisa un pack_id bueno con NULL ──")
with get_db() as conn:
    # Re-sincronizar la misma orden pero sin pack (p.ej. respuesta parcial de ML).
    _upsert_venta_api(conn, _orden(ORDER_ID, None, "R95390894", "Manguera Radiador"), STORES, None)
    r3 = conn.execute("SELECT pack_id FROM ventas_ml WHERE num_venta=?", (ORDER_ID,)).fetchone()
chk(r3["pack_id"] == PACK_ID, "COALESCE conserva el pack_id previo (igual que con `comprador`)")

print("\n── Se puede BUSCAR por el número de ML ──")
# Mismo WHERE que routers/ventas.py::_construir_filtros
SQL = ("SELECT num_venta FROM ventas_ml WHERE "
       "(num_venta LIKE ? OR pack_id LIKE ? OR sku LIKE ? OR titulo LIKE ?)")
with get_db() as conn:
    for etiqueta, termino, esperado in (
        ("el número de ML completo", PACK_ID, ORDER_ID),
        ("sólo la terminación (049)", "049", ORDER_ID),
        ("el número interno", ORDER_ID, ORDER_ID),
        ("el SKU", "R95390894", ORDER_ID),
    ):
        like = f"%{termino}%"
        hits = [x["num_venta"] for x in conn.execute(SQL, (like, like, like, like)).fetchall()]
        chk(esperado in hits, f"buscando por {etiqueta} → encuentra la venta")

print("\n── El índice de búsqueda existe (la tabla trae ~27k filas) ──")
with get_db() as conn:
    idx = [x["name"] for x in conn.execute("PRAGMA index_list(ventas_ml)").fetchall()]
chk("idx_ventas_pack_id" in idx, "idx_ventas_pack_id creado por la migración")

print("\n── La migración es idempotente (corre en cada arranque) ──")
database.init_database()
with get_db() as conn:
    r4 = conn.execute("SELECT pack_id FROM ventas_ml WHERE num_venta=?", (ORDER_ID,)).fetchone()
chk(r4["pack_id"] == PACK_ID, "re-inicializar no pierde datos ni duplica la columna")

print("\n" + ("🎉 TODOS LOS CHECKS DE PACK_ID PASARON" if ok else "❌ HAY CHECKS FALLIDOS"))
sys.exit(0 if ok else 1)
