"""Lectura de postventa de ML. El buzón se confirma sólo tras persistir el caso."""
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from database import get_db
from services import ml_client
from services.sync_ml import get_config, set_config

CLAIM_RE = re.compile(r"^/?post-purchase/v1/claims/(\d+)(?:/)?$")
_thread = None
_lock = threading.Lock()
_retry_at = {}
_retry_count = {}
_reconcile_retry_at = {}


def _fecha_utc(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def _accion_vendedor(claim, detail):
    if claim.get("status") != "opened":
        return None, None
    for player in claim.get("players") or []:
        if player.get("role") != "respondent" or player.get("type") != "seller":
            continue
        actions = [a for a in player.get("available_actions") or [] if a.get("mandatory")]
        if not actions:
            actions = player.get("available_actions") or []
        if actions:
            action = min(actions, key=lambda a: _fecha_utc(a.get("due_date")) or "9999")
            return action.get("action"), _fecha_utc(action.get("due_date") or detail.get("due_date"))
    if detail.get("action_responsible") in ("seller", "respondent"):
        return detail.get("title") or "Acción del vendedor", _fecha_utc(detail.get("due_date"))
    return None, None


def procesar_reclamo(cuenta, claim_id):
    path = f"/post-purchase/v1/claims/{claim_id}"
    claim = ml_client.get(path, cuenta_ml=cuenta)
    detail = ml_client.get_opcional(path + "/detail", cuenta_ml=cuenta) or {}
    reason_id = claim.get("reason_id")
    reason = (ml_client.get_opcional(f"/post-purchase/v1/claims/reasons/{reason_id}", cuenta_ml=cuenta)
              if reason_id else None) or {}
    returned = ml_client.get_opcional(f"/post-purchase/v2/claims/{claim_id}/returns", cuenta_ml=cuenta)
    action, deadline = _accion_vendedor(claim, detail)
    # Registrar el resultado negativo antes de confirmar el aviso. Una futura
    # notificación del mismo claim puede convertirlo en devolución.
    if not returned and claim.get("type") != "return":
        with get_db() as conn:
            conn.execute("""INSERT INTO ml_postventa_sin_devolucion
                (cuenta_ml, claim_id, estado_ml, actualizado_ml, comprobado_en) VALUES (?,?,?,?,?)
                ON CONFLICT(cuenta_ml,claim_id) DO UPDATE SET estado_ml=excluded.estado_ml,
                actualizado_ml=excluded.actualizado_ml, comprobado_en=excluded.comprobado_en""",
                (cuenta, str(claim_id), claim.get("status"), claim.get("last_updated"),
                 datetime.now(timezone.utc).isoformat()))
        return False
    order_id = claim.get("order_id") or (claim.get("resource_id") if claim.get("resource") == "order" else None)
    pack_id = claim.get("pack_id")
    order = ml_client.get_opcional(f"/orders/{order_id}", cuenta_ml=cuenta) if order_id else None
    productos = [{"sku": (i.get("item") or {}).get("seller_sku") or (i.get("item") or {}).get("seller_custom_field"),
                  "titulo": (i.get("item") or {}).get("title"), "unidades_vendidas": i.get("quantity")}
                 for i in (order or {}).get("order_items") or []]
    reason_text = reason.get("detail") or reason.get("name") or detail.get("problem") or reason_id
    if isinstance(reason_text, dict):
        reason_text = reason_text.get("text") or reason_text.get("name") or reason_id
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute("DELETE FROM ml_postventa_sin_devolucion WHERE cuenta_ml=? AND claim_id=?", (cuenta, str(claim_id)))
        conn.execute("""INSERT INTO ml_devoluciones
            (cuenta_ml, claim_id, order_id, pack_id, return_id, estado_ml, estado_devolucion,
             accion_pendiente, fecha_limite, unidades_reclamadas, motivo, reason_id, detalle,
             creado_ml, actualizado_ml, raw_claim, raw_return, productos_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(cuenta_ml,claim_id) DO UPDATE SET
             order_id=excluded.order_id, pack_id=excluded.pack_id, return_id=excluded.return_id,
             estado_ml=excluded.estado_ml, estado_devolucion=excluded.estado_devolucion,
             accion_pendiente=excluded.accion_pendiente, fecha_limite=excluded.fecha_limite,
             unidades_reclamadas=excluded.unidades_reclamadas, motivo=excluded.motivo,
             reason_id=excluded.reason_id, detalle=excluded.detalle,
             actualizado_ml=excluded.actualizado_ml, raw_claim=excluded.raw_claim,
             raw_return=excluded.raw_return, productos_json=excluded.productos_json""",
            (cuenta, str(claim_id), str(order_id) if order_id else None,
             str(pack_id) if pack_id else None, str(returned.get("id")) if returned and returned.get("id") else None,
             claim.get("status"), returned.get("status") if returned else None,
             action, deadline, claim.get("claimed_quantity"), str(reason_text) if reason_text else None,
             reason_id, detail.get("description"), claim.get("date_created"),
             claim.get("last_updated") or now, json.dumps(claim, ensure_ascii=False),
             json.dumps(returned, ensure_ascii=False) if returned else None,
             json.dumps(productos, ensure_ascii=False)))
    return True


def procesar_pendientes(limit=30):
    with get_db() as conn:
        rows = conn.execute("""SELECT id, cuenta_ml, resource FROM ml_notificaciones
            WHERE topic='post_purchase' AND procesada=0
            AND resource GLOB '*claims/[0-9]*' ORDER BY id LIMIT 500""").fetchall()
    groups = {}
    for row in rows:
        match = CLAIM_RE.fullmatch(row["resource"] or "")
        if match:
            groups.setdefault((row["cuenta_ml"], match.group(1)), []).append(row["id"])
    ok = errors = 0
    for key, ids in list(groups.items())[:limit]:
        if time.monotonic() < _retry_at.get(key, 0):
            continue
        try:
            procesar_reclamo(*key)
        except ml_client.MLError:
            attempt = min(_retry_count.get(key, 0) + 1, 8)
            _retry_count[key] = attempt
            _retry_at[key] = time.monotonic() + min(60 * 2 ** (attempt - 1), 3600)
            errors += 1
            continue
        _retry_at.pop(key, None)
        _retry_count.pop(key, None)
        with get_db() as conn:
            conn.execute("""UPDATE ml_notificaciones SET procesada=1
                WHERE id IN (""" + ",".join("?" for _ in ids) + ")", ids)
        ok += 1
    return {"procesados": ok, "errores": errors}


def reconciliar(cuenta):
    """Busca abiertos, cambios nuevos y cerrados históricos con acción pendiente."""
    with get_db() as conn:
        seller = get_config(conn, "seller_id", cuenta)
        if not seller:
            return 0
        since = get_config(conn, "devoluciones_inicio", cuenta)
        closed_scanned = get_config(conn, "devoluciones_cerrados_revisados", cuenta)
        if not since:
            since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
            set_config(conn, "devoluciones_inicio", since, cuenta)
    total = 0
    # El filtro abierto recupera casos antiguos activos. El rango recupera
    # cierres recientes y eventos perdidos desde el lanzamiento.
    searches = [{"status": "opened"}, {"range": f"last_updated:after:{since}"}]
    if not closed_scanned:
        searches.append({"status": "closed", "sort": "date_created:asc"})
    for extra in searches:
        for offset in range(0, 9950, 50):
            data = ml_client.get("/post-purchase/v1/claims/search", params={
                "players.user_id": seller, "players.role": "respondent",
                "limit": 50, "offset": offset, **extra}, cuenta_ml=cuenta)
            claims = data.get("data") or []
            with get_db() as conn:
                for claim in claims:
                    if claim.get("status") == "closed" and not any(
                        a.get("available_actions") for a in claim.get("players") or []):
                        continue
                    existing = conn.execute("""SELECT actualizado_ml FROM ml_devoluciones
                        WHERE cuenta_ml=? AND claim_id=? UNION ALL
                        SELECT actualizado_ml FROM ml_postventa_sin_devolucion
                        WHERE cuenta_ml=? AND claim_id=? LIMIT 1""",
                        (cuenta, str(claim["id"]), cuenta, str(claim["id"]))).fetchone()
                    if existing and existing["actualizado_ml"] == claim.get("last_updated"):
                        continue
                    resource = f"/post-purchase/v1/claims/{claim['id']}"
                    pending = conn.execute("""SELECT 1 FROM ml_notificaciones WHERE cuenta_ml=?
                        AND topic='post_purchase' AND resource=? AND procesada=0 LIMIT 1""", (cuenta, resource)).fetchone()
                    if not pending:
                        conn.execute("""INSERT INTO ml_notificaciones
                            (topic, resource, user_id, recibido_en, procesada, cuenta_ml)
                            VALUES ('post_purchase', ?, ?, ?, 0, ?)""",
                            (resource, seller, datetime.now().isoformat(timespec="seconds"), cuenta))
                        total += 1
            if len(claims) < 50 or offset + 50 >= (data.get("paging") or {}).get("total", 0):
                break
        if extra.get("status") == "closed":
            with get_db() as conn:
                set_config(conn, "devoluciones_cerrados_revisados", "1", cuenta)
    return total


def _loop():
    while True:
        try:
            with get_db() as conn:
                accounts = [r["clave"] for r in conn.execute("SELECT clave FROM ml_cuentas WHERE activo=1")]
            for cuenta in accounts:
                if not ml_client.hay_conexion(cuenta):
                    continue
                with get_db() as conn:
                    last = get_config(conn, "devoluciones_ultima_reconciliacion", cuenta)
                try:
                    if time.monotonic() >= _reconcile_retry_at.get(cuenta, 0) and (
                        not last or datetime.now(timezone.utc) - datetime.fromisoformat(last) >= timedelta(hours=6)):
                        reconciliar(cuenta)
                        _reconcile_retry_at.pop(cuenta, None)
                        with get_db() as conn:
                            set_config(conn, "devoluciones_ultima_reconciliacion", datetime.now(timezone.utc).isoformat(), cuenta)
                except ml_client.MLError:
                    # Fallar la búsqueda no impide consumir los avisos ya recibidos.
                    _reconcile_retry_at[cuenta] = time.monotonic() + 900
        except Exception:
            # Un error de configuración no mata el hilo.
            pass
        try:
            procesar_pendientes()
        except Exception:
            # El siguiente ciclo vuelve a intentar el aviso que quedó pendiente.
            pass
        time.sleep(60)


def iniciar_scheduler():
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return False
        _thread = threading.Thread(target=_loop, name="ml-postventa-scheduler", daemon=True)
        _thread.start()
        return True
