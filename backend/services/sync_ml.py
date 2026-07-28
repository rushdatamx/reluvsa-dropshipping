"""
Job de sincronización con la API de Mercado Libre (sustituye los Excels de
Ventas ML y Detalle de colecta que ML retiró).

Todo el tráfico de red sale por services.ml_client (SOLO GET — app de lectura).
Este módulo solo ORQUESTA: pide órdenes/envíos y hace upserts en las MISMAS
tablas y con la MISMA semántica que los parsers de Excel:
- ventas_ml por num_venta (no toca `albaran`; no pisa `comprador` con NULL).
- envios_colecta por num_envio, RESPETANDO lugar_override (regla de Gaby).
- El cruce venta↔envío por API es DIRECTO (order.shipping.id) → confianza 1.0;
  resolver_cruce_ventas queda para los datos legacy del Excel.
- Al final: recruzar_conceptos_sin_match (cruce retroactivo de facturas).

Estrategia (skill mercadolibre-api §9):
- backfill: 12 meses de /orders/search por VENTANAS de 15 días (date_created),
  de la más antigua a la más reciente; el offset interno por ventana queda chico
  (no dependemos del tope de offset no documentado de ML) y cursor_fecha avanza
  monótono → checkpoint reanudable (re-procesar una ventana parcial es inocuo
  porque todo es upsert).
- incremental: order.date_last_updated.from = ultima_sync - 5 min de solape.
  ultima_sync solo avanza si la corrida termina 'completado' (sin huecos).

Corre en un thread daemon propio (el backfill puede durar mucho más que un
request). Las llamadas a la API se hacen SIN conexión de BD abierta; los upserts
van en una transacción corta por página (WAL: los lectores no se bloquean).
"""
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from database import get_db
from services import ml_client
from services.matcher import recruzar_conceptos_sin_match
from services.parser_colecta import LUGAR_A_BODEGA, _resolver_proveedor, resolver_cruce_ventas

VENTANA_DIAS = 15
BACKFILL_DIAS = 365          # la API solo da 12 meses de órdenes hacia atrás
MARGEN_SOLAPE_MIN = 5
LIMIT = 50
HEARTBEAT_MUERTO_MIN = 10
OFFSET_TOPE = 10000          # tope de seguridad por ventana (no debería alcanzarse)
API_LOG_RETENCION_DIAS = 30

# Zona horaria de México (sin DST desde 2022): las fechas del Excel eran hora
# local naive; convertimos las fechas ISO de la API a lo mismo para que el cruce
# fecha+título legacy (±5 min) siga funcionando con datos mixtos.
TZ_MX = timezone(timedelta(hours=-6))

ESTADO_MAP = {
    "paid": "Pagado",
    "confirmed": "Confirmada",
    "payment_required": "Pago pendiente",
    "payment_in_process": "Pago en proceso",
    "partially_paid": "Pago parcial",
    "cancelled": "Cancelada",
    "invalid": "Inválida",
}

_sync_lock = threading.Lock()


class SyncEnCurso(Exception):
    """Ya hay una sincronización corriendo (heartbeat fresco)."""

    def __init__(self, run: dict):
        self.run = run
        super().__init__("Ya hay una sincronización en curso")


# ---------------------------------------------------------------------------
# Config clave-valor
# ---------------------------------------------------------------------------

def get_config(conn, clave: str) -> Optional[str]:
    row = conn.execute("SELECT valor FROM ml_config WHERE clave = ?", (clave,)).fetchone()
    return row["valor"] if row else None


def set_config(conn, clave: str, valor: Optional[str]) -> None:
    conn.execute(
        """INSERT INTO ml_config (clave, valor, actualizado_en) VALUES (?, ?, ?)
           ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor, actualizado_en=excluded.actualizado_en""",
        (clave, valor, datetime.utcnow().isoformat(timespec="seconds")),
    )


# ---------------------------------------------------------------------------
# Bootstrap post-conexión: identidad del seller + catálogo de depósitos
# ---------------------------------------------------------------------------

def _bootstrap_post_conexion() -> dict:
    """GET /users/me (identidad + tags multi-origen) y catálogo de depósitos.
    Lo llama el callback OAuth y el inicio de cada sync (idempotente)."""
    me = ml_client.get("/users/me")
    seller_id = str(me["id"])
    nickname = me.get("nickname")
    tags = me.get("tags") or []

    with get_db() as conn:
        set_config(conn, "seller_id", seller_id)
        set_config(conn, "nickname", nickname)
        set_config(conn, "tags_json", json.dumps(tags))

    stores_count = _sincronizar_stores(seller_id)
    return {
        "seller_id": seller_id,
        "nickname": nickname,
        "multiorigen": {
            "warehouse_management": "warehouse_management" in tags,
            "multiwarehouse": "multiwarehouse" in tags,
        },
        "stores": stores_count,
    }


def _sincronizar_stores(seller_id: str) -> int:
    """Catálogo de depósitos → ml_stores. description = lo que el Excel mostraba
    en 'Depósito' (MATRIZ/KIM/CAUPLAS/...); codigo_bodega vía LUGAR_A_BODEGA."""
    try:
        data = ml_client.get(f"/users/{seller_id}/stores/search", params={"tags": "stock_location"})
    except ml_client.MLError:
        # Cuenta sin multi-origen o permiso pendiente: no es fatal, el lugar
        # saldrá del shipment.origin en el sync.
        return 0

    resultados = data.get("results") or []
    ahora = datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as conn:
        for st in resultados:
            desc = (st.get("description") or "").strip()
            codigo = LUGAR_A_BODEGA.get(desc.upper())
            conn.execute(
                """INSERT INTO ml_stores (store_id, description, network_node_id, codigo_bodega, raw_json, actualizado_en)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(store_id) DO UPDATE SET
                       description=excluded.description, network_node_id=excluded.network_node_id,
                       codigo_bodega=excluded.codigo_bodega, raw_json=excluded.raw_json,
                       actualizado_en=excluded.actualizado_en""",
                (
                    str(st.get("id")),
                    desc or None,
                    str(st.get("network_node_id")) if st.get("network_node_id") is not None else None,
                    codigo,
                    json.dumps(st),
                    ahora,
                ),
            )
    return len(resultados)


def _cargar_stores(conn) -> dict:
    """{'por_store': {store_id: description}, 'por_node': {node_id: description}}"""
    por_store, por_node = {}, {}
    for r in conn.execute("SELECT store_id, description, network_node_id FROM ml_stores").fetchall():
        if r["store_id"]:
            por_store[r["store_id"]] = r["description"]
        if r["network_node_id"]:
            por_node[r["network_node_id"]] = r["description"]
    return {"por_store": por_store, "por_node": por_node}


# ---------------------------------------------------------------------------
# Arranque / control de corridas
# ---------------------------------------------------------------------------

def iniciar_sync(tipo: str = "incremental") -> dict:
    """Valida precondiciones, registra la corrida y la lanza en un thread daemon.
    Lanza SyncEnCurso si ya hay una corriendo con heartbeat fresco."""
    if tipo not in ("incremental", "backfill"):
        raise ValueError(f"tipo de sync inválido: {tipo}")
    if not ml_client.hay_conexion():
        raise ml_client.MLNoConectado("No hay cuenta de ML conectada")

    ahora = datetime.utcnow()
    with get_db() as conn:
        viva = conn.execute(
            "SELECT * FROM ml_sync_runs WHERE estado='en_curso' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if viva:
            try:
                heartbeat = datetime.fromisoformat(viva["actualizado_en"])
            except (ValueError, TypeError):
                heartbeat = None
            if heartbeat and (ahora - heartbeat) < timedelta(minutes=HEARTBEAT_MUERTO_MIN):
                raise SyncEnCurso(dict(viva))
            # Corrida muerta (deploy/crash a la mitad): se marca y se permite otra.
            conn.execute(
                "UPDATE ml_sync_runs SET estado='abortado', terminado_en=? WHERE id=?",
                (ahora.isoformat(timespec="seconds"), viva["id"]),
            )

        # Sin ultima_sync (nunca se ha sincronizado) el incremental no tiene punto
        # de partida: degrada a backfill.
        if tipo == "incremental" and not get_config(conn, "ultima_sync"):
            tipo = "backfill"

    if not _sync_lock.acquire(blocking=False):
        with get_db() as conn:
            viva = conn.execute(
                "SELECT * FROM ml_sync_runs WHERE estado='en_curso' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        raise SyncEnCurso(dict(viva) if viva else {})

    try:
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO ml_sync_runs (tipo, estado, iniciado_en, actualizado_en)
                   VALUES (?, 'en_curso', ?, ?)""",
                (tipo, ahora.isoformat(timespec="seconds"), ahora.isoformat(timespec="seconds")),
            )
            run_id = cur.lastrowid
    except Exception:
        _sync_lock.release()
        raise

    hilo = threading.Thread(target=_ejecutar_sync, args=(run_id, tipo), daemon=True)
    hilo.start()
    return {"run_id": run_id, "tipo": tipo}


def _ejecutar_sync(run_id: int, tipo: str) -> None:
    """Cuerpo del thread. Libera el lock SIEMPRE y nunca deja la run en_curso."""
    try:
        boot = _bootstrap_post_conexion()
        seller_id = boot["seller_id"]
        inicio = datetime.utcnow()
        stats = {"ordenes_vistas": 0, "ventas_upsert": 0, "envios_upsert": 0,
                 "errores": 0, "detalle_errores": []}

        if tipo == "backfill":
            _run_backfill(run_id, seller_id, inicio, stats)
        else:
            _run_incremental(run_id, seller_id, inicio, stats)
    except Exception as e:
        with get_db() as conn:
            conn.execute(
                "UPDATE ml_sync_runs SET estado='error', detalle=?, terminado_en=?, actualizado_en=? WHERE id=?",
                (f"{type(e).__name__}: {e}",
                 datetime.utcnow().isoformat(timespec="seconds"),
                 datetime.utcnow().isoformat(timespec="seconds"),
                 run_id),
            )
    finally:
        _sync_lock.release()


def _run_backfill(run_id: int, seller_id: str, inicio: datetime, stats: dict) -> None:
    desde = inicio - timedelta(days=BACKFILL_DIAS)
    with get_db() as conn:
        # Reanudar desde el checkpoint de un backfill previo que no terminó.
        prev = conn.execute(
            """SELECT cursor_fecha FROM ml_sync_runs
               WHERE tipo='backfill' AND estado IN ('abortado','error') AND cursor_fecha IS NOT NULL
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if prev and prev["cursor_fecha"]:
            try:
                desde = max(desde, datetime.fromisoformat(prev["cursor_fecha"]))
            except (ValueError, TypeError):
                pass
        conn.execute(
            "UPDATE ml_sync_runs SET ventana_desde=?, ventana_hasta=? WHERE id=?",
            (desde.isoformat(timespec="seconds"), inicio.isoformat(timespec="seconds"), run_id),
        )

    w0 = desde
    while w0 < inicio:
        w1 = min(w0 + timedelta(days=VENTANA_DIAS), inicio)
        _procesar_filtro(run_id, seller_id, {
            "order.date_created.from": _iso_ml(w0),
            "order.date_created.to": _iso_ml(w1),
        }, stats)
        with get_db() as conn:
            conn.execute(
                "UPDATE ml_sync_runs SET cursor_fecha=?, actualizado_en=? WHERE id=?",
                (w1.isoformat(timespec="seconds"),
                 datetime.utcnow().isoformat(timespec="seconds"), run_id),
            )
        w0 = w1

    _finalizar(run_id, inicio, stats)


def _run_incremental(run_id: int, seller_id: str, inicio: datetime, stats: dict) -> None:
    with get_db() as conn:
        ultima = get_config(conn, "ultima_sync")
    desde = datetime.fromisoformat(ultima) - timedelta(minutes=MARGEN_SOLAPE_MIN)
    with get_db() as conn:
        conn.execute(
            "UPDATE ml_sync_runs SET ventana_desde=?, ventana_hasta=? WHERE id=?",
            (desde.isoformat(timespec="seconds"), inicio.isoformat(timespec="seconds"), run_id),
        )
    _procesar_filtro(run_id, seller_id, {
        "order.date_last_updated.from": _iso_ml(desde),
    }, stats)
    _finalizar(run_id, inicio, stats)


# ---------------------------------------------------------------------------
# Núcleo: traer órdenes por filtro y upsertearlas
# ---------------------------------------------------------------------------

def _iso_ml(dt: datetime) -> str:
    """Formato de fecha que aceptan los filtros de /orders/search."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")


def _procesar_filtro(run_id: int, seller_id: str, filtro: dict, stats: dict) -> None:
    """Pagina /orders/search con un filtro dado y upsertea cada orden.
    Las llamadas a la API van SIN conexión de BD abierta; los upserts de cada
    página se hacen juntos en una transacción corta."""
    offset = 0
    while True:
        params = {"seller": seller_id, "limit": LIMIT, "offset": offset, "sort": "date_asc"}
        params.update(filtro)
        page = ml_client.get("/orders/search", params=params)
        results = page.get("results") or []

        # 1) Traer de la API todo lo de esta página (sin BD abierta).
        paquete = []  # (order, ship, sla)
        for order in results:
            stats["ordenes_vistas"] += 1
            try:
                paquete.append((order,) + _traer_envio(order))
            except Exception as e:
                _anotar_error(stats, f"orden {order.get('id')}: {type(e).__name__}: {e}")

        # 2) Upsert de la página completa en una transacción corta.
        with get_db() as conn:
            stores = _cargar_stores(conn)
            for order, ship, sla in paquete:
                try:
                    _procesar_orden(conn, order, ship, sla, stores, stats)
                except Exception as e:
                    _anotar_error(stats, f"upsert orden {order.get('id')}: {type(e).__name__}: {e}")
            _tocar_run(conn, run_id, stats)

        total = (page.get("paging") or {}).get("total") or 0
        offset += LIMIT
        if not results or offset >= total:
            break
        if offset > OFFSET_TOPE:
            _anotar_error(stats, f"ventana con paginación excesiva (offset>{OFFSET_TOPE}); se truncó: {filtro}")
            break


def _traer_envio(order: dict) -> Tuple[Optional[dict], Optional[dict]]:
    """(shipment, sla) de una orden — o (None, None) si no tiene envío."""
    ship_id = (order.get("shipping") or {}).get("id")
    if not ship_id:
        return None, None
    ship = ml_client.get(f"/orders/{order['id']}/shipments")
    sla = ml_client.get_opcional(f"/shipments/{ship_id}/sla")
    return ship, sla


def _anotar_error(stats: dict, msg: str) -> None:
    stats["errores"] += 1
    stats["detalle_errores"] = (stats.get("detalle_errores") or [])[-4:] + [msg]


def _tocar_run(conn, run_id: int, stats: dict) -> None:
    """Heartbeat + contadores (se toca en cada página)."""
    conn.execute(
        """UPDATE ml_sync_runs SET ordenes_vistas=?, ventas_upsert=?, envios_upsert=?,
                                   errores=?, detalle=?, actualizado_en=?
           WHERE id=?""",
        (stats["ordenes_vistas"], stats["ventas_upsert"], stats["envios_upsert"],
         stats["errores"], json.dumps(stats.get("detalle_errores") or []),
         datetime.utcnow().isoformat(timespec="seconds"), run_id),
    )


# ---------------------------------------------------------------------------
# Mapeo y upserts (misma semántica que los parsers de Excel)
# ---------------------------------------------------------------------------

def _fecha_api(iso: Optional[str]) -> Optional[datetime]:
    """ISO de la API (con offset) → datetime naive en hora de México, igual que
    guardaban los parsers de Excel (para que el cruce legacy ±5 min siga vivo)."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(TZ_MX).replace(tzinfo=None)
    return dt


def _mapear_estado(order: dict) -> Optional[str]:
    tags = order.get("tags") or []
    if "delivered" in tags:
        return "Entregado"
    status = order.get("status")
    return ESTADO_MAP.get(status, status)


def _procesar_orden(conn, order: dict, ship: Optional[dict], sla: Optional[dict],
                    stores: dict, stats: dict) -> None:
    receiver_name = None
    if ship:
        receiver_name = ((ship.get("receiver_address") or {}).get("receiver_name")
                         or (ship.get("destination") or {}).get("receiver_name"))

    _upsert_venta_api(conn, order, stores, receiver_name)
    stats["ventas_upsert"] += 1

    if ship and ship.get("id"):
        _upsert_envio_api(conn, order, ship, sla, stores)
        stats["envios_upsert"] += 1


def _deposito_de_orden(order: dict, stores: dict) -> Optional[str]:
    """Columna 'Depósito' del Excel = store de origen del stock (multi-origen)."""
    for item in order.get("order_items") or []:
        store_id = ((item.get("stock") or {}).get("store_id"))
        if store_id is not None and str(store_id) in stores["por_store"]:
            return stores["por_store"][str(store_id)]
    return None


def _upsert_venta_api(conn, order: dict, stores: dict, receiver_name: Optional[str]) -> None:
    """Upsert en ventas_ml con la misma semántica que parser_ventas_ml: UPDATE si
    existe / INSERT si no. NO toca `albaran` (viene del Excel de Gaby) y NO pisa
    `comprador` con NULL (COALESCE: la API enmascara datos tarde o temprano)."""
    num_venta = str(order["id"])
    items = order.get("order_items") or []
    primero = (items[0].get("item") or {}) if items else {}
    sku = primero.get("seller_sku") or primero.get("seller_custom_field")
    titulo = primero.get("title")
    unidades = sum(int(i.get("quantity") or 0) for i in items) or None
    fecha = _fecha_api(order.get("date_created"))
    estado = _mapear_estado(order)
    total = order.get("total_amount")
    deposito = _deposito_de_orden(order, stores)

    existe = conn.execute(
        "SELECT num_venta FROM ventas_ml WHERE num_venta = ?", (num_venta,)
    ).fetchone()
    if existe:
        conn.execute(
            """UPDATE ventas_ml SET sku=?, deposito=COALESCE(?, deposito), fecha_venta=?, estado=?,
                                    titulo=?, unidades=?, total=?, comprador=COALESCE(?, comprador)
               WHERE num_venta=?""",
            (sku, deposito, fecha, estado, titulo, unidades, total, receiver_name, num_venta),
        )
    else:
        conn.execute(
            """INSERT INTO ventas_ml (num_venta, sku, deposito, fecha_venta, estado, titulo,
                                      unidades, total, comprador)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (num_venta, sku, deposito, fecha, estado, titulo, unidades, total, receiver_name),
        )


def _resolver_lugar(order: dict, ship: dict, stores: dict) -> Optional[str]:
    """El 'Lugar indicado' (columna J, la regla de Gaby) desde el origen del envío.
    Prioridad: node_id del origin (estructurado) → description del store; fallback:
    store del stock de la orden."""
    origin = ship.get("origin") or {}
    node_id = ((origin.get("node") or {}).get("node_id")) or origin.get("node_id")
    if node_id is not None and str(node_id) in stores["por_node"]:
        return stores["por_node"][str(node_id)]
    return _deposito_de_orden(order, stores)


def _upsert_envio_api(conn, order: dict, ship: dict, sla: Optional[dict], stores: dict) -> None:
    """Upsert en envios_colecta calcado de parser_colecta: respeta lugar_override
    (el override de Gaby manda sobre lo que diga la API) y no pisa cumplio_sla
    con NULL. El cruce a la venta es DIRECTO por ID (confianza 1.0)."""
    num_envio = str(ship["id"])
    num_venta = str(order["id"])
    lugar_indicado = _resolver_lugar(order, ship, stores)
    titulo = None
    items = order.get("order_items") or []
    if items:
        titulo = (items[0].get("item") or {}).get("title")
    fecha = _fecha_api(order.get("date_created"))

    sla_status = (sla or {}).get("status")
    if sla_status in ("on_time", "early"):
        cumplio_sla = 1
    elif sla_status == "delayed":
        cumplio_sla = 0
    else:
        cumplio_sla = None  # insuficient_info / sin SLA → conservar lo existente

    proveedor_id = _resolver_proveedor(conn, lugar_indicado)

    existente = conn.execute(
        "SELECT num_envio, lugar_override, cumplio_sla FROM envios_colecta WHERE num_envio = ?",
        (num_envio,),
    ).fetchone()

    if existente:
        override = existente["lugar_override"]
        if override:
            proveedor_id = _resolver_proveedor(conn, override) or proveedor_id
        if cumplio_sla is None:
            cumplio_sla = existente["cumplio_sla"]
        conn.execute(
            """UPDATE envios_colecta SET num_venta=?, num_venta_ml=?, match_cruce_confianza=1.0,
                                         fecha_venta=?, titulo=?, lugar_indicado=?,
                                         proveedor_id=?, cumplio_sla=?
               WHERE num_envio=?""",
            (num_venta, num_venta, fecha, titulo, lugar_indicado, proveedor_id, cumplio_sla, num_envio),
        )
    else:
        conn.execute(
            """INSERT INTO envios_colecta
               (num_envio, num_venta, num_venta_ml, match_cruce_confianza, fecha_venta,
                titulo, lugar_indicado, proveedor_id, cumplio_sla)
               VALUES (?, ?, ?, 1.0, ?, ?, ?, ?, ?)""",
            (num_envio, num_venta, num_venta, fecha, titulo, lugar_indicado, proveedor_id, cumplio_sla),
        )


# ---------------------------------------------------------------------------
# Cierre de la corrida
# ---------------------------------------------------------------------------

def _finalizar(run_id: int, inicio: datetime, stats: dict) -> None:
    with get_db() as conn:
        # Cruce legacy (envíos del Excel sin ID directo) + cruce retroactivo de facturas.
        cruce = resolver_cruce_ventas(conn)
        recruce = recruzar_conceptos_sin_match(conn)

        # Las notificaciones pendientes quedan reconciliadas por el polling de esta
        # corrida (date_last_updated con solape); las que lleguen DURANTE la corrida
        # refieren updates posteriores a `inicio`, que el próximo incremental
        # (ultima_sync - 5 min) vuelve a cubrir. Por eso es seguro marcarlas todas.
        conn.execute("UPDATE ml_notificaciones SET procesada=1 WHERE procesada=0")

        # Poda de auditoría.
        corte = (datetime.utcnow() - timedelta(days=API_LOG_RETENCION_DIAS)).isoformat(timespec="seconds")
        conn.execute("DELETE FROM ml_api_log WHERE ts < ?", (corte,))

        # ultima_sync = INICIO de la corrida (no el fin): lo actualizado durante la
        # corrida se re-consulta en la próxima. Solo avanza si terminó completa.
        set_config(conn, "ultima_sync", inicio.isoformat(timespec="seconds"))

        resumen = {
            "ordenes_vistas": stats["ordenes_vistas"],
            "ventas_upsert": stats["ventas_upsert"],
            "envios_upsert": stats["envios_upsert"],
            "errores": stats["errores"],
            "detalle_errores": stats.get("detalle_errores") or [],
            "cruce_legacy": cruce,
            "recruce_facturas": recruce,
        }
        ahora = datetime.utcnow().isoformat(timespec="seconds")
        conn.execute(
            """UPDATE ml_sync_runs SET estado='completado', ordenes_vistas=?, ventas_upsert=?,
                                       envios_upsert=?, errores=?, detalle=?, actualizado_en=?, terminado_en=?
               WHERE id=?""",
            (stats["ordenes_vistas"], stats["ventas_upsert"], stats["envios_upsert"],
             stats["errores"], json.dumps(resumen, ensure_ascii=False), ahora, ahora, run_id),
        )
