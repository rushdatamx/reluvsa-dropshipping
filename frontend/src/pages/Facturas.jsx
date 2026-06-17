import React, { useEffect, useState } from 'react';
import {
  Upload, FileText, FileCode, Download, Search, ChevronDown, ChevronRight,
  CheckCircle, AlertTriangle,
} from 'lucide-react';
import PageHeader from '../components/PageHeader';
import {
  listarFacturas, subirFacturasMultiple, getFactura, exportarFacturasCsv,
  descargarArchivoFactura, listarProveedores,
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
      setDetalle((d) => ({ ...d, [f.id]: data.conceptos }));
    }
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
          ? 'Sube tus facturas (XML + PDF) — el sistema las cruzará automáticamente con los pedidos'
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
                        <div className="text-xs font-semibold text-notion-text-secondary mb-2">
                          Conceptos de la factura y la venta a la que cruzan
                        </div>
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="text-notion-text-secondary">
                              <th className="text-left py-1 pr-3 font-medium">Código</th>
                              <th className="text-left py-1 pr-3 font-medium">Descripción</th>
                              <th className="text-right py-1 pr-3 font-medium">Importe</th>
                              <th className="text-left py-1 pr-3 font-medium"># Venta</th>
                              <th className="text-left py-1 pr-3 font-medium">Título de la venta</th>
                              <th className="text-left py-1 font-medium">Cruce</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(detalle[f.id] || []).map((c) => (
                              <tr key={c.id} className="border-t border-notion-border">
                                <td className="py-1.5 pr-3 font-mono">{c.codigo_prov || '—'}</td>
                                <td className="py-1.5 pr-3 max-w-[260px] truncate" title={c.descripcion}>{c.descripcion || '—'}</td>
                                <td className="py-1.5 pr-3 text-right">{c.importe != null ? `$${Number(c.importe).toFixed(2)}` : ''}</td>
                                <td className="py-1.5 pr-3 font-mono">{c.num_venta_match || <span className="text-danger">sin cruce</span>}</td>
                                <td className="py-1.5 pr-3 max-w-[260px] truncate" title={c.venta_titulo}>{c.venta_titulo || '—'}</td>
                                <td className="py-1.5">
                                  {c.num_venta_match ? (
                                    <span className="text-notion-text-secondary">
                                      {c.match_method} · {c.match_confidence != null ? `${Math.round(c.match_confidence * 100)}%` : ''}
                                    </span>
                                  ) : <span className="text-danger">—</span>}
                                </td>
                              </tr>
                            ))}
                            {(detalle[f.id] || []).length === 0 && (
                              <tr><td colSpan="6" className="py-2 text-notion-text-secondary">Cargando conceptos…</td></tr>
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
