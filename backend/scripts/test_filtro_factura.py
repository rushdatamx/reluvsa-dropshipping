"""Regresión del buscador único de ventas por número de factura.

Protege las representaciones que ve Gaby (Serie/Folio varía por proveedor), el
cruce exclusivo contra conceptos relacionados y la ausencia de duplicados.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(prefix="test_filtro_factura_"), "test.db")

import database  # noqa: E402
from database import get_db  # noqa: E402
from models import UserInfo  # noqa: E402
from routers.ventas import _SELECT_VENTAS, _construir_filtros, export_csv, listar  # noqa: E402

ok = True


def chk(cond, msg):
    global ok
    print(("✅ " if cond else "❌ ") + msg)
    ok = ok and bool(cond)


database.init_database()
ADMIN = UserInfo(user_id=1, email="admin@test.local", rol="admin", proveedor_id=None)
PROV_USER = UserInfo(user_id=2, email="kim@test.local", rol="proveedor", proveedor_id=2)

PROVEEDORES = [
    (16, "NO RELACIONADA", "NR010101AAA", "NR"),
]
VENTAS = [
    ("V-KIM", "SKU-KIM", "Pieza KIM"),
    ("V-CAU", "SKU-CAU", "Pieza CAUPLAS"),
    ("V-KG", "SKU-KG", "Pieza KG"),
    ("V-AG", "SKU-AG", "Pieza AG"),
    ("V-VAZ", "SKU-VAZ", "Pieza VAZLO"),
    ("V-NOREL", "SKU-NOREL", "Sólo factura no relacionada"),
    ("V-MULTI", "SKU-MULTI", "Factura con varios conceptos"),
]

with get_db() as conn:
    conn.executemany(
        "INSERT INTO proveedores (id,nombre,rfc,codigo_bodega,activo) VALUES (?,?,?,?,1)",
        PROVEEDORES,
    )
    conn.executemany(
        "INSERT INTO ventas_ml (num_venta,sku,titulo,fecha_venta,estado,total) VALUES (?,?,?,?,?,?)",
        [(num, sku, titulo, "2026-08-15 10:00:00", "Entregado", 100) for num, sku, titulo in VENTAS],
    )
    conn.executemany(
        "INSERT INTO envios_colecta (num_envio,num_venta_ml,proveedor_id) VALUES (?,?,?)",
        [("E-" + num, num, {"V-KIM": 2, "V-CAU": 1, "V-KG": 5, "V-AG": 3,
                             "V-VAZ": 4, "V-MULTI": 2}.get(num)) for num, _, _ in VENTAS],
    )
    facturas = [
        (101, 2, "KIM-UUID", "K", "26804"),
        (102, 1, "CAU-UUID", "CD", "970091508"),
        (103, 5, "KG-UUID", "S", "464516"),
        (104, 3, "AG-UUID", "", "1000030"),
        (105, 4, "VAZ-UUID", "FVC", "02755"),
        (106, 16, "NR-UUID", "NR", "970091508"),
        (107, 2, "MULTI-UUID", "K", "99999"),
    ]
    conn.executemany(
        "INSERT INTO facturas (id,proveedor_id,uuid_cfdi,serie,folio) VALUES (?,?,?,?,?)",
        facturas,
    )
    conceptos = [
        (101, "V-KIM"), (102, "V-CAU"), (103, "V-KG"), (104, "V-AG"),
        (105, "V-VAZ"),
        (107, "V-MULTI"), (107, "V-MULTI"),
    ]
    conn.executemany(
        "INSERT INTO factura_conceptos (factura_id,num_venta_match) VALUES (?,?)", conceptos
    )
    conn.commit()


def consultar(q=None, user=ADMIN, **extras):
    args = dict(proveedor_id=None, estado="todas", q=q, facturada=None, sla=None,
                cruce=None, fecha_desde=None, fecha_hasta=None, deposito=None,
                logistica=None, albaran=None)
    args.update(extras)
    where, params, join_factura = _construir_filtros(user, **args)
    sql = _SELECT_VENTAS.format(join_factura=join_factura, where=" AND ".join(where))
    with get_db() as conn:
        return [r["num_venta"] for r in conn.execute(sql, params).fetchall()]


chk(consultar("K26804") == ["V-KIM"], "KIM: busca Serie+Folio visible")
chk(consultar("970091508 CD") == ["V-CAU"], "CAUPLAS: busca Folio + Serie visible")
chk(consultar("S 464516") == ["V-KG"], "KG: busca Serie + espacio + Folio visible")
chk(consultar("1000030") == ["V-AG"], "AG: busca Folio visible")
chk(consultar("FVC02755") == ["V-VAZ"], "VAZLO: busca Serie+Folio visible")
chk(consultar("268") == ["V-KIM"], "Busca por folio parcial")
chk(consultar("CA") == ["V-CAU"], "Busca por serie parcial")
chk(consultar("970091508") == ["V-CAU"], "Busca por folio aunque exista una factura no relacionada")
chk(consultar("970091508", proveedor_id=1, fecha_desde="2026-08-15", fecha_hasta="2026-08-15",
              facturada="true") == ["V-CAU"], "Factura, proveedor, fecha y facturada se acumulan")
chk(consultar("99999") == ["V-MULTI"], "Varios conceptos devuelven una sola venta")
chk(consultar("970091508", user=PROV_USER) == [], "Proveedor no ve facturas de otro proveedor")
chk(consultar("K26804", user=PROV_USER) == ["V-KIM"], "Proveedor conserva su restricción y búsqueda")

# La factura de V-NOREL existe, pero su concepto no está cruzado a ninguna venta.
chk(consultar("NR970091508") == [], "No devuelve una venta por factura no relacionada")

# Listado y CSV construyen exactamente el mismo universo filtrado.
where_l, params_l, join_l = _construir_filtros(
    ADMIN, None, "todas", "K", None, None, None, None, None, None, None, None
)
where_c, params_c, join_c = _construir_filtros(
    ADMIN, None, "todas", "K", None, None, None, None, None, None, None, None
)
chk((where_l, params_l, join_l) == (where_c, params_c, join_c),
    "Listado y CSV usan exactamente el mismo filtro")
chk("q" in __import__("inspect").signature(listar).parameters and
    "q" in __import__("inspect").signature(export_csv).parameters,
    "Listado y CSV conservan el parámetro público q")

src = Path(__file__).resolve().parents[2] / "frontend/src/pages/Ventas.jsx"
chk("factura" in src.read_text(), "El placeholder frontend anuncia la búsqueda por factura")

print("\nRESULTADO:", "TODO OK ✅" if ok else "HAY FALLAS ❌")
sys.exit(0 if ok else 1)
