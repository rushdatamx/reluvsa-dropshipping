"""
Fija las trampas de la corrección del # de venta de KIM con ceros de más.

Contexto: Gaby reportó (2026-08-12) que KIM sí imprime el # de venta en el PDF, pero
"en la factura viene 6 ceros y en la venta correcta es 4 ceros". Medido en prod: el 69%
de los 473 números impresos trae ceros de más (longitudes de 15 a 19 dígitos).

Estos tests fijan lo que NO debe romperse al tocar
`corregir_cruces_num_venta_kim_ceros.py`. Corre sin red y con BD desechable.

    /opt/venv/bin/python backend/scripts/test_num_venta_kim_ceros.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from corregir_cruces_num_venta_kim_ceros import (  # noqa: E402
    PATRON_NUM_IMPRESO,
    candidatos_por_ceros,
    sku_cuadra,
    construir_plan,
    aplicar,
)

fallas = []


def check(nombre, cond):
    print(("  ok   " if cond else "  FALLA") + f"  {nombre}")
    if not cond:
        fallas.append(nombre)


# ---------------------------------------------------------------- extracción
print("\n1. El patrón reconoce el número impreso con ceros de más")

check("captura 16 dígitos (el caso limpio)",
      PATRON_NUM_IMPRESO.findall("#2000017847026330") == ["2000017847026330"])
check("captura 17 dígitos (1 cero de más)",
      PATRON_NUM_IMPRESO.findall("#20000016910060548") == ["20000016910060548"])
check("captura 19 dígitos (3 ceros de más)",
      PATRON_NUM_IMPRESO.findall("#2000000017634639548") == ["2000000017634639548"])
check("captura con espacio tras el gato",
      PATRON_NUM_IMPRESO.findall("# 2000017847026330") == ["2000017847026330"])
# El bloque del PDF trae importes y RFC alrededor; no deben confundirse con el número.
check("NO captura números cortos (importes, folios)",
      PATRON_NUM_IMPRESO.findall("#30166 (Ocho dolares 65/100)") == [])
check("exige el gato: un número suelto no cuenta",
      PATRON_NUM_IMPRESO.findall("total 2000017847026330") == [])


# ------------------------------------------------------- R1: borrar, no inventar
print("\n2. R1 — sólo se BORRAN ceros, nunca se agrega ni se cambia un dígito")

check("16 dígitos se devuelve tal cual",
      candidatos_por_ceros("2000017847026330") == {"2000017847026330"})

c17 = candidatos_por_ceros("20000016910060548")
check("17 dígitos produce el real por borrado de un cero",
      "2000016910060548" in c17)
check("toda variante conserva 16 dígitos y empieza con 2",
      all(len(v) == 16 and v.startswith("2") for v in c17))

# 🔴 La trampa que motivó R2/R3: borrar ceros fabrica varios números con pinta válida.
c_trampa = candidatos_por_ceros("20000016910060548")
check("un mismo impreso puede dar VARIAS variantes plausibles", len(c_trampa) > 1)

# 🔴 Y el caso que prueba que R1 NO basta por sí sola (K29628, medido en prod):
# impreso 20000117582609224 vs venta real 2000017582609224 -> difieren en MÁS que un
# cero (el impreso trae '11' donde el real trae '01'). Ninguna variante por borrado da
# con la venta, y las que produce son números inexistentes con forma válida.
# Es correcto que este caso NO se corrija: R2/R3 lo descartan al no hallar venta.
c_k29628 = candidatos_por_ceros("20000117582609224")
check("K29628: el número real NO es alcanzable borrando ceros",
      "2000017582609224" not in c_k29628)
check("K29628: aun así produce variantes de forma válida (por eso hacen falta R2/R3)",
      len(c_k29628) > 0 and all(len(v) == 16 for v in c_k29628))

# Nunca se agregan dígitos: un número más corto que 16 no se puede completar.
check("15 dígitos NO produce candidatas (no se inventan dígitos)",
      candidatos_por_ceros("200001727393774") == set())
# Cota de seguridad: más de 6 ceros sobrantes se descarta en vez de explotar
# combinatoriamente (y sería un dato demasiado corrupto para confiar).
check("más de 6 dígitos sobrantes se descarta",
      candidatos_por_ceros("2" + "0" * 30) == set())

# Sólo se borran CEROS: un dígito distinto jamás se toca.
check("no se borran dígitos que no sean cero",
      all(v.count("9") == "29999916910060548".count("9")
          for v in candidatos_por_ceros("29999916910060548")))


# ------------------------------------------------------------ R3: SKU cuadra
print("\n3. R3 — el SKU de la venta debe ser la misma pieza que el concepto")

check("KIM factura sin sufijo y publica con -Z: cuadra",
      sku_cuadra("96413748", "96413748-Z"))
check("idénticos cuadran", sku_cuadra("98510-M4000-Z", "98510-M4000-Z"))
check("piezas distintas NO cuadran", not sku_cuadra("96413748", "96591481-Z"))
check("vacío no cuadra (no se asume)", not sku_cuadra("", "96413748-Z"))
check("tokens de 1-2 chars no bastan para cruzar",
      not sku_cuadra("A1", "B2-Z"))


# ------------------------------------------------------- plan sobre BD desechable
print("\n4. El plan sobre una BD real (desechable)")


def bd_de_prueba():
    ruta = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(ruta)
    conn.executescript("""
        CREATE TABLE proveedores (id INTEGER PRIMARY KEY, nombre TEXT,
                                  codigo_bodega TEXT);
        CREATE TABLE ventas_ml (num_venta TEXT PRIMARY KEY, sku TEXT,
                                fecha_venta TEXT, pack_id TEXT);
        CREATE TABLE envios_colecta (num_envio TEXT PRIMARY KEY, num_venta_ml TEXT,
                                     proveedor_id INTEGER);
        CREATE TABLE facturas (id INTEGER PRIMARY KEY, proveedor_id INTEGER,
                               serie TEXT, folio TEXT, fecha_factura TEXT,
                               pdf_path TEXT);
        CREATE TABLE factura_conceptos (id INTEGER PRIMARY KEY, factura_id INTEGER,
                                        codigo_prov TEXT, num_venta_match TEXT,
                                        match_method TEXT, match_confidence REAL);
        INSERT INTO proveedores VALUES (2,'KIMS AUTO','KIM'),(1,'QUALITY HOSES','CAUPLAS');
    """)
    return ruta, conn


ruta, conn = bd_de_prueba()
# Venta real de KIM; la factura imprimirá el número con un cero de más.
conn.execute("INSERT INTO ventas_ml VALUES ('2000016910060548','96413748-Z','2026-06-10',NULL)")
conn.execute("INSERT INTO envios_colecta VALUES ('E1','2000016910060548',2)")
# La venta a la que el portal la cruzó por error (misma pieza, otra fecha).
conn.execute("INSERT INTO ventas_ml VALUES ('2000016785027442','96413748-Z','2026-06-05',NULL)")
conn.execute("INSERT INTO envios_colecta VALUES ('E2','2000016785027442',2)")
conn.execute("INSERT INTO facturas VALUES (10,2,'K','28116','2026-06-15','/f/k28116.pdf')")
conn.execute("INSERT INTO factura_conceptos VALUES "
             "(100,10,'96413748','2000016785027442','codigo_exact',1.0)")
conn.commit()

cache = {"k28116.pdf": ["20000016910060548"]}  # 17 dígitos, como en prod
plan = construir_plan(conn, "/f", cache)

check("detecta la corrección pese al cero de más", len(plan["correcciones"]) == 1)
if plan["correcciones"]:
    c = plan["correcciones"][0]
    check("reasigna a la venta del número impreso normalizado",
          c["a"] == "2000016910060548")
    check("registra de dónde venía", c["de"] == "2000016785027442")

aplicar(conn, plan)
r = conn.execute("SELECT num_venta_match, match_method, match_confidence "
                 "FROM factura_conceptos WHERE id=100").fetchone()
check("aplica el cambio en BD", r[0] == "2000016910060548")
check("marca el método del proveedor", r[1] == "num_venta_proveedor")

# Idempotencia: una 2a corrida no debe encontrar nada.
plan2 = construir_plan(conn, "/f", cache)
check("IDEMPOTENTE: 2a corrida no cambia nada",
      len(plan2["correcciones"]) == 0 and len(plan2["liberar"]) == 0)

# Nunca se borra una fila de factura_conceptos.
n = conn.execute("SELECT COUNT(*) FROM factura_conceptos").fetchone()[0]
check("NINGUNA fila borrada", n == 1)
conn.close()
os.unlink(ruta)


# ---------------------------------------------------- R2 y R3 como guardas duras
print("\n5. Las guardas: ante duda NO se toca (un falso es peor que un pendiente)")

# R2: la venta existe y el número cuadra, pero el envío es de OTRO proveedor.
ruta, conn = bd_de_prueba()
conn.execute("INSERT INTO ventas_ml VALUES ('2000016910060548','96413748-Z','2026-06-10',NULL)")
conn.execute("INSERT INTO envios_colecta VALUES ('E1','2000016910060548',1)")  # CAUPLAS
conn.execute("INSERT INTO facturas VALUES (10,2,'K','28116','2026-06-15','/f/k.pdf')")
conn.execute("INSERT INTO factura_conceptos VALUES (100,10,'96413748',NULL,NULL,NULL)")
conn.commit()
plan = construir_plan(conn, "/f", {"k.pdf": ["20000016910060548"]})
check("R2: venta de OTRO proveedor -> no se toca",
      len(plan["correcciones"]) == 0 and plan["no_es_kim"] == 1)
conn.close(); os.unlink(ruta)

# R3: el número resuelve a una venta de KIM pero de OTRA pieza.
ruta, conn = bd_de_prueba()
conn.execute("INSERT INTO ventas_ml VALUES ('2000016910060548','96591481-Z','2026-06-10',NULL)")
conn.execute("INSERT INTO envios_colecta VALUES ('E1','2000016910060548',2)")
conn.execute("INSERT INTO facturas VALUES (10,2,'K','28116','2026-06-15','/f/k.pdf')")
conn.execute("INSERT INTO factura_conceptos VALUES (100,10,'96413748',NULL,NULL,NULL)")
conn.commit()
plan = construir_plan(conn, "/f", {"k.pdf": ["20000016910060548"]})
check("R3: SKU distinto -> no se toca",
      len(plan["correcciones"]) == 0 and plan["sku_no_cuadra"] == 1)
conn.close(); os.unlink(ruta)

# 🔴 La trampa central: DOS variantes de ceros existen, ambas de KIM y misma pieza.
# Sin la guarda se elegiría una al azar y se escribiría un cruce falso con conf 1.0.
ruta, conn = bd_de_prueba()
conn.execute("INSERT INTO ventas_ml VALUES ('2000016910060548','96413748-Z','2026-06-10',NULL)")
conn.execute("INSERT INTO ventas_ml VALUES ('2000160910060548','96413748-Z','2026-06-11',NULL)")
conn.execute("INSERT INTO envios_colecta VALUES ('E1','2000016910060548',2)")
conn.execute("INSERT INTO envios_colecta VALUES ('E2','2000160910060548',2)")
conn.execute("INSERT INTO facturas VALUES (10,2,'K','28116','2026-06-15','/f/k.pdf')")
conn.execute("INSERT INTO factura_conceptos VALUES (100,10,'96413748',NULL,NULL,NULL)")
conn.commit()
plan = construir_plan(conn, "/f", {"k.pdf": ["20000160910060548"]})
check("AMBIGUO (2 variantes válidas de KIM y misma pieza) -> NO se cruza ninguna",
      len(plan["correcciones"]) == 0 and plan["multi_candidata"] == 1)
conn.close(); os.unlink(ruta)

# Garantía heredada: la venta no puede ser posterior a su factura.
ruta, conn = bd_de_prueba()
conn.execute("INSERT INTO ventas_ml VALUES ('2000016910060548','96413748-Z','2026-07-20',NULL)")
conn.execute("INSERT INTO envios_colecta VALUES ('E1','2000016910060548',2)")
conn.execute("INSERT INTO facturas VALUES (10,2,'K','28116','2026-06-15','/f/k.pdf')")
conn.execute("INSERT INTO factura_conceptos VALUES (100,10,'96413748',NULL,NULL,NULL)")
conn.commit()
plan = construir_plan(conn, "/f", {"k.pdf": ["20000016910060548"]})
check("venta POSTERIOR a la factura -> no se toca",
      len(plan["correcciones"]) == 0 and plan["venta_posterior"] == 1)
conn.close(); os.unlink(ruta)

# El mismo # en DOS facturas distintas: ambiguo, no se toca ninguna (heredado).
ruta, conn = bd_de_prueba()
conn.execute("INSERT INTO ventas_ml VALUES ('2000016910060548','96413748-Z','2026-06-10',NULL)")
conn.execute("INSERT INTO envios_colecta VALUES ('E1','2000016910060548',2)")
conn.execute("INSERT INTO facturas VALUES (10,2,'K','28116','2026-06-15','/f/a.pdf')")
conn.execute("INSERT INTO facturas VALUES (11,2,'K','28117','2026-06-15','/f/b.pdf')")
conn.execute("INSERT INTO factura_conceptos VALUES (100,10,'96413748',NULL,NULL,NULL)")
conn.execute("INSERT INTO factura_conceptos VALUES (101,11,'96413748',NULL,NULL,NULL)")
conn.commit()
plan = construir_plan(conn, "/f",
                      {"a.pdf": ["20000016910060548"], "b.pdf": ["20000016910060548"]})
check("mismo # en 2 facturas -> ambiguo, no se toca ninguna",
      len(plan["correcciones"]) == 0 and plan["ambiguos"] == 2)
conn.close(); os.unlink(ruta)


# --------------------------------------------------------- exclusividad de KIM
print("\n6. ⚠️ EXCLUSIVO DE KIM: ningún otro proveedor se toca")

ruta, conn = bd_de_prueba()
# Una factura de CAUPLAS cuyo PDF (hipotéticamente) trajera un número.
conn.execute("INSERT INTO ventas_ml VALUES ('2000016910060548','CAU2692','2026-06-10',NULL)")
conn.execute("INSERT INTO envios_colecta VALUES ('E1','2000016910060548',1)")
conn.execute("INSERT INTO facturas VALUES (10,1,'CD','970091508','2026-06-15','/f/c.pdf')")
conn.execute("INSERT INTO factura_conceptos VALUES (100,10,'CAU2692',NULL,NULL,NULL)")
conn.commit()
plan = construir_plan(conn, "/f", {"c.pdf": ["20000016910060548"]})
check("una factura de CAUPLAS ni siquiera se revisa",
      plan["facturas_revisadas"] == 0 and len(plan["correcciones"]) == 0)
conn.close(); os.unlink(ruta)

import corregir_cruces_num_venta_kim_ceros as mod  # noqa: E402
check("la constante de proveedor sigue siendo KIM", mod.CODIGO_BODEGA == "KIM")
fuente = open(mod.__file__).read()
check("el filtro por proveedor está en la consulta de facturas",
      "f.proveedor_id = ?" in fuente)
check("las ventas candidatas se filtran por proveedor del envío",
      "e.proveedor_id" in fuente)


# -------------------------------------------- Garantía 5: liberar al ocupante ajeno
print("\n7. Garantía 5 — el ocupante ajeno se libera; el hermano NO")

# ⚠️ POR QUÉ ESTOS 2 CASOS: hasta 2026-08-12 la liberación sólo se comprobaba con
# `len(plan["liberar"]) == 0` en la prueba de idempotencia. Un assert que nunca ve el
# caso positivo está VERDE POR OMISIÓN: si `liberar` dejara de calcularse, o si se
# calculara de más, ningún test se ponía en rojo. (Mismo error que el e2e de
# `total_neto`.) Aquí se ejercita la rama en sus DOS direcciones.

# (a) POSITIVO: la venta destino ya la ocupa un concepto de OTRA factura de KIM.
#     Ese cruce es falso por construcción (el # impreso dice que la venta es de la
#     factura A), así que debe quedar PENDIENTE — nunca reasignado a ciegas.
ruta, conn = bd_de_prueba()
conn.execute("INSERT INTO ventas_ml VALUES ('2000016910060548','96413748-Z','2026-06-10',NULL)")
conn.execute("INSERT INTO envios_colecta VALUES ('E1','2000016910060548',2)")
conn.execute("INSERT INTO ventas_ml VALUES ('2000016785027442','96413748-Z','2026-06-05',NULL)")
conn.execute("INSERT INTO envios_colecta VALUES ('E2','2000016785027442',2)")
# Factura A: su PDF dice que la venta es la '...060548'; hoy apunta a la equivocada.
conn.execute("INSERT INTO facturas VALUES (10,2,'K','28116','2026-06-15','/f/a.pdf')")
conn.execute("INSERT INTO factura_conceptos VALUES "
             "(100,10,'96413748','2000016785027442','codigo_exact',1.0)")
# Factura B (otra factura de KIM, sin # impreso): está sentada en la venta destino.
conn.execute("INSERT INTO facturas VALUES (11,2,'K','28200','2026-06-16','/f/b.pdf')")
conn.execute("INSERT INTO factura_conceptos VALUES "
             "(101,11,'96413748','2000016910060548','codigo_exact',1.0)")
conn.commit()
plan = construir_plan(conn, "/f", {"a.pdf": ["20000016910060548"], "b.pdf": []})

check("(a) el ocupante de OTRA factura SÍ entra en la lista de liberación",
      len(plan["liberar"]) == 1
      and plan["liberar"][0]["concepto_id"] == 101)
check("(a) el concepto que se corrige NO se libera a sí mismo",
      all(l["concepto_id"] != 100 for l in plan["liberar"]))

aplicar(conn, plan)
r_ocup = conn.execute("SELECT num_venta_match, match_method, match_confidence "
                      "FROM factura_conceptos WHERE id=101").fetchone()
check("(a) tras aplicar, el ocupante queda PENDIENTE (los 3 campos en NULL)",
      r_ocup[0] is None and r_ocup[1] is None and r_ocup[2] is None)
r_corr = conn.execute("SELECT num_venta_match FROM factura_conceptos "
                      "WHERE id=100").fetchone()
check("(a) y la corrección sí se escribió (no se pisaron entre sí)",
      r_corr[0] == "2000016910060548")
check("(a) NINGUNA fila borrada al liberar",
      conn.execute("SELECT COUNT(*) FROM factura_conceptos").fetchone()[0] == 2)
plan_a2 = construir_plan(conn, "/f", {"a.pdf": ["20000016910060548"], "b.pdf": []})
check("(a) IDEMPOTENTE: la 2a corrida ya no libera nada",
      len(plan_a2["correcciones"]) == 0 and len(plan_a2["liberar"]) == 0)
conn.close(); os.unlink(ruta)

# (b) NEGATIVO — el bug que hacía OSCILAR al script hermano: dos conceptos de la MISMA
#     factura apuntando a la misma venta es LEGÍTIMO (KIM parte una venta en 2 piezas).
#     Si "ajeno" se comparara por concepto y no por factura, cada hermano liberaría al
#     otro: la corrida N los borra, la N+1 los vuelve a poner, y nunca converge.
ruta, conn = bd_de_prueba()
conn.execute("INSERT INTO ventas_ml VALUES ('2000016910060548','96413748-Z','2026-06-10',NULL)")
conn.execute("INSERT INTO envios_colecta VALUES ('E1','2000016910060548',2)")
conn.execute("INSERT INTO ventas_ml VALUES ('2000016785027442','96413748-Z','2026-06-05',NULL)")
conn.execute("INSERT INTO envios_colecta VALUES ('E2','2000016785027442',2)")
conn.execute("INSERT INTO facturas VALUES (10,2,'K','28116','2026-06-15','/f/a.pdf')")
# Hermano 1: mal cruzado -> se corrige. Hermano 2: YA está en la venta correcta.
conn.execute("INSERT INTO factura_conceptos VALUES "
             "(100,10,'96413748','2000016785027442','codigo_exact',1.0)")
conn.execute("INSERT INTO factura_conceptos VALUES "
             "(101,10,'96413748','2000016910060548','codigo_exact',1.0)")
conn.commit()
plan = construir_plan(conn, "/f", {"a.pdf": ["20000016910060548"]})

check("(b) el hermano de la MISMA factura NO se libera",
      len(plan["liberar"]) == 0)
check("(b) el hermano ya correcto se cuenta como ya_ok, no como corrección",
      plan["ya_ok"] == 1 and len(plan["correcciones"]) == 1)

aplicar(conn, plan)
r1 = conn.execute("SELECT num_venta_match FROM factura_conceptos WHERE id=100").fetchone()
r2 = conn.execute("SELECT num_venta_match FROM factura_conceptos WHERE id=101").fetchone()
check("(b) los DOS hermanos quedan en la misma venta (ninguno pendiente)",
      r1[0] == "2000016910060548" and r2[0] == "2000016910060548")

# NO OSCILA: 3 corridas seguidas dejan el mismo estado.
estable = True
for _ in range(3):
    p = construir_plan(conn, "/f", {"a.pdf": ["20000016910060548"]})
    if p["correcciones"] or p["liberar"]:
        estable = False
        break
    aplicar(conn, p)
check("(b) NO OSCILA: 3 corridas extra no mueven nada", estable)
conn.close(); os.unlink(ruta)


print("\n" + "=" * 62)
if fallas:
    print(f"🔴 {len(fallas)} FALLAS:")
    for f in fallas:
        print("   -", f)
    sys.exit(1)
print("✅ TODO OK")
sys.exit(0)
