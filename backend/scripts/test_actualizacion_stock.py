"""Business rules and authenticated API; synthetic fixtures only."""
import json
import tempfile
import time
import unittest
from pathlib import Path

import openpyxl
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import actualizacion_stock as api
from routers.auth import get_current_user
from models import UserInfo
from services import actualizacion_stock as svc


def excel(path, rows, title='Stock'):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    book.save(path)


def headers(account):
    base = ['Col%s' % i for i in range(30)]
    for i, value in {0: 'ID', 2: 'VarianteID', 4: 'Status', 7: 'Cantidad', 16: 'Att_SellerSKU', 18: 'Marca'}.items():
        base[i] = value
    return base + (['MATRIZ_75741158', 'KG_79675692', 'Difirsa_79236833'] if account == 'nereus' else ['MATRIZ_42210569', 'KIM_73524263', 'CAUPLAS_73524265', 'AG_73522745', 'KG_79605734', 'VAZLO_74477853'])


def row(account, sku, state='ACTIVO', brand='KeepOnGreen', stock=9, matriz=0, ident='123', variant='01'):
    values = [''] * len(headers(account))
    for i, value in {0: ident, 2: variant, 4: state, 7: stock + matriz, 16: sku, 18: brand, 30: matriz, 31 if account == 'nereus' else 35: stock}.items():
        values[i] = value
    return values


class Rules(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def analyze(self, rows, stock, account='nereus', provider='KG'):
        excel(self.directory / 'export.xlsx', [headers(account)] + rows)
        path = self.directory / 'stock.xlsx'
        excel(path, [['Código', 'Stock diario']] + stock)
        summary = svc.analyze(self.directory, account, {provider: path})
        return summary, list(svc.lines(self.directory / 'changes.jsonl'))

    def test_boundaries_states_brands_and_other_warehouses(self):
        rows = [row('nereus', str(n)) for n in (0, 4, 5, 6)]
        rows += [row('nereus', 'X', state='PARA REVISAR'), row('nereus', 'Y', brand='OTRA'), row('nereus', 'Z', brand=' keepongreen ', matriz=4)]
        summary, changes = self.analyze(rows, [(str(n), n) for n in (0, 4, 5, 6)] + [('X', 0), ('Y', 0), ('Z', 1)])
        self.assertEqual(summary['modificadas'], 6)
        self.assertEqual([x['valores'][4] for x in changes], ['PAUSADO', 'PAUSADO', 'ACTIVO', 'ACTIVO', 'PARA REVISAR', 'ACTIVO'])
        self.assertEqual(changes[-1]['valores'][7], 5)
        self.assertEqual(changes[-1]['valores'][30], 4)
        self.assertEqual(changes[-1]['valores'][18], ' keepongreen ')

    def test_equivalences_conflicts_and_packages(self):
        inv = svc.Inventory('CAUPLAS')
        for sku, value in [('001', 10), ('CAU001', 11), ('A', 8), ('B', 5), ('KIT1', 6), ('BAD', -1), ('DUP', 5), ('DUP', 6), ('SAME', 4), ('SAME', 4), ('A&A', 20), ('X-Y', 5)]:
            inv.add(sku, value, 'test')
        self.assertEqual(inv.lookup('001'), (10, ''))
        self.assertIn('ambigua', inv.lookup('CAUPLAS-001')[1])
        self.assertEqual(inv.lookup('A&A'), (20, ''))
        self.assertEqual(inv.lookup('A&A&B'), (4, ''))
        self.assertEqual(inv.lookup('KIT1'), (6, ''))
        self.assertIsNone(inv.lookup('KIT2')[0])
        for sku in ['BAD', 'DUP', '1', 'XY', 'X-Y-K', 'A&MISSING']:
            self.assertIsNone(inv.lookup(sku)[0])
        self.assertEqual(inv.lookup('SAME'), (4, ''))
        for value in [None, '', ' ', '#REF!', -1, 1.5, 'nan', True]:
            self.assertIsNone(svc.integer(value))
        for value in [0, 4, 5, 6, '10.0']:
            self.assertIsNotNone(svc.integer(value))
        self.assertEqual(svc.norm(123.0), '123')
        self.assertEqual(svc.norm(' 001 '), '001')

    def test_partial_relu_and_preservation(self):
        r = row('reluvsa', 'VAZLO-A', brand='Cualquiera', matriz=2)
        r[31] = 3
        summary, changes = self.analyze([r, row('reluvsa', 'ABSENT')], [('A', 1)], 'reluvsa', 'VAZLO')
        self.assertEqual(summary['modificadas'], 1)
        result = changes[0]['valores']
        self.assertEqual(result[7], 6)
        self.assertEqual(result[31], 3)
        for i in range(len(r)):
            if i not in (4, 7, 35):
                self.assertEqual(result[i], r[i] or None)
        self.assertEqual(summary['proveedores']['VAZLO']['pendientes'], 1)

    def test_any_ambiguity_blocks_whole_row(self):
        r = row('reluvsa', 'CAUPLAS-001')
        excel(self.directory / 'export.xlsx', [headers('reluvsa'), r])
        paths = {}
        for p, data in [('CAUPLAS', [('001', 2), ('CAU001', 3)]), ('AG', [('CAUPLAS-001', 5)])]:
            paths[p] = self.directory / (p + '.xlsx')
            excel(paths[p], [['SKU', 'STOCK']] + data)
        summary = svc.analyze(self.directory, 'reluvsa', paths)
        self.assertEqual(summary['modificadas'], 0)
        self.assertEqual(summary['proveedores']['AG']['pendientes'], 1)

    def test_invalid_existing_warehouse_and_wrong_account(self):
        r = row('nereus', 'A'); r[30] = '#REF!'
        summary, changes = self.analyze([r], [('A', 0)])
        self.assertEqual(changes, [])
        self.assertEqual(summary['pendientes'], 1)
        with self.assertRaisesRegex(ValueError, 'cuenta'):
            svc.analyze(self.directory, 'reluvsa', {'VAZLO': self.directory / 'stock.xlsx'})

    def test_kims_sheets_and_vazlo_second_column(self):
        path = self.directory / 'kim.xlsx'
        book = openpyxl.Workbook(); s = book.active; s.title = 'STOCK'
        s.append(['Att_SellerSKU', 'SKUKIMS', 'Marca', 'STOCK']); s.append(['A', 'different', 'Brand', 8])
        s = book.create_sheet('KITS'); s.append(['Att_SellerSKU', None, None, 'STOCK']); s.append(['KIT1', 'A', None, '#REF!'])
        s = book.create_sheet('Auxiliar'); s.append(['A', 99]); book.save(path)
        inv = svc.read_inventory(path, 'KIMS')
        self.assertEqual(inv.lookup('A'), (8, ''))
        self.assertIsNone(inv.lookup('KIT1')[0])
        self.assertEqual(len(inv.issues), 1)
        excel(path, [['SKU', None, 'CDMX'], ['A', 7, 99]])
        self.assertEqual(svc.read_inventory(path, 'VAZLO').lookup('A'), (7, ''))

    def test_generation_size_order_identifiers_no_duplicates(self):
        rows = [row('nereus', 'A', ident=str(i), variant='000' + str(i)) for i in range(100)]
        summary, changes = self.analyze(rows, [('A', 0)])
        parts = svc.generate(self.directory, limit=6500)
        self.assertGreater(len(parts), 1)
        result = []
        for part in parts:
            self.assertLessEqual(part['bytes'], 6500)
            book = openpyxl.load_workbook(self.directory / part['nombre'], read_only=True)
            data = list(book.active.values); book.close()
            self.assertEqual(list(data[0]), headers('nereus'))
            result.extend(tuple(r) + (None,) * (len(headers("nereus")) - len(r)) for r in data[1:])
        self.assertEqual(len(result), summary['modificadas'])
        self.assertEqual([r[0] for r in result], [str(i) for i in range(100)])
        self.assertEqual([r[2] for r in result], ['000' + str(i) for i in range(100)])
        self.assertEqual([list(r) for r in result], [c['valores'] for c in changes])

    def test_dates_formulas_and_text_cells_remain_typed(self):
        from datetime import datetime
        r = row('nereus', 'A')
        r[1] = '=1+2'
        r[5] = datetime(2026, 11, 6)
        r[6] = '000123'
        r[7] = '=SUM(AE2:AG2)'
        self.analyze([r], [('A', 0)])
        svc.generate(self.directory)
        book = openpyxl.load_workbook(self.directory / 'stock_0001.xlsx', read_only=True, data_only=False)
        cells = list(book.active.iter_rows())[1]
        self.assertEqual(cells[1].value, '=1+2')
        self.assertEqual(cells[1].data_type, 'f')
        self.assertEqual(cells[5].value, r[5])
        self.assertEqual(cells[6].value, '000123')
        book.close()

    def test_difirsa_and_all_provider_prefixes(self):
        summary, changes = self.analyze([row('nereus', 'ABC', brand=' engine fil ')], [('ABC', 4)], 'nereus', 'DIFIRSA')
        self.assertEqual(changes[0]['valores'][32], 4)
        self.assertEqual(changes[0]['valores'][31], 9)
        self.assertEqual(changes[0]['valores'][7], 13)
        for provider, sku in [('KG', 'KG-A'), ('CAUPLAS', 'CAU001'), ('VAZLO', 'VAZLO-A'), ('AG', 'AG A')]:
            inv = svc.Inventory(provider)
            bare = '001' if provider == 'CAUPLAS' else 'A'
            inv.add(bare, 6, 'test')
            self.assertEqual(inv.lookup(sku), (6, ''))
            self.assertEqual(inv.lookup(sku + '&' + sku), (3, ''))
            self.assertEqual(inv.lookup(sku + ' & ' + bare), (3, ''))

    def test_no_changes_no_empty_excel(self):
        self.analyze([row('nereus', 'A', stock=6)], [('A', 6)])
        self.assertEqual(svc.generate(self.directory), [])
        self.assertFalse((self.directory / 'stock.zip').exists())


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.add_middleware(api.UploadLimitMiddleware)
        self.app.include_router(api.router)
        self.client = TestClient(self.app)

    def auth(self, role):
        self.app.dependency_overrides[get_current_user] = lambda: UserInfo(user_id=1, email='test@example.com', rol=role, proveedor_id=1)

    def test_auth_expiry_and_download_path(self):
        self.assertIn(self.client.get('/api/actualizacion-stock/missing').status_code, (401, 403))
        self.auth('proveedor')
        self.assertEqual(self.client.get('/api/actualizacion-stock/missing').status_code, 403)
        self.auth('admin')
        key, path = svc.create_job('nereus')
        self.assertEqual(self.client.get('/api/actualizacion-stock/' + key).status_code, 200)
        self.assertEqual(self.client.get('/api/actualizacion-stock/' + key + '/archivos/export.xlsx').status_code, 404)
        svc.JOBS[key]['creado'] -= svc.TTL + 1
        self.assertEqual(self.client.get('/api/actualizacion-stock/' + key).status_code, 410)
        svc.JOBS[key]['estado'] = 'completo'; svc.cleanup()
        self.assertFalse(path.exists())

    def test_background_upload_poll_results_generation(self):
        self.auth('admin')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            excel(root / 'export.xlsx', [headers('nereus'), row('nereus', 'A')])
            excel(root / 'stock.xlsx', [['SKU', 'STOCK'], ['A', 0]])
            response = self.client.post('/api/actualizacion-stock/analizar', data={'cuenta': 'nereus', 'proveedores': '["KG"]'}, files=[('exporte', ('x.xlsx', (root / 'export.xlsx').read_bytes())), ('stocks', ('s.xlsx', (root / 'stock.xlsx').read_bytes()))])
            self.assertEqual(response.status_code, 202, response.text)
            key = response.json()['id']
            for _ in range(100):
                state = self.client.get('/api/actualizacion-stock/' + key).json()
                if state['estado'] not in ('en_proceso', 'pendiente'): break
                time.sleep(.02)
            self.assertEqual(state['estado'], 'completo', state)
            response = self.client.get('/api/actualizacion-stock/' + key + '/resultados?tipo=modificada&limite=1')
            self.assertEqual(response.json()['total'], 1)
            self.assertEqual(len(response.json()['resultados']), 1)
            self.assertEqual(self.client.post('/api/actualizacion-stock/' + key + '/generar').status_code, 202)
            for _ in range(100):
                state = self.client.get('/api/actualizacion-stock/' + key).json()
                if state['estado'] not in ('en_proceso', 'pendiente'): break
                time.sleep(.02)
            self.assertEqual(state['estado'], 'completo', state)
            for filename in ['stock_0001.xlsx', 'stock.zip', 'reporte.csv']:
                self.assertEqual(self.client.get('/api/actualizacion-stock/' + key + '/archivos/' + filename).status_code, 200)

    def test_stream_total_limit_and_invalid_background_error(self):
        self.auth('admin')
        previous = api.MAX_UPLOAD
        api.MAX_UPLOAD = 10
        try:
            response = self.client.post('/api/actualizacion-stock/analizar', data={'cuenta': 'nereus', 'proveedores': '["KG"]'}, files=[('exporte', ('x.xlsx', b'123')), ('stocks', ('s.csv', b'A,1'))])
            self.assertEqual(response.status_code, 413)
        finally:
            api.MAX_UPLOAD = previous
        response = self.client.post('/api/actualizacion-stock/analizar', data={'cuenta': 'nereus', 'proveedores': '["KG"]'}, files=[('exporte', ('x.xlsx', b'not an excel')), ('stocks', ('s.csv', b'SKU,STOCK\nA,1'))])
        self.assertEqual(response.status_code, 202)
        key = response.json()['id']
        for _ in range(100):
            state = self.client.get('/api/actualizacion-stock/' + key).json()
            if state['estado'] not in ('en_proceso', 'pendiente'): break
            time.sleep(.02)
        self.assertEqual(state['estado'], 'error')
        self.assertTrue(state['error'])

    def test_upload_limit_and_invalid_file(self):
        self.auth('admin')
        previous = api.MAX_FILE
        api.MAX_FILE = 100
        try:
            response = self.client.post('/api/actualizacion-stock/analizar', data={'cuenta': 'nereus', 'proveedores': '["KG"]'}, files=[('exporte', ('x.xlsx', b'1' * 101)), ('stocks', ('s.csv', b'A,1'))])
            self.assertEqual(response.status_code, 413)
        finally:
            api.MAX_FILE = previous
        response = self.client.post('/api/actualizacion-stock/analizar', data={'cuenta': 'nereus', 'proveedores': '["KG"]'}, files=[('exporte', ('x.csv', b'1')), ('stocks', ('s.csv', b'A,1'))])
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
