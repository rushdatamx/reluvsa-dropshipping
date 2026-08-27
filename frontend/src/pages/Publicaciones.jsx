import React, { useEffect, useState } from 'react';
import { PackagePlus, FileSpreadsheet, Download, AlertTriangle, Check, Loader2 } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { pubProveedores, pubAnalizar, pubGenerar } from '../services/api';

// Cuerpo fijo de la descripción. Es el mismo texto de la plantilla real de Gaby:
// ella lo edita una vez por proveedor y no lo vuelve a tocar.
const DESCRIPCION_BASE_DEFAULT = `IMPORTANTE

1. ¿No estas seguro de que el producto sea el que estás buscando?, ¿tienes dudas de su compatibilidad, originalidad o función?

R: Nosotros te apoyamos con todas tus dudas en la sección de preguntas, por favor NO realices la compra si no estas seguro de que el producto es el que necesitas, realiza todas las preguntas que consideres pertinentes antes de realizar la compra.

2. ¿Realizaste la compra pero no estas conforme con el producto?
R: ¡Contáctanos! por medio del chat de la compra, estamos para apoyarte en todo lo que necesites, por favor NO devuelvas el producto sin antes habernos contactado, te responderemos lo más pronto posible.

Horarios de atención: Lun-Vie 08:30 a 5:30 y Sábados de 8:00 a 4:00

BENEFICIOS DE COMPRAR EN LA TIENDA OFICIAL RELUVSA:
RELUVSA es la marca más reconocida de Autopartes en México, manejamos los productos más novedosos, la mayor variedad y una calidad excelente. El mercado de los accesorios para autos es muy amplio y existen muchos productos sin marca o piratas, nosotros te garantizamos los mejores productos del mercado 100% originales con el respaldo y la calidad que solo RELUVSA puede ofrecerte. Tú compra está completamente protegida.

¡GARANTÍA DE SATISFACCIÓN TOTAL!
Si el producto no satisface tus expectativas la devolución es ¡Gratis!

FACTURACIÓN
Contamos con un Sistema de auto-facturación, al momento de recibir tu producto te llegará un enlace para poder realizarla.

Todas las aplicaciones son referenciales, se debe validar físicamente el producto.`;

function Campo({ label, hint, children }) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      {hint && <span className="block text-xs text-notion-text-secondary mb-1">{hint}</span>}
      {children}
    </label>
  );
}

const inputCls = 'w-full px-3 py-2 border border-notion-border rounded-lg text-sm mt-1';

export default function Publicaciones() {
  const [soportados, setSoportados] = useState([]);
  const [envioPendiente, setEnvioPendiente] = useState(false);
  const [proveedor, setProveedor] = useState('KG');
  const [catalogo, setCatalogo] = useState(null);
  const [publicacionesML, setPublicacionesML] = useState(null);

  const [analisis, setAnalisis] = useState(null);
  const [lineasSel, setLineasSel] = useState([]);
  const [cargando, setCargando] = useState(false);
  const [generando, setGenerando] = useState(false);
  const [error, setError] = useState(null);

  const [config, setConfig] = useState({
    marca: '', categoria_ml: '', cantidad: 10,
    iva: 0.16, utilidad: 0.5, comision_ml: 0.13, envio: 0,
    descripcion_base: DESCRIPCION_BASE_DEFAULT,
  });

  useEffect(() => {
    pubProveedores()
      .then(({ data }) => {
        setSoportados(data.soportados || []);
        setEnvioPendiente(!!data.envio_pendiente);
        if (data.soportados?.length) setProveedor(data.soportados[0]);
      })
      .catch(() => {});
  }, []);

  const analizar = async () => {
    if (!catalogo) return;
    setCargando(true); setError(null); setAnalisis(null); setLineasSel([]);
    try {
      const { data } = await pubAnalizar(proveedor, catalogo, publicacionesML);
      setAnalisis(data);
    } catch (err) {
      setError(err.response?.data?.detail || 'No pude analizar el catálogo.');
    } finally {
      setCargando(false);
    }
  };

  const generar = async () => {
    if (!catalogo) return;
    setGenerando(true); setError(null);
    try {
      const { data } = await pubGenerar({
        codigo_bodega: proveedor,
        catalogo,
        publicaciones_ml: publicacionesML,
        lineas: lineasSel.length ? JSON.stringify(lineasSel) : '',
        solo_faltantes: true,
        ...config,
      });
      const url = URL.createObjectURL(new Blob([data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `publicaciones_${proveedor.toLowerCase()}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      // El error viene como Blob porque pedimos responseType blob.
      let detalle = 'No pude generar la plantilla.';
      try { detalle = JSON.parse(await err.response.data.text()).detail || detalle; } catch (_) {}
      setError(detalle);
    } finally {
      setGenerando(false);
    }
  };

  const toggleLinea = (linea) =>
    setLineasSel((prev) =>
      prev.includes(linea) ? prev.filter((l) => l !== linea) : [...prev, linea]);

  const publicacionesElegidas = analisis
    ? (lineasSel.length
        ? analisis.por_linea.filter((l) => lineasSel.includes(l.linea))
        : analisis.por_linea
      ).reduce((s, l) => s + (l.publicaciones_faltantes ?? l.piezas), 0)
    : 0;

  return (
    <div>
      <PageHeader
        title="Publicaciones masivas"
        subtitle="Convierte el catálogo de un proveedor en la plantilla lista para subir a Mercado Libre"
      />

      {error && (
        <div className="mb-4 p-3 bg-red-50 text-danger rounded-lg text-sm flex gap-2">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" /> {error}
        </div>
      )}

      {/* PASO 1 — cargar */}
      <div className="bg-white rounded-xl border border-notion-border p-5 mb-4">
        <div className="flex items-center gap-2 mb-4">
          <span className="w-6 h-6 rounded-full bg-reluvsa-black text-reluvsa-yellow text-xs font-bold flex items-center justify-center">1</span>
          <h3 className="font-semibold">Elige el proveedor y carga su catálogo</h3>
        </div>

        <div className="grid md:grid-cols-3 gap-4">
          <Campo label="Proveedor" hint="Cada uno manda su información distinta">
            <select value={proveedor} onChange={(e) => setProveedor(e.target.value)} className={inputCls}>
              {soportados.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </Campo>

          <Campo label="Catálogo del proveedor" hint="El Excel con sus claves y precios">
            <input type="file" accept=".xlsx" className="w-full text-sm mt-2"
                   onChange={(e) => setCatalogo(e.target.files?.[0] || null)} />
          </Campo>

          <Campo label="Publicaciones ML (opcional)" hint="Para saber qué ya está publicado">
            <input type="file" accept=".xlsx" className="w-full text-sm mt-2"
                   onChange={(e) => setPublicacionesML(e.target.files?.[0] || null)} />
          </Campo>
        </div>

        <button onClick={analizar} disabled={!catalogo || cargando}
                className="mt-4 px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50 flex items-center gap-2">
          {cargando ? <Loader2 size={16} className="animate-spin" /> : <FileSpreadsheet size={16} />}
          {cargando ? 'Analizando…' : 'Analizar catálogo'}
        </button>
      </div>

      {/* PASO 2 — el cruce */}
      {analisis && (
        <div className="bg-white rounded-xl border border-notion-border p-5 mb-4">
          <div className="flex items-center gap-2 mb-4">
            <span className="w-6 h-6 rounded-full bg-reluvsa-black text-reluvsa-yellow text-xs font-bold flex items-center justify-center">2</span>
            <h3 className="font-semibold">Qué falta publicar</h3>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            {[
              ['Filas del master', analisis.filas_master ?? analisis.total_catalogo, ''],
              ['SKU únicos', analisis.sku_unicos ?? analisis.total_catalogo, ''],
              ['Compatibilidades válidas', analisis.compatibilidades_validas ?? analisis.total_catalogo, 'text-success'],
              ['Compatibilidades inválidas', analisis.compatibilidades_invalidas ?? 0, 'text-reluvsa-red'],
              ['Variantes estimadas', analisis.variantes_estimadas ?? analisis.publicaciones_estimadas, ''],
              ['Variantes existentes', analisis.variantes_existentes ?? analisis.ya_publicadas, 'text-success'],
              ['Variantes faltantes', analisis.variantes_faltantes ?? analisis.faltantes, 'text-reluvsa-red font-bold'],
              ['Duplicados descartados', (analisis.duplicados_descartados ?? 0) + (analisis.variantes_deduplicadas ?? 0), ''],
            ].map(([label, valor, cls]) => (
              <div key={label} className="bg-notion-bg rounded-lg p-3">
                <p className="text-xs text-notion-text-secondary">{label}</p>
                <p className={`text-2xl font-semibold ${cls}`}>{valor.toLocaleString('es-MX')}</p>
              </div>
            ))}
          </div>

          {!analisis.cruce_realizado && (
            <div className="mb-3 p-3 bg-amber-50 text-amber-800 rounded-lg text-xs">
              No cargaste el reporte de Publicaciones de ML, así que <strong>no se pudo cruzar</strong>:
              se está contando todo el catálogo como faltante.
            </div>
          )}

          {analisis.precio_presente === false && (
            <div className="mb-3 p-3 bg-amber-50 text-amber-800 rounded-lg text-xs flex gap-2">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>El master no trae la columna <strong>Precio</strong>. La plantilla sí se puede
                generar, pero las celdas de precio quedarán vacías para revisión manual.</span>
            </div>
          )}

          {analisis.aplicaciones_truncadas > 0 && (
            <div className="mb-3 p-3 bg-amber-50 text-amber-800 rounded-lg text-xs flex gap-2">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>
                <strong>{analisis.aplicaciones_truncadas} aplicaciones vienen cortadas</strong> en el
                catálogo del proveedor (su archivo recorta la celda). Se excluyen para no publicar
                piezas diciendo que sirven para menos autos de los que sirven. Pídele el catálogo completo.
              </span>
            </div>
          )}

          <p className="text-sm font-medium mb-2">
            Filtra por línea para trabajar por tandas
            <span className="text-notion-text-secondary font-normal"> — ninguna seleccionada = todas</span>
          </p>
          <div className="flex flex-wrap gap-2 max-h-52 overflow-y-auto">
            {analisis.por_linea.map(({ linea, piezas, compatibilidades, publicaciones_faltantes }) => (
              <button key={linea} onClick={() => toggleLinea(linea)}
                      className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                        lineasSel.includes(linea)
                          ? 'bg-reluvsa-black text-reluvsa-yellow border-reluvsa-black'
                          : 'bg-white border-notion-border hover:bg-notion-bg'}`}>
                {lineasSel.includes(linea) && <Check size={12} className="inline mr-1" />}
                {linea} <span className="opacity-60">({piezas} SKU · {compatibilidades ?? piezas} compat. · {publicaciones_faltantes ?? piezas} pub.)</span>
              </button>
            ))}
          </div>

          {(analisis.errores_total > 0 || analisis.variantes_excluidas > 0) && (
            <div className="mt-5 border border-red-200 rounded-lg overflow-hidden">
              <div className="px-3 py-2 bg-red-50 text-red-800 text-sm font-semibold">
                Registros excluidos ({analisis.errores_total?.toLocaleString('es-MX')})
              </div>
              <div className="max-h-64 overflow-auto">
                {(analisis.errores || []).map((e, i) => (
                  <div key={`${e.fila}-${i}`} className="px-3 py-2 border-t border-red-100 text-xs grid md:grid-cols-[70px_140px_1fr] gap-2">
                    <span>Fila {e.fila ?? '—'}</span><span className="font-mono">{e.clave || 'Sin clave'}</span>
                    <span>{[e.armadora, e.modelo, e.anio].filter(Boolean).join(' · ')} — <strong>{e.motivo}</strong></span>
                  </div>
                ))}
              </div>
              {analisis.errores_total > (analisis.errores || []).length && (
                <p className="px-3 py-2 text-xs text-notion-text-secondary">Se muestran los primeros {(analisis.errores || []).length}; el total completo se conserva en la métrica.</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* PASO 3 — generar */}
      {analisis && (
        <div className="bg-white rounded-xl border border-notion-border p-5">
          <div className="flex items-center gap-2 mb-4">
            <span className="w-6 h-6 rounded-full bg-reluvsa-black text-reluvsa-yellow text-xs font-bold flex items-center justify-center">3</span>
            <h3 className="font-semibold">Genera la plantilla</h3>
          </div>

          <div className="grid md:grid-cols-3 gap-4 mb-4">
            <Campo label="Marca" hint="Va en la columna Marca de ML">
              <input className={inputCls} value={config.marca}
                     onChange={(e) => setConfig({ ...config, marca: e.target.value })}
                     placeholder="KeepOnGreen" />
            </Campo>
            <Campo label="Categoría ML" hint="Ej. MLM163963">
              <input className={inputCls} value={config.categoria_ml}
                     onChange={(e) => setConfig({ ...config, categoria_ml: e.target.value })} />
            </Campo>
            <Campo label="Cantidad (stock)">
              <input type="number" className={inputCls} value={config.cantidad}
                     onChange={(e) => setConfig({ ...config, cantidad: +e.target.value })} />
            </Campo>
          </div>

          <p className="text-sm font-medium mb-1">Cómo se calcula el precio</p>
          <p className="text-xs text-notion-text-secondary mb-2">
            costo × (1 + IVA) × (1 + utilidad), más el envío, dividido entre (1 − comisión).
            La comisión se <strong>divide</strong> porque ML la cobra sobre el precio final.
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-2">
            {[['IVA', 'iva'], ['Utilidad', 'utilidad'], ['Comisión ML', 'comision_ml']].map(([label, key]) => (
              <Campo key={key} label={label}>
                <input type="number" step="0.01" className={inputCls} value={config[key]}
                       onChange={(e) => setConfig({ ...config, [key]: +e.target.value })} />
              </Campo>
            ))}
            <Campo label="Envío" hint="⬜ pendiente por línea">
              <input type="number" step="1" className={inputCls} value={config.envio}
                     onChange={(e) => setConfig({ ...config, envio: +e.target.value })} />
            </Campo>
          </div>

          {envioPendiente && config.envio === 0 && (
            <div className="mb-4 p-3 bg-amber-50 text-amber-800 rounded-lg text-xs">
              El <strong>costo de envío todavía no está cargado</strong>, así que el precio sale sin él
              y queda por debajo del real. Puedes poner un monto aquí mientras tanto; lo definitivo es
              una tabla por línea de producto (radiadores, tomas de agua…).
            </div>
          )}

          <Campo label="Descripción base"
                 hint="El texto fijo que va al final de todas. Arriba se agregan solas la pieza, el OEM y las compatibilidades.">
            <textarea rows={7} className={`${inputCls} font-mono text-xs`}
                      value={config.descripcion_base}
                      onChange={(e) => setConfig({ ...config, descripcion_base: e.target.value })} />
          </Campo>

          <div className="mt-4 p-3 bg-notion-bg rounded-lg text-xs text-notion-text-secondary">
            <strong>Imagen 1–5 se completan automáticamente</strong> desde el nuevo master KG.
            Imagen 6–10 permanecen vacías. En el catálogo legado se conserva el comportamiento anterior.
          </div>

          <button onClick={generar} disabled={generando}
                  className="mt-4 px-4 py-2 bg-reluvsa-yellow text-reluvsa-black rounded-lg text-sm font-bold hover:brightness-95 disabled:opacity-50 flex items-center gap-2">
            {generando ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
            {generando ? 'Generando…' : `Descargar plantilla (${publicacionesElegidas.toLocaleString('es-MX')} publicaciones)`}
          </button>
        </div>
      )}
    </div>
  );
}
