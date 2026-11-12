import React, { useCallback, useEffect, useMemo, useState } from 'react';
import PageHeader from '../components/PageHeader';
import { actualizarDevolucion, descargarEvidenciaDevolucion, detalleDevolucion, listarDevoluciones, subirEvidenciaDevolucion } from '../services/api';

const fecha = (value) => value ? new Date(value).toLocaleString('es-MX', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/Monterrey' }) : 'Sin fecha';
const plazo = (item, ahora) => {
  const limite = Date.parse(item.fecha_limite);
  if (!Number.isFinite(limite)) return { tipo: 'sin_fecha', texto: 'Sin fecha informada por ML', color: 'text-gray-500' };
  if (item.estado_ml !== 'opened') return { tipo: 'cerrado', texto: 'Caso cerrado', color: 'text-gray-500' };
  const restante = limite - ahora;
  const minutos = Math.max(1, Math.ceil(Math.abs(restante) / 60000));
  const duracion = minutos >= 1440 ? `${Math.floor(minutos / 1440)} d ${Math.floor((minutos % 1440) / 60)} h` : `${Math.floor(minutos / 60)} h ${minutos % 60} min`;
  return { tipo: restante <= 0 ? 'vencido' : 'pendiente', restante,
    texto: restante <= 0 ? `Vencido hace ${duracion}` : `Vence en ${duracion}`,
    color: restante <= 86400000 ? 'text-red-700' : restante <= 172800000 ? 'text-amber-700' : restante <= 259200000 ? 'text-yellow-800' : 'text-gray-600' };
};

const mostrar = (value) => value === null || value === undefined || value === '' ? 'Sin dato' : value;

const iniciador = (item) => ({ reluvsa: 'RELUVSA', cliente: 'Cliente', mercado_libre: 'Mercado Libre', no_identificado: 'No identificado' }[item.mediacion_iniciada_por] || (item.mediacion_historial_consultado_en ? '—' : 'No identificado'));

export default function Devoluciones() {
  const [filtros, setFiltros] = useState({ cuenta: '', almacen: '', mediacion: '', revisado: '' });
  const [vencimiento, setVencimiento] = useState('');
  const [ahora, setAhora] = useState(Date.now);
  useEffect(() => {
    const actualizar = () => setAhora(Date.now());
    const timer = setInterval(actualizar, 60000);
    window.addEventListener('focus', actualizar);
    document.addEventListener('visibilitychange', actualizar);
    return () => { clearInterval(timer); window.removeEventListener('focus', actualizar); document.removeEventListener('visibilitychange', actualizar); };
  }, []);
  const [data, setData] = useState({ total: 0, sin_revisar: 0, sin_mediacion: 0, almacenes: [], items: [] });
  const [detalle, setDetalle] = useState(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [orden, setOrden] = useState({ campo: 'fecha_limite', direccion: 'asc' });

  const cargar = useCallback(async () => {
    try {
      const params = Object.fromEntries(Object.entries(filtros).filter(([, v]) => v !== ''));
      setData((await listarDevoluciones(params)).data);
      setError('');
    } catch (e) { setError(e.response?.data?.detail || 'No se pudieron cargar las devoluciones'); }
  }, [filtros]);
  useEffect(() => { cargar(); }, [cargar]);

  const abrir = async (item) => {
    try { setDetalle((await detalleDevolucion(item.cuenta_ml, item.claim_id)).data); setError(''); }
    catch (e) { setError(e.response?.data?.detail || 'No se pudo abrir el detalle'); }
  };
  const marcar = async (field, value) => {
    setSaving(true);
    try {
      setDetalle((await actualizarDevolucion(detalle.cuenta_ml, detalle.claim_id, { [field]: value })).data);
      await cargar();
    } catch (e) { setError(e.response?.data?.detail || 'No se pudo guardar'); }
    finally { setSaving(false); }
  };
  const subir = async (files) => {
    if (!files.length) return;
    setSaving(true);
    try {
      for (const file of files) await subirEvidenciaDevolucion(detalle.cuenta_ml, detalle.claim_id, file);
      await abrir(detalle);
    } catch (e) { setError(e.response?.data?.detail || 'No se pudo subir la evidencia'); }
    finally { setSaving(false); }
  };
  const descargar = async (archivo) => {
    try {
      const response = await descargarEvidenciaDevolucion(detalle.cuenta_ml, detalle.claim_id, archivo.id);
      const url = URL.createObjectURL(response.data);
      const link = document.createElement('a'); link.href = url; link.download = archivo.nombre_original; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setError('No se pudo descargar la evidencia'); }
  };
  const filtro = (key, label, values) => <label className="text-sm text-gray-600">{label}
    <select className="ml-2 border rounded px-2 py-1 bg-white" value={filtros[key]}
      onChange={(e) => setFiltros({ ...filtros, [key]: e.target.value })}>
      {values.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
    </select>
  </label>;
  const almacenes = data.almacenes || [];
  const itemsFiltrados = useMemo(() => data.items.filter((item) => {
    if (!vencimiento) return true;
    const estado = plazo(item, ahora);
    if (vencimiento === 'vencido' || vencimiento === 'sin_fecha') return estado.tipo === vencimiento;
    return estado.tipo === 'pendiente' && estado.restante <= Number(vencimiento) * 3600000;
  }), [data.items, vencimiento, ahora]);
  const resumenPlazo = (item) => { const estado = plazo(item, ahora); return <span className={`block text-xs mt-1 ${estado.color}`}>{estado.texto}</span>; };
  const itemsOrdenados = useMemo(() => {
    const camposFecha = new Set(['fecha_limite', 'fecha_albaran']);
    const camposNumero = new Set(['unidades_reclamadas']);
    const camposBooleanos = new Set(['mediacion', 'revisado']);
    const collator = new Intl.Collator('es-MX', { numeric: true, sensitivity: 'base' });
    const valor = (item, campo) => {
      if (campo === 'mediacion_iniciada_por') return iniciador(item);
      if (campo === 'numero_venta') return item.numero_venta;
      if (campo === 'estado') return item.estado_devolucion || item.estado_ml;
      return item[campo];
    };
    return [...itemsFiltrados].sort((a, b) => {
      const av = valor(a, orden.campo);
      const bv = valor(b, orden.campo);
      const aVacio = av === null || av === undefined || av === '';
      const bVacio = bv === null || bv === undefined || bv === '';
      if (aVacio || bVacio) return aVacio === bVacio ? 0 : (aVacio ? 1 : -1);
      let comparacion;
      if (camposFecha.has(orden.campo)) {
        comparacion = (Date.parse(av) || 0) - (Date.parse(bv) || 0);
      } else if (camposNumero.has(orden.campo)) {
        comparacion = Number(av) - Number(bv);
      } else if (camposBooleanos.has(orden.campo)) {
        comparacion = Number(Boolean(av)) - Number(Boolean(bv));
      } else {
        comparacion = collator.compare(String(av), String(bv));
      }
      return orden.direccion === 'asc' ? comparacion : -comparacion;
    });
  }, [itemsFiltrados, orden]);

  const ordenarPor = (campo) => setOrden((actual) => ({
    campo,
    direccion: actual.campo === campo
      ? (actual.direccion === 'asc' ? 'desc' : 'asc')
      : (campo === 'fecha_limite' || campo === 'fecha_albaran' || campo === 'unidades_reclamadas' ? 'desc' : 'asc'),
  }));
  const encabezado = (texto, campo) => <th key={campo} aria-sort={orden.campo === campo ? (orden.direccion === 'asc' ? 'ascending' : 'descending') : 'none'} className="p-3 whitespace-nowrap">
    <button type="button" onClick={() => ordenarPor(campo)} className="inline-flex items-center gap-1 font-semibold hover:text-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded">
      {texto}<span aria-hidden="true" className="text-xs text-gray-500">{orden.campo === campo ? (orden.direccion === 'asc' ? '↑' : '↓') : '↕'}</span>
    </button>
  </th>;

  return <div>
    <PageHeader title="Devoluciones" subtitle="Casos de Mercado Libre y seguimiento interno" />
    {error && <p role="alert" className="mb-4 rounded bg-red-50 text-red-700 p-3">{error}</p>}
    <div className="flex flex-wrap gap-4 mb-4">
      {filtro('cuenta', 'Cuenta', [['', 'Todas'], ['principal', 'Principal'], ['secundaria', 'Secundaria']])}
      {filtro('almacen', 'Almacén', [['', 'Todos'], ...almacenes.map((a) => [a, a])])}
      {filtro('mediacion', 'Mediación', [['', 'Todas'], ['true', 'Con mediación'], ['false', 'Sin mediación']])}
      {filtro('revisado', 'Revisado', [['', 'Todos'], ['true', 'Revisado'], ['false', 'No revisado']])}
      <label className="text-sm text-gray-600">Vencimiento
        <select className="ml-2 border rounded px-2 py-1 bg-white" value={vencimiento} onChange={(e) => setVencimiento(e.target.value)}>
          {[['', 'Todos'], ['24', 'Próximas 24 horas'], ['48', 'Próximas 48 horas'], ['72', 'Próximas 72 horas'], ['vencido', 'Vencidos'], ['sin_fecha', 'Sin fecha informada']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
    </div>
    <p className="text-xs text-gray-500 mb-3">Plazo para responder informado por ML · Horario de Monterrey · Datos de la última sincronización.</p>
    <div className="flex gap-4 text-sm mb-4"><strong>{itemsFiltrados.length} casos</strong><span>{itemsFiltrados.filter(i => !i.revisado).length} sin revisar</span><span>{itemsFiltrados.filter(i => !i.mediacion).length} sin mediación</span></div>
    <div className="overflow-x-auto bg-white rounded-lg border">
      <table className="min-w-full text-sm"><thead className="bg-gray-50 text-left"><tr>
        {encabezado('Cuenta', 'cuenta_ml')}{encabezado('Venta', 'numero_venta')}{encabezado('SKU', 'sku')}{encabezado('Almacén', 'almacen')}{encabezado('Unidades reclamadas', 'unidades_reclamadas')}{encabezado('Motivo', 'motivo')}{encabezado('Fecha límite', 'fecha_limite')}{encabezado('Albarán', 'albaran')}{encabezado('Fecha albarán', 'fecha_albaran')}{encabezado('Estado ML', 'estado')}{encabezado('Mediación', 'mediacion')}{encabezado('Mediación iniciada por', 'mediacion_iniciada_por')}{encabezado('Revisión', 'revisado')}
      </tr></thead><tbody>{itemsOrdenados.map((item) => <tr key={`${item.cuenta_ml}-${item.claim_id}`} className="border-t hover:bg-gray-50 cursor-pointer" onClick={() => abrir(item)}>
        <td className="p-3">{item.cuenta_ml === 'principal' ? 'Principal' : 'Secundaria'}</td>
        <td className="p-3 font-medium">{item.numero_venta || 'Pendiente de identificar'}{item.sin_cruce && item.numero_venta ? <span className="block text-xs text-amber-700">Sin cruce local</span> : null}</td>
        <td className="p-3">{mostrar(item.sku)}</td><td className="p-3">{mostrar(item.almacen)}</td>
        <td className="p-3">{mostrar(item.unidades_reclamadas)}</td><td className="p-3 max-w-xs truncate" title={item.motivo || ''}>{mostrar(item.motivo)}</td>
        <td className="p-3 whitespace-nowrap">{fecha(item.fecha_limite)}{resumenPlazo(item)}</td><td className="p-3">{mostrar(item.albaran)}</td>
        <td className="p-3">{item.fecha_albaran || 'Sin fecha'}</td><td className="p-3">{mostrar(item.estado_devolucion || item.estado_ml)}</td>
        <td className="p-3">{item.mediacion ? 'Con mediación' : 'Sin mediación'}</td><td className="p-3">{iniciador(item)}</td><td className="p-3">{item.revisado ? 'Revisado' : 'Sin revisar'}</td>
      </tr>)}</tbody></table>
      {!itemsFiltrados.length && <p className="p-6 text-gray-500">No hay devoluciones para estos filtros.</p>}
    </div>
    {detalle && <div className="fixed inset-0 z-50 bg-black/40 flex justify-end" onClick={() => setDetalle(null)}>
      <section className="bg-white w-full max-w-2xl h-full overflow-y-auto p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-start"><div><h2 className="text-xl font-bold">Devolución {detalle.claim_id}</h2>
          <p className="text-sm text-gray-500">{detalle.cuenta_ml} · Venta {detalle.numero_venta || 'pendiente de identificar'}</p></div>
          <button onClick={() => setDetalle(null)} className="text-gray-500 text-xl" aria-label="Cerrar">×</button></div>
        <dl className="grid grid-cols-2 gap-3 my-6 text-sm">
          <div><dt className="text-gray-500">Estado ML</dt><dd>{mostrar(detalle.estado_devolucion || detalle.estado_ml)}</dd></div>
          <div><dt className="text-gray-500">Mediación iniciada por</dt><dd>{iniciador(detalle)} · {fecha(detalle.mediacion_fecha_apertura)}</dd></div>
          <div><dt className="text-gray-500">Acción pendiente</dt><dd>{mostrar(detalle.accion_pendiente)}</dd></div>
          <div><dt className="text-gray-500">Fecha límite</dt><dd>{fecha(detalle.fecha_limite)}{resumenPlazo(detalle)}</dd></div>
          <div><dt className="text-gray-500">Unidades reclamadas</dt><dd>{mostrar(detalle.unidades_reclamadas)}</dd></div>
          <div><dt className="text-gray-500">Motivo</dt><dd>{mostrar(detalle.motivo)}</dd></div>
          <div><dt className="text-gray-500">Almacén</dt><dd>{mostrar(detalle.almacen)}</dd></div>
          <div><dt className="text-gray-500">Albarán</dt><dd>{mostrar(detalle.albaran)}</dd></div>
          <div><dt className="text-gray-500">Fecha de albarán</dt><dd>{detalle.fecha_albaran || 'Sin fecha'}</dd></div>
        </dl>
        {detalle.detalle && <p className="my-4 text-sm">{detalle.detalle}</p>}
        <h3 className="font-semibold mb-2">Productos</h3>
        <ul className="mb-6 text-sm space-y-1">{(detalle.productos.length ? detalle.productos : detalle.ventas).map((p, idx) =>
          <li key={idx} className="border rounded p-2">{mostrar(p.sku)} · {mostrar(p.titulo)}{p.unidades_vendidas ? ` · ${p.unidades_vendidas} vendidas` : ''}</li>)}</ul>
        <div className="flex gap-3 mb-2">
          <button disabled={saving} onClick={() => marcar('revisado', !detalle.revisado)} className="border rounded px-3 py-2 text-sm">{detalle.revisado ? 'Revisado ✓' : 'Sin revisar'}</button>
        </div>
        <p className="text-xs text-gray-500 mb-6">Mediación: {detalle.mediacion ? 'Con mediación (estado derivado de Mercado Libre)' : 'Sin mediación (estado derivado de Mercado Libre)'}. Último cambio interno: {detalle.seguimiento_usuario || 'Sin cambios'} · {fecha(detalle.seguimiento_fecha)}.</p>
        <h3 className="font-semibold mb-2">Evidencia</h3>
        <p className="text-xs text-gray-500 mb-2">JPG, PNG o PDF · máximo 10 MB cada uno · 10 archivos por caso</p>
        <input type="file" accept="image/jpeg,image/png,application/pdf" multiple disabled={saving} onChange={(e) => { subir([...e.target.files]); e.target.value = ''; }} />
        <ul className="mt-3 text-sm space-y-2">{(detalle.evidencias || []).map((a) => <li key={a.id}>
          <button className="text-blue-700 underline" onClick={() => descargar(a)}>{a.nombre_original}</button> <span className="text-gray-500">· {a.usuario} · {fecha(a.creado_en)}</span>
        </li>)}</ul>
      </section>
    </div>}
  </div>;
}
