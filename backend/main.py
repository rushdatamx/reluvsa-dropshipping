"""
API FastAPI del Portal Dropshipping RELUVSA.
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_database
from routers import admin, auth, envios, facturas, incidencias, metricas, proveedores, uploads, ventas, webhooks

app = FastAPI(
    title="Portal Dropshipping RELUVSA",
    description="Conciliación ventas Mercado Libre ↔ envíos colecta ↔ facturas proveedor",
    version="0.1.0",
)

cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:5173",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# Inicializa BD al arrancar (idempotente)
init_database()

# Routers
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(proveedores.router)
app.include_router(ventas.router)
app.include_router(envios.router)
app.include_router(facturas.router)
app.include_router(incidencias.router)
app.include_router(metricas.router)
app.include_router(uploads.router)
app.include_router(webhooks.router)


@app.get("/")
def root():
    return {
        "service": "Portal Dropshipping RELUVSA",
        "version": "0.1.0",
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
