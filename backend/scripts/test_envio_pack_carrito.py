"""
BUG A: el envío del carrito cubre a las N ventas del paquete (reportado por Gaby, 2026-08-17).

EL PROBLEMA
-----------
Mercado Libre crea UN SOLO envío por carrito y lo cuelga de UNA SOLA de las N órdenes.
Las demás ventas del pack quedaban sin envío -> sin proveedor -> INVISIBLES para el
matcher (que sólo busca `WHERE e.proveedor_id = ?`). Gaby lo describió como: "este número
de venta sólo viene asociado a un sku cuando la venta es de 2 skus".

Su caso real (verificado en prod): pack 2000014490469643 con CAU4218 (envío + factura) y
CAU3832 (sin envío, sin factura, invisible).

LO QUE FIJAN ESTOS TESTS
------------------------
 1. La venta hermana del pack encuentra el envío, su proveedor y su logística.
 2. 🔴 EL SLA NO SE INFLA: el envío sigue contando UNA vez por paquete, no N veces.
    Es la decisión explícita de Gaby ("como 1 retraso"). Es el test más importante.
 3. El matcher YA VE la venta hermana y puede cruzarle su factura.
 4. 🔴 NUNCA se cruza pack_id contra num_venta (los 268 números ambiguos de prod).
 5. Un pack_id NULL en ambos lados NO une nada (si no, producto cartesiano).
 6. El listado de Ventas no duplica filas cuando una venta tiene 2 envíos.
 7. El desempate del matcher es DETERMINISTA (las hermanas comparten fecha al segundo).
 8. La migración es idempotente y puebla pack_id desde la venta cruzada.

Corre sin red, con BD desechable.
    python3 backend/scripts/test_envio_pack_carrito.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="reluvsa_envio_pack_")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test.db")

from database import get_db, init_database  # noqa: E402
from models import UserInfo  # noqa: E402
from routers.metricas import metricas_proveedores  # noqa: E402
from routers.ventas import listar  # noqa: E402
from services.envio_pack import ENVIO_CUBRE_VENTA  # noqa: E402
from services.matcher import match_conceptos_a_ventas  # noqa: E402

FALLOS = []


def ok(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" | {msg}")
    if not cond:
        FALLOS.append(msg)


ADMIN = UserInfo(user_id=1, email="admin@test.local", rol="admin", proveedor_id=None)


def _pid(conn, codigo):
    return conn.execute(
        "SELECT id FROM proveedores WHERE codigo_bodega = ?", (codigo,)
    ).fetchone()["id"]


def _venta(conn, num_venta, sku, pack_id=None, fecha="2026-08-12 01:14:53"):
    conn.execute(
        """INSERT INTO ventas_ml (num_venta, sku, fecha_venta, titulo, pack_id, estado)
           VALUES (?,?,?,?,?,'Entregado')""",
        (num_venta, sku, fecha, f"Manguera {sku}", pack_id),
    )


def _envio(conn, num_envio, num_venta_ml, proveedor_id, pack_id=None,
           logistic="cross_docking", cumplio_sla=0):
    conn.execute(
        """INSERT INTO envios_colecta
           (num_envio, num_venta, num_venta_ml, match_cruce_confianza, fecha_venta,
            titulo, lugar_indicado, proveedor_id, cumplio_sla, logistic_type, pack_id,
            excluido_analisis)
           VALUES (?,?,?,1.0,'2026-08-12 01:14:53','Manguera','CAUPLAS',?,?,?,?,0)""",
        (num_envio, num_venta_ml, num_venta_ml, proveedor_id, cumplio_sla, logistic, pack_id),
    )


def _fila_venta(num_venta, **kw):
    """La fila del listado de Ventas para una venta concreta."""
    r = listar(user=ADMIN, estado="todas", limit=200, **kw)
    for it in r["items"]:
        if it["num_venta"] == num_venta:
            return it
    return None


print("\n=== BUG A: el envío del carrito cubre a las ventas hermanas ===\n")

init_database()

# El caso exacto que reportó Gaby.
PACK = "2000014490469643"
V_CON_ENVIO = "2000017891678512"   # CAU4218: ML le colgó el envío
V_HERMANA = "2000017891680168"     # CAU3832: quedaba invisible

print("[1] La venta hermana encuentra el envío del paquete")

with get_db() as conn:
    cauplas = _pid(conn, "CAUPLAS")
    _venta(conn, V_CON_ENVIO, "CAU4218", pack_id=PACK)
    _venta(conn, V_HERMANA, "CAU3832", pack_id=PACK)
    # UN solo envío para las dos ventas, como lo manda ML. Llega tarde (cumplio_sla=0).
    _envio(conn, "47748773289", V_CON_ENVIO, cauplas, pack_id=PACK, cumplio_sla=0)
    conn.commit()

f_hermana = _fila_venta(V_HERMANA)
ok(f_hermana is not None, "la venta hermana sigue apareciendo en el listado")
ok(f_hermana and f_hermana["num_envio"] == "47748773289",
   f"la hermana YA resuelve su envío (got {f_hermana and f_hermana['num_envio']})")
ok(f_hermana and f_hermana["proveedor_nombre"] == "QUALITY HOSES",
   f"y hereda el proveedor del paquete (got {f_hermana and f_hermana['proveedor_nombre']})")
ok(f_hermana and f_hermana["logistic_type"] == "cross_docking",
   "y su logística (antes salía '—')")
ok(f_hermana and f_hermana["cumplio_sla"] == 0,
   "y el SLA del paquete")

# ------------------------------------------------------------------ 2. EL SLA NO SE INFLA
print("\n[2] 🔴 El SLA cuenta UNA vez por paquete, no una por venta")

fila = [x for x in metricas_proveedores(user=ADMIN) if x["codigo_bodega"] == "CAUPLAS"][0]
ok(fila["total_envios"] == 1,
   f"⚠️ total_envios = 1 pese a las 2 ventas del carrito (got {fila['total_envios']})")
ok(fila["porcentaje_entregas_a_tiempo"] == 0.0,
   f"el retraso pesa UNA vez, no dos (got {fila['porcentaje_entregas_a_tiempo']}%)")

# La prueba directa contra la tabla: propagamos el VÍNCULO, nunca la FILA.
with get_db() as conn:
    n = conn.execute("SELECT COUNT(*) c FROM envios_colecta").fetchone()["c"]
ok(n == 1, f"envios_colecta sigue con UNA fila (got {n}) — no se duplicaron envíos")

# --------------------------------------------------------------- 3. el matcher YA la ve
print("\n[3] El matcher ve la venta hermana y le cruza su factura")

with get_db() as conn:
    m = match_conceptos_a_ventas(
        conn, cauplas, {"codigo": "CAU3832", "descripcion": "Manguera CAU3832"},
        fecha_factura="2026-08-13 10:00:00",
    )
ok(m is not None, "la factura del SKU de la hermana ya encuentra su venta")
ok(m and m["num_venta"] == V_HERMANA,
   f"y cruza a la venta correcta del carrito (got {m and m['num_venta']})")

# --------------------------------------- 4. jamás pack_id contra num_venta (los 268)
print("\n[4] 🔴 NUNCA se cruza pack_id contra num_venta")

sql = ENVIO_CUBRE_VENTA.replace(" ", "").replace("\n", "")
ok("e.pack_id=v.pack_id" in sql,
   "el vínculo por paquete es pack_id contra pack_id")
ok("e.pack_id=v.num_venta" not in sql and "e.num_venta_ml=v.pack_id" not in sql,
   "⚠️ y NUNCA mezcla las dos llaves (en prod 268 números son order.id de una venta "
   "y pack_id de otra)")

# La prueba de comportamiento, no sólo de texto: una venta cuyo num_venta es el pack_id
# de OTRA no debe heredar su envío.
with get_db() as conn:
    kim = _pid(conn, "KIM")
    _venta(conn, "PACK-COLISION", "KIM-1")          # su num_venta ES el pack de la otra
    _venta(conn, "V-OTRA", "KIM-2", pack_id="PACK-COLISION")
    _envio(conn, "E-OTRA", "V-OTRA", kim, pack_id="PACK-COLISION")
    conn.commit()

f_colision = _fila_venta("PACK-COLISION")
ok(f_colision and f_colision["num_envio"] is None,
   f"la venta cuyo num_venta coincide con un pack_id ajeno NO hereda ese envío "
   f"(got {f_colision and f_colision['num_envio']})")

# ------------------------------------------------- 5. NULL == NULL no une nada
print("\n[5] 🔴 Dos pack_id NULL no se unen (si no: producto cartesiano)")

with get_db() as conn:
    vazlo = _pid(conn, "VAZLO")
    _venta(conn, "V-SIN-PACK", "VAZ-1")             # pack_id NULL
    _envio(conn, "E-SIN-PACK", "V-OTRA-SIN", vazlo)  # pack_id NULL, de otra venta
    conn.commit()

f_sin = _fila_venta("V-SIN-PACK")
ok(f_sin and f_sin["num_envio"] is None,
   f"una venta sin pack NO adopta un envío sin pack (got {f_sin and f_sin['num_envio']})")

# --------------------------------------------- 6. no duplica filas en el listado
print("\n[6] Una venta con 2 envíos sale en UNA fila, no en dos")

with get_db() as conn:
    ag = _pid(conn, "AG")
    _venta(conn, "V-REENVIO", "AG-1", pack_id="PACK-REENVIO")
    _envio(conn, "E-RE-1", "V-REENVIO", None, pack_id="PACK-REENVIO", logistic="fulfillment")
    _envio(conn, "E-RE-2", "V-REENVIO", ag, pack_id="PACK-REENVIO")
    conn.commit()

r = listar(user=ADMIN, estado="todas", limit=200)
apariciones = [x for x in r["items"] if x["num_venta"] == "V-REENVIO"]
ok(len(apariciones) == 1,
   f"la venta con 2 envíos aparece UNA vez (got {len(apariciones)}) — si no, Gaby las "
   f"leería como ventas duplicadas")
ok(apariciones and apariciones[0]["proveedor_id"] == ag,
   "y se queda con el envío que SÍ trae proveedor, no con el vacío")
# El total de la paginación tiene que cuadrar con las filas devueltas.
ok(r["total"] == len(r["items"]),
   f"el total de paginación cuadra con las filas mostradas ({r['total']} vs {len(r['items'])})")

# ---------------------------------------------------- 7. desempate determinista
print("\n[7] El desempate entre hermanas es determinista")

with get_db() as conn:
    kg = _pid(conn, "KG")
    # Dos hermanas con el MISMO SKU y la MISMA fecha al segundo: en prod 1,071 de 1,073
    # pares comparten fecha exacta, así que el ORDER BY tiene que romper el empate.
    _venta(conn, "V-GEM-1", "KG-SAME", pack_id="PACK-GEM", fecha="2026-08-01 10:00:00")
    _venta(conn, "V-GEM-2", "KG-SAME", pack_id="PACK-GEM", fecha="2026-08-01 10:00:00")
    _envio(conn, "E-GEM", "V-GEM-1", kg, pack_id="PACK-GEM")
    conn.commit()

elegidas = set()
for _ in range(5):
    with get_db() as conn:
        m = match_conceptos_a_ventas(
            conn, kg, {"codigo": "KG-SAME", "descripcion": "Manguera KG-SAME"},
            fecha_factura="2026-08-05 10:00:00",
        )
    elegidas.add(m["num_venta"] if m else None)
ok(len(elegidas) == 1,
   f"5 corridas eligen SIEMPRE la misma hermana (got {elegidas}) — sin el desempate por "
   f"num_venta el resultado variaría en silencio")

# ------------------------------------------------------- 8. migración idempotente
print("\n[8] La marca de carrito distingue las filas del mismo paquete")

f_con = _fila_venta(V_CON_ENVIO)
ok(f_hermana and f_hermana["pack_ventas"] == 2,
   f"la hermana informa que el paquete trae 2 productos (got {f_hermana and f_hermana['pack_ventas']})")
ok(f_con and f_con["pack_ventas"] == 2, "y la otra venta del carrito también")
# 🔴 Una venta normal debe decir 1, NO 0: el subquery no cuenta filas cuando pack_id es
# NULL, y un "0 productos" en el CSV se leería como un dato roto.
f_sola = _fila_venta("V-SIN-PACK")
ok(f_sola and f_sola["pack_ventas"] == 1,
   f"una venta sin carrito informa 1 producto, nunca 0 (got {f_sola and f_sola['pack_ventas']})")


print("\n[9] La migración puebla pack_id y es idempotente")

with get_db() as conn:
    antes = conn.execute(
        "SELECT pack_id FROM envios_colecta WHERE num_envio = '47748773289'"
    ).fetchone()["pack_id"]
ok(antes == PACK, f"el envío tiene el pack de su venta (got {antes})")

# Correr init_database() otra vez no debe cambiar nada ni reventar.
init_database()
init_database()
with get_db() as conn:
    despues = conn.execute(
        "SELECT pack_id FROM envios_colecta WHERE num_envio = '47748773289'"
    ).fetchone()["pack_id"]
    filas = conn.execute("SELECT COUNT(*) c FROM envios_colecta").fetchone()["c"]
ok(despues == PACK, "tras 2 arranques más el pack_id sigue igual (idempotente)")
ok(filas == 6, f"y no se creó ni se borró ninguna fila de envío (got {filas})")

# El caso que la migración tiene que resolver hacia atrás: un envío al que le falta el
# pack_id (como los ~58k de prod antes del deploy) lo recupera de su venta cruzada.
with get_db() as conn:
    conn.execute("UPDATE envios_colecta SET pack_id = NULL WHERE num_envio = '47748773289'")
    conn.commit()
init_database()
with get_db() as conn:
    recuperado = conn.execute(
        "SELECT pack_id FROM envios_colecta WHERE num_envio = '47748773289'"
    ).fetchone()["pack_id"]
ok(recuperado == PACK,
   f"un envío sin pack_id lo recupera de su venta al arrancar (got {recuperado})")

print("\n" + "=" * 62)
if FALLOS:
    print(f"❌ {len(FALLOS)} FALLA(S):")
    for f in FALLOS:
        print(f"   - {f}")
    sys.exit(1)
print("✅ TODO OK — el carrito comparte su envío sin inflar el SLA")
