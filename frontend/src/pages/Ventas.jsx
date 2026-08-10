import React, { useEffect, useState } from 'react';
import { Search, CheckCircle, XCircle, AlertCircle, Download } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { listarVentas, listarProveedores, reasignarEnvio, exportarVentasCsv } from '../services/api';
import { useAuth } from '../context/AuthContext';

const FILTROS_VACIOS = {
  q: '',
  facturada: '',        // '' = todas | 'true' | 'false'
  sla: '',              // '' = todas | 'a_tiempo' | 'tarde'
  cruce: '',            // '' = todas | 'con_envio' | 'sin_envio' | 'sin_proveedor'
  proveedor_id: '',     // solo admin
  // 'todos' (default: muestra TODO, incluida MATRIZ) | 'proveedores' (oculta MATRIZ) | 'matriz'.
  // El default era 'proveedores'; el cliente pidió ver también las de MATRIZ (2026-08-03).
  deposito: 'todos',
  logistica: '',        // '' = todas | 'full' | 'colecta' | 'otros'
  fecha_desde: '',
  fecha_hasta: '',
};

// Etiqueta y color de cada tipo de logística de ML. La distinción que importa:
// FULL = ML surte desde su bodega (NO es dropshipping, por eso nunca trae bodega
// de proveedor); COLECTA = el proveedor surte, que es lo que el portal mide.
const LOGISTICA = {
  fulfillment:   { txt: 'FULL',    clase: 'bg-purple-100 text-purple-700' },
  cross_docking: { txt: 'COLECTA', clase: 'bg-blue-100 text-blue-700' },
  xd_drop_off:   { txt: 'Places',  clase: 'bg-notion-100 text-notion-600' },
  self_service:  { txt: 'Flex',    clase: 'bg-notion-100 text-notion-600' },
};

const LIMIT = 50; // ventas por página

const MESES_CORTOS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

// Formato corto legible para la fecha de venta (ISO -> "13 may 2026"). La fecha
// llega como ISO con o sin hora (p.ej. "2026-05-13 23:43" o "2026-05-13").
function fechaCorta(iso) {
  if (!iso) return '—';
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  const [, anio, mes, dia] = m;
  return `${parseInt(dia, 10)} ${MESES_CORTOS[parseInt(mes, 10) - 1]} ${anio}`;
}

// El "Total (MXN)" que Gaby ve en el portal de ML: lo que RELUVSA recibe ya
// descontados cargos por venta, envíos e impuestos. Las ventas cargadas antes de
// que existiera la columna quedan en '—' hasta que el sync las vuelva a tocar
// (se decidió no hacer backfill), por eso se distingue el null del 0.
function montoMXN(valor) {
  if (valor == null) return '—';
  return valor.toLocaleString('es-MX', { style: 'currency', currency: 'MXN' });
}

export default function Ventas() {
  const { isAdmin } = useAuth();
  const [data, setData] = useState({ items: [], total: 0, page: 1 });
  const [loading, setLoading] = useState(false);
  const [exportando, setExportando] = useState(false);
  const [filtros, setFiltros] = useState(FILTROS_VACIOS);
  const [proveedores, setProveedores] = useState([]);
  const [reasignando, setReasignando] = useState(null); // num_envio en curso

  // Convierte el estado de filtros a los query params del backend (omite vacíos).
  const paramsDeFiltros = () => {
    const p = {};
    Object.entries(filtros).forEach(([k, v]) => { if (v) p[k] = v; });
    return p;
  };

  const cargar = async (p = 1) => {
    setLoading(true);
    try {
      const { data } = await listarVentas({ ...paramsDeFiltros(), page: p, limit: LIMIT });
      setData(data);
    } finally {
      setLoading(false);
    }
  };

  const onExportar = async () => {
    setExportando(true);
    try {
      const { data: blob } = await exportarVentasCsv(paramsDeFiltros());
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'ventas_cruces.csv';
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      alert('No se pudo exportar: ' + (e.response?.data?.detail || e.message));
    } finally {
      setExportando(false);
    }
  };

  const limpiar = async () => {
    setFiltros(FILTROS_VACIOS);
    setLoading(true);
    try {
      // FILTROS_VACIOS trae deposito='todos'; mandamos ese default explícito.
      const { data } = await listarVentas({ deposito: 'todos', page: 1, limit: LIMIT });
      setData(data);
    } finally {
      setLoading(false);
    }
  };
  const set = (k, v) => setFiltros((f) => ({ ...f, [k]: v }));

  useEffect(() => { cargar(); }, []);
  useEffect(() => {
    listarProveedores().then(({ data }) => setProveedores(data)).catch(() => {});
  }, []);

  // Reasigna la bodega de un envío (caso col J = MATRIZ / vacío, sin proveedor
  // dropshipping). El backend resuelve el proveedor desde el código de bodega.
  const onReasignar = async (numEnvio, codigoBodega) => {
    if (!numEnvio || !codigoBodega) return;
    setReasignando(numEnvio);
    try {
      await reasignarEnvio(numEnvio, { lugar_override: codigoBodega });
      await cargar(data.page); // recargar en la misma página, no saltar a la 1
    } catch (e) {
      alert('No se pudo reasignar la bodega: ' + (e.response?.data?.detail || e.message));
    } finally {
      setReasignando(null);
    }
  };

  return (
    <div>
      <PageHeader
        title="Ventas y cruces"
        subtitle="Conciliación de ventas Mercado Libre con envíos de colecta y facturas de proveedor"
        actions={
          <button
            onClick={onExportar}
            disabled={exportando}
            className="flex items-center gap-1.5 text-sm px-3 py-2 border border-notion-border rounded-lg font-semibold hover:bg-notion-bg-subtle disabled:opacity-50"
          >
            <Download size={16} /> {exportando ? 'Exportando…' : 'Exportar CSV'}
          </button>
        }
      />

      <div className="bg-white rounded-xl border border-notion-border p-4 mb-4 space-y-3">
        <div className="flex gap-3 flex-wrap items-end">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Buscar</label>
            <div className="relative">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-notion-text-secondary" />
              <input
                type="text"
                value={filtros.q}
                onChange={(e) => set('q', e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && cargar(1)}
                placeholder="# de venta de ML, SKU o título"
                className="w-full pl-9 pr-3 py-2 border border-notion-border rounded-lg text-sm focus:outline-none focus:border-reluvsa-black"
              />
            </div>
          </div>
          <div className="min-w-[150px]">
            <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Facturación</label>
            <select value={filtros.facturada} onChange={(e) => set('facturada', e.target.value)}
              className="w-full px-3 py-2 border border-notion-border rounded-lg text-sm focus:outline-none focus:border-reluvsa-black">
              <option value="">Todas</option>
              <option value="true">Facturadas</option>
              <option value="false">Sin factura</option>
            </select>
          </div>
          <div className="min-w-[140px]">
            <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Entrega (SLA)</label>
            <select value={filtros.sla} onChange={(e) => set('sla', e.target.value)}
              className="w-full px-3 py-2 border border-notion-border rounded-lg text-sm focus:outline-none focus:border-reluvsa-black">
              <option value="">Todas</option>
              <option value="a_tiempo">A tiempo</option>
              <option value="tarde">Tarde</option>
            </select>
          </div>
          <div className="min-w-[170px]">
            <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Cruce con colecta</label>
            <select value={filtros.cruce} onChange={(e) => set('cruce', e.target.value)}
              className="w-full px-3 py-2 border border-notion-border rounded-lg text-sm focus:outline-none focus:border-reluvsa-black">
              <option value="">Todas</option>
              <option value="con_envio">Con envío</option>
              <option value="sin_envio">Sin envío</option>
              <option value="sin_proveedor">Envío sin proveedor</option>
            </select>
          </div>
          <div className="min-w-[160px]">
            <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Depósito (bodega)</label>
            <select value={filtros.deposito} onChange={(e) => set('deposito', e.target.value)}
              className="w-full px-3 py-2 border border-notion-border rounded-lg text-sm focus:outline-none focus:border-reluvsa-black">
              <option value="todos">Todos</option>
              <option value="proveedores">Solo proveedores</option>
              <option value="matriz">Solo MATRIZ</option>
            </select>
          </div>
          <div className="min-w-[160px]">
            <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Logística</label>
            <select value={filtros.logistica} onChange={(e) => set('logistica', e.target.value)}
              className="w-full px-3 py-2 border border-notion-border rounded-lg text-sm focus:outline-none focus:border-reluvsa-black">
              <option value="">Todas</option>
              <option value="colecta">Solo COLECTA</option>
              <option value="full">Solo FULL</option>
              <option value="otros">Otras (Places/Flex)</option>
            </select>
          </div>
        </div>
        <div className="flex gap-3 flex-wrap items-end">
          {isAdmin() && (
            <div className="min-w-[180px]">
              <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Proveedor</label>
              <select value={filtros.proveedor_id} onChange={(e) => set('proveedor_id', e.target.value)}
                className="w-full px-3 py-2 border border-notion-border rounded-lg text-sm focus:outline-none focus:border-reluvsa-black">
                <option value="">Todos</option>
                {proveedores.map((p) => (
                  <option key={p.id} value={p.id}>{p.nombre}</option>
                ))}
              </select>
            </div>
          )}
          <div className="min-w-[150px]">
            <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Venta desde</label>
            <input type="date" value={filtros.fecha_desde} onChange={(e) => set('fecha_desde', e.target.value)}
              className="w-full px-3 py-2 border border-notion-border rounded-lg text-sm focus:outline-none focus:border-reluvsa-black" />
          </div>
          <div className="min-w-[150px]">
            <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Venta hasta</label>
            <input type="date" value={filtros.fecha_hasta} onChange={(e) => set('fecha_hasta', e.target.value)}
              className="w-full px-3 py-2 border border-notion-border rounded-lg text-sm focus:outline-none focus:border-reluvsa-black" />
          </div>
          <button
            onClick={() => cargar(1)}
            className="px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg text-sm font-semibold hover:bg-gray-800"
          >
            Aplicar
          </button>
          <button
            onClick={limpiar}
            className="px-4 py-2 border border-notion-border rounded-lg text-sm font-medium hover:bg-notion-bg-subtle"
          >
            Limpiar
          </button>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-notion-border overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-notion-bg-subtle border-b border-notion-border">
              <tr>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Venta</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Albarán</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Fecha</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">SKU</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Título</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Unidades</th>
                <th className="text-right px-4 py-3 font-semibold text-notion-text-secondary">Total (MXN)</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Logística</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Proveedor / Bodega</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">SLA</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Factura</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Factura #</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan="12" className="p-8 text-center text-notion-text-secondary">Cargando...</td></tr>
              ) : data.items.length === 0 ? (
                <tr><td colSpan="12" className="p-8 text-center text-notion-text-secondary">Sin ventas registradas. Sube el reporte desde "Cargar reportes".</td></tr>
              ) : data.items.map((v) => (
                <tr key={v.num_venta} className="border-t border-notion-border hover:bg-notion-bg-subtle">
                  <td className="px-4 py-3 font-mono text-xs">
                    {/* El número que Gaby ve en el portal de Mercado Libre: el pack_id
                        cuando la venta viene de un carrito, y si no el num_venta (que es
                        justo lo que ML muestra en esas). El interno queda en el title
                        para soporte, sin cargar la tabla. */}
                    <span title={v.pack_id ? `Nº interno del portal: ${v.num_venta}` : undefined}>
                      {v.pack_id || v.num_venta}
                    </span>
                    {v.deposito && (
                      <span
                        className={`ml-2 px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                          v.deposito === 'MATRIZ'
                            ? 'bg-notion-bg-subtle text-notion-text-secondary'
                            : 'bg-reluvsa-yellow/30 text-reluvsa-black'
                        }`}
                        title={`Depósito (bodega de origen): ${v.deposito}`}
                      >
                        {v.deposito}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs whitespace-nowrap">
                    {v.albaran ? v.albaran : <span className="text-notion-text-secondary">—</span>}
                  </td>
                  <td className="px-4 py-3 text-xs text-notion-text-secondary whitespace-nowrap">{fechaCorta(v.fecha_venta)}</td>
                  <td className="px-4 py-3 font-mono text-xs whitespace-nowrap">
                    <span className="text-reluvsa-red">{v.sku || '—'}</span>
                    {v.kit_componentes && v.kit_componentes.length > 0 && (
                      <div className="mt-1 space-y-0.5" title="Componentes del kit (lo que el proveedor factura)">
                        {v.kit_componentes.map((c, i) => (
                          <div key={i} className="text-[11px] text-notion-text-secondary">
                            {c.codigo} ×{c.cantidad}
                          </div>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 max-w-md truncate">{v.titulo || '—'}</td>
                  <td className="px-4 py-3 text-xs">{v.unidades != null ? v.unidades : '—'}</td>
                  <td className="px-4 py-3 text-right whitespace-nowrap tabular-nums">
                    {montoMXN(v.total_neto)}
                  </td>
                  <td className="px-4 py-3">
                    {v.logistic_type ? (
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                          (LOGISTICA[v.logistic_type] || {}).clase || 'bg-notion-100 text-notion-600'
                        }`}
                        title={
                          v.logistic_type === 'fulfillment'
                            ? 'FULL: la mercancía sale de la bodega de Mercado Libre, no del proveedor (no es dropshipping)'
                            : `Tipo de logística de ML: ${v.logistic_type}`
                        }
                      >
                        {(LOGISTICA[v.logistic_type] || {}).txt || v.logistic_type}
                      </span>
                    ) : (
                      <span className="text-notion-text-secondary text-xs">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {v.proveedor_nombre ? (
                      <div className="flex items-center gap-2">
                        <span className="px-2 py-0.5 bg-reluvsa-black text-reluvsa-yellow text-xs rounded font-semibold">
                          {v.proveedor_nombre}
                        </span>
                        {v.lugar_override && (
                          <span className="text-[10px] text-notion-text-secondary" title="Bodega reasignada manualmente">✎ manual</span>
                        )}
                      </div>
                    ) : v.logistic_type === 'fulfillment' ? (
                      // FULL: la mercancía ya está en la bodega de ML, así que ML manda
                      // origin=null y NUNCA habrá bodega de proveedor. Pedirle a Gaby que
                      // "asigne bodega" aquí era trabajo inútil sobre una venta que ni
                      // siquiera es dropshipping — por eso se muestra el motivo, no el selector.
                      <span
                        className="text-notion-text-secondary text-xs"
                        title="Las ventas FULL las despacha Mercado Libre desde su propia bodega: no hay proveedor dropshipping que asignar."
                      >
                        No aplica (FULL)
                      </span>
                    ) : v.num_envio ? (
                      // Hay envío de colecta pero la col J (Lugar indicado) no mapea a
                      // un proveedor dropshipping (MATRIZ / vacío). Gaby reasigna aquí.
                      <select
                        value=""
                        disabled={reasignando === v.num_envio}
                        onChange={(e) => onReasignar(v.num_envio, e.target.value)}
                        className="text-xs border border-warning/50 bg-warning/5 rounded px-2 py-1 focus:outline-none focus:border-reluvsa-black"
                        title={v.lugar_indicado ? `Lugar indicado: ${v.lugar_indicado}` : 'Sin lugar indicado'}
                      >
                        <option value="">
                          {reasignando === v.num_envio ? 'Asignando…' : '⚠ Asignar bodega…'}
                        </option>
                        {proveedores.map((p) => (
                          <option key={p.id} value={p.codigo_bodega}>{p.codigo_bodega} — {p.nombre}</option>
                        ))}
                      </select>
                    ) : (
                      <span className="text-notion-text-secondary text-xs">Sin envío</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {v.cumplio_sla === 1 ? (
                      <CheckCircle size={16} className="text-success" />
                    ) : v.cumplio_sla === 0 ? (
                      <XCircle size={16} className="text-danger" />
                    ) : (
                      <span className="text-notion-text-secondary text-xs">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {v.facturas_count > 0 ? (
                      <span className="text-success text-xs font-medium">✓ Facturado</span>
                    ) : (
                      <span className="text-warning text-xs font-medium flex items-center gap-1">
                        <AlertCircle size={14} /> Pendiente
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs whitespace-nowrap">
                    {v.facturas_num ? v.facturas_num : <span className="text-notion-text-secondary">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="border-t border-notion-border px-4 py-2 flex items-center justify-between">
          <span className="text-xs text-notion-text-secondary">
            {data.total} ventas · Página {data.page} de {Math.max(1, Math.ceil(data.total / LIMIT))}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => cargar(data.page - 1)}
              disabled={data.page <= 1 || loading}
              className="px-3 py-1.5 border border-notion-border rounded-lg text-sm font-medium hover:bg-notion-bg-subtle disabled:opacity-40 disabled:cursor-not-allowed"
            >
              ‹ Anterior
            </button>
            <button
              onClick={() => cargar(data.page + 1)}
              disabled={data.page >= Math.ceil(data.total / LIMIT) || loading}
              className="px-3 py-1.5 border border-notion-border rounded-lg text-sm font-medium hover:bg-notion-bg-subtle disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Siguiente ›
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
