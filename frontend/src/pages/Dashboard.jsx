import React, { useEffect, useState } from 'react';
import { ShoppingCart, Package, FileText, AlertCircle, Users } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { getResumen } from '../services/api';

function StatCard({ label, value, icon: Icon, color }) {
  return (
    <div className="bg-white rounded-xl border border-notion-border p-5">
      <div className="flex items-center gap-3 mb-2">
        <div className={`p-2 rounded-lg ${color}`}>
          <Icon size={20} className="text-white" />
        </div>
        <p className="text-sm text-notion-text-secondary">{label}</p>
      </div>
      <p className="text-3xl font-bold text-notion-text-primary">
        {value === null || value === undefined ? '—' : value.toLocaleString('es-MX')}
      </p>
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState(null);

  useEffect(() => {
    getResumen().then((r) => setData(r.data)).catch(() => setData({}));
  }, []);

  return (
    <div>
      <PageHeader
        title="Resumen"
        subtitle="Vista general del portal de dropshipping"
      />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        <StatCard label="Ventas registradas" value={data?.ventas} icon={ShoppingCart} color="bg-reluvsa-black" />
        <StatCard label="Envíos de colecta" value={data?.envios} icon={Package} color="bg-reluvsa-black" />
        <StatCard label="Facturas recibidas" value={data?.facturas} icon={FileText} color="bg-reluvsa-black" />
        <StatCard label="Incidencias abiertas" value={data?.incidencias_abiertas} icon={AlertCircle} color="bg-danger" />
        <StatCard label="Proveedores activos" value={data?.proveedores_activos} icon={Users} color="bg-reluvsa-black" />
      </div>

      <div className="mt-8 bg-white rounded-xl border border-notion-border p-6">
        <h2 className="text-lg font-semibold text-notion-text-primary mb-2">Cómo empezar</h2>
        <ol className="list-decimal list-inside space-y-1 text-sm text-notion-text-secondary">
          <li>Sube el <strong>reporte de Ventas Mercado Libre</strong> y el <strong>Detalle de envíos de colecta</strong> en "Cargar reportes".</li>
          <li>Verifica los cruces en "Ventas y cruces"; reasigna manualmente la bodega de las guías marcadas como "Sin información del lugar".</li>
          <li>Cada proveedor sube su factura (XML + PDF) desde su cuenta — el sistema las cruza automáticamente con los pedidos.</li>
          <li>Las métricas se actualizan en tiempo real en "Métricas proveedores".</li>
        </ol>
      </div>
    </div>
  );
}
