import React, { useEffect, useState } from 'react';
import { AlertTriangle, Download, FileSpreadsheet, Loader2 } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { stockAnalizar, stockEstado, stockResultados, stockGenerar, stockDescargar } from '../services/api';

const providers = { reluvsa: ['VAZLO', 'CAUPLAS', 'KIMS', 'AG'], nereus: ['KG', 'DIFIRSA'] };
const card = 'bg-white rounded-xl border border-notion-border p-5 mb-4';
const button = 'px-4 py-2 rounded-lg text-sm font-semibold bg-reluvsa-black text-reluvsa-yellow hover:bg-gray-800 transition-colors disabled:opacity-50 flex items-center gap-2';
const field = 'border border-notion-border rounded-lg px-3 py-2 text-sm bg-white';
const errorText = err => err.response?.data?.detail || 'No se pudo completar la operación. Intenta de nuevo.';

export default function ActualizacionStock() {
  const [cuenta, setCuenta] = useState('reluvsa');
  const [exporte, setExporte] = useState(null);
  const [stocks, setStocks] = useState({});
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pagina, setPagina] = useState(1);
  const [tipo, setTipo] = useState('pendiente');
  const [proveedor, setProveedor] = useState('');
  const [detalle, setDetalle] = useState(null);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [downloading, setDownloading] = useState('');
  const running = job && ['pendiente', 'en_proceso'].includes(job.estado);

  useEffect(() => {
    if (!running) return undefined;
    let stopped = false;
    let timer;
    const poll = async () => {
      try {
        const { data } = await stockEstado(job.id);
        if (!stopped) {
          setJob(data);
          if (['pendiente', 'en_proceso'].includes(data.estado)) timer = setTimeout(poll, 1800);
        }
      } catch (err) {
        if (!stopped) { setError(errorText(err)); setJob(null); }
      }
    };
    timer = setTimeout(poll, 800);
    return () => { stopped = true; clearTimeout(timer); };
  }, [job?.id, running]);

  useEffect(() => {
    if (!job?.resumen) return undefined;
    let stopped = false;
    setLoadingDetails(true);
    stockResultados(job.id, { pagina, tipo, proveedor }).then(({ data }) => {
      if (!stopped) setDetalle(data);
    }).catch(err => { if (!stopped) setError(errorText(err)); })
      .finally(() => { if (!stopped) setLoadingDetails(false); });
    return () => { stopped = true; };
  }, [job?.id, job?.resumen, pagina, tipo, proveedor]);

  const clearResult = () => { setJob(null); setDetalle(null); setError(''); setPagina(1); setProveedor(''); };

  const analyze = async () => {
    setError('');
    const files = [exporte, ...Object.values(stocks)];
    if (files.some(f => f.size > 100000000) || files.reduce((sum, f) => sum + f.size, 0) > 250000000) {
      setError('Máximo 100 MB por archivo y 250 MB por carga.'); return;
    }
    setBusy(true); clearResult(); setTipo('pendiente');
    try { const { data } = await stockAnalizar(cuenta, exporte, stocks); setJob(data); }
    catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  };
  const generate = async () => {
    setBusy(true); setError('');
    try { const { data } = await stockGenerar(job.id); setJob(data); }
    catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  };
  const download = async nombre => {
    setDownloading(nombre); setError('');
    try {
      const { data } = await stockDescargar(job.id, nombre);
      const url = URL.createObjectURL(data);
      const a = document.createElement('a'); a.href = url; a.download = nombre; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      let message = 'No se pudo descargar el archivo.';
      try { message = JSON.parse(await err.response.data.text()).detail || message; } catch (_) {}
      setError(message);
    } finally { setDownloading(''); }
  };
  const locked = busy || running;
  const summary = job?.resumen;
  return <div>
    <PageHeader title="Actualización de stock" subtitle="Carga el stock diario, revisa los cambios y descarga los Excel para AutoAzur" />
    {(error || job?.error) && <div role="alert" className="mb-4 p-3 bg-red-50 text-danger rounded-lg text-sm flex gap-2"><AlertTriangle size={18} />{error || job.error}</div>}
    <section className={card}>
      <h2 className="font-semibold mb-4">1. Cuenta y archivos</h2>
      <fieldset disabled={locked} className="space-y-4 disabled:opacity-60">
        <label className="block text-sm">Cuenta
          <select className={`${field} block mt-1`} value={cuenta} onChange={e => { setCuenta(e.target.value); setStocks({}); setExporte(null); setJob(null); setDetalle(null); setProveedor(''); }}>
            <option value="reluvsa">RELUVSA</option><option value="nereus">Nereus</option>
          </select>
        </label>
        <div key={cuenta} className="grid md:grid-cols-2 gap-5">
          <label className="text-sm font-medium">Exporte de AutoAzur (.xlsx)<input type="file" accept=".xlsx" className="block mt-2 w-full text-sm" onChange={e => { clearResult(); setExporte(e.target.files[0] || null); }} /></label>
          {providers[cuenta].map(p => <label key={p} className="text-sm font-medium">Stock {p} (.xlsx o .csv)
            <input type="file" accept=".xlsx,.csv" className="block mt-2 w-full text-sm" onChange={e => { clearResult(); setStocks(prev => { const next = { ...prev }; if (e.target.files[0]) next[p] = e.target.files[0]; else delete next[p]; return next; }); }} />
          </label>)}
        </div>
      </fieldset>
      <p className="text-xs text-notion-text-secondary my-4">Carga uno o varios proveedores. Máximo 100 MB por archivo y 250 MB en total. Los originales se conservan. Las descargas caducan a las 24 horas; un reinicio puede exigir volver a cargar.</p>
      <button className={button} disabled={locked || !exporte || !Object.keys(stocks).length} onClick={analyze}>
        {busy ? <Loader2 size={16} className="animate-spin" /> : <FileSpreadsheet size={16} />}Analizar stock
      </button>
    </section>
    {running && <div role="status" className={`${card} flex gap-3 items-center`}><Loader2 className="animate-spin" size={20} />{job.fase === 'generacion' ? 'Generando y verificando archivos…' : 'Analizando archivos…'} Puedes revisar el resultado al terminar.</div>}
    {summary && <>
      <section className={card}>
        <h2 className="font-semibold mb-2">2. Resumen y revisión</h2>
        <p className="text-sm mb-4">{summary.filas.toLocaleString('es-MX')} filas · {summary.modificadas.toLocaleString('es-MX')} modificadas · {summary.pendientes.toLocaleString('es-MX')} pendientes</p>
        <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-3">
          {Object.entries(summary.proveedores).map(([p, values]) => <article key={p} className="bg-notion-bg rounded-lg p-4">
            <h3 className="font-semibold mb-2">{p}</h3>
            {Object.entries(values).map(([k, v]) => <p key={k} className="flex justify-between text-xs py-1"><span>{k.replaceAll('_', ' ')}</span><strong>{v.toLocaleString('es-MX')}</strong></p>)}
          </article>)}
        </div>
        <p className="text-xs text-notion-text-secondary mt-3">Las bodegas pendientes conservan su stock. Una equivalencia ambigua bloquea todos los cambios de esa fila. Cada variante recibe la disponibilidad calculada; los kits requieren stock directo.</p>
        <div className="flex flex-wrap gap-2 my-4">
          <select aria-label="Tipo de resultado" className={field} value={tipo} onChange={e => { setTipo(e.target.value); setPagina(1); }}>
            <option value="pendiente">Pendientes</option><option value="modificada">Modificadas</option><option value="sin_cambios">Sin cambios</option><option value="todos">Todos</option>
          </select>
          <select aria-label="Proveedor" className={field} value={proveedor} onChange={e => { setProveedor(e.target.value); setPagina(1); }}>
            <option value="">Todos los proveedores</option>{Object.keys(summary.proveedores).map(p => <option key={p}>{p}</option>)}
          </select>
        </div>
        {loadingDetails ? <p role="status" className="text-sm py-4">Cargando detalle…</p> : <div className="overflow-x-auto"><table className="w-full text-xs text-left">
          <thead><tr className="border-b border-notion-border">{['Fila / SKU', 'Proveedor', 'Motivo', 'Anterior', 'Propuesto'].map(h => <th key={h} className="p-2">{h}</th>)}</tr></thead>
          <tbody>{detalle?.resultados.map((r, i) => <tr key={i} className="border-b border-notion-border hover:bg-notion-bg">
            <td className="p-2">{r.fila || r.origen} · {r.sku || '(vacío)'}</td><td className="p-2">{r.proveedor}</td><td className="p-2">{r.motivo}</td>
            {[r.anterior, r.propuesto].map((v, j) => <td key={j} className="p-2">{v ? `${v.estado} · total ${v.total ?? 'vacío'} · bodega ${v.bodega ?? 'vacía'}` : j === 0 && r.stock_recibido !== undefined ? String(r.stock_recibido ?? 'vacío') : '—'}</td>)}
          </tr>)}</tbody>
        </table>{!detalle?.total && <p className="text-sm py-4">No hay resultados con este filtro.</p>}</div>}
        <div className="flex items-center gap-3 mt-4 text-xs">
          <button className={field} disabled={pagina === 1 || loadingDetails} onClick={() => setPagina(p => p - 1)}>Anterior</button>
          <span>Página {pagina} de {Math.max(1, Math.ceil((detalle?.total || 0) / 25))}</span>
          <button className={field} disabled={pagina * 25 >= (detalle?.total || 0) || loadingDetails} onClick={() => setPagina(p => p + 1)}>Siguiente</button>
        </div>
      </section>
      <section className={card}>
        <h2 className="font-semibold mb-2">3. Generar y descargar</h2>
        <p className="text-xs text-notion-text-secondary mb-4">Sólo se exportan filas modificadas. Sube los Excel a AutoAzur; el ZIP sirve para transportarlos. Antes del uso diario, valida con Gaby una importación pequeña con variante, paquete, activación y pausa.</p>
        {!summary.modificadas ? <p className="text-sm mb-3">No hay cambios. No se genera un Excel vacío.</p> : !job.archivos.length && <button className={button} disabled={locked} onClick={generate}><FileSpreadsheet size={16} />Generar Excel</button>}
        <div className="flex flex-wrap gap-2 mt-3">
          {[...job.archivos.map(f => f.nombre), ...(job.archivos.length ? ['stock.zip'] : []), 'reporte.csv'].map(nombre => <button key={nombre} className={`${field} flex items-center gap-2 hover:bg-notion-bg disabled:opacity-50`} disabled={!!downloading} onClick={() => download(nombre)}><Download size={14} />{nombre}{downloading === nombre ? '…' : ''}</button>)}
        </div>
        {job.archivos.map(f => <p key={f.nombre} className="text-xs text-notion-text-secondary mt-2">{f.nombre}: {f.filas.toLocaleString('es-MX')} filas · {f.bytes.toLocaleString('es-MX')} bytes</p>)}
      </section>
    </>}
  </div>;
}
