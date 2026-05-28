import React from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, ShoppingCart, FileText, AlertCircle, BarChart3, Upload, Users, LogOut } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { cn } from '../lib/utils';

const NAV_ADMIN = [
  { to: '/', label: 'Resumen', icon: LayoutDashboard, exact: true },
  { to: '/ventas', label: 'Ventas y cruces', icon: ShoppingCart },
  { to: '/facturas', label: 'Facturas', icon: FileText },
  { to: '/incidencias', label: 'Incidencias', icon: AlertCircle },
  { to: '/metricas', label: 'Métricas proveedores', icon: BarChart3 },
  { to: '/uploads', label: 'Cargar reportes', icon: Upload },
  { to: '/proveedores', label: 'Proveedores', icon: Users },
];

const NAV_PROVEEDOR = [
  { to: '/', label: 'Mis pedidos', icon: ShoppingCart, exact: true },
  { to: '/facturas', label: 'Mis facturas', icon: FileText },
  { to: '/incidencias', label: 'Mis incidencias', icon: AlertCircle },
  { to: '/metricas', label: 'Mi desempeño', icon: BarChart3 },
];

export default function Sidebar() {
  const { user, isAdmin, logout } = useAuth();
  const items = isAdmin() ? NAV_ADMIN : NAV_PROVEEDOR;

  return (
    <aside className="w-64 bg-reluvsa-black text-white flex flex-col">
      <div className="p-5 border-b border-gray-800">
        <div className="inline-block bg-reluvsa-yellow text-reluvsa-black font-bold text-lg px-3 py-1 rounded">
          RELUVSA
        </div>
        <p className="text-xs text-gray-400 mt-2">Portal Dropshipping</p>
      </div>

      <nav className="flex-1 p-3 space-y-1">
        {items.map(({ to, label, icon: Icon, exact }) => (
          <NavLink
            key={to}
            to={to}
            end={exact}
            className={({ isActive }) => cn(
              'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
              isActive
                ? 'bg-reluvsa-yellow text-reluvsa-black'
                : 'text-gray-300 hover:bg-gray-800 hover:text-white'
            )}
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="p-3 border-t border-gray-800">
        <div className="px-3 py-2 mb-2">
          <p className="text-xs text-gray-400">Sesión</p>
          <p className="text-sm font-medium truncate">{user?.email}</p>
          {user?.proveedor_nombre && (
            <p className="text-xs text-reluvsa-yellow font-medium">{user.proveedor_nombre}</p>
          )}
        </div>
        <button
          onClick={logout}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors"
        >
          <LogOut size={18} />
          Cerrar sesión
        </button>
      </div>
    </aside>
  );
}
