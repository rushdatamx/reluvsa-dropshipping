"""Conciliación auditable entre la exportación ERP y CFDI cargados."""
import csv
import io
import json
import shutil
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from database import UPLOADS_DIR, get_db
from models import UserInfo
from routers.auth import require_admin
from services.detector_archivo import detectar_tipo_xlsx
from services.parser_erp import parse_erp

router = APIRouter(prefix="/api/conciliacion-erp", tags=["conciliacion-erp"])


def _normalizar_factura(codigo: str, serie: str, folio: str) -> str:
    """Llave ERP: KIM usa Serie+Folio; CAUPLAS sólo Folio."""
    base = (folio or "") if codigo == "CAUPLAS" else (serie or "") + (folio or "")
    return "".join(str(base).upper().split()).replace("-", "")


def _carga(conn, carga_id: Optional[int]):
    if carga_id is None:
        row = conn.execute("SELECT * FROM cargas_erp ORDER BY id DESC LIMIT 1").fetchone()
    else:
        row = conn.execute("SELECT * FROM cargas_erp WHERE id = ?", (carga_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No hay una carga ERP disponible")
    return dict(row)


def _filtrar(items, proveedor=None, estado=None, fecha_desde=None, fecha_hasta=None, q=None):
    q = (q or "").strip().upper()
    out = []
    for item in items:
        if proveedor and item.get("proveedor_codigo") != proveedor:
            continue
        if estado and item["estado"] != estado:
            continue
        fecha = item.get("fecha_referencia") or item.get("fecha_factura") or ""
        if fecha_desde and fecha < fecha_desde:
            continue
        if fecha_hasta and fecha > fecha_hasta:
            continue
        searchable = " ".join(str(item.get(k) or "") for k in (
            "referencia_original", "referencia_normalizada", "proveedor_original",
            "factura_serie", "factura_folio", "uuid_cfdi",
        )).upper()
        if q and q not in searchable:
            continue
        out.append(item)
    return out


def _resultados(conn, carga_id=None, proveedor=None, estado=None, fecha_desde=None, fecha_hasta=None, q=None):
    carga = _carga(conn, carga_id)
    entradas = [dict(r) for r in conn.execute(
        "SELECT * FROM entradas_erp WHERE carga_id = ? ORDER BY numero_fila", (carga["id"],)
    )]
    facturas = [dict(r) for r in conn.execute(
        """SELECT f.id, f.proveedor_id, f.serie, f.folio, f.uuid_cfdi, f.fecha_factura,
                  p.codigo_bodega, p.nombre AS proveedor_nombre
             FROM facturas f JOIN proveedores p ON p.id = f.proveedor_id
             WHERE p.codigo_bodega IN ('KIM', 'CAUPLAS')
               AND date(f.fecha_factura) BETWEEN date(?) AND date(?)""",
        (carga["fecha_referencia_desde"], carga["fecha_referencia_hasta"]),
    )]
    facturas_por_llave = {}
    for factura in facturas:
        llave = (factura["codigo_bodega"], _normalizar_factura(factura["codigo_bodega"], factura["serie"], factura["folio"]))
        # No elegimos silenciosamente si el XML tiene la misma llave dos veces.
        facturas_por_llave.setdefault(llave, []).append(factura)

    conteos = Counter(
        (e["proveedor_codigo"], e["referencia_normalizada"])
        for e in entradas if e["proveedor_codigo"] and e["referencia_normalizada"]
    )
    matched_invoice_ids = set()
    items = []
    for entrada in entradas:
        llave = (entrada["proveedor_codigo"], entrada["referencia_normalizada"])
        candidatas = facturas_por_llave.get(llave, []) if not entrada["motivo_revision"] else []
        factura = candidatas[0] if len(candidatas) == 1 else None
        if entrada["motivo_revision"]:
            item_estado = "Para revisar"
        elif factura:
            item_estado = "Confirmada"
            matched_invoice_ids.add(factura["id"])
        else:
            item_estado = "Pendiente de proveedor"
        item = {
            "tipo": "entrada_erp", "id": entrada["id"], "carga_id": carga["id"],
            "estado": item_estado, "duplicada_erp": conteos[llave] > 1,
            **entrada,
            "factura_id": factura["id"] if factura else None,
            "factura_serie": factura["serie"] if factura else None,
            "factura_folio": factura["folio"] if factura else None,
            "uuid_cfdi": factura["uuid_cfdi"] if factura else None,
            "fecha_factura": factura["fecha_factura"] if factura else None,
        }
        if len(candidatas) > 1:
            item["estado"] = "Para revisar"
            item["motivo_revision"] = "Más de un XML coincide con esta referencia"
        items.append(item)

    for factura in facturas:
        if factura["id"] in matched_invoice_ids:
            continue
        items.append({
            "tipo": "factura_sin_erp", "id": f"factura-{factura['id']}", "carga_id": carga["id"],
            "estado": "Factura sin entrada ERP", "duplicada_erp": False,
            "proveedor_codigo": factura["codigo_bodega"], "proveedor_original": factura["proveedor_nombre"],
            "referencia_original": _normalizar_factura(factura["codigo_bodega"], factura["serie"], factura["folio"]),
            "referencia_normalizada": _normalizar_factura(factura["codigo_bodega"], factura["serie"], factura["folio"]),
            "fecha_referencia": None, "fecha_captura": None, "motivo_revision": None,
            "factura_id": factura["id"], "factura_serie": factura["serie"],
            "factura_folio": factura["folio"], "uuid_cfdi": factura["uuid_cfdi"],
            "fecha_factura": factura["fecha_factura"],
        })
    return carga, _filtrar(items, proveedor, estado, fecha_desde, fecha_hasta, q)


@router.post("/upload")
async def upload(file: UploadFile = File(...), user: UserInfo = Depends(require_admin)):
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Se espera un archivo .xlsx")
    fd, tmp_name = tempfile.mkstemp(suffix=".xlsx")
    tmp = Path(tmp_name)
    archivo_guardado = None
    try:
        with open(fd, "wb") as out:
            shutil.copyfileobj(file.file, out)
        if detectar_tipo_xlsx(tmp) != "erp_facturas":
            raise HTTPException(status_code=400, detail="Este archivo no corresponde a la plantilla ERP de facturas.")
        parsed = parse_erp(tmp)
        directorio = UPLOADS_DIR / "erp"
        directorio.mkdir(parents=True, exist_ok=True)
        archivo_guardado = directorio / f"{uuid.uuid4().hex}_{Path(file.filename).name}"
        shutil.copy2(tmp, archivo_guardado)
        resumen = {"total": len(parsed["entradas"]), "para_revision": sum(bool(x["motivo_revision"]) for x in parsed["entradas"])}
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO cargas_erp (nombre_archivo, archivo_path, fecha_referencia_desde, fecha_referencia_hasta,
                   total_entradas, resumen_json, subido_por) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (file.filename, str(archivo_guardado), parsed["fecha_desde"], parsed["fecha_hasta"], len(parsed["entradas"]), json.dumps(resumen), user.user_id),
            )
            carga_id = cur.lastrowid
            conn.executemany(
                """INSERT INTO entradas_erp (carga_id, numero_fila, fila_original_json, proveedor_original,
                   proveedor_codigo, referencia_original, referencia_normalizada, fecha_referencia,
                   fecha_captura, motivo_revision) VALUES (:carga_id, :numero_fila, :fila_original_json,
                   :proveedor_original, :proveedor_codigo, :referencia_original, :referencia_normalizada,
                   :fecha_referencia, :fecha_captura, :motivo_revision)""",
                [{**entry, "carga_id": carga_id} for entry in parsed["entradas"]],
            )
        return {"carga_id": carga_id, **resumen, "fecha_desde": parsed["fecha_desde"], "fecha_hasta": parsed["fecha_hasta"]}
    except Exception:
        # No dejamos un Excel huérfano si la inserción transaccional falla.
        if archivo_guardado:
            archivo_guardado.unlink(missing_ok=True)
        raise
    finally:
        tmp.unlink(missing_ok=True)


@router.get("/cargas")
def cargas(user: UserInfo = Depends(require_admin)):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT c.*, u.email AS usuario_email FROM cargas_erp c
               LEFT JOIN usuarios u ON u.id = c.subido_por ORDER BY c.id DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("")
def listar(carga_id: Optional[int] = None, proveedor: Optional[str] = None, estado: Optional[str] = None,
           fecha_desde: Optional[str] = None, fecha_hasta: Optional[str] = None, q: Optional[str] = None,
           user: UserInfo = Depends(require_admin)):
    with get_db() as conn:
        carga, items = _resultados(conn, carga_id, proveedor, estado, fecha_desde, fecha_hasta, q)
    return {"carga": carga, "items": items, "total": len(items)}


@router.get("/resumen")
def resumen(carga_id: Optional[int] = None, proveedor: Optional[str] = None, estado: Optional[str] = None,
            fecha_desde: Optional[str] = None, fecha_hasta: Optional[str] = None, q: Optional[str] = None,
            user: UserInfo = Depends(require_admin)):
    with get_db() as conn:
        carga, items = _resultados(conn, carga_id, proveedor, estado, fecha_desde, fecha_hasta, q)
    conteo = Counter(item["estado"] for item in items)
    return {"carga": carga, "total": len(items), "confirmadas": conteo["Confirmada"],
            "pendientes": conteo["Pendiente de proveedor"], "sin_erp": conteo["Factura sin entrada ERP"],
            "para_revision": conteo["Para revisar"], "duplicadas_erp": sum(x["duplicada_erp"] for x in items)}


@router.get("/export.csv")
def export_csv(carga_id: Optional[int] = None, proveedor: Optional[str] = None, estado: Optional[str] = None,
               fecha_desde: Optional[str] = None, fecha_hasta: Optional[str] = None, q: Optional[str] = None,
               user: UserInfo = Depends(require_admin)):
    with get_db() as conn:
        _, items = _resultados(conn, carga_id, proveedor, estado, fecha_desde, fecha_hasta, q)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Estado", "Alerta", "Proveedor", "Referencia ERP", "Fecha referencia", "Fecha captura", "Serie XML", "Folio XML", "Fecha factura", "Motivo revisión"])
    for item in items:
        writer.writerow([item["estado"], "Duplicada en ERP" if item["duplicada_erp"] else "", item.get("proveedor_codigo") or item.get("proveedor_original") or "", item.get("referencia_original") or "", item.get("fecha_referencia") or "", item.get("fecha_captura") or "", item.get("factura_serie") or "", item.get("factura_folio") or "", item.get("fecha_factura") or "", item.get("motivo_revision") or ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=conciliacion_erp.csv"})
