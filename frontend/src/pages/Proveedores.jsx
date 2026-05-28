import React, { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import { listarProveedores } from '../services/api';

export default function Proveedores() {
  const [rows, setRows] = useState([]);
  useEffect(() => { listarProveedores().then((r) => setRows(r.data)); }, []);

  return (
    <div>
      <PageHeader
        title="Proveedores"
        subtitle="Los 5 proveedores dropshipping identificados"
      />
      <div className="bg-white rounded-xl border border-notion-border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-notion-bg-subtle border-b border-notion-border">
            <tr>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Nombre</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">RFC</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Código bodega</th>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Estado</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id} className="border-t border-notion-border">
                <td className="px-4 py-3 font-semibold">{p.nombre}</td>
                <td className="px-4 py-3 font-mono text-xs">{p.rfc}</td>
                <td className="px-4 py-3">
                  <span className="px-2 py-0.5 bg-reluvsa-yellow text-reluvsa-black text-xs rounded font-semibold">
                    {p.codigo_bodega}
                  </span>
                </td>
                <td className="px-4 py-3">
                  {p.activo ? (
                    <span className="text-success text-xs font-medium">Activo</span>
                  ) : (
                    <span className="text-notion-text-secondary text-xs">Inactivo</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
