"""Carga de albaranes de Gaby: venta original → carrito, entre ambas cuentas.

Cada paso busca primero candidatos; más de una cuenta implica ambigüedad y no
escribe nada. La venta gana globalmente sobre el carrito. Sólo la principal puede
resolver por su PK legacy cuando no tiene número original. Un carrito único
actualiza todas sus ventas dentro de esa cuenta. Los duplicados se aplican en
orden del archivo y las celdas vacías conservan los valores previos.

Sólo UPDATE sobre ventas existentes, nunca INSERT. Las filas sin aplicar se
reportan completas con su posición y valores originales; el XLSX es efímero.
"""
import base64
from io import BytesIO
import re
from pathlib import Path

import openpyxl

from database import get_db

# Textos ancla para reconocer cada columna en el header (en minúsculas, sin acentos
# duros: comparamos contra el texto de la celda ya normalizado).
_ANCLAS_VENTA = ("# de venta", "num de venta", "numero de venta", "num venta", "venta")
_ANCLAS_ALBARAN = ("# de albaran", "# de albarán", "num de albaran", "numero de albaran",
                   "num albaran", "albaran", "albarán")

# Compatibilidad: la vista antigua conserva 25 ejemplos; el detalle y XLSX van completos.
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

    idx_fecha = next((i for i, cell in enumerate(rows[header_idx])
                      if _norm(cell) in ("fecha de albarán", "fecha de albaran")), None)

    por_num_venta = por_pack_id = ventas_actualizadas = 0
    no_encontrados = sin_albaran = ambiguos = 0
    pendientes = []

    def pendiente(fila, row, numero, albaran, fecha, resultado, candidatas=()):
        pendientes.append({
            "fila": fila,
            "fila_original": [v.isoformat() if hasattr(v, "isoformat") else
                              (str(v) if v is not None else "") for v in row],
            "numero_recibido": numero, "albaran": albaran, "fecha": fecha,
            "resultado": resultado, "cuentas_candidatas": sorted(candidatas),
        })

    with get_db() as conn:
        for fila, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
            if not any(v is not None and str(v).strip() for v in row):
                continue
            num_venta = _celda_a_texto(row[idx_venta]) if idx_venta < len(row) else ""
            albaran = _celda_a_texto(row[idx_albaran]) if idx_albaran < len(row) else ""
            fecha = None
            if idx_fecha is not None and idx_fecha < len(row) and row[idx_fecha] is not None:
                valor = row[idx_fecha]
                if hasattr(valor, "date"):
                    fecha = valor.date().isoformat()
                elif hasattr(valor, "isoformat"):
                    fecha = valor.isoformat()
                else:
                    fecha = str(valor).strip() or None
            if not albaran and not fecha:
                sin_albaran += 1
                pendiente(fila, row, num_venta, albaran, fecha, "Sin albarán ni fecha")
                continue

            # IDs originales en ambas cuentas; sólo principal admite la PK legacy.
            ventas = conn.execute(
                """SELECT num_venta, cuenta_ml FROM ventas_ml
                   WHERE num_venta_origen = ?
                   UNION
                   SELECT num_venta, cuenta_ml FROM ventas_ml
                   WHERE cuenta_ml = 'principal' AND num_venta = ?
                     AND NULLIF(num_venta_origen, '') IS NULL""",
                (num_venta, num_venta),
            ).fetchall() if num_venta else []
            cuentas = {v["cuenta_ml"] for v in ventas}
            if len(cuentas) > 1:
                ambiguos += 1
                pendiente(fila, row, num_venta, albaran, fecha, "Coincidencia ambigua", cuentas)
                continue
            if ventas:
                for venta in ventas:
                    cur = conn.execute(
                        "UPDATE ventas_ml SET albaran = COALESCE(NULLIF(?, ''), albaran), fecha_albaran = COALESCE(?, fecha_albaran) WHERE num_venta = ? AND cuenta_ml = ?",
                        (albaran, fecha, venta["num_venta"], venta["cuenta_ml"]),
                    )
                    ventas_actualizadas += cur.rowcount
                por_num_venta += 1
                continue

            # Sólo si ninguna venta coincidió, resolver el carrito por separado.
            cuentas = {v["cuenta_ml"] for v in conn.execute(
                "SELECT DISTINCT cuenta_ml FROM ventas_ml WHERE pack_id = ?", (num_venta,),
            ).fetchall()} if num_venta else set()
            if len(cuentas) > 1:
                ambiguos += 1
                pendiente(fila, row, num_venta, albaran, fecha, "Coincidencia ambigua", cuentas)
                continue
            if cuentas:
                cur = conn.execute(
                    "UPDATE ventas_ml SET albaran = COALESCE(NULLIF(?, ''), albaran), fecha_albaran = COALESCE(?, fecha_albaran) WHERE pack_id = ? AND cuenta_ml = ?",
                    (albaran, fecha, num_venta, next(iter(cuentas))),
                )
                por_pack_id += 1
                ventas_actualizadas += cur.rowcount
                continue
            no_encontrados += 1
            pendiente(fila, row, num_venta, albaran, fecha, "Sin coincidencia local")

    return {
        "ok": True,
        "actualizados": por_num_venta + por_pack_id,
        "por_num_venta": por_num_venta,
        "por_pack_id": por_pack_id,
        "ventas_actualizadas": ventas_actualizadas,
        "no_encontrados": no_encontrados,
        "sin_albaran": sin_albaran,
        "ambiguos": ambiguos,
        "sin_aplicar": len(pendientes),
        "filas_sin_aplicar": pendientes,
        "ejemplos_no_encontrados": [p["numero_recibido"] for p in pendientes
                                    if p["resultado"] == "Sin coincidencia local"][:MAX_EJEMPLOS],
        "reporte_sin_aplicar_base64": _reporte_pendientes(pendientes, rows[header_idx]) if pendientes else None,
    }


def _reporte_pendientes(pendientes, encabezados):
    """Reporte efímero: IDs como texto, sin fórmulas ejecutables ni histórico nuevo."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Filas sin aplicar"
    ws.append(["Fila original", "Número recibido", "Albarán", "Fecha", "Resultado",
               "Cuentas candidatas"] + [str(v or "") for v in encabezados])
    for p in pendientes:
        ws.append([str(p["fila"]), p["numero_recibido"], p["albaran"], p["fecha"] or "",
                   p["resultado"], ", ".join(p["cuentas_candidatas"])] + p["fila_original"])
    for row in ws:
        for cell in row:
            cell.data_type = "s"
            cell.number_format = "@"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for col, ancho in {"A": 15, "B": 25, "C": 22, "D": 18, "E": 30, "F": 25}.items():
        ws.column_dimensions[col].width = ancho
    salida = BytesIO()
    wb.save(salida)
    wb.close()
    return base64.b64encode(salida.getvalue()).decode("ascii")
