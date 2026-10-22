"""Subida y consulta de facturas CFDI (PDF + XML)."""
import csv
import hashlib
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
from services.ocupacion_facturas import arbitrar_ocupacion
from services.folio_factura import formatear_folio
from services.uuid_pdf import extraer_uuid_de_pdf
from services.ocr_difrisa import es_factura_difrisa, leer_pdf_difrisa

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
    # Facturas con al menos un concepto sin cubrir por un cruce efectivo.
    if sin_cruzar:
        where.append(
            "EXISTS (SELECT 1 FROM factura_conceptos fc0 WHERE fc0.factura_id=f.id "
            "AND NOT EXISTS (SELECT 1 FROM factura_concepto_ventas fv WHERE fv.concepto_id=fc0.id))"
        )

    sql = f"""
        SELECT f.*, p.nombre as proveedor_nombre, p.codigo_bodega,
               (SELECT COUNT(*) FROM factura_conceptos fc WHERE fc.factura_id = f.id) as total_conceptos,
               (SELECT COUNT(*) FROM factura_conceptos fc WHERE fc.factura_id=f.id
                 AND NOT EXISTS (SELECT 1 FROM factura_concepto_ventas fv WHERE fv.concepto_id=fc.id)) as conceptos_pendientes
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
        d["conceptos_matched"] = d["total_conceptos"] - d.pop("conceptos_pendientes")
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
            """SELECT fc.*, v.titulo as venta_titulo, v.fecha_venta as venta_fecha,
                      (SELECT json_group_array(json_object('num_venta', fv.num_venta_match,
                             'num_venta_origen', va.num_venta_origen, 'cantidad', fv.cantidad_asignada,
                             'titulo', va.titulo, 'sku', va.sku, 'origen', fv.origen))
                         FROM factura_concepto_ventas fv
                         JOIN ventas_ml va ON va.num_venta=fv.num_venta_match
                        WHERE fv.concepto_id=fc.id) AS asignaciones_json
               FROM factura_conceptos fc
               LEFT JOIN ventas_ml v ON v.num_venta = fc.num_venta_match
               WHERE fc.factura_id = ?
               ORDER BY fc.id""",
            (factura_id,),
        ).fetchall()
        referencias = conn.execute(
            "SELECT * FROM factura_referencias WHERE factura_id=? ORDER BY orden", (factura_id,)
        ).fetchall()
        ocr = conn.execute("SELECT estado, uuid_detectado, texto FROM factura_ocr WHERE factura_id=?",
                           (factura_id,)).fetchone()
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
    import json
    conceptos_out = []
    for c in conceptos:
        item = dict(c)
        try:
            item["asignaciones"] = json.loads(item.pop("asignaciones_json") or "[]")
        except (TypeError, ValueError):
            item["asignaciones"] = []
        conceptos_out.append(item)
    return {"factura": d, "conceptos": conceptos_out,
            "referencias": [dict(r) for r in referencias],
            "ocr": ({"estado": ocr["estado"], "uuid_detectado": ocr["uuid_detectado"]}
                    if ocr else None)}


def _factura_difrisa_autorizada(conn, factura_id: int, user: UserInfo):
    fac = _factura_autorizada(conn, factura_id, user)
    if not es_factura_difrisa(conn, fac["proveedor_id"]):
        raise HTTPException(status_code=400, detail="La conciliación especial sólo aplica a DIFIRSA")
    return fac


def _ventas_de_referencia(conn, proveedor_id: int, numero_origen: str,
                          fecha_factura: Optional[str] = None) -> list:
    """Busca en secundaria con prioridad order.id y fallback separado a pack_id."""
    directo = conn.execute(
        """SELECT num_venta FROM ventas_ml
            WHERE cuenta_ml='secundaria' AND num_venta_origen=? LIMIT 1""", (numero_origen,)
    ).fetchone()
    if directo:
        clausula, param = "v.num_venta_origen=?", numero_origen
    else:
        clausula, param = "v.pack_id=?", numero_origen
    rows = conn.execute(
        f"""SELECT v.num_venta, v.num_venta_origen, v.pack_id, v.sku, v.unidades,
                   v.titulo, v.fecha_venta,
                   (UPPER(TRIM(COALESCE(v.deposito,'')))='DIFIRSA' OR EXISTS (
                       SELECT 1 FROM envios_colecta e
                        WHERE e.cuenta_ml='secundaria' AND e.proveedor_id=?
                          AND (e.num_venta_ml=v.num_venta OR
                               (e.pack_id IS NOT NULL AND v.pack_id IS NOT NULL
                                AND e.pack_id=v.pack_id)))) AS proveedor_compatible
              FROM ventas_ml v
             WHERE v.cuenta_ml='secundaria' AND {clausula}
             ORDER BY v.fecha_venta, v.num_venta""", (proveedor_id, param)
    ).fetchall()
    return [{**dict(r), "fecha_compatible": (not fecha_factura or not r["fecha_venta"] or
            str(r["fecha_venta"])[:10] <= str(fecha_factura)[:10])} for r in rows]


def _concepto_compatible(conn, proveedor_id: int, concepto, venta) -> bool:
    codigo = (concepto["codigo_prov"] or "").strip().upper()
    sku = (venta["sku"] or "").strip().upper()
    if codigo and sku and codigo == sku:
        return True
    if codigo and sku and conn.execute(
        """SELECT 1 FROM proveedor_sku_equivalencias
             WHERE proveedor_id=? AND UPPER(TRIM(codigo_prov))=? AND UPPER(TRIM(sku_ml))=?""",
        (proveedor_id, codigo, sku),
    ).fetchone():
        return True
    if codigo and conn.execute(
        """SELECT 1 FROM kit_componentes
             WHERE UPPER(TRIM(kit_sku))=? AND UPPER(TRIM(componente_codigo))=?
               AND (proveedor_id IS NULL OR proveedor_id=?)""",
        (sku, codigo, proveedor_id),
    ).fetchone():
        return True
    return False


@router.get("/{factura_id}/difirsa")
def conciliacion_difrisa(factura_id: int, user: UserInfo = Depends(get_current_user)):
    with get_db() as conn:
        fac = _factura_difrisa_autorizada(conn, factura_id, user)
        referencias = conn.execute(
            "SELECT * FROM factura_referencias WHERE factura_id=? ORDER BY orden", (factura_id,)
        ).fetchall()
        conceptos = conn.execute(
            "SELECT * FROM factura_conceptos WHERE factura_id=? ORDER BY id", (factura_id,)
        ).fetchall()
        propuestas = conn.execute(
            """SELECT concepto_id, num_venta_match AS num_venta, cantidad
                 FROM factura_asignaciones WHERE factura_id=? AND estado='propuesta'
                 ORDER BY concepto_id, id""", (factura_id,)
        ).fetchall()
        confirmadas = conn.execute(
            """SELECT concepto_id, num_venta_match AS num_venta, cantidad
                 FROM factura_asignaciones WHERE factura_id=? AND estado='confirmada'
                 ORDER BY concepto_id, id""", (factura_id,)
        ).fetchall()
        por_ref = {}
        for ref in referencias:
            ventas = _ventas_de_referencia(conn, fac["proveedor_id"], ref["num_venta_origen"] or "",
                                           fac["fecha_factura"])
            for v in ventas:
                por_ref.setdefault(v["num_venta"], v)
        conceptos_out = []
        for c in conceptos:
            d = dict(c)
            d["candidatas"] = []
            d["ventas_referenciadas"] = []
            for v in por_ref.values():
                if v["proveedor_compatible"] and v["fecha_compatible"]:
                    d["ventas_referenciadas"].append({k: v[k] for k in (
                        "num_venta", "num_venta_origen", "pack_id", "sku", "unidades", "titulo", "fecha_venta"
                    )})
                compatible = _concepto_compatible(conn, fac["proveedor_id"], c, v)
                if v["proveedor_compatible"] and v["fecha_compatible"] and compatible:
                    d["candidatas"].append({k: v[k] for k in (
                        "num_venta", "num_venta_origen", "pack_id", "sku", "unidades", "titulo", "fecha_venta"
                    )})
            conceptos_out.append(d)
        referencias_out = []
        for ref in referencias:
            ventas = _ventas_de_referencia(conn, fac["proveedor_id"], ref["num_venta_origen"] or "",
                                           fac["fecha_factura"])
            referencias_out.append({**dict(ref), "resueltas": ventas})
        ocr = conn.execute("SELECT estado, uuid_detectado FROM factura_ocr WHERE factura_id=?",
                           (factura_id,)).fetchone()
    return {"factura_id": factura_id, "referencias": referencias_out,
            "conceptos": conceptos_out, "ocr": dict(ocr) if ocr else None,
            "propuestas": [dict(p) for p in propuestas],
            "asignaciones": [dict(a) for a in confirmadas]}


@router.put("/{factura_id}/difirsa/referencias")
def corregir_referencias_difrisa(factura_id: int, payload: dict,
                                  user: UserInfo = Depends(get_current_user)):
    if user.rol != "admin":
        raise HTTPException(status_code=403, detail="Sólo administración puede revisar la conciliación")
    referencias = payload.get("referencias")
    if not isinstance(referencias, list) or not referencias:
        raise HTTPException(status_code=400, detail="Incluye al menos una referencia de venta")
    normalizadas = []
    for valor in referencias:
        raw = str(valor).strip()
        if raw.startswith("20000") and len(raw) == 16 and raw.isdigit():
            origen = raw
        elif len(raw) == 11 and raw.startswith("18") and raw.isdigit():
            origen = "20000" + raw
        else:
            raise HTTPException(status_code=400, detail=f"Referencia DIFIRSA inválida: {raw}")
        if origen not in normalizadas:
            normalizadas.append(origen)
    with get_db() as conn:
        _factura_difrisa_autorizada(conn, factura_id, user)
        conn.execute("DELETE FROM factura_asignaciones WHERE factura_id=? AND estado='propuesta'", (factura_id,))
        conn.execute("DELETE FROM factura_referencias WHERE factura_id=?", (factura_id,))
        conn.executemany(
            """INSERT INTO factura_referencias
               (factura_id, orden, valor_original, num_venta_origen, fuente)
               VALUES (?, ?, ?, ?, 'manual')""",
            [(factura_id, i, n[-11:], n) for i, n in enumerate(normalizadas)],
        )
    return conciliacion_difrisa(factura_id, user)


@router.post("/{factura_id}/difirsa/proponer")
def proponer_difrisa(factura_id: int, user: UserInfo = Depends(get_current_user)):
    if user.rol != "admin":
        raise HTTPException(status_code=403, detail="Sólo administración puede revisar la conciliación")
    snapshot = conciliacion_difrisa(factura_id, user)
    conceptos = snapshot["conceptos"]
    propuestas = []
    capacidad = {}
    for c in conceptos:
        compatibles = c["candidatas"]
        qty = float(c["cantidad"] or 0)
        if qty <= 0 or not compatibles:
            continue
        disponibles = []
        for v in compatibles:
            units = float(v["unidades"] or 0) - capacidad.get(v["num_venta"], 0)
            if units > 0:
                disponibles.append({**v, "disponibles": units})
        if len(disponibles) == 1 and disponibles[0]["disponibles"] >= qty:
            toma = [(disponibles[0], qty)]
        elif len(disponibles) > 1 and abs(sum(v["disponibles"] for v in disponibles) - qty) < 1e-6:
            toma = [(v, v["disponibles"]) for v in disponibles]
        else:
            continue
        for v, cantidad in toma:
            propuestas.append((factura_id, c["id"], v["num_venta"], cantidad, "propuesta"))
            capacidad[v["num_venta"]] = capacidad.get(v["num_venta"], 0) + cantidad
    with get_db() as conn:
        conn.execute("DELETE FROM factura_asignaciones WHERE factura_id=? AND estado='propuesta'", (factura_id,))
        conn.executemany(
            """INSERT INTO factura_asignaciones
               (factura_id, concepto_id, num_venta_match, cantidad, estado) VALUES (?, ?, ?, ?, ?)""",
            propuestas,
        )
    return {**snapshot, "propuestas": [
        {"concepto_id": c_id, "num_venta": num, "cantidad": qty}
        for _, c_id, num, qty, _ in propuestas
    ]}


@router.post("/{factura_id}/difirsa/confirmar")
def confirmar_difrisa(factura_id: int, payload: dict,
                      user: UserInfo = Depends(get_current_user)):
    if user.rol != "admin":
        raise HTTPException(status_code=403, detail="Sólo administración puede confirmar cruces")
    asignaciones = payload.get("asignaciones")
    equivalencias = payload.get("equivalencias", [])
    if not isinstance(asignaciones, list):
        raise HTTPException(status_code=400, detail="Faltan las asignaciones revisadas")
    with get_db() as conn:
        fac = _factura_difrisa_autorizada(conn, factura_id, user)
        conceptos = conn.execute(
            "SELECT * FROM factura_conceptos WHERE factura_id=? ORDER BY id", (factura_id,)
        ).fetchall()
        snapshot = conciliacion_difrisa(factura_id, user)
        ventas_referenciadas = {v["num_venta"] for c in snapshot["conceptos"] for v in c["ventas_referenciadas"]}
        conceptos_por_id = {c["id"]: c for c in conceptos}
        equivalencias_payload = {
            (str(eq.get("codigo_prov") or "").strip().upper(),
             str(eq.get("sku_ml") or "").strip().upper()) for eq in equivalencias
        }
        suma_concepto = {}
        suma_venta = {}
        preparados = []
        for a in asignaciones:
            cid, numero, cantidad = a.get("concepto_id"), a.get("num_venta"), a.get("cantidad")
            try:
                cantidad = float(cantidad)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Cantidad de asignación inválida")
            c = conceptos_por_id.get(cid)
            if not c or numero not in ventas_referenciadas or cantidad <= 0:
                raise HTTPException(status_code=400, detail="Asignación fuera de las referencias o conceptos de la factura")
            venta_obj = next(v for v in snapshot["conceptos"] if v["id"] == cid)
            venta = next(v for v in venta_obj["ventas_referenciadas"] if v["num_venta"] == numero)
            codigo = (c["codigo_prov"] or "").strip().upper()
            sku = (venta["sku"] or "").strip().upper()
            compatible = _concepto_compatible(conn, fac["proveedor_id"], c, venta)
            compatible = compatible or (codigo, sku) in equivalencias_payload
            if not compatible:
                raise HTTPException(status_code=400, detail="El producto requiere una equivalencia aprobada antes de asignarlo")
            suma_concepto[cid] = suma_concepto.get(cid, 0) + cantidad
            suma_venta[numero] = suma_venta.get(numero, 0) + cantidad
            preparados.append((cid, numero, cantidad))
        for c in conceptos:
            if abs(suma_concepto.get(c["id"], 0) - float(c["cantidad"] or 0)) > 1e-6:
                raise HTTPException(status_code=400, detail=f"El concepto {c['codigo_prov']} debe quedar asignado por completo")
        for numero, cantidad in suma_venta.items():
            venta = conn.execute("SELECT unidades, sku FROM ventas_ml WHERE num_venta=?", (numero,)).fetchone()
            if not venta or venta["unidades"] is None or cantidad > float(venta["unidades"]) + 1e-6:
                raise HTTPException(status_code=400, detail="La cantidad asignada excede las unidades de la venta")
            ya_usada = conn.execute(
                """SELECT COALESCE(SUM(cantidad_asignada),0) FROM factura_concepto_ventas
                    WHERE num_venta_match=? AND factura_id!=?""", (numero, factura_id)
            ).fetchone()[0]
            if ya_usada:
                raise HTTPException(status_code=409, detail="La venta ya está asociada a otra factura")
        for eq in equivalencias:
            codigo = str(eq.get("codigo_prov") or "").strip().upper()
            sku = str(eq.get("sku_ml") or "").strip().upper()
            if not codigo or not sku:
                raise HTTPException(status_code=400, detail="Equivalencia de producto incompleta")
            conn.execute(
                """INSERT INTO proveedor_sku_equivalencias (proveedor_id, codigo_prov, sku_ml, creado_por)
                   VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                (fac["proveedor_id"], codigo, sku, user.user_id),
            )
        conn.execute("DELETE FROM factura_asignaciones WHERE factura_id=?", (factura_id,))
        ahora = datetime.utcnow().isoformat(timespec="seconds")
        conn.executemany(
            """INSERT INTO factura_asignaciones
               (factura_id, concepto_id, num_venta_match, cantidad, estado, confirmado_por, confirmado_en)
               VALUES (?, ?, ?, ?, 'confirmada', ?, ?)""",
            [(factura_id, cid, num, qty, user.user_id, ahora) for cid, num, qty in preparados],
        )
    return detalle(factura_id, user)


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
                       user: UserInfo, prov_row, pdf_analysis: Optional[dict] = None) -> dict:
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

    # Paso 0 del matcher: el # de venta impreso en el PDF (sólo KIM). Se arma UNA vez y
    # se reusa en todos los conceptos — el PDF se lee una sola vez por factura, no una
    # por concepto. `codigos` va completo a propósito: KIM parte una venta en varias
    # piezas y cualquiera de ellas sirve para verificar que la venta es la correcta.
    ctx_factura = {
        # El id habilita la caché del # impreso (facturas.num_venta_pdf): el PDF se abre
        # una sola vez y los recruces posteriores ya no lo releen.
        "id": factura_id,
        # Acceso defensivo: si un llamador futuro pasa un row sin la columna, el paso 0
        # se salta en vez de tumbar la subida de la factura.
        "codigo_bodega": (prov_row["codigo_bodega"]
                          if prov_row and "codigo_bodega" in prov_row.keys() else None),
        "pdf_path": str(pdf_path) if pdf_path else None,
        "codigos": [c.get("codigo") for c in parsed["conceptos"] if c.get("codigo")],
    }

    es_difrisa = (prov_row and (prov_row["codigo_bodega"] or "").strip().upper() == "DIFIRSA")
    for c in parsed["conceptos"]:
        # DIFIRSA trae referencias de venta comunes a toda la factura; sus conceptos
        # requieren reparto de cantidades confirmado por Gaby, no match por fila.
        match = (None if es_difrisa else match_conceptos_a_ventas(
            conn, user.proveedor_id, c, fecha_factura=parsed.get("fecha"), factura=ctx_factura
        ))
        arbitraje = arbitrar_ocupacion(conn, factura_id, match)
        match = arbitraje["match"]
        conn.execute(
            """INSERT INTO factura_conceptos (factura_id, codigo_prov, num_venta_proveedor,
                                              cruce_numero_estado, conflicto_factura,
                                              descripcion, cantidad,
                                              precio_unitario, importe, num_venta_match, match_method, match_confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                factura_id,
                c.get("codigo"),
                c.get("num_venta_proveedor"),
                c.get("cruce_numero_estado"),
                arbitraje["conflicto"],
                c.get("descripcion"),
                c.get("cantidad"),
                c.get("precio_unitario"),
                c.get("importe"),
                match.get("num_venta") if match else None,
                match.get("method") if match else None,
                match.get("confidence") if match else None,
            ),
        )

    if es_difrisa:
        if pdf_analysis:
            conn.execute(
                """INSERT INTO factura_ocr (factura_id, texto, estado, uuid_detectado, pdf_sha256, motor)
                   VALUES (?, ?, ?, ?, ?, 'tesseract')""",
                (factura_id, pdf_analysis.get("texto"), pdf_analysis.get("estado"),
                 pdf_analysis.get("uuid"), pdf_analysis.get("sha256")),
            )
            for i, referencia in enumerate(pdf_analysis.get("referencias", [])):
                conn.execute(
                    """INSERT INTO factura_referencias
                       (factura_id, orden, valor_original, num_venta_origen, fuente, confianza)
                       VALUES (?, ?, ?, ?, 'ocr', ?)""",
                    (factura_id, i, referencia["valor_original"],
                     referencia.get("num_venta_origen"), referencia.get("confianza")),
                )
        else:
            conn.execute(
                "INSERT INTO factura_ocr (factura_id, estado, motor) VALUES (?, 'sin_pdf', 'tesseract')",
                (factura_id,),
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
        uuid_pdf = (pdf.get("ocr") or {}).get("uuid") or extraer_uuid_de_pdf(pdf["path"])
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
    # Si el lote contiene exactamente un XML y un PDF, la asociación es inequívoca
    # aunque el OCR no pueda leer el UUID y los nombres no coincidan.
    if len(xmls_info) == 1 and len(pdfs_guardados) == 1 and not asignados:
        asignados[xmls_info[0]["idx"]] = pdfs_guardados[0]["path"]
        pdfs_guardados[0]["usado"] = True
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
    with get_db() as conn:
        proveedor_difrisa = es_factura_difrisa(conn, user.proveedor_id)

    pdfs_guardados = []
    for i, pdf in enumerate(pdfs or []):
        if not pdf or not pdf.filename or not pdf.filename.lower().endswith(".pdf"):
            continue
        path = _guardar_subida(pdf, user.proveedor_id, f"{timestamp}_p{i}")
        analisis_pdf = None
        if proveedor_difrisa:
            try:
                analisis_pdf = leer_pdf_difrisa(path)
                analisis_pdf["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            except Exception as exc:
                analisis_pdf = {"estado": "error", "error": str(exc), "referencias": []}
        pdfs_guardados.append({"path": path, "original": pdf.filename, "usado": False,
                               "ocr": analisis_pdf})

    asignados = _emparejar_pdfs(xmls_info, pdfs_guardados)

    # 3) Registrar cada factura en BD (RFC + dedup). Errores son por-factura.
    registradas = []
    with get_db() as conn:
        prov = conn.execute(
            # codigo_bodega lo necesita el paso 0 del matcher (# de venta impreso, KIM).
            "SELECT rfc, nombre, codigo_bodega FROM proveedores WHERE id = ?",
            (user.proveedor_id,),
        ).fetchone()
        for x in xmls_info:
            pdf_path = asignados.get(x["idx"])
            try:
                info_pdf = next((p for p in pdfs_guardados if p["path"] == pdf_path), None)
                res = _registrar_factura(
                    conn, x["parsed"], x["path"], pdf_path, user, prov,
                    (info_pdf or {}).get("ocr"),
                )
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
