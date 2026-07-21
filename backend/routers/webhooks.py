"""
Receptor de webhooks (notificaciones) de Mercado Libre.

ML manda POST {_id, resource, user_id, topic, attempts, sent, received} a la callback
URL configurada en el DevCenter y exige HTTP 200 en <=500 ms; si no, desactiva los
tópicos. Por eso este endpoint SOLO guarda la notificación en ml_notificaciones y
contesta — nunca consulta la API de ML ni procesa nada aquí. El estado completo del
recurso lo pedirá el job de sync (GET {resource}) leyendo procesada=0.

Sin auth: ML no manda ningún token/firma en la notificación (la URL es el secreto).
Cualquier payload inválido igual responde 200 para no disparar el fallback de ML;
se guarda el body crudo para diagnóstico.
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from database import get_db
from routers.auth import require_admin

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/mercadolibre")
async def recibir_notificacion_ml(request: Request):
    raw = (await request.body()).decode("utf-8", errors="replace")
    try:
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            data = {}
    except ValueError:
        data = {}

    with get_db() as conn:
        conn.execute(
            """INSERT INTO ml_notificaciones
               (notif_id, topic, resource, user_id, attempts, sent, raw_body, recibido_en)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(data.get("_id")) if data.get("_id") is not None else None,
                data.get("topic"),
                data.get("resource"),
                str(data.get("user_id")) if data.get("user_id") is not None else None,
                data.get("attempts"),
                data.get("sent"),
                raw,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )

    return {"ok": True}


@router.get("/mercadolibre/recientes", dependencies=[Depends(require_admin)])
def notificaciones_recientes(limit: int = Query(default=50, le=500)):
    """Últimas notificaciones recibidas — para verificar que ML sí está llegando."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, notif_id, topic, resource, user_id, attempts, sent,
                      recibido_en, procesada
               FROM ml_notificaciones
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS c FROM ml_notificaciones").fetchone()["c"]
    return {"total": total, "notificaciones": [dict(r) for r in rows]}
