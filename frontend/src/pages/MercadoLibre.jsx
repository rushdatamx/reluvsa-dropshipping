import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, CheckCircle2, Clock, Copy, Link2, RefreshCw, Store, XCircle, Zap,
} from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { mlEstado, mlIniciarOauth, mlNotificaciones, mlSync, mlSyncAuto } from '../services/api';

const INTERVALOS = [15, 30, 60, 120, 240];

const fechaCorta = (iso) => (iso ? String(iso).replace('T', ' ').slice(0, 16) : null);

const POLL_MS = 4000;

function Badge({ ok, children }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
      ok ? 'bg-green-50 text-success' : 'bg-gray-100 text-notion-text-secondary'
    }`}>
      {children}
    </span>
  );
}

export default function MercadoLibre() {
  const [estado, setEstado] = useState(null);
  const [notifs, setNotifs] = useState([]);
  const [authUrl, setAuthUrl] = useState(null);
  const [aviso, setAviso] = useState(null);
  const [error, setError] = useState(null);
  const [copiado, setCopiado] = useState(false);
  const pollRef = useRef(null);

  const cargar = useCallback(async () => {
    try {
      const [{ data: est }, { data: nots }] = await Promise.all([mlEstado(), mlNotificaciones(15)]);
      setEstado(est);
      setNotifs(nots.notificaciones || []);
      return est;
    } catch {
      return null;
    }
  }, []);

  // Carga inicial + refresco periódico (muestra el avance de la sync en vivo).
  useEffect(() => {
    cargar();
    pollRef.current = setInterval(cargar, POLL_MS);
    return () => clearInterval(pollRef.current);
  }, [cargar]);

  const conectar = async () => {
    setError(null);
    try {
      const { data } = await mlIniciarOauth();
      setAuthUrl(data.authorization_url);
      setAviso(data.aviso);
      window.open(data.authorization_url, '_blank', 'noopener');
    } catch (err) {
      setError(err.response?.data?.detail || 'No se pudo iniciar la autorización');
    }
  };

  const copiarUrl = async () => {
    try {
      await navigator.clipboard.writeText(authUrl);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 2000);
    } catch { /* clipboard no disponible */ }
  };

  const sincronizar = async (tipo) => {
    setError(null);
    if (tipo === 'backfill' && !window.confirm(
      'El backfill trae los últimos 12 meses de órdenes desde Mercado Libre y puede tardar. ¿Continuar?'
    )) return;
    try {
      await mlSync(tipo);
      cargar();
    } catch (err) {
      const d = err.response?.data?.detail;
      setError(typeof d === 'string' ? d : d?.mensaje || 'No se pudo iniciar la sincronización');
    }
  };

  const guardarSyncAuto = async (payload) => {
    setError(null);
    try {
      await mlSyncAuto(payload);
      cargar();
    } catch (err) {
      const d = err.response?.data?.detail;
      setError(typeof d === 'string' ? d : 'No se pudo guardar la configuración de la sync automática');
    }
  };

  const auto = estado?.sync_auto;
  const run = estado?.sync_en_curso;
  const ultima = estado?.ultima_run;
  const resumenUltima = (() => {
    if (!ultima?.detalle) return null;
    try { return JSON.parse(ultima.detalle); } catch { return null; }
  })();

  return (
    <div>
      <PageHeader
        title="Mercado Libre"
        subtitle="Conexión con la API oficial (solo lectura) y sincronización de ventas y envíos"
      />

      {error && (
        <div className="mb-4 p-3 rounded-lg bg-red-50 text-danger text-sm flex items-center gap-2">
          <XCircle size={16} /> {String(error)}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        {/* ─── Tarjeta: conexión ─── */}
        <div className="bg-white rounded-xl border border-notion-border p-5">
          <div className="flex items-center gap-2 mb-3">
            <Link2 size={20} className="text-reluvsa-black" />
            <h3 className="font-semibold">Conexión con la cuenta ML</h3>
          </div>

          {!estado ? (
            <p className="text-sm text-notion-text-secondary">Cargando…</p>
          ) : !estado.configurado ? (
            <div className="p-3 rounded-lg bg-amber-50 text-amber-800 text-sm flex items-start gap-2">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              <span>
                Faltan las credenciales de la app (<code>ML_CLIENT_ID</code> / <code>ML_CLIENT_SECRET</code>)
                en el servidor. Hay que cargarlas en Railway antes de conectar.
              </span>
            </div>
          ) : estado.conectado ? (
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <CheckCircle2 size={18} className="text-success" />
                <span className="font-semibold">
                  {estado.nickname || 'Cuenta conectada'}
                </span>
                {estado.seller_id && (
                  <span className="text-notion-text-secondary">· seller {estado.seller_id}</span>
                )}
              </div>
              {estado.token_expira_en_min != null && estado.token_expira_en_min > 0 && (
                <p className="text-notion-text-secondary">
                  Token vigente (se renueva solo; expira en ~{estado.token_expira_en_min} min).
                </p>
              )}
              <div className="flex flex-wrap gap-2 pt-1">
                <Badge ok={estado.multiorigen?.warehouse_management}>multi-origen</Badge>
                {(estado.stores || []).map((s) => (
                  <Badge key={s.store_id} ok={!!s.codigo_bodega}>
                    <Store size={12} /> {s.description || s.store_id}
                    {s.codigo_bodega ? ` → ${s.codigo_bodega}` : ''}
                  </Badge>
                ))}
              </div>
              {estado.requiere_reautorizacion && (
                <div className="p-3 rounded-lg bg-amber-50 text-amber-800 flex items-start gap-2">
                  <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                  <span>La autorización venció: hay que volver a conectar con el titular.</span>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-notion-text-secondary">
                Aún no hay una cuenta de Mercado Libre conectada.
              </p>
              <button
                onClick={conectar}
                className="px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg text-sm font-semibold hover:bg-gray-800"
              >
                Conectar cuenta de Mercado Libre
              </button>
            </div>
          )}

          {authUrl && !estado?.conectado && (
            <div className="mt-4 p-3 rounded-lg bg-amber-50 text-sm space-y-2">
              <p className="font-semibold text-amber-900 flex items-center gap-2">
                <AlertTriangle size={16} /> {aviso}
              </p>
              <p className="text-amber-800">
                Si el titular está en otra computadora, cópiale este enlace:
              </p>
              <div className="flex gap-2">
                <input
                  readOnly
                  value={authUrl}
                  className="flex-1 text-xs px-2 py-1.5 rounded border border-amber-200 bg-white truncate"
                  onFocus={(e) => e.target.select()}
                />
                <button
                  onClick={copiarUrl}
                  className="px-3 py-1.5 rounded bg-reluvsa-black text-reluvsa-yellow text-xs font-semibold flex items-center gap-1"
                >
                  <Copy size={12} /> {copiado ? 'Copiado' : 'Copiar'}
                </button>
              </div>
            </div>
          )}
        </div>

        {/* ─── Tarjeta: sincronización ─── */}
        <div className="bg-white rounded-xl border border-notion-border p-5">
          <div className="flex items-center gap-2 mb-3">
            <RefreshCw size={20} className="text-reluvsa-black" />
            <h3 className="font-semibold">Sincronización</h3>
          </div>

          {run ? (
            <div className="space-y-2 text-sm">
              <p className="flex items-center gap-2 font-semibold">
                <RefreshCw size={16} className="animate-spin text-reluvsa-black" />
                Sincronización {run.tipo} en curso…
              </p>
              <div className="grid grid-cols-2 gap-2 text-notion-text-secondary">
                <span>Órdenes vistas: <b className="text-notion-text-primary">{run.ordenes_vistas}</b></span>
                <span>Ventas: <b className="text-notion-text-primary">{run.ventas_upsert}</b></span>
                <span>Envíos: <b className="text-notion-text-primary">{run.envios_upsert}</b></span>
                <span>Errores: <b className={run.errores ? 'text-danger' : 'text-notion-text-primary'}>{run.errores}</b></span>
              </div>
              {run.cursor_fecha && (
                <p className="text-xs text-notion-text-secondary">
                  Avance histórico: hasta {String(run.cursor_fecha).slice(0, 10)}
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-notion-text-secondary">
                {estado?.ultima_sync
                  ? `Última sincronización: ${String(estado.ultima_sync).replace('T', ' ').slice(0, 16)}`
                  : 'Todavía no se ha sincronizado nada.'}
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => sincronizar('incremental')}
                  disabled={!estado?.conectado}
                  className="px-4 py-2 bg-reluvsa-black text-reluvsa-yellow rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50 flex items-center gap-2"
                >
                  <Zap size={16} /> Sincronizar ahora
                </button>
                <button
                  onClick={() => sincronizar('backfill')}
                  disabled={!estado?.conectado}
                  className="px-4 py-2 border border-notion-border rounded-lg text-sm font-semibold hover:bg-gray-50 disabled:opacity-50"
                >
                  Backfill 12 meses
                </button>
              </div>
              {ultima && (
                <div className={`p-3 rounded-lg text-xs ${
                  ultima.estado === 'completado' ? 'bg-green-50 text-success' : 'bg-red-50 text-danger'
                }`}>
                  <p className="font-semibold mb-1">
                    Última corrida ({ultima.tipo}): {ultima.estado}
                    {ultima.terminado_en ? ` · ${String(ultima.terminado_en).replace('T', ' ')}` : ''}
                  </p>
                  {resumenUltima ? (
                    <p>
                      {resumenUltima.ordenes_vistas} órdenes · {resumenUltima.ventas_upsert} ventas ·{' '}
                      {resumenUltima.envios_upsert} envíos · {resumenUltima.errores} errores
                    </p>
                  ) : (
                    ultima.detalle && <p className="truncate">{ultima.detalle}</p>
                  )}
                </div>
              )}
            </div>
          )}

          {/* ─── Sync automática ─── */}
          {auto && (
            <div className="mt-4 pt-4 border-t border-notion-border space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-sm">
                  <Clock size={16} className={auto.activo ? 'text-success' : 'text-notion-text-secondary'} />
                  <span className="font-semibold">Sincronización automática</span>
                  <Badge ok={auto.activo}>{auto.activo ? 'activa' : 'apagada'}</Badge>
                </div>
                <button
                  onClick={() => guardarSyncAuto({ activo: !auto.activo })}
                  className="px-3 py-1 rounded-lg border border-notion-border text-xs font-semibold hover:bg-gray-50"
                >
                  {auto.activo ? 'Apagar' : 'Encender'}
                </button>
              </div>

              <div className="flex items-center gap-2 text-sm">
                <span className="text-notion-text-secondary">Cada</span>
                <select
                  value={auto.intervalo_minutos}
                  onChange={(e) => guardarSyncAuto({ intervalo_minutos: Number(e.target.value) })}
                  className="px-2 py-1 rounded border border-notion-border text-sm bg-white"
                >
                  {INTERVALOS.map((m) => (
                    <option key={m} value={m}>
                      {m < 60 ? `${m} min` : `${m / 60} h`}
                    </option>
                  ))}
                </select>
              </div>

              {auto.activo && auto.proxima_aproximada && (
                <p className="text-xs text-notion-text-secondary">
                  Próxima corrida aproximada: {fechaCorta(auto.proxima_aproximada)}
                </p>
              )}

              {!auto.scheduler_vivo && (
                <p className="text-xs text-danger flex items-center gap-1">
                  <AlertTriangle size={12} /> El proceso de fondo no está corriendo en el servidor.
                </p>
              )}

              {auto.activo && !auto.ultimo_intento && (
                <p className="text-xs text-amber-700 flex items-start gap-1">
                  <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                  <span>
                    La sync automática arranca después de la primera sincronización manual
                    (así el backfill de 12 meses nunca se dispara solo).
                  </span>
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ─── Notificaciones webhook ─── */}
      <div className="bg-white rounded-xl border border-notion-border p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold">Últimas notificaciones de ML (webhooks)</h3>
          {estado?.notificaciones_pendientes > 0 && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-800 font-medium">
              {estado.notificaciones_pendientes} pendientes de procesar
            </span>
          )}
        </div>
        {notifs.length === 0 ? (
          <p className="text-sm text-notion-text-secondary">
            Todavía no ha llegado ninguna notificación.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-notion-text-secondary border-b border-notion-border">
                  <th className="py-2 pr-4">Tópico</th>
                  <th className="py-2 pr-4">Recurso</th>
                  <th className="py-2 pr-4">Recibida</th>
                  <th className="py-2">Procesada</th>
                </tr>
              </thead>
              <tbody>
                {notifs.map((n) => (
                  <tr key={n.id} className="border-b border-notion-border last:border-0">
                    <td className="py-2 pr-4 font-medium">{n.topic || '—'}</td>
                    <td className="py-2 pr-4 font-mono text-xs">{n.resource || '—'}</td>
                    <td className="py-2 pr-4 text-notion-text-secondary">
                      {String(n.recibido_en || '').replace('T', ' ')}
                    </td>
                    <td className="py-2">
                      {n.procesada ? (
                        <CheckCircle2 size={16} className="text-success" />
                      ) : (
                        <span className="text-xs text-notion-text-secondary">pendiente</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
