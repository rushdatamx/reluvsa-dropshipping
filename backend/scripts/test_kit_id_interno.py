"""Test del cruce de kits por ID interno normalizado (el reporte de Gaby del 2026-08-05).

Gaby: "los kits siguen sin detectarlos, no detecta con las facturas. Si lo detecta en el
desglose porque marca kit y los componentes pero al asignarle factura no". O sea: la tabla
Ventas SÍ muestra el kit y sus componentes (el Excel se cargó bien), pero la venta sigue
saliendo "Pendiente" aunque el proveedor ya subió el XML.

Diagnosticado contra la BD de producción con su ejemplo (factura CAUPLAS 970096331,
kits KIT0216 y KIT03554): la cadena estaba intacta — ventas, envío con proveedor CAUPLAS,
factura de CAUPLAS, componentes cargados. Lo único que fallaba era el FORMATO del código:

    Excel de Gaby   -> 'CAU11370'
    Factura CAUPLAS -> '11370  M2650963'

Es el mismo desfase de esquemas que el paso 2 (codigo_id_interno) ya resolvía para los SKU
normales desde junio, pero el paso 3 (kit_componente) comparaba texto crudo. Medido en prod:
798 de 1859 componentes usan ese formato y 88 de 106 conceptos huérfanos lo sufren.

⚠️ La hipótesis vieja del sufijo '-K' quedó DESCARTADA: sólo 8 de 1859 relaciones lo traen,
y de los 106 conceptos sin cruzar CERO coincidían con un componente ni quitándoselo.

Lo que este test fija (que un refactor NO debe romper):
  1. El caso real de Gaby: 'CAU11370' cruza contra '11370  M2650963'.
  2. El cruce por texto (sufijo -K) sigue funcionando — no hay regresión.
  3. Se exige SUBCONJUNTO de tokens, no intersección (si no, un token suelto cruza cualquier cosa).
  4. Se exige un token de >= 4 caracteres (en prod el token '409' aparece en 21 kits distintos).
  5. Aislamiento por proveedor: no cruza contra kits de otro proveedor.
  6. Un componente en 2 kits se desempata por DESCRIPCIÓN, no por azar.
  7. Ambiguo sin desempate -> NO se cruza (un cruce falso es peor que un pendiente).

Corre sin red (BD desechable en tmp). Uso: python3 backend/scripts/test_kit_id_interno.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="reluvsa_kit_id_")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test.db")

from database import get_db, init_database  # noqa: E402
from services.matcher import (  # noqa: E402
    _componente_cruza,
    _tokens_codigo,
    _tokens_componente,
    match_conceptos_a_ventas,
)

FALLOS = []


def ok(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" | {msg}")
    if not cond:
        FALLOS.append(msg)


def _pid(conn, codigo):
    return conn.execute(
        "SELECT id FROM proveedores WHERE codigo_bodega = ?", (codigo,)
    ).fetchone()["id"]


def _venta_kit(conn, num_venta, kit_sku, titulo, proveedor_id, fecha="2026-08-03 10:00:00"):
    conn.execute(
        "INSERT INTO ventas_ml (num_venta, sku, fecha_venta, titulo) VALUES (?,?,?,?)",
        (num_venta, kit_sku, fecha, titulo),
    )
    conn.execute(
        """INSERT INTO envios_colecta
           (num_envio, num_venta, num_venta_ml, match_cruce_confianza, fecha_venta,
            titulo, proveedor_id, logistic_type, cumplio_sla, excluido_analisis)
           VALUES (?,?,?,1.0,?,?,?,'cross_docking',1,0)""",
        (f"E{num_venta}", num_venta, num_venta, fecha, titulo, proveedor_id),
    )


def _componentes(conn, kit_sku, codigos):
    for cod in codigos:
        conn.execute(
            "INSERT INTO kit_componentes (kit_sku, componente_codigo, cantidad) VALUES (?,?,1)",
            (kit_sku, cod),
        )


print("\n=== Kits: cruce por ID interno normalizado ===\n")

init_database()

# ------------------------------------------------- 1) El caso real de Gaby (fact 970096331)
print("[1] El caso real de Gaby: CAU11370 vs '11370  M2650963'")

with get_db() as conn:
    cauplas = _pid(conn, "CAUPLAS")
    _venta_kit(conn, "2000017736505762", "KIT03554",
               "Kit Mangueras Radiador Inf/sup P/ Vw Vento 1.6", cauplas)
    _componentes(conn, "KIT03554", ["CAU11370", "CAU11374"])
    conn.commit()

    m = match_conceptos_a_ventas(conn, cauplas, {
        "codigo": "11370  M2650963",
        "descripcion": "VW VENTO 1.6L 2014-2021 RAD INF T/M",
    })

ok(m is not None, "el concepto de la factura CAUPLAS cruza (antes quedaba huérfano)")
ok(m and m["num_venta"] == "2000017736505762", f"cruza a la venta correcta (got {m and m['num_venta']})")
ok(m and m["method"] == "kit_componente", f"por el paso kit_componente (got {m and m['method']})")

# El segundo componente del mismo kit también.
with get_db() as conn:
    m2 = match_conceptos_a_ventas(conn, cauplas, {
        "codigo": "11374  M2650963",
        "descripcion": "VW VENTO 1.6L 2014-2021 RAD SUP T/M",
    })
ok(m2 is not None and m2["method"] == "kit_componente",
   "el 2do componente del kit también cruza")

# --------------------------------------------------------- 2) Sin regresión: sufijo -K
print("\n[2] El cruce por texto (sufijo -K) sigue funcionando")

with get_db() as conn:
    kim = _pid(conn, "KIM")
    _venta_kit(conn, "2000016762751980", "KIT0337", "Kit calaveras Hyundai Atos", kim)
    _componentes(conn, "KIT0337", ["KDTL-057-K", "KDTL-058-K"])
    conn.commit()
    m = match_conceptos_a_ventas(conn, kim, {
        "codigo": "KDTL-057", "descripcion": "Calavera trasera",
    })
ok(m is not None and m["num_venta"] == "2000016762751980" and m["method"] == "kit_componente",
   "'KDTL-057' cruza contra el componente 'KDTL-057-K' (sin regresión)")

# ------------------------------------------------------ 3-4) Las guardas anti-falso-positivo
print("\n[3] Guarda: SUBCONJUNTO de tokens, no intersección")

ok(_componente_cruza(_tokens_componente("CAU11370"), _tokens_codigo("11370  M2650963")),
   "'CAU11370' ⊆ '11370 M2650963' -> cruza")
ok(not _componente_cruza(_tokens_componente("VAZLO-30-257"), _tokens_codigo("30578  M2626339")),
   "⚠️ 'VAZLO-30-257' NO cruza con '30578...' pese a compartir un token (subconjunto, no intersección)")
ok(not _componente_cruza(_tokens_componente("CAU11370"), _tokens_codigo("11371  M2650963")),
   "un dígito distinto NO cruza")

print("\n[4] Guarda: se exige un token de >= 4 caracteres")

ok(not _componente_cruza(_tokens_componente("409"), _tokens_codigo("409  M2650963")),
   "⚠️ el componente '409' (token corto, vive en 21 kits en prod) NO cruza")
ok(not _componente_cruza(_tokens_componente("GC46"), _tokens_codigo("46 123 456")),
   "un componente sin ningún token largo NO cruza")
ok(_componente_cruza(_tokens_componente("19280663"), _tokens_codigo("19280663  M2650963")),
   "un token largo sí cruza")

# El falso positivo, end to end: una venta-kit de otro coche no debe robarse el concepto.
with get_db() as conn:
    ag = _pid(conn, "AG")
    _venta_kit(conn, "V-AG-CORTO", "KITAG1", "Kit generico", ag)
    _componentes(conn, "KITAG1", ["409"])
    conn.commit()
    m = match_conceptos_a_ventas(conn, ag, {"codigo": "409  M2650963", "descripcion": ""})
ok(m is None, "⚠️ E2E: el componente de token corto no se roba el concepto")

# ------------------------------------------------------------ 5) Aislamiento por proveedor
print("\n[5] Aislamiento por proveedor")

with get_db() as conn:
    vazlo = _pid(conn, "VAZLO")
    m = match_conceptos_a_ventas(conn, vazlo, {
        "codigo": "11370  M2650963", "descripcion": "VW VENTO 1.6L RAD INF",
    })
ok(m is None, "el kit de CAUPLAS no cruza con una factura de VAZLO")

# --------------------------------------------------- 6) Desempate por descripción
print("\n[6] Componente en 2 kits: desempata la descripción")

with get_db() as conn:
    kg = _pid(conn, "KG")
    # El MISMO componente (CAU4506) en dos kits de coches distintos, ambos sin facturar.
    _venta_kit(conn, "V-KG-VENTO", "KITKG-VENTO", "Kit mangueras VW Vento 1.6",
               kg, fecha="2026-08-01 10:00:00")
    _componentes(conn, "KITKG-VENTO", ["CAU4506"])
    _venta_kit(conn, "V-KG-CHEVY", "KITKG-CHEVY", "Kit mangueras GM Chevy 1.6",
               kg, fecha="2026-08-02 10:00:00")  # más RECIENTE a propósito
    _componentes(conn, "KITKG-CHEVY", ["CAU4506"])
    conn.commit()

    # La descripción dice VENTO, pero la venta más reciente es la de CHEVY: si el
    # desempate por descripción no funcionara, ganaría CHEVY por antigüedad.
    m = match_conceptos_a_ventas(conn, kg, {
        "codigo": "4506  M2649748",
        "descripcion": "VW VENTO 1.6L 2014-2021 RAD SUP T/M",
    })

ok(m is not None, "cruza pese a la ambigüedad")
ok(m and m["num_venta"] == "V-KG-VENTO",
   f"⚠️ gana el kit del coche correcto por descripción, no el más reciente (got {m and m['num_venta']})")

# ------------------------------------- 7) Ambiguo sin desempate: NO se cruza (None)
print("\n[7] Ambiguo y sin desempate: prefiere NO cruzar")

with get_db() as conn:
    m = match_conceptos_a_ventas(conn, kg, {"codigo": "4506  M2649748", "descripcion": ""})
ok(m is None,
   f"⚠️ sin descripción que desempate NO inventa un cruce (got {m and m['num_venta']})")

# El caso real que lo motivó: medido contra prod, elegir "la más reciente" cruzaba un
# concepto de Nissan Platina al kit de un Clio (comparten plataforma → comparten
# componentes). Un cruce falso dice "ya facturado" y nadie lo vuelve a revisar.
with get_db() as conn:
    ag2 = _pid(conn, "VAZLO")
    _venta_kit(conn, "V-PLATINA", "KIT-PLAT", "Kit mangueras Nissan Platina 1.6",
               ag2, fecha="2026-08-01 10:00:00")
    _componentes(conn, "KIT-PLAT", ["CAU6537"])
    _venta_kit(conn, "V-CLIO", "KIT-CLIO", "Kit mangueras Renault Clio 1.6",
               ag2, fecha="2026-08-02 10:00:00")  # más reciente
    _componentes(conn, "KIT-CLIO", ["CAU6537"])
    conn.commit()
    m = match_conceptos_a_ventas(conn, ag2, {"codigo": "6537  M2650987", "descripcion": ""})
ok(m is None, "⚠️ Platina/Clio: sin descripción no se cruza al kit equivocado")

# Pero CON descripción, el mismo caso sí resuelve al correcto.
with get_db() as conn:
    m = match_conceptos_a_ventas(conn, ag2, {
        "codigo": "6537  M2650987", "descripcion": "NSN PLATINA 1.6L 2001-2009 RAD SUP",
    })
ok(m is not None and m["num_venta"] == "V-PLATINA",
   f"con descripción sí resuelve al kit correcto (got {m and m['num_venta']})")

# ------------------------- 8) El MISMO kit vendido N veces sí cruza (no es ambigüedad)
print("\n[8] El mismo kit vendido varias veces: cruza sin necesitar descripción")

# Es el caso masivo en prod (ej. 'KIT0454' de Chevy vendido 6 veces): el producto no está
# en duda, sólo a cuál de las ventas idénticas aplicar la factura. Distinto de tener 2
# kits DISTINTOS, donde sí se puede cruzar al producto equivocado.
with get_db() as conn:
    kim2 = _pid(conn, "KIM")
    for i, f in enumerate(["2026-08-01 10:00:00", "2026-08-02 10:00:00", "2026-08-03 10:00:00"]):
        _venta_kit(conn, f"V-REPE-{i}", "KIT-REPE",
                   "Kit De 9 Mangueras P/ Chevrolet Chevy", kim2, fecha=f)
    _componentes(conn, "KIT-REPE", ["CAU7777"])
    conn.commit()
    m = match_conceptos_a_ventas(conn, kim2, {"codigo": "7777  M2649748", "descripcion": ""})

ok(m is not None, "⚠️ el mismo kit repetido SÍ cruza aunque no haya descripción")
ok(m and m["num_venta"] == "V-REPE-2",
   f"y elige la venta más reciente sin facturar (got {m and m['num_venta']})")
ok(m and m["confidence"] == 0.95,
   f"con confianza plena: el producto no estaba en duda (got {m and m['confidence']})")

# ---------------------------------------------------------------------------- resumen
print("\n" + "=" * 60)
if FALLOS:
    print(f"❌ {len(FALLOS)} FALLA(S):")
    for m_ in FALLOS:
        print(f"   - {m_}")
    sys.exit(1)
print("✅ TODO OK — los kits cruzan por ID interno sin abrir falsos positivos")
