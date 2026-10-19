import React, { useEffect, useState } from 'react';
import {
  Upload, FileText, FileCode, Download, Search, ChevronDown, ChevronRight,
  CheckCircle, AlertTriangle,
} from 'lucide-react';
import PageHeader from '../components/PageHeader';
import {
  listarFacturas, subirFacturasMultiple, getFactura, exportarFacturasCsv,
  descargarArchivoFactura, listarProveedores, getConciliacionDifirsa,
  guardarReferenciasDifirsa, proponerCrucesDifirsa, confirmarCrucesDifirsa,
} from '../services/api';
import { useAuth } from '../context/AuthContext';

const FILTROS_VACIOS = {
  q: '',
  proveedor_id: '',     // solo admin
  fecha_desde: '',
  fecha_hasta: '',
  sin_cruzar: false,    // solo facturas con conceptos sin cruzar a venta
};

const MESES_CORTOS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

function fechaCorta(iso) {
  if (!iso) return '—';
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  const [, anio, mes, dia] = m;
  return `${parseInt(dia, 10)} ${MESES_CORTOS[parseInt(mes, 10) - 1]} ${anio}`;
}

// Abre/descarga un archivo protegido por JWT: lo baja como blob y lo abre en otra pestaña.
async function abrirArchivo(id, tipo) {
  try {
    const { data: blob } = await descargarArchivoFactura(id, tipo);
    const url = window.URL.createObjectURL(blob);
    window.open(url, '_blank');
    // No revocamos de inmediato: la pestaña nueva aún lee el blob. Lo soltamos tarde.
    setTimeout(() => window.URL.revokeObjectURL(url), 60000);
  } catch (e) {
    alert('No se pudo abrir el archivo: ' + (e.response?.data?.detail || e.message));
  }
}

export default function Facturas() {
  const { isProveedor, isAdmin } = useAuth();
  const [facturas, setFacturas] = useState([]);
  const [proveedores, setProveedores] = useState([]);
  const [filtros, setFiltros] = useState(FILTROS_VACIOS);
  const [loading, setLoading] = useState(false);
  const [exportando, setExportando] = useState(false);
  const [expandida, setExpandida] = useState(null);   // factura_id expandida
  const [detalle, setDetalle] = useState({});          // { [id]: conceptos[] }
  const [conciliaciones, setConciliaciones] = useState({});
  const [referenciasTexto, setReferenciasTexto] = useState({});
  const [asignacionesDraft, setAsignacionesDraft] = useState({});
  const [guardandoCruce, setGuardandoCruce] = useState(null);
  const [mensajeCruce, setMensajeCruce] = useState({});

  // Subida (rol proveedor) — múltiples XML + PDF
  const [xmlFiles, setXmlFiles] = useState([]);
  const [pdfFiles, setPdfFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState(null);
  const [resultado, setResultado] = useState(null); // resumen detallado del último lote

  const paramsDeFiltros = () => {
    const p = {};
    Object.entries(filtros).forEach(([k, v]) => {
      if (k === 'sin_cruzar') { if (v) p[k] = true; }
      else if (v) p[k] = v;
    });
    return p;
  };

  const cargar = async () => {
    setLoading(true);
    try {
      const { data } = await listarFacturas(paramsDeFiltros());
      setFacturas(data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { cargar(); }, []);
  useEffect(() => { if (isAdmin()) listarProveedores().then((r) => setProveedores(r.data)); }, []);

  const set = (k, v) => setFiltros((f) => ({ ...f, [k]: v }));

  const limpiar = () => { setFiltros(FILTROS_VACIOS); setTimeout(cargar, 0); };

  const toggleExpandir = async (f) => {
    if (expandida === f.id) { setExpandida(null); return; }
    setExpandida(f.id);
    if (!detalle[f.id]) {
      const { data } = await getFactura(f.id);
      setDetalle((d) => ({ ...d, [f.id]: data }));
      if (f.codigo_bodega === 'DIFIRSA') {
        const { data: c } = await getConciliacionDifirsa(f.id);
        setConciliaciones((prev) => ({ ...prev, [f.id]: c }));
        setReferenciasTexto((prev) => ({
          ...prev,
          [f.id]: c.referencias.map((r) => r.valor_original).join('\n'),
        }));
      }
    }
  };

  const actualizarConciliacion = (id, data) => {
    setConciliaciones((prev) => ({ ...prev, [id]: data }));
    setAsignacionesDraft((prev) => ({
      ...prev,
      [id]: data.propuestas || data.asignaciones || [],
    }));
  };

  const guardarReferencias = async (id) => {
    setGuardandoCruce(id); setMensajeCruce((m) => ({ ...m, [id]: null }));
    try {
      const refs = (referenciasTexto[id] || '').split(/[\s,;/]+/).filter(Boolean);
      const { data } = await guardarReferenciasDifirsa(id, refs);
      setConciliaciones((prev) => ({ ...prev, [id]: data }));
      setAsignacionesDraft((prev) => ({ ...prev, [id]: [] }));
      setMensajeCruce((m) => ({ ...m, [id]: 'Referencias guardadas. Genera una propuesta o asigna las ventas.' }));
    } catch (e) {
      setMensajeCruce((m) => ({ ...m, [id]: e.response?.data?.detail || 'No se pudieron guardar las referencias.' }));
    } finally { setGuardandoCruce(null); }
  };

  const generarPropuesta = async (id) => {
    setGuardandoCruce(id); setMensajeCruce((m) => ({ ...m, [id]: null }));
    try {
      const { data } = await proponerCrucesDifirsa(id);
      actualizarConciliacion(id, data);
      setMensajeCruce((m) => ({ ...m, [id]: `${data.propuestas.length} asignaciones sugeridas. Revísalas antes de confirmar.` }));
    } catch (e) {
      setMensajeCruce((m) => ({ ...m, [id]: e.response?.data?.detail || 'No se pudo generar la propuesta.' }));
    } finally { setGuardandoCruce(null); }
  };

  const confirmarPropuesta = async (id) => {
    setGuardandoCruce(id); setMensajeCruce((m) => ({ ...m, [id]: null }));
    try {
      const conc = conciliaciones[id];
      const conceptos = conc.conceptos || [];
      const rows = asignacionesDraft[id] || [];
      const payload = {
        asignaciones: rows.map((a) => ({
          concepto_id: a.concepto_id, num_venta: a.num_venta, cantidad: Number(a.cantidad),
        })),
        equivalencias: rows.flatMap((a) => {
          const c = conceptos.find((x) => x.id === a.concepto_id);
          const sale = c?.ventas_referenciadas.find((x) => x.num_venta === a.num_venta);
          if (!c || !sale || (c.codigo_prov || '').trim().toUpperCase() === (sale.sku || '').trim().toUpperCase()) return [];
          return [{ codigo_prov: c.codigo_prov, sku_ml: sale.sku }];
        }),
      };
      const { data } = await confirmarCrucesDifirsa(id, payload);
      setDetalle((prev) => ({ ...prev, [id]: data }));
      const { data: updated } = await getConciliacionDifirsa(id);
      setConciliaciones((prev) => ({ ...prev, [id]: updated }));
      setAsignacionesDraft((prev) => ({ ...prev, [id]: [] }));
      setMensajeCruce((m) => ({ ...m, [id]: 'Cruce confirmado y guardado.' }));
      cargar();
    } catch (e) {
      setMensajeCruce((m) => ({ ...m, [id]: e.response?.data?.detail || 'No se pudo confirmar el cruce.' }));
    } finally { setGuardandoCruce(null); }
  };

  const editarAsignacion = (id, conceptoId, index, campo, valor) => {
    setAsignacionesDraft((prev) => {
      const rows = [...(prev[id] || [])];
      const delConcepto = rows.filter((r) => r.concepto_id === conceptoId);
      const otro = rows.filter((r) => r.concepto_id !== conceptoId);
      delConcepto[index] = { ...delConcepto[index], [campo]: campo === 'cantidad' ? Number(valor) : valor };
      return { ...prev, [id]: [...otro, ...delConcepto] };
    });
  };

  const agregarAsignacion = (id, conceptoId) => {
    const conc = conciliaciones[id]?.conceptos.find((c) => c.id === conceptoId);
    const selected = (asignacionesDraft[id] || []).filter((a) => a.concepto_id === conceptoId);
    const available = conc?.ventas_referenciadas.find((v) => !selected.some((a) => a.num_venta === v.num_venta));
    if (!available) return;
    setAsignacionesDraft((prev) => ({
      ...prev,
      [id]: [...(prev[id] || []), { concepto_id: conceptoId, num_venta: available.num_venta, cantidad: 0 }],
    }));
  };

  const quitarAsignacion = (id, conceptoId, index) => {
    setAsignacionesDraft((prev) => {
      const rows = (prev[id] || []).filter((a) => a.concepto_id !== conceptoId);
      const selected = (prev[id] || []).filter((a) => a.concepto_id === conceptoId).filter((_, i) => i !== index);
      return { ...prev, [id]: [...rows, ...selected] };
    });
  };

  const onExportar = async () => {
    setExportando(true);
    try {
      const { data: blob } = await exportarFacturasCsv(paramsDeFiltros());
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'facturas.csv';
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      alert('No se pudo exportar: ' + (e.response?.data?.detail || e.message));
    } finally {
      setExportando(false);
    }
  };

  const handleSubir = async (e) => {
    e.preventDefault();
    if (!xmlFiles.length) return;
    setUploading(true);
    setMsg(null);
    setResultado(null);
    try {
      const { data } = await subirFacturasMultiple(xmlFiles, pdfFiles);
      const r = data.resumen;
      const ok = r.facturas_registradas > 0;
      setMsg({
        ok,
        text: ok
          ? `${r.facturas_registradas} factura(s) registrada(s)${r.errores ? `, ${r.errores} con error` : ''}.`
          : 'No se registró ninguna factura. Revisa el detalle.',
      });
      setResultado(data);
      setXmlFiles([]); setPdfFiles([]);
      e.target.reset();
      cargar();
    } catch (err) {
      setMsg({ ok: false, text: err.response?.data?.detail || 'Error al subir facturas' });
    } finally {
      setUploading(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Facturas"
      subtitle={isProveedor()
          ? 'Sube tus facturas (XML + PDF) para conciliarlas con tus pedidos'
          : 'Facturas subidas por los proveedores, con su PDF/XML y las ventas a las que cruzan'}
      />

      {isProveedor() && (
        <div className="bg-white rounded-xl border border-notion-border p-5 mb-6">
          <h3 className="font-semibold mb-1 flex items-center gap-2"><Upload size={18} /> Subir facturas</h3>
          <p className="text-xs text-notion-text-secondary mb-4">
            Puedes seleccionar <strong>varios XML y varios PDF</strong> a la vez. El sistema empareja
            cada PDF con su XML automáticamente (por el folio fiscal). El XML es obligatorio; el PDF es opcional.
          </p>
          <form onSubmit={handleSubir} className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
            <div>
              <label className="block text-xs font-semibold text-notion-text-secondary mb-1">
                XML (obligatorio){xmlFiles.length > 0 ? ` — ${xmlFiles.length}` : ''}
              </label>
              <input type="file" accept=".xml" multiple
                onChange={(e) => setXmlFiles(Array.from(e.target.files || []))}
                className="w-full text-sm" required />
            </div>
            <div>
              <label className="block text-xs font-semibold text-notion-text-secondary mb-1">
                PDF (opcional){pdfFiles.length > 0 ? ` — ${pdfFiles.length}` : ''}
              </label>
              <input type="file" accept=".pdf" multiple
                onChange={(e) => setPdfFiles(Array.from(e.target.files || []))}
                className="w-full text-sm" />
            </div>
            <button type="submit" disabled={!xmlFiles.length || uploading}
              className="px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50">
              {uploading ? 'Subiendo...' : `Subir ${xmlFiles.length || ''} factura(s)`}
            </button>
          </form>
          {msg && (
            <div className={`mt-3 p-3 rounded-lg text-sm ${msg.ok ? 'bg-green-50 text-success' : 'bg-red-50 text-danger'}`}>
              {msg.text}
            </div>
          )}
          {resultado && (
            <div className="mt-3 text-xs space-y-2">
              {resultado.registradas?.length > 0 && (
                <div>
                  <span className="font-semibold text-success">Registradas:</span>{' '}
                  {resultado.registradas.map((r, i) => (
                    <span key={i} className="inline-block mr-2">
                      {r.archivo} ({r.conceptos} conceptos{r.con_pdf ? ', con PDF' : ', sin PDF'})
                    </span>
                  ))}
                </div>
              )}
              {resultado.errores?.length > 0 && (
                <div>
                  <span className="font-semibold text-danger">Con error:</span>{' '}
                  {resultado.errores.map((er, i) => (
                    <span key={i} className="inline-block mr-2">{er.archivo}: {er.detail}</span>
                  ))}
                </div>
              )}
              {resultado.pdfs_sin_emparejar?.length > 0 && (
                <div className="text-warning">
                  <span className="font-semibold">PDF sin emparejar (ignorados):</span>{' '}
                  {resultado.pdfs_sin_emparejar.join(', ')} — no coincidieron con ningún XML del lote.
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Filtros */}
      <div className="bg-white rounded-xl border border-notion-border p-4 mb-4">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3 items-end">
          <div className="md:col-span-2">
            <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Buscar (folio / UUID)</label>
            <div className="relative">
              <Search size={15} className="absolute left-2.5 top-2.5 text-notion-text-secondary" />
              <input value={filtros.q} onChange={(e) => set('q', e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && cargar()}
                placeholder="K26804, UUID…"
                className="w-full pl-8 pr-3 py-2 text-sm border border-notion-border rounded-lg" />
            </div>
          </div>
          {isAdmin() && (
            <div>
              <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Proveedor</label>
              <select value={filtros.proveedor_id} onChange={(e) => set('proveedor_id', e.target.value)}
                className="w-full px-3 py-2 text-sm border border-notion-border rounded-lg">
                <option value="">Todos</option>
                {proveedores.map((p) => <option key={p.id} value={p.id}>{p.nombre}</option>)}
              </select>
            </div>
          )}
          <div>
            <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Desde</label>
            <input type="date" value={filtros.fecha_desde} onChange={(e) => set('fecha_desde', e.target.value)}
              className="w-full px-3 py-2 text-sm border border-notion-border rounded-lg" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Hasta</label>
            <input type="date" value={filtros.fecha_hasta} onChange={(e) => set('fecha_hasta', e.target.value)}
              className="w-full px-3 py-2 text-sm border border-notion-border rounded-lg" />
          </div>
        </div>
        <div className="flex items-center justify-between mt-3">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={filtros.sin_cruzar}
              onChange={(e) => set('sin_cruzar', e.target.checked)} />
            Solo con conceptos sin cruzar a venta
          </label>
          <div className="flex gap-2">
            <button onClick={() => cargar()}
              className="px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg text-sm font-semibold hover:bg-gray-800">
              Aplicar
            </button>
            <button onClick={limpiar}
              className="px-4 py-2 border border-notion-border rounded-lg text-sm hover:bg-notion-bg-subtle">
              Limpiar
            </button>
            <button onClick={onExportar} disabled={exportando}
              className="px-4 py-2 border border-notion-border rounded-lg text-sm hover:bg-notion-bg-subtle flex items-center gap-1.5 disabled:opacity-50">
              <Download size={15} /> {exportando ? 'Exportando…' : 'Exportar'}
            </button>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-notion-border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-notion-bg-subtle border-b border-notion-border">
            <tr>
              <th className="w-8 px-2 py-3"></th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Factura #</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Proveedor</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Fecha</th>
              <th className="text-right px-4 py-3 font-semibold text-notion-text-secondary">Total</th>
              <th className="text-center px-4 py-3 font-semibold text-notion-text-secondary">Archivos</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Cruces</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan="7" className="p-8 text-center text-notion-text-secondary">Cargando…</td></tr>
            ) : facturas.length === 0 ? (
              <tr><td colSpan="7" className="p-8 text-center text-notion-text-secondary">Sin facturas registradas</td></tr>
            ) : facturas.map((f) => {
              const abierta = expandida === f.id;
              const todoCruzado = f.total_conceptos > 0 && f.conceptos_matched === f.total_conceptos;
              return (
                <React.Fragment key={f.id}>
                  <tr className="border-t border-notion-border hover:bg-notion-bg-subtle cursor-pointer"
                    onClick={() => toggleExpandir(f)}>
                    <td className="px-2 py-3 text-notion-text-secondary">
                      {abierta ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">{f.folio_proveedor || `${f.serie || ''}${f.folio || f.id}`}</td>
                    <td className="px-4 py-3">{f.proveedor_nombre}</td>
                    <td className="px-4 py-3 text-xs whitespace-nowrap">{fechaCorta(f.fecha_factura)}</td>
                    <td className="px-4 py-3 text-right font-medium whitespace-nowrap">
                      {f.total != null ? `$${f.total.toFixed(2)}` : ''} {f.moneda}
                    </td>
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-center gap-2">
                        {f.tiene_pdf ? (
                          <button onClick={() => abrirArchivo(f.id, 'pdf')} title="Abrir PDF"
                            className="flex items-center gap-1 text-xs px-2 py-1 rounded border border-notion-border hover:bg-notion-bg-subtle">
                            <FileText size={14} /> PDF
                          </button>
                        ) : (
                          <span title="Sin PDF — pídelo al proveedor"
                            className="flex items-center gap-1 text-xs px-2 py-1 rounded border border-red-200 text-danger bg-red-50">
                            <AlertTriangle size={13} /> PDF
                          </span>
                        )}
                        {f.tiene_xml ? (
                          <button onClick={() => abrirArchivo(f.id, 'xml')} title="Abrir XML"
                            className="flex items-center gap-1 text-xs px-2 py-1 rounded border border-notion-border hover:bg-notion-bg-subtle">
                            <FileCode size={14} /> XML
                          </button>
                        ) : (
                          <span title="Sin XML" className="flex items-center gap-1 text-xs px-2 py-1 rounded border border-red-200 text-danger bg-red-50">
                            <AlertTriangle size={13} /> XML
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs">
                      <span className={`inline-flex items-center gap-1 ${todoCruzado ? 'text-success' : 'text-warning'}`}>
                        {todoCruzado ? <CheckCircle size={13} /> : <AlertTriangle size={13} />}
                        {f.conceptos_matched}/{f.total_conceptos} conceptos
                      </span>
                    </td>
                  </tr>
                  {abierta && (
                    <tr className="bg-notion-bg-subtle">
                      <td></td>
                      <td colSpan="6" className="px-4 py-3">
                        {f.codigo_bodega === 'DIFIRSA' && conciliaciones[f.id] && (
                          <section className="mb-5 rounded-xl border border-amber-200 bg-white p-4">
                            <div className="flex flex-wrap items-start justify-between gap-3">
                              <div>
                                <h4 className="font-semibold">Revisión DIFIRSA</h4>
                                <p className="mt-1 text-xs text-notion-text-secondary">
                                  Revisa las referencias leídas del PDF y distribuye todas las unidades. Los cambios se aplican sólo al confirmar.
                                </p>
                              </div>
                              <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800">
                                OCR: {conciliaciones[f.id].ocr?.estado || 'sin lectura'}
                              </span>
                            </div>
                            {isAdmin() && (
                              <>
                                <label className="mt-4 block text-xs font-semibold text-notion-text-secondary">
                                  Referencias de venta (una por línea; se acepta el sufijo o el número completo)
                                </label>
                                <textarea value={referenciasTexto[f.id] || ''}
                                  onChange={(e) => setReferenciasTexto((prev) => ({ ...prev, [f.id]: e.target.value }))}
                                  rows={3} className="mt-1 w-full rounded-lg border border-notion-border p-2 font-mono text-xs" />
                                <div className="mt-2 flex flex-wrap gap-2">
                                  <button onClick={() => guardarReferencias(f.id)} disabled={guardandoCruce === f.id}
                                    className="rounded-lg border border-notion-border px-3 py-1.5 text-xs font-semibold hover:bg-notion-bg-subtle disabled:opacity-50">
                                    Guardar referencias
                                  </button>
                                  <button onClick={() => generarPropuesta(f.id)} disabled={guardandoCruce === f.id}
                                    className="rounded-lg bg-reluvsa-black px-3 py-1.5 text-xs font-semibold text-reluvsa-yellow disabled:opacity-50">
                                    Generar sugerencia
                                  </button>
                                </div>
                                <div className="mt-4 space-y-3">
                                  {conciliaciones[f.id].conceptos.map((c) => {
                                    const rows = (asignacionesDraft[f.id] || []).filter((a) => a.concepto_id === c.id);
                                    const asignado = rows.reduce((s, a) => s + (Number(a.cantidad) || 0), 0);
                                    const restante = Number(c.cantidad || 0) - asignado;
                                    return (
                                      <div key={c.id} className="rounded-lg border border-notion-border p-3">
                                        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                                          <div><span className="font-mono font-semibold">{c.codigo_prov}</span><span className="ml-2 text-notion-text-secondary">{c.descripcion}</span></div>
                                          <span className={Math.abs(restante) < 0.000001 ? 'font-semibold text-success' : 'font-semibold text-warning'}>
                                            {asignado} / {c.cantidad} unidades
                                          </span>
                                        </div>
                                        {rows.map((a, index) => (
                                          <div key={`${c.id}-${index}`} className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-[1fr_100px_auto]">
                                            <select value={a.num_venta || ''} onChange={(e) => editarAsignacion(f.id, c.id, index, 'num_venta', e.target.value)}
                                              className="min-w-0 rounded-lg border border-notion-border px-2 py-1.5 text-xs">
                                              <option value="">Selecciona venta de las referencias</option>
                                              {c.ventas_referenciadas.map((v) => (
                                                <option key={v.num_venta} value={v.num_venta}>
                                                  {(v.num_venta_origen || v.num_venta)} · {v.sku || 'sin SKU'} · {v.unidades ?? '?'} u. · {v.titulo || ''}
                                                </option>
                                              ))}
                                            </select>
                                            <input type="number" min="0" step="1" value={a.cantidad ?? 0}
                                              onChange={(e) => editarAsignacion(f.id, c.id, index, 'cantidad', e.target.value)}
                                              className="rounded-lg border border-notion-border px-2 py-1.5 text-xs" aria-label="Unidades asignadas" />
                                            <button onClick={() => quitarAsignacion(f.id, c.id, index)}
                                              className="rounded-lg border border-notion-border px-2 py-1.5 text-xs hover:bg-red-50 hover:text-danger">Quitar</button>
                                          </div>
                                        ))}
                                        <button onClick={() => agregarAsignacion(f.id, c.id)}
                                          className="mt-2 text-xs font-semibold text-blue-700 hover:underline">+ Repartir en otra venta</button>
                                        {c.candidatas.length === 0 && (
                                          <p className="mt-2 text-xs text-warning">Sin SKU igual o equivalencia conocida. Si eliges una venta, se guardará la equivalencia para futuras facturas.</p>
                                        )}
                                      </div>
                                    );
                                  })}
                                </div>
                                {mensajeCruce[f.id] && <p className="mt-3 text-xs text-notion-text-secondary">{mensajeCruce[f.id]}</p>}
                                <div className="mt-3 flex justify-end">
                                  <button onClick={() => confirmarPropuesta(f.id)} disabled={guardandoCruce === f.id}
                                    className="rounded-lg bg-reluvsa-black px-4 py-2 text-xs font-semibold text-reluvsa-yellow disabled:opacity-50">
                                    {guardandoCruce === f.id ? 'Guardando…' : 'Confirmar cruces revisados'}
                                  </button>
                                </div>
                              </>
                            )}
                          </section>
                        )}
                        <div className="text-xs font-semibold text-notion-text-secondary mb-2">
                          Conceptos de la factura y la venta a la que cruzan
                        </div>
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="text-notion-text-secondary">
                              <th className="text-left py-1 pr-3 font-medium">Código</th>
                              <th className="text-left py-1 pr-3 font-medium"># declarado CAUPLAS</th>
                              <th className="text-left py-1 pr-3 font-medium">Descripción</th>
                              <th className="text-right py-1 pr-3 font-medium">Importe</th>
                              <th className="text-left py-1 pr-3 font-medium"># Venta</th>
                              <th className="text-left py-1 pr-3 font-medium">Título de la venta</th>
                              <th className="text-left py-1 font-medium">Cruce</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(detalle[f.id]?.conceptos || []).map((c) => (
                              <tr key={c.id} className="border-t border-notion-border">
                                <td className="py-1.5 pr-3 font-mono">{c.codigo_prov || '—'}</td>
                                <td className="py-1.5 pr-3 font-mono">
                                  {c.num_venta_proveedor || '—'}
                                  {c.cruce_numero_estado === 'numero_invalido' && (
                                    <span className="ml-2 inline-flex rounded bg-red-100 px-2 py-0.5 font-sans font-semibold text-danger">
                                      Número inválido
                                    </span>
                                  )}
                                </td>
                                <td className="py-1.5 pr-3 max-w-[260px] truncate" title={c.descripcion}>{c.descripcion || '—'}</td>
                                <td className="py-1.5 pr-3 text-right">{c.importe != null ? `$${Number(c.importe).toFixed(2)}` : ''}</td>
                                <td className="py-1.5 pr-3 font-mono">
                                  {c.asignaciones?.length
                                    ? c.asignaciones.map((a) => <div key={a.num_venta}>{a.num_venta_origen || a.num_venta} × {a.cantidad}</div>)
                                    : c.num_venta_match || <span className="text-danger">sin cruce</span>}
                                </td>
                                <td className="py-1.5 pr-3 max-w-[260px] truncate" title={c.asignaciones?.map((a) => a.titulo).filter(Boolean).join(' · ') || c.venta_titulo}>
                                  {c.asignaciones?.length
                                    ? c.asignaciones.map((a) => <div key={a.num_venta}>{a.titulo || a.sku || '—'}</div>)
                                    : c.venta_titulo || '—'}
                                </td>
                                <td className="py-1.5">
                                  {c.conflicto_factura ? (
                                    <div>
                                      <span className="inline-flex rounded bg-red-100 px-2 py-0.5 font-semibold text-danger">
                                        Conflicto de factura
                                      </span>
                                      <div className="mt-1 max-w-[260px] text-notion-text-secondary">
                                        {{
                                          desplazada_por_numero_explicito: 'Otra factura declaró explícitamente esta venta; este concepto quedó pendiente.',
                                          numero_explicito_duplicado: 'Otra factura ya declaró explícitamente la misma venta; se conservó la primera.',
                                          venta_ocupada_por_otra_factura: 'La venta ya pertenece a otra factura; este concepto quedó pendiente.',
                                        }[c.conflicto_factura] || c.conflicto_factura}
                                      </div>
                                    </div>
                                  ) : c.num_venta_match ? (
                                    <span className="text-notion-text-secondary">
                                      {c.match_method} · {c.match_confidence != null ? `${Math.round(c.match_confidence * 100)}%` : ''}
                                    </span>
                                  ) : <span className="text-danger">—</span>}
                                </td>
                              </tr>
                            ))}
                            {(detalle[f.id] || []).length === 0 && (
                              <tr><td colSpan="7" className="py-2 text-notion-text-secondary">Cargando conceptos…</td></tr>
                            )}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
