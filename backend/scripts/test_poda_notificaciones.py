"""
Regresión de la poda de `ml_notificaciones` (services/sync_ml.py::_finalizar).

Fija dos garantías que hoy solo sostiene un comentario y que un refactor podría
romper en silencio:

1. ORDEN: el DELETE va ANTES del `UPDATE ... SET procesada=1 WHERE procesada=0`.
   Invertirlo haría que una notificación pendiente ANTIGUA se marque procesada y
   se borre en la MISMA corrida, sin haberse consumido nunca.
2. ZONA HORARIA: `recibido_en` lo escribe routers/webhooks.py con datetime.now()
   (hora LOCAL MX), no utcnow() como ml_api_log. Usar utcnow() en el corte
   borraría ~6 h de más en cada poda.

No toca la red ni la API de ML: es SQL puro sobre una BD desechable.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test_poda.db")

import database  # noqa: E402
from database import get_db  # noqa: E402
from services.sync_ml import NOTIF_RETENCION_DIAS, _finalizar  # noqa: E402

ok = True


def chk(cond: bool, msg: str) -> None:
    global ok
    print(("✅ " if cond else "❌ ") + msg)
    ok = ok and bool(cond)


def _insertar(conn, topic: str, dias_atras: int, procesada: int) -> None:
    ts = (datetime.now() - timedelta(days=dias_atras)).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO ml_notificaciones (topic, resource, raw_body, recibido_en, procesada)
           VALUES (?, ?, ?, ?, ?)""",
        (topic, "/orders/1", '{"topic":"orders_v2"}', ts, procesada),
    )


database.init_database()

VIEJA = NOTIF_RETENCION_DIAS + 30
BORDE = NOTIF_RETENCION_DIAS - 1

with get_db() as conn:
    _insertar(conn, "vieja_procesada", VIEJA, 1)   # única que debe borrarse
    _insertar(conn, "vieja_pendiente", VIEJA, 0)   # trabajo sin consumir: se conserva
    _insertar(conn, "nueva_procesada", 2, 1)
    _insertar(conn, "nueva_pendiente", 2, 0)
    _insertar(conn, "borde_procesada", BORDE, 1)   # dentro de la retención
    # Testigo de negocio: la poda no debe tocar tablas de datos.
    conn.execute(
        "INSERT INTO ventas_ml (num_venta, sku, titulo) VALUES ('V-TEST','SKU-1','Producto')"
    )

_finalizar(
    run_id=0,
    inicio=datetime.now(),
    stats={
        "ordenes_vistas": 0,
        "ventas_upsert": 0,
        "envios_upsert": 0,
        "errores": 0,
        "detalle_errores": [],
    },
)

with get_db() as conn:
    vivas = {r["topic"] for r in conn.execute("SELECT topic FROM ml_notificaciones")}
    ventas = conn.execute("SELECT COUNT(*) AS c FROM ventas_ml").fetchone()["c"]
    pendientes = conn.execute(
        "SELECT COUNT(*) AS c FROM ml_notificaciones WHERE procesada = 0"
    ).fetchone()["c"]

chk("vieja_procesada" not in vivas, f"procesada y vieja (>{NOTIF_RETENCION_DIAS}d) → borrada")
chk("vieja_pendiente" in vivas, "PENDIENTE y vieja → CONSERVADA (el DELETE va antes del UPDATE)")
chk("nueva_procesada" in vivas, "procesada y reciente → conservada")
chk("nueva_pendiente" in vivas, "pendiente y reciente → conservada")
chk("borde_procesada" in vivas, f"borde {BORDE}d < {NOTIF_RETENCION_DIAS}d → conservada (sin desfase de TZ)")
chk(
    vivas == {"vieja_pendiente", "nueva_procesada", "nueva_pendiente", "borde_procesada"},
    f"sobreviven exactamente las 4 esperadas: {sorted(vivas)}",
)
chk(ventas == 1, "ventas_ml INTACTA — la poda no toca datos de negocio")
chk(pendientes == 0, "las pendientes quedaron reconciliadas por la corrida (procesada=1)")

print("\n" + ("🎉 PODA DE NOTIFICACIONES VERIFICADA" if ok else "💥 REGRESIÓN EN LA PODA"))
sys.exit(0 if ok else 1)
