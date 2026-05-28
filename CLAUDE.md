# CLAUDE.md — Portal Dropshipping RELUVSA

## Contexto del negocio

RELUVSA vende refacciones en Mercado Libre operando con 5 proveedores en modelo dropshipping. El portal concilia 3 flujos: **Venta ML → Envío de colecta → Factura del proveedor**.

Cliente operativo: **Gaby (RELUVSA)**.
Receptor fiscal de facturas: **GRUPO PEMIT — RFC GPE230915JWA** (misma entidad legal que RELUVSA).

## Proveedores

| RFC | Razón social | Código bodega |
|---|---|---|
| QHO180116NW0 | QUALITY HOSES | CAUPLAS |
| KAC1601193F6 | KIMS AUTO CORPORATION | KIM |
| ARG041025AU2 | ARGENPARTS | AG |
| VIM990605M8A | VAZLO COMERCIAL | VAZLO |
| PENDIENTE | KEEPONGREEN | KG |

`MATRIZ` es bodega propia, **no** es proveedor dropshipping.

## Reglas de Gaby (críticas)

- **Detalle de colecta**: la columna importante para asignar al proveedor es **K (Lugar real)**. Cuando trae "Sin información del lugar" → permitir override manual de Gaby. Ese override persiste y manda sobre el lugar real al recargar el reporte.
- **Ventas ML**: la columna relevante es **T = SKU**. Cruce con colecta por **# de venta**, no por SKU.
- **Facturas**: cada proveedor sube su XML+PDF; el match se hace por código del proveedor (NoIdentificacion del concepto) contra SKU de la venta. Fallback: fuzzy match por descripción contra título de la venta.
- **PENDIENTES ACDELCO** y **LISTA PRECIOS KG**: NO son para el motor de cruces. Son entradas del **módulo 2 (publicaciones masivas)** — pendiente de implementar.
- **Publicaciones ML**: la columna Q (`Att_SellerSKU`) cruza contra SKUs de catálogo de proveedor para detectar qué falta publicar.

## Stack

- **Frontend**: React 18 + Tailwind 3 + Lucide + react-router. Paleta RELUVSA (amarillo `#FFED00`, negro `#1a1a1a`, rojo `#E31E24`) + Notion grays. Font: Plus Jakarta Sans.
- **Backend**: FastAPI + SQLite + python-jose JWT + bcrypt + openpyxl + lxml + rapidfuzz.
- **Deploy**: Railway (backend) + Vercel (frontend).

## Modelo de datos

Ver `backend/database.py` para el SQL canónico. Tablas:
- `proveedores`, `usuarios`
- `ventas_ml`, `envios_colecta`
- `facturas`, `factura_conceptos`
- `incidencias`
- `catalogos_proveedor`, `catalogo_items` (módulo 2)
- `publicaciones_ml`, `plantillas_ml` (módulo 2)

## Estado actual

- ✅ Esqueleto del repo (backend + frontend) con todas las pantallas placeholder funcionales.
- ✅ Módulo 1 (conciliación): backend completo de uploads, parsers, matcher, métricas. Frontend con tabla de ventas, facturas, incidencias.
- ⏳ Módulo 2 (publicaciones masivas): pendiente.
- ⏳ Polish UI + datos reales + deploy.

## Convenciones

- Las contraseñas se guardan con bcrypt.
- Las fechas en SQLite se guardan en ISO 8601 (TEXT).
- Códigos de bodega son la clave canónica para resolver proveedor desde un Excel de colecta.
- Ningún archivo subido se guarda en git: van a `backend/uploads/`.
