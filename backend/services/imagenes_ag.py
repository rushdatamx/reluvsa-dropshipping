"""Galería de FOTOS AG: sólo acepta rutas del bucket autorizado de ARGENPARTS."""
import re
from pathlib import PurePath
from typing import Dict, Iterable, List, Tuple

import openpyxl

from services.parser_catalogo import _normalizar, _texto

HOST = "https://apimagenes.s3.amazonaws.com"
_SUFIJO = re.compile(r"^(.*?)[_-](\d+)$")


def _nombre(valor):
    return PurePath(str(valor or "").strip().replace("\\", "/")).stem.strip()


def leer_imagenes_ag(ruta, skus_master: Iterable[str]) -> Tuple[Dict[str, List[str]], Dict[str, int]]:
    """Lee las diez hojas del libro y cruza Nombre / SKU_2 / SKU-2.

    El libro vigente de AG aporta ``URL FINAL``; los campos de servidor,
    carpeta, nombre y extensión se conservan como respaldo para libros
    anteriores. Se toleran mayúsculas y acentos en los encabezados.
    """
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    skus = {_texto(s).upper() for s in skus_master if _texto(s)}
    galerias: Dict[str, List[str]] = {}
    resumen = {"skus_con_fotos": 0, "urls_detectadas": 0, "urls_validas": 0,
        "urls_cruzadas": 0, "urls_omitidas": 0, "urls_sin_match": 0,
        "sku_master_sin_foto": 0}
    try:
        for ws in wb.worksheets[:10]:
            rows = ws.iter_rows(values_only=True)
            header = next(rows, None)
            if not header: continue
            cols = {_normalizar(x): i for i, x in enumerate(header) if _texto(x)}
            def col(*nombres):
                return next((cols[_normalizar(n)] for n in nombres if _normalizar(n) in cols), None)
            servidor = col("servidor", "url servidor", "server")
            carpeta = col("carpeta", "nombre de la carpeta", "folder")
            archivo = col("nombre", "nombre de la foto", "archivo", "name")
            extension = col("extension", "extensión", "formato de la foto", "ext")
            url_final = col("url final")
            if archivo is None: continue
            for fila in rows:
                def valor(i): return fila[i] if i is not None and i < len(fila) else ""
                nombre = _nombre(valor(archivo))
                if not nombre: continue
                resumen["urls_detectadas"] += 1
                # ``URL FINAL`` es la fuente de verdad del libro actual. Si no
                # viene en una fila, se mantiene la construcción histórica para
                # que los archivos anteriores sigan siendo compatibles. Ambas
                # rutas pasan después por el candado de host, HTTPS, sin
                # redirecciones y resolución mínima antes de salir en el XLSX.
                url = _texto(valor(url_final)).strip()
                if not url:
                    ext = _texto(valor(extension)).lstrip(".") or "jpg"
                    base = _texto(valor(servidor)).rstrip("/") or HOST
                    partes = [str(valor(carpeta) or "").strip("/"), nombre + "." + ext]
                    url = base + "/" + "/".join(p for p in partes if p)
                if re.search(r"\b(?:combo|kit)\b", nombre, re.IGNORECASE):
                    resumen["urls_omitidas"] += 1; continue
                sku, posicion = (nombre.upper(), 1) if nombre.upper() in skus else (None, None)
                if sku is None:
                    m = _SUFIJO.match(nombre)
                    if m and int(m.group(2)) >= 2:
                        posible = m.group(1).strip().upper()
                        if posible in skus: sku, posicion = posible, int(m.group(2))
                if sku is None:
                    resumen["urls_sin_match"] += 1; resumen["urls_omitidas"] += 1; continue
                if posicion > 10:
                    resumen["urls_omitidas"] += 1; continue
                galeria = galerias.setdefault(sku, [""] * 10)
                if url in galeria or galeria[posicion - 1]:
                    resumen["urls_omitidas"] += 1; continue
                galeria[posicion - 1] = url
                resumen["urls_validas"] += 1; resumen["urls_cruzadas"] += 1
    finally:
        wb.close()
    galerias = {sku: fotos for sku, fotos in galerias.items() if any(fotos)}
    resumen["skus_con_fotos"] = len(galerias)
    resumen["sku_master_sin_foto"] = len(skus - set(galerias))
    return galerias, resumen


def asignar_imagenes_ag(filas, galerias):
    for fila in filas:
        fila.imagenes = list(galerias.get(_texto(fila.sku).upper(), []))
