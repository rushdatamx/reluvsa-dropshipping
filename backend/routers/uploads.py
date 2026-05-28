"""Carga de Excels de Mercado Libre y detalle de colecta (admin)."""
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from routers.auth import require_admin
from services.parser_ventas_ml import parse_ventas_ml
from services.parser_colecta import parse_colecta

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

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
        result = parse_colecta(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return result
