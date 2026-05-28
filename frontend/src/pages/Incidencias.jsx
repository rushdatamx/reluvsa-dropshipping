import React, { useEffect, useState } from 'react';
import { AlertCircle, CheckCircle } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { listarIncidencias, resolverIncidencia } from '../services/api';
import { useAuth } from '../context/AuthContext';

const TIPOS = {
  devolucion: 'Devolución',
  producto_equivocado: 'Producto equivocado',
  no_entregado: 'No entregado',
  factura_tardia: 'Factura tardía',
  factura_incorrecta: 'Factura incorrecta',
  otro: 'Otro',
};

export default function Incidencias() {
  const { isAdmin } = useAuth();
  const [items, setItems] = useState([]);

  const cargar = () => listarIncidencias().then((r) => setItems(r.data));
  useEffect(() => { cargar(); }, []);

  const handleResolver = async (id) => {
    await resolverIncidencia(id);
    cargar();
  };

  return (
    <div>
      <PageHeader
        title="Incidencias"
        subtitle="Devoluciones, productos equivocados y otros problemas asignados a proveedores"
      />

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
                <td className="px-4 py-3 max-w-md truncate">{i.descripcion}</td>
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
