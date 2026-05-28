import React, { useEffect, useState } from 'react';
import { Upload, FileText } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { listarFacturas, subirFactura } from '../services/api';
import { useAuth } from '../context/AuthContext';

export default function Facturas() {
  const { isProveedor } = useAuth();
  const [facturas, setFacturas] = useState([]);
  const [xml, setXml] = useState(null);
  const [pdf, setPdf] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState(null);

  const cargar = () => listarFacturas().then((r) => setFacturas(r.data));

  useEffect(() => { cargar(); }, []);

  const handleSubir = async (e) => {
    e.preventDefault();
    if (!xml) return;
    setUploading(true);
    setMsg(null);
    try {
      const { data } = await subirFactura(xml, pdf);
      setMsg({ ok: true, text: `Factura registrada (${data.conceptos} conceptos)` });
      setXml(null); setPdf(null);
      cargar();
    } catch (err) {
      setMsg({ ok: false, text: err.response?.data?.detail || 'Error al subir factura' });
    } finally {
      setUploading(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Facturas"
        subtitle={isProveedor() ? 'Sube tus facturas (XML + PDF) — el sistema las cruzará automáticamente con los pedidos' : 'Facturas subidas por proveedores'}
      />

      {isProveedor() && (
        <div className="bg-white rounded-xl border border-notion-border p-5 mb-6">
          <h3 className="font-semibold mb-4 flex items-center gap-2"><Upload size={18} /> Subir factura</h3>
          <form onSubmit={handleSubir} className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
            <div>
              <label className="block text-xs font-semibold text-notion-text-secondary mb-1">XML (obligatorio)</label>
              <input
                type="file"
                accept=".xml"
                onChange={(e) => setXml(e.target.files?.[0] || null)}
                className="w-full text-sm"
                required
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-notion-text-secondary mb-1">PDF (opcional)</label>
              <input
                type="file"
                accept=".pdf"
                onChange={(e) => setPdf(e.target.files?.[0] || null)}
                className="w-full text-sm"
              />
            </div>
            <button
              type="submit"
              disabled={!xml || uploading}
              className="px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50"
            >
              {uploading ? 'Subiendo...' : 'Subir factura'}
            </button>
          </form>
          {msg && (
            <div className={`mt-3 p-3 rounded-lg text-sm ${msg.ok ? 'bg-green-50 text-success' : 'bg-red-50 text-danger'}`}>
              {msg.text}
            </div>
          )}
        </div>
      )}

      <div className="bg-white rounded-xl border border-notion-border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-notion-bg-subtle border-b border-notion-border">
            <tr>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Folio</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Proveedor</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">UUID</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Fecha</th>
              <th className="text-right px-4 py-3 font-semibold text-notion-text-secondary">Total</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Cruces</th>
            </tr>
          </thead>
          <tbody>
            {facturas.length === 0 ? (
              <tr><td colSpan="6" className="p-8 text-center text-notion-text-secondary">Sin facturas registradas</td></tr>
            ) : facturas.map((f) => (
              <tr key={f.id} className="border-t border-notion-border hover:bg-notion-bg-subtle">
                <td className="px-4 py-3 font-mono text-xs">{f.serie || ''}{f.folio || f.id}</td>
                <td className="px-4 py-3">{f.proveedor_nombre}</td>
                <td className="px-4 py-3 font-mono text-xs truncate max-w-[180px]">{f.uuid_cfdi}</td>
                <td className="px-4 py-3 text-xs">{f.fecha_factura?.split('T')[0]}</td>
                <td className="px-4 py-3 text-right font-medium">${f.total?.toFixed(2)} {f.moneda}</td>
                <td className="px-4 py-3 text-xs">
                  <span className={f.conceptos_matched === f.total_conceptos ? 'text-success' : 'text-warning'}>
                    {f.conceptos_matched}/{f.total_conceptos} conceptos
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
