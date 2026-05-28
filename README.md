# Portal Dropshipping RELUVSA

Plataforma de conciliación entre ventas de Mercado Libre, envíos de colecta y facturas de proveedores en modelo dropshipping.

## Estructura

```
dropshipping-reluvsa/
├── backend/      # FastAPI + SQLite
├── frontend/     # React + Tailwind
├── data/         # SQLite local (no commiteado)
└── archivos/     # Excels y PDFs de muestra
```

## Setup local

### Backend
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 database.py                  # crea schema y siembra proveedores
python3 scripts/crear_usuario.py admin gaby@reluvsa.com "Cambiar123!"
python3 scripts/crear_usuario.py proveedor VAZLO contacto@vazlo.com "PassVazlo!"
uvicorn main:app --reload --port 8000
```

API en http://localhost:8000 · docs en http://localhost:8000/docs

### Frontend
```bash
cd frontend
npm install
npm start
```

App en http://localhost:3000

## Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `DATABASE_PATH` | `../data/dropshipping.db` | SQLite |
| `JWT_SECRET_KEY` | random temporal | firma JWT |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | CSV de orígenes |
| `REACT_APP_API_URL` | `http://localhost:8000/api` | base URL del frontend |

## Roles

- **admin** — Gaby/RELUVSA. Sube reportes ML, conciliación, incidencias, métricas.
- **proveedor** — un usuario por proveedor (CAUPLAS, KIM, AG, VAZLO, KG). Solo ve sus pedidos y sube sus facturas.

## Deploy

- Backend: Railway (Procfile + railway.json).
- Frontend: Vercel (vercel.json).
