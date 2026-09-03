"""Cruce de export de ImageKit con el master de publicaciones CAUPLAS.

El CSV no es una fuente de URLs arbitrarias: únicamente aporta una galería a
cada SKU que ya existe en el master.  El nombre completo tiene prioridad sobre
el sufijo ``-N`` para no romper SKU legítimos como ``ABC-2``.
"""
import csv
import io
import re
from pathlib import PurePath
from typing import Dict, Iterable, List, Tuple


_SUFIJO_FOTO = re.compile(r"^(.*)-(\d+)$")


def _normalizar_sku(valor) -> str:
    return str(valor or "").strip().upper()


def _nombre_sin_extension(valor) -> str:
    # ImageKit manda un nombre de archivo; toleramos también una ruta Windows.
    nombre = str(valor or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    return PurePath(nombre).stem.strip()


def leer_imagenes_cauplas(contenido: bytes, skus_master: Iterable[str]) -> Tuple[Dict[str, List[str]], Dict[str, int]]:
    """Lee ``Name,URL`` y devuelve una galería indexada (Imagen1..Imagen10).

    Las estadísticas son deliberadamente de cruce, no de red: la comprobación
    HTTPS/host/tipo/resolución se hace una sola vez al generar el Excel.
    """
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            texto = contenido.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("El CSV de imágenes debe estar codificado en UTF-8.") from exc

    lector = csv.DictReader(io.StringIO(texto))
    encabezados = {str(x or "").strip().casefold(): x for x in (lector.fieldnames or [])}
    if "name" not in encabezados or "url" not in encabezados:
        raise ValueError("El CSV de imágenes CAUPLAS debe incluir las columnas Name y URL.")

    nombre_col, url_col = encabezados["name"], encabezados["url"]
    skus = {_normalizar_sku(sku) for sku in skus_master if _normalizar_sku(sku)}
    galerias: Dict[str, List[str]] = {}
    resumen = {
        "skus_con_fotos": 0, "urls_detectadas": 0, "urls_validas": 0,
        "urls_omitidas": 0, "urls_sin_match": 0, "sku_master_sin_foto": 0,
    }

    for fila in lector:
        url = str(fila.get(url_col) or "").strip()
        if not url:
            continue
        resumen["urls_detectadas"] += 1
        nombre = _nombre_sin_extension(fila.get(nombre_col))
        nombre_normalizado = _normalizar_sku(nombre)
        sku, posicion = None, None
        if nombre_normalizado in skus:
            sku, posicion = nombre_normalizado, 1
        else:
            sufijo = _SUFIJO_FOTO.match(nombre)
            if sufijo and int(sufijo.group(2)) >= 2:
                posible = _normalizar_sku(sufijo.group(1))
                if posible in skus:
                    sku, posicion = posible, int(sufijo.group(2))
        if sku is None:
            resumen["urls_sin_match"] += 1
            resumen["urls_omitidas"] += 1
            continue
        if posicion > 10:
            resumen["urls_omitidas"] += 1
            continue
        galeria = galerias.setdefault(sku, [""] * 10)
        # No se repite una URL en la galería y tampoco se adivina cuál de dos
        # URLs para la misma posición debe ganar: la primera fila es canónica.
        if url in galeria or galeria[posicion - 1]:
            resumen["urls_omitidas"] += 1
            continue
        galeria[posicion - 1] = url
        resumen["urls_validas"] += 1

    galerias = {sku: fotos for sku, fotos in galerias.items() if any(fotos)}
    resumen["skus_con_fotos"] = len(galerias)
    resumen["sku_master_sin_foto"] = len(skus - set(galerias))
    return galerias, resumen


def asignar_imagenes_cauplas(filas, galerias: Dict[str, List[str]]) -> None:
    """Copia la misma galería a todas las variantes derivadas de un SKU."""
    for fila in filas:
        fila.imagenes = list(galerias.get(_normalizar_sku(fila.sku), []))
