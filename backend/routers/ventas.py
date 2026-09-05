"""Listado y consulta de ventas ML (admin) y pedidos pendientes (proveedor)."""
import csv
import io
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from database import get_db
from models import UserInfo
from routers.auth import get_current_user
from services.envio_pack import ENVIO_CUBRE_VENTA
from services.folio_factura import formatear_folio

router = APIRouter(prefix="/api/ventas", tags=["ventas"])

_MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun",
                 "jul", "ago", "sep", "oct", "nov", "dic"]


def _folios_facturas(raw):
    """Convierte el group_concat 'serie|folio|codigo_bodega,serie|folio|codigo_bodega'
    en los # de factura que ve el proveedor, separados por ', '. Vacío si no hay."""
    if not raw:
        return ""
    nums = []
    for item in raw.split(","):
        partes = item.split("|")
        if len(partes) != 3:
            continue
        serie, folio, cod = partes
        num = formatear_folio(cod, serie, folio)
        if num and num not in nums:
            nums.append(num)
    return ", ".join(nums)


def _componentes_kit(raw):
    """Convierte el group_concat 'codigo|cantidad,codigo|cantidad' en una lista de
    dicts [{codigo, cantidad}]. Lista vacía si la venta no es un kit."""
    if not raw:
        return []
    out = []
    for item in raw.split(","):
        partes = item.split("|")
        if len(partes) != 2:
            continue
        codigo, cant = partes[0].strip(), partes[1].strip()
        if not codigo:
            continue
        try:
            cant_n = int(float(cant))
        except (ValueError, TypeError):
            cant_n = 1
        out.append({"codigo": codigo, "cantidad": cant_n})
    return out


def _componentes_kit_texto(raw):
    """Formato compacto para el CSV: 'KDTL-057 x1, KDTL-058 x1'. Vacío si no es kit."""
    return ", ".join(f"{c['codigo']} x{c['cantidad']}" for c in _componentes_kit(raw))


# Cómo se le muestra a Gaby cada tipo de logística de ML. FULL y COLECTA son los
# dos que importan al negocio: en FULL la mercancía ya está en la bodega de ML (no
# la surte el proveedor, por eso esos envíos nunca traen bodega de origen), mientras
# que COLECTA es el dropshipping real que el portal mide.
_LOGISTICA_ETIQUETA = {
    "fulfillment": "FULL",
    "cross_docking": "COLECTA",
    "xd_drop_off": "Places",
    "self_service": "Flex",
}


def _logistica_txt(valor):
    """Etiqueta legible del logistic_type. Si ML manda un tipo que no conocemos, se
    devuelve tal cual (mejor mostrar el crudo que esconder el caso nuevo)."""
    if not valor:
        return ""
    return _LOGISTICA_ETIQUETA.get(valor, valor)


def _piezas_carrito(r):
    """Piezas que van en el paquete físico, para el CSV.

    En un carrito es la suma de las unidades de sus N ventas (`pack_piezas`); en una
    venta suelta, sus propias unidades — el "paquete" es ella misma.

    🔴 Devuelve celda VACÍA, nunca 0, cuando no se conocen las unidades (32 ventas en
    prod las traen NULL): un "0 piezas" se leería como un paquete vacío en vez de como
    un dato ausente. Mismo criterio que `total_neto` y que la columna Unidades.
    """
    piezas = r["pack_piezas"] if r["pack_piezas"] is not None else r["unidades"]
    return piezas if piezas is not None else ""


def _total_sin_iva(total_neto):
    """Importe sin IVA para el CSV, con dos decimales.

    ``total_neto`` ya es el total que ML deposita; este valor es únicamente una
    presentación derivada para la exportación. Decimal evita que la precisión
    binaria de float cambie el redondeo de un monto monetario.
    """
    if total_neto is None:
        return ""
    return str(
        (Decimal(str(total_neto)) / Decimal("1.16")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )


def _fecha_corta(iso):
    """Formato corto legible para el CSV ('2026-05-13 23:43' -> '13 may 2026'),
    igual que la tabla de Ventas en el frontend."""
    if not iso:
        return ""
    s = str(iso)
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            anio, mes, dia = int(s[0:4]), int(s[5:7]), int(s[8:10])
            return f"{dia} {_MESES_CORTOS[mes - 1]} {anio}"
        except (ValueError, IndexError):
            pass
    return s


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
    logistica: Optional[str] = None,
    albaran: Optional[str] = None,
):
    """Arma la cláusula WHERE + JOINs compartida por el listado y el export.

    Devuelve (where_list, params, join_factura). El JOIN a factura_conceptos se
    agrega solo si algún filtro de facturación lo necesita.

    `deposito` controla la bodega de origen (col 'Depósito' del reporte ML):
      - None / "todos" (default): sin filtro, muestra TODO incluida MATRIZ.
      - "proveedores": oculta MATRIZ (solo dropshipping).
      - "matriz": solo MATRIZ.

    `logistica` filtra por cómo despachó ML el envío (envios_colecta.logistic_type):
      - "full": fulfillment (ML surte desde su bodega; NO es dropshipping).
      - "colecta": cross_docking (el proveedor surte; el flujo que mide el portal).
      - "otros": cualquier otro tipo (Places/Flex) con envío existente.

    `estado` filtra por el estado de la venta (ventas_ml.estado, poblado por el sync
    vía ESTADO_MAP: 'Entregado', 'Pagado', 'Cancelada'...). Pedido de Gaby (2026-08-11):
    las canceladas le estorbaban en la vista diaria, pero NO quiso borrarlas.
      - None / "sin_canceladas" (DEFAULT): oculta las canceladas.
      - "solo_canceladas": únicamente las canceladas (para revisarlas juntas).
      - "todas": sin filtro.
      - cualquier otro valor: igualdad exacta (compatibilidad con ?estado=Entregado).

    `albaran` acepta "con_albaran" y "sin_albaran". Vacío o desconocido no filtra.
    """
    if user.rol == "proveedor":
        proveedor_id = user.proveedor_id

    where = ["1=1"]
    params: list = []
    join_factura = ""

    if proveedor_id:
        where.append("e.proveedor_id = ?")
        params.append(proveedor_id)

    # Estado de la venta. El DEFAULT (estado vacío) oculta las canceladas: Gaby las veía
    # mezcladas en su trabajo diario y pidió no tenerlas por default, pero explícitamente
    # NO quiso quitarlas del portal (una cancelada CON factura del proveedor es una
    # incidencia que hay que poder ver: en prod hay 4 así).
    #
    # 🔴 El guard `IS NULL OR` NO es cosmético: en SQL `NULL != 'Cancelada'` evalúa a NULL,
    # no a true, así que sin él las 279 ventas con estado NULL (todas de jun-2026, entradas
    # por el Excel legacy que el sync por API nunca volvió a tocar) DESAPARECERÍAN en
    # silencio de la vista por defecto. Mismo criterio que NO_ES_FULL en metricas.py.
    # Los 3 modos se comparan en minúscula: un "Todas" con mayúscula caería a la rama de
    # compatibilidad y filtraría por un estado que no existe -> tabla VACÍA en vez de
    # "todas", que es un fallo silencioso (parece que no hay ventas, no que el filtro falló).
    modo_estado = (estado or "").strip().lower()
    if not modo_estado or modo_estado == "sin_canceladas":
        where.append("(v.estado IS NULL OR v.estado != 'Cancelada')")
    elif modo_estado == "solo_canceladas":
        where.append("v.estado = 'Cancelada'")
    elif modo_estado == "todas":
        pass  # sin filtro
    else:
        # Compatibilidad: ?estado=Entregado sigue filtrando por igualdad exacta.
        where.append("v.estado = ?")
        params.append(estado)

    if q:
        # v.pack_id es el número que ML le muestra a Gaby en su portal, y es el que
        # ella usa a diario para buscar (casi nunca el num_venta interno). Va indexado
        # (idx_ventas_pack_id) porque esta consulta corre en cada tecleo sobre ~27k filas.
        where.append("(v.num_venta LIKE ? OR v.pack_id LIKE ? OR v.sku LIKE ? OR v.titulo LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like, like])

    # Facturada / sin factura. Se evalúa con un subquery EXISTS para no duplicar
    # filas cuando una venta tiene varios conceptos facturados.
    existe_factura = "EXISTS (SELECT 1 FROM factura_conceptos fc WHERE fc.num_venta_match = v.num_venta)"
    if facturada == "true":
        where.append(existe_factura)
    elif facturada == "false":
        where.append("NOT " + existe_factura)

    modo_albaran = (albaran or "").strip().lower()
    if modo_albaran == "con_albaran":
        where.append("v.albaran IS NOT NULL AND TRIM(v.albaran) != ''")
    elif modo_albaran == "sin_albaran":
        where.append("(v.albaran IS NULL OR TRIM(v.albaran) = '')")

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

    # Bodega de origen (col 'Depósito'). El default MOSTRABA solo dropshipping y
    # ocultaba MATRIZ; el cliente pidió (2026-08-03) ver también las de MATRIZ, así
    # que el default pasó a "todos". El selector conserva "Solo proveedores" para
    # recuperar la vista limpia cuando Gaby la quiera.
    if deposito == "matriz":
        where.append("v.deposito = 'MATRIZ'")
    elif deposito == "proveedores":
        where.append("(v.deposito IS NULL OR v.deposito != 'MATRIZ')")
    else:  # None / "todos": comportamiento por defecto
        pass  # sin filtro: muestra todo, incluida MATRIZ

    # Tipo de logística del envío. Separa FULL (ML surte de su propia bodega, no es
    # dropshipping y por eso nunca trae bodega de proveedor) de COLECTA (el flujo que
    # el portal realmente mide). Indexado por idx_envios_logistic_type.
    if logistica == "full":
        where.append("e.logistic_type = 'fulfillment'")
    elif logistica == "colecta":
        where.append("e.logistic_type = 'cross_docking'")
    elif logistica == "otros":
        where.append(
            "e.num_envio IS NOT NULL AND e.logistic_type IS NOT NULL "
            "AND e.logistic_type NOT IN ('fulfillment', 'cross_docking')"
        )

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
    SELECT v.num_venta, v.pack_id, v.sku, v.deposito, v.fecha_venta, v.estado, v.titulo, v.unidades,
           v.total, v.total_neto, v.albaran, v.comprador_estado, v.forma_entrega,
           e.num_envio, e.lugar_indicado, e.lugar_real, e.lugar_override, e.cumplio_sla,
           e.logistic_type,
           e.proveedor_id, p.nombre as proveedor_nombre,
           -- Fija cuál envío gana cuando la venta resuelve a varios (ver el GROUP BY
           -- de abajo): el que trae proveedor. No se devuelve a la UI, sólo dirige la
           -- elección de SQLite.
           MAX(CASE WHEN e.proveedor_id IS NOT NULL THEN 1 ELSE 0 END) as _envio_rank,
           (SELECT COUNT(*) FROM factura_conceptos fc2 WHERE fc2.num_venta_match = v.num_venta) as facturas_count,
           -- Cuántas ventas comparten el paquete (carrito). Es 1 en una venta normal.
           -- Sirve para que la UI marque "Carrito 2 de 2": las N ventas de un pack muestran
           -- el MISMO número (el pack_id, que es el que ML enseña) y ahora también el mismo
           -- proveedor/SLA/logística, así que sin la marca se leen como filas duplicadas.
           -- Es justo la confusión que Gaby reportó ("sólo viene asociado a un sku").
           -- MAX(1, ...) para que una venta sin pack diga 1 (es un paquete de un producto),
           -- no 0: el subquery no cuenta filas cuando pack_id es NULL y un "0 productos"
           -- en el CSV se leería como un dato roto.
           MAX(1, (SELECT COUNT(*) FROM ventas_ml v2
                   WHERE v.pack_id IS NOT NULL AND v2.pack_id = v.pack_id)) as pack_ventas,
           -- Cuántas PIEZAS van en el paquete físico = la suma de las unidades de las N
           -- ventas del carrito. Es distinto de pack_ventas: el carrito de KIMS que reportó
           -- Gaby trae 2 productos (pack_ventas=2) pero 3 piezas (2 birlos + 1 tuerca).
           -- En una venta sin pack es simplemente sus propias unidades.
           --
           -- 🔴 Se deja NULL cuando falta el dato, NUNCA 0 (mismo criterio que total_neto):
           -- hay 32 ventas con `unidades` NULL en prod, y un "0 piezas" en el CSV se leería
           -- como un paquete vacío en vez de como un dato ausente. Por eso NO lleva
           -- IFNULL/COALESCE: SUM() sobre NULL da NULL y así debe salir (en el CSV se
           -- convierte en celda vacía, igual que Unidades).
           (SELECT SUM(v4.unidades) FROM ventas_ml v4
            WHERE v4.pack_id = v.pack_id AND v.pack_id IS NOT NULL) as pack_piezas,
           -- Facturas cruzadas a esta venta: cada una como 'serie|folio|codigo_bodega',
           -- separadas por coma (group_concat DISTINCT usa coma fija). DISTINCT porque una
           -- factura puede tener varios conceptos cruzando a la misma venta. Se formatea por
           -- proveedor en Python (formatear_folio). serie/folio no contienen comas (CFDI).
           (SELECT group_concat(DISTINCT
                       IFNULL(f3.serie,'') || '|' || IFNULL(f3.folio,'') || '|' || IFNULL(p3.codigo_bodega,''))
            FROM factura_conceptos fc3
            JOIN facturas f3 ON f3.id = fc3.factura_id
            JOIN proveedores p3 ON p3.id = f3.proveedor_id
            WHERE fc3.num_venta_match = v.num_venta) as facturas_raw,
           -- Componentes del kit, si esta venta es un kit (su SKU está en kit_componentes).
           -- Cada componente como 'codigo|cantidad', separados por coma. Vacío si no es kit.
           (SELECT group_concat(kc.componente_codigo || '|' || kc.cantidad)
            FROM kit_componentes kc
            WHERE kc.kit_sku = UPPER(TRIM(v.sku))) as kit_componentes_raw
    FROM ventas_ml v
    LEFT JOIN envios_colecta e ON {envio_cubre_venta}
    LEFT JOIN proveedores p ON p.id = e.proveedor_id
    {join_factura}
    WHERE {where}
    -- Una venta puede resolver a MÁS DE UN envío: ya pasaba antes de este cambio (en
    -- prod hay 2 ventas con 2 envíos, reexpediciones del mismo paquete). Sin agrupar,
    -- la venta saldría en 2 filas y Gaby las leería como ventas duplicadas.
    --
    -- El MAX(...) no es decorativo: en un GROUP BY "bare" SQLite elige una fila
    -- ARBITRARIA para las columnas no agregadas, pero documenta que un MIN/MAX en la
    -- lista de selección FIJA de qué fila salen las demás. Así preferimos de forma
    -- determinista el envío que SÍ trae proveedor, que es el útil para Gaby (un envío
    -- sin bodega la deja sin SLA ni cruce de factura). Sin esto, una venta con 2 envíos
    -- podría mostrar el vacío y parecer "sin asignar" teniendo bodega buena al lado.
    GROUP BY v.num_venta
    ORDER BY v.fecha_venta DESC, v.num_venta DESC
""".replace("{envio_cubre_venta}", ENVIO_CUBRE_VENTA)
# La condición del envío se sustituye UNA vez, aquí: no es un parámetro que varíe por
# llamada y dejarla en el .format() de cada caller obligaba a pasarla en los 3 sitios
# (uno se olvidó y el test se cayó con KeyError). Los {join_factura}/{where} SÍ siguen
# resolviéndose por llamada.


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
    logistica: Optional[str] = None,
    albaran: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
):
    # Compatibilidad: el viejo sin_factura=true equivale a facturada=false.
    if sin_factura and not facturada:
        facturada = "false"

    where, params, join_factura = _construir_filtros(
        user, proveedor_id, estado, q, facturada, sla, cruce, fecha_desde, fecha_hasta,
        deposito, logistica, albaran
    )

    offset = (page - 1) * limit
    sql = _SELECT_VENTAS.format(join_factura=join_factura, where=" AND ".join(where)) + " LIMIT ? OFFSET ?"
    count_params = list(params)
    params.extend([limit, offset])

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
        # COUNT(DISTINCT v.num_venta), no COUNT(*): el JOIN a envíos puede producir
        # varias filas por venta (una venta con 2 envíos), y un COUNT(*) devolvería un
        # total MAYOR que las filas que el listado agrupa -> Gaby vería una última
        # página vacía y un contador que no cuadra con lo que tiene enfrente.
        count_sql = f"""
            SELECT COUNT(DISTINCT v.num_venta) as c
            FROM ventas_ml v
            LEFT JOIN envios_colecta e ON {ENVIO_CUBRE_VENTA}
            {join_factura}
            WHERE {' AND '.join(where)}
        """
        total = conn.execute(count_sql, count_params).fetchone()["c"]

    items = []
    for r in rows:
        d = dict(r)
        d["facturas_num"] = _folios_facturas(d.pop("facturas_raw", None))
        d["kit_componentes"] = _componentes_kit(d.pop("kit_componentes_raw", None))
        items.append(d)

    return {
        "page": page,
        "limit": limit,
        "total": total,
        "items": items,
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
    logistica: Optional[str] = None,
    albaran: Optional[str] = None,
    q: Optional[str] = None,
):
    """Exporta a CSV TODAS las filas que cumplen los filtros (sin paginar).
    Mismos filtros que el listado, para que Gaby baje exactamente lo que ve.
    """
    where, params, join_factura = _construir_filtros(
        user, proveedor_id, estado, q, facturada, sla, cruce, fecha_desde, fecha_hasta,
        deposito, logistica, albaran
    )
    sql = _SELECT_VENTAS.format(join_factura=join_factura, where=" AND ".join(where))

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    def _sla_txt(v):
        return "A tiempo" if v == 1 else ("Tarde" if v == 0 else "")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        # "Num venta" = el número tal como lo ve Gaby en el portal de ML (pack_id si
        # existe, si no el order.id). "Num venta interno" se conserva porque es la
        # llave con la que cruzan factura, albarán y envío.
        "Num venta", "Num venta interno",
        # "Ingresos por productos" es el precio del producto (total_amount de ML) — es
        # el nombre que ML le da en su reporte. "Total (MXN)" es el neto que RELUVSA
        # recibe ya descontados cargos, envíos e impuestos (pedido de Gaby 2026-08-10).
        "Albaran", "SKU", "Deposito", "Fecha venta", "Estado", "Titulo", "Unidades",
        "Ingresos por productos (MXN)", "Total (MXN)", "Total sin IVA (MXN)",
        "Num envio", "Logistica", "Lugar indicado", "Bodega override", "Proveedor", "SLA",
        "Facturada", "Num factura",
        "Componentes kit",
        # Cuántos productos DISTINTOS trae el paquete. En un carrito, las N filas comparten
        # el "Num venta" (el pack_id, que es el que ML enseña) y el envío; esta columna
        # explica por qué se repite el número.
        #
        # ⚠️ NO son unidades: las unidades van por fila, en su propia columna, y difieren
        # entre hermanas. Vivía en la posición 3, pegada al "Num venta", y Gaby la leyó como
        # la cantidad (2026-08-21: reportó "2 y 2" en un carrito donde las unidades reales
        # eran 2 y 1 — el dato siempre estuvo bien, sólo se leyó la columna de al lado).
        # Va al final y dice "carrito" —el mismo término del badge de la UI— justo para que
        # no vuelva a confundirse con una cantidad de piezas.
        "Productos en el carrito",
        # Las PIEZAS que van en el paquete físico (pedido de Mario 2026-08-21). Es la suma de
        # las unidades de las N ventas del carrito, y por eso difiere de la columna anterior:
        # el pack de KIMS trae 2 productos pero 3 piezas (2 birlos + 1 tuerca). Va pegada a
        # "Productos en el carrito" —las dos describen el paquete, no la fila— y ambas lejos
        # del "Num venta", que es lo que causaba la confusión original.
        "Piezas del carrito",
    ])
    for r in rows:
        w.writerow([
            r["pack_id"] or r["num_venta"], r["num_venta"],
            r["albaran"] or "", r["sku"] or "", r["deposito"] or "", _fecha_corta(r["fecha_venta"]),
            r["estado"] or "",
            r["titulo"] or "", r["unidades"] if r["unidades"] is not None else "",
            r["total"] if r["total"] is not None else "",
            r["total_neto"] if r["total_neto"] is not None else "",
            _total_sin_iva(r["total_neto"]),
            r["num_envio"] or "", _logistica_txt(r["logistic_type"]),
            r["lugar_indicado"] or "", r["lugar_override"] or "",
            r["proveedor_nombre"] or "", _sla_txt(r["cumplio_sla"]),
            "Si" if r["facturas_count"] > 0 else "No",
            _folios_facturas(r["facturas_raw"]),
            _componentes_kit_texto(r["kit_componentes_raw"]),
            # Van al final, en el mismo orden que el encabezado (ver los comentarios de arriba).
            r["pack_ventas"] if r["pack_ventas"] else 1,
            # Sin pack, las piezas del "paquete" son las de la propia venta. Celda VACÍA si no
            # se conocen las unidades (32 ventas en prod): un 0 se leería como paquete vacío.
            _piezas_carrito(r),
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
        # El envío puede estar cruzado directo a esta venta o cubrirla por pertenecer al
        # mismo paquete (carrito). Se prefiere el que trae proveedor y, entre iguales, el
        # cruce directo sobre el heredado del pack, para que el detalle no muestre un
        # envío sin bodega teniendo al lado uno resuelto.
        envio = conn.execute(
            f"""SELECT e.* FROM envios_colecta e
                JOIN ventas_ml v ON v.num_venta = ?
                WHERE {ENVIO_CUBRE_VENTA}
                ORDER BY (e.proveedor_id IS NOT NULL) DESC,
                         (e.num_venta_ml = v.num_venta) DESC
                LIMIT 1""",
            (num_venta,),
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
