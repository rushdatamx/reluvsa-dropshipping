"""Las 4 métricas por proveedor: SLA, tiempo facturación, errores, frecuencia stock."""
from typing import Optional

from fastapi import APIRouter, Depends

from database import get_db
from models import UserInfo
from routers.auth import get_current_user

router = APIRouter(prefix="/api/metricas", tags=["metricas"])


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
                "SELECT COUNT(*) c FROM envios_colecta WHERE proveedor_id = ? AND excluido_analisis = 0",
                (pid,),
            ).fetchone()["c"]

            a_tiempo = conn.execute(
                "SELECT COUNT(*) c FROM envios_colecta WHERE proveedor_id = ? AND cumplio_sla = 1 AND excluido_analisis = 0",
                (pid,),
            ).fetchone()["c"]

            pct_a_tiempo = round(a_tiempo / total * 100, 1) if total else None

            tiempo_fact = conn.execute(
                """SELECT AVG(JULIANDAY(f.fecha_factura) - JULIANDAY(v.fecha_venta)) as avg_dias
                   FROM facturas f
                   JOIN factura_conceptos fc ON fc.factura_id = f.id
                   JOIN ventas_ml v ON v.num_venta = fc.num_venta_match
                   WHERE f.proveedor_id = ? AND v.fecha_venta IS NOT NULL AND f.fecha_factura IS NOT NULL""",
                (pid,),
            ).fetchone()

            errores = conn.execute(
                """SELECT COUNT(*) c FROM factura_conceptos fc
                   JOIN facturas f ON f.id = fc.factura_id
                   WHERE f.proveedor_id = ? AND (fc.num_venta_match IS NULL OR fc.match_confidence < 0.5)""",
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
