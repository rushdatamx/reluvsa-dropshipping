"""
Parser del Excel de relación kit -> componentes que aporta Gaby.

Cada venta de Mercado Libre que es un "kit" tiene un SKU sintético de RELUVSA
(ej. 'KIT0337') que NO aparece en ninguna factura: el proveedor factura los
componentes reales del kit (ej. 'KDTL-057', 'KDTL-058'). Este Excel es la tabla
puente que dice de qué se compone cada kit, para que el matcher cruce los conceptos
de la factura contra los componentes y la venta-kit deje de salir "Pendiente".

El archivo tiene 3 columnas: Paquete (-> Tag del kit), Componente (-> Tag) y Cantidad.
Se carga de forma INCREMENTAL (upsert por (kit_sku, componente)): re-subir un archivo
agrega los kits nuevos y actualiza la cantidad de los existentes, sin borrar los que
no vengan en el archivo. El kit_sku se guarda normalizado (UPPER + TRIM) porque el
Excel trae formatos inconsistentes (algunos con espacio final).

El header se detecta por contenido (texto ancla, tolerante a variaciones de nombre),
no por posición fija de columna.
"""
import re
from pathlib import Path

import openpyxl

from database import get_db

# Textos ancla para reconocer cada columna en el header (en minúsculas, sin espacios
# extra: comparamos contra el texto de la celda ya normalizado). El Excel real trae
# "Paquete -> Tag", "Componente -> Tag", "Cantidad".
_ANCLAS_KIT = ("paquete", "kit")
_ANCLAS_COMPONENTE = ("componente",)
_ANCLAS_CANTIDAD = ("cantidad", "cant")


def _norm(val) -> str:
    """Normaliza una celda a string en minúsculas y sin espacios extra."""
    if val is None:
        return ""
    return re.sub(r"\s+", " ", str(val).strip().lower())


def _to_int(val, default=1) -> int:
    """Cantidad defensiva: tolera floats ('4.0'), espacios y basura. Mínimo 1."""
    if val is None:
        return default
    s = str(val).strip()
    if not s:
        return default
    try:
        n = int(float(s))
        return n if n >= 1 else default
    except (ValueError, TypeError):
        return default


def _detectar_columnas(rows):
    """Busca en las primeras filas la que contenga las 3 anclas (kit + componente +
    cantidad). Devuelve (header_idx, idx_kit, idx_comp, idx_cant) o None si no la halla.
    """
    for i, row in enumerate(rows[:15]):
        idx_kit = idx_comp = idx_cant = None
        for j, cell in enumerate(row):
            txt = _norm(cell)
            if not txt:
                continue
            # Componente primero ("componente" contiene substring que podría chocar);
            # cantidad después; kit al final ("kit"/"paquete" son los más genéricos).
            if idx_comp is None and any(a in txt for a in _ANCLAS_COMPONENTE):
                idx_comp = j
            elif idx_cant is None and any(a in txt for a in _ANCLAS_CANTIDAD):
                idx_cant = j
            elif idx_kit is None and any(a in txt for a in _ANCLAS_KIT):
                idx_kit = j
        if idx_kit is not None and idx_comp is not None and idx_cant is not None:
            return i, idx_kit, idx_comp, idx_cant
    return None


def parse_kits(path: Path) -> dict:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    detected = _detectar_columnas(rows)
    if detected is None:
        raise ValueError(
            "No encontré las columnas «Paquete», «Componente» y «Cantidad» en el "
            "archivo. Verifica que el Excel de kits tenga esos tres encabezados."
        )
    header_idx, idx_kit, idx_comp, idx_cant = detected

    filas = 0
    nuevos = 0
    actualizados = 0
    kits = set()

    with get_db() as conn:
        for row in rows[header_idx + 1:]:
            if idx_kit >= len(row) or idx_comp >= len(row):
                continue
            kit_sku = row[idx_kit]
            componente = row[idx_comp]
            if not kit_sku or not componente:
                continue
            kit_sku = str(kit_sku).strip().upper()
            componente = str(componente).strip()
            if not kit_sku or not componente:
                continue

            cantidad = _to_int(row[idx_cant] if idx_cant < len(row) else None)
            filas += 1
            kits.add(kit_sku)

            # ¿Ya existía esa relación? (el rowcount de un upsert no distingue
            # insert de update de forma fiable en SQLite, así que lo verificamos antes.)
            existe = conn.execute(
                "SELECT 1 FROM kit_componentes WHERE kit_sku = ? AND componente_codigo = ?",
                (kit_sku, componente),
            ).fetchone()

            # Upsert: agrega el componente o actualiza su cantidad. No borra nada.
            conn.execute(
                """INSERT INTO kit_componentes (kit_sku, componente_codigo, cantidad)
                   VALUES (?, ?, ?)
                   ON CONFLICT(kit_sku, componente_codigo)
                   DO UPDATE SET cantidad = excluded.cantidad""",
                (kit_sku, componente, cantidad),
            )
            if existe:
                actualizados += 1
            else:
                nuevos += 1

    return {
        "ok": True,
        "kits": len(kits),
        "filas": filas,
        "nuevos": nuevos,
        "actualizados": actualizados,
    }
