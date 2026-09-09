import React, { useMemo, useState } from 'react';
import {
  AlertTriangle, CarFront, Check, ChevronLeft, ChevronRight, Download,
  FileSpreadsheet, Loader2, Search, X,
} from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { autozurAnalizar, autozurDetalle, autozurGenerar } from '../services/api';

const inputCls = 'w-full px-3 py-2 border border-notion-border rounded-lg text-sm mt-1 bg-white';
const POR_PAGINA = 20;

function CampoArchivo({ label, hint, accept = '.xlsx', onChange }) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      <span className="block text-xs text-notion-text-secondary mb-1">{hint}</span>
      <input type="file" accept={accept} className="w-full text-sm mt-2"
             onChange={(e) => onChange(e.target.files?.[0] || null)} />
    </label>
  );
}

function Estado({ estado, decision }) {
  const estilos = {
    lista: 'bg-green-100 text-green-800',
    revision: 'bg-amber-100 text-amber-800',
    sin_coincidencia: 'bg-red-100 text-red-800',
  };
  const textos = {
    lista: 'Lista', revision: 'Revisión', sin_coincidencia: 'Sin coincidencia',
  };
  if (decision === 'aprobada') return <span className="px-2 py-1 rounded-full text-xs font-semibold bg-green-100 text-green-800">Aprobada</span>;
  if (decision === 'excluida') return <span className="px-2 py-1 rounded-full text-xs font-semibold bg-gray-200 text-gray-700">Excluida</span>;
  return <span className={`px-2 py-1 rounded-full text-xs font-semibold ${estilos[estado]}`}>{textos[estado]}</span>;
}

function Dato({ label, value }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wide text-notion-text-secondary">{label}</p>
      <p className="text-sm font-medium">{value || '—'}</p>
    </div>
  );
}

export default function PublicacionesAutozur() {
  const [publicaciones, setPublicaciones] = useState(null);
  const [catalogo, setCatalogo] = useState(null);
  const [analisis, setAnalisis] = useState(null);
  const [decisiones, setDecisiones] = useState({});
  const [detalles, setDetalles] = useState({});
  const [cargandoDetalle, setCargandoDetalle] = useState(null);
  const [estado, setEstado] = useState('revision');
  const [busqueda, setBusqueda] = useState('');
  const [pagina, setPagina] = useState(1);
  const [cargando, setCargando] = useState(false);
  const [generando, setGenerando] = useState(false);
  const [error, setError] = useState(null);

  const analizar = async () => {
    if (!publicaciones || !catalogo) return;
    setCargando(true); setError(null); setAnalisis(null); setDecisiones({}); setDetalles({}); setPagina(1);
    try {
      const { data } = await autozurAnalizar(publicaciones, catalogo);
      setAnalisis(data);
      setEstado(data.revision ? 'revision' : 'lista');
    } catch (err) {
      setError(err.response?.data?.detail || 'No pude analizar los archivos de Autozur.');
    } finally {
      setCargando(false);
    }
  };

  const resultados = useMemo(() => {
    if (!analisis) return [];
    const q = busqueda.trim().toLowerCase();
    return analisis.resultados.filter((r) => {
      const estadoVisible = estado === 'todos' || r.estado === estado;
      const coincide = !q || `${r.user_product_id} ${r.sku} ${r.titulo}`.toLowerCase().includes(q);
      return estadoVisible && coincide;
    });
  }, [analisis, estado, busqueda]);

  const paginas = Math.max(1, Math.ceil(resultados.length / POR_PAGINA));
  const visibles = resultados.slice((pagina - 1) * POR_PAGINA, pagina * POR_PAGINA);

  const cambiarFiltro = (valor) => { setEstado(valor); setPagina(1); };
  const decidir = (id, decision) => setDecisiones((prev) => {
    const copia = { ...prev };
    if (copia[id] === decision) delete copia[id];
    else copia[id] = decision;
    return copia;
  });

  const cargarPropuestas = async (id) => {
    setCargandoDetalle(id); setError(null);
    try {
      const { data } = await autozurDetalle(analisis.session_id, id);
      setDetalles((prev) => ({ ...prev, [id]: data.compatibilidades || [] }));
    } catch (err) {
      setError(err.response?.data?.detail || 'No pude cargar todas las compatibilidades propuestas.');
    } finally {
      setCargandoDetalle(null);
    }
  };

  const generar = async () => {
    setGenerando(true); setError(null);
    const aprobadas = Object.entries(decisiones).filter(([, v]) => v === 'aprobada').map(([id]) => Number(id));
    const excluidas = Object.entries(decisiones).filter(([, v]) => v === 'excluida').map(([id]) => Number(id));
    try {
      const respuesta = await autozurGenerar(analisis.session_id, aprobadas, excluidas);
      const url = URL.createObjectURL(new Blob([respuesta.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = 'compatibilidades_autozur.xlsx';
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      let detalle = 'No pude generar el archivo de compatibilidades.';
      try { detalle = JSON.parse(await err.response.data.text()).detail || detalle; } catch (_) {}
      setError(detalle);
    } finally {
      setGenerando(false);
    }
  };

  return (
    <div>
      <PageHeader title="Publicaciones Autozur"
        subtitle="Cruza publicaciones existentes con el catálogo vehicular y revisa los casos ambiguos" />

      {error && <div className="mb-4 p-3 bg-red-50 text-danger rounded-lg text-sm flex gap-2">
        <AlertTriangle size={16} className="shrink-0 mt-0.5" /> {error}
      </div>}

      <div className="bg-white rounded-xl border border-notion-border p-5 mb-4">
        <div className="flex items-center gap-2 mb-4">
          <span className="w-6 h-6 rounded-full bg-reluvsa-black text-reluvsa-yellow text-xs font-bold flex items-center justify-center">1</span>
          <h3 className="font-semibold">Carga los archivos de Autozur</h3>
        </div>
        <div className="grid md:grid-cols-2 gap-5">
          <CampoArchivo label="Publicaciones sin compatibilidad"
            hint="Plantilla con UserProductID, título y SKU" onChange={setPublicaciones} />
          <CampoArchivo label="Catálogo de vehículos México"
            hint="Catálogo maestro con fabricante, modelo, años y motor" onChange={setCatalogo} />
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button onClick={analizar} disabled={!publicaciones || !catalogo || cargando}
            className="px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50 flex items-center gap-2">
            {cargando ? <Loader2 size={16} className="animate-spin" /> : <FileSpreadsheet size={16} />}
            {cargando ? 'Analizando 349 mil vehículos…' : 'Analizar compatibilidades'}
          </button>
          <p className="text-xs text-notion-text-secondary">El proceso es local y no modifica publicaciones en Mercado Libre.</p>
        </div>
      </div>

      {analisis && <>
        <div className="bg-white rounded-xl border border-notion-border p-5 mb-4">
          <div className="flex items-center gap-2 mb-4">
            <span className="w-6 h-6 rounded-full bg-reluvsa-black text-reluvsa-yellow text-xs font-bold flex items-center justify-center">2</span>
            <h3 className="font-semibold">Resultado del cruce</h3>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
            {[
              ['Publicaciones', analisis.publicaciones, ''],
              ['Listas', analisis.listas, 'text-success'],
              ['A revisión', analisis.revision, 'text-amber-700'],
              ['Sin coincidencia', analisis.sin_coincidencia, 'text-reluvsa-red'],
              ['Compatibilidades', analisis.compatibilidades_automaticas, ''],
              ['Vehículos catálogo', analisis.filas_catalogo, ''],
            ].map(([label, valor, cls]) => <div key={label} className="bg-notion-bg rounded-lg p-3">
              <p className="text-xs text-notion-text-secondary">{label}</p>
              <p className={`text-2xl font-semibold ${cls}`}>{Number(valor).toLocaleString('es-MX')}</p>
            </div>)}
          </div>
        </div>

        <div className="bg-white rounded-xl border border-notion-border p-5 mb-4">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 mb-4">
            <div>
              <h3 className="font-semibold">Revisión de publicaciones</h3>
              <p className="text-xs text-notion-text-secondary mt-1">Las ambiguas sólo se exportan después de aprobarlas.</p>
            </div>
            <div className="flex flex-col sm:flex-row gap-2">
              <div className="relative">
                <Search size={15} className="absolute left-3 top-3 text-gray-400" />
                <input value={busqueda} onChange={(e) => { setBusqueda(e.target.value); setPagina(1); }}
                  placeholder="ID, SKU o título" className={`${inputCls} mt-0 pl-9 sm:w-64`} />
              </div>
              <select value={estado} onChange={(e) => cambiarFiltro(e.target.value)} className={`${inputCls} mt-0 sm:w-44`}>
                <option value="revision">A revisión</option>
                <option value="lista">Listas</option>
                <option value="sin_coincidencia">Sin coincidencia</option>
                <option value="todos">Todas</option>
              </select>
            </div>
          </div>

          <div className="space-y-3">
            {visibles.map((r) => {
              const decision = decisiones[r.id];
              const x = r.extraido;
              const propuestas = detalles[r.id] || r.muestra;
              const detalleCompleto = r.compatibilidades <= r.muestra.length || !!detalles[r.id];
              return <article key={r.id} className="border border-notion-border rounded-xl p-4 hover:border-gray-400 transition-colors">
                <div className="flex flex-col md:flex-row md:items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <Estado estado={r.estado} decision={decision} />
                      <span className="text-xs text-notion-text-secondary">ID {r.user_product_id} · SKU {r.sku}</span>
                    </div>
                    <p className="text-sm font-semibold">{r.titulo}</p>
                    {r.motivo && <p className="text-xs text-amber-800 mt-1">{r.motivo}</p>}
                  </div>
                  <div className="flex gap-2 shrink-0">
                    {r.estado === 'revision' && r.compatibilidades > 0 && detalleCompleto &&
                      <button onClick={() => decidir(r.id, 'aprobada')}
                        className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-green-600 text-white hover:bg-green-700 flex items-center gap-1">
                        <Check size={13} /> Aprobar propuestas
                      </button>}
                    {r.estado === 'revision' && r.compatibilidades > r.muestra.length && !detalleCompleto &&
                      <button onClick={() => cargarPropuestas(r.id)} disabled={cargandoDetalle === r.id}
                        className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50 flex items-center gap-1">
                        {cargandoDetalle === r.id ? <Loader2 size={13} className="animate-spin" /> : <CarFront size={13} />}
                        Revisar {r.compatibilidades.toLocaleString('es-MX')} propuestas
                      </button>}
                    {(r.estado === 'lista' || r.estado === 'revision') &&
                      <button onClick={() => decidir(r.id, 'excluida')}
                        className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-notion-border hover:bg-notion-bg flex items-center gap-1">
                        <X size={13} /> Excluir
                      </button>}
                  </div>
                </div>

                <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-4 p-3 bg-notion-bg rounded-lg">
                  <Dato label="Fabricante" value={x.fabricante} />
                  <Dato label="Modelo" value={x.modelo} />
                  <Dato label="Años" value={x.anio_desde ? `${x.anio_desde}–${x.anio_hasta}` : ''} />
                  <Dato label="Litros" value={x.litros} />
                  <Dato label="Cilindros" value={x.cilindros} />
                </div>

                {propuestas.length > 0 && <div className="mt-3 overflow-x-auto">
                  <p className="text-xs font-semibold mb-2">{r.compatibilidades.toLocaleString('es-MX')} compatibilidades propuestas</p>
                  <div className={detalleCompleto && propuestas.length > 5 ? 'max-h-72 overflow-auto border border-notion-border rounded-lg' : ''}>
                  <table className="w-full text-xs">
                    <thead><tr className="text-left text-notion-text-secondary border-b border-notion-border">
                      {['Fabricante', 'Modelo', 'Año', 'Submodelo', 'Litros', 'Cilindros'].map((h) => <th key={h} className="py-1.5 pr-3 font-medium">{h}</th>)}
                    </tr></thead>
                    <tbody>{propuestas.map((c, i) => <tr key={`${r.id}-${i}`} className="border-b border-notion-border/50">
                      {c.slice(0, 6).map((v, j) => <td key={j} className="py-1.5 pr-3 whitespace-nowrap">{v || '—'}</td>)}
                    </tr>)}</tbody>
                  </table>
                  </div>
                  {!detalleCompleto && <p className="text-[11px] text-notion-text-secondary mt-1">Se muestran 5. Abre todas las propuestas antes de aprobar.</p>}
                </div>}
              </article>;
            })}
            {!visibles.length && <div className="text-center py-10 text-sm text-notion-text-secondary">No hay publicaciones con este filtro.</div>}
          </div>

          <div className="flex items-center justify-between mt-4">
            <p className="text-xs text-notion-text-secondary">{resultados.length.toLocaleString('es-MX')} resultados</p>
            <div className="flex items-center gap-2">
              <button onClick={() => setPagina((p) => Math.max(1, p - 1))} disabled={pagina === 1}
                className="p-2 border border-notion-border rounded-lg disabled:opacity-40"><ChevronLeft size={16} /></button>
              <span className="text-xs">Página {pagina} de {paginas}</span>
              <button onClick={() => setPagina((p) => Math.min(paginas, p + 1))} disabled={pagina === paginas}
                className="p-2 border border-notion-border rounded-lg disabled:opacity-40"><ChevronRight size={16} /></button>
            </div>
          </div>
        </div>

        <div className="bg-white rounded-xl border border-notion-border p-5">
          <div className="flex items-center gap-2 mb-2">
            <span className="w-6 h-6 rounded-full bg-reluvsa-black text-reluvsa-yellow text-xs font-bold flex items-center justify-center">3</span>
            <h3 className="font-semibold">Descarga la plantilla</h3>
          </div>
          <p className="text-xs text-notion-text-secondary mb-4">Incluye las coincidencias claras y las revisiones aprobadas. Excluye automáticamente los casos pendientes.</p>
          <button onClick={generar} disabled={generando || (analisis.listas === 0 && !Object.values(decisiones).includes('aprobada'))}
            className="px-4 py-2 bg-reluvsa-yellow text-reluvsa-black rounded-lg text-sm font-bold hover:brightness-95 disabled:opacity-50 flex items-center gap-2">
            {generando ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
            {generando ? 'Generando…' : 'Descargar compatibilidades Autozur'}
          </button>
        </div>
      </>}
    </div>
  );
}
