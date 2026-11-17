"""Albaranes mixtos, ambigüedad y reporte íntegro sin histórico."""
import base64
from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ['DATABASE_PATH'] = str(Path(_tmp.name) / 'test.db')
import openpyxl
from database import init_database, get_db
from services.parser_albaran import parse_albaran

init_database()
with get_db() as conn:
    conn.executemany('INSERT INTO ventas_ml(num_venta,num_venta_origen,cuenta_ml,pack_id,albaran,fecha_albaran) VALUES (?,?,?,?,?,?)', [
        ('legacy', None, 'principal', None, 'viejo', '2026-10-01'),
        ('secundaria::s1', 's1', 'secundaria', 'carrito', None, None),
        ('secundaria::s2', 's2', 'secundaria', 'carrito', None, None),
        ('colision', 'colision', 'principal', 'compartido', None, None),
        ('secundaria::colision', 'colision', 'secundaria', 'compartido', None, None),
        ('prioridad', 'prioridad', 'principal', None, None, None),
        ('secundaria::otra', 'otra', 'secundaria', 'prioridad', None, None),
        ('secundaria::carritocolision', 'carritocolision', 'secundaria', 'colision', None, None),
    ])
wb = openpyxl.Workbook()
ws = wb.active
ws.append(['# de venta', '# de albarán', 'Fecha de albarán', 'Nota original'])
ws.append(['legacy', '', date(2026, 11, 1), 'fecha sola'])
ws.append(['s1', 'A-1', None])
ws.append(['carrito', 'A-2', date(2026, 11, 2)])
ws.append(['s1', 'A-3', None])
ws.append(['colision', 'no escribir', None])
ws.append(['compartido', 'no escribir', None])
ws.append(['prioridad', 'venta gana', None])
ws.append(['legacy', None, None])
for i in range(30):
    ws.append([str(200001234567890123 + i), '=unsafe', None, 'nota'])
    ws.cell(ws.max_row, 2).data_type = 's'
ws.append([None, 'sin número', None, 'conservar'])
path = Path(_tmp.name) / 'mixto.xlsx'
wb.save(path)
r = parse_albaran(path)
assert r['actualizados'] == 5 and r['ventas_actualizadas'] == 6, r
assert r['ambiguos'] == 2 and r['no_encontrados'] == 31 and r['sin_albaran'] == 1
assert r['sin_aplicar'] == len(r['filas_sin_aplicar']) == 34
assert r['actualizados'] + r['sin_aplicar'] == ws.max_row - 1
with get_db() as conn:
    rows = {x['num_venta']: dict(x) for x in conn.execute('SELECT * FROM ventas_ml')}
assert rows['legacy']['albaran'] == 'viejo' and rows['legacy']['fecha_albaran'] == '2026-11-01'
assert rows['secundaria::s1']['albaran'] == 'A-3' and rows['secundaria::s1']['fecha_albaran'] == '2026-11-02'
assert rows['secundaria::s2']['albaran'] == 'A-2'
assert rows['prioridad']['albaran'] == 'venta gana'
assert rows['secundaria::otra']['albaran'] is None
assert all(rows[key]['albaran'] is None for key in ['colision', 'secundaria::colision', 'secundaria::carritocolision'])
assert r['filas_sin_aplicar'][0]['cuentas_candidatas'] == ['principal', 'secundaria']
reporte = openpyxl.load_workbook(BytesIO(base64.b64decode(r['reporte_sin_aplicar_base64'])))
ws = reporte.active
assert ws.max_row == 35
assert ws.cell(5, 2).value == '200001234567890123'
assert ws.cell(5, 2).data_type == 's' and ws.cell(5, 2).number_format == '@'
assert ws.cell(5, 3).value == '=unsafe' and ws.cell(5, 3).data_type == 's'
assert ws.cell(5, 10).value == 'nota'
assert r['filas_sin_aplicar'][3]['fila_original'][-1] == 'nota'
print('OK: mixto, legacy, prioridad, colisiones, duplicados, celdas vacías, 34 pendientes y XLSX texto seguro')
