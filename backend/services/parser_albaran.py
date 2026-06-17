"""
Parser del Excel de números de albarán que aporta Gaby.

Es un archivo simple de 2 columnas: # de venta + # de albarán. Gaby lo arma a mano
con los albaranes que va recibiendo y lo re-sube cuando tiene más. El portal cruza
por num_venta contra ventas_ml (cruce 1:1 directo, NO por fecha+título como la colecta)
y guarda el albarán en ventas_ml.albaran para mostrarlo en la pestaña Ventas.

Solo ENRIQUECE ventas ya cargadas (UPDATE, nunca INSERT): si un num_venta del Excel no
existe en ventas_ml, se reporta como no_encontrado y no se crea fila huérfana. Una fila
con albarán vacío se ignora (no borra el albarán existente, por si Gaby re-sube parcial).

El header se detecta por contenido (texto ancla, tolerante a variaciones de nombre),
no por posición fija de columna.
"""
import re
from pathlib import Path
from typing import Optional

import openpyxl

from database import get_db

# Textos ancla para reconocer cada columna en el header (en minúsculas, sin acentos
# duros: comparamos contra el texto de la celda ya normalizado).
_ANCLAS_VENTA = ("# de venta", "num de venta", "numero de venta", "num venta", "venta")
_ANCLAS_ALBARAN = ("# de albaran", "# de albarán", "num de albaran", "numero de albaran",
                   "num albaran", "albaran", "albarán")


def _norm(val) -> str:
    """Normaliza una celda a string en minúsculas y sin espacios extra."""
    if val is None:
        return ""
    return re.sub(r"\s+", " ", str(val).strip().lower())


def _detectar_columnas(rows):
    """Busca en las primeras filas la que contenga ambas anclas (venta + albarán).
    Devuelve (header_idx, idx_venta, idx_albaran) o (None, None, None) si no la halla.
    """
    for i, row in enumerate(rows[:15]):
        idx_venta = None
        idx_albaran = None
        for j, cell in enumerate(row):
            txt = _norm(cell)
            if not txt:
                continue
            # Albarán primero: "venta" es substring de varias cosas, así que evaluamos
            # la columna albarán antes y no dejamos que "venta" pise una celda de albarán.
            if idx_albaran is None and any(a in txt for a in _ANCLAS_ALBARAN):
                idx_albaran = j
            elif idx_venta is None and any(a in txt for a in _ANCLAS_VENTA):
                idx_venta = j
        if idx_venta is not None and idx_albaran is not None:
            return i, idx_venta, idx_albaran
    return None, None, None


def parse_albaran(path: Path) -> dict:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header_idx, idx_venta, idx_albaran = _detectar_columnas(rows)
    if header_idx is None:
        raise ValueError(
            "No encontré las columnas «# de venta» y «# de albarán» en el archivo. "
            "Verifica que el Excel tenga esos dos encabezados."
        )

    actualizados = 0
    no_encontrados = 0
    sin_albaran = 0

    with get_db() as conn:
        for row in rows[header_idx + 1:]:
            if idx_venta >= len(row):
                continue
            num_venta = row[idx_venta]
            if not num_venta:
                continue
            num_venta = str(num_venta).strip()
            if not num_venta:
                continue

            albaran = row[idx_albaran] if idx_albaran < len(row) else None
            albaran = str(albaran).strip() if albaran is not None else ""
            if not albaran:
                sin_albaran += 1
                continue  # no tocar: evita borrar un albarán existente al re-subir parcial

            cur = conn.execute(
                "UPDATE ventas_ml SET albaran = ? WHERE num_venta = ?",
                (albaran, num_venta),
            )
            if cur.rowcount:
                actualizados += 1
            else:
                no_encontrados += 1  # venta no cargada aún; no creamos fila huérfana

    return {
        "ok": True,
        "actualizados": actualizados,
        "no_encontrados": no_encontrados,
        "sin_albaran": sin_albaran,
    }
