"""Carga incremental del desglose de kits del catálogo AG."""
from pathlib import Path
import re

import openpyxl

from database import get_db


def parse_kits_ag(path: Path) -> dict:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    norm = lambda x: re.sub(r"\s+", " ", str(x or "").strip().lower())
    header_idx = None
    idx_code = None
    idx_components = None
    for i, row in enumerate(rows[:15]):
        header = [norm(x) for x in row]
        try:
            code_idx = header.index("código") if "código" in header else header.index("codigo")
        except ValueError:
            continue
        comp_idxs = [j for j, x in enumerate(header) if re.fullmatch(r"componente\s+[1-8]", x)]
        if len(set(comp_idxs)) == 8:
            header_idx, idx_code, idx_components = i, code_idx, comp_idxs
            break
    if header_idx is None:
        raise ValueError("El archivo AG debe incluir los encabezados «Código» y «Componente 1» a «Componente 8».")

    with get_db() as conn:
        proveedor = conn.execute("SELECT id FROM proveedores WHERE UPPER(codigo_bodega)='AG'").fetchone()
        if not proveedor:
            raise ValueError("No está configurado el proveedor AG.")
        seen = set()
        nuevos = actualizados = 0
        for row in rows[header_idx + 1:]:
            sku = str(row[idx_code] or "").strip().upper() if idx_code < len(row) else ""
            if not sku:
                continue
            for j in idx_components:
                component = str(row[j] or "").strip() if j < len(row) else ""
                if not component:
                    continue
                key = (sku, component.upper())
                if key in seen:
                    continue
                seen.add(key)
                exists = conn.execute("""SELECT 1 FROM kit_componentes
                    WHERE kit_sku=? AND UPPER(TRIM(componente_codigo))=? AND proveedor_id=?""",
                    (sku, component.upper(), proveedor["id"])).fetchone()
                conn.execute("""INSERT INTO kit_componentes
                    (kit_sku, componente_codigo, cantidad, proveedor_id) VALUES (?, ?, 1, ?)
                    ON CONFLICT(kit_sku, componente_codigo, proveedor_id)
                    DO UPDATE SET cantidad=1""", (sku, component, proveedor["id"]))
                if exists:
                    actualizados += 1
                else:
                    nuevos += 1
    return {"ok": True, "kits": len({k[0] for k in seen}), "relaciones": len(seen),
            "nuevos": nuevos, "actualizados": actualizados}
