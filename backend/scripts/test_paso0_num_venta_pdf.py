"""
Fija el PASO 0 del matcher: el # de venta que KIM imprime en el PDF manda sobre la fecha.

Contexto (2026-08-12): la corrección histórica (commit `0161ade`) arregló 228 cruces
hacia atrás, pero el motor seguía cruzando las facturas NUEVAS por fecha — el número
vive sólo en el PDF y el matcher leía el XML. Mario decidió: **para KIM, fijarse
siempre en el PDF**, tolerando los ceros de más.

Lo que fijan estos tests:
  1. El PDF con número GANA sobre el cruce por fecha (aunque la fecha diera otra venta).
  2. 🔴 Si KIM puso número y NO resuelve -> NO se cruza (pendiente), no se cae a fecha.
  3. Sin número / sin PDF -> se cruza por fecha como siempre (el ~45% de hoy).
  4. ⚠️ EXCLUSIVO DE KIM: ningún otro proveedor lee su PDF.
  5. El PDF que llega DESPUÉS del XML corrige el cruce por inferencia ya escrito.

Corre sin red, con BD desechable y PDFs generados al vuelo.

    backend/.venv/bin/python backend/scripts/test_paso0_num_venta_pdf.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

fallas = []
tmpdir = tempfile.mkdtemp()

# El matcher resuelve los PDF contra UPLOADS_DIR/facturas; se apunta al tmp ANTES de
# importarlo (la constante se evalúa al importar).
os.environ["UPLOADS_DIR"] = tmpdir
os.makedirs(os.path.join(tmpdir, "facturas"), exist_ok=True)

from services.matcher import (  # noqa: E402
    match_conceptos_a_ventas,
    recruzar_conceptos_sin_match,
)
from services.num_venta_pdf import (  # noqa: E402
    METODO,
    candidatos_por_ceros,
    resolver_num_venta_impreso,
    sku_cuadra,
)


def check(nombre, cond):
    print(("  ok   " if cond else "  FALLA") + f"  {nombre}")
    if not cond:
        fallas.append(nombre)


def pdf_con_texto(nombre: str, texto: str) -> str:
    """Genera un PDF real (con capa de texto) para que pdfplumber lo lea de verdad.

    Se escribe un PDF mínimo a mano en vez de mockear la extracción: mockearla dejaría
    sin probar justo la parte que toca el mundo real (que el texto se pueda extraer).
    """
    ruta = os.path.join(tmpdir, "facturas", nombre)
    contenido = f"BT /F1 12 Tf 50 700 Td ({texto}) Tj ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(contenido)} >>\nstream\n{contenido}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n"
    out += (f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF")
    open(ruta, "wb").write(out.encode("latin-1"))
    return ruta


def bd():
    ruta = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE proveedores (id INTEGER PRIMARY KEY, nombre TEXT, codigo_bodega TEXT);
        CREATE TABLE ventas_ml (num_venta TEXT PRIMARY KEY, sku TEXT, titulo TEXT,
                                fecha_venta TEXT);
        CREATE TABLE envios_colecta (num_envio TEXT PRIMARY KEY, num_venta_ml TEXT,
                                     proveedor_id INTEGER);
        CREATE TABLE facturas (id INTEGER PRIMARY KEY, proveedor_id INTEGER, folio TEXT,
                               fecha_factura TEXT, pdf_path TEXT, num_venta_pdf TEXT);
        CREATE TABLE factura_conceptos (id INTEGER PRIMARY KEY, factura_id INTEGER,
                                        codigo_prov TEXT, descripcion TEXT,
                                        num_venta_match TEXT, match_method TEXT,
                                        match_confidence REAL);
        CREATE TABLE kit_componentes (kit_sku TEXT, componente_codigo TEXT, cantidad REAL);
        INSERT INTO proveedores VALUES (2,'KIMS AUTO','KIM'),(1,'QUALITY HOSES','CAUPLAS');
    """)
    return ruta, conn


# Dos ventas de KIM, MISMA pieza. La "vieja" es la que ganaría por fecha; la del PDF es
# la correcta. Es exactamente la forma del bug que produjo los 216 cruces falsos.
VENTA_PDF = "2000016910060548"
VENTA_FECHA = "2000016785027442"
SKU = "96413748-Z"


def sembrar(conn, pdf_nombre, texto_pdf):
    conn.execute("INSERT INTO ventas_ml VALUES (?,?,'Manguera',?)",
                 (VENTA_PDF, SKU, "2026-06-10"))
    conn.execute("INSERT INTO envios_colecta VALUES ('E1',?,2)", (VENTA_PDF,))
    conn.execute("INSERT INTO ventas_ml VALUES (?,?,'Manguera',?)",
                 (VENTA_FECHA, SKU, "2026-06-12"))   # más reciente -> ganaría por fecha
    conn.execute("INSERT INTO envios_colecta VALUES ('E2',?,2)", (VENTA_FECHA,))
    ruta = pdf_con_texto(pdf_nombre, texto_pdf) if texto_pdf is not None else None
    conn.execute("INSERT INTO facturas VALUES (10,2,'28116','2026-06-15',?,NULL)", (ruta,))
    conn.commit()
    return ruta


CTX = lambda ruta: {"codigo_bodega": "KIM", "pdf_path": ruta, "codigos": ["96413748"]}


# ------------------------------------------------------------------ 1. el PDF manda
print("\n1. El # impreso GANA sobre el cruce por fecha")

ruta, conn = bd()
p = sembrar(conn, "k1.pdf", "FACTURA KIM  # 20000016910060548  TOTAL $100")  # 17 dígitos
m = match_conceptos_a_ventas(conn, 2, {"codigo": "96413748", "descripcion": "Manguera"},
                             fecha_factura="2026-06-15", factura=CTX(p))
check("cruza a la venta del PDF, no a la que ganaría por fecha",
      m and m["num_venta"] == VENTA_PDF)
check("marca el método del proveedor", m and m["method"] == METODO)
check("con confianza 1.0", m and m["confidence"] == 1.0)

# Sin el contexto de factura el comportamiento viejo se conserva intacto: es lo que
# permite que los 14 llamadores existentes (tests y scripts) sigan valiendo sin tocarse.
m_viejo = match_conceptos_a_ventas(conn, 2, {"codigo": "96413748", "descripcion": "x"},
                                   fecha_factura="2026-06-15")
check("SIN contexto de factura se conserva el comportamiento anterior",
      m_viejo and m_viejo["num_venta"] == VENTA_FECHA
      and m_viejo["method"] == "codigo_exact")
conn.close(); os.unlink(ruta)


# --------------------------------------------- 2. número corrupto -> NO se cruza
print("\n2. 🔴 KIM puso número y NO resuelve -> pendiente, NO se cae a fecha")

# K29628 real: imprimió '20000117582609224' (un '1' donde iba un '0'). Ninguna variante
# por borrado de ceros da con una venta existente.
ruta, conn = bd()
p = sembrar(conn, "k2.pdf", "FACTURA KIM  # 20000117582609224")
m = match_conceptos_a_ventas(conn, 2, {"codigo": "96413748", "descripcion": "Manguera"},
                             fecha_factura="2026-06-15", factura=CTX(p))
check("número irrecuperable -> NO cruza (aunque la fecha sí daría una venta)", m is None)
conn.close(); os.unlink(ruta)

# El número resuelve a una venta de KIM pero de OTRA pieza -> tampoco.
ruta, conn = bd()
conn.execute("UPDATE ventas_ml SET sku='99999999-Z' WHERE num_venta=?", (VENTA_PDF,))
p = sembrar(conn, "k3.pdf", "FACTURA KIM  # 20000016910060548")
conn.execute("UPDATE ventas_ml SET sku='99999999-Z' WHERE num_venta=?", (VENTA_PDF,))
conn.commit()
m = match_conceptos_a_ventas(conn, 2, {"codigo": "96413748", "descripcion": "Manguera"},
                             fecha_factura="2026-06-15", factura=CTX(p))
check("SKU que no cuadra -> NO cruza", m is None)
conn.close(); os.unlink(ruta)


# ------------------------------------------- 3. sin número -> se cruza como siempre
print("\n3. Sin # impreso se cruza por fecha (el ~45% de hoy NO debe quedar pendiente)")

ruta, conn = bd()
p = sembrar(conn, "k4.pdf", "FACTURA KIM sin numero de venta  TOTAL $100")
m = match_conceptos_a_ventas(conn, 2, {"codigo": "96413748", "descripcion": "Manguera"},
                             fecha_factura="2026-06-15", factura=CTX(p))
check("PDF sin número -> cruza por el camino normal",
      m and m["method"] == "codigo_exact" and m["num_venta"] == VENTA_FECHA)
conn.close(); os.unlink(ruta)

ruta, conn = bd()
sembrar(conn, "k5.pdf", None)   # factura sin PDF
m = match_conceptos_a_ventas(conn, 2, {"codigo": "96413748", "descripcion": "Manguera"},
                             fecha_factura="2026-06-15",
                             factura={"codigo_bodega": "KIM", "pdf_path": None,
                                      "codigos": ["96413748"]})
check("factura SIN PDF -> cruza por el camino normal", m and m["method"] == "codigo_exact")

# Un PDF cuyo path ya no existe (contenedor viejo) tampoco debe romper nada.
m = match_conceptos_a_ventas(conn, 2, {"codigo": "96413748", "descripcion": "Manguera"},
                             fecha_factura="2026-06-15",
                             factura={"codigo_bodega": "KIM",
                                      "pdf_path": "/no/existe/x.pdf",
                                      "codigos": ["96413748"]})
check("PDF inexistente -> cruza por el camino normal, no revienta",
      m and m["method"] == "codigo_exact")
conn.close(); os.unlink(ruta)


# ------------------------------------------------------- 4. exclusivo de KIM
print("\n4. ⚠️ EXCLUSIVO DE KIM: no se lee el PDF de otros proveedores")

ruta, conn = bd()
p = sembrar(conn, "c1.pdf", "FACTURA  # 20000016910060548")
# Mismo PDF, mismo número, pero la factura es de CAUPLAS.
conn.execute("UPDATE envios_colecta SET proveedor_id=1")
conn.commit()
m = match_conceptos_a_ventas(conn, 1, {"codigo": "96413748", "descripcion": "Manguera"},
                             fecha_factura="2026-06-15",
                             factura={"codigo_bodega": "CAUPLAS", "pdf_path": p,
                                      "codigos": ["96413748"]})
check("CAUPLAS no usa el paso 0 (cruza por el camino normal)",
      m and m["method"] == "codigo_exact")
conn.close(); os.unlink(ruta)

import services.num_venta_pdf as mod  # noqa: E402
check("la constante de proveedor sigue siendo KIM", mod.CODIGO_BODEGA_KIM == "KIM")


# ---------------------------------- 5. el PDF que llega TARDE corrige la inferencia
print("\n5. El PDF que llega DESPUÉS del XML corrige el cruce por inferencia")

# ⚠️ Éste es el hueco que motivó el cambio de regla del recruce: hasta hoy "sólo
# enriquecía, nunca rompía un match existente", así que un XML-primero-PDF-después
# dejaba el cruce por fecha (posiblemente falso, con conf 1.0) para siempre.
ruta, conn = bd()
p = sembrar(conn, "k6.pdf", "FACTURA KIM  # 20000016910060548")
# El concepto YA cruzó por fecha a la venta equivocada (lo que pasa al subir sólo el XML).
conn.execute("INSERT INTO factura_conceptos VALUES "
             "(100,10,'96413748','Manguera',?,'codigo_exact',1.0)", (VENTA_FECHA,))
conn.commit()

res = recruzar_conceptos_sin_match(conn)
r = conn.execute("SELECT num_venta_match, match_method, match_confidence "
                 "FROM factura_conceptos WHERE id=100").fetchone()
check("el recruce corrige el cruce por inferencia al aparecer el PDF",
      r["num_venta_match"] == VENTA_PDF)
check("y lo sella con el método del proveedor",
      r["match_method"] == METODO and r["match_confidence"] == 1.0)
check("lo reporta en el resumen", res.get("conceptos_corregidos_por_pdf") == 1)

# IDEMPOTENTE: la 2a corrida no vuelve a contar ni a mover nada.
res2 = recruzar_conceptos_sin_match(conn)
r2 = conn.execute("SELECT num_venta_match FROM factura_conceptos WHERE id=100").fetchone()
check("IDEMPOTENTE: la 2a corrida no corrige nada",
      res2.get("conceptos_corregidos_por_pdf") == 0 and r2[0] == VENTA_PDF)
check("NINGUNA fila borrada",
      conn.execute("SELECT COUNT(*) FROM factura_conceptos").fetchone()[0] == 1)
conn.close(); os.unlink(ruta)

# 🔴 Un número corrupto NO debe DESTRUIR un cruce que ya existía. Aquí, a diferencia del
# alta, borrarlo quitaría información sin poner nada mejor en su lugar.
ruta, conn = bd()
p = sembrar(conn, "k7.pdf", "FACTURA KIM  # 20000117582609224")   # irrecuperable
conn.execute("INSERT INTO factura_conceptos VALUES "
             "(100,10,'96413748','Manguera',?,'codigo_exact',1.0)", (VENTA_FECHA,))
conn.commit()
recruzar_conceptos_sin_match(conn)
r = conn.execute("SELECT num_venta_match FROM factura_conceptos WHERE id=100").fetchone()
check("número corrupto NO borra un cruce previo del recruce", r[0] == VENTA_FECHA)
conn.close(); os.unlink(ruta)

# Y un cruce ya sellado por el proveedor no se vuelve a tocar ni a degradar.
ruta, conn = bd()
p = sembrar(conn, "k8.pdf", "FACTURA KIM  # 20000016910060548")
conn.execute("INSERT INTO factura_conceptos VALUES "
             "(100,10,'96413748','Manguera',?,?,1.0)", (VENTA_PDF, METODO))
conn.commit()
res = recruzar_conceptos_sin_match(conn)
check("un cruce ya sellado por el proveedor no se recuenta",
      res.get("conceptos_corregidos_por_pdf") == 0)
conn.close(); os.unlink(ruta)


# ------------------------------------------------- las 3 reglas siguen intactas
print("\n6. Las 3 reglas del servicio (heredadas de la corrección histórica)")

check("R1: sólo se borran ceros, nunca se inventan dígitos",
      candidatos_por_ceros("200001727393774") == set())
check("R1: el real sale por borrado", "2000016910060548" in
      candidatos_por_ceros("20000016910060548"))
check("R3: KIM factura sin sufijo y publica con -Z", sku_cuadra("96413748", "96413748-Z"))
check("R3: piezas distintas no cuadran", not sku_cuadra("96413748", "96591481-Z"))

# 🔴 Ambigüedad: dos variantes válidas del mismo proveedor y la misma pieza -> None.
ruta, conn = bd()
conn.execute("INSERT INTO ventas_ml VALUES ('2000016910060548',?,'m','2026-06-10')", (SKU,))
conn.execute("INSERT INTO ventas_ml VALUES ('2000160910060548',?,'m','2026-06-11')", (SKU,))
conn.execute("INSERT INTO envios_colecta VALUES ('E1','2000016910060548',2)")
conn.execute("INSERT INTO envios_colecta VALUES ('E2','2000160910060548',2)")
conn.commit()
check("R3: >1 candidata válida -> None (no se adivina)",
      resolver_num_venta_impreso(conn, 2, "20000160910060548", ["96413748"],
                                 "2026-06-15") is None)
conn.close(); os.unlink(ruta)

# Garantía heredada: la venta no puede ser posterior a su factura.
ruta, conn = bd()
conn.execute("INSERT INTO ventas_ml VALUES ('2000016910060548',?,'m','2026-07-20')", (SKU,))
conn.execute("INSERT INTO envios_colecta VALUES ('E1','2000016910060548',2)")
conn.commit()
check("venta POSTERIOR a la factura -> None",
      resolver_num_venta_impreso(conn, 2, "20000016910060548", ["96413748"],
                                 "2026-06-15") is None)
check("misma venta, factura posterior -> sí resuelve",
      resolver_num_venta_impreso(conn, 2, "20000016910060548", ["96413748"],
                                 "2026-08-01") == "2000016910060548")
conn.close(); os.unlink(ruta)


# ------------------------------------------------- 7. la caché del # impreso
print("\n7. La caché: el PDF se abre UNA vez, no en cada recruce")

# ⚠️ POR QUÉ EXISTE: el recruce corre tras CADA carga de ventas/colecta, dentro de su
# transacción. Medido en prod, entrarían 548 facturas de KIM al conjunto y ~45% NO trae
# número -> se releerían para siempre sin corregir nada. Con la caché, un PDF leído no
# se vuelve a abrir.
import services.matcher as mm  # noqa: E402

ruta, conn = bd()
p = sembrar(conn, "k9.pdf", "FACTURA KIM sin numero")
conn.execute("INSERT INTO factura_conceptos VALUES "
             "(100,10,'96413748','Manguera',?,'codigo_exact',1.0)", (VENTA_FECHA,))
conn.commit()

lecturas = {"n": 0}
_real = mm.extraer_numeros_pdf
mm.extraer_numeros_pdf = lambda r: (lecturas.__setitem__("n", lecturas["n"] + 1),
                                    _real(r))[1]

recruzar_conceptos_sin_match(conn)
tras_1a = lecturas["n"]
recruzar_conceptos_sin_match(conn)
recruzar_conceptos_sin_match(conn)
mm.extraer_numeros_pdf = _real

check("el PDF se lee en la 1a corrida", tras_1a == 1)
check("🔴 un PDF SIN número no se vuelve a abrir nunca", lecturas["n"] == 1)

# El estado se distingue: '' = leído sin número (≠ NULL = sin leer).
v = conn.execute("SELECT num_venta_pdf FROM facturas WHERE id=10").fetchone()[0]
check("se cachea '' (leído, sin número), NO NULL", v == "")
check("y el cruce previo se conserva intacto",
      conn.execute("SELECT num_venta_match FROM factura_conceptos "
                   "WHERE id=100").fetchone()[0] == VENTA_FECHA)
conn.close(); os.unlink(ruta)

# Con número: se cachea el número y tampoco se relee.
ruta, conn = bd()
p = sembrar(conn, "k10.pdf", "FACTURA KIM  # 20000016910060548")
conn.execute("INSERT INTO factura_conceptos VALUES "
             "(100,10,'96413748','Manguera',?,'codigo_exact',1.0)", (VENTA_FECHA,))
conn.commit()
lecturas["n"] = 0
mm.extraer_numeros_pdf = lambda r: (lecturas.__setitem__("n", lecturas["n"] + 1),
                                    _real(r))[1]
recruzar_conceptos_sin_match(conn)
recruzar_conceptos_sin_match(conn)
mm.extraer_numeros_pdf = _real
check("con número: también se abre una sola vez", lecturas["n"] == 1)
check("se cachea el número hallado",
      conn.execute("SELECT num_venta_pdf FROM facturas WHERE id=10").fetchone()[0]
      == "20000016910060548")
check("y la corrección se aplicó",
      conn.execute("SELECT num_venta_match FROM factura_conceptos "
                   "WHERE id=100").fetchone()[0] == VENTA_PDF)
conn.close(); os.unlink(ruta)

# Un PDF que todavía NO existe no debe cachearse como "sin número": puede llegar después
# (es justo el caso XML-primero-PDF-después que este cambio vino a resolver).
ruta, conn = bd()
sembrar(conn, "k11.pdf", None)
conn.execute("UPDATE facturas SET pdf_path='/no/existe/aun.pdf' WHERE id=10")
conn.commit()
mm._num_impreso_cacheado(conn, 10, "/no/existe/aun.pdf")
check("un PDF ausente NO se cachea (el archivo puede llegar después)",
      conn.execute("SELECT num_venta_pdf FROM facturas WHERE id=10").fetchone()[0] is None)
conn.close(); os.unlink(ruta)


print("\n" + "=" * 62)
if fallas:
    print(f"🔴 {len(fallas)} FALLAS:")
    for f in fallas:
        print("   -", f)
    sys.exit(1)
print("✅ TODO OK — el PDF de KIM manda sobre la fecha, y ante duda queda pendiente")
sys.exit(0)
