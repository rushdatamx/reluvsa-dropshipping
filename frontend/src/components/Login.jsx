import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { login as apiLogin } from '../services/api';

export default function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const { data } = await apiLogin(email, password);
      login(data.token, {
        email: data.email,
        rol: data.rol,
        proveedor_id: data.proveedor_id,
        proveedor_nombre: data.proveedor_nombre,
      });
    } catch (err) {
      setError(err.response?.data?.detail || 'Credenciales inválidas');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-reluvsa-yellow">
      <div className="bg-white p-8 rounded-xl shadow-xl w-full max-w-md border-2 border-reluvsa-black">
        <div className="text-center mb-8">
          <div className="inline-block bg-reluvsa-black text-reluvsa-yellow font-bold text-2xl px-6 py-3 rounded-lg mb-3">
            RELUVSA
          </div>
          <p className="text-reluvsa-black font-medium">Portal de Dropshipping</p>
          <p className="text-notion-text-secondary text-sm mt-1">
            Conciliación ventas · envíos · facturas
          </p>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="mb-4">
            <label className="block text-sm font-semibold text-reluvsa-black mb-2">
              Correo
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-4 py-3 border-2 border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-reluvsa-yellow focus:border-reluvsa-black transition-all"
              placeholder="tu@correo.com"
              required
            />
          </div>

          <div className="mb-6">
            <label className="block text-sm font-semibold text-reluvsa-black mb-2">
              Contraseña
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-4 py-3 border-2 border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-reluvsa-yellow focus:border-reluvsa-black transition-all"
              placeholder="••••••••"
              required
            />
          </div>

          {error && (
            <div className="mb-4 p-3 bg-red-50 border border-red-300 rounded-lg">
              <p className="text-red-600 text-sm text-center font-medium">{error}</p>
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-reluvsa-black text-reluvsa-yellow py-3 rounded-lg font-bold text-lg hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? 'Ingresando...' : 'Ingresar'}
          </button>
        </form>

        <div className="flex items-center justify-center gap-2 mt-8 pt-6 border-t border-gray-200">
          <p className="text-gray-400 text-xs">
            Desarrollado por Rushdata
          </p>
        </div>
      </div>
    </div>
  );
}
