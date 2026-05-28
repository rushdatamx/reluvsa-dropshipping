"""Subida y consulta de facturas CFDI (PDF + XML)."""
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from database import get_db
from models import UserInfo
from routers.auth import get_current_user, require_proveedor
from services.parser_cfdi import parse_cfdi_xml
from services.matcher import match_conceptos_a_ventas

router = APIRouter(prefix="/api/facturas", tags=["facturas"])

FACTURAS_DIR = Path(__file__).parent.parent / "uploads" / "facturas"
FACTURAS_DIR.mkdir(parents=True, exist_ok=True)


@router.get("")
def listar(user: UserInfo = Depends(get_current_user), proveedor_id: Optional[int] = None):
    if user.rol == "proveedor":
        proveedor_id = user.proveedor_id

    where = []
    params: list = []
    if proveedor_id:
        where.append("f.proveedor_id = ?")
        params.append(proveedor_id)

    sql = f"""
        SELECT f.*, p.nombre as proveedor_nombre,
               (SELECT COUNT(*) FROM factura_conceptos fc WHERE fc.factura_id = f.id) as total_conceptos,
               (SELECT COUNT(*) FROM factura_conceptos fc WHERE fc.factura_id = f.id AND fc.num_venta_match IS NOT NULL) as conceptos_matched
        FROM facturas f
        JOIN proveedores p ON p.id = f.proveedor_id
        {('WHERE ' + ' AND '.join(where)) if where else ''}
        ORDER BY f.fecha_subida DESC
        LIMIT 200
    """
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


@router.get("/{factura_id}")
def detalle(factura_id: int, user: UserInfo = Depends(get_current_user)):
    with get_db() as conn:
        fac = conn.execute("SELECT * FROM facturas WHERE id = ?", (factura_id,)).fetchone()
        if not fac:
            raise HTTPException(status_code=404, detail="Factura no encontrada")
        if user.rol == "proveedor" and fac["proveedor_id"] != user.proveedor_id:
            raise HTTPException(status_code=403, detail="Sin acceso a esta factura")
        conceptos = conn.execute(
            "SELECT * FROM factura_conceptos WHERE factura_id = ?", (factura_id,)
        ).fetchall()
    return {"factura": dict(fac), "conceptos": [dict(c) for c in conceptos]}


@router.post("/upload", dependencies=[Depends(require_proveedor)])
async def upload(
    user: UserInfo = Depends(require_proveedor),
    xml: UploadFile = File(...),
    pdf: Optional[UploadFile] = File(None),
):
    if not xml.filename or not xml.filename.lower().endswith(".xml"):
        raise HTTPException(status_code=400, detail="Se requiere XML del CFDI")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    xml_filename = f"{user.proveedor_id}_{timestamp}_{xml.filename}"
    xml_path = FACTURAS_DIR / xml_filename
    with xml_path.open("wb") as f:
        shutil.copyfileobj(xml.file, f)

    pdf_path = None
    if pdf and pdf.filename and pdf.filename.lower().endswith(".pdf"):
        pdf_filename = f"{user.proveedor_id}_{timestamp}_{pdf.filename}"
        pdf_path = FACTURAS_DIR / pdf_filename
        with pdf_path.open("wb") as f:
            shutil.copyfileobj(pdf.file, f)

    try:
        parsed = parse_cfdi_xml(xml_path)
    except Exception as exc:
        xml_path.unlink(missing_ok=True)
        if pdf_path:
            pdf_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"XML inválido: {exc}")

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM facturas WHERE uuid_cfdi = ?", (parsed["uuid_cfdi"],)
        ).fetchone()
        if existing:
            xml_path.unlink(missing_ok=True)
            if pdf_path:
                pdf_path.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=f"Factura ya registrada (id {existing['id']})")

        cur = conn.execute(
            """INSERT INTO facturas (proveedor_id, uuid_cfdi, serie, folio, rfc_emisor, rfc_receptor,
                                     fecha_factura, total, moneda, pdf_path, xml_path, subido_por)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user.proveedor_id,
                parsed["uuid_cfdi"],
                parsed.get("serie"),
                parsed.get("folio"),
                parsed.get("rfc_emisor"),
                parsed.get("rfc_receptor"),
                parsed.get("fecha"),
                parsed.get("total"),
                parsed.get("moneda"),
                str(pdf_path) if pdf_path else None,
                str(xml_path),
                user.user_id,
            ),
        )
        factura_id = cur.lastrowid

        for c in parsed["conceptos"]:
            match = match_conceptos_a_ventas(conn, user.proveedor_id, c)
            conn.execute(
                """INSERT INTO factura_conceptos (factura_id, codigo_prov, descripcion, cantidad,
                                                  precio_unitario, importe, num_venta_match, match_method, match_confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    factura_id,
                    c.get("codigo"),
                    c.get("descripcion"),
                    c.get("cantidad"),
                    c.get("precio_unitario"),
                    c.get("importe"),
                    match.get("num_venta") if match else None,
                    match.get("method") if match else None,
                    match.get("confidence") if match else None,
                ),
            )

    return {"ok": True, "factura_id": factura_id, "conceptos": len(parsed["conceptos"])}
