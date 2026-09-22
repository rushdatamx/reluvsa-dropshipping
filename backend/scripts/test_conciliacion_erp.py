"""Regresión del módulo ERP: parser, duplicados y cruce dinámico por proveedor."""
import json
import os
import sys
import tempfile
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
work = Path(tempfile.mkdtemp(prefix="reluvsa_erp_test_"))
os.environ["DATABASE_PATH"] = str(work / "test.db")

from database import get_db, init_database  # noqa: E402
from routers.conciliacion_erp import _resultados  # noqa: E402
from services.detector_archivo import detectar_tipo_xlsx  # noqa: E402
from services.parser_erp import parse_erp  # noqa: E402


def main():
    xlsx = work / "erp.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Referencia", "Proveedor -> Nombre", "Fecha Referencia", "Fecha Captura"])
    ws.append(["K29344", "KIMS AUTO CORPORATION", "2026-09-01", "2026-09-02"])
    ws.append([970094252, "QUALITY HOSES SA DE CV", "2026-09-01", "2026-09-02"])
    ws.append([970094252, "QUALITY HOSES SA DE CV", "2026-09-01", "2026-09-02"])
    ws.append([29345, "KIMS AUTO CORPORATION", "2026-09-01", "2026-09-02"])
    wb.save(xlsx)
    assert detectar_tipo_xlsx(xlsx) == "erp_facturas"
    parsed = parse_erp(xlsx)
    assert len(parsed["entradas"]) == 4
    assert parsed["entradas"][0]["proveedor_codigo"] == "KIM"
    assert parsed["entradas"][1]["proveedor_codigo"] == "CAUPLAS"
    assert "debe iniciar con K" in parsed["entradas"][3]["motivo_revision"]

    init_database()
    with get_db() as conn:
        carga = conn.execute("INSERT INTO cargas_erp (nombre_archivo, fecha_referencia_desde, fecha_referencia_hasta, total_entradas) VALUES (?, ?, ?, ?)", ("erp.xlsx", "2026-09-01", "2026-09-01", 4)).lastrowid
        conn.executemany(
            """INSERT INTO entradas_erp (carga_id, numero_fila, fila_original_json, proveedor_original, proveedor_codigo, referencia_original, referencia_normalizada, fecha_referencia, fecha_captura, motivo_revision)
               VALUES (:carga_id,:numero_fila,:fila_original_json,:proveedor_original,:proveedor_codigo,:referencia_original,:referencia_normalizada,:fecha_referencia,:fecha_captura,:motivo_revision)""",
            [{**x, "carga_id": carga} for x in parsed["entradas"]],
        )
        _, before = _resultados(conn, carga)
        assert sum(x["estado"] == "Pendiente de proveedor" for x in before) == 3
        assert sum(x["duplicada_erp"] for x in before) == 2
        kim = conn.execute("SELECT id FROM proveedores WHERE codigo_bodega='KIM'").fetchone()[0]
        cauplas = conn.execute("SELECT id FROM proveedores WHERE codigo_bodega='CAUPLAS'").fetchone()[0]
        conn.execute("INSERT INTO facturas (proveedor_id, uuid_cfdi, serie, folio, fecha_factura) VALUES (?, ?, ?, ?, ?)", (kim, "kim-ok", "K", "29344", "2026-09-01"))
        conn.execute("INSERT INTO facturas (proveedor_id, uuid_cfdi, serie, folio, fecha_factura) VALUES (?, ?, ?, ?, ?)", (cauplas, "cauplas-ok", "CD", "970094252", "2026-09-01"))
        # Fuera del rango: nunca puede producir "Factura sin entrada ERP".
        conn.execute("INSERT INTO facturas (proveedor_id, uuid_cfdi, serie, folio, fecha_factura) VALUES (?, ?, ?, ?, ?)", (kim, "fuera-rango", "K", "99999", "2026-09-02"))
        _, after = _resultados(conn, carga)
        assert sum(x["estado"] == "Confirmada" for x in after) == 3
        assert not any(x.get("uuid_cfdi") == "fuera-rango" for x in after)
    print("OK: conciliación ERP (parser, alias, duplicados, cruce dinámico y rango)")


if __name__ == "__main__":
    main()
