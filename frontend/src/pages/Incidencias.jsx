import React, { useEffect, useState } from 'react';
import { AlertCircle, CheckCircle, Plus, X } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { listarIncidencias, resolverIncidencia, crearIncidencia } from '../services/api';
import { useAuth } from '../context/AuthContext';

const TIPOS = {
  devolucion: 'Devolución',
  producto_equivocado: 'Producto equivocado',
  no_entregado: 'No entregado',
  factura_tardia: 'Factura tardía',
  factura_incorrecta: 'Factura incorrecta',
  otro: 'Otro',
};

const FORM_VACIO = { num_venta: '', tipo: 'devolucion', descripcion: '' };

export default function Incidencias() {
  const { isAdmin } = useAuth();
  const [items, setItems] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(FORM_VACIO);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const cargar = () => listarIncidencias().then((r) => setItems(r.data));
  useEffect(() => { cargar(); }, []);

  const handleResolver = async (id) => {
    await resolverIncidencia(id);
    cargar();
  };

  const abrirForm = () => { setForm(FORM_VACIO); setError(null); setShowForm(true); };

  const handleCrear = async (e) => {
    e.preventDefault();
    if (!form.num_venta.trim()) { setError('Indica el # de venta'); return; }
    setSaving(true);
    setError(null);
    try {
      await crearIncidencia({ ...form, num_venta: form.num_venta.trim() });
      setShowForm(false);
      setForm(FORM_VACIO);
      await cargar();
    } catch (err) {
      setError(err.response?.data?.detail || 'No se pudo crear la incidencia');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Incidencias"
        subtitle="Devoluciones, productos equivocados y otros problemas asignados a proveedores"
        actions={isAdmin() ? (
          <button
            onClick={abrirForm}
            className="flex items-center gap-1.5 text-sm px-3 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg font-semibold hover:bg-gray-800"
          >
            <Plus size={16} /> Nueva incidencia
          </button>
        ) : null}
      />

      {showForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="bg-white rounded-xl border border-notion-border w-full max-w-md p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-bold text-notion-text-primary">Nueva incidencia</h2>
              <button onClick={() => setShowForm(false)} className="text-notion-text-secondary hover:text-notion-text-primary">
                <X size={18} />
              </button>
            </div>
            <form onSubmit={handleCrear} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-notion-text-secondary mb-1"># de venta ML</label>
                <input
                  type="text"
                  value={form.num_venta}
                  onChange={(e) => setForm({ ...form, num_venta: e.target.value })}
                  placeholder="Ej. 2000012345678901"
                  className="w-full px-3 py-2 border border-notion-border rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-reluvsa-yellow"
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-notion-text-secondary mb-1">Tipo</label>
                <select
                  value={form.tipo}
                  onChange={(e) => setForm({ ...form, tipo: e.target.value })}
                  className="w-full px-3 py-2 border border-notion-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-reluvsa-yellow"
                >
                  {Object.entries(TIPOS).map(([k, label]) => (
                    <option key={k} value={k}>{label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-notion-text-secondary mb-1">Descripción</label>
                <textarea
                  value={form.descripcion}
                  onChange={(e) => setForm({ ...form, descripcion: e.target.value })}
                  rows={3}
                  placeholder="Detalle del problema"
                  className="w-full px-3 py-2 border border-notion-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-reluvsa-yellow"
                />
              </div>
              {error && <p className="text-sm text-reluvsa-red">{error}</p>}
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowForm(false)}
                  className="text-sm px-4 py-2 border border-notion-border rounded-lg font-medium hover:bg-notion-bg-subtle"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="text-sm px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg font-semibold hover:bg-gray-800 disabled:opacity-50"
                >
                  {saving ? 'Creando…' : 'Crear'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="bg-white rounded-xl border border-notion-border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-notion-bg-subtle border-b border-notion-border">
            <tr>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Venta</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Tipo</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Proveedor</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Descripción</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Estado</th>
              {isAdmin() && <th className="px-4 py-3"></th>}
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={isAdmin() ? 6 : 5} className="p-8 text-center text-notion-text-secondary">Sin incidencias</td></tr>
            ) : items.map((i) => (
              <tr key={i.id} className="border-t border-notion-border">
                <td className="px-4 py-3 font-mono text-xs">{i.num_venta}</td>
                <td className="px-4 py-3">
                  <span className="px-2 py-0.5 bg-notion-bg-subtle rounded text-xs font-medium">
                    {TIPOS[i.tipo] || i.tipo}
                  </span>
                </td>
                <td className="px-4 py-3">{i.proveedor_nombre || '—'}</td>
                <td className="px-4 py-3 max-w-md whitespace-pre-wrap break-words">{i.descripcion}</td>
                <td className="px-4 py-3">
                  {i.estado === 'resuelta' ? (
                    <span className="flex items-center gap-1 text-success text-xs font-medium">
                      <CheckCircle size={14} /> Resuelta
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-warning text-xs font-medium">
                      <AlertCircle size={14} /> {i.estado}
                    </span>
                  )}
                </td>
                {isAdmin() && (
                  <td className="px-4 py-3 text-right">
                    {i.estado !== 'resuelta' && (
                      <button
                        onClick={() => handleResolver(i.id)}
                        className="text-xs px-3 py-1 bg-reluvsa-black text-reluvsa-yellow rounded font-semibold hover:bg-gray-800"
                      >
                        Resolver
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
