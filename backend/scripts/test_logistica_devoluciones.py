"""Estados explícitos, privacidad y refresco independiente del reclamo (sin red)."""
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

_tmp = tempfile.TemporaryDirectory()
os.environ['DATABASE_PATH'] = str(Path(_tmp.name) / 'test.db')
sys.path.insert(0, str(Path(__file__).parent.parent))
from database import init_database, get_db, _migrar_columna_logistica_devoluciones
from services.logistica_devoluciones import normalizar_devolucion
from services import sync_devoluciones as sync, ml_client

init_database()
for source, target in [('pending', 'preparacion'), ('ready_to_ship', 'preparacion'),
                       ('shipped', 'en_camino'), ('delivered', 'entregado'),
                       ('not_delivered', 'incidencia'), ('cancelled', 'cancelado'),
                       ('unexpected', 'desconocido')]:
    raw = {'status': 'closed', 'shipments': [{'shipment_id': 123, 'type': 'return',
           'status': source, 'destination': {'name': 'warehouse', 'shipping_address': {'address_line': 'PRIVATE'}},
           'tracking_number': 'TRACK'}]}
    result = normalizar_devolucion(raw)
    assert result['estado_logistico'] == target, result
    assert result['envios'][0]['id'] == '123'
    assert result['envios'][0]['destino'] == 'warehouse'
    assert 'PRIVATE' not in str(result)
assert normalizar_devolucion({'shipping': {'id': 1, 'status': 'shipped'}, 'destination': 'seller_address'})['estado_logistico'] == 'en_camino'
for raw in [None, '', '{', [], {'shipments': [None]}, {'status': 'shipped'},
            {'shipments': [{'type': 'forward', 'status': 'shipped'}]}]:
    assert normalizar_devolucion(raw)['estado_logistico'] == 'desconocido'
assert normalizar_devolucion({'shipments': [{'type': 'return', 'status': 'delivered'},
    {'type': 'return_from_triage', 'status': 'shipped'}]})['estado_logistico'] == 'en_camino'

old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
fresh = datetime.now(timezone.utc).isoformat()
with get_db() as conn:
    _migrar_columna_logistica_devoluciones(conn.cursor())
    _migrar_columna_logistica_devoluciones(conn.cursor())
    conn.execute("INSERT INTO ml_config(clave,valor) VALUES ('seller_id','seller')")
    for cid, estado, last in [('1','shipped',old), ('2','ready_to_ship',old), ('3','not_delivered',old),
                             ('4','delivered',old), ('5','shipped',fresh), ('6','delivered',None)]:
        conn.execute('''INSERT INTO ml_devoluciones(cuenta_ml,claim_id,raw_return,logistica_consultada_en,revisado)
            VALUES ('principal',?,?,?,1)''', (cid, __import__('json').dumps({'shipping': {'status': estado}}), last))
assert sync.encolar_refresco_logistico('principal') == 4
assert sync.encolar_refresco_logistico('principal') == 0
old_get, old_optional = ml_client.get, ml_client.get_opcional

def claim(path, **kwargs):
    return {'id': 1, 'type': 'returns', 'status': 'closed', 'last_updated': 'unchanged'}

def optional(path, **kwargs):
    if path.endswith('/returns'):
        return {'id': 8, 'status': 'closed', 'shipments': [{'type': 'return', 'status': 'delivered'}]}
    return []

ml_client.get, ml_client.get_opcional = claim, optional
try:
    sync.procesar_reclamo('principal', '1')
    with get_db() as conn:
        row = conn.execute("SELECT * FROM ml_devoluciones WHERE claim_id='1'").fetchone()
        assert row['revisado'] == 1
        assert row['logistica_consultada_en'] > old
        assert normalizar_devolucion(row['raw_return'])['estado_logistico'] == 'entregado'
        preserved = row['raw_return'], row['logistica_consultada_en']
    ml_client.get_opcional = lambda *args, **kwargs: None
    try:
        sync.procesar_reclamo('principal', '1')
        assert False, 'Missing existing return must retry'
    except ml_client.MLError:
        pass
    with get_db() as conn:
        row = conn.execute("SELECT raw_return,logistica_consultada_en,revisado FROM ml_devoluciones WHERE claim_id='1'").fetchone()
        assert (row[0], row[1]) == preserved and row[2] == 1
finally:
    ml_client.get, ml_client.get_opcional = old_get, old_optional
print('Logística de devoluciones: normalización, privacidad, migración y refresco OK')

# Listado: mismo universo para filtros/contadores, sin corte histórico y sin
# confundir un reclamo cerrado con un retorno todavía en tránsito.
from fastapi.testclient import TestClient
from main import app
from routers.auth import require_admin
from models import UserInfo
app.dependency_overrides[require_admin] = lambda: UserInfo(user_id=1, email="test@test.mx", rol="admin", proveedor_id=None)
try:
    with get_db() as conn:
        conn.execute("UPDATE ml_devoluciones SET creado_ml='2026-10-01T00:00:00Z', estado_ml='closed'")
    client = TestClient(app)
    response = client.get('/api/devoluciones')
    assert response.status_code == 200, response.text
    data = response.json()
    assert {i['claim_id'] for i in data['items']} == {'5'}
    assert data['total'] == len(data['items']) and data['sin_revisar'] == 0
    assert data['items'][0]['envios'][0]['estado'] == 'shipped'
    assert data['items'][0]['logistica_consultada_en']
    assert client.get('/api/devoluciones', params={'estado_logistico': 'todos'}).json()['total'] == 6
    assert client.get('/api/devoluciones', params={'fecha_desde': '2026-11-12'}).json()['total'] == 0
    assert client.get('/api/devoluciones', params={'fecha_hasta': '2026-10-01'}).json()['total'] == 1
    assert client.get('/api/devoluciones', params={'estado_logistico': 'bogus'}).status_code == 422
    assert client.get('/api/devoluciones', params={'fecha_desde': '2026-11-12', 'fecha_hasta': '2026-10-01'}).status_code == 422
    detail = client.get('/api/devoluciones/principal/5').json()
    assert detail['estado_logistico'] == 'en_camino' and detail['estado_ml'] == 'closed'
    assert 'raw_return' not in detail
finally:
    app.dependency_overrides.clear()
print('API logística: filtros, contadores, fechas anteriores y reclamos cerrados OK')
