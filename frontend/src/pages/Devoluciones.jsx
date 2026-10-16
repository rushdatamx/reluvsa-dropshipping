import React, { useCallback, useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import { actualizarDevolucion, descargarEvidenciaDevolucion, detalleDevolucion, listarDevoluciones, subirEvidenciaDevolucion } from '../services/api';

const fecha = (value) => value ? new Date(value).toLocaleString('es-MX', { dateStyle: 'medium', timeStyle: 'short' }) : 'Sin fecha';
const mostrar = (value) => value === null || value === undefined || value === '' ? 'Sin dato' : value;

export default function Devoluciones() {
  const [filtros, setFiltros] = useState({ cuenta: '', almacen: '', mediacion: '', revisado: '' });
  const [data, setData] = useState({ total: 0, sin_revisar: 0, sin_mediacion: 0, almacenes: [], items: [] });
  const [detalle, setDetalle] = useState(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

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

  return <div>
    <PageHeader title="Devoluciones" subtitle="Casos de Mercado Libre y seguimiento interno" />
    {error && <p role="alert" className="mb-4 rounded bg-red-50 text-red-700 p-3">{error}</p>}
    <div className="flex flex-wrap gap-4 mb-4">
      {filtro('cuenta', 'Cuenta', [['', 'Todas'], ['principal', 'Principal'], ['secundaria', 'Secundaria']])}
      {filtro('almacen', 'Almacén', [['', 'Todos'], ...almacenes.map((a) => [a, a])])}
      {filtro('mediacion', 'Mediación', [['', 'Todas'], ['true', 'Con mediación'], ['false', 'Sin mediación']])}
      {filtro('revisado', 'Revisión', [['', 'Todas'], ['true', 'Revisado'], ['false', 'Sin revisar']])}
    </div>
    <div className="flex gap-4 text-sm mb-4"><strong>{data.total} casos</strong><span>{data.sin_revisar} sin revisar</span><span>{data.sin_mediacion} sin mediación</span></div>
    <div className="overflow-x-auto bg-white rounded-lg border">
      <table className="min-w-full text-sm"><thead className="bg-gray-50 text-left"><tr>
        {['Cuenta', 'Venta', 'SKU', 'Almacén', 'Unidades reclamadas', 'Motivo', 'Fecha límite', 'Albarán', 'Fecha albarán', 'Estado ML', 'Mediación', 'Revisión'].map((h) => <th key={h} className="p-3 whitespace-nowrap">{h}</th>)}
      </tr></thead><tbody>{data.items.map((item) => <tr key={`${item.cuenta_ml}-${item.claim_id}`} className="border-t hover:bg-gray-50 cursor-pointer" onClick={() => abrir(item)}>
        <td className="p-3">{item.cuenta_ml === 'principal' ? 'Principal' : 'Secundaria'}</td>
        <td className="p-3 font-medium">{item.numero_venta || 'Pendiente de identificar'}{item.sin_cruce && item.numero_venta ? <span className="block text-xs text-amber-700">Sin cruce local</span> : null}</td>
        <td className="p-3">{mostrar(item.sku)}</td><td className="p-3">{mostrar(item.almacen)}</td>
        <td className="p-3">{mostrar(item.unidades_reclamadas)}</td><td className="p-3 max-w-xs truncate" title={item.motivo || ''}>{mostrar(item.motivo)}</td>
        <td className="p-3 whitespace-nowrap">{fecha(item.fecha_limite)}</td><td className="p-3">{mostrar(item.albaran)}</td>
        <td className="p-3">{item.fecha_albaran || 'Sin fecha'}</td><td className="p-3">{mostrar(item.estado_devolucion || item.estado_ml)}</td>
        <td className="p-3">{item.mediacion ? 'Con mediación' : 'Sin mediación'}</td><td className="p-3">{item.revisado ? 'Revisado' : 'Sin revisar'}</td>
      </tr>)}</tbody></table>
      {!data.items.length && <p className="p-6 text-gray-500">No hay devoluciones para estos filtros.</p>}
    </div>
    {detalle && <div className="fixed inset-0 z-50 bg-black/40 flex justify-end" onClick={() => setDetalle(null)}>
      <section className="bg-white w-full max-w-2xl h-full overflow-y-auto p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-start"><div><h2 className="text-xl font-bold">Devolución {detalle.claim_id}</h2>
          <p className="text-sm text-gray-500">{detalle.cuenta_ml} · Venta {detalle.numero_venta || 'pendiente de identificar'}</p></div>
          <button onClick={() => setDetalle(null)} className="text-gray-500 text-xl" aria-label="Cerrar">×</button></div>
        <dl className="grid grid-cols-2 gap-3 my-6 text-sm">
          <div><dt className="text-gray-500">Estado ML</dt><dd>{mostrar(detalle.estado_devolucion || detalle.estado_ml)}</dd></div>
          <div><dt className="text-gray-500">Acción pendiente</dt><dd>{mostrar(detalle.accion_pendiente)}</dd></div>
          <div><dt className="text-gray-500">Fecha límite</dt><dd>{fecha(detalle.fecha_limite)}</dd></div>
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
