# CLAUDE.md — Portal Dropshipping RELUVSA

> Este archivo es el contexto canónico para cualquier sesión de Claude que retome el proyecto. Léelo antes de tocar código.

---

## 1. Contexto del negocio

**Cliente**: RELUVSA — refaccionaria que vende en Mercado Libre operando con 5 proveedores en modelo **dropshipping** (el proveedor manda directo al comprador final).

**Contacto operativo**: Gaby (RELUVSA) — Mario (mario@rushdata.com.mx) actúa como Project Manager intermediario.

**Receptor fiscal de facturas**: GRUPO PEMIT — RFC `GPE230915JWA`. Es la misma entidad legal que RELUVSA (RELUVSA es nombre comercial).

**Problema que resuelve el portal**: los proveedores no se ajustan bien al proceso de dropshipping — facturas tardías, productos equivocados, stock desactualizado, sin SLA medible. El portal concilia 3 flujos para medir desempeño y exigir corrección:

```
Venta Mercado Libre  →  Envío de colecta  →  Factura del proveedor
```

**Objetivo de Gaby**: bajar las incidencias y poder decirle a cada proveedor con datos exactos "esto es lo que tienes que mejorar".

---

## 2. Los 5 proveedores

| RFC | Razón social | Código bodega | Esquema SKU típico |
|---|---|---|---|
| `QHO180116NW0` | QUALITY HOSES | `CAUPLAS` | ID interno + `M2622638` |
| `KAC1601193F6` | KIMS AUTO CORPORATION | `KIM` | `9030175-Z`, `39300-4A800-Z` |
| `ARG041025AU2` | ARGENPARTS | `AG` | numérico `4905967` |
| `VIM990605M8A` | VAZLO COMERCIAL | `VAZLO` | `10-293`, `10-750` |
| pendiente | KEEPONGREEN | `KG` | `KGP-XXXX` |

⚠️ `MATRIZ` aparece en stock pero es bodega propia de RELUVSA, **NO** es proveedor dropshipping.

⚠️ Cada proveedor usa su propio esquema de códigos. **No hay código universal cruzable con MercadoLibre**. El match con MercadoLibre requiere:
1. Tabla puente `(rfc_emisor, codigo_proveedor) → sku_ml` por proveedor (lo construye el matcher conforme va aprendiendo).
2. Fallback fuzzy por descripción del concepto contra título de la venta.

---

## 3. Reglas de Gaby (críticas — no asumir nada distinto)

### Detalle de colecta
- La columna importante para asignar al proveedor es **K (Lugar real)**, no la J (Lugar indicado).
- Cuando K trae "Sin información del lugar" (caso frecuente), **Gaby reasigna manualmente** la bodega.
- Ese override **persiste** y manda sobre el lugar real cuando se vuelve a cargar el Excel. Implementado en `envios_colecta.lugar_override`.

### Ventas ML
- La columna relevante para identificar el producto es **T = SKU**.
- El cruce con el detalle de colecta se hace por **`# de venta`**, no por SKU.

### Facturas
- Cada proveedor sube **XML + PDF** desde su cuenta.
- El match concepto-venta intenta primero por **`NoIdentificacion`** del XML (== SKU del proveedor) contra SKU de la venta.
- Fallback: fuzzy match por descripción contra título de la venta (umbral 0.6).
- Confidence < 0.5 cuenta como **error de facturación** en métricas.

### PENDIENTES ACDELCO y LISTA PRECIOS KG
- **NO** son para el motor de cruces.
- Son entradas del **Módulo 2 (publicaciones masivas)** — pendiente de implementar.
- En `PENDIENTES ACDELCO.xlsx`, la fila amarilla son los campos fijos que siempre van iguales en una publicación nueva; el resto lo llena Gaby manual.

### Publicaciones ML
- La columna **Q (`Att_SellerSKU`)** cruza contra los SKUs del catálogo del proveedor para detectar qué SKUs falta publicar.

---

## 4. Las 4 métricas que mide el portal

| Métrica | Cálculo (SQL en `routers/metricas.py`) |
|---|---|
| % entregas a tiempo en colecta | `cumplio_sla=1 / total envíos no excluidos` |
| Tiempo promedio de facturación | `avg(fecha_factura - fecha_venta)` |
| Errores de facturación | conceptos sin match + conceptos con confidence < 0.5 |
| Frecuencia actualización de stock | días desde el último upload de catálogo del proveedor |

---

## 5. Stack y arquitectura

### Frontend
- React 18 + Tailwind 3 + Lucide Icons + react-router-dom.
- Paleta RELUVSA: amarillo `#FFED00`, negro `#1a1a1a`, rojo `#E31E24`.
- Paleta Notion grays para superficies neutras.
- Font: Plus Jakarta Sans.
- Bundler: react-scripts (CRA).
- Estilo copiado de `~/Desktop/2026/RushData/RELUVSA/catalogo-reluvsa ` (con espacio final en el path — al usar la ruta, comillar siempre).

### Backend
- FastAPI + SQLite (sin ORM, SQL directo).
- Auth JWT con `python-jose` + contraseñas con `bcrypt`.
- Parsers: `openpyxl` (Excels), `lxml` (CFDI XML).
- Matching: `rapidfuzz` (token_set_ratio).
- Sin migrations: `database.py::init_database()` crea schema idempotente al arrancar y siembra los 5 proveedores.

### Deploy
- Backend: Railway (Procfile + railway.json listos).
- Frontend: Vercel (vercel.json + Root Directory = `frontend`).

### Repo
- GitHub privado: `git@github.com:rushdatamx/reluvsa-dropshipping.git` — branch `main`.

---

## 6. Estructura del repo

```
dropshipping-reluvsa/
├── README.md              # setup rápido
├── CLAUDE.md              # este archivo
├── .gitignore             # excluye archivos/, data/*.db, uploads/, node_modules
├── backend/
│   ├── main.py            # FastAPI app + CORS + wire-up de routers
│   ├── database.py        # SCHEMA + get_db() + init_database() + seed proveedores
│   ├── models.py          # Pydantic schemas
│   ├── requirements.txt
│   ├── Procfile           # Railway start command
│   ├── railway.json
│   ├── routers/
│   │   ├── auth.py        # /api/auth/login, /me + dependencies require_admin/require_proveedor
│   │   ├── proveedores.py # /api/proveedores
│   │   ├── ventas.py      # /api/ventas, /api/ventas/{num_venta}
│   │   ├── envios.py      # PATCH /api/envios/{id}/reasignar (override bodega)
│   │   ├── facturas.py    # GET listar + POST /upload (XML + PDF) con matching automático
│   │   ├── incidencias.py # CRUD + PATCH resolver
│   │   ├── metricas.py    # /api/metricas/proveedores + /resumen
│   │   └── uploads.py     # POST /api/uploads/ventas-ml y /colecta (admin)
│   ├── services/
│   │   ├── parser_ventas_ml.py  # parsea 66 cols del Excel de ventas ML
│   │   ├── parser_colecta.py    # parsea colecta + resuelve proveedor desde col K
│   │   ├── parser_cfdi.py       # CFDI 4.0 y 3.3 con lxml
│   │   └── matcher.py           # match concepto→venta (código exacto + fuzzy fallback)
│   ├── scripts/
│   │   └── crear_usuario.py     # CLI para crear admin o proveedor
│   └── uploads/                 # facturas subidas (ignorado en git, .gitkeep)
├── frontend/
│   ├── package.json
│   ├── tailwind.config.js       # paleta reluvsa/notion + Plus Jakarta Sans
│   ├── postcss.config.js
│   ├── vercel.json
│   ├── public/index.html
│   └── src/
│       ├── App.jsx              # BrowserRouter + AuthProvider + routes admin/proveedor
│       ├── index.js / index.css
│       ├── context/AuthContext.jsx
│       ├── lib/utils.js         # cn() helper (clsx + twMerge)
│       ├── services/api.js      # axios + interceptors JWT
│       ├── components/
│       │   ├── Login.jsx        # pantalla amarillo/negro
│       │   ├── Sidebar.jsx      # nav diferenciada admin vs proveedor
│       │   └── PageHeader.jsx
│       └── pages/
│           ├── Dashboard.jsx    # stats cards
│           ├── Ventas.jsx       # tabla con cruces + filtro "sin factura"
│           ├── Facturas.jsx     # listado + uploader de XML+PDF (rol proveedor)
│           ├── Incidencias.jsx
│           ├── Metricas.jsx     # tabla de las 4 métricas por proveedor
│           ├── Uploads.jsx      # cargar Excels de ventas ML y colecta (admin)
│           └── Proveedores.jsx  # listado de los 5
├── data/                        # SQLite local (ignorado)
└── archivos/                    # IGNORADO en git — datos reales del cliente (PII)
    ├── detalle-envios/          # reporte de ventas ML + detalle colecta
    ├── facturas-ejemplos/       # 3 PDFs CFDI + 1 imagen
    └── publicaciones-masivas/   # Publicaciones ML + lista KG + plantilla ACDELCO
```

---

## 7. Modelo de datos (SQLite)

Schema canónico en `backend/database.py`. Resumen:

```
proveedores       (id, nombre, rfc, codigo_bodega, contacto_*, activo)
usuarios          (id, email, password_hash, rol[admin|proveedor], proveedor_id)
ventas_ml         (num_venta PK, sku, fecha_venta, estado, titulo, total,
                   comprador, comprador_estado, forma_entrega,
                   factura_adjunta_ml, devolucion_unidades, reclamos)
envios_colecta    (num_envio PK, num_venta FK, lugar_indicado, lugar_real,
                   lugar_override, proveedor_id, cumplio_sla, excluido_analisis)
facturas          (id, proveedor_id, uuid_cfdi, serie, folio,
                   rfc_emisor, rfc_receptor, fecha, total, moneda, pdf_path, xml_path)
factura_conceptos (id, factura_id, codigo_prov, descripcion, cantidad, importe,
                   num_venta_match, match_method, match_confidence)
incidencias       (id, num_venta FK, proveedor_id FK, tipo, descripcion, estado)
catalogos_proveedor + catalogo_items  (módulo 2 — publicaciones masivas, NO usado aún)
publicaciones_ml + plantillas_ml      (módulo 2 — publicaciones masivas, NO usado aún)
```

Convenciones:
- **No usamos ORM.** SQL directo con `conn.execute()`.
- Fechas en ISO 8601 (TEXT).
- Foreign keys ON (PRAGMA en `get_connection`).
- Códigos de bodega son la **clave canónica** para resolver proveedor desde la columna K del Excel de colecta.

---

## 8. Estado actual (último update: 2026-06-02)

### ✅ Paso A COMPLETADO — Backend desplegado a Railway (2026-06-02)
- Proyecto Railway: `reluvsa-dropshipping` (antes nombre random `zoological-youthfulness`); servicio conectado a `rushdatamx/reluvsa-dropshipping`, branch `main`, auto-deploy ON.
- Root Directory = `/backend`. Builder: Railpack 0.25.0 (Railway ya no usa Nixpacks por default en proyectos nuevos; igual buildea Python por `requirements.txt`).
- 3 variables configuradas: `JWT_SECRET_KEY`, `CORS_ORIGINS=https://reluvsa-dropshipping-ghov.vercel.app`, `DATABASE_PATH=/data/dropshipping.db`.
- Volumen persistente montado en `/data` (se adjunta con **clic derecho sobre el servicio en el canvas → Attach Volume**, NO desde Settings → la UI nueva no tiene sección Volumes en Settings).
- URL pública: **`https://reluvsa-dropshipping-production.up.railway.app`**.
- Health-checks OK: `GET /` → 200 JSON; `GET /api/proveedores` sin token → 401; preflight CORS desde el origen Vercel devuelve el `access-control-allow-origin` correcto.

---

## 8.bis Estado anterior (último update: 2026-06-01)

### ✅ Completado
- Esqueleto completo del repo (46 archivos, ~2,860 líneas) commiteado en GitHub.
- **Módulo 1 — Conciliación**:
  - Backend completo: auth, CRUD proveedores, ventas, envíos, facturas, incidencias, métricas, uploads.
  - Parsers reales y probados (sintaxis) para Ventas ML, Detalle Colecta, CFDI 4.0/3.3.
  - Matcher con código exacto + fallback fuzzy (rapidfuzz token_set_ratio).
  - Frontend con todas las pantallas funcionales y estilo RELUVSA replicado de catalogo-reluvsa.
- **Frontend desplegado en Vercel** ✅ (Root Directory = `frontend`, framework CRA).
- **Backend probado en local (2026-06-01)** ✅
  - venv en `backend/.venv` con 41 paquetes (FastAPI 0.128, Pydantic 2.13, bcrypt 5.0, lxml 6.1, rapidfuzz 3.13).
  - `init_database()` corre sin errores; seed de los 5 proveedores OK.
  - `/api/auth/login` emite JWT y `/api/proveedores` con token responde 200 con los 5 proveedores.
  - 2 bugs encontrados y arreglados (commit `714e363`):
    1. `services/matcher.py` usaba `dict | None` (PEP 604) — incompatible con Python 3.9. Cambiado a `Optional[dict]`. Sigue siendo válido en 3.11 (Railway).
    2. `routers/proveedores.py` tenía `Proveedor(**dict(r), activo=bool(...))` en 3 funciones → TypeError por kwarg duplicado. Cambiado a `Proveedor(**{**dict(r), "activo": bool(...)})`.
  - Usuario admin local de prueba (NO usar en prod): `test@local.dev` / `TestLocal123!`.

### ⏳ En proceso / siguiente
- **Backend NO desplegado a Railway todavía**. Plan completo en sección 9, Paso A (actualizado con valores concretos).
- **No hay usuarios reales** todavía (el `test@local.dev` solo vive en la SQLite local).
- Variable `REACT_APP_API_URL` en Vercel **no configurada** (apunta a localhost por default).

### ❌ No iniciado
- **Módulo 2 — Publicaciones masivas** (LISTA PRECIOS KG → CSV ML + detector de SKUs faltantes + plantillas).
- UI para reasignación manual de bodega (botón en Ventas.jsx).
- Logo real de RELUVSA (placeholder con texto por ahora).
- Probar con datos reales (los 7 archivos están locales en `archivos/`).

---

## 9. Siguientes pasos (orden recomendado)

### Paso A — Desplegar backend a Railway (EN CURSO — esperando que el usuario termine en la UI)
Decisiones tomadas en sesión 2026-06-01:
- Usuario opera Railway desde la **UI web** (no CLI).
- Frontend Vercel confirmado en `https://reluvsa-dropshipping-ghov.vercel.app` (proyecto recreado; el viejo `reluvsa-dropshipping.vercel.app` crasheaba con `react-scripts: command not found` por Root Directory mal configurado; el nuevo buildea OK con Root Directory = `frontend`).
- Se configura **volumen persistente** (sin él los usuarios se borran en cada redeploy).

Pasos:
1. railway.com/new → "Deploy from GitHub repo" → `rushdatamx/reluvsa-dropshipping`.
2. Settings del servicio:
   - Root Directory: `backend`
   - Builder: Nixpacks (default)
   - Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT` (autodetectado por Procfile)
3. Variables de entorno (las 3):
   - `JWT_SECRET_KEY` = un valor aleatorio largo de 64+ chars. **Importante: la próxima sesión debe regenerar uno nuevo con `python -c "import secrets; print(secrets.token_urlsafe(64))"`** y no reutilizar el que aparezca en el historial del chat (queda expuesto).
   - `CORS_ORIGINS` = `https://reluvsa-dropshipping-ghov.vercel.app`
   - `DATABASE_PATH` = `/data/dropshipping.db`
4. Settings → Volumes → "+ Add Volume":
   - Mount Path: `/data`
   - Size: 1 GB
5. Settings → Networking → "Generate Domain" → anotar la URL pública (algo como `https://reluvsa-dropshipping-production.up.railway.app`).

### Paso B — Crear usuarios desde Railway shell
```
python3 scripts/crear_usuario.py admin gaby@reluvsa.com "PASSWORD_SEGURO"
python3 scripts/crear_usuario.py proveedor CAUPLAS quality@hoses.com "PASSWORD_CAUPLAS"
python3 scripts/crear_usuario.py proveedor KIM contacto@kimsauto.com "PASSWORD_KIM"
python3 scripts/crear_usuario.py proveedor AG contacto@argenparts.com "PASSWORD_AG"
python3 scripts/crear_usuario.py proveedor VAZLO contacto@vazlocomercial.com "PASSWORD_VAZLO"
python3 scripts/crear_usuario.py proveedor KG contacto@keepongreen.com "PASSWORD_KG"
```

### Paso C — Conectar frontend con backend
1. En Vercel → Settings → Environment Variables:
   - `REACT_APP_API_URL` = `https://<railway-url>.up.railway.app/api`
2. Vercel → Deployments → Redeploy del último build.

### Paso D — Probar end-to-end
1. Login con `gaby@reluvsa.com`.
2. Cargar reportes → subir el Excel de Ventas ML.
3. Cargar reportes → subir el Excel de Detalle Colecta.
4. Ventas y cruces → ver tabla con proveedores asignados; reasignar las "Sin información".
5. Login con un proveedor → subir un XML+PDF de factura → ver match automático.
6. Métricas proveedores → ver las 4 métricas pobladas.

### Paso E — Módulo 2: publicaciones masivas
Diseño preliminar en este CLAUDE.md (sección Reglas de Gaby). A construir:
- Uploader de catálogos de proveedor (LISTA PRECIOS KG y similares).
- Detector de SKUs faltantes contra `Publicaciones ML` (col Q).
- Editor de plantilla ML con campos fijos por proveedor (la fila amarilla).
- Export a CSV en formato Mercado Libre.

---

## 10. Lecciones aprendidas / decisiones tomadas

- **Vercel monorepo**: hay que configurar Root Directory = `frontend` o falla con `react-scripts: command not found`. No agregar `vercel.json` en la raíz; usar la UI de Vercel.
- **El receptor de facturas es GRUPO PEMIT, no RELUVSA**. Misma entidad legal, decidimos hardcodear `GPE230915JWA`.
- **5 proveedores, no 4**. La nota original de Gaby decía 4 pero el catálogo y las facturas muestran 5 (incluido KeepOnGreen).
- **MATRIZ no es proveedor** — es bodega propia. Importante en el mapeo de col K → proveedor.
- **Cada proveedor usa su propio SKU**. Por eso el matcher tiene fallback fuzzy: ARGENPARTS tiene descripciones tan pobres ("Base de amortiguador Del") que sin el código numérico no hay match.
- **Los archivos del cliente NO van al repo** — están en `archivos/` y excluidos por `.gitignore`. Tienen PII (nombres de compradores, RFCs, direcciones).
- **Python 3.9 en local, 3.11 en Railway**. El Mac tiene Python 3.9.6 del sistema (sin python3.11 instalado). El código del repo usa PEP 604 (`X | None`) que requiere 3.10+. Decisión: arreglar lo que truene con `Optional[X]` en lugar de instalar Python nuevo. Solo apareció en `services/matcher.py:14` (commit `714e363`); si aparece más en futuras adiciones, mismo fix.
- **Bug pattern `Proveedor(**dict(r), activo=bool(...))`**: si la columna ya viene en el SELECT, pasarla otra vez como kwarg explota con `TypeError: got multiple values for keyword argument`. Patrón correcto: `Proveedor(**{**dict(r), "activo": bool(r["activo"])})`. Aplicado en proveedores.py; si se replica en otros routers que serialicen booleanos, mismo fix.
- **Volumen persistente en Railway es obligatorio**: sin él, SQLite vive en el filesystem efímero y los usuarios/datos se borran en cada redeploy (incluyendo redeploys automáticos por push). Mount path `/data` + `DATABASE_PATH=/data/dropshipping.db`.
- **JWT_SECRET_KEY no se commitea ni se reutiliza entre sesiones**. Regenerar siempre con `secrets.token_urlsafe(64)` y pegarlo solo en Railway. Si se filtra (p.ej. en historial de chat), regenerar — invalida todos los tokens activos pero como aún no hay usuarios reales el costo es cero.

---

## 11. Memorias persistentes relacionadas

En `~/.claude/projects/-Users-jmariopgarcia-Desktop-2026-RushData-RELUVSA-dropshipping-reluvsa/memory/`:
- `project_reluvsa_dropshipping.md` — objetivo y métricas
- `project_proveedores_dropshipping.md` — los 5 proveedores con detalle
- `project_receptor_pemit.md` — relación RELUVSA / GRUPO PEMIT
- `project_reglas_gaby.md` — reglas de cada archivo según notas de Gaby
- `reference_catalogo_reluvsa.md` — repo base copiado
- `user_mario_rushdata.md` — perfil del usuario
