"""
Parser del Excel de números de albarán que aporta Gaby.

Es un archivo simple de 2 columnas: # de venta + # de albarán. Gaby lo arma a mano
con los albaranes que va recibiendo y lo re-sube cuando tiene más. El portal cruza
contra ventas_ml y guarda el albarán en ventas_ml.albaran para mostrarlo en la
pestaña Ventas.

⭐ EL CRUCE ES EN DOS PASOS (2026-08-12), no sólo por num_venta:

    paso 1: num_venta  (= order.id, la PK)   -> si hay match, GANA y no se sigue
    paso 2: pack_id    (= el # de carrito)   -> sólo si el paso 1 no encontró nada

Gaby captura el número que ML le muestra EN PANTALLA, y ML muestra el `pack_id`
cuando la venta viene de un carrito y el `order.id` cuando no. Su archivo trae los
dos tipos mezclados sin que ella pueda distinguirlos. Medido contra prod con el
archivo real de KIMS: 79 cruzaban por num_venta y 52 por pack_id — exactamente los
"79 actualizados / 52 no encontrados" que reportó. 79+52=131 = todas las filas.

🔴 NUNCA consultar las dos llaves en un solo WHERE con un OR. Hay 268 números que son
order.id de una venta y pack_id de OTRA distinta: la consulta única devolvería 2
ventas y escribiría el albarán en la equivocada. El orden importa y los pasos van
SEPARADOS. (Misma regla ya fijada para el matcher de facturas en CLAUDE.md.)

Un pack_id puede colgar VARIAS ventas (787 packs multi-venta en prod, de 2 a 6): el
albarán se escribe en TODAS, porque el carrito es un solo paquete físico y la nota de
entrega ampara todo lo que viaja dentro (decisión de Mario, 2026-08-12).

Solo ENRIQUECE ventas ya cargadas (UPDATE, nunca INSERT): si un número del Excel no
existe ni como num_venta ni como pack_id, se reporta como no_encontrado y no se crea
fila huérfana. Una fila con albarán vacío se ignora (no borra el albarán existente,
por si Gaby re-sube parcial).

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

# Cuántos números no encontrados se devuelven al frontend. Gaby necesita verlos para
# ir a buscarlos a ML, pero un archivo roto no debe devolver miles de filas.
MAX_EJEMPLOS = 25


def _norm(val) -> str:
    """Normaliza una celda a string en minúsculas y sin espacios extra."""
    if val is None:
        return ""
    return re.sub(r"\s+", " ", str(val).strip().lower())


def _celda_a_texto(val) -> str:
    """Convierte una celda a texto plano.

    Excel entrega los números como int/float, y en ventas_ml tanto `num_venta` como
    `pack_id` son TEXT: sin esto, un 2000014366425187 numérico no cruzaría, y un
    float dejaría un '.0' pegado al albarán.
    """
    if val is None:
        return ""
    txt = str(val).strip()
    if txt.endswith(".0"):
        txt = txt[:-2]
    return txt


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

    por_num_venta = 0        # filas que cruzaron por order.id (paso 1)
    por_pack_id = 0          # filas que cruzaron por pack_id  (paso 2)
    ventas_actualizadas = 0  # filas de ventas_ml escritas (un pack puede ser >1)
    no_encontrados = 0
    sin_albaran = 0
    ejemplos_no_encontrados = []

    with get_db() as conn:
        for row in rows[header_idx + 1:]:
            if idx_venta >= len(row):
                continue
            num_venta = _celda_a_texto(row[idx_venta])
            if not num_venta:
                continue

            albaran = _celda_a_texto(row[idx_albaran]) if idx_albaran < len(row) else ""
            if not albaran:
                sin_albaran += 1
                continue  # no tocar: evita borrar un albarán existente al re-subir parcial

            # ── Paso 1: por num_venta (order.id). Si cruza, gana y no se sigue. ──
            cur = conn.execute(
                "UPDATE ventas_ml SET albaran = ? WHERE num_venta = ?",
                (albaran, num_venta),
            )
            if cur.rowcount:
                por_num_venta += 1
                ventas_actualizadas += cur.rowcount
                continue

            # ── Paso 2: por pack_id (el # de carrito que ML muestra en pantalla). ──
            # Consulta SEPARADA a propósito: combinarla con el paso 1 cruzaría números
            # que son order.id de una venta y pack_id de otra distinta.
            cur = conn.execute(
                "UPDATE ventas_ml SET albaran = ? WHERE pack_id = ?",
                (albaran, num_venta),
            )
            if cur.rowcount:
                # El carrito viaja junto: el albarán ampara todas sus ventas.
                por_pack_id += 1
                ventas_actualizadas += cur.rowcount
                continue

            no_encontrados += 1  # venta no cargada aún; no creamos fila huérfana
            if len(ejemplos_no_encontrados) < MAX_EJEMPLOS:
                ejemplos_no_encontrados.append(num_venta)

    return {
        "ok": True,
        # `actualizados` = filas del Excel que encontraron su venta. Se conserva el
        # nombre porque es lo que Gaby lee como "cuántos entraron".
        "actualizados": por_num_venta + por_pack_id,
        "por_num_venta": por_num_venta,
        "por_pack_id": por_pack_id,
        "ventas_actualizadas": ventas_actualizadas,
        "no_encontrados": no_encontrados,
        "sin_albaran": sin_albaran,
        "ejemplos_no_encontrados": ejemplos_no_encontrados,
    }
