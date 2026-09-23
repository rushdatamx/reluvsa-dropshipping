"""Regresión del endpoint de verificación del callback de Mercado Libre.

El DevCenter puede consultar la callback con GET o HEAD antes de aceptarla.
Estas verificaciones deben ser inertes: no leen la BD, no procesan webhooks y
no hacen tráfico hacia Mercado Libre.

Uso: backend/.venv/bin/python backend/scripts/test_webhook_callback_verificacion.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from routers.webhooks import router  # noqa: E402


def verificar(metodo: str) -> None:
    app = FastAPI()
    app.include_router(router)
    respuesta = TestClient(app).request(metodo, "/api/webhooks/mercadolibre")
    if respuesta.status_code != 200:
        raise SystemExit(
            f"❌ {metodo} callback devuelve {respuesta.status_code}; se esperaba 200"
        )
    if respuesta.content:
        raise SystemExit(f"❌ {metodo} callback no debe exponer contenido")
    print(f"✅ {metodo} callback devuelve 200 sin contenido")


verificar("GET")
verificar("HEAD")
