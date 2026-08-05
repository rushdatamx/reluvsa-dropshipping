"""Las 4 métricas por proveedor: SLA, tiempo facturación, errores, frecuencia stock."""
from typing import Optional

from fastapi import APIRouter, Depends

from database import get_db
from models import UserInfo
from routers.auth import get_current_user

router = APIRouter(prefix="/api/metricas", tags=["metricas"])

# Las ventas FULL (fulfillment) las despacha Mercado Libre desde su propia bodega:
# no son dropshipping, así que no miden el desempeño de ningún proveedor. Se excluyen
# de las 4 métricas.
#
# ⚠️ La condición es "IS NULL OR != 'fulfillment'", NUNCA "= 'cross_docking'": los envíos
# que vienen de los Excels legacy (anteriores a ago-2025) tienen logistic_type NULL para
# siempre — la API de ML sólo entrega 12 meses — y filtrarlos por igualdad borraría de las
# métricas toda la historia que el portal rescató.
NO_ES_FULL = "(e.logistic_type IS NULL OR e.logistic_type != 'fulfillment')"


@router.get("/proveedores")
def metricas_proveedores(
    user: UserInfo = Depends(get_current_user),
    proveedor_id: Optional[int] = None,
):
    if user.rol == "proveedor":
        proveedor_id = user.proveedor_id

    where = "WHERE p.activo = 1"
    params: list = []
    if proveedor_id:
        where += " AND p.id = ?"
        params.append(proveedor_id)

    with get_db() as conn:
        proveedores = conn.execute(
            f"SELECT id, nombre, codigo_bodega FROM proveedores p {where} ORDER BY nombre",
            params,
        ).fetchall()

        result = []
        for p in proveedores:
            pid = p["id"]

            total = conn.execute(
                f"""SELECT COUNT(*) c FROM envios_colecta e
                    WHERE e.proveedor_id = ? AND e.excluido_analisis = 0
                      AND {NO_ES_FULL}""",
                (pid,),
            ).fetchone()["c"]

            a_tiempo = conn.execute(
                f"""SELECT COUNT(*) c FROM envios_colecta e
                    WHERE e.proveedor_id = ? AND e.cumplio_sla = 1 AND e.excluido_analisis = 0
                      AND {NO_ES_FULL}""",
                (pid,),
            ).fetchone()["c"]

            pct_a_tiempo = round(a_tiempo / total * 100, 1) if total else None

            # El LEFT JOIN a envios_colecta es SÓLO para poder descartar las FULL. Tiene que
            # ser LEFT y no JOIN: un concepto cruzado a una venta que todavía no tiene envío
            # desaparecería de la métrica y cambiaría los números de hoy. Con LEFT, esa venta
            # deja logistic_type en NULL y NO_ES_FULL la conserva.
            tiempo_fact = conn.execute(
                f"""SELECT AVG(JULIANDAY(f.fecha_factura) - JULIANDAY(v.fecha_venta)) as avg_dias
                   FROM facturas f
                   JOIN factura_conceptos fc ON fc.factura_id = f.id
                   JOIN ventas_ml v ON v.num_venta = fc.num_venta_match
                   LEFT JOIN envios_colecta e ON e.num_venta_ml = v.num_venta
                   WHERE f.proveedor_id = ? AND v.fecha_venta IS NOT NULL AND f.fecha_factura IS NOT NULL
                     AND {NO_ES_FULL}""",
                (pid,),
            ).fetchone()

            # Un concepto SIN cruzar (num_venta_match IS NULL) no tiene venta ni envío que
            # consultar, así que no puede ser FULL: se cuenta siempre. El filtro de FULL sólo
            # aplica a los conceptos que sí cruzaron pero con confianza baja.
            errores = conn.execute(
                f"""SELECT COUNT(*) c FROM factura_conceptos fc
                   JOIN facturas f ON f.id = fc.factura_id
                   LEFT JOIN ventas_ml v ON v.num_venta = fc.num_venta_match
                   LEFT JOIN envios_colecta e ON e.num_venta_ml = v.num_venta
                   WHERE f.proveedor_id = ?
                     AND (fc.num_venta_match IS NULL
                          OR (fc.match_confidence < 0.5 AND {NO_ES_FULL}))""",
                (pid,),
            ).fetchone()["c"]

            incidencias_abiertas = conn.execute(
                "SELECT COUNT(*) c FROM incidencias WHERE proveedor_id = ? AND estado = 'abierta'",
                (pid,),
            ).fetchone()["c"]

            ultima = conn.execute(
                "SELECT MAX(fecha_subida) f FROM catalogos_proveedor WHERE proveedor_id = ?",
                (pid,),
            ).fetchone()

            dias_stock = None
            if ultima and ultima["f"]:
                dias_stock = conn.execute(
                    "SELECT CAST(JULIANDAY('now') - JULIANDAY(?) AS INTEGER) d",
                    (ultima["f"],),
                ).fetchone()["d"]

            result.append({
                "proveedor_id": pid,
                "proveedor_nombre": p["nombre"],
                "codigo_bodega": p["codigo_bodega"],
                "total_envios": total,
                "porcentaje_entregas_a_tiempo": pct_a_tiempo,
                "tiempo_promedio_facturacion_dias": round(tiempo_fact["avg_dias"], 1) if tiempo_fact["avg_dias"] is not None else None,
                "errores_facturacion": errores,
                "incidencias_abiertas": incidencias_abiertas,
                "dias_desde_ultima_actualizacion_stock": dias_stock,
            })

    return result


@router.get("/resumen")
def resumen(user: UserInfo = Depends(get_current_user)):
    with get_db() as conn:
        ventas = conn.execute("SELECT COUNT(*) c FROM ventas_ml").fetchone()["c"]
        envios = conn.execute("SELECT COUNT(*) c FROM envios_colecta").fetchone()["c"]
        facturas = conn.execute("SELECT COUNT(*) c FROM facturas").fetchone()["c"]
        incidencias_abiertas = conn.execute(
            "SELECT COUNT(*) c FROM incidencias WHERE estado = 'abierta'"
        ).fetchone()["c"]
        proveedores = conn.execute(
            "SELECT COUNT(*) c FROM proveedores WHERE activo = 1"
        ).fetchone()["c"]

    return {
        "ventas": ventas,
        "envios": envios,
        "facturas": facturas,
        "incidencias_abiertas": incidencias_abiertas,
        "proveedores_activos": proveedores,
    }
