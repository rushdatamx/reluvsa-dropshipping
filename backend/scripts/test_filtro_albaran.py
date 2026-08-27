"""Regresión del filtro opcional de albarán en Ventas y cruces."""
import inspect
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(prefix="test_filtro_albaran_"), "test.db")

import database  # noqa: E402
from database import get_db  # noqa: E402
from models import UserInfo  # noqa: E402
from routers.ventas import _construir_filtros, _SELECT_VENTAS, export_csv, listar  # noqa: E402

ok = True


def chk(cond, msg):
    global ok
    print(("✅ " if cond else "❌ ") + msg)
    ok = ok and bool(cond)


database.init_database()
ADMIN = UserInfo(user_id=1, email="admin@test.local", rol="admin", proveedor_id=None)

with get_db() as conn:
    conn.execute(
        "INSERT INTO proveedores (id,nombre,rfc,codigo_bodega,activo) VALUES (91,'P1','AAA010101AAA','P1',1)"
    )
    conn.execute(
        "INSERT INTO proveedores (id,nombre,rfc,codigo_bodega,activo) VALUES (92,'P2','BBB010101BBB','P2',1)"
    )
    ventas = [
        ("NULL", None, "2026-08-10 10:00:00", "Entregado"),
        ("VACIA", "", "2026-08-11 10:00:00", "Entregado"),
        ("ESPACIOS", "   ", "2026-08-12 10:00:00", "Entregado"),
        ("CON-1", "A-100", "2026-08-13 10:00:00", "Entregado"),
        ("CON-2", " A-200 ", "2026-08-14 10:00:00", "Pagado"),
        ("CON-CANCELADA", "A-300", "2026-08-15 10:00:00", "Cancelada"),
    ]
    for num, albaran, fecha, estado in ventas:
        conn.execute(
            "INSERT INTO ventas_ml (num_venta,sku,fecha_venta,estado,titulo,total,albaran) VALUES (?,?,?,?,?,?,?)",
            (num, "SKU-" + num, fecha, estado, "Producto " + num, 100, albaran),
        )
    # Cuatro ventas pertenecen a P1; las otras dos a P2. Esto permite probar AND.
    for i, (num, proveedor) in enumerate([
        ("NULL", 91), ("VACIA", 91), ("CON-1", 91), ("CON-CANCELADA", 91),
        ("ESPACIOS", 92), ("CON-2", 92),
    ], 1):
        conn.execute(
            "INSERT INTO envios_colecta (num_envio,num_venta_ml,proveedor_id) VALUES (?,?,?)",
            (f"E{i}", num, proveedor),
        )
    conn.commit()


def consultar(albaran=None, **extras):
    args = dict(proveedor_id=None, estado="todas", q=None, facturada=None, sla=None,
                cruce=None, fecha_desde=None, fecha_hasta=None, deposito=None,
                logistica=None, albaran=albaran)
    args.update(extras)
    where, params, join_factura = _construir_filtros(ADMIN, **args)
    sql = _SELECT_VENTAS.format(join_factura=join_factura, where=" AND ".join(where))
    with get_db() as conn:
        return [r["num_venta"] for r in conn.execute(sql, params).fetchall()]


todas = set(consultar())
sin = set(consultar("sin_albaran"))
con = set(consultar("con_albaran"))
chk(todas == {"NULL", "VACIA", "ESPACIOS", "CON-1", "CON-2", "CON-CANCELADA"},
    "Sin parámetro aparecen todas")
chk(sin == {"NULL", "VACIA", "ESPACIOS"}, "NULL, vacío y espacios aparecen en Sin albarán")
chk(con == {"CON-1", "CON-2", "CON-CANCELADA"}, "Sólo valores reales aparecen en Con albarán")
chk(set(consultar(" CON_ALBARAN ")) == con, "El modo tolera espacios y mayúsculas")
chk(set(consultar("modo_futuro")) == todas, "Un modo desconocido no aplica filtro")

combinado = set(consultar(
    "sin_albaran", proveedor_id=91, fecha_desde="2026-08-11", fecha_hasta="2026-08-13",
    estado="Entregado",
))
chk(combinado == {"VACIA"}, "Proveedor, fechas, estado y albarán se acumulan mediante AND")

# Los dos endpoints exponen el parámetro y ambos delegan en la fuente compartida.
chk("albaran" in inspect.signature(listar).parameters, "GET /api/ventas acepta albaran")
chk("albaran" in inspect.signature(export_csv).parameters, "GET /api/ventas/export.csv acepta albaran")
where_lista, params_lista, join_lista = _construir_filtros(
    ADMIN, None, "todas", None, None, None, None, None, None, None, None, "sin_albaran"
)
where_csv, params_csv, join_csv = _construir_filtros(
    ADMIN, None, "todas", None, None, None, None, None, None, None, None, "sin_albaran"
)
chk((where_lista, params_lista, join_lista) == (where_csv, params_csv, join_csv),
    "Listado y CSV construyen exactamente el mismo filtro")

# Contrato del frontend: el mismo estado alimenta aplicar, paginación y exportación.
src = (BASE.parent / "frontend" / "src" / "pages" / "Ventas.jsx").read_text()
chk("albaran: ''" in src and "set('albaran'" in src, "El selector inicia y limpia en Todas")
chk("listarVentas({ ...paramsDeFiltros(), page: p" in src, "La paginación conserva el filtro")
chk("exportarVentasCsv(paramsDeFiltros())" in src, "El CSV recibe los filtros aplicados")

print("\nRESULTADO:", "TODO OK ✅" if ok else "HAY FALLAS ❌")
sys.exit(0 if ok else 1)
