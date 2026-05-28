import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';

import { AuthProvider, useAuth } from './context/AuthContext';
import Login from './components/Login';
import Sidebar from './components/Sidebar';
import Dashboard from './pages/Dashboard';
import Ventas from './pages/Ventas';
import Facturas from './pages/Facturas';
import Incidencias from './pages/Incidencias';
import Metricas from './pages/Metricas';
import Uploads from './pages/Uploads';
import Proveedores from './pages/Proveedores';

function AdminOnly({ children }) {
  const { isAdmin } = useAuth();
  return isAdmin() ? children : <Navigate to="/" replace />;
}

function Shell() {
  const { user, loading, isAdmin } = useAuth();

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center bg-notion-bg-subtle">Cargando...</div>;
  }
  if (!user) return <Login />;

  return (
    <BrowserRouter>
      <div className="min-h-screen flex bg-notion-bg-subtle">
        <Sidebar />
        <main className="flex-1 p-6 lg:p-8 overflow-y-auto">
          <Routes>
            <Route path="/" element={isAdmin() ? <Dashboard /> : <Ventas />} />
            <Route path="/ventas" element={<Ventas />} />
            <Route path="/facturas" element={<Facturas />} />
            <Route path="/incidencias" element={<Incidencias />} />
            <Route path="/metricas" element={<Metricas />} />
            <Route path="/uploads" element={<AdminOnly><Uploads /></AdminOnly>} />
            <Route path="/proveedores" element={<AdminOnly><Proveedores /></AdminOnly>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
