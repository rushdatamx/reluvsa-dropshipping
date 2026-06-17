"""Carga de Excels de Mercado Libre y detalle de colecta (admin)."""
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from routers.auth import require_admin
from services.parser_ventas_ml import parse_ventas_ml
from services.parser_colecta import parse_colecta
from services.parser_albaran import parse_albaran
from services.detector_archivo import detectar_tipo_xlsx

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

# Mensajes del candado: nombre legible de cada tipo y dónde va.
_NOMBRE_TIPO = {
    "ventas_ml": "Ventas de Mercado Libre",
    "colecta": "Detalle de envíos de colecta",
    "albaran": "Números de albarán",
}


def _validar_tipo(tmp, esperado: str):
    """Candado: rechaza si el contenido del .xlsx no es el tipo esperado.
    Detecta por contenido (robusto al renombrado), no por el nombre del archivo.
    """
    detectado = detectar_tipo_xlsx(tmp)
    if detectado == esperado:
        return
    if detectado is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No reconozco este archivo como un reporte de {_NOMBRE_TIPO[esperado]}. "
                "Verifica que sea el Excel correcto descargado de Mercado Libre."
            ),
        )
    raise HTTPException(
        status_code=400,
        detail=(
            f"Este archivo parece ser de «{_NOMBRE_TIPO[detectado]}», no de "
            f"«{_NOMBRE_TIPO[esperado]}». Súbelo en su sección correspondiente."
        ),
    )

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _save_tmp(file: UploadFile) -> Path:
    suffix = Path(file.filename or "upload.xlsx").suffix or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=UPLOAD_DIR)
    with open(fd, "wb") as out:
        shutil.copyfileobj(file.file, out)
    return Path(tmp_path)


@router.post("/ventas-ml", dependencies=[Depends(require_admin)])
async def subir_ventas_ml(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Se espera archivo .xlsx")
    tmp = _save_tmp(file)
    try:
        _validar_tipo(tmp, "ventas_ml")
        result = parse_ventas_ml(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return result


@router.post("/colecta", dependencies=[Depends(require_admin)])
async def subir_colecta(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Se espera archivo .xlsx")
    tmp = _save_tmp(file)
    try:
        _validar_tipo(tmp, "colecta")
        result = parse_colecta(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return result


@router.post("/albaran", dependencies=[Depends(require_admin)])
async def subir_albaran(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Se espera archivo .xlsx")
    tmp = _save_tmp(file)
    try:
        _validar_tipo(tmp, "albaran")
        result = parse_albaran(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return result
