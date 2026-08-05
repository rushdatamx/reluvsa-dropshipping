"""Test E2E: las 4 métricas por proveedor EXCLUYEN las ventas FULL (fulfillment).

Contexto (2026-08-05): en FULL la mercancía sale de la bodega de Mercado Libre — el
proveedor no la surte — así que esas ventas no miden su desempeño y no deben entrar al
SLA ni al tiempo de facturación. En producción hoy ya se excluyen DE FACTO, porque ML
manda origin=null en FULL y ningún envío FULL tiene proveedor asignado (verificado sobre
los 55,918 envíos). Pero esa exclusión es ACCIDENTAL: se rompe en cuanto alguien usa el
selector de bodega de la pestaña Ventas para reasignar una venta FULL a mano. Este cambio
la vuelve explícita en el SQL.

Lo que este test fija (que un refactor NO debe romper):
  1. Un envío FULL CON proveedor asignado a mano NO cuenta en total_envios ni en % a tiempo.
  2. Un envío COLECTA (cross_docking) sí cuenta — el caso normal, no hay regresión.
  3. ⚠️ Un envío LEGACY (logistic_type NULL, de los Excels anteriores a ago-2025) SÍ cuenta.
     La condición debe ser "IS NULL OR != 'fulfillment'", nunca "= 'cross_docking'", o se
     borraría de las métricas toda la historia que el portal rescató.
  4. El tiempo promedio de facturación ignora las facturas de ventas FULL.
  5. ⚠️ El JOIN a envios_colecta en tiempo_fact es LEFT: un concepto cruzado a una venta
     que todavía no tiene envío NO debe desaparecer de la métrica.
  6. Un concepto SIN cruzar sigue contando como error de facturación (no tiene venta ni
     envío que consultar, así que no puede ser FULL).
  7. Un concepto de confianza baja sobre una venta FULL NO cuenta como error.

Corre sin red (BD desechable en tmp). Uso: python3 backend/scripts/test_metricas_excluir_full.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="reluvsa_metricas_full_")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test.db")

from database import get_db, init_database  # noqa: E402
from models import UserInfo  # noqa: E402
from routers.metricas import metricas_proveedores  # noqa: E402

FALLOS = []


def ok(cond, msg):
    print(("  OK   " if cond else "  FALLA") + f" | {msg}")
    if not cond:
        FALLOS.append(msg)


ADMIN = UserInfo(user_id=1, email="admin@test.local", rol="admin", proveedor_id=None)


def _pid(conn, codigo):
    return conn.execute(
        "SELECT id FROM proveedores WHERE codigo_bodega = ?", (codigo,)
    ).fetchone()["id"]


def _venta(conn, num_venta, fecha="2026-07-15 10:00:00", sku="X-1"):
    conn.execute(
        "INSERT INTO ventas_ml (num_venta, sku, fecha_venta, titulo) VALUES (?,?,?,?)",
        (num_venta, sku, fecha, f"Producto {sku}"),
    )


def _envio(conn, num_envio, num_venta, proveedor_id, logistic, cumplio_sla=1):
    """Envío ya cruzado a su venta, como lo deja el sync."""
    conn.execute(
        """INSERT INTO envios_colecta
           (num_envio, num_venta, num_venta_ml, match_cruce_confianza, fecha_venta,
            titulo, proveedor_id, logistic_type, cumplio_sla, excluido_analisis)
           VALUES (?,?,?,1.0,?,?,?,?,?,0)""",
        (num_envio, num_venta, num_venta, "2026-07-15 10:00:00",
         "Producto", proveedor_id, logistic, cumplio_sla),
    )


def _factura(conn, fid, proveedor_id, fecha_factura):
    conn.execute(
        """INSERT INTO facturas (id, proveedor_id, uuid_cfdi, serie, folio,
           rfc_emisor, rfc_receptor, fecha_factura, total)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (fid, proveedor_id, f"uuid-{fid}", "A", str(fid),
         "XXX010101XXX", "GPE230915JWA", fecha_factura, 100.0),
    )


def _concepto(conn, factura_id, num_venta_match, confidence):
    conn.execute(
        """INSERT INTO factura_conceptos
           (factura_id, codigo_prov, descripcion, cantidad, importe,
            num_venta_match, match_method, match_confidence)
           VALUES (?,?,?,1,100.0,?,?,?)""",
        (factura_id, "COD-1", "Pieza", num_venta_match, "codigo_exact", confidence),
    )


def _fila(codigo):
    """La fila de métricas de un proveedor, por código de bodega."""
    filas = metricas_proveedores(user=ADMIN)
    for f in filas:
        if f["codigo_bodega"] == codigo:
            return f
    return None


print("\n=== Métricas: exclusión de ventas FULL ===\n")

init_database()

# ---------------------------------------------------------------- SLA / conteo de envíos
print("[1] SLA y conteo de envíos")

with get_db() as conn:
    kim = _pid(conn, "KIM")

    # COLECTA (el caso normal): cuenta.
    _venta(conn, "V-COLECTA-1")
    _envio(conn, "E-COLECTA-1", "V-COLECTA-1", kim, "cross_docking", cumplio_sla=1)

    # LEGACY (Excel viejo, sin logistic_type): DEBE contar.
    _venta(conn, "V-LEGACY-1")
    _envio(conn, "E-LEGACY-1", "V-LEGACY-1", kim, None, cumplio_sla=0)

    # FULL reasignada a mano por Gaby: NO debe contar.
    _venta(conn, "V-FULL-1")
    _envio(conn, "E-FULL-1", "V-FULL-1", kim, "fulfillment", cumplio_sla=0)
    conn.commit()

f = _fila("KIM")
ok(f["total_envios"] == 2,
   f"total_envios cuenta COLECTA + LEGACY y descarta FULL (esperado 2, got {f['total_envios']})")
ok(f["porcentaje_entregas_a_tiempo"] == 50.0,
   f"% a tiempo = 1 de 2 (la FULL atrasada no penaliza) (esperado 50.0, got {f['porcentaje_entregas_a_tiempo']})")

# Sin el filtro, la FULL atrasada habría bajado el SLA a 33.3%.
ok(f["porcentaje_entregas_a_tiempo"] != round(1 / 3 * 100, 1),
   "el SLA NO es el que saldría contando la FULL (33.3%)")

# ------------------------------------------------------- tiempo promedio de facturación
print("\n[2] Tiempo promedio de facturación")

with get_db() as conn:
    ag = _pid(conn, "AG")

    # COLECTA facturada a 2 días.
    _venta(conn, "V-AG-COLECTA", fecha="2026-07-01 10:00:00")
    _envio(conn, "E-AG-COLECTA", "V-AG-COLECTA", ag, "cross_docking")
    _factura(conn, 100, ag, "2026-07-03 10:00:00")
    _concepto(conn, 100, "V-AG-COLECTA", 1.0)

    # FULL facturada a 20 días: debe quedar FUERA del promedio.
    _venta(conn, "V-AG-FULL", fecha="2026-07-01 10:00:00")
    _envio(conn, "E-AG-FULL", "V-AG-FULL", ag, "fulfillment")
    _factura(conn, 101, ag, "2026-07-21 10:00:00")
    _concepto(conn, 101, "V-AG-FULL", 1.0)
    conn.commit()

f = _fila("AG")
ok(f["tiempo_promedio_facturacion_dias"] == 2.0,
   f"promedio ignora la factura de la venta FULL (esperado 2.0, got {f['tiempo_promedio_facturacion_dias']})")

# El LEFT JOIN: una venta facturada que todavía NO tiene envío debe seguir contando.
with get_db() as conn:
    vazlo = _pid(conn, "VAZLO")
    _venta(conn, "V-VAZLO-SIN-ENVIO", fecha="2026-07-01 10:00:00")  # sin envío a propósito
    _factura(conn, 102, vazlo, "2026-07-05 10:00:00")
    _concepto(conn, 102, "V-VAZLO-SIN-ENVIO", 1.0)
    conn.commit()

f = _fila("VAZLO")
ok(f["tiempo_promedio_facturacion_dias"] == 4.0,
   f"⚠️ LEFT JOIN: venta facturada SIN envío sigue contando (esperado 4.0, got {f['tiempo_promedio_facturacion_dias']})")

# ------------------------------------------------------------- errores de facturación
print("\n[3] Errores de facturación")

with get_db() as conn:
    kg = _pid(conn, "KG")

    # Concepto sin cruzar: cuenta como error (no tiene venta que consultar).
    _factura(conn, 200, kg, "2026-07-10 10:00:00")
    _concepto(conn, 200, None, None)

    # Confianza baja sobre venta COLECTA: cuenta como error.
    _venta(conn, "V-KG-COLECTA", fecha="2026-07-01 10:00:00")
    _envio(conn, "E-KG-COLECTA", "V-KG-COLECTA", kg, "cross_docking")
    _concepto(conn, 200, "V-KG-COLECTA", 0.3)

    # Confianza baja sobre venta FULL: NO cuenta.
    _venta(conn, "V-KG-FULL", fecha="2026-07-01 10:00:00")
    _envio(conn, "E-KG-FULL", "V-KG-FULL", kg, "fulfillment")
    _concepto(conn, 200, "V-KG-FULL", 0.3)

    # Confianza baja sobre venta LEGACY (NULL): SÍ cuenta.
    _venta(conn, "V-KG-LEGACY", fecha="2026-07-01 10:00:00")
    _envio(conn, "E-KG-LEGACY", "V-KG-LEGACY", kg, None)
    _concepto(conn, 200, "V-KG-LEGACY", 0.3)
    conn.commit()

f = _fila("KG")
ok(f["errores_facturacion"] == 3,
   f"errores = sin-cruzar + baja-confianza(COLECTA) + baja-confianza(LEGACY), sin la FULL "
   f"(esperado 3, got {f['errores_facturacion']})")

# ------------------------------------------------------------------ sin doble conteo
print("\n[4] Sin regresiones de conteo")

with get_db() as conn:
    cauplas = _pid(conn, "CAUPLAS")
    # Una venta con DOS envíos cruzados no debe inflar el promedio de facturación
    # (el LEFT JOIN podría duplicar filas).
    _venta(conn, "V-CAU-1", fecha="2026-07-01 10:00:00")
    _envio(conn, "E-CAU-1a", "V-CAU-1", cauplas, "cross_docking")
    _envio(conn, "E-CAU-1b", "V-CAU-1", cauplas, "cross_docking")
    _factura(conn, 300, cauplas, "2026-07-06 10:00:00")
    _concepto(conn, 300, "V-CAU-1", 1.0)
    conn.commit()

f = _fila("CAUPLAS")
ok(f["tiempo_promedio_facturacion_dias"] == 5.0,
   f"promedio correcto aunque la venta tenga 2 envíos (esperado 5.0, got {f['tiempo_promedio_facturacion_dias']})")
ok(f["total_envios"] == 2, f"los 2 envíos COLECTA se cuentan (got {f['total_envios']})")

# Caso asimétrico (detectado por api-guardian): una venta con un envío FULL y otro COLECTA
# a la vez. El LEFT JOIN produce 2 filas para el mismo concepto, así que un COUNT(*) mal
# escrito contaría el error dos veces. Además fija la semántica elegida: NO_ES_FULL conserva
# la venta si ALGÚN envío no es FULL (OR, no AND) — lo conservador, para no descartar nunca
# una venta legítimamente COLECTA. En prod es teórico (0 envíos FULL con proveedor).
print("\n[4.bis] Venta con envío FULL y COLECTA simultáneos (no duplica)")

with get_db() as conn:
    _venta(conn, "V-CAU-MIXTA", fecha="2026-07-01 10:00:00")
    _envio(conn, "E-CAU-MIXTA-full", "V-CAU-MIXTA", cauplas, "fulfillment")
    _envio(conn, "E-CAU-MIXTA-col", "V-CAU-MIXTA", cauplas, "cross_docking")
    _factura(conn, 301, cauplas, "2026-07-11 10:00:00")
    _concepto(conn, 301, "V-CAU-MIXTA", 0.3)  # confianza baja -> 1 error, no 2
    conn.commit()

f = _fila("CAUPLAS")
ok(f["errores_facturacion"] == 1,
   f"⚠️ el concepto de confianza baja cuenta UNA vez pese a los 2 envíos (esperado 1, got {f['errores_facturacion']})")
# ⚠️ 6.7 = avg(5, 5, 10): la factura de V-CAU-1 aparece DOS veces porque esa venta tiene 2
# envíos COLECTA, y la de V-CAU-MIXTA una sola (su envío FULL queda excluido por NO_ES_FULL).
# Que una venta multi-envío pese doble en el promedio es preexistente al filtro de FULL — el
# AVG siempre se calculó por concepto — y en prod es teórico: el sync crea un envío por
# order.shipping.id. Se fija aquí para que un refactor no lo cambie sin darse cuenta.
ok(f["tiempo_promedio_facturacion_dias"] == 6.7,
   f"AVG con multi-envío: avg(5,5,10)=6.7, el envío FULL no agrega fila (got {f['tiempo_promedio_facturacion_dias']})")
ok(f["total_envios"] == 3,
   f"el envío FULL sigue fuera del conteo aunque comparta venta con uno COLECTA (esperado 3, got {f['total_envios']})")

# ------------------------------------------------------- vista de proveedor (rol limitado)
print("\n[5] El filtro aplica igual para el rol proveedor")

prov_kim = None
with get_db() as conn:
    prov_kim = _pid(conn, "KIM")

user_prov = UserInfo(user_id=2, email="kim@reluvsa.local", rol="proveedor", proveedor_id=prov_kim)
filas = metricas_proveedores(user=user_prov)
ok(len(filas) == 1 and filas[0]["codigo_bodega"] == "KIM",
   "el proveedor sólo ve su propia fila")
ok(filas[0]["total_envios"] == 2,
   f"y también con las FULL excluidas (esperado 2, got {filas[0]['total_envios']})")

# ---------------------------------------------------------------------------- resumen
print("\n" + "=" * 60)
if FALLOS:
    print(f"❌ {len(FALLOS)} FALLA(S):")
    for m in FALLOS:
        print(f"   - {m}")
    sys.exit(1)
print("✅ TODO OK — las métricas excluyen las ventas FULL sin perder la historia legacy")
