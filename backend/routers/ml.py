"""
Integración con Mercado Libre: OAuth (conectar la cuenta), estado y sync.

Seguridad (docs/configuracion-app-ml.md §6 + skill api-seguridad):
- Todo el tráfico a ML sale por services.ml_client (SOLO GET + POST /oauth/token).
- /oauth/callback es el ÚNICO endpoint sin JWT (lo abre el navegador del titular
  vía redirect de ML); se protege con el state anti-CSRF de un solo uso.
- /estado NUNCA devuelve tokens — solo metadatos.
- La autorización debe hacerla el TITULAR de la cuenta ML (un operador da
  invalid_operator_user_id).
"""
import html as html_mod
import json
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from database import get_db
from models import MLDepositoProveedorRequest, MLSyncAutoRequest, MLSyncCuentaAutoRequest, MLSyncRequest
from routers.auth import require_admin
from services import ml_client, sync_ml

router = APIRouter(prefix="/api/ml", tags=["mercadolibre"])

STATE_TTL_MIN = 10

AVISO_TITULAR = (
    "Esta autorización debe hacerla el TITULAR de la cuenta de Mercado Libre "
    "(la cuenta principal, no un operador/colaborador)."
)


def _cuenta_valida(cuenta: str) -> str:
    if cuenta not in ("principal", "secundaria"):
        raise HTTPException(status_code=404, detail="Cuenta ML no encontrada")
    return cuenta


def _html(titulo: str, mensaje: str, ok: bool = True, status_code: int = 200) -> HTMLResponse:
    # titulo/mensaje pueden interpolar valores externos (p.ej. ?error= del redirect
    # de ML o el nickname de la cuenta): se escapan siempre.
    titulo = html_mod.escape(titulo)
    mensaje = html_mod.escape(mensaje)
    color = "#2e7d32" if ok else "#E31E24"
    icono = "✓" if ok else "✕"
    return HTMLResponse(status_code=status_code, content=f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>{titulo}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body {{ font-family: -apple-system, 'Segoe UI', sans-serif; background:#1a1a1a; color:#fff;
        display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }}
 .card {{ background:#fff; color:#1a1a1a; border-radius:12px; padding:40px 48px; max-width:520px;
        text-align:center; box-shadow:0 8px 30px rgba(0,0,0,.4); }}
 .icono {{ font-size:48px; color:{color}; }}
 h1 {{ font-size:22px; margin:12px 0 8px; }}
 p {{ color:#555; line-height:1.5; }}
 .marca {{ margin-top:24px; font-weight:700; }} .marca span {{ background:#FFED00; padding:2px 8px; border-radius:4px; }}
</style></head><body><div class="card">
<div class="icono">{icono}</div><h1>{titulo}</h1><p>{mensaje}</p>
<div class="marca"><span>RELUVSA</span> · Portal Dropshipping</div>
</div></body></html>""")


@router.post("/oauth/iniciar", dependencies=[Depends(require_admin)])
def iniciar_oauth():
    """Genera la URL de autorización (con state anti-CSRF de un solo uso).
    La UI la abre y también la muestra copiable: el titular puede estar en otra máquina."""
    if not ml_client.esta_configurado():
        raise HTTPException(
            status_code=409,
            detail="ML_CLIENT_ID / ML_CLIENT_SECRET no están configurados en el servidor (Railway).",
        )

    state = secrets.token_urlsafe(32)
    ahora = datetime.utcnow()
    with get_db() as conn:
        # Limpieza de states vencidos de paso.
        conn.execute("DELETE FROM ml_oauth_state WHERE expira_en < ?",
                     (ahora.isoformat(timespec="seconds"),))
        conn.execute(
            "INSERT INTO ml_oauth_state (state, creado_en, expira_en) VALUES (?, ?, ?)",
            (state, ahora.isoformat(timespec="seconds"),
             (ahora + timedelta(minutes=STATE_TTL_MIN)).isoformat(timespec="seconds")),
        )
    return {"authorization_url": ml_client.build_authorization_url(state), "aviso": AVISO_TITULAR}


@router.post("/cuentas/{cuenta}/oauth/iniciar", dependencies=[Depends(require_admin)])
def iniciar_oauth_cuenta(cuenta: str):
    cuenta = _cuenta_valida(cuenta)
    if not ml_client.esta_configurado(cuenta):
        raise HTTPException(status_code=409, detail="Las credenciales de esta cuenta no están configuradas en Railway.")
    state = secrets.token_urlsafe(32)
    ahora = datetime.utcnow()
    with get_db() as conn:
        conn.execute("DELETE FROM ml_oauth_state WHERE expira_en < ?", (ahora.isoformat(timespec="seconds"),))
        # El state contiene la cuenta en BD, no en el parámetro del callback: no es manipulable.
        conn.execute("INSERT INTO ml_oauth_state (state, creado_en, expira_en) VALUES (?, ?, ?)",
                     (f"{cuenta}:{state}", ahora.isoformat(timespec="seconds"),
                      (ahora + timedelta(minutes=STATE_TTL_MIN)).isoformat(timespec="seconds")))
    return {"authorization_url": ml_client.build_authorization_url(f"{cuenta}:{state}", cuenta), "aviso": AVISO_TITULAR}


@router.get("/oauth/callback")
def oauth_callback(code: str = None, state: str = None, error: str = None):
    """Aterrizaje del redirect de ML tras autorizar. SIN JWT (lo abre el navegador
    del titular); la protección es el state de un solo uso con TTL."""
    if error:
        # p.ej. access_denied si el titular canceló. Sin detalles internos.
        return _html("Autorización no completada",
                     f"Mercado Libre reportó: {error}. Puedes intentarlo de nuevo desde el portal.",
                     ok=False, status_code=400)
    if not code or not state:
        return _html("Solicitud inválida", "Faltan parámetros de la autorización.", ok=False, status_code=400)
    ahora = datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as conn:
        cur = conn.execute("UPDATE ml_oauth_state SET usado_en=? WHERE state=? AND usado_en IS NULL AND expira_en >= ?",
                           (ahora, state, ahora))
        if cur.rowcount != 1:
            return _html("Enlace inválido o vencido", "Esta autorización ya fue usada o expiró.", False, 400)
    try:
        ml_client.canjear_code(code)
        boot = sync_ml._bootstrap_post_conexion()
    except ml_client.MLError:
        return _html("No se pudo completar la conexión", "Mercado Libre rechazó el intercambio de credenciales.", False, 400)
    return _html(f"Cuenta {boot.get('nickname') or 'ML'} conectada", "Puedes cerrar esta pestaña y volver al portal.")


@router.get("/cuentas/{cuenta}/oauth/callback")
def oauth_callback_cuenta(cuenta: str, code: str = None, state: str = None, error: str = None):
    cuenta = _cuenta_valida(cuenta)
    if error or not code or not state or not state.startswith(f"{cuenta}:"):
        return _html("Autorización no completada", "La solicitud de autorización es inválida o fue cancelada.", False, 400)
    ahora = datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as conn:
        cur = conn.execute("UPDATE ml_oauth_state SET usado_en=? WHERE state=? AND usado_en IS NULL AND expira_en >= ?",
                           (ahora, state, ahora))
        if cur.rowcount != 1:
            return _html("Enlace inválido o vencido", "Genera una nueva autorización desde el portal.", False, 400)
    try:
        ml_client.canjear_code(code, cuenta)
        boot = sync_ml._bootstrap_post_conexion(cuenta)
    except ml_client.MLError:
        return _html("No se pudo completar la conexión", "Mercado Libre rechazó el intercambio. Debe autorizar el titular.", False, 400)
    return _html(f"Cuenta {boot.get('nickname') or cuenta} conectada", "La cuenta quedó lista para validar depósitos y sincronizar.")


@router.get("/cuentas", dependencies=[Depends(require_admin)])
def estado_cuentas_ml():
    salida = []
    with get_db() as conn:
        for r in conn.execute("SELECT * FROM ml_cuentas ORDER BY clave").fetchall():
            cuenta = r["clave"]
            salida.append({**dict(r), "configurado": ml_client.esta_configurado(cuenta),
                           **ml_client.estado_conexion(cuenta),
                           "ultima_sync": sync_ml.get_config(conn, "ultima_sync", cuenta),
                           "stores": [dict(s) for s in conn.execute(
                               "SELECT store_id, description, network_node_id, proveedor_id FROM ml_deposito_proveedor WHERE cuenta_ml=? ORDER BY description", (cuenta,)).fetchall()]})
    return {"cuentas": salida}


@router.patch("/cuentas/{cuenta}/depositos/{store_id}", dependencies=[Depends(require_admin)])
def asignar_proveedor_deposito(cuenta: str, store_id: str, payload: MLDepositoProveedorRequest):
    """Persistencia manual del proveedor para una bodega desconocida.
    Solo cambia la asignación local; jamás escribe depósitos ni stock en ML.
    """
    cuenta = _cuenta_valida(cuenta)
    with get_db() as conn:
        if payload.proveedor_id is not None and not conn.execute("SELECT 1 FROM proveedores WHERE id=?", (payload.proveedor_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Proveedor no encontrado")
        cur = conn.execute("UPDATE ml_deposito_proveedor SET proveedor_id=?, actualizado_en=? WHERE cuenta_ml=? AND store_id=?",
                           (payload.proveedor_id, datetime.utcnow().isoformat(timespec="seconds"), cuenta, store_id))
        if cur.rowcount != 1:
            raise HTTPException(status_code=404, detail="Depósito no encontrado")
    return {"ok": True}


@router.patch("/cuentas/{cuenta}/sync-auto", dependencies=[Depends(require_admin)])
def configurar_sync_auto_cuenta(cuenta: str, payload: MLSyncCuentaAutoRequest):
    cuenta = _cuenta_valida(cuenta)
    if payload.intervalo_minutos is not None and not (sync_ml.SYNC_AUTO_MIN_MINUTOS <= payload.intervalo_minutos <= sync_ml.SYNC_AUTO_MAX_MINUTOS):
        raise HTTPException(status_code=400, detail="Intervalo de sync inválido")
    with get_db() as conn:
        sets, params = [], []
        if payload.activo is not None:
            sets.append("sync_activo=?"); params.append(1 if payload.activo else 0)
        if payload.intervalo_minutos is not None:
            sets.append("sync_intervalo_minutos=?"); params.append(payload.intervalo_minutos)
        if sets:
            params.extend([datetime.utcnow().isoformat(timespec="seconds"), cuenta])
            conn.execute(f"UPDATE ml_cuentas SET {', '.join(sets)}, actualizado_en=? WHERE clave=?", params)
        return dict(conn.execute("SELECT clave, sync_activo, sync_intervalo_minutos FROM ml_cuentas WHERE clave=?", (cuenta,)).fetchone())


@router.get("/estado", dependencies=[Depends(require_admin)])
def estado_ml():
    """Estado completo de la integración para la UI. NUNCA incluye tokens."""
    estado = {
        "configurado": ml_client.esta_configurado(),
        **ml_client.estado_conexion(),
        "requiere_reautorizacion": False,
        "nickname": None,
        "seller_id": None,
        "multiorigen": None,
        "stores": [],
        "ultima_sync": None,
        "sync_en_curso": None,
        "ultima_run": None,
        "notificaciones_pendientes": 0,
        "sync_auto": None,
    }

    with get_db() as conn:
        estado["seller_id"] = sync_ml.get_config(conn, "seller_id")
        estado["nickname"] = sync_ml.get_config(conn, "nickname")
        estado["ultima_sync"] = sync_ml.get_config(conn, "ultima_sync")
        tags_json = sync_ml.get_config(conn, "tags_json")
        if tags_json:
            try:
                tags = json.loads(tags_json)
            except ValueError:
                tags = []
            estado["multiorigen"] = {
                "warehouse_management": "warehouse_management" in tags,
                "multiwarehouse": "multiwarehouse" in tags,
            }
        estado["stores"] = [
            dict(r) for r in conn.execute(
                "SELECT store_id, description, network_node_id, codigo_bodega FROM ml_stores ORDER BY description"
            ).fetchall()
        ]
        viva = conn.execute(
            "SELECT * FROM ml_sync_runs WHERE estado='en_curso' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        estado["sync_en_curso"] = dict(viva) if viva else None
        ultima = conn.execute(
            "SELECT * FROM ml_sync_runs WHERE estado != 'en_curso' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        estado["ultima_run"] = dict(ultima) if ultima else None
        estado["notificaciones_pendientes"] = conn.execute(
            "SELECT COUNT(*) AS c FROM ml_notificaciones WHERE procesada = 0"
        ).fetchone()["c"]
        estado["sync_auto"] = sync_ml.estado_sync_auto(conn)

    return estado


@router.post("/sync-auto", dependencies=[Depends(require_admin)])
def configurar_sync_auto(payload: MLSyncAutoRequest):
    """Enciende/apaga la sync automática y ajusta su intervalo (sin redeploy)."""
    with get_db() as conn:
        if payload.activo is not None:
            sync_ml.set_config(conn, "sync_auto_activo", "1" if payload.activo else "0")
        if payload.intervalo_minutos is not None:
            minutos = int(payload.intervalo_minutos)
            if not (sync_ml.SYNC_AUTO_MIN_MINUTOS <= minutos <= sync_ml.SYNC_AUTO_MAX_MINUTOS):
                raise HTTPException(
                    status_code=400,
                    detail=(f"El intervalo debe estar entre {sync_ml.SYNC_AUTO_MIN_MINUTOS} y "
                            f"{sync_ml.SYNC_AUTO_MAX_MINUTOS} minutos."),
                )
            sync_ml.set_config(conn, "sync_auto_minutos", str(minutos))
        return sync_ml.estado_sync_auto(conn)


@router.get("/api-log", dependencies=[Depends(require_admin)])
def api_log(limit: int = 50, solo_errores: bool = False):
    """Auditoría de las últimas llamadas salientes a ML (tabla ml_api_log).

    Diagnóstico: permite ver el status real que devolvió ML (p.ej. por qué falló
    el canje en /oauth/token) sin depender de los logs del contenedor.

    La tabla NUNCA contiene tokens ni secretos (ml_client._path_filtrado filtra la
    query y el body jamás se registra), así que exponerla al admin es seguro.
    """
    limit = max(1, min(int(limit), 200))
    sql = "SELECT id, metodo, path, status, ms, error, ts FROM ml_api_log"
    if solo_errores:
        sql += " WHERE status IS NULL OR status >= 400"
    sql += " ORDER BY id DESC LIMIT ?"
    with get_db() as conn:
        filas = [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]
    return {"total_devuelto": len(filas), "llamadas": filas}


@router.post("/sync", dependencies=[Depends(require_admin)])
def disparar_sync(payload: MLSyncRequest):
    """Dispara una sincronización manual (incremental o backfill de 12 meses)."""
    if payload.tipo not in ("incremental", "backfill"):
        raise HTTPException(status_code=400, detail="tipo debe ser 'incremental' o 'backfill'")
    try:
        return sync_ml.iniciar_sync(payload.tipo)
    except sync_ml.SyncEnCurso as e:
        raise HTTPException(status_code=409, detail={
            "mensaje": "Ya hay una sincronización en curso",
            "run": {k: e.run.get(k) for k in ("id", "tipo", "ordenes_vistas", "iniciado_en")},
        })
    except ml_client.MLNoConectado:
        raise HTTPException(status_code=409, detail="No hay cuenta de Mercado Libre conectada todavía.")
    except ml_client.MLNoConfigurado:
        raise HTTPException(status_code=409, detail="ML_CLIENT_ID / ML_CLIENT_SECRET no configurados en el servidor.")


@router.post("/cuentas/{cuenta}/sync", dependencies=[Depends(require_admin)])
def disparar_sync_cuenta(cuenta: str, payload: MLSyncRequest):
    cuenta = _cuenta_valida(cuenta)
    if payload.tipo not in ("incremental", "backfill"):
        raise HTTPException(status_code=400, detail="tipo debe ser 'incremental' o 'backfill'")
    try:
        return sync_ml.iniciar_sync(payload.tipo, cuenta)
    except sync_ml.SyncEnCurso:
        raise HTTPException(status_code=409, detail="Ya hay una sincronización en curso en el worker.")
    except (ml_client.MLNoConectado, ml_client.MLNoConfigurado):
        raise HTTPException(status_code=409, detail="La cuenta no está conectada o configurada.")
