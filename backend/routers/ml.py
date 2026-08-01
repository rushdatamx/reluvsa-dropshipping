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
from models import MLSyncAutoRequest, MLSyncRequest
from routers.auth import require_admin
from services import ml_client, sync_ml

router = APIRouter(prefix="/api/ml", tags=["mercadolibre"])

STATE_TTL_MIN = 10

AVISO_TITULAR = (
    "Esta autorización debe hacerla el TITULAR de la cuenta de Mercado Libre "
    "(la cuenta principal, no un operador/colaborador)."
)


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
        return _html("Solicitud inválida", "Faltan parámetros de la autorización.",
                     ok=False, status_code=400)

    ahora = datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as conn:
        # Validar y QUEMAR el state en la misma sentencia (un solo uso, no expirado).
        cur = conn.execute(
            "UPDATE ml_oauth_state SET usado_en=? WHERE state=? AND usado_en IS NULL AND expira_en >= ?",
            (ahora, state, ahora),
        )
        if cur.rowcount != 1:
            return _html("Enlace inválido o vencido",
                         "Esta autorización ya fue usada o expiró. Genera una nueva desde el portal.",
                         ok=False, status_code=400)

    try:
        ml_client.canjear_code(code)
    except ml_client.MLError:
        return _html("No se pudo completar la conexión",
                     "Mercado Libre rechazó el intercambio de credenciales. Verifica que la "
                     "autorización la haya hecho el titular de la cuenta e inténtalo de nuevo.",
                     ok=False, status_code=400)

    # Bootstrap post-conexión (identidad + depósitos). Si falla, los tokens YA
    # quedaron guardados: se reintenta al inicio de cada sync.
    try:
        boot = sync_ml._bootstrap_post_conexion()
        nickname = boot.get("nickname") or "cuenta ML"
        return _html(f"Cuenta {nickname} conectada",
                     "El portal ya puede leer ventas y envíos de Mercado Libre. "
                     "Puedes cerrar esta pestaña y volver al portal.")
    except ml_client.MLError:
        return _html("Cuenta conectada (con pendiente)",
                     "La cuenta quedó conectada, pero no se pudo leer su información inicial "
                     "(se reintentará al sincronizar). Verifica en el portal.")


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


@router.get("/diag-pack", dependencies=[Depends(require_admin)])
def diag_pack(num_venta: str = None):
    """Diagnóstico SOLO LECTURA: cómo viene `pack_id` en las órdenes de la cuenta.

    Contexto: el portal de ML le muestra a Gaby un número de venta distinto al que
    mostramos nosotros (nosotros guardamos `order.id`; ML parece mostrar `pack_id`,
    que hoy NO persistimos). Antes de implementar hay que saber:
      1. si ML manda pack_id SIEMPRE o sólo en compras de carrito (si viene null,
         la columna necesita fallback o saldría vacía);
      2. si un pack agrupa VARIAS órdenes (varias filas mostrarían el mismo número);
      3. en un caso concreto, si el número de ML es el pack_id o es otra cosa.

    No escribe nada: sólo GET a ML (allowlist) y SELECT sobre la BD.
    """
    salida = {"ejemplo": None, "muestra": None}

    # --- 1) Caso concreto, si se pide ---
    if num_venta:
        num_venta = str(num_venta).strip()
        detalle = {"num_venta_consultado": num_venta}
        try:
            o = ml_client.get(f"/orders/{num_venta}")
            detalle["orden"] = {
                "order_id": str(o.get("id")),
                "pack_id": str(o.get("pack_id")) if o.get("pack_id") else None,
                "shipping_id": str((o.get("shipping") or {}).get("id") or "") or None,
                "date_created": o.get("date_created"),
                "status": o.get("status"),
                "items": [
                    {"sku": (it.get("item") or {}).get("seller_sku"),
                     "titulo": (it.get("item") or {}).get("title")}
                    for it in (o.get("order_items") or [])
                ],
            }
        except ml_client.MLError as e:
            detalle["orden"] = f"{type(e).__name__}: {e}"

        try:
            p = ml_client.get(f"/packs/{num_venta}")
            detalle["como_pack"] = {
                "es_pack": True,
                "ordenes": [str(x.get("id")) for x in (p.get("orders") or [])],
            }
        except ml_client.MLError as e:
            detalle["como_pack"] = f"no es un pack ({type(e).__name__})"

        with get_db() as conn:
            r = conn.execute(
                "SELECT num_venta, sku, titulo, fecha_venta, deposito, albaran FROM ventas_ml WHERE num_venta=?",
                (num_venta,),
            ).fetchone()
        detalle["en_nuestra_bd"] = dict(r) if r else None
        salida["ejemplo"] = detalle

    # --- 2) Muestra de órdenes recientes: ¿qué tan seguido viene pack_id? ---
    with get_db() as conn:
        seller = sync_ml.get_config(conn, "seller_id")
    if not seller:
        salida["muestra"] = "No hay seller_id en ml_config (¿cuenta conectada?)"
        return salida

    try:
        page = ml_client.get("/orders/search", params={
            "seller": seller, "limit": 50, "sort": "date_desc",
        })
        resultados = page.get("results") or []
        con_pack = [o for o in resultados if o.get("pack_id")]
        packs = {}
        for o in con_pack:
            packs.setdefault(str(o["pack_id"]), []).append(str(o.get("id")))
        multi = {k: v for k, v in packs.items() if len(v) > 1}

        salida["muestra"] = {
            "ordenes_analizadas": len(resultados),
            "con_pack_id": len(con_pack),
            "SIN_pack_id": len(resultados) - len(con_pack),
            "packs_distintos": len(packs),
            "packs_con_varias_ordenes": len(multi),
            "ejemplos_multi": dict(list(multi.items())[:5]),
            "ejemplos": [
                {"order_id": str(o.get("id")),
                 "pack_id": str(o.get("pack_id")) if o.get("pack_id") else None}
                for o in resultados[:10]
            ],
        }
    except ml_client.MLError as e:
        salida["muestra"] = f"{type(e).__name__}: {e}"

    return salida


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
