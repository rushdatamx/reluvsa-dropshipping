"""
Regresión del filtro de ventas CANCELADAS en la pestaña Ventas.

Contexto (WhatsApp de Gaby, 2026-08-11): *"me di cuenta que también me arroja las ventas
canceladas, hay forma de quitarlas?? pero sólo las canceladas, no las de reclamos o
devoluciones, o me recomienda no quitarlas??, quisiera identificarlas más rápido"*.

Decisión (Mario): NO se borran del portal — dejan de venir POR DEFAULT y un selector las
recupera. Borrarlas habría destruido señal: en prod hay 4 ventas canceladas CON factura
cruzada, que es justo la incidencia que el portal existe para detectar (el proveedor
facturó algo que se canceló).

Medición contra prod al implementar (58,678 ventas):
    Entregado  57,011  |  Pagado  779  |  Cancelada  609  |  NULL  279

⚠️ Lo que este test protege (y que un refactor puede romper EN SILENCIO):

1. 🔴 LAS VENTAS CON `estado` NULL SIGUEN VISIBLES EN EL DEFAULT. En SQL,
   `NULL != 'Cancelada'` evalúa a NULL — NO a true — así que un filtro escrito como
   `v.estado != 'Cancelada'` a secas haría DESAPARECER en silencio las 279 ventas con
   estado NULL (todas de jun-2026, entradas por el Excel legacy que el sync por API nunca
   volvió a tocar). Por eso la condición es `(v.estado IS NULL OR v.estado != 'Cancelada')`.
   Mismo criterio que `NO_ES_FULL` en metricas.py con `logistic_type`.
2. El DEFAULT es "sin canceladas" en el BACKEND, no sólo en el React. Si el default viviera
   sólo en el frontend, una llamada directa a la API y el CSV traerían canceladas y se
   desincronizarían de lo que Gaby ve en la tabla.
3. El CSV respeta el filtro (comparte `_construir_filtros` con el listado): Gaby baja
   exactamente lo que ve.
4. Compatibilidad: `?estado=Entregado` sigue filtrando por igualdad exacta. Si alguien
   convierte el parámetro en un enum cerrado, rompe a cualquier consumidor que lo use así.
5. "solo_canceladas" existe porque Gaby pidió *"identificarlas más rápido"* — no basta con
   esconderlas; tiene que poder verlas juntas.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test_canceladas.db")

import database  # noqa: E402
from database import get_db  # noqa: E402
from models import UserInfo  # noqa: E402
from routers.ventas import _construir_filtros, _SELECT_VENTAS  # noqa: E402

ok = True


def chk(cond: bool, msg: str) -> None:
    global ok
    print(("✅ " if cond else "❌ ") + msg)
    ok = ok and bool(cond)


database.init_database()

ADMIN = UserInfo(user_id=1, email="gaby@reluvsa.com", rol="admin", proveedor_id=None)

# Universo de prueba: refleja la composición real de prod, incluido el caso NULL.
VENTAS = [
    ("V-ENT-1", "Entregado"),
    ("V-ENT-2", "Entregado"),
    ("V-PAG-1", "Pagado"),
    ("V-CAN-1", "Cancelada"),
    ("V-CAN-2", "Cancelada"),
    ("V-NULL-1", None),        # legacy del Excel: el parser nunca pobló `estado`
    ("V-NULL-2", None),
]

with get_db() as conn:
    for num, estado in VENTAS:
        conn.execute(
            "INSERT INTO ventas_ml (num_venta, sku, fecha_venta, estado, titulo, total) "
            "VALUES (?,?,?,?,?,?)",
            (num, "SKU-" + num, "2026-06-15 10:00:00", estado, "Producto " + num, 100.0),
        )
    conn.commit()


def consultar(estado):
    """Corre el SELECT real del listado con el filtro dado y devuelve los num_venta."""
    where, params, join_factura = _construir_filtros(
        ADMIN, None, estado, None, None, None, None, None, None, None, None
    )
    sql = _SELECT_VENTAS.format(join_factura=join_factura, where=" AND ".join(where))
    with get_db() as conn:
        return {r["num_venta"] for r in conn.execute(sql, params).fetchall()}


# --- 1. Default (sin parámetro): oculta canceladas, CONSERVA los NULL ------------------
default = consultar(None)
chk("V-CAN-1" not in default and "V-CAN-2" not in default,
    "Default: las ventas Cancelada NO aparecen")
chk("V-NULL-1" in default and "V-NULL-2" in default,
    "🔴 Default: las ventas con estado NULL SÍ aparecen (trampa del `!=` contra NULL)")
chk("V-ENT-1" in default and "V-ENT-2" in default and "V-PAG-1" in default,
    "Default: Entregado y Pagado siguen visibles")
chk(len(default) == 5, f"Default devuelve 5 de 7 ventas (dio {len(default)})")

# --- 2. El default vive en el BACKEND, no sólo en el frontend --------------------------
chk(consultar(None) == consultar("sin_canceladas"),
    "Sin parámetro == 'sin_canceladas' (el default no depende del React)")

# --- 3. "solo_canceladas": Gaby las revisa juntas --------------------------------------
solo = consultar("solo_canceladas")
chk(solo == {"V-CAN-1", "V-CAN-2"},
    "'solo_canceladas' devuelve únicamente las canceladas")

# --- 4. "todas": nada se perdió del portal --------------------------------------------
todas = consultar("todas")
chk(len(todas) == 7, f"'todas' devuelve las 7 ventas (dio {len(todas)})")
chk("V-CAN-1" in todas and "V-NULL-1" in todas,
    "'todas' incluye canceladas y NULL: nada se borró del portal")

# --- 4.bis. Los modos toleran mayúsculas/espacios --------------------------------------
# Detectado por el api-guardian: un "Todas" con mayúscula caería a la rama de compatibilidad
# y filtraría por un estado inexistente -> tabla VACÍA en vez de "todas". Es un fallo
# SILENCIOSO (parece que no hay ventas, no que el filtro se equivocó).
chk(consultar("Todas") == todas, "'Todas' (mayúscula) NO deja la tabla vacía")
chk(consultar(" solo_canceladas ") == solo, "Espacios alrededor del modo no lo rompen")
chk(consultar("SIN_CANCELADAS") == default, "'SIN_CANCELADAS' se comporta como el default")

# --- 5. Compatibilidad: igualdad exacta sigue funcionando ------------------------------
chk(consultar("Entregado") == {"V-ENT-1", "V-ENT-2"},
    "Compatibilidad: ?estado=Entregado filtra por igualdad exacta")
chk(consultar("Cancelada") == {"V-CAN-1", "V-CAN-2"},
    "Compatibilidad: ?estado=Cancelada filtra por igualdad exacta")

# --- 6. Una cancelada CON factura sigue siendo recuperable ----------------------------
# Es el caso que justifica NO borrarlas: el proveedor facturó algo que se canceló.
with get_db() as conn:
    conn.execute(
        "INSERT INTO proveedores (id, nombre, rfc, codigo_bodega, activo) VALUES (?,?,?,?,1)",
        (99, "PROV TEST", "TST010101AAA", "TST"),
    )
    conn.execute(
        "INSERT INTO facturas (id, proveedor_id, uuid_cfdi, serie, folio, rfc_emisor, "
        "rfc_receptor, total) VALUES (?,?,?,?,?,?,?,?)",
        (500, 99, "UUID-TEST-CANCELADA", "A", "1", "TST010101AAA", "GPE230915JWA", 100.0),
    )
    conn.execute(
        "INSERT INTO factura_conceptos (factura_id, codigo_prov, descripcion, cantidad, "
        "importe, num_venta_match, match_method, match_confidence) VALUES (?,?,?,?,?,?,?,?)",
        (500, "SKU-V-CAN-1", "Pieza", 1, 100.0, "V-CAN-1", "codigo_exact", 1.0),
    )
    conn.commit()

chk("V-CAN-1" in consultar("solo_canceladas"),
    "La cancelada CON factura se recupera con 'solo_canceladas' (la incidencia sigue visible)")
chk("V-CAN-1" not in consultar(None),
    "…y sigue fuera de la vista diaria por default")

# --- 7. El CSV comparte el mismo filtro ------------------------------------------------
# _construir_filtros es la única fuente del WHERE para listado y export: si el listado
# oculta canceladas, el CSV también. Se verifica que la firma sea la misma llamada.
where_csv, params_csv, _ = _construir_filtros(
    ADMIN, None, None, None, None, None, None, None, None, None, None
)
chk(any("Cancelada" in w for w in where_csv),
    "El CSV (mismo _construir_filtros) aplica el filtro de canceladas por default")

print()
print("RESULTADO:", "TODO OK ✅" if ok else "HAY FALLAS ❌")
sys.exit(0 if ok else 1)
