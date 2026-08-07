"""Fija las garantías del filtro por fecha de factura (hallazgo 2026-08-07).

El bug: los 4 pasos del matcher elegían candidata sólo con `ORDER BY v.fecha_venta DESC
LIMIT 1`, sin mirar nunca la fecha de la FACTURA. Cuando un SKU se vendía varias veces la
factura se iba a una venta arbitraria. Medido contra el cruce manual de Gaby: KIM acertaba
el 27% y en 17 casos el portal marcaba "✓ Facturado" sobre mercancía sin salir.

Correr:  ./.venv/bin/python scripts/test_fecha_factura_venta.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.matcher import (  # noqa: E402
    VENTANA_FACTURACION_DIAS,
    _filtro_fecha,
    match_conceptos_a_ventas,
    recruzar_conceptos_sin_match,
)

fallos = []


def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        fallos.append(msg)


def _bd():
    """BD desechable con el esquema mínimo que toca el matcher."""
    ruta = os.path.join(tempfile.mkdtemp(), "t.db")
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE proveedores (id INTEGER PRIMARY KEY, nombre TEXT, codigo_bodega TEXT);
        CREATE TABLE ventas_ml (num_venta TEXT PRIMARY KEY, sku TEXT, fecha_venta TEXT,
                                titulo TEXT, pack_id TEXT);
        CREATE TABLE envios_colecta (num_envio TEXT PRIMARY KEY, num_venta TEXT,
                                     num_venta_ml TEXT, proveedor_id INTEGER,
                                     fecha_venta TEXT);
        CREATE TABLE facturas (id INTEGER PRIMARY KEY, proveedor_id INTEGER,
                               fecha_factura TEXT);
        CREATE TABLE factura_conceptos (id INTEGER PRIMARY KEY, factura_id INTEGER,
                                        codigo_prov TEXT, descripcion TEXT,
                                        num_venta_match TEXT, match_method TEXT,
                                        match_confidence REAL);
        CREATE TABLE kit_componentes (kit_sku TEXT, componente_codigo TEXT,
                                      cantidad REAL, PRIMARY KEY (kit_sku, componente_codigo));
        INSERT INTO proveedores VALUES (1,'KIMS AUTO','KIM');
    """)
    conn.commit()
    return conn


def _venta(conn, num, sku, fecha, titulo="Pieza"):
    conn.execute("INSERT INTO ventas_ml (num_venta,sku,fecha_venta,titulo) VALUES (?,?,?,?)",
                 (num, sku, fecha, titulo))
    conn.execute("INSERT INTO envios_colecta (num_envio,num_venta,num_venta_ml,proveedor_id,"
                 "fecha_venta) VALUES (?,?,?,?,?)", (f"E{num}", num, num, 1, fecha))
    conn.commit()


print("=" * 70)
print("FILTRO POR FECHA DE FACTURA — no facturar lo que aún no se ha vendido")
print("=" * 70)

# ---------------------------------------------------------------- 1
print("\n1) Una venta POSTERIOR a la factura nunca se elige")
conn = _bd()
_venta(conn, "V_VIEJA", "ABC-Z", "2026-06-01 10:00")
_venta(conn, "V_FUTURA", "ABC-Z", "2026-07-20 10:00")   # después de la factura
m = match_conceptos_a_ventas(conn, 1, {"codigo": "ABC-Z", "descripcion": ""},
                             fecha_factura="2026-06-03 12:00")
check(m is not None and m["num_venta"] == "V_VIEJA",
      f"elige la venta anterior a la factura, no la posterior (eligió {m and m['num_venta']})")

# Sin el filtro, el comportamiento viejo elegía la más reciente (la futura) — es el bug.
m_old = match_conceptos_a_ventas(conn, 1, {"codigo": "ABC-Z", "descripcion": ""})
check(m_old is not None and m_old["num_venta"] == "V_FUTURA",
      "sin fecha se reproduce el comportamiento anterior (compatibilidad hacia atrás)")

# ---------------------------------------------------------------- 2
print("\n2) Entre varias ventas válidas gana la más reciente ANTERIOR a la factura")
conn = _bd()
_venta(conn, "V_ENE", "ABC-Z", "2026-06-01 10:00")
_venta(conn, "V_FEB", "ABC-Z", "2026-06-10 10:00")
_venta(conn, "V_MAR", "ABC-Z", "2026-06-20 10:00")
m = match_conceptos_a_ventas(conn, 1, {"codigo": "ABC-Z", "descripcion": ""},
                             fecha_factura="2026-06-12 09:00")
check(m is not None and m["num_venta"] == "V_FEB",
      f"elige la más cercana por debajo de la factura (eligió {m and m['num_venta']})")

# ---------------------------------------------------------------- 3
print("\n3) Una venta más vieja que la ventana no se rescata")
conn = _bd()
_venta(conn, "V_ANTIGUA", "ABC-Z", "2025-01-01 10:00")
m = match_conceptos_a_ventas(conn, 1, {"codigo": "ABC-Z", "descripcion": ""},
                             fecha_factura="2026-06-01 10:00")
check(m is None,
      f"venta de hace >{VENTANA_FACTURACION_DIAS} días queda fuera (dio {m and m['num_venta']})")

# ---------------------------------------------------------------- 4
print("\n4) Mismo día: la factura se emite horas ANTES que la venta y sigue valiendo")
# ⚠️ Se compara con date(), no con el timestamp. Una factura de las 10:00 para una venta
# de las 14:00 del MISMO día es legítima; comparar timestamps la descartaría.
conn = _bd()
_venta(conn, "V_HOY", "ABC-Z", "2026-06-10 14:00")
m = match_conceptos_a_ventas(conn, 1, {"codigo": "ABC-Z", "descripcion": ""},
                             fecha_factura="2026-06-10 10:00")
check(m is not None and m["num_venta"] == "V_HOY",
      "la comparación es por DÍA, no por hora")

# ---------------------------------------------------------------- 5
print("\n5) El filtro aplica a los 4 pasos, no sólo al exacto")
# paso 2 (ID interno): la factura dice '2692  M2626339', la venta 'CAU2692'
conn = _bd()
_venta(conn, "V_POST", "CAU2692", "2026-07-20 10:00")
m = match_conceptos_a_ventas(conn, 1, {"codigo": "2692  M2626339", "descripcion": ""},
                             fecha_factura="2026-06-03 12:00")
check(m is None, "paso 2 (id interno) respeta el filtro de fecha")

# paso 3 (kit): el componente pertenece a un kit vendido DESPUÉS de la factura
conn = _bd()
_venta(conn, "V_KIT", "KIT0337", "2026-07-20 10:00")
conn.execute("INSERT INTO kit_componentes VALUES ('KIT0337','KDTL-057',1)")
conn.commit()
m = match_conceptos_a_ventas(conn, 1, {"codigo": "KDTL-057", "descripcion": "Manguera"},
                             fecha_factura="2026-06-03 12:00")
check(m is None, "paso 3 (kit) respeta el filtro de fecha")

# paso 4 (fuzzy)
conn = _bd()
_venta(conn, "V_FUZZ", "OTRO", "2026-07-20 10:00", titulo="Manguera radiador superior Aveo")
m = match_conceptos_a_ventas(conn, 1,
                             {"codigo": "", "descripcion": "Manguera radiador superior Aveo"},
                             fecha_factura="2026-06-03 12:00")
check(m is None, "paso 4 (fuzzy) respeta el filtro de fecha")

# ---------------------------------------------------------------- 6
print("\n6) El RECRUCE también filtra por fecha")
# Es el punto más fácil de olvidar: corre tras cada carga de ventas/colecta, justo cuando
# entran ventas NUEVAS (posteriores a facturas viejas). Sin el filtro reintroduciría por la
# puerta de atrás exactamente los cruces que el fix evita.
conn = _bd()
conn.execute("INSERT INTO facturas (id,proveedor_id,fecha_factura) VALUES (1,1,'2026-06-03 12:00')")
conn.execute("INSERT INTO factura_conceptos (id,factura_id,codigo_prov,descripcion) "
             "VALUES (1,1,'ABC-Z','Pieza')")
conn.commit()
_venta(conn, "V_NUEVA", "ABC-Z", "2026-07-20 10:00")   # venta posterior a la factura
res = recruzar_conceptos_sin_match(conn)
fila = conn.execute("SELECT num_venta_match FROM factura_conceptos WHERE id=1").fetchone()
check(fila["num_venta_match"] is None,
      f"el recruce NO cuelga una venta posterior a la factura (quedó {fila['num_venta_match']})")

# ...y sí cruza cuando la venta es anterior
_venta(conn, "V_OK", "ABC-Z", "2026-06-01 10:00")
recruzar_conceptos_sin_match(conn)
fila = conn.execute("SELECT num_venta_match FROM factura_conceptos WHERE id=1").fetchone()
check(fila["num_venta_match"] == "V_OK",
      "el recruce sí cruza cuando aparece una venta anterior válida")

# ---------------------------------------------------------------- 7
print("\n7) Sin fecha de factura el filtro se desactiva por completo")
sql, par = _filtro_fecha(None)
check(sql == "" and par == [], "fecha_factura=None no agrega SQL ni parámetros")
sql, par = _filtro_fecha("2026-06-01")
check("date(v.fecha_venta) <= date(?)" in sql and len(par) == 3,
      "con fecha, el SQL usa parámetros vinculados (sin interpolar valores)")

# ---------------------------------------------------------------- 8
print("\n8) Una venta ya facturada sigue sin reasignarse (garantía preexistente)")
conn = _bd()
_venta(conn, "V1", "ABC-Z", "2026-06-01 10:00")
conn.execute("INSERT INTO facturas (id,proveedor_id,fecha_factura) VALUES (1,1,'2026-06-02')")
conn.execute("INSERT INTO factura_conceptos (id,factura_id,codigo_prov,descripcion,"
             "num_venta_match) VALUES (1,1,'ABC-Z','x','V1')")
conn.commit()
m = match_conceptos_a_ventas(conn, 1, {"codigo": "ABC-Z", "descripcion": ""},
                             fecha_factura="2026-06-02 10:00")
check(m is None, "no se cuelga una segunda factura a una venta ya cruzada")

print("\n" + "=" * 70)
if fallos:
    print(f"❌ {len(fallos)} FALLO(S)")
    for f in fallos:
        print("   -", f)
    sys.exit(1)
print("✅ TODO OK — el filtro por fecha de factura está fijado en los 4 pasos y el recruce")
