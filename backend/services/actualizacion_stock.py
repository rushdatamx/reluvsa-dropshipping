"""AutoAzur stock: isolated, sequential Excel processing; no business database/API."""
import csv
from datetime import date, datetime, time as datetime_time
import json
import math
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import openpyxl
from openpyxl.cell import WriteOnlyCell

ACCOUNTS = {'nereus': ('KG', 'DIFIRSA'), 'reluvsa': ('VAZLO', 'CAUPLAS', 'KIMS', 'AG')}
PREFIXES = {'KG': ('KG-',), 'CAUPLAS': ('CAUPLAS-', 'CAU'), 'VAZLO': ('VAZLO-',), 'AG': ('AG ',)}
HEADERS = {'nereus': {'MATRIZ_75741158', 'KG_79675692', 'DIFIRSA_79236833'},
           'reluvsa': {'MATRIZ_42210569', 'KIM_73524263', 'CAUPLAS_73524265', 'AG_73522745', 'KG_79605734', 'VAZLO_74477853'}}
ROOT = Path(tempfile.gettempdir()) / 'reluvsa-stock-jobs'
ROOT.mkdir(mode=0o700, exist_ok=True)
TTL = 86400
MAX_PART = 9_500_000
JOBS = {}
LOCK = threading.RLock()
SWEEPER_STARTED = False
POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='stock')


def norm(value):
    if value is None:
        return ''
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(value) and value == int(value):
            return str(int(value))
    return str(value).strip().upper()


def integer(value, blank_zero=False):
    if value is None or str(value).strip() == '':
        return 0 if blank_zero else None
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    if not re.fullmatch(r'\d+(?:\.0+)?', text):
        return None
    try:
        return int(text.split('.')[0])
    except ValueError:
        return None


def canonical(sku, provider):
    for prefix in PREFIXES.get(provider, ()):
        if sku.startswith(prefix):
            return sku[len(prefix):]
    return sku


class Inventory:
    def __init__(self, provider):
        self.provider = provider
        self.values = {}
        self.aliases = defaultdict(set)
        self.issues = []

    def add(self, sku, stock, location):
        sku = norm(sku)
        if not sku:
            return
        value = integer(stock)
        if sku in self.values and self.values[sku] != value:
            value = None
            reason = 'Stock duplicado contradictorio o inválido'
        else:
            reason = 'Stock inválido'
        self.values[sku] = value
        self.aliases[canonical(sku, self.provider)].add(sku)
        if value is None:
            self.issues.append({'sku': sku, 'motivo': reason, 'origen': location, 'proveedor': self.provider, 'stock_recibido': stock})

    def resolve(self, sku):
        if sku in self.values:
            value = self.values[sku]
            return value, '' if value is not None else 'Stock inválido o contradictorio', sku
        candidates = self.aliases.get(canonical(sku, self.provider), set())
        if len(candidates) > 1:
            return None, 'Equivalencia ambigua', None
        if candidates:
            key = next(iter(candidates))
            value = self.values[key]
            return value, '' if value is not None else 'Stock inválido o contradictorio', key
        return None, 'Sin coincidencia', None

    def direct(self, sku):
        value, reason, _ = self.resolve(sku)
        return value, reason

    def lookup(self, sku):
        value, reason = self.direct(sku)
        if reason != 'Sin coincidencia' or '&' not in sku:
            return value, reason
        # Count resolved inventory codes: KG-A&A and KG-A&KG-A both need two A.
        repetitions = Counter()
        stocks = {}
        for component in sku.split('&'):
            part = norm(component)
            if not part:
                return None, 'Componente vacío'
            value, reason, key = self.resolve(part)
            if reason:
                return None, 'Componente %s: %s' % (part, reason)
            repetitions[key] += 1
            stocks[key] = value
        return min(stocks[key] // count for key, count in repetitions.items()), ''


def read_inventory(path, provider):
    inv = Inventory(provider)
    workbook = None
    if path.suffix == '.csv':
        # UTF-8 exports and Windows CSVs are both common.
        try:
            with path.open(encoding='utf-8-sig') as probe:
                while probe.read(1024 * 1024):
                    pass
            encoding = 'utf-8-sig'
        except UnicodeDecodeError:
            encoding = 'cp1252'
        source = path.open(encoding=encoding, newline='')
        sample = source.read(4096)
        source.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        sheets = [(path.name, csv.reader(source, dialect))]
    else:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        preferred = {'CAUPLAS': ('STOCK',), 'KIMS': ('STOCK', 'KITS')}.get(provider, ())
        selected = [s for s in workbook if norm(s.title) in preferred] if preferred else []
        if not selected:
            selected = [workbook.worksheets[0]]
        sheets = [(s.title, s.iter_rows(values_only=True)) for s in selected]
    try:
        for title, rows in sheets:
            rows = iter(rows)
            first = next(rows, None)
            if first is None:
                continue
            h = [norm(v) for v in first]
            sku_col = next((h.index(k) for k in ('ATT_SELLERSKU', 'SKU', 'ARTÍCULO', 'ARTICULO', 'CLAVE') if k in h), 0)
            stock_col = next((h.index(k) for k in ('STOCK', 'DISPONIBILIDAD', 'EXIS') if k in h), 1)
            if provider == 'VAZLO':
                stock_col = 1
            # Simplified headerless files supported; arbitrary textual headers skipped.
            if len(first) > stock_col and integer(first[stock_col]) is not None:
                inv.add(first[sku_col], first[stock_col], '%s:1' % title)
            for number, row in enumerate(rows, 2):
                if len(row) <= max(sku_col, stock_col):
                    continue
                inv.add(row[sku_col], row[stock_col], '%s:%s' % (title, number))
    finally:
        if workbook:
            workbook.close()
        else:
            source.close()
    if not inv.values:
        raise ValueError('El archivo de %s no contiene códigos de stock.' % provider)
    return inv


def encode_value(value):
    if isinstance(value, (datetime, date, datetime_time)):
        return {'__excel_type__': type(value).__name__, 'value': value.isoformat()}
    raise TypeError('Tipo de celda no compatible: %s' % type(value).__name__)


def decode_value(value):
    kind = value.get('__excel_type__')
    if kind in ('datetime', 'date', 'time'):
        return {'datetime': datetime, 'date': date, 'time': datetime_time}[kind].fromisoformat(value['value'])
    return value


def dump_line(file, data):
    file.write(json.dumps(data, ensure_ascii=False, default=encode_value) + '\n')


def analyze(directory, account, paths):
    inventories = {p: read_inventory(path, p) for p, path in paths.items()}
    totals = {p: dict.fromkeys(('coincidencias', 'modificadas', 'activaciones', 'pausas', 'sin_cambios', 'pendientes'), 0) for p in paths}
    workbook = openpyxl.load_workbook(directory / 'export.xlsx', read_only=True, data_only=False)
    count = modified = pending = 0
    try:
        rows = workbook.worksheets[0].iter_rows()
        headers = [cell.value for cell in next(rows, ())]
        h = [norm(x) for x in headers]
        if len(h) < 33 or h[16] != 'ATT_SELLERSKU' or h[4] != 'STATUS' or h[7] != 'CANTIDAD' or h[18] != 'MARCA':
            raise ValueError('Exporte AutoAzur inválido: se requieren Status E, Cantidad H, Att_SellerSKU Q y Marca S.')
        warehouses = {name: i for i, name in enumerate(h) if re.fullmatch(r'.+_\d+', name)}
        if set(warehouses) != HEADERS[account] or len(warehouses) != len(h[30:]):
            raise ValueError('Las bodegas del exporte no corresponden a la cuenta seleccionada.')
        columns = {p: next(i for name, i in warehouses.items() if name.startswith(('KIM' if p == 'KIMS' else p) + '_')) for p in paths}
        if account == 'nereus' and (columns.get('KG', 31) != 31 or columns.get('DIFIRSA', 32) != 32):
            raise ValueError('Nereus requiere KG en AF y DIFIRSA en AG.')
        (directory / 'headers.json').write_text(json.dumps(headers, default=str), encoding='utf-8')
        with (directory / 'details.jsonl').open('w', encoding='utf-8') as details, (directory / 'changes.jsonl').open('w', encoding='utf-8') as changes:
            for inv in inventories.values():
                for issue in inv.issues:
                    pending += 1
                    totals[inv.provider]['pendientes'] += 1
                    dump_line(details, dict(issue, tipo='pendiente', fila=None, anterior=None, propuesto=None))
            for number, raw in enumerate(rows, 2):
                count += 1
                row = [cell.value for cell in raw] + [None] * (len(headers) - len(raw))
                sku = norm(row[16])
                eligible = [p for p in paths if account != 'nereus' or norm(row[18]) == {'KG': 'KEEPONGREEN', 'DIFIRSA': 'ENGINE FIL'}[p]]
                if not eligible:
                    continue
                proposed = row.copy()
                reasons = {}
                matched = []
                for p in eligible:
                    value, reason = inventories[p].lookup(sku)
                    if reason:
                        reasons[p] = reason
                    else:
                        matched.append(p)
                        totals[p]['coincidencias'] += 1
                        proposed[columns[p]] = value
                # Invalid existing warehouse values block the row even if that warehouse was loaded.
                invalid = [headers[i] for i in warehouses.values() if integer(row[i], True) is None]
                ambiguous = any('ambigua' in reason.lower() for reason in reasons.values())
                if invalid or ambiguous:
                    proposed = row.copy()
                    for p in eligible:
                        reasons[p] = 'Bodega inválida: ' + ', '.join(invalid) if invalid else 'Fila bloqueada por equivalencia ambigua'
                elif matched:
                    proposed[7] = sum(integer(proposed[i], True) for i in warehouses.values())
                    if row[4] in ('ACTIVO', 'PAUSADO'):
                        proposed[4] = 'ACTIVO' if proposed[7] >= 5 else 'PAUSADO'
                # Numeric strings equal to numeric values are not changes by themselves.
                changed = [i for i in (4, 7, *columns.values()) if proposed[i] != row[i] and not (i != 4 and integer(proposed[i], True) == integer(row[i], True))]
                if not changed:
                    proposed = row.copy()
                if changed:
                    modified += 1
                    dump_line(changes, {'fila': number, 'valores': proposed, 'tipos': {i: cell.data_type for i, cell in enumerate(raw) if cell.data_type in ('f', 'e') and proposed[i] == row[i]}})
                for p in eligible:
                    if p in reasons:
                        totals[p]['pendientes'] += 1
                        pending += 1
                    else:
                        totals[p]['modificadas' if changed else 'sin_cambios'] += 1
                        if changed and proposed[4] != row[4]:
                            totals[p]['activaciones' if proposed[4] == 'ACTIVO' else 'pausas'] += 1
                    dump_line(details, {'fila': number, 'sku': sku, 'proveedor': p, 'tipo': 'pendiente' if p in reasons else 'modificada' if changed else 'sin_cambios', 'motivo': reasons.get(p, 'Stock actualizado' if changed else 'Sin cambios'), 'anterior': {'estado': row[4], 'total': row[7], 'bodega': row[columns[p]]}, 'propuesto': {'estado': proposed[4], 'total': proposed[7], 'bodega': proposed[columns[p]]}})
    finally:
        workbook.close()
    summary = {'filas': count, 'modificadas': modified, 'pendientes': pending, 'proveedores': totals}
    (directory / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')
    with (directory / 'reporte.csv').open('w', encoding='utf-8-sig', newline='') as out:
        writer = csv.writer(out)
        metrics = ('coincidencias', 'modificadas', 'activaciones', 'pausas', 'sin_cambios', 'pendientes')
        writer.writerow(['tipo', 'proveedor', 'fila', 'sku', 'motivo', 'stock_recibido',
                         'estado_anterior', 'total_anterior', 'bodega_anterior',
                         'estado_propuesto', 'total_propuesto', 'bodega_propuesta', *metrics])
        for p, values in totals.items():
            writer.writerow(['resumen', p] + [''] * 10 + [values[key] for key in metrics])
        for item in lines(directory / 'details.jsonl'):
            if item['tipo'] == 'pendiente':
                before = item['anterior'] or {}
                after = item['propuesto'] or {}
                values = [item.get(k, '') for k in ('tipo', 'proveedor', 'fila', 'sku', 'motivo', 'stock_recibido')]
                values += [before.get(k, '') for k in ('estado', 'total', 'bodega')]
                values += [after.get(k, '') for k in ('estado', 'total', 'bodega')]
                writer.writerow([safe_csv(value) for value in values] + [''] * len(metrics))
    return summary


def safe_csv(value):
    text = str(value) if value is not None else ''
    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text


def lines(path):
    with path.open(encoding='utf-8') as file:
        for line in file:
            yield json.loads(line, object_hook=decode_value)


def append_values(sheet, values, types=None):
    # Keep strings as strings, including identifiers and text beginning '='.
    cells = []
    for index, value in enumerate(values):
        cell = WriteOnlyCell(sheet, value=value)
        if isinstance(value, str):
            cell.data_type = 's'
        if types and str(index) in types:
            cell.data_type = types[str(index)]
        cells.append(cell)
    sheet.append(cells)


def generate(directory, limit=MAX_PART):
    headers = json.loads((directory / 'headers.json').read_text(encoding='utf-8'))
    count = sum(1 for _ in lines(directory / 'changes.jsonl'))
    parts = []

    def write_part(start, end):
        path = directory / ('stock_%04d.xlsx' % (len(parts) + 1))
        book = openpyxl.Workbook(write_only=True)
        sheet = book.create_sheet('Stock')
        append_values(sheet, headers)
        for index, item in enumerate(lines(directory / 'changes.jsonl')):
            if index >= end:
                break
            if index >= start:
                append_values(sheet, item['valores'], item.get('tipos'))
        book.save(path)
        if path.stat().st_size > limit:
            path.unlink()
            if end - start <= 1:
                raise ValueError('Una fila excede el tamaño máximo de importación.')
            middle = (start + end) // 2
            write_part(start, middle)
            write_part(middle, end)
            return
        check = openpyxl.load_workbook(path, read_only=True)
        try:
            actual = check.worksheets[0].iter_rows(values_only=True)
            if list(next(actual)) != headers or sum(1 for _ in actual) != end - start:
                raise ValueError('Falló la verificación del Excel generado.')
        finally:
            check.close()
        parts.append({'nombre': path.name, 'bytes': path.stat().st_size, 'filas': end - start})

    if count:
        write_part(0, count)
        with zipfile.ZipFile(directory / 'stock.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            for part in parts:
                archive.write(directory / part['nombre'], part['nombre'])
    return parts


def cleanup():
    with LOCK:
        for key, job in list(JOBS.items()):
            if time.time() - job['creado'] >= TTL and job['estado'] not in ('pendiente', 'en_proceso'):
                shutil.rmtree(ROOT / key, ignore_errors=True)
                del JOBS[key]
        for directory in ROOT.iterdir():
            if directory.is_dir() and re.fullmatch(r'[a-f0-9]{32}', directory.name) and directory.name not in JOBS and time.time() - directory.stat().st_mtime >= TTL:
                shutil.rmtree(directory, ignore_errors=True)


def run_job(key, action, *args):
    with LOCK:
        JOBS[key].update(estado='en_proceso', error=None)
    try:
        result = action(ROOT / key, *args)
        with LOCK:
            JOBS[key].update(estado='completo', **({'resumen': result} if action == analyze else {'archivos': result}))
    except Exception as exc:
        with LOCK:
            JOBS[key].update(estado='error', error=str(exc) if isinstance(exc, ValueError) else 'Archivo inválido o no procesable. Verifica que sea un Excel/CSV válido y vuelve a cargar.')


def sweep_forever():
    while True:
        time.sleep(300)
        cleanup()


def start_cleanup():
    global SWEEPER_STARTED
    with LOCK:
        if not SWEEPER_STARTED:
            threading.Thread(target=sweep_forever, name='stock-expiry', daemon=True).start()
            SWEEPER_STARTED = True
    cleanup()


def create_job(account):
    start_cleanup()
    key = uuid.uuid4().hex
    directory = ROOT / key
    directory.mkdir(mode=0o700)
    with LOCK:
        JOBS[key] = {'id': key, 'cuenta': account, 'creado': time.time(), 'estado': 'pendiente', 'fase': 'analisis', 'archivos': []}
    return key, directory
