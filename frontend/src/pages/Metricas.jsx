import React, { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import { getMetricasProveedores } from '../services/api';

function MetricCell({ value, suffix, threshold, higherIsBetter = true }) {
  if (value === null || value === undefined) {
    return <span className="text-notion-text-secondary">—</span>;
  }
  let color = 'text-notion-text-primary';
  if (threshold !== undefined) {
    const good = higherIsBetter ? value >= threshold : value <= threshold;
    color = good ? 'text-success' : 'text-danger';
  }
  return <span className={`font-semibold ${color}`}>{value}{suffix}</span>;
}

export default function Metricas() {
  const [rows, setRows] = useState([]);

  useEffect(() => {
    getMetricasProveedores().then((r) => setRows(r.data));
  }, []);

  return (
    <div>
      <PageHeader
        title="Métricas de proveedores"
        subtitle="Desempeño operativo por proveedor"
      />

      <div className="bg-white rounded-xl border border-notion-border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-notion-bg-subtle border-b border-notion-border">
            <tr>
              <th className="text-left px-4 py-3 font-semibold text-notion-text-secondary">Proveedor</th>
              <th className="text-right px-4 py-3 font-semibold text-notion-text-secondary">Envíos</th>
              <th className="text-right px-4 py-3 font-semibold text-notion-text-secondary">% a tiempo</th>
              <th className="text-right px-4 py-3 font-semibold text-notion-text-secondary">Tiempo facturación</th>
              <th className="text-right px-4 py-3 font-semibold text-notion-text-secondary">Errores fact.</th>
              <th className="text-right px-4 py-3 font-semibold text-notion-text-secondary">Incidencias abiertas</th>
              <th className="text-right px-4 py-3 font-semibold text-notion-text-secondary">Días sin actualizar stock</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan="7" className="p-8 text-center text-notion-text-secondary">Sin datos aún. Sube ventas y colecta.</td></tr>
            ) : rows.map((r) => (
              <tr key={r.proveedor_id} className="border-t border-notion-border hover:bg-notion-bg-subtle">
                <td className="px-4 py-3">
                  <div className="font-semibold">{r.proveedor_nombre}</div>
                  <div className="text-xs text-notion-text-secondary font-mono">{r.codigo_bodega}</div>
                </td>
                <td className="px-4 py-3 text-right">{r.total_envios}</td>
                <td className="px-4 py-3 text-right">
                  <MetricCell value={r.porcentaje_entregas_a_tiempo} suffix="%" threshold={90} higherIsBetter />
                </td>
                <td className="px-4 py-3 text-right">
                  <MetricCell value={r.tiempo_promedio_facturacion_dias} suffix=" días" threshold={5} higherIsBetter={false} />
                </td>
                <td className="px-4 py-3 text-right">
                  <MetricCell value={r.errores_facturacion} threshold={3} higherIsBetter={false} />
                </td>
                <td className="px-4 py-3 text-right">
                  <MetricCell value={r.incidencias_abiertas} threshold={0} higherIsBetter={false} />
                </td>
                <td className="px-4 py-3 text-right">
                  <MetricCell value={r.dias_desde_ultima_actualizacion_stock} suffix=" días" threshold={7} higherIsBetter={false} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
