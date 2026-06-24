"""Subida y consulta de facturas CFDI (PDF + XML)."""
import csv
import io
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from database import get_db, UPLOADS_DIR
from models import UserInfo
from routers.auth import get_current_user, require_proveedor
from services.parser_cfdi import parse_cfdi_xml
from services.matcher import match_conceptos_a_ventas
from services.folio_factura import formatear_folio
from services.uuid_pdf import extraer_uuid_de_pdf

router = APIRouter(prefix="/api/facturas", tags=["facturas"])

# Carpeta persistente para los archivos de factura (ver UPLOADS_DIR en database.py:
# vive en el volumen de Railway para sobrevivir a los redeploys).
FACTURAS_DIR = UPLOADS_DIR / "facturas"
FACTURAS_DIR.mkdir(parents=True, exist_ok=True)


def _resolver_archivo(path_guardado: Optional[str]) -> Optional[Path]:
    """Resuelve la ruta real de un archivo de factura tolerando que el path guardado
    en BD sea de un contenedor anterior. Estrategia: confiar en el path si existe;
    si no, reintentar por NOMBRE de archivo dentro de FACTURAS_DIR actual (los nombres
    son únicos: '<proveedor>_<timestamp>_<original>'). Devuelve None si no se halla.
    """
    if not path_guardado:
        return None
    p = Path(path_guardado)
    if p.is_file():
        return p
    candidato = FACTURAS_DIR / p.name
    if candidato.is_file():
        return candidato
    return None


@router.get("")
def listar(
    user: UserInfo = Depends(get_current_user),
    proveedor_id: Optional[int] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    sin_cruzar: bool = False,
    q: Optional[str] = None,
):
    if user.rol == "proveedor":
        proveedor_id = user.proveedor_id

    where = []
    params: list = []
    if proveedor_id:
        where.append("f.proveedor_id = ?")
        params.append(proveedor_id)
    if fecha_desde:
        where.append("date(f.fecha_factura) >= date(?)")
        params.append(fecha_desde)
    if fecha_hasta:
        where.append("date(f.fecha_factura) <= date(?)")
        params.append(fecha_hasta)
    if q:
        # Busca por UUID, serie y folio sueltos, y también por serie+folio JUNTOS:
        # el proveedor escribe el "Factura #" como lo ve en su PDF (KIM 'K28027',
        # CAUPLAS '970091508'), que es la combinación de serie y folio. Comparamos
        # contra serie||folio quitando espacios de ambos lados para tolerar separadores.
        where.append(
            "(f.uuid_cfdi LIKE ? OR f.serie LIKE ? OR f.folio LIKE ? "
            " OR REPLACE(COALESCE(f.serie,'')||COALESCE(f.folio,''),' ','') LIKE ? "
            " OR REPLACE(COALESCE(f.folio,'')||COALESCE(f.serie,''),' ','') LIKE ?)"
        )
        term = q.strip()
        like = f"%{term}%"
        like_combinado = f"%{term.replace(' ', '')}%"
        params.extend([like, like, like, like_combinado, like_combinado])
    # Facturas con al menos un concepto sin cruzar a venta (señal de error de facturación).
    if sin_cruzar:
        where.append(
            "EXISTS (SELECT 1 FROM factura_conceptos fc0 "
            "WHERE fc0.factura_id = f.id AND fc0.num_venta_match IS NULL)"
        )

    sql = f"""
        SELECT f.*, p.nombre as proveedor_nombre, p.codigo_bodega,
               (SELECT COUNT(*) FROM factura_conceptos fc WHERE fc.factura_id = f.id) as total_conceptos,
               (SELECT COUNT(*) FROM factura_conceptos fc WHERE fc.factura_id = f.id AND fc.num_venta_match IS NOT NULL) as conceptos_matched
        FROM facturas f
        JOIN proveedores p ON p.id = f.proveedor_id
        {('WHERE ' + ' AND '.join(where)) if where else ''}
        ORDER BY f.fecha_subida DESC
        LIMIT 500
    """
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        d["folio_proveedor"] = formatear_folio(r["codigo_bodega"], r["serie"], r["folio"])
        d["tiene_pdf"] = _resolver_archivo(r["pdf_path"]) is not None
        d["tiene_xml"] = _resolver_archivo(r["xml_path"]) is not None
        out.append(d)
    return out


@router.get("/export.csv")
def export_csv(
    user: UserInfo = Depends(get_current_user),
    proveedor_id: Optional[int] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    sin_cruzar: bool = False,
    q: Optional[str] = None,
):
    """Exporta el listado de facturas (mismos filtros) a CSV."""
    facturas = listar(user, proveedor_id, fecha_desde, fecha_hasta, sin_cruzar, q)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Factura #", "Proveedor", "UUID", "RFC emisor", "Fecha factura", "Total", "Moneda",
        "Conceptos", "Conceptos cruzados", "Tiene PDF", "Tiene XML", "Fecha subida",
    ])
    for f in facturas:
        fecha_fac = (f.get("fecha_factura") or "").split("T")[0]
        w.writerow([
            f.get("folio_proveedor") or "",
            f.get("proveedor_nombre") or "",
            f.get("uuid_cfdi") or "",
            f.get("rfc_emisor") or "",
            fecha_fac,
            f.get("total") if f.get("total") is not None else "",
            f.get("moneda") or "",
            f.get("total_conceptos"),
            f.get("conceptos_matched"),
            "Si" if f.get("tiene_pdf") else "No",
            "Si" if f.get("tiene_xml") else "No",
            (f.get("fecha_subida") or "").split(".")[0],
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=facturas.csv"},
    )


def _factura_autorizada(conn, factura_id: int, user: UserInfo):
    """Devuelve la fila de la factura validando acceso (proveedor solo las suyas)."""
    fac = conn.execute("SELECT * FROM facturas WHERE id = ?", (factura_id,)).fetchone()
    if not fac:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    if user.rol == "proveedor" and fac["proveedor_id"] != user.proveedor_id:
        raise HTTPException(status_code=403, detail="Sin acceso a esta factura")
    return fac


@router.get("/{factura_id}")
def detalle(factura_id: int, user: UserInfo = Depends(get_current_user)):
    with get_db() as conn:
        fac = _factura_autorizada(conn, factura_id, user)
        # Cada concepto con la venta a la que cruzó (num_venta + título), para que
        # el admin vea de un vistazo factura ↔ venta.
        conceptos = conn.execute(
            """SELECT fc.*, v.titulo as venta_titulo, v.fecha_venta as venta_fecha
               FROM factura_conceptos fc
               LEFT JOIN ventas_ml v ON v.num_venta = fc.num_venta_match
               WHERE fc.factura_id = ?
               ORDER BY fc.id""",
            (factura_id,),
        ).fetchall()
        prov = conn.execute(
            "SELECT nombre, codigo_bodega FROM proveedores WHERE id = ?",
            (fac["proveedor_id"],),
        ).fetchone()

    d = dict(fac)
    d["proveedor_nombre"] = prov["nombre"] if prov else None
    d["folio_proveedor"] = formatear_folio(
        prov["codigo_bodega"] if prov else None, fac["serie"], fac["folio"]
    )
    d["tiene_pdf"] = _resolver_archivo(fac["pdf_path"]) is not None
    d["tiene_xml"] = _resolver_archivo(fac["xml_path"]) is not None
    return {"factura": d, "conceptos": [dict(c) for c in conceptos]}


@router.get("/{factura_id}/pdf")
def descargar_pdf(factura_id: int, user: UserInfo = Depends(get_current_user)):
    with get_db() as conn:
        fac = _factura_autorizada(conn, factura_id, user)
    archivo = _resolver_archivo(fac["pdf_path"])
    if not archivo:
        raise HTTPException(status_code=404, detail="Esta factura no tiene PDF disponible")
    return FileResponse(archivo, media_type="application/pdf", filename=archivo.name)


@router.get("/{factura_id}/xml")
def descargar_xml(factura_id: int, user: UserInfo = Depends(get_current_user)):
    with get_db() as conn:
        fac = _factura_autorizada(conn, factura_id, user)
    archivo = _resolver_archivo(fac["xml_path"])
    if not archivo:
        raise HTTPException(status_code=404, detail="Esta factura no tiene XML disponible")
    return FileResponse(archivo, media_type="application/xml", filename=archivo.name)


def _guardar_subida(file: UploadFile, proveedor_id: int, timestamp: str) -> Path:
    """Guarda un UploadFile en FACTURAS_DIR con nombre único y devuelve su ruta."""
    nombre = f"{proveedor_id}_{timestamp}_{file.filename}"
    destino = FACTURAS_DIR / nombre
    with destino.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return destino


def _registrar_factura(conn, parsed: dict, xml_path: Path, pdf_path: Optional[Path],
                       user: UserInfo, prov_row) -> dict:
    """Inserta UNA factura (XML ya parseado + PDF opcional ya emparejado) con su matching.

    Valida RFC y dedup por UUID. Lanza HTTPException (400/409) en caso de error; el
    llamador decide si borra archivos. NO hace commit (lo maneja el context manager).
    Devuelve {factura_id, conceptos}.
    """
    rfc_xml = (parsed.get("rfc_emisor") or "").strip().upper()
    rfc_prov = (prov_row["rfc"] or "").strip().upper() if prov_row else ""
    if not rfc_xml or rfc_xml != rfc_prov:
        raise HTTPException(
            status_code=400,
            detail=(
                f"El RFC emisor del XML ({rfc_xml or 'vacío'}) no corresponde "
                f"a tu proveedor ({rfc_prov or '—'})."
            ),
        )

    existing = conn.execute(
        "SELECT id FROM facturas WHERE uuid_cfdi = ?", (parsed["uuid_cfdi"],)
    ).fetchone()
    if existing:
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

    return {"factura_id": factura_id, "conceptos": len(parsed["conceptos"])}


def _emparejar_pdfs(xmls_info: list, pdfs_guardados: list) -> dict:
    """Empareja cada PDF guardado con el XML correcto.

    Estrategia por PDF (en orden):
      (a) UUID impreso dentro del PDF == uuid_cfdi de algún XML del lote.
      (b) fallback: mismo nombre base de archivo (sin extensión) que el XML original.
    Un PDF que no casa con ningún XML queda huérfano (se reporta, no rompe nada).

    xmls_info: lista de dicts {uuid, xml_basename (nombre original sin ext), idx}
    pdfs_guardados: lista de dicts {path (Path en disco), original (nombre subido)}
    Devuelve {idx_xml: pdf_path} y muta pdfs_guardados marcando 'usado'.
    """
    por_uuid = {x["uuid"].upper(): x["idx"] for x in xmls_info if x.get("uuid")}
    por_nombre = {
        Path(x["xml_basename"]).stem.lower(): x["idx"] for x in xmls_info
    }
    asignados: dict = {}
    for pdf in pdfs_guardados:
        # (a) por UUID impreso
        uuid_pdf = extraer_uuid_de_pdf(pdf["path"])
        idx = por_uuid.get(uuid_pdf.upper()) if uuid_pdf else None
        # (b) fallback por nombre base
        if idx is None:
            stem = Path(pdf["original"]).stem.lower()
            idx = por_nombre.get(stem)
        if idx is not None and idx not in asignados:
            asignados[idx] = pdf["path"]
            pdf["usado"] = True
        else:
            pdf["usado"] = False
    return asignados


@router.post("/upload", dependencies=[Depends(require_proveedor)])
async def upload(
    user: UserInfo = Depends(require_proveedor),
    xml: UploadFile = File(...),
    pdf: Optional[UploadFile] = File(None),
):
    """Sube UNA factura (1 XML + 1 PDF opcional). Se mantiene por compatibilidad;
    internamente delega en la carga múltiple."""
    pdfs = [pdf] if (pdf and pdf.filename) else []
    resumen = await _upload_multiple_impl(user, [xml], pdfs)
    if resumen["registradas"]:
        r0 = resumen["registradas"][0]
        return {"ok": True, "factura_id": r0["factura_id"], "conceptos": r0["conceptos"]}
    # No se registró ninguna: propagar el primer error como antes (400/409).
    err = resumen["errores"][0] if resumen["errores"] else None
    raise HTTPException(status_code=err["status"] if err else 400,
                        detail=err["detail"] if err else "No se pudo registrar la factura")


@router.post("/upload-multiple", dependencies=[Depends(require_proveedor)])
async def upload_multiple(
    user: UserInfo = Depends(require_proveedor),
    xmls: list[UploadFile] = File(...),
    pdfs: list[UploadFile] = File(default=[]),
):
    """Sube VARIAS facturas a la vez. Cada XML es una factura (identificada por su UUID).
    Los PDF se emparejan automáticamente: por UUID impreso y, si no, por nombre de archivo.
    Un PDF que no casa con ningún XML se ignora y se reporta. El proceso nunca se rompe
    por un archivo malo: cada XML se procesa de forma independiente."""
    return await _upload_multiple_impl(user, xmls, pdfs)


async def _upload_multiple_impl(user: UserInfo, xmls: list, pdfs: list) -> dict:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 1) Guardar y parsear cada XML. Los que no son CFDI válido → error individual.
    xmls_info = []     # {idx, path, parsed, uuid, xml_basename}
    errores = []       # {archivo, status, detail}
    for i, xml in enumerate(xmls):
        if not xml.filename or not xml.filename.lower().endswith(".xml"):
            errores.append({"archivo": xml.filename or f"xml#{i}", "status": 400,
                            "detail": "No es un archivo .xml"})
            continue
        path = _guardar_subida(xml, user.proveedor_id, f"{timestamp}_{i}")
        try:
            parsed = parse_cfdi_xml(path)
        except ValueError as exc:
            path.unlink(missing_ok=True)
            errores.append({"archivo": xml.filename, "status": 400, "detail": str(exc)})
            continue
        except Exception as exc:
            path.unlink(missing_ok=True)
            errores.append({"archivo": xml.filename, "status": 400,
                            "detail": f"No se pudo leer el XML: {exc}"})
            continue
        xmls_info.append({"idx": len(xmls_info), "path": path, "parsed": parsed,
                          "uuid": parsed.get("uuid_cfdi") or "", "xml_basename": xml.filename})

    # 2) Guardar PDFs y emparejarlos con los XML.
    pdfs_guardados = []
    for i, pdf in enumerate(pdfs or []):
        if not pdf or not pdf.filename or not pdf.filename.lower().endswith(".pdf"):
            continue
        path = _guardar_subida(pdf, user.proveedor_id, f"{timestamp}_p{i}")
        pdfs_guardados.append({"path": path, "original": pdf.filename, "usado": False})

    asignados = _emparejar_pdfs(xmls_info, pdfs_guardados)

    # 3) Registrar cada factura en BD (RFC + dedup). Errores son por-factura.
    registradas = []
    with get_db() as conn:
        prov = conn.execute(
            "SELECT rfc, nombre FROM proveedores WHERE id = ?", (user.proveedor_id,)
        ).fetchone()
        for x in xmls_info:
            pdf_path = asignados.get(x["idx"])
            try:
                res = _registrar_factura(conn, x["parsed"], x["path"], pdf_path, user, prov)
                registradas.append({**res, "archivo": x["xml_basename"],
                                    "con_pdf": pdf_path is not None})
            except HTTPException as exc:
                # Limpiar archivos de esta factura fallida (el XML; el PDF se limpia abajo
                # si quedó huérfano, pero si se asignó a esta factura fallida lo soltamos).
                x["path"].unlink(missing_ok=True)
                if pdf_path:
                    Path(pdf_path).unlink(missing_ok=True)
                    # marcar el pdf como no-usado para no contarlo doble
                    for p in pdfs_guardados:
                        if p["path"] == pdf_path:
                            p["usado"] = False
                errores.append({"archivo": x["xml_basename"], "status": exc.status_code,
                                "detail": exc.detail})

    # 4) PDFs que no se emparejaron con ningún XML: se descartan (se avisa).
    pdfs_huerfanos = []
    for p in pdfs_guardados:
        if not p["usado"]:
            p["path"].unlink(missing_ok=True)
            pdfs_huerfanos.append(p["original"])

    return {
        "ok": True,
        "registradas": registradas,
        "errores": errores,
        "pdfs_sin_emparejar": pdfs_huerfanos,
        "resumen": {
            "facturas_registradas": len(registradas),
            "con_pdf": sum(1 for r in registradas if r["con_pdf"]),
            "errores": len(errores),
            "pdfs_sin_emparejar": len(pdfs_huerfanos),
        },
    }
