"""
Lector del catálogo de un proveedor y cruce contra lo ya publicado en ML.

Dos responsabilidades:
  1. Leer el Excel del proveedor según su PERFIL (services/perfiles_catalogo.py).
  2. Decir qué claves YA están publicadas y cuáles FALTAN — que es lo que Gaby
     describió del reporte de Publicaciones ML: *"este documento es para hacer el
     cruce de lo que ya tenemos publicado y de lo que hace falta, es la columna Q
     que incluye los skus de las piezas"*.

⚠️ La columna Q ('Att_SellerSKU') puede traer VARIOS SKU pegados con '&' cuando
la publicación es un paquete ('NO625HB&NO625HB'). Hay que partirla, o un SKU que
sí está publicado dentro de un paquete se contaría como faltante.

Medido sobre los archivos reales: 14,946 publicaciones traen sólo 626 SKU únicos
(porque N publicaciones comparten pieza, justo la expansión que genera este
módulo). De las 3,676 claves de KG, 69 ya están publicadas y 3,607 faltan.
"""
from pathlib import Path
from typing import Dict, List, Optional, Set

import openpyxl

from services.perfiles_catalogo import PerfilCatalogo

# Columna Q del reporte de Publicaciones ML (0-based).
COL_ATT_SELLER_SKU = 16
COL_STATUS = 4
COL_TITULO = 1


def _texto(val) -> str:
    """Normaliza una celda a texto. Excel entrega números como int/float."""
    if val is None:
        return ""
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val).strip()


def leer_catalogo(ruta, perfil: PerfilCatalogo) -> List[Dict]:
    """Lee el Excel del proveedor y devuelve una lista de piezas."""
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    ws = wb[perfil.nombre_hoja] if perfil.nombre_hoja else wb.worksheets[0]

    piezas: List[Dict] = []
    for fila in ws.iter_rows(min_row=perfil.fila_header + 1, values_only=True):
        clave = _texto(fila[perfil.col_clave]) if len(fila) > perfil.col_clave else ""
        if not clave:
            continue  # filas de relleno / totales al final de la hoja

        def celda(i: Optional[int]):
            return fila[i] if i is not None and len(fila) > i else None

        piezas.append({
            "clave": clave,
            "linea": _texto(celda(perfil.col_linea)),
            "aplicaciones": celda(perfil.col_aplicaciones),
            "costo": celda(perfil.col_precio_costo),
            "codigo_barras": _texto(celda(perfil.col_codigo_barras)),
            "precio_publico": celda(perfil.col_precio_publico),
        })

    wb.close()
    return piezas


def leer_skus_publicados(ruta) -> Set[str]:
    """SKU ya publicados, desde la columna Q del reporte de Publicaciones ML."""
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    ws = wb.worksheets[0]

    skus: Set[str] = set()
    for fila in ws.iter_rows(min_row=2, values_only=True):
        if len(fila) <= COL_ATT_SELLER_SKU:
            continue
        crudo = _texto(fila[COL_ATT_SELLER_SKU])
        if not crudo:
            continue
        # '&' separa los SKU de un paquete: cada parte cuenta como publicada.
        for parte in crudo.split("&"):
            limpio = parte.strip().upper()
            if limpio:
                skus.add(limpio)

    wb.close()
    return skus


def cruzar(piezas: List[Dict], publicados: Set[str]) -> Dict:
    """Separa el catálogo en publicadas vs faltantes."""
    faltantes, ya = [], []
    for p in piezas:
        destino = ya if p["clave"].upper() in publicados else faltantes
        destino.append(p)

    return {
        "total_catalogo": len(piezas),
        "ya_publicadas": len(ya),
        "faltantes": len(faltantes),
        "piezas_faltantes": faltantes,
        "piezas_publicadas": ya,
    }
