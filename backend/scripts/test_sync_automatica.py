"""
Regresión de la SYNC AUTOMÁTICA (scheduler de services/sync_ml.py) y del
reintento de red de services/ml_client.py::_request.

Lo que fija:

SCHEDULER
1. El intervalo por defecto es 30 min y se puede cambiar por config, acotado a
   [SYNC_AUTO_MIN_MINUTOS, SYNC_AUTO_MAX_MINUTOS] (un intervalo de 1 min
   martillaría la API de ML).
2. El interruptor `sync_auto_activo` apaga el disparo (default: encendido).
3. ⚠️ SIN `ultima_sync` NO dispara: el incremental degradaría a BACKFILL de 12
   meses (~1 h) y esa corrida grande debe ser decisión de un humano, no un
   efecto del arranque del contenedor.
4. ⚠️ El reloj es `sync_auto_ultimo_intento`, NO `ultima_sync`: `ultima_sync`
   solo avanza si la corrida TERMINA completa, así que usarla como reloj haría
   que una corrida fallida se reintentara en CADA tick (cada minuto).
5. El tick marca el intento ANTES de lanzar (misma garantía que el punto 4).
6. `iniciar_scheduler()` es idempotente: no crea dos hilos.

REINTENTO DE RED (prerequisito de la sync automática: con el botón manual
alguien ve el error y reintenta; con sync automática nadie está mirando)
7. Un GET que falla por red y luego responde 200 SE REINTENTA y termina bien.
8. Los reintentos se agotan y entonces sí lanza MLError.
9. ⚠️ POST /oauth/token NO se reintenta: si la respuesta se perdió en la red, ML
   pudo haber rotado ya el refresh token (de un solo uso) y reintentar lo
   quemaría sin poder persistir el nuevo.

No toca la red real: httpx.MockTransport + BD desechable.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test_sync_auto.db")
os.environ["ML_CLIENT_ID"] = "test-client-id"
os.environ["ML_CLIENT_SECRET"] = "test-client-secret"

import httpx  # noqa: E402

import database  # noqa: E402
from database import get_db  # noqa: E402
from services import ml_client, sync_ml  # noqa: E402

ok = True


def chk(cond: bool, msg: str) -> None:
    global ok
    print(("✅ " if cond else "❌ ") + msg)
    ok = ok and bool(cond)


database.init_database()

# Sin sleeps reales: el backoff no debe alargar el test.
ml_client.time.sleep = lambda *_a, **_k: None

AHORA = datetime.utcnow()


def _limpiar_config() -> None:
    with get_db() as conn:
        conn.execute(
            "DELETE FROM ml_config WHERE clave IN "
            "('sync_auto_activo','sync_auto_minutos','sync_auto_ultimo_intento','ultima_sync')"
        )


# ---------------------------------------------------------------------------
# 1. Intervalo: default y acotado
# ---------------------------------------------------------------------------
print("\n── Intervalo configurable ──")
_limpiar_config()
with get_db() as conn:
    chk(sync_ml.sync_auto_minutos(conn) == 30,
        "intervalo por defecto = 30 min (lo que pidió Mario)")

    sync_ml.set_config(conn, "sync_auto_minutos", "60")
    chk(sync_ml.sync_auto_minutos(conn) == 60, "intervalo configurable a 60 min sin tocar código")

    sync_ml.set_config(conn, "sync_auto_minutos", "1")
    chk(sync_ml.sync_auto_minutos(conn) == sync_ml.SYNC_AUTO_MIN_MINUTOS,
        f"intervalo de 1 min se acota al piso ({sync_ml.SYNC_AUTO_MIN_MINUTOS} min): no martillar la API")

    sync_ml.set_config(conn, "sync_auto_minutos", "99999")
    chk(sync_ml.sync_auto_minutos(conn) == sync_ml.SYNC_AUTO_MAX_MINUTOS,
        f"intervalo absurdo se acota al techo ({sync_ml.SYNC_AUTO_MAX_MINUTOS} min)")

    sync_ml.set_config(conn, "sync_auto_minutos", "no-es-numero")
    chk(sync_ml.sync_auto_minutos(conn) == 30, "valor corrupto en config → cae al default, no revienta")

# ---------------------------------------------------------------------------
# 2 y 3. Interruptor + la garantía de NO disparar backfill solo
# ---------------------------------------------------------------------------
print("\n── Cuándo toca sincronizar ──")
_limpiar_config()
with get_db() as conn:
    chk(sync_ml.sync_auto_activo(conn) is True, "sync automática ENCENDIDA por defecto")

    # Sin ultima_sync ni intento previo: jamás disparar (degradaría a backfill).
    chk(sync_ml._toca_sincronizar(conn, AHORA) is False,
        "SIN sincronización previa NO dispara (evita backfill de 12 meses automático)")

    sync_ml.set_config(conn, "ultima_sync", (AHORA - timedelta(minutes=31)).isoformat(timespec="seconds"))
    chk(sync_ml._toca_sincronizar(conn, AHORA) is True,
        "con 31 min desde la última sync (intervalo 30) → SÍ toca")

    sync_ml.set_config(conn, "ultima_sync", (AHORA - timedelta(minutes=10)).isoformat(timespec="seconds"))
    chk(sync_ml._toca_sincronizar(conn, AHORA) is False,
        "con 10 min desde la última sync → NO toca todavía")

    sync_ml.set_config(conn, "ultima_sync", (AHORA - timedelta(minutes=31)).isoformat(timespec="seconds"))
    sync_ml.set_config(conn, "sync_auto_activo", "0")
    chk(sync_ml._toca_sincronizar(conn, AHORA) is False,
        "interruptor apagado → NO dispara aunque haya pasado el intervalo")
    sync_ml.set_config(conn, "sync_auto_activo", "1")
    chk(sync_ml._toca_sincronizar(conn, AHORA) is True, "interruptor encendido de vuelta → vuelve a disparar")

# ---------------------------------------------------------------------------
# 4. El reloj es sync_auto_ultimo_intento, NO ultima_sync
# ---------------------------------------------------------------------------
print("\n── El reloj no es ultima_sync (una corrida fallida no se reintenta cada minuto) ──")
_limpiar_config()
with get_db() as conn:
    # Escenario real: la corrida de hace 2 min FALLÓ, así que ultima_sync sigue vieja.
    sync_ml.set_config(conn, "ultima_sync", (AHORA - timedelta(hours=5)).isoformat(timespec="seconds"))
    sync_ml.set_config(conn, "sync_auto_ultimo_intento", (AHORA - timedelta(minutes=2)).isoformat(timespec="seconds"))
    chk(sync_ml._toca_sincronizar(conn, AHORA) is False,
        "corrida fallida hace 2 min + ultima_sync vieja → NO reintenta (espera el intervalo completo)")

    sync_ml.set_config(conn, "sync_auto_ultimo_intento", (AHORA - timedelta(minutes=31)).isoformat(timespec="seconds"))
    chk(sync_ml._toca_sincronizar(conn, AHORA) is True,
        "pasado el intervalo desde el último INTENTO → vuelve a intentar")

# ---------------------------------------------------------------------------
# 5. El tick marca el intento ANTES de lanzar
# ---------------------------------------------------------------------------
print("\n── El tick registra el intento antes de lanzar ──")
_limpiar_config()
with get_db() as conn:
    sync_ml.set_config(conn, "ultima_sync", (AHORA - timedelta(minutes=31)).isoformat(timespec="seconds"))

lanzados = []
_iniciar_real = sync_ml.iniciar_sync
sync_ml.iniciar_sync = lambda tipo="incremental": (lanzados.append(tipo) or {"run_id": 1, "tipo": tipo})
try:
    sync_ml._tick_scheduler()
    chk(lanzados == ["incremental"], "el tick lanza una sync INCREMENTAL (nunca backfill)")
    with get_db() as conn:
        chk(sync_ml.get_config(conn, "sync_auto_ultimo_intento") is not None,
            "el intento quedó registrado en config")
        chk(sync_ml._toca_sincronizar(conn, datetime.utcnow()) is False,
            "inmediatamente después del tick ya NO toca (no se dispara en cada tick)")

    # Con una sync ya en curso, el tick no debe duplicar nada.
    with get_db() as conn:
        sync_ml.set_config(conn, "sync_auto_ultimo_intento",
                           (datetime.utcnow() - timedelta(minutes=31)).isoformat(timespec="seconds"))

    def _en_curso(tipo="incremental"):
        raise sync_ml.SyncEnCurso({"id": 9, "tipo": "backfill"})

    sync_ml.iniciar_sync = _en_curso
    with get_db() as conn:
        antes = sync_ml.get_config(conn, "sync_auto_ultimo_intento")
    chk(sync_ml._tick_scheduler() is None, "si ya hay una sync en curso el tick no duplica (SyncEnCurso)")
    with get_db() as conn:
        chk(sync_ml.get_config(conn, "sync_auto_ultimo_intento") == antes,
            "y DEVUELVE el reloj: tras un backfill largo la incremental entra al liberarse, "
            "no un intervalo después")
        chk(sync_ml._toca_sincronizar(conn, datetime.utcnow()) is True,
            "→ el próximo tick vuelve a intentar de inmediato")

    # Sin cuenta conectada tampoco debe tumbar el hilo.
    with get_db() as conn:
        sync_ml.set_config(conn, "sync_auto_ultimo_intento",
                           (datetime.utcnow() - timedelta(minutes=31)).isoformat(timespec="seconds"))

    def _no_conectado(tipo="incremental"):
        raise ml_client.MLNoConectado("sin cuenta")

    sync_ml.iniciar_sync = _no_conectado
    chk(sync_ml._tick_scheduler() is None, "sin cuenta conectada el tick devuelve None (no revienta el hilo)")
finally:
    sync_ml.iniciar_sync = _iniciar_real

# ---------------------------------------------------------------------------
# 6. Arranque idempotente del scheduler
# ---------------------------------------------------------------------------
print("\n── Arranque del scheduler ──")
sync_ml.SCHEDULER_TICK_SEG = 3600  # que no haga nada durante el test
chk(sync_ml.iniciar_scheduler() is True, "iniciar_scheduler() arranca el hilo")
chk(sync_ml.scheduler_vivo() is True, "el hilo queda vivo")
chk(sync_ml.iniciar_scheduler() is False,
    "2a llamada NO crea otro hilo (Railway puede re-ejecutar el arranque)")
chk(sync_ml._scheduler_hilo.daemon is True, "el hilo es daemon (no bloquea el apagado del proceso)")

with get_db() as conn:
    est = sync_ml.estado_sync_auto(conn)
chk(est["intervalo_minutos"] == 30 and est["activo"] is True and est["scheduler_vivo"] is True,
    "estado_sync_auto() reporta activo/intervalo/hilo para la UI")
chk("access_token" not in str(est) and "refresh" not in str(est).lower(),
    "estado_sync_auto() no expone nada sensible")

# ---------------------------------------------------------------------------
# 7-9. Reintento de red en ml_client (prerequisito de la sync automática)
# ---------------------------------------------------------------------------
print("\n── Reintento ante errores de red ──")

with get_db() as conn:
    conn.execute(
        """INSERT INTO ml_tokens (id, access_token, refresh_token, token_type, expira_en, obtenido_en, actualizado_en)
           VALUES (1, 'acc-vigente', 'ref-1', 'Bearer', ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET access_token=excluded.access_token,
               refresh_token=excluded.refresh_token, expira_en=excluded.expira_en""",
        ((datetime.utcnow() + timedelta(hours=5)).isoformat(timespec="seconds"),
         datetime.utcnow().isoformat(timespec="seconds"),
         datetime.utcnow().isoformat(timespec="seconds")),
    )

intentos = {"n": 0}


def _transport_falla_una_vez(request):
    intentos["n"] += 1
    if intentos["n"] == 1:
        raise httpx.ConnectError("parpadeo de red", request=request)
    return httpx.Response(200, json={"id": 389112733, "nickname": "RELUVSA AUTOPARTES"})


_http_real = ml_client._http
ml_client._http = lambda: httpx.Client(transport=httpx.MockTransport(_transport_falla_una_vez))
try:
    data = ml_client.get("/users/me")
    chk(data.get("nickname") == "RELUVSA AUTOPARTES",
        "un GET que falla por red se REINTENTA y termina bien (antes tumbaba el backfill)")
    chk(intentos["n"] == 2, "hizo exactamente 2 intentos (1 fallo + 1 exitoso)")
finally:
    ml_client._http = _http_real

# Reintentos agotados → sí falla.
intentos_todos = {"n": 0}


def _transport_siempre_falla(request):
    intentos_todos["n"] += 1
    raise httpx.ConnectError("red caída", request=request)


ml_client._http = lambda: httpx.Client(transport=httpx.MockTransport(_transport_siempre_falla))
try:
    fallo = False
    try:
        ml_client.get("/users/me")
    except ml_client.MLError:
        fallo = True
    chk(fallo, "si la red sigue caída tras los reintentos, SÍ lanza MLError (no se traga el error)")
    chk(intentos_todos["n"] == 1 + sync_ml.ml_client.MAX_REINTENTOS_RED,
        f"reintentos acotados a {ml_client.MAX_REINTENTOS_RED} (no loop infinito)")
finally:
    ml_client._http = _http_real

# El canje/refresh de OAuth NO se reintenta (el refresh token es de un solo uso).
intentos_oauth = {"n": 0}


def _transport_oauth_falla(request):
    intentos_oauth["n"] += 1
    raise httpx.ConnectError("red caída", request=request)


ml_client._http = lambda: httpx.Client(transport=httpx.MockTransport(_transport_oauth_falla))
try:
    try:
        ml_client._post_oauth_token({"grant_type": "refresh_token"})
    except ml_client.MLError:
        pass
    chk(intentos_oauth["n"] == 1,
        "POST /oauth/token NO se reintenta: reintentarlo podría quemar el refresh token (un solo uso)")
finally:
    ml_client._http = _http_real

# ---------------------------------------------------------------------------
# Testigo: nada de esto tocó datos de negocio ni la allowlist
# ---------------------------------------------------------------------------
print("\n── Testigos de seguridad ──")
bloqueado = False
try:
    ml_client._assert_permitido("POST", "https://api.mercadolibre.com/items/MLM123")
except ml_client.MLEscrituraProhibida:
    bloqueado = True
chk(bloqueado, "la allowlist SOLO LECTURA sigue intacta tras los cambios")

with get_db() as conn:
    filas = conn.execute("SELECT metodo, path FROM ml_api_log").fetchall()
chk(all(r["metodo"] == "GET" or r["path"] == "/oauth/token" for r in filas),
    "ml_api_log solo registra GET (+ /oauth/token)")

print("\n" + ("🎉 TODOS LOS CHECKS DE LA SYNC AUTOMÁTICA PASARON" if ok else "❌ HAY CHECKS FALLIDOS"))
sys.exit(0 if ok else 1)
