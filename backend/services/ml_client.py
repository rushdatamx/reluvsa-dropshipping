"""
Cliente HTTP de la API de Mercado Libre — ÚNICO punto de salida a la red de ML.

🔒 APP SOLO LECTURA (docs/configuracion-app-ml.md §6): este módulo impone a nivel
técnico que de aquí solo pueden salir requests GET a api.mercadolibre.com, con la
única excepción de POST /oauth/token (canje y refresh de tokens OAuth). Cualquier
otro método, path u host lanza MLEscrituraProhibida ANTES de tocar la red.

PROHIBIDO importar httpx o llamar api.mercadolibre.com desde cualquier otro archivo
del backend — la verificación estática de scripts/test_ml_client_solo_lectura.py lo
revisa. Todo consumo de la API pasa por ml_client.get()/get_opcional().

Tokens:
- access token: expira según el expires_in REAL de la respuesta (nunca hardcodear).
- refresh token: de UN SOLO USO — ML rota el refresh en cada renovación; access y
  refresh nuevos se persisten JUNTOS (una sentencia, transaccional) antes de usarse.
- NUNCA se loguean, ni en ml_api_log, ni en excepciones, ni en respuestas HTTP.

OAuth SIN PKCE: el panel de la app lo tiene deshabilitado; no se manda
code_challenge/code_verifier (mandarlo sería un bug, no una mejora).
"""
import os
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode, urlsplit

import httpx

from database import get_db

API_BASE = "https://api.mercadolibre.com"
API_HOST = "api.mercadolibre.com"
# Solo se usa para ARMAR la URL de autorización que abre el navegador del titular;
# el backend jamás hace requests a este host.
AUTH_URL = "https://auth.mercadolibre.com.mx/authorization"
OAUTH_TOKEN_PATH = "/oauth/token"

REDIRECT_URI_DEFAULT = (
    "https://reluvsa-dropshipping-production.up.railway.app/api/ml/oauth/callback"
)

# Margen antes de la expiración real para refrescar proactivamente.
MARGEN_EXPIRACION_SEG = 60
MAX_REINTENTOS_429 = 5

# Claves que jamás deben aparecer en ml_api_log (defensa en profundidad; los GET de
# este proyecto no llevan secretos en query, pero se filtra igual).
_CLAVES_SENSIBLES = {"access_token", "refresh_token", "code", "client_secret", "token"}

_refresh_lock = threading.Lock()


class MLError(Exception):
    """Error genérico de la integración con Mercado Libre."""


class MLEscrituraProhibida(MLError):
    """Se intentó un request fuera de la allowlist (la app es SOLO LECTURA)."""


class MLNoConectado(MLError):
    """No hay tokens en ml_tokens: la cuenta ML aún no está autorizada."""


class MLTokenInvalido(MLError):
    """El refresh token ya no sirve (invalid_grant): re-autorizar con el titular."""


class MLNoConfigurado(MLError):
    """Faltan ML_CLIENT_ID / ML_CLIENT_SECRET en el entorno."""


def _client_id() -> str:
    val = (os.getenv("ML_CLIENT_ID") or "").strip()
    if not val:
        raise MLNoConfigurado("ML_CLIENT_ID no está configurado en el entorno")
    return val


def _client_secret() -> str:
    val = (os.getenv("ML_CLIENT_SECRET") or "").strip()
    if not val:
        raise MLNoConfigurado("ML_CLIENT_SECRET no está configurado en el entorno")
    return val


def _redirect_uri() -> str:
    return (os.getenv("ML_REDIRECT_URI") or REDIRECT_URI_DEFAULT).strip()


def esta_configurado() -> bool:
    return bool((os.getenv("ML_CLIENT_ID") or "").strip() and (os.getenv("ML_CLIENT_SECRET") or "").strip())


def _utcnow() -> datetime:
    return datetime.utcnow()


def _http() -> httpx.Client:
    """Factory del cliente httpx. Los tests la parchean con MockTransport."""
    return httpx.Client(timeout=30.0)


def _assert_permitido(metodo: str, url: str) -> None:
    """Allowlist de la app SOLO LECTURA. Se ejecuta ANTES de tocar la red.

    Permitido: GET https://api.mercadolibre.com/... y POST /oauth/token (única
    excepción, para canje/refresh OAuth). Todo lo demás es un bug por definición.
    """
    partes = urlsplit(url)
    metodo = (metodo or "").upper()
    if partes.scheme != "https" or partes.netloc != API_HOST:
        raise MLEscrituraProhibida(
            f"Request bloqueado: host/esquema no permitido ({metodo} {partes.scheme}://{partes.netloc}{partes.path}). "
            f"Solo se permite https://{API_HOST}."
        )
    if metodo == "GET":
        return
    if metodo == "POST" and partes.path == OAUTH_TOKEN_PATH:
        return
    raise MLEscrituraProhibida(
        f"Request bloqueado: {metodo} {partes.path} no está permitido. "
        "La app es SOLO LECTURA: únicamente GET (+ POST /oauth/token)."
    )


def _path_filtrado(url: str, params: Optional[dict]) -> str:
    """Path + query para ml_api_log, con las claves sensibles filtradas."""
    partes = urlsplit(url)
    path = partes.path
    limpios = {
        k: v for k, v in (params or {}).items() if k.lower() not in _CLAVES_SENSIBLES
    }
    if limpios:
        return f"{path}?{urlencode(limpios)}"
    return path


def _log_llamada(metodo: str, path: str, status: Optional[int], ms: int, error: Optional[str] = None) -> None:
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO ml_api_log (metodo, path, status, ms, error, ts) VALUES (?, ?, ?, ?, ?, ?)",
                (metodo.upper(), path, status, ms, error, _utcnow().isoformat(timespec="seconds")),
            )
    except Exception:
        # La auditoría nunca debe tumbar una llamada legítima.
        pass


def _request(metodo: str, url: str, params: Optional[dict] = None,
             data: Optional[dict] = None, headers: Optional[dict] = None) -> httpx.Response:
    """ÚNICO punto del backend que ejecuta requests hacia ML.

    1ª línea: la allowlist. Después: reintentos con backoff exponencial + jitter
    ante 429 y registro de cada llamada en ml_api_log (sin secretos).
    """
    _assert_permitido(metodo, url)
    path_log = _path_filtrado(url, params)

    intento = 0
    while True:
        t0 = time.monotonic()
        try:
            with _http() as client:
                resp = client.request(metodo.upper(), url, params=params, data=data, headers=headers)
        except httpx.HTTPError as e:
            ms = int((time.monotonic() - t0) * 1000)
            _log_llamada(metodo, path_log, None, ms, type(e).__name__)
            raise MLError(f"Error de red hacia ML ({type(e).__name__}) en {path_log}")

        ms = int((time.monotonic() - t0) * 1000)
        _log_llamada(metodo, path_log, resp.status_code, ms)

        if resp.status_code == 429 and intento < MAX_REINTENTOS_429:
            espera = min(2 ** intento + random.uniform(0, 1), 60)
            time.sleep(espera)
            intento += 1
            continue
        return resp


# ---------------------------------------------------------------------------
# Tokens (persistencia atómica + refresh serializado)
# ---------------------------------------------------------------------------

def _guardar_tokens(data: dict) -> None:
    """Persiste access+refresh nuevos JUNTOS en una sola sentencia (atómico).

    Usa el expires_in REAL de la respuesta (la doc de ML muestra ejemplos con 3 h
    y texto normativo de 6 h: nunca hardcodear).
    """
    ahora = _utcnow()
    expira_en = ahora + timedelta(seconds=int(data["expires_in"]))
    with get_db() as conn:
        conn.execute(
            """INSERT INTO ml_tokens (id, access_token, refresh_token, token_type, scope,
                                      ml_user_id, expira_en, obtenido_en, actualizado_en)
               VALUES (1, ?, ?, ?, ?, ?, ?,
                       COALESCE((SELECT obtenido_en FROM ml_tokens WHERE id = 1), ?), ?)
               ON CONFLICT(id) DO UPDATE SET
                   access_token = excluded.access_token,
                   refresh_token = excluded.refresh_token,
                   token_type = excluded.token_type,
                   scope = excluded.scope,
                   ml_user_id = excluded.ml_user_id,
                   expira_en = excluded.expira_en,
                   actualizado_en = excluded.actualizado_en""",
            (
                data["access_token"],
                data["refresh_token"],
                data.get("token_type", "Bearer"),
                data.get("scope"),
                str(data["user_id"]) if data.get("user_id") is not None else None,
                expira_en.isoformat(timespec="seconds"),
                ahora.isoformat(timespec="seconds"),
                ahora.isoformat(timespec="seconds"),
            ),
        )


def _leer_tokens() -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM ml_tokens WHERE id = 1").fetchone()
    return dict(row) if row else None


def _expirado(tokens: dict) -> bool:
    try:
        expira = datetime.fromisoformat(tokens["expira_en"])
    except (ValueError, TypeError, KeyError):
        return True
    return _utcnow() >= expira - timedelta(seconds=MARGEN_EXPIRACION_SEG)


def _post_oauth_token(form: dict) -> dict:
    """POST /oauth/token (la única escritura permitida por la allowlist)."""
    resp = _request("POST", API_BASE + OAUTH_TOKEN_PATH, data=form,
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    if resp.status_code != 200:
        # Nunca incluir el body completo (defensa): solo el código de error de ML.
        codigo = None
        try:
            codigo = resp.json().get("error")
        except Exception:
            pass
        # El código de error de ML es el discriminador del diagnóstico
        # (invalid_client = credenciales; invalid_grant = code/redirect_uri/PKCE) y se
        # perdía dentro de la excepción. Se persiste en la auditoría — es una etiqueta
        # corta y enumerada de ML, NUNCA el body ni credenciales.
        _log_llamada("POST", OAUTH_TOKEN_PATH, resp.status_code, 0,
                     f"oauth_error={codigo}" if codigo else "oauth_error=desconocido")
        if codigo == "invalid_grant":
            raise MLTokenInvalido(
                "ML rechazó el token (invalid_grant): hay que re-autorizar con el titular de la cuenta"
            )
        raise MLError(f"/oauth/token respondió {resp.status_code} (error={codigo})")
    return resp.json()


def _refrescar(access_que_fallo: Optional[str] = None) -> str:
    """Renueva el access token. Serializado con lock + double-check contra BD:
    si dos hilos llegan con el token vencido, solo UNO hace el POST de refresh
    (crítico: el refresh token es de un solo uso)."""
    with _refresh_lock:
        tokens = _leer_tokens()
        if not tokens:
            raise MLNoConectado("No hay cuenta de ML conectada")
        # ¿Otro hilo ya refrescó mientras esperábamos el lock?
        if not _expirado(tokens) and tokens["access_token"] != (access_que_fallo or ""):
            return tokens["access_token"]

        data = _post_oauth_token({
            "grant_type": "refresh_token",
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "refresh_token": tokens["refresh_token"],
        })
        _guardar_tokens(data)
        return data["access_token"]


def _token_valido() -> str:
    tokens = _leer_tokens()
    if not tokens:
        raise MLNoConectado("No hay cuenta de ML conectada")
    if _expirado(tokens):
        return _refrescar(tokens["access_token"])
    return tokens["access_token"]


# ---------------------------------------------------------------------------
# API pública del cliente (solo lectura)
# ---------------------------------------------------------------------------

def get(path: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> dict:
    """GET autenticado a la API de ML. Ante 401 refresca el token y reintenta UNA vez."""
    access = _token_valido()
    hdrs = dict(headers or {})
    hdrs["Authorization"] = f"Bearer {access}"
    resp = _request("GET", API_BASE + path, params=params, headers=hdrs)

    if resp.status_code == 401:
        access = _refrescar(access)
        hdrs["Authorization"] = f"Bearer {access}"
        resp = _request("GET", API_BASE + path, params=params, headers=hdrs)

    if resp.status_code in (200, 206):
        return resp.json()
    raise MLError(f"GET {path} respondió {resp.status_code}")


def get_opcional(path: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> Optional[dict]:
    """Como get(), pero 404 devuelve None (p.ej. /sla no aplica a cancelados/fulfillment)."""
    access = _token_valido()
    hdrs = dict(headers or {})
    hdrs["Authorization"] = f"Bearer {access}"
    resp = _request("GET", API_BASE + path, params=params, headers=hdrs)

    if resp.status_code == 401:
        access = _refrescar(access)
        hdrs["Authorization"] = f"Bearer {access}"
        resp = _request("GET", API_BASE + path, params=params, headers=hdrs)

    if resp.status_code == 404:
        return None
    if resp.status_code in (200, 206):
        return resp.json()
    raise MLError(f"GET {path} respondió {resp.status_code}")


# ---------------------------------------------------------------------------
# OAuth (canje inicial)
# ---------------------------------------------------------------------------

def build_authorization_url(state: str) -> str:
    """URL de autorización que debe abrir el TITULAR de la cuenta ML en su navegador.
    SIN PKCE (deshabilitado en el panel): no se manda code_challenge."""
    query = urlencode({
        "response_type": "code",
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "state": state,
    })
    return f"{AUTH_URL}?{query}"


def canjear_code(code: str) -> dict:
    """Canjea el authorization code por tokens y los persiste atómicamente.
    Devuelve metadatos NO sensibles: {ml_user_id, scope}."""
    data = _post_oauth_token({
        "grant_type": "authorization_code",
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "code": code,
        "redirect_uri": _redirect_uri(),
    })
    _guardar_tokens(data)
    return {
        "ml_user_id": str(data.get("user_id")) if data.get("user_id") is not None else None,
        "scope": data.get("scope"),
    }


# ---------------------------------------------------------------------------
# Estado (sin exponer tokens jamás)
# ---------------------------------------------------------------------------

def hay_conexion() -> bool:
    return _leer_tokens() is not None


def estado_conexion() -> dict:
    """Metadatos de la conexión para la UI. NUNCA incluye los tokens."""
    tokens = _leer_tokens()
    if not tokens:
        return {"conectado": False}
    try:
        expira = datetime.fromisoformat(tokens["expira_en"])
        restante_min = int((expira - _utcnow()).total_seconds() // 60)
    except (ValueError, TypeError):
        restante_min = None
    return {
        "conectado": True,
        "ml_user_id": tokens.get("ml_user_id"),
        "scope": tokens.get("scope"),
        "obtenido_en": tokens.get("obtenido_en"),
        "actualizado_en": tokens.get("actualizado_en"),
        "token_expira_en_min": restante_min,
    }
