import React, { useState } from 'react';
import { Upload, FileSpreadsheet, Download } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { subirVentasML, subirColecta, subirAlbaranes, subirKits, subirKitsAG, subirCargaERP } from '../services/api';
import { Link } from 'react-router-dom';

function UploadCard({ title, description, onSubmit, accept = '.xlsx', renderResult }) {
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
            renderResult ? renderResult(result.data) : <pre>{JSON.stringify(result.data, null, 2)}</pre>
          ) : (
            result.text
          )}
        </div>
      )}
    </div>
  );
}

function descargarPendientes(base64) {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  }));
  const enlace = document.createElement('a');
  enlace.href = url;
  enlace.download = 'albaranes-filas-sin-aplicar.xlsx';
  enlace.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderResultadoAlbaran(d) {
  const pendientes = d.filas_sin_aplicar || [];
  const total = (d.actualizados || 0) + (d.sin_aplicar || 0);
  return (
    <div className="space-y-2">
      <div className="font-semibold">{d.actualizados} de {total} filas aplicadas · {d.ventas_actualizadas} ventas actualizadas</div>
      <ul className="list-disc list-inside space-y-0.5">
        <li>{d.por_num_venta ?? 0} por # de venta</li>
        <li>{d.por_pack_id ?? 0} por # de carrito (todas sus ventas en la misma cuenta)</li>
        <li>{d.no_encontrados ?? 0} sin coincidencia local</li>
        <li>{d.ambiguos ?? 0} con coincidencia ambigua</li>
        <li>{d.sin_albaran ?? 0} sin albarán ni fecha</li>
      </ul>
      {pendientes.length > 0 && (
        <div className="pt-2 border-t border-green-200 space-y-2 text-notion-text">
          <div className="font-semibold">{d.sin_aplicar} filas sin aplicar</div>
          <p>Sin coincidencia local significa que el número no cruzó con las ventas cargadas en el portal. Revisa el archivo y vuelve a subirlo para recuperar este reporte.</p>
          <button type="button" onClick={() => descargarPendientes(d.reporte_sin_aplicar_base64)}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-reluvsa-black text-reluvsa-yellow font-semibold hover:bg-gray-800">
            <Download size={14} /> Descargar filas sin aplicar
          </button>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead><tr><th className="py-1 pr-2">Fila</th><th className="pr-2">Número</th><th>Resultado</th></tr></thead>
              <tbody>{pendientes.slice(0, 5).map((p) => (
                <tr key={p.fila} className="border-t border-green-100">
                  <td className="py-1 pr-2">{p.fila}</td><td className="pr-2 font-mono">{p.numero_recibido || 'Sin número'}</td>
                  <td>{p.resultado}{p.cuentas_candidatas?.length > 0 && ` (${p.cuentas_candidatas.join(', ')})`}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <p>Vista de {Math.min(pendientes.length, 5)} de {pendientes.length} filas. El Excel incluye todas las filas y sus datos originales.</p>
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
          description="Excel con # de venta, # de albarán y fecha de albarán opcional. Puede mezclar ambas cuentas y números de venta o carrito. Las coincidencias ambiguas se dejan para revisión."
          onSubmit={subirAlbaranes}
          renderResult={renderResultadoAlbaran}
        />
        <UploadCard
          title="Entradas ERP de facturas"
          description="Plantilla ERP con Referencia, Proveedor → Nombre, Fecha Referencia y Fecha Captura. Cada carga queda disponible para auditoría."
          onSubmit={subirCargaERP}
          renderResult={(d) => <div><strong>Carga #{d.carga_id}</strong>: {d.total} filas guardadas ({d.para_revision} para revisar).<Link to="/conciliacion-erp" className="block underline mt-1">Abrir conciliación ERP</Link></div>}
        />
        <UploadCard
          title="Relación kits → componentes"
          description="Excel con 3 columnas: Paquete (KIT), Componente y Cantidad. Permite que las ventas de kits crucen su factura por los componentes reales (el proveedor no factura el código del kit). Se acumula: re-subir actualiza y agrega, no borra lo anterior."
          onSubmit={subirKits}
        />
        <UploadCard
          title="Relación kits AG"
          description="Excel de catálogo AG con Código y Componente 1 a Componente 8. Consolida aplicaciones repetidas y agrega relaciones; cada componente cuenta como una unidad. La carga queda aislada para AG."
          onSubmit={subirKitsAG}
        />
      </div>
    </div>
  );
}
