"""Fija el arreglo del cruce de CAUPLAS reportado por Gaby el 2026-08-17.

EL BUG
------
CAUPLAS reusa el ID interno de la pieza en TODAS sus facturas y sólo cambia el folio de
pedido que va al lado (`M######`). El matcher comparaba nada más el ID interno y elegía
"la venta más reciente", así que cuando la factura y la venta caían el MISMO día podía
llevarse una venta ocurrida HORAS DESPUÉS de emitirse la factura.

El caso textual de Gaby (venta 2000017906137676, KIT0342):

    factura 970096782 emitida 12-ago 16:17 -> "7694 M2653006"  <- se la llevó
    venta            ocurrida 12-ago 18:10                     <- 2 h DESPUÉS
    factura 970096819 emitida 13-ago 16:19 -> "7694 M2653287"  <- la de verdad

DOS PIEZAS, Y LA DIFERENCIA IMPORTA
-----------------------------------
1. `_orden_candidatas` (motor): PREFIERE las ventas anteriores a la factura, sin
   descartar a las posteriores del mismo día.
2. `corregir_cruces_cauplas_factura_anterior.py`: corrige hacia atrás los cruces ya
   persistidos, y sólo cuando existe un huérfano de la misma pieza que pueda recibirlos.

🔴 LO QUE NO SE PUEDE REDISEÑAR (cada punto tiene su caso abajo):
  1. El filtro de `_filtro_fecha` sigue comparando por `date()`. Cambiarlo a timestamp
     "para arreglar esto" DESCARTARÍA cruces legítimos del mismo día — en prod hay
     facturas buenas emitidas antes de la venta que amparan, y una de ellas
     (2000017927450774) no tiene ninguna otra factura con su pieza: quedaría pendiente
     para siempre.
  2. La preferencia NO es un filtro. Si la única candidata es posterior, se cruza igual.
  3. El script exige un huérfano de la MISMA pieza y POSTERIOR a la venta; sin eso no
     toca nada (no se libera a ciegas).
  4. El concepto que cede la venta queda PENDIENTE, nunca se borra la fila.

Correr:  ./.venv/bin/python scripts/test_cauplas_factura_anterior.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from services.matcher import (  # noqa: E402
    _orden_candidatas,
    match_conceptos_a_ventas,
)
from corregir_cruces_cauplas_factura_anterior import (  # noqa: E402
    analizar,
    aplicar,
    tokens_pieza,
    _misma_pieza,
)

fallos = []


def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        fallos.append(msg)


def _bd():
    ruta = os.path.join(tempfile.mkdtemp(), "t.db")
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE proveedores (id INTEGER PRIMARY KEY, nombre TEXT, codigo_bodega TEXT);
        CREATE TABLE ventas_ml (num_venta TEXT PRIMARY KEY, sku TEXT, fecha_venta TEXT,
                                titulo TEXT, pack_id TEXT);
        CREATE TABLE envios_colecta (num_envio TEXT PRIMARY KEY, num_venta TEXT,
                                     num_venta_ml TEXT, proveedor_id INTEGER,
                                     fecha_venta TEXT, pack_id TEXT);
        CREATE TABLE facturas (id INTEGER PRIMARY KEY, proveedor_id INTEGER,
                               folio TEXT, fecha_factura TEXT);
        CREATE TABLE factura_conceptos (id INTEGER PRIMARY KEY, factura_id INTEGER,
                                        codigo_prov TEXT, descripcion TEXT,
                                        num_venta_match TEXT, match_method TEXT,
                                        match_confidence REAL);
        CREATE TABLE kit_componentes (kit_sku TEXT, componente_codigo TEXT,
                                      cantidad REAL, PRIMARY KEY (kit_sku, componente_codigo));
        INSERT INTO proveedores VALUES (1,'QUALITY HOSES','CAUPLAS');
    """)
    conn.commit()
    return conn


def _venta(conn, num, sku, fecha, titulo="Pieza"):
    conn.execute("INSERT INTO ventas_ml (num_venta,sku,fecha_venta,titulo) VALUES (?,?,?,?)",
                 (num, sku, fecha, titulo))
    conn.execute("INSERT INTO envios_colecta (num_envio,num_venta,num_venta_ml,proveedor_id,"
                 "fecha_venta) VALUES (?,?,?,?,?)", (f"E{num}", num, num, 1, fecha))
    conn.commit()


def _factura(conn, fid, folio, fecha):
    conn.execute("INSERT INTO facturas (id,proveedor_id,folio,fecha_factura) VALUES (?,?,?,?)",
                 (fid, 1, folio, fecha))
    conn.commit()


def _concepto(conn, cid, fid, codigo, match=None, metodo=None, conf=None, desc="Pieza"):
    conn.execute("""INSERT INTO factura_conceptos
                    (id,factura_id,codigo_prov,descripcion,num_venta_match,match_method,
                     match_confidence) VALUES (?,?,?,?,?,?,?)""",
                 (cid, fid, codigo, desc, match, metodo, conf))
    conn.commit()


print("=" * 74)
print("CAUPLAS — la factura no puede ser anterior a la venta que ampara (2026-08-17)")
print("=" * 74)

# ---------------------------------------------------------------- 1
print("\n1) EL CASO DE GABY: entre dos ventas del mismo día gana la anterior a la factura")
conn = _bd()
# La venta vieja ya existía cuando se emitió la factura; la nueva ocurrió después.
_venta(conn, "V_ANTES", "CAU7694", "2026-08-12 09:00")
_venta(conn, "V_DESPUES", "CAU7694", "2026-08-12 18:10")
m = match_conceptos_a_ventas(conn, 1, {"codigo": "7694 M2653006", "descripcion": "DODGE i10"},
                             fecha_factura="2026-08-12 16:17")
check(m is not None and m["num_venta"] == "V_ANTES",
      f"elige la venta que ya existía, no la de 2 h después (eligió {m and m['num_venta']})")

# ---------------------------------------------------------------- 2
print("\n2) 🔴 La preferencia NO es un filtro: si la única candidata es posterior, cruza")
# Es la garantía que impide dejar ventas pendientes para siempre. En prod,
# 2000017927450774 es exactamente este caso: no hay otra factura con su pieza.
conn = _bd()
_venta(conn, "V_UNICA", "CAU7461", "2026-08-13 21:08")
m = match_conceptos_a_ventas(conn, 1, {"codigo": "7461 M265420", "descripcion": "Pieza"},
                             fecha_factura="2026-08-13 16:19")
check(m is not None and m["num_venta"] == "V_UNICA",
      "con una sola candidata posterior (mismo día) se cruza igual, no se pierde")

# ---------------------------------------------------------------- 3
print("\n3) La preferencia aplica también al paso 3 (componente de kit)")
conn = _bd()
conn.execute("INSERT INTO kit_componentes VALUES ('KIT0342','CAU7694',1)")
conn.commit()
_venta(conn, "K_ANTES", "KIT0342", "2026-08-12 09:00")
_venta(conn, "K_DESPUES", "KIT0342", "2026-08-12 18:10")
m = match_conceptos_a_ventas(conn, 1,
                             {"codigo": "7694 M2653006", "descripcion": "DODGE i10 CALEF"},
                             fecha_factura="2026-08-12 16:17")
check(m is not None and m["num_venta"] == "K_ANTES",
      f"el paso de kits también prefiere la anterior (eligió {m and m['num_venta']})")

# ---------------------------------------------------------------- 4
print("\n3.bis) 🔴 La preferencia aplica en LOS 4 PASOS (orden de parámetros correcto)")
# El ORDER BY nuevo agrega un parámetro al final en 5 consultas distintas. Un desfase
# entre el orden de los `?` y el de los parámetros NO daría error de SQL: daría
# resultados equivocados en silencio. Este caso ejercita los 4 pasos a la vez.
conn = _bd()
conn.execute("INSERT INTO kit_componentes VALUES ('KIT0342','CAU7694',1)")
conn.commit()
for paso, sku, titulo in (
    ("P1", "CAU9001", "Manguera"),
    ("P2", "CAU8002", "Manguera"),
    ("P3", "KIT0342", "Manguera"),
    ("P4", "ZZZ", "Bomba de agua especial Tsuru"),
):
    _venta(conn, f"{paso}_ANTES", sku, "2026-08-12 09:00", titulo)
    _venta(conn, f"{paso}_DESPUES", sku, "2026-08-12 18:10", titulo)

for paso, codigo, desc in (
    ("P1", "CAU9001", ""),                       # paso 1: código exacto
    ("P2", "8002 M2653006", ""),                 # paso 2: ID interno
    ("P3", "7694 M2653006", "DODGE i10 CALEF"),  # paso 3: componente de kit
    ("P4", "", "Bomba de agua especial Tsuru"),  # paso 4: fuzzy
):
    m = match_conceptos_a_ventas(conn, 1, {"codigo": codigo, "descripcion": desc},
                                 fecha_factura="2026-08-12 16:17")
    check(m is not None and m["num_venta"] == f"{paso}_ANTES",
          f"{paso}: prefiere la venta anterior (eligió {m and m['num_venta']})")

print("\n4) Sin fecha de factura, el orden es el de siempre (compatibilidad)")
sql, par = _orden_candidatas(None)
check("v.fecha_venta DESC" in sql and par == [],
      "sin fecha no se agrega parámetro ni criterio nuevo")
sql2, par2 = _orden_candidatas("2026-08-12 16:17")
check(len(par2) == 1 and "?" in sql2,
      "con fecha el criterio usa parámetro vinculado (sin interpolar valores)")

# ---------------------------------------------------------------- 5
print("\n5) El script corrige el cruce imposible y lo manda a la factura correcta")
conn = _bd()
_venta(conn, "V_GABY", "KIT0342", "2026-08-12 18:10")
_factura(conn, 10, "970096782", "2026-08-12 16:17")   # ANTES de la venta
_factura(conn, 11, "970096819", "2026-08-13 16:19")   # la correcta
_concepto(conn, 100, 10, "7694 M2653006", match="V_GABY",
          metodo="kit_componente", conf=0.95)
_concepto(conn, 101, 11, "7694 M2653287")             # huérfano, misma pieza
corr = analizar(conn)
check(len(corr) == 1 and corr[0]["folio_despues"] == "970096819",
      f"detecta 1 corrección hacia el folio 970096819 (dio {[c['folio_despues'] for c in corr]})")
aplicar(conn, corr)
conn.commit()
fila_nueva = conn.execute("SELECT num_venta_match FROM factura_conceptos WHERE id=101").fetchone()
fila_vieja = conn.execute("SELECT num_venta_match FROM factura_conceptos WHERE id=100").fetchone()
check(fila_nueva is not None and fila_nueva[0] == "V_GABY",
      "la venta queda en el concepto de la factura correcta")
# 🔴 Se comprueba que la FILA siga existiendo, no sólo que su match sea NULL. Si el
# script borrara el concepto, un `fetchone()[0]` reventaría con TypeError en vez de
# reportar el fallo — verificado por mutación (DELETE en vez de UPDATE).
check(fila_vieja is not None, "🔴 la fila del concepto equivocado NO se borra")
check(fila_vieja is not None and fila_vieja[0] is None,
      "🔴 el concepto equivocado queda PENDIENTE (match en NULL)")
check(conn.execute("SELECT COUNT(*) FROM factura_conceptos").fetchone()[0] == 2,
      "ninguna fila se borra")

# ---------------------------------------------------------------- 6
print("\n6) 🔴 Sin huérfano compatible posterior NO se toca nada")
# Liberar aquí dejaría la venta pendiente para siempre: perder información sin poner
# nada mejor. Es el caso de 2000017927450774 en prod.
conn = _bd()
_venta(conn, "V_SOLA", "CAU7461", "2026-08-13 21:08")
_factura(conn, 20, "970096818", "2026-08-13 16:19")
_concepto(conn, 200, 20, "7461 M265420", match="V_SOLA",
          metodo="kit_componente", conf=0.95)
check(analizar(conn) == [], "sin alternativa el cruce se conserva")

# ---------------------------------------------------------------- 7
print("\n7) 🔴 Un huérfano de OTRA pieza no sirve de destino")
conn = _bd()
_venta(conn, "V_X", "CAU7694", "2026-08-12 18:10")
_factura(conn, 30, "970096782", "2026-08-12 16:17")
_factura(conn, 31, "970096819", "2026-08-13 16:19")
_concepto(conn, 300, 30, "7694 M2653006", match="V_X", metodo="codigo_id_interno", conf=0.9)
_concepto(conn, 301, 31, "9999 M2653287")            # pieza distinta
check(analizar(conn) == [], "no se reasigna a un concepto de otra pieza")

# ---------------------------------------------------------------- 8
print("\n8) 🔴 Un huérfano ANTERIOR a la venta tampoco sirve")
# Sería el mismo error que estamos corrigiendo, sólo que en otra factura.
conn = _bd()
_venta(conn, "V_Y", "CAU7694", "2026-08-12 18:10")
_factura(conn, 40, "970096782", "2026-08-12 16:17")
_factura(conn, 41, "970096700", "2026-08-10 10:00")  # anterior a la venta
_concepto(conn, 400, 40, "7694 M2653006", match="V_Y", metodo="codigo_id_interno", conf=0.9)
_concepto(conn, 401, 41, "7694 M2650000")
check(analizar(conn) == [], "no se reasigna a una factura que también precede a la venta")

# ---------------------------------------------------------------- 9
print("\n9) Un cruce normal (factura posterior a la venta) no se toca")
conn = _bd()
_venta(conn, "V_OK", "CAU7694", "2026-08-10 10:00")
_factura(conn, 50, "970096782", "2026-08-12 16:17")
_factura(conn, 51, "970096819", "2026-08-13 16:19")
_concepto(conn, 500, 50, "7694 M2653006", match="V_OK", metodo="codigo_id_interno", conf=0.9)
_concepto(conn, 501, 51, "7694 M2653287")
check(analizar(conn) == [], "el cruce correcto se conserva intacto")

# ---------------------------------------------------------------- 10
print("\n10) Idempotencia: la 2a corrida no encuentra nada")
conn = _bd()
_venta(conn, "V_I", "CAU7694", "2026-08-12 18:10")
_factura(conn, 60, "970096782", "2026-08-12 16:17")
_factura(conn, 61, "970096819", "2026-08-13 16:19")
_concepto(conn, 600, 60, "7694 M2653006", match="V_I", metodo="codigo_id_interno", conf=0.9)
_concepto(conn, 601, 61, "7694 M2653287")
c1 = analizar(conn)
aplicar(conn, c1)
conn.commit()
check(len(c1) == 1 and analizar(conn) == [], "la 2a corrida no corrige nada")

# ---------------------------------------------------------------- 11
print("\n11) 🔴 EXCLUSIVO DE CAUPLAS: otro proveedor no se toca")
# ⚠️ El código de la pieza tiene que ser uno que SÍ pasaría todas las demás guardas
# (token de >= 4 caracteres compartido), o este caso pasaría por la razón equivocada:
# quedaría bloqueado por la guarda de token corto y no probaría la exclusividad.
# Verificado por mutación: al quitar el filtro por proveedor, este check se pone rojo.
conn = _bd()
conn.execute("INSERT INTO proveedores VALUES (2,'KIMS AUTO','KIM')")
conn.execute("INSERT INTO ventas_ml (num_venta,sku,fecha_venta,titulo) "
             "VALUES ('V_KIM','KIM7694','2026-08-12 18:10','x')")
conn.execute("INSERT INTO envios_colecta (num_envio,num_venta,num_venta_ml,proveedor_id,"
             "fecha_venta) VALUES ('EK','V_KIM','V_KIM',2,'2026-08-12 18:10')")
conn.execute("INSERT INTO facturas (id,proveedor_id,folio,fecha_factura) "
             "VALUES (70,2,'K111','2026-08-12 16:17')")
conn.execute("INSERT INTO facturas (id,proveedor_id,folio,fecha_factura) "
             "VALUES (71,2,'K222','2026-08-13 16:19')")
_concepto(conn, 700, 70, "7694 M2653006", match="V_KIM", metodo="codigo_id_interno", conf=0.9)
_concepto(conn, 701, 71, "7694 M2653287")
check(analizar(conn) == [], "un cruce de KIM en la misma situación NO se corrige")

# ---------------------------------------------------------------- 12
print("\n12) La guarda de token corto (>=4) evita robar conceptos ajenos")
check(_misma_pieza(tokens_pieza("CAU7694"), tokens_pieza("7694 M2653006")),
      "'CAU7694' cruza contra '7694 M2653006'")
check(not _misma_pieza(tokens_pieza("CAU409"), tokens_pieza("409 M2653006")),
      "🔴 un token de 3 dígitos ('409') NO basta: vive en 21 kits distintos")

print()
print("=" * 74)
if fallos:
    print(f"❌ {len(fallos)} FALLO(S)")
    for f in fallos:
        print("   -", f)
    sys.exit(1)
print("✅ TODO OK — el cruce de CAUPLAS por fecha de factura está fijado")
