"""Seguimiento interno de devoluciones reales de Mercado Libre (sólo admin)."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from database import UPLOADS_DIR, get_db
from models import UserInfo
from routers.auth import require_admin

router = APIRouter(prefix="/api/devoluciones", tags=["devoluciones"], dependencies=[Depends(require_admin)])
EVIDENCE_DIR = UPLOADS_DIR / "devoluciones"
MAX_FILE = 10 * 1024 * 1024
TIPOS = {b"\xff\xd8\xff": ("image/jpeg", ".jpg"), b"\x89PNG\r\n\x1a\n": ("image/png", ".png"), b"%PDF-": ("application/pdf", ".pdf")}


class Seguimiento(BaseModel):
    mediacion: Optional[bool] = None
    revisado: Optional[bool] = None


def _caso(conn, cuenta, claim_id):
    row = conn.execute("SELECT * FROM ml_devoluciones WHERE cuenta_ml=? AND claim_id=?", (cuenta, claim_id)).fetchone()
    if not row:
        raise HTTPException(404, "Devolución no encontrada")
    item = dict(row)
    if item["order_id"]:
        ventas = conn.execute("""SELECT num_venta, num_venta_origen, pack_id, sku, titulo, deposito,
            albaran, fecha_albaran FROM ventas_ml WHERE cuenta_ml=? AND num_venta_origen=?""",
            (cuenta, item["order_id"])).fetchall()
    elif item["pack_id"]:
        ventas = conn.execute("""SELECT num_venta, num_venta_origen, pack_id, sku, titulo, deposito,
            albaran, fecha_albaran FROM ventas_ml WHERE cuenta_ml=? AND pack_id=?""",
            (cuenta, item["pack_id"])).fetchall()
    else:
        ventas = []
    item["ventas"] = [dict(v) for v in ventas]
    item["sin_cruce"] = not bool(ventas)
    item["numero_venta"] = (item["ventas"][0].get("pack_id") or item["ventas"][0]["num_venta_origen"]) if ventas else None
    item["sku"] = ", ".join(dict.fromkeys(v["sku"] for v in ventas if v["sku"])) or None
    item["almacen"] = ", ".join(dict.fromkeys(v["deposito"] for v in ventas if v["deposito"])) or None
    item["albaran"] = ", ".join(dict.fromkeys(v["albaran"] for v in ventas if v["albaran"])) or None
    item["fecha_albaran"] = next((v["fecha_albaran"] for v in ventas if v["fecha_albaran"]), None)
    item["productos"] = json.loads(item.pop("productos_json") or "[]")
    item.pop("raw_claim", None)
    item.pop("raw_return", None)
    return item


@router.get("")
def listar(cuenta: Optional[str] = None, almacen: Optional[str] = None,
           mediacion: Optional[bool] = None, revisado: Optional[bool] = None):
    with get_db() as conn:
        rows = conn.execute("SELECT cuenta_ml, claim_id FROM ml_devoluciones").fetchall()
        all_items = [_caso(conn, r["cuenta_ml"], r["claim_id"]) for r in rows]
    almacenes = sorted({a for i in all_items for a in (i["almacen"] or "").split(", ") if a})
    items = all_items
    items = [i for i in items if (not cuenta or i["cuenta_ml"] == cuenta)
             and (not almacen or almacen in (i["almacen"] or "").split(", "))
             and (mediacion is None or bool(i["mediacion"]) == mediacion)
             and (revisado is None or bool(i["revisado"]) == revisado)]
    items.sort(key=lambda i: (not bool(i["fecha_limite"]), i["fecha_limite"] or "9999", i["claim_id"]))
    return {"total": len(items), "almacenes": almacenes, "sin_revisar": sum(not i["revisado"] for i in items),
            "sin_mediacion": sum(not i["mediacion"] for i in items), "items": items}


@router.get("/{cuenta}/{claim_id}")
def detalle(cuenta: str, claim_id: str):
    with get_db() as conn:
        item = _caso(conn, cuenta, claim_id)
        item["evidencias"] = [dict(r) for r in conn.execute("""SELECT id, nombre_original, tipo, tamano,
            usuario, creado_en FROM ml_devolucion_archivos WHERE cuenta_ml=? AND claim_id=? ORDER BY id""",
            (cuenta, claim_id))]
    return item


@router.patch("/{cuenta}/{claim_id}")
def seguimiento(cuenta: str, claim_id: str, payload: Seguimiento, user: UserInfo = Depends(require_admin)):
    if payload.mediacion is None and payload.revisado is None:
        raise HTTPException(400, "Indica una marca para actualizar")
    with get_db() as conn:
        _caso(conn, cuenta, claim_id)
        conn.execute("""UPDATE ml_devoluciones SET mediacion=COALESCE(?, mediacion),
            revisado=COALESCE(?, revisado), seguimiento_usuario=?, seguimiento_fecha=?
            WHERE cuenta_ml=? AND claim_id=?""",
            (int(payload.mediacion) if payload.mediacion is not None else None,
             int(payload.revisado) if payload.revisado is not None else None,
             user.email, datetime.now(timezone.utc).isoformat(), cuenta, claim_id))
        return _caso(conn, cuenta, claim_id)


@router.post("/{cuenta}/{claim_id}/evidencias")
async def subir_evidencia(cuenta: str, claim_id: str, file: UploadFile = File(...),
                         user: UserInfo = Depends(require_admin)):
    with get_db() as conn:
        _caso(conn, cuenta, claim_id)
        count = conn.execute("SELECT COUNT(*) FROM ml_devolucion_archivos WHERE cuenta_ml=? AND claim_id=?",
                             (cuenta, claim_id)).fetchone()[0]
        if count >= 10:
            raise HTTPException(400, "Máximo 10 archivos por devolución")
    content = await file.read(MAX_FILE + 1)
    if len(content) > MAX_FILE:
        raise HTTPException(413, "Máximo 10 MB por archivo")
    detected = next((value for magic, value in TIPOS.items() if content.startswith(magic)), None)
    valid = detected and (
        (detected[0] == "image/jpeg" and content.endswith(b"\xff\xd9")) or
        (detected[0] == "image/png" and b"IEND" in content[-32:]) or
        (detected[0] == "application/pdf" and b"%%EOF" in content[-1024:])
    )
    if not valid:
        raise HTTPException(400, "Se admiten JPG, PNG y PDF válidos")
    mime, extension = detected
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    server_name = uuid.uuid4().hex + extension
    target = EVIDENCE_DIR / server_name
    nombre_original = Path((file.filename or "evidencia").replace("\\", "/")).name
    nombre_original = "".join(char for char in nombre_original if char.isprintable())[:200] or "evidencia"
    try:
        target.write_bytes(content)
        with get_db() as conn:
            conn.execute("""INSERT INTO ml_devolucion_archivos
                (cuenta_ml, claim_id, nombre_original, nombre_servidor, tipo, tamano, usuario, creado_en)
                VALUES (?,?,?,?,?,?,?,?)""",
                (cuenta, claim_id, nombre_original, server_name,
                 mime, len(content), user.email, datetime.now(timezone.utc).isoformat()))
    except sqlite3.IntegrityError:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "Máximo 10 archivos por devolución")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {"ok": True}


@router.get("/{cuenta}/{claim_id}/evidencias/{archivo_id}")
def descargar_evidencia(cuenta: str, claim_id: str, archivo_id: int):
    with get_db() as conn:
        row = conn.execute("""SELECT nombre_servidor, nombre_original, tipo FROM ml_devolucion_archivos
            WHERE id=? AND cuenta_ml=? AND claim_id=?""", (archivo_id, cuenta, claim_id)).fetchone()
    if not row:
        raise HTTPException(404, "Archivo no encontrado")
    target = EVIDENCE_DIR / row["nombre_servidor"]
    if not target.is_file():
        raise HTTPException(404, "Archivo no disponible")
    return FileResponse(target, media_type=row["tipo"], filename=row["nombre_original"])
