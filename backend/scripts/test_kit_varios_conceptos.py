"""Fija que una venta-kit reciba TODOS sus componentes (reporte de Gaby, 2026-08-17).

EL BUG
------
Gaby: *"de esta factura se vinculó a 2 ventas con el mismo sku pero sólo debería
vincularse a la venta con terminación 9104"*. Reproducido en prod (factura `970096936`,
pedido `M2653711`, KIT0207):

    "13351 M2653711" (CALEF ENTRADA) -> venta ...786110
    "13353 M2653711" (CALEF SALIDA)  -> venta ...639104   <- OTRA venta del mismo kit

CAUSA: los 5 pasos del matcher excluyen con `fc.id IS NULL` a cualquier venta que YA
tenga un concepto cruzado. Para una venta normal es correcto —una pieza, una factura—,
pero **una venta-kit necesita N conceptos, uno por componente**. El primero ocupaba la
venta y los demás se iban a otras ventas del mismo kit.

MAGNITUD MEDIDA EN PROD (antes del arreglo): de 141 ventas-kit facturadas, **130 estaban
incompletas** — CAUPLAS 115 de 115 (había `KIT03561` con 1 de sus 8 piezas) y KIM 15 de
26. Además, 85 de los 95 conceptos huérfanos de CAUPLAS eran componentes de kit que no
encontraban dónde caer: contaban como *error de facturación* del proveedor sin serlo.

RESULTADO DEL ARREGLO (re-cruce completo sobre copia de prod):

    CAUPLAS  kits completos   0 -> 43   incompletos 115 -> 13   huérfanos 95 -> 10
    KIM      kits completos  10 -> 18   incompletos  13 ->  9

🔴 LAS 4 REGLAS QUE NO SE PUEDEN REDISEÑAR (cada una con su caso y su mutación):

  R1. Una venta-kit admite VARIOS conceptos, pero NUNCA dos veces el mismo componente
      (eso serían dos kits vendidos, y el segundo es de otra venta).
  R2. `_tokens_pieza` excluye el folio de pedido. Con `_tokens_codigo` a secas, dos
      componentes DISTINTOS del mismo pedido comparten los dígitos del folio y parecen
      la misma pieza — era el bug que partía el kit de Gaby.
  R3. El desempate por pedido exige coincidencia EXACTA del conjunto de piezas. Un
      pedido suele ser un LOTE MIXTO (en prod, `M2649748` trae piezas de 2 kits más 2
      sueltas); con "subconjunto" calzaría contra cualquiera de ellos.
  R4. Los conceptos de un MISMO pedido van a la MISMA venta. Sin esto resuelven bien el
      kit pero caen en ventas distintas — exactamente lo que reportó Gaby.

Correr:  ./.venv/bin/python scripts/test_kit_varios_conceptos.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.matcher import (  # noqa: E402
    _componente_ya_cubierto,
    _piezas_del_pedido,
    _tokens_pieza,
    _venta_del_pedido,
    match_conceptos_a_ventas,
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


def _venta(conn, num, sku, fecha, titulo="Kit Manguera"):
    conn.execute("INSERT INTO ventas_ml (num_venta,sku,fecha_venta,titulo) VALUES (?,?,?,?)",
                 (num, sku, fecha, titulo))
    conn.execute("INSERT INTO envios_colecta (num_envio,num_venta,num_venta_ml,proveedor_id,"
                 "fecha_venta) VALUES (?,?,?,?,?)", (f"E{num}", num, num, 1, fecha))
    conn.commit()


def _kit(conn, kit, *comps):
    for c in comps:
        conn.execute("INSERT INTO kit_componentes VALUES (?,?,1)", (kit, c))
    conn.commit()


def _cruzar(conn, factura_id, codigo, desc, fecha_factura, cid, codigos=None):
    """Cruza un concepto y PERSISTE el resultado, replicando el flujo real.

    ⚠️ Se imita a `routers/facturas.py`: primero se CRUZA y después se INSERTA (no al
    revés). Por eso el matcher recibe `factura={'codigos': [...]}` con los códigos del
    XML — al procesar el primer concepto sus hermanos aún no están en la BD, y sin esa
    lista el desempate por pedido no podría ver el conjunto completo de piezas.
    """
    m = match_conceptos_a_ventas(
        conn, 1, {"codigo": codigo, "descripcion": desc},
        fecha_factura=fecha_factura,
        factura={"codigos": codigos} if codigos else None,
    )
    conn.execute(
        """INSERT INTO factura_conceptos
           (id,factura_id,codigo_prov,descripcion,num_venta_match,match_method,match_confidence)
           VALUES (?,?,?,?,?,?,?)""",
        (cid, factura_id, codigo, desc,
         m["num_venta"] if m else None,
         m["method"] if m else None,
         m["confidence"] if m else None),
    )
    conn.commit()
    return m["num_venta"] if m else None


print("=" * 74)
print("UNA VENTA-KIT RECIBE TODOS SUS COMPONENTES (reporte de Gaby, 2026-08-17)")
print("=" * 74)

# ---------------------------------------------------------------- 1
print("\n1) EL CASO DE GABY: los 2 componentes del pedido van a la MISMA venta")
conn = _bd()
_kit(conn, "KIT0207", "CAU13351", "CAU13353")
_venta(conn, "V_VIEJA", "KIT0207", "2026-08-14 13:43")
_venta(conn, "V_NUEVA", "KIT0207", "2026-08-16 13:57")
conn.execute("INSERT INTO facturas VALUES (1,1,'970096936','2026-08-17 11:10')")
conn.commit()
COD_XML = ["13351 M2653711", "13353 M2653711"]
a = _cruzar(conn, 1, "13351 M2653711", "GM SPARK CALEF, ENTRADA", "2026-08-17 11:10", 100, COD_XML)
b = _cruzar(conn, 1, "13353 M2653711", "GM SPARK CALEF, SALIDA", "2026-08-17 11:10", 101, COD_XML)
check(a is not None and b is not None, f"ambos componentes cruzan (dio {a}, {b})")
check(a == b, f"🔴 LOS DOS van a la MISMA venta (dio {a} y {b})")

# ---------------------------------------------------------------- 2
print("\n2) 🔴 R1: el MISMO componente NO se cruza dos veces a la misma venta")
# Dos facturas distintas con la misma pieza = dos kits vendidos -> dos ventas.
conn = _bd()
_kit(conn, "KIT0207", "CAU13351", "CAU13353")
_venta(conn, "V_A", "KIT0207", "2026-08-10 10:00")
_venta(conn, "V_B", "KIT0207", "2026-08-11 10:00")
conn.execute("INSERT INTO facturas VALUES (1,1,'F1','2026-08-12 10:00')")
conn.execute("INSERT INTO facturas VALUES (2,1,'F2','2026-08-12 11:00')")
conn.commit()
x = _cruzar(conn, 1, "13351 M2000001", "CALEF ENTRADA", "2026-08-12 10:00", 200)
y = _cruzar(conn, 2, "13351 M2000002", "CALEF ENTRADA", "2026-08-12 11:00", 201)
check(x is not None and y is not None and x != y,
      f"dos veces la MISMA pieza caen en ventas DISTINTAS (dio {x}, {y})")

# ---------------------------------------------------------------- 3
print("\n3) 🔴 R2: `_tokens_pieza` no confunde el folio de pedido con la pieza")
check(_tokens_pieza("13351  M2653711") == {"13351"},
      f"'13351  M2653711' -> sólo la pieza (dio {_tokens_pieza('13351  M2653711')})")
check(_tokens_pieza("13353  M2653711") == {"13353"},
      "el hermano del mismo pedido da una pieza DISTINTA")
check(not (_tokens_pieza("13351  M2653711") & _tokens_pieza("13353  M2653711")),
      "🔴 dos componentes del mismo pedido NO se parecen (era el bug que partía el kit)")
check(_tokens_pieza("CAU7694") == {"7694"}, "un código sin pedido sigue funcionando")

# ---------------------------------------------------------------- 4
print("\n4) 🔴 R3: el desempate por pedido exige conjunto EXACTO, no subconjunto")
# KIT0207 = {13351,13353}; KIT03555 = esos 2 + 6 más. El pedido trae sólo 2 -> KIT0207.
conn = _bd()
_kit(conn, "KIT0207", "CAU13351", "CAU13353")
_kit(conn, "KIT03555", "CAU13351", "CAU13353", "CAU13066", "CAU13069",
     "CAU13352", "CAU13354", "CAU13355", "CAU13067")
# Ambos kits son del mismo coche: la descripción NO puede desempatar.
_venta(conn, "V_0207", "KIT0207", "2026-08-14 10:00", "Kit Manguera Spark Beat")
_venta(conn, "V_3555", "KIT03555", "2026-08-15 10:00", "Kit Mangueras Spark 1.2")
conn.execute("INSERT INTO facturas VALUES (1,1,'F','2026-08-17 10:00')")
conn.commit()
COD_XML4 = ["13351 M2653711", "13353 M2653711"]
r1 = _cruzar(conn, 1, "13351 M2653711", "GM SPARK CALEF, ENTRADA", "2026-08-17 10:00", 300, COD_XML4)
r2 = _cruzar(conn, 1, "13353 M2653711", "GM SPARK CALEF, SALIDA", "2026-08-17 10:00", 301, COD_XML4)
check(r1 == "V_0207" and r2 == "V_0207",
      f"el pedido de 2 piezas identifica a KIT0207, no al de 8 (dio {r1}, {r2})")

# ---------------------------------------------------------------- 5
print("\n5) 🔴 R3 (cont.): un pedido MIXTO no se resuelve por pedido, se abstiene")
# `M2649748` en prod trae piezas de 2 kits + 2 sueltas: el conjunto no calza con ningún
# kit, así que _desempatar_por_pedido debe devolver None y dejar decidir a la descripción.
conn = _bd()
_kit(conn, "KIT_A", "CAU4500", "CAU4508")
_kit(conn, "KIT_B", "CAU4506", "CAU4511")
conn.execute("INSERT INTO facturas VALUES (1,1,'F','2026-08-17 10:00')")
for i, cod in enumerate(("4500 M2649748", "4508 M2649748", "4506 M2649748", "4511 M2649748")):
    conn.execute("INSERT INTO factura_conceptos (id,factura_id,codigo_prov,descripcion) "
                 "VALUES (?,?,?,?)", (400 + i, 1, cod, "x"))
conn.commit()
piezas = _piezas_del_pedido(conn, "4500 M2649748")
check(piezas == {"4500", "4508", "4506", "4511"},
      f"el pedido mixto reúne las 4 piezas (dio {piezas})")
check(piezas != {"4500", "4508"} and piezas != {"4506", "4511"},
      "🔴 el conjunto NO calza con ningún kit -> el desempate por pedido se abstiene")

# ---------------------------------------------------------------- 6
print("\n6) 🔴 R4: si un hermano del pedido ya cruzó, el resto lo sigue")
conn = _bd()
_kit(conn, "KIT0207", "CAU13351", "CAU13353")
_venta(conn, "V_1", "KIT0207", "2026-08-10 10:00")
_venta(conn, "V_2", "KIT0207", "2026-08-11 10:00")
conn.execute("INSERT INTO facturas VALUES (1,1,'F','2026-08-12 10:00')")
conn.execute("""INSERT INTO factura_conceptos
   (id,factura_id,codigo_prov,descripcion,num_venta_match,match_method,match_confidence)
   VALUES (500,1,'13351 M2653711','CALEF ENTRADA','V_1','kit_componente',0.95)""")
conn.commit()
check(_venta_del_pedido(conn, "13353 M2653711") == "V_1",
      "el hermano ya cruzado marca la venta del pedido")
r = _cruzar(conn, 1, "13353 M2653711", "CALEF SALIDA", "2026-08-12 10:00", 501)
check(r == "V_1", f"el 2º componente sigue al hermano, no elige la más reciente (dio {r})")

# ---------------------------------------------------------------- 7
print("\n7) `_componente_ya_cubierto` distingue pieza cubierta de pieza nueva")
conn = _bd()
_kit(conn, "KIT0207", "CAU13351", "CAU13353")
_venta(conn, "V_X", "KIT0207", "2026-08-10 10:00")
conn.execute("INSERT INTO facturas VALUES (1,1,'F','2026-08-12 10:00')")
conn.execute("""INSERT INTO factura_conceptos
   (id,factura_id,codigo_prov,descripcion,num_venta_match) VALUES
   (600,1,'13351 M2653711','x','V_X')""")
conn.commit()
check(_componente_ya_cubierto(conn, "V_X", "13351 M2999999"),
      "la MISMA pieza (otro pedido) sí está cubierta")
check(not _componente_ya_cubierto(conn, "V_X", "13353 M2653711"),
      "🔴 el hermano del MISMO pedido NO está cubierto (era el bug)")

# ---------------------------------------------------------------- 8
print("\n8) Una venta NORMAL (no kit) sigue admitiendo una sola factura")
conn = _bd()
_venta(conn, "V_N", "CAU9999", "2026-08-10 10:00")
conn.execute("INSERT INTO facturas VALUES (1,1,'F1','2026-08-12 10:00')")
conn.execute("INSERT INTO facturas VALUES (2,1,'F2','2026-08-12 11:00')")
conn.commit()
p = _cruzar(conn, 1, "CAU9999", "Manguera", "2026-08-12 10:00", 700)
q = _cruzar(conn, 2, "CAU9999", "Manguera", "2026-08-12 11:00", 701)
check(p == "V_N", "la venta normal cruza la 1a vez")
check(q is None, "🔴 NO admite una 2a factura (el `fc.id IS NULL` sigue vivo fuera de kits)")

# ---------------------------------------------------------------- 9
print("\n9) Un kit COMPLETO no acepta más conceptos de piezas que ya tiene")
conn = _bd()
_kit(conn, "KIT0207", "CAU13351", "CAU13353")
_venta(conn, "V_U", "KIT0207", "2026-08-10 10:00")
conn.execute("INSERT INTO facturas VALUES (1,1,'F','2026-08-12 10:00')")
conn.commit()
_cruzar(conn, 1, "13351 M2000001", "ENTRADA", "2026-08-12 10:00", 800)
_cruzar(conn, 1, "13353 M2000001", "SALIDA", "2026-08-12 10:00", 801)
extra = _cruzar(conn, 1, "13351 M2000009", "ENTRADA otra vez", "2026-08-12 10:00", 802)
check(extra != "V_U", f"un 3er concepto de una pieza ya cubierta no se apila (dio {extra})")

# ---------------------------------------------------------------- 10
print("\n10) 🔴 R1 por la vía de TEXTO: la guarda también aplica ahí")
# Los casos 2 y 9 ejercitan la vía (b) (ID interno). Verificado por mutación: quitar la
# guarda de la vía (a) NO los ponía en rojo, así que hacía falta un caso que entre por
# texto — el componente va literal en el código de la factura, sin folio de pedido.
conn = _bd()
_kit(conn, "KIT_TXT", "KDTL-057", "KDTL-058")
_venta(conn, "V_T1", "KIT_TXT", "2026-08-10 10:00")
_venta(conn, "V_T2", "KIT_TXT", "2026-08-11 10:00")
conn.execute("INSERT INTO facturas VALUES (1,1,'F1','2026-08-12 10:00')")
conn.execute("INSERT INTO facturas VALUES (2,1,'F2','2026-08-12 11:00')")
conn.commit()
t1 = _cruzar(conn, 1, "KDTL-057", "Manguera", "2026-08-12 10:00", 900)
t2 = _cruzar(conn, 2, "KDTL-057", "Manguera", "2026-08-12 11:00", 901)
check(t1 is not None and t2 is not None and t1 != t2,
      f"la MISMA pieza por texto cae en ventas distintas (dio {t1}, {t2})")
t3 = _cruzar(conn, 1, "KDTL-058", "Manguera salida", "2026-08-12 10:00", 902)
check(t3 == t1, f"el otro componente SÍ entra en la misma venta (dio {t3}, esperado {t1})")

# ---------------------------------------------------------------- 11
print("\n11) 🔴 CRÍTICO (api-guardian): un código SIN separador no apaga la guarda")
# El folio de pedido se detecta con [A-Z]\d{5,}. Sin anclar, esa regex se comía el
# PREFIJO DE BODEGA pegado al número ('CAU11370' -> "folio" U11370, 'P2172292' ->
# "folio" P2172292) y `_tokens_pieza` devolvía set(). Con el conjunto vacío la guarda
# anti-apilamiento se desactivaba y N facturas de la MISMA pieza se apilaban en UNA
# sola venta con confianza 0.95 — el defecto inverso al que reportó Gaby.
check(_tokens_pieza("CAU11370") == {"11370"},
      f"'CAU11370' -> la pieza, no un folio (dio {_tokens_pieza('CAU11370')})")
check(_tokens_pieza("13351  M2653711") == {"13351"},
      "el folio SEPARADO de CAUPLAS se sigue descartando")
# 🔴 El ancla NO salva a los códigos que ABREN con letra: ahí no hay nada que anclar y
# la pieza queda vacía. Se fija el valor REAL para que la documentación no vuelva a
# divergir del código (el api-guardian cazó un comentario que afirmaba {'2172292'}).
# A éstos los protege el fallback por texto, y el assert de abajo lo demuestra.
check(_tokens_pieza("P2172292") == set(),
      f"'P2172292' queda VACÍO por diseño (dio {_tokens_pieza('P2172292')})")

conn = _bd()
_kit(conn, "KIT_AG", "P2172292", "P2172293")   # esquema real de ARGENPARTS (§2)
for i, (nv, f) in enumerate((("V1", "2026-08-10 10:00"), ("V2", "2026-08-11 10:00"),
                             ("V3", "2026-08-12 10:00"))):
    _venta(conn, nv, "KIT_AG", f, "Kit AG")
    conn.execute("INSERT INTO facturas VALUES (?,1,?, '2026-08-13 10:00')", (i + 1, f"F{i}"))
conn.commit()
destinos = [_cruzar(conn, i + 1, "P2172292", "Base amortiguador", "2026-08-13 10:00", 1000 + i)
            for i in range(3)]
cruzadas = [d for d in destinos if d]
check(len(set(cruzadas)) == len(cruzadas),
      f"🔴 3 facturas de la MISMA pieza NO se apilan en una venta (dio {destinos})")

# ---------------------------------------------------------------- 12
print("\n12) 🔴 El paso (0) no sigue a un hermano de OTRO proveedor")
# `_venta_del_pedido` no filtra por proveedor en su SQL: lo único que lo impide es el
# `e.proveedor_id = ?` del paso (0). El api-guardian midió que quitarlo NO ponía el test
# en rojo — era la única de las 4 reglas sin cobertura.
conn = _bd()
conn.execute("INSERT INTO proveedores VALUES (2,'KIMS AUTO','KIM')")
_kit(conn, "KIT0207", "CAU13351", "CAU13353")
# La venta del hermano es de KIM (proveedor 2), no de CAUPLAS.
conn.execute("INSERT INTO ventas_ml (num_venta,sku,fecha_venta,titulo) "
             "VALUES ('V_AJENA','KIT0207','2026-08-10 10:00','Kit')")
conn.execute("INSERT INTO envios_colecta (num_envio,num_venta,num_venta_ml,proveedor_id,"
             "fecha_venta) VALUES ('EA','V_AJENA','V_AJENA',2,'2026-08-10 10:00')")
conn.execute("INSERT INTO facturas VALUES (1,1,'F','2026-08-12 10:00')")
conn.execute("""INSERT INTO factura_conceptos
   (id,factura_id,codigo_prov,descripcion,num_venta_match,match_method,match_confidence)
   VALUES (1100,1,'13351 M2653711','ENTRADA','V_AJENA','kit_componente',0.95)""")
conn.commit()
r_aj = _cruzar(conn, 1, "13353 M2653711", "SALIDA", "2026-08-12 10:00", 1101)
check(r_aj != "V_AJENA",
      f"🔴 no hereda la venta de otro proveedor (dio {r_aj})")

print()
print("=" * 74)
if fallos:
    print(f"❌ {len(fallos)} FALLO(S)")
    for f in fallos:
        print("   -", f)
    sys.exit(1)
print("✅ TODO OK — la venta-kit recibe todos sus componentes, sin apilarlos")
