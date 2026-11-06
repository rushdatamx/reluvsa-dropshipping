"""Administrative, asynchronous file-only stock workflow."""
import json
import shutil
import time
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from python_multipart import MultipartParser
from python_multipart.multipart import parse_options_header
from routers.auth import require_admin
from services import actualizacion_stock as svc

router = APIRouter(prefix='/api/actualizacion-stock', tags=['actualizacion-stock'], dependencies=[Depends(require_admin)])
MAX_FILE = 100_000_000
MAX_UPLOAD = 250_000_000


class UploadLimitMiddleware:
    """Bound the actual ASGI stream before multipart parsing/spooling."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') != '/api/actualizacion-stock/analizar':
            return await self.app(scope, receive, send)
        received = 0
        part_size = 0
        content_type = dict(scope.get('headers', [])).get(b'content-type', b'')
        _, options = parse_options_header(content_type)

        def begin_part():
            nonlocal part_size
            part_size = 0

        def part_data(data, start, end):
            nonlocal part_size
            part_size += end - start
            if part_size > MAX_FILE:
                raise HTTPException(413, 'Máximo 100 MB por archivo y 250 MB por carga.')

        parser = MultipartParser(options[b'boundary'], {'on_part_begin': begin_part, 'on_part_data': part_data}) if b'boundary' in options else None

        async def limited_receive():
            nonlocal received
            message = await receive()
            received += len(message.get('body', b''))
            if received > MAX_UPLOAD:
                raise HTTPException(413, 'La carga supera 250 MB. Divide los proveedores en otra carga.')
            if parser and message.get('body'):
                parser.write(message['body'])
            return message

        await self.app(scope, limited_receive, send)


def get_job(key):
    with svc.LOCK:
        job = svc.JOBS.get(key)
        if not job or time.time() - job['creado'] >= svc.TTL:
            raise HTTPException(410, 'Sesión expirada o reiniciada. Vuelve a cargar los archivos.')
        return dict(job)


@router.post('/analizar', status_code=202)
async def start(cuenta: str = Form(...), proveedores: str = Form(...), exporte: UploadFile = File(...), stocks: List[UploadFile] = File(...)):
    if cuenta not in svc.ACCOUNTS:
        raise HTTPException(400, 'Selecciona RELUVSA o Nereus.')
    try:
        providers = json.loads(proveedores)
        if not isinstance(providers, list) or not providers or len(providers) != len(stocks) or any(not isinstance(p, str) or p not in svc.ACCOUNTS[cuenta] for p in providers) or len(set(providers)) != len(providers):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(400, 'Asigna cada archivo a un proveedor válido, sin repetir proveedores.')
    files = [exporte] + stocks
    if Path(exporte.filename or '').suffix.lower() != '.xlsx' or any(Path(f.filename or '').suffix.lower() not in ('.xlsx', '.csv') for f in stocks):
        raise HTTPException(400, 'El exporte debe ser .xlsx y los stocks .xlsx o .csv.')
    with svc.LOCK:
        if sum(j['estado'] in ('pendiente', 'en_proceso') for j in svc.JOBS.values()) >= 4:
            raise HTTPException(429, 'Hay varios trabajos en proceso. Intenta más tarde.')
    key, directory = svc.create_job(cuenta)
    paths = {}
    total = 0
    try:
        for index, file in enumerate(files):
            path = directory / ('export.xlsx' if index == 0 else 'stock_%s%s' % (index, Path(file.filename).suffix.lower()))
            size = 0
            with path.open('wb') as output:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_FILE or total > MAX_UPLOAD:
                        raise HTTPException(413, 'Máximo 100 MB por archivo y 250 MB por carga.')
                    output.write(chunk)
            if size == 0:
                raise HTTPException(400, 'Uno de los archivos está vacío.')
            if index:
                paths[providers[index - 1]] = path
        svc.POOL.submit(svc.run_job, key, svc.analyze, cuenta, paths)
        return get_job(key)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        with svc.LOCK:
            svc.JOBS.pop(key, None)
        raise
    finally:
        for file in files:
            await file.close()


@router.get('/{key}')
def status(key: str):
    return get_job(key)


@router.get('/{key}/resultados')
def results(key: str, pagina: int = Query(1, ge=1), limite: int = Query(25, ge=1, le=100), tipo: str = Query('todos', pattern='^(todos|pendiente|modificada|sin_cambios)$'), proveedor: str = ''):
    job = get_job(key)
    if 'resumen' not in job:
        raise HTTPException(409, 'El análisis aún no está completo.')
    items = []
    count = 0
    for item in svc.lines(svc.ROOT / key / 'details.jsonl'):
        if (tipo == 'todos' or item['tipo'] == tipo) and (not proveedor or item['proveedor'] == proveedor):
            if (pagina - 1) * limite <= count < pagina * limite:
                items.append(item)
            count += 1
    return {'resultados': items, 'total': count, 'pagina': pagina}


@router.post('/{key}/generar', status_code=202)
def generate(key: str):
    with svc.LOCK:
        job = get_job(key)
        if 'resumen' not in job or job['estado'] in ('pendiente', 'en_proceso'):
            raise HTTPException(409, 'Espera a que termine el trabajo actual.')
        if job['archivos'] or not job['resumen']['modificadas']:
            return job
        svc.JOBS[key].update(estado='pendiente', fase='generacion')
        svc.POOL.submit(svc.run_job, key, svc.generate)
        return get_job(key)


@router.get('/{key}/archivos/{nombre}')
def download(key: str, nombre: str):
    job = get_job(key)
    allowed = {'reporte.csv'} if 'resumen' in job else set()
    if job['estado'] == 'completo' and job['archivos']:
        allowed.update(p['nombre'] for p in job['archivos'])
        allowed.add('stock.zip')
    if nombre not in allowed:
        raise HTTPException(404, 'Archivo no disponible.')
    path = svc.ROOT / key / nombre
    if not path.is_file():
        raise HTTPException(410, 'Archivo expirado. Vuelve a cargar los archivos.')
    return FileResponse(path, filename=nombre)
