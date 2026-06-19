import React, { useState } from 'react';
import { Upload, FileSpreadsheet } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { subirVentasML, subirColecta, subirAlbaranes, subirKits } from '../services/api';

function UploadCard({ title, description, onSubmit, accept = '.xlsx' }) {
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const handle = async () => {
    if (!file) return;
    setLoading(true);
    setResult(null);
    try {
      const { data } = await onSubmit(file);
      setResult({ ok: true, data });
    } catch (err) {
      setResult({ ok: false, text: err.response?.data?.detail || 'Error al procesar archivo' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-white rounded-xl border border-notion-border p-5">
      <div className="flex items-center gap-2 mb-2">
        <FileSpreadsheet size={20} className="text-reluvsa-black" />
        <h3 className="font-semibold">{title}</h3>
      </div>
      <p className="text-sm text-notion-text-secondary mb-4">{description}</p>

      <input
        type="file"
        accept={accept}
        onChange={(e) => setFile(e.target.files?.[0] || null)}
        className="w-full text-sm mb-3"
      />

      <button
        onClick={handle}
        disabled={!file || loading}
        className="w-full px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50 flex items-center justify-center gap-2"
      >
        <Upload size={16} /> {loading ? 'Procesando...' : 'Subir'}
      </button>

      {result && (
        <div className={`mt-3 p-3 rounded-lg text-xs ${result.ok ? 'bg-green-50 text-success' : 'bg-red-50 text-danger'}`}>
          {result.ok ? (
            <pre>{JSON.stringify(result.data, null, 2)}</pre>
          ) : (
            result.text
          )}
        </div>
      )}
    </div>
  );
}

export default function Uploads() {
  return (
    <div>
      <PageHeader
        title="Cargar reportes"
        subtitle="Sube los Excels exportados de Mercado Libre"
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <UploadCard
          title="Reporte de Ventas Mercado Libre"
          description="Excel oficial exportado desde ML (Ventas MX). Inserta nuevas ventas y actualiza estado de existentes."
          onSubmit={subirVentasML}
        />
        <UploadCard
          title="Detalle de envíos de colecta"
          description="Excel oficial de colecta. Asigna proveedor automáticamente por columna J (Lugar indicado) y respeta overrides manuales."
          onSubmit={subirColecta}
        />
        <UploadCard
          title="Números de albarán"
          description="Excel con 2 columnas: # de venta y # de albarán. Cruza por número de venta y agrega el albarán a las ventas ya cargadas (solo actualiza, no crea ventas nuevas)."
          onSubmit={subirAlbaranes}
        />
        <UploadCard
          title="Relación kits → componentes"
          description="Excel con 3 columnas: Paquete (KIT), Componente y Cantidad. Permite que las ventas de kits crucen su factura por los componentes reales (el proveedor no factura el código del kit). Se acumula: re-subir actualiza y agrega, no borra lo anterior."
          onSubmit={subirKits}
        />
      </div>
    </div>
  );
}
