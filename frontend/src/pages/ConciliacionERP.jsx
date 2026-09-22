import React, { useEffect, useState } from 'react';
import { Download, FileSpreadsheet, Search, Upload } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { exportarConciliacionERPCsv, listarCargasERP, listarConciliacionERP, resumenConciliacionERP, subirCargaERP } from '../services/api';

const ESTADOS = ['Confirmada', 'Pendiente de proveedor', 'Factura sin entrada ERP', 'Para revisar'];
const estiloEstado = {
  Confirmada: 'bg-green-100 text-green-700',
  'Pendiente de proveedor': 'bg-amber-100 text-amber-700',
  'Factura sin entrada ERP': 'bg-red-100 text-red-700',
  'Para revisar': 'bg-purple-100 text-purple-700',
};

export default function ConciliacionERP() {
  const [cargas, setCargas] = useState([]);
  const [data, setData] = useState({ items: [], carga: null });
  const [resumen, setResumen] = useState(null);
  const [filtros, setFiltros] = useState({ carga_id: '', proveedor: '', estado: '', fecha_desde: '', fecha_hasta: '', q: '' });
  const [archivo, setArchivo] = useState(null);
  const [subiendo, setSubiendo] = useState(false);
  const [mensaje, setMensaje] = useState('');

  const params = () => Object.fromEntries(Object.entries(filtros).filter(([, v]) => v));
  const cargar = async () => {
    try {
      const p = params();
      const [listado, cards, historial] = await Promise.all([listarConciliacionERP(p), resumenConciliacionERP(p), listarCargasERP()]);
      setData(listado.data); setResumen(cards.data); setCargas(historial.data);
      if (!filtros.carga_id && listado.data.carga?.id) setFiltros((f) => ({ ...f, carga_id: String(listado.data.carga.id) }));
    } catch (err) { setMensaje(err.response?.data?.detail || 'No se pudo cargar la conciliación.'); }
  };
  useEffect(() => { cargar(); }, []); // La actualización dinámica ocurre en cada consulta/refresco.

  const subir = async () => {
    if (!archivo) return;
    setSubiendo(true); setMensaje('');
    try {
      const { data: respuesta } = await subirCargaERP(archivo);
      setMensaje(`Carga #${respuesta.carga_id} guardada: ${respuesta.total} filas.`);
      setFiltros((f) => ({ ...f, carga_id: String(respuesta.carga_id) }));
      setTimeout(cargar, 0);
    } catch (err) { setMensaje(err.response?.data?.detail || 'No se pudo procesar el Excel.'); }
    finally { setSubiendo(false); }
  };
  const exportar = async () => {
    const { data: blob } = await exportarConciliacionERPCsv(params());
    const url = window.URL.createObjectURL(blob); const a = document.createElement('a');
    a.href = url; a.download = 'conciliacion_erp.csv'; a.click(); window.URL.revokeObjectURL(url);
  };
  const set = (key, value) => setFiltros((f) => ({ ...f, [key]: value }));
  const cards = resumen ? [
    ['Confirmadas', resumen.confirmadas, 'text-green-700'], ['Pendientes', resumen.pendientes, 'text-amber-700'],
    ['Sin entrada ERP', resumen.sin_erp, 'text-red-700'], ['Para revisar', resumen.para_revision, 'text-purple-700'],
  ] : [];

  return <div>
    <PageHeader title="Conciliación ERP" subtitle="Cruce dinámico entre entradas ERP y XML de facturas" actions={<button onClick={exportar} className="flex items-center gap-1.5 text-sm px-3 py-2 border border-notion-border rounded-lg font-semibold hover:bg-notion-bg-subtle"><Download size={16}/> Exportar CSV</button>} />
    <div className="bg-white rounded-xl border border-notion-border p-4 mb-4 flex flex-wrap gap-3 items-end">
      <div className="flex-1 min-w-[230px]"><label className="block text-xs font-semibold text-notion-text-secondary mb-1">Nueva carga ERP</label><input type="file" accept=".xlsx" onChange={(e) => setArchivo(e.target.files?.[0] || null)} className="text-sm w-full" /></div>
      <button onClick={subir} disabled={!archivo || subiendo} className="px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg text-sm font-semibold disabled:opacity-50 flex gap-2 items-center"><Upload size={16}/>{subiendo ? 'Procesando…' : 'Guardar carga'}</button>
      {mensaje && <p className="text-sm text-notion-text-secondary">{mensaje}</p>}
    </div>
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">{cards.map(([label, value, color]) => <div key={label} className="bg-white border border-notion-border rounded-xl p-4"><p className="text-xs text-notion-text-secondary">{label}</p><p className={`text-2xl font-bold mt-1 ${color}`}>{value}</p></div>)}</div>
    <div className="bg-white rounded-xl border border-notion-border p-4 mb-4 space-y-3">
      <div className="flex flex-wrap gap-3 items-end">
        <div><label className="block text-xs font-semibold text-notion-text-secondary mb-1">Carga histórica</label><select value={filtros.carga_id} onChange={(e) => set('carga_id', e.target.value)} className="border border-notion-border rounded-lg p-2 text-sm"><option value="">Última carga</option>{cargas.map((c) => <option key={c.id} value={c.id}>#{c.id} · {c.nombre_archivo} · {String(c.fecha_subida).slice(0, 10)}</option>)}</select></div>
        <div><label className="block text-xs font-semibold text-notion-text-secondary mb-1">Proveedor</label><select value={filtros.proveedor} onChange={(e) => set('proveedor', e.target.value)} className="border border-notion-border rounded-lg p-2 text-sm"><option value="">Todos</option><option value="KIM">KIM</option><option value="CAUPLAS">CAUPLAS</option></select></div>
        <div><label className="block text-xs font-semibold text-notion-text-secondary mb-1">Estado</label><select value={filtros.estado} onChange={(e) => set('estado', e.target.value)} className="border border-notion-border rounded-lg p-2 text-sm"><option value="">Todos</option>{ESTADOS.map((e) => <option key={e}>{e}</option>)}</select></div>
        <div><label className="block text-xs font-semibold text-notion-text-secondary mb-1">Desde</label><input type="date" value={filtros.fecha_desde} onChange={(e) => set('fecha_desde', e.target.value)} className="border border-notion-border rounded-lg p-2 text-sm" /></div>
        <div><label className="block text-xs font-semibold text-notion-text-secondary mb-1">Hasta</label><input type="date" value={filtros.fecha_hasta} onChange={(e) => set('fecha_hasta', e.target.value)} className="border border-notion-border rounded-lg p-2 text-sm" /></div>
        <button onClick={cargar} className="px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg text-sm font-semibold">Aplicar filtros</button>
      </div>
      <div className="relative max-w-lg"><Search size={16} className="absolute top-1/2 -translate-y-1/2 left-3 text-notion-text-secondary"/><input value={filtros.q} onChange={(e) => set('q', e.target.value)} onKeyDown={(e) => e.key === 'Enter' && cargar()} placeholder="Buscar referencia, XML o proveedor" className="w-full border border-notion-border rounded-lg p-2 pl-9 text-sm"/></div>
    </div>
    {data.carga && <p className="text-xs text-notion-text-secondary mb-2">Carga #{data.carga.id}: rango de referencia {data.carga.fecha_referencia_desde} a {data.carga.fecha_referencia_hasta} · {data.total} resultado(s)</p>}
    <div className="bg-white rounded-xl border border-notion-border overflow-x-auto"><table className="w-full text-sm"><thead className="bg-notion-bg-subtle text-notion-text-secondary text-left"><tr><th className="p-3">Estado</th><th className="p-3">Proveedor</th><th className="p-3">Referencia ERP</th><th className="p-3">Fechas</th><th className="p-3">XML</th><th className="p-3">Alertas / revisión</th></tr></thead><tbody>{data.items.map((x) => <tr key={x.id} className="border-t border-notion-border"><td className="p-3"><span className={`px-2 py-1 rounded-full text-xs font-semibold ${estiloEstado[x.estado] || 'bg-notion-bg-subtle'}`}>{x.estado}</span></td><td className="p-3 font-medium">{x.proveedor_codigo || x.proveedor_original || '—'}</td><td className="p-3">{x.referencia_original || '—'}</td><td className="p-3 text-xs">ERP: {x.fecha_referencia || '—'}<br/>Captura: {x.fecha_captura || '—'}</td><td className="p-3 text-xs">{x.factura_serie || ''}{x.factura_folio || '—'}<br/>{x.fecha_factura?.slice(0, 10) || ''}</td><td className="p-3 text-xs">{x.duplicada_erp && <span className="text-amber-700 font-semibold block">Duplicada en ERP</span>}{x.motivo_revision || '—'}</td></tr>)}</tbody></table>{!data.items.length && <div className="p-10 text-center text-notion-text-secondary"><FileSpreadsheet className="mx-auto mb-2"/>No hay resultados para estos filtros.</div>}</div>
  </div>;
}
