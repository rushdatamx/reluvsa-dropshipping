"""Listado y consulta de ventas ML (admin) y pedidos pendientes (proveedor)."""
import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from database import get_db
from models import UserInfo
from routers.auth import get_current_user

router = APIRouter(prefix="/api/ventas", tags=["ventas"])


def _construir_filtros(
    user: UserInfo,
    proveedor_id: Optional[int],
    estado: Optional[str],
    q: Optional[str],
    facturada: Optional[str],
    sla: Optional[str],
    cruce: Optional[str],
    fecha_desde: Optional[str],
    fecha_hasta: Optional[str],
    deposito: Optional[str] = None,
):
    """Arma la cláusula WHERE + JOINs compartida por el listado y el export.

    Devuelve (where_list, params, join_factura). El JOIN a factura_conceptos se
    agrega solo si algún filtro de facturación lo necesita.

    `deposito` controla la bodega de origen (col 'Depósito' del reporte ML):
      - None / "proveedores" (default): OCULTA MATRIZ (bodega propia, no dropshipping).
      - "matriz": solo MATRIZ.
      - "todos": sin filtro de bodega.
    """
    if user.rol == "proveedor":
        proveedor_id = user.proveedor_id

    where = ["1=1"]
    params: list = []
    join_factura = ""

    if proveedor_id:
        where.append("e.proveedor_id = ?")
        params.append(proveedor_id)

    if estado:
        where.append("v.estado = ?")
        params.append(estado)

    if q:
        where.append("(v.num_venta LIKE ? OR v.sku LIKE ? OR v.titulo LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])

    # Facturada / sin factura. Se evalúa con un subquery EXISTS para no duplicar
    # filas cuando una venta tiene varios conceptos facturados.
    existe_factura = "EXISTS (SELECT 1 FROM factura_conceptos fc WHERE fc.num_venta_match = v.num_venta)"
    if facturada == "true":
        where.append(existe_factura)
    elif facturada == "false":
        where.append("NOT " + existe_factura)

    # SLA del envío: a tiempo (1) / tarde (0). Implica que haya envío cruzado.
    if sla == "a_tiempo":
        where.append("e.cumplio_sla = 1")
    elif sla == "tarde":
        where.append("e.cumplio_sla = 0")

    # Estado del cruce venta <-> colecta.
    if cruce == "con_envio":
        where.append("e.num_envio IS NOT NULL")
    elif cruce == "sin_envio":
        where.append("e.num_envio IS NULL")
    elif cruce == "sin_proveedor":
        where.append("e.num_envio IS NOT NULL AND e.proveedor_id IS NULL")

    # Bodega de origen (col 'Depósito'). Por defecto se oculta MATRIZ (ruido: es
    # bodega propia de RELUVSA, no proveedor dropshipping). Gaby ya no la quita a mano.
    if deposito == "matriz":
        where.append("v.deposito = 'MATRIZ'")
    elif deposito == "todos":
        pass  # sin filtro: muestra todo, incluida MATRIZ
    else:  # None / "proveedores": comportamiento por defecto
        where.append("(v.deposito IS NULL OR v.deposito != 'MATRIZ')")

    # Rango por fecha de venta (ISO 'YYYY-MM-DD...', compara como string).
    if fecha_desde:
        where.append("v.fecha_venta >= ?")
        params.append(fecha_desde)
    if fecha_hasta:
        # Incluir todo el día 'hasta': comparar con el fin del día.
        where.append("v.fecha_venta <= ?")
        params.append(fecha_hasta + " 23:59:59")

    return where, params, join_factura


_SELECT_VENTAS = """
    SELECT v.num_venta, v.sku, v.deposito, v.fecha_venta, v.estado, v.titulo, v.unidades,
           v.total, v.comprador_estado, v.forma_entrega,
           e.num_envio, e.lugar_indicado, e.lugar_real, e.lugar_override, e.cumplio_sla,
           e.proveedor_id, p.nombre as proveedor_nombre,
           (SELECT COUNT(*) FROM factura_conceptos fc2 WHERE fc2.num_venta_match = v.num_venta) as facturas_count
    FROM ventas_ml v
    LEFT JOIN envios_colecta e ON e.num_venta_ml = v.num_venta
    LEFT JOIN proveedores p ON p.id = e.proveedor_id
    {join_factura}
    WHERE {where}
    ORDER BY v.fecha_venta DESC
"""


@router.get("")
def listar(
    user: UserInfo = Depends(get_current_user),
    proveedor_id: Optional[int] = None,
    sin_factura: bool = False,
    facturada: Optional[str] = None,
    sla: Optional[str] = None,
    cruce: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    estado: Optional[str] = None,
    deposito: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
):
    # Compatibilidad: el viejo sin_factura=true equivale a facturada=false.
    if sin_factura and not facturada:
        facturada = "false"

    where, params, join_factura = _construir_filtros(
        user, proveedor_id, estado, q, facturada, sla, cruce, fecha_desde, fecha_hasta, deposito
    )

    offset = (page - 1) * limit
    sql = _SELECT_VENTAS.format(join_factura=join_factura, where=" AND ".join(where)) + " LIMIT ? OFFSET ?"
    count_params = list(params)
    params.extend([limit, offset])

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
        count_sql = f"""
            SELECT COUNT(*) as c
            FROM ventas_ml v
            LEFT JOIN envios_colecta e ON e.num_venta_ml = v.num_venta
            {join_factura}
            WHERE {' AND '.join(where)}
        """
        total = conn.execute(count_sql, count_params).fetchone()["c"]

    return {
        "page": page,
        "limit": limit,
        "total": total,
        "items": [dict(r) for r in rows],
    }


@router.get("/export.csv")
def export_csv(
    user: UserInfo = Depends(get_current_user),
    proveedor_id: Optional[int] = None,
    facturada: Optional[str] = None,
    sla: Optional[str] = None,
    cruce: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    estado: Optional[str] = None,
    deposito: Optional[str] = None,
    q: Optional[str] = None,
):
    """Exporta a CSV TODAS las filas que cumplen los filtros (sin paginar).
    Mismos filtros que el listado, para que Gaby baje exactamente lo que ve.
    """
    where, params, join_factura = _construir_filtros(
        user, proveedor_id, estado, q, facturada, sla, cruce, fecha_desde, fecha_hasta, deposito
    )
    sql = _SELECT_VENTAS.format(join_factura=join_factura, where=" AND ".join(where))

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    def _sla_txt(v):
        return "A tiempo" if v == 1 else ("Tarde" if v == 0 else "")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Num venta", "SKU", "Deposito", "Fecha venta", "Estado", "Titulo", "Unidades", "Total",
        "Num envio", "Lugar indicado", "Bodega override", "Proveedor", "SLA", "Facturada",
    ])
    for r in rows:
        w.writerow([
            r["num_venta"], r["sku"] or "", r["deposito"] or "", r["fecha_venta"] or "", r["estado"] or "",
            r["titulo"] or "", r["unidades"] if r["unidades"] is not None else "",
            r["total"] if r["total"] is not None else "",
            r["num_envio"] or "", r["lugar_indicado"] or "", r["lugar_override"] or "",
            r["proveedor_nombre"] or "", _sla_txt(r["cumplio_sla"]),
            "Si" if r["facturas_count"] > 0 else "No",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ventas_cruces.csv"},
    )


@router.get("/{num_venta}")
def detalle(num_venta: str, user: UserInfo = Depends(get_current_user)):
    with get_db() as conn:
        venta = conn.execute(
            "SELECT * FROM ventas_ml WHERE num_venta = ?", (num_venta,)
        ).fetchone()
        envio = conn.execute(
            "SELECT * FROM envios_colecta WHERE num_venta_ml = ?", (num_venta,)
        ).fetchone()
        conceptos = conn.execute(
            """SELECT fc.*, f.uuid_cfdi, f.folio, f.fecha_factura
               FROM factura_conceptos fc
               JOIN facturas f ON f.id = fc.factura_id
               WHERE fc.num_venta_match = ?""",
            (num_venta,),
        ).fetchall()
        incidencias = conn.execute(
            "SELECT * FROM incidencias WHERE num_venta = ? ORDER BY created_at DESC",
            (num_venta,),
        ).fetchall()

    return {
        "venta": dict(venta) if venta else None,
        "envio": dict(envio) if envio else None,
        "conceptos_factura": [dict(c) for c in conceptos],
        "incidencias": [dict(i) for i in incidencias],
    }
