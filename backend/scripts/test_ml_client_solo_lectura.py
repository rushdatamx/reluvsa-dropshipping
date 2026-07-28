"""Test del guardián de solo-lectura del cliente de la API de Mercado Libre.

Verifica SIN RED (httpx.MockTransport) que ml_client:
1. Permite GET a api.mercadolibre.com y POST /oauth/token — nada más.
2. Bloquea POST/PUT/DELETE/PATCH a recursos ML y cualquier otro host/esquema
   ANTES de tocar la red (el transport no recibe ni un request).
3. Ante 401 refresca el token y reintenta una vez; access+refresh nuevos quedan
   persistidos juntos en ml_tokens.
4. Refresh concurrente (2 hilos, token vencido): exactamente UN POST de refresh
   (el refresh token de ML es de un solo uso).
5. Ningún token aparece en ml_api_log (query filtrada).
6. Verificación ESTÁTICA: ningún otro archivo del backend menciona
   api.mercadolibre.com ni importa httpx — ml_client es el único punto de salida.

Uso: backend/.venv/bin/python backend/scripts/test_ml_client_solo_lectura.py
"""
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["ML_CLIENT_ID"] = "TEST_APP_ID"
os.environ["ML_CLIENT_SECRET"] = "TEST_APP_SECRET"
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx  # noqa: E402

import database  # noqa: E402
from services import ml_client  # noqa: E402
from services.ml_client import (  # noqa: E402
    API_BASE,
    MLEscrituraProhibida,
)


def ok(cond, msg):
    print(("✅" if cond else "❌") + " " + msg)
    if not cond:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Transport mock: registra todo lo que "sale a la red"
# ---------------------------------------------------------------------------
REQUESTS = []          # [(metodo, url, headers)]
REFRESH_COUNT = [0]


def _handler(request: httpx.Request) -> httpx.Response:
    REQUESTS.append((request.method, str(request.url), dict(request.headers)))
    path = request.url.path

    if request.method == "POST" and path == "/oauth/token":
        REFRESH_COUNT[0] += 1
        n = REFRESH_COUNT[0]
        return httpx.Response(200, json={
            "access_token": f"ACCESS_NUEVO_{n}",
            "refresh_token": f"REFRESH_NUEVO_{n}",
            "token_type": "Bearer",
            "expires_in": 21600,
            "scope": "read offline_access",
            "user_id": 999888777,
        })

    auth = request.headers.get("authorization", "")
    if "ACCESS_VIEJO_401" in auth:
        return httpx.Response(401, json={"message": "invalid token"})
    return httpx.Response(200, json={"ok": True, "path": path})


def _instalar_mock():
    transport = httpx.MockTransport(_handler)
    ml_client._http = lambda: httpx.Client(transport=transport, timeout=30.0)


def _sembrar_token(access="ACCESS_VALIDO", refresh="REFRESH_VALIDO", vencido=False):
    delta = timedelta(hours=-1) if vencido else timedelta(hours=5)
    expira = (datetime.utcnow() + delta).isoformat(timespec="seconds")
    ahora = datetime.utcnow().isoformat(timespec="seconds")
    with database.get_db() as conn:
        conn.execute("DELETE FROM ml_tokens")
        conn.execute(
            """INSERT INTO ml_tokens (id, access_token, refresh_token, token_type, scope,
                                      ml_user_id, expira_en, obtenido_en, actualizado_en)
               VALUES (1, ?, ?, 'Bearer', 'read', '999888777', ?, ?, ?)""",
            (access, refresh, expira, ahora, ahora),
        )


def main():
    database.init_database()
    _instalar_mock()

    # --- 1) GET permitido pasa ---
    _sembrar_token()
    r = ml_client.get("/users/me")
    ok(r.get("ok") is True, "GET /users/me permitido y respondido")
    ok(REQUESTS[-1][0] == "GET" and "api.mercadolibre.com" in REQUESTS[-1][1],
       "el GET salió a api.mercadolibre.com")

    # --- 2) Escrituras bloqueadas ANTES de tocar la red ---
    antes = len(REQUESTS)
    bloqueados = 0
    for metodo in ("POST", "PUT", "DELETE", "PATCH"):
        try:
            ml_client._request(metodo, API_BASE + "/items/MLM12345")
        except MLEscrituraProhibida:
            bloqueados += 1
    ok(bloqueados == 4, "POST/PUT/DELETE/PATCH a recursos ML lanzan MLEscrituraProhibida")
    ok(len(REQUESTS) == antes, "ninguna escritura bloqueada llegó a la red")

    # --- 3) Hosts/esquemas ajenos bloqueados ---
    antes = len(REQUESTS)
    bloqueados = 0
    for url in ("https://evil.com/orders", "http://api.mercadolibre.com/orders",
                "https://api.mercadolibre.com.evil.com/orders"):
        try:
            ml_client._request("GET", url)
        except MLEscrituraProhibida:
            bloqueados += 1
    ok(bloqueados == 3, "GET a host ajeno / http sin TLS / host disfrazado → bloqueados")
    ok(len(REQUESTS) == antes, "ningún request a host ajeno llegó a la red")

    # --- 4) POST /oauth/token SÍ permitido (única excepción) ---
    resp = ml_client._request("POST", API_BASE + "/oauth/token",
                              data={"grant_type": "refresh_token"})
    ok(resp.status_code == 200, "POST /oauth/token permitido (única excepción de la allowlist)")

    # --- 5) 401 → refresh + retry, tokens nuevos persistidos JUNTOS ---
    _sembrar_token(access="ACCESS_VIEJO_401", refresh="REFRESH_VIEJO")
    refresh_antes = REFRESH_COUNT[0]
    r = ml_client.get("/orders/search", params={"seller": "999888777"})
    ok(r.get("ok") is True, "GET con token rechazado (401) termina bien tras refresh+retry")
    ok(REFRESH_COUNT[0] == refresh_antes + 1, "hubo exactamente 1 refresh")
    with database.get_db() as conn:
        row = conn.execute("SELECT * FROM ml_tokens WHERE id=1").fetchone()
    ok(row["access_token"].startswith("ACCESS_NUEVO_")
       and row["refresh_token"].startswith("REFRESH_NUEVO_"),
       "access y refresh NUEVOS quedaron persistidos juntos en ml_tokens")

    # --- 6) Refresh concurrente: 2 hilos, token vencido → UN solo refresh ---
    _sembrar_token(access="ACCESS_EXPIRADO", refresh="REFRESH_UNICO", vencido=True)
    refresh_antes = REFRESH_COUNT[0]
    resultados = []

    def _pedir():
        resultados.append(ml_client._token_valido())

    hilos = [threading.Thread(target=_pedir) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    ok(REFRESH_COUNT[0] == refresh_antes + 1,
       "refresh concurrente (2 hilos, token vencido) → exactamente 1 POST de refresh")
    ok(len(set(resultados)) == 1, "ambos hilos terminaron con el MISMO access token")

    # --- 7) Ningún token en ml_api_log (query filtrada) ---
    ml_client.get("/users/me", params={"access_token": "SECRETO_NO_LOGUEABLE", "limit": 5})
    with database.get_db() as conn:
        logs = conn.execute("SELECT metodo, path, error FROM ml_api_log").fetchall()
    volcado = json.dumps([dict(r) for r in logs])
    tokens_filtrados = all(
        s not in volcado
        for s in ("SECRETO_NO_LOGUEABLE", "ACCESS_NUEVO", "REFRESH_NUEVO",
                  "ACCESS_VALIDO", "REFRESH_VALIDO", "TEST_APP_SECRET")
    )
    ok(tokens_filtrados, "ningún token/secreto aparece en ml_api_log")
    ok(all(r["metodo"] in ("GET", "POST") for r in logs), "ml_api_log solo registra GET y POST(/oauth/token)")

    # --- 8) Verificación estática: ml_client es el ÚNICO punto de salida ---
    backend_dir = Path(__file__).parent.parent
    excluidos = {
        backend_dir / "services" / "ml_client.py",
        # Tests que usan httpx.MockTransport para simular la API (nunca red real):
        backend_dir / "scripts" / "test_sync_ml_e2e.py",
        Path(__file__).resolve(),
    }
    infractores = []
    for py in backend_dir.rglob("*.py"):
        if ".venv" in py.parts or py.resolve() in {p.resolve() for p in excluidos}:
            continue
        texto = py.read_text(encoding="utf-8", errors="replace")
        if "api.mercadolibre.com" in texto:
            infractores.append(f"{py.name}: menciona api.mercadolibre.com")
        if "import httpx" in texto or "from httpx" in texto:
            infractores.append(f"{py.name}: importa httpx")
    ok(not infractores,
       "estática: ningún archivo fuera de ml_client toca httpx/api.mercadolibre.com"
       + ("" if not infractores else f" → {infractores}"))

    print("\n🎉 TODOS LOS CHECKS DEL GUARDIÁN DE SOLO-LECTURA PASARON")


if __name__ == "__main__":
    try:
        main()
    finally:
        Path(_tmpdb).unlink(missing_ok=True)
