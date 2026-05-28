import React, { useEffect, useState } from 'react';
import { Search, CheckCircle, XCircle, AlertCircle } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { listarVentas } from '../services/api';

export default function Ventas() {
  const [data, setData] = useState({ items: [], total: 0, page: 1 });
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState('');
  const [sinFactura, setSinFactura] = useState(false);

  const cargar = async () => {
    setLoading(true);
    try {
      const { data } = await listarVentas({ q: q || undefined, sin_factura: sinFactura || undefined, page: 1, limit: 100 });
      setData(data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { cargar(); }, []);

  return (
    <div>
      <PageHeader
        title="Ventas y cruces"
        subtitle="Conciliación de ventas Mercado Libre con envíos de colecta y facturas de proveedor"
      />

      <div className="bg-white rounded-xl border border-notion-border p-4 mb-4 flex gap-3 flex-wrap items-end">
        <div className="flex-1 min-w-[200px]">
          <label className="block text-xs font-semibold text-notion-text-secondary mb-1">Buscar</label>
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-notion-text-secondary" />
            <input
              type="text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && cargar()}
              placeholder="# venta, SKU o título"
              className="w-full pl-9 pr-3 py-2 border border-notion-border rounded-lg text-sm focus:outline-none focus:border-reluvsa-black"
            />
          </div>
        </div>
        <label className="flex items-center gap-2 px-3 py-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={sinFactura}
            onChange={(e) => setSinFactura(e.target.checked)}
            className="accent-reluvsa-black"
          />
          Solo sin factura
        </label>
        <button
          onClick={cargar}
          className="px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg text-sm font-semibold hover:bg-gray-800"
        >
          Aplicar
        </button>
      </div>

      <div className="bg-white rounded-xl border border-notion-border overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-notion-bg-subtle border-b border-notion-border">
              <tr>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Venta</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">SKU</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Título</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Proveedor</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">SLA</th>
                <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Factura</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan="6" className="p-8 text-center text-notion-text-secondary">Cargando...</td></tr>
              ) : data.items.length === 0 ? (
                <tr><td colSpan="6" className="p-8 text-center text-notion-text-secondary">Sin ventas registradas. Sube el reporte desde "Cargar reportes".</td></tr>
              ) : data.items.map((v) => (
                <tr key={v.num_venta} className="border-t border-notion-border hover:bg-notion-bg-subtle">
                  <td className="px-4 py-3 font-mono text-xs">{v.num_venta}</td>
                  <td className="px-4 py-3 font-mono text-xs text-reluvsa-red">{v.sku || '—'}</td>
                  <td className="px-4 py-3 max-w-md truncate">{v.titulo || '—'}</td>
                  <td className="px-4 py-3">
                    {v.proveedor_nombre ? (
                      <span className="px-2 py-0.5 bg-reluvsa-black text-reluvsa-yellow text-xs rounded font-semibold">
                        {v.proveedor_nombre}
                      </span>
                    ) : <span className="text-notion-text-secondary text-xs">Sin asignar</span>}
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="border-t border-notion-border px-4 py-2 text-xs text-notion-text-secondary">
          {data.total} ventas · Página {data.page}
        </div>
      </div>
    </div>
  );
}
