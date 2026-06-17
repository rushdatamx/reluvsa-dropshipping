import axios from 'axios';

export const API_BASE = process.env.REACT_APP_API_URL || 'http://localhost:8000/api';

const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      window.location.reload();
    }
    return Promise.reject(err);
  }
);

// Auth
export const login = (email, password) => api.post('/auth/login', { email, password });

// Resumen + métricas
export const getResumen = () => api.get('/metricas/resumen');
export const getMetricasProveedores = (params = {}) => api.get('/metricas/proveedores', { params });

// Proveedores
export const listarProveedores = () => api.get('/proveedores');

// Ventas
export const listarVentas = (params = {}) => api.get('/ventas', { params });
export const getVenta = (numVenta) => api.get(`/ventas/${encodeURIComponent(numVenta)}`);
export const exportarVentasCsv = (params = {}) =>
  api.get('/ventas/export.csv', { params, responseType: 'blob' });

// Envíos
export const reasignarEnvio = (numEnvio, payload) =>
  api.patch(`/envios/${encodeURIComponent(numEnvio)}/reasignar`, payload);

// Facturas
export const listarFacturas = (params = {}) => api.get('/facturas', { params });
export const subirFactura = (xmlFile, pdfFile) => {
  const fd = new FormData();
  fd.append('xml', xmlFile);
  if (pdfFile) fd.append('pdf', pdfFile);
  return api.post('/facturas/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
};

// Incidencias
export const listarIncidencias = (params = {}) => api.get('/incidencias', { params });
export const crearIncidencia = (payload) => api.post('/incidencias', payload);
export const resolverIncidencia = (id) => api.patch(`/incidencias/${id}/resolver`);

// Uploads (admin)
export const subirVentasML = (file) => {
  const fd = new FormData();
  fd.append('file', file);
  return api.post('/uploads/ventas-ml', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  });
};
export const subirColecta = (file) => {
  const fd = new FormData();
  fd.append('file', file);
  return api.post('/uploads/colecta', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  });
};
export const subirAlbaranes = (file) => {
  const fd = new FormData();
  fd.append('file', file);
  return api.post('/uploads/albaran', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  });
};

export default api;
