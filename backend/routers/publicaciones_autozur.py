"""Publicaciones Autozur: cruce local de dos archivos Excel, sin API de ML."""
import json
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from routers.auth import require_admin
from services.autozur_compatibilidades import (
    SESSION_DIR, analizar, cargar_sesion, escribir_resultado, guardar_sesion,
    leer_catalogo, leer_publicaciones, resumen_para_cliente,
)

router = APIRouter(prefix="/api/publicaciones-autozur", tags=["publicaciones-autozur"])


def _guardar(file: UploadFile) -> Path:
    if Path(file.filename or "").suffix.lower() != ".xlsx":
        raise HTTPException(status_code=400, detail="Autozur requiere archivos .xlsx.")
    fd, nombre = tempfile.mkstemp(suffix=".xlsx", dir=SESSION_DIR)
    with open(fd, "wb") as salida:
        shutil.copyfileobj(file.file, salida)
    return Path(nombre)


@router.post("/analizar")
def analizar_archivos(
    publicaciones: UploadFile = File(...),
    catalogo: UploadFile = File(...),
    _=Depends(require_admin),
):
    ruta_publicaciones = _guardar(publicaciones)
    ruta_catalogo = _guardar(catalogo)
    try:
        try:
            filas_publicaciones = leer_publicaciones(ruta_publicaciones)
            indice_catalogo = leer_catalogo(ruta_catalogo)
            resultado = analizar(filas_publicaciones, indice_catalogo)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="No pude leer los archivos de Autozur. Verifica que correspondan a las plantillas esperadas.",
            )
        session_id = guardar_sesion(filas_publicaciones, resultado)
        return resumen_para_cliente(session_id, resultado)
    finally:
        ruta_publicaciones.unlink(missing_ok=True)
        ruta_catalogo.unlink(missing_ok=True)


@router.post("/generar")
def generar_archivo(
    session_id: str = Form(...),
    aprobadas_revision: str = Form("[]"),
    excluidas: str = Form("[]"),
    _=Depends(require_admin),
):
    try:
        aprobadas = json.loads(aprobadas_revision)
        ids_excluidas = json.loads(excluidas)
        if not isinstance(aprobadas, list) or not isinstance(ids_excluidas, list):
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Las revisiones aprobadas no son una lista válida.")
    try:
        session = cargar_sesion(session_id)
        fd, destino = tempfile.mkstemp(suffix=".xlsx", dir=SESSION_DIR)
        os.close(fd)
        Path(destino).unlink(missing_ok=True)
        total = escribir_resultado(session, aprobadas, ids_excluidas, destino)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return FileResponse(
        destino,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"compatibilidades_autozur_{total}.xlsx",
        headers={"X-Compatibilidades-Generadas": str(total)},
        background=BackgroundTask(Path(destino).unlink, missing_ok=True),
    )


@router.get("/sesiones/{session_id}/resultados/{resultado_id}")
def detalle_resultado(session_id: str, resultado_id: int, _=Depends(require_admin)):
    """Entrega las propuestas completas sólo cuando Gaby abre una revisión."""
    try:
        session = cargar_sesion(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    resultados = session.get("resultados", [])
    if resultado_id < 0 or resultado_id >= len(resultados):
        raise HTTPException(status_code=404, detail="La publicación no pertenece a esta sesión.")
    resultado = resultados[resultado_id]
    if resultado.get("id") != resultado_id:
        raise HTTPException(status_code=409, detail="La sesión de análisis está dañada; vuelve a analizar.")
    return {
        "id": resultado_id,
        "compatibilidades": resultado.get("candidatos", []),
    }
