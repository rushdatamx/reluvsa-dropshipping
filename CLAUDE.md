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
| `ARG041025AU2` | ARGENPARTS | `AG` | `P2172292` (factura) → `AG P2172292-2` (ML) |
| `VIM990605M8A` | VAZLO COMERCIAL | `VAZLO` | `30-578` (factura) → `VAZLO-30-578&30-578` (ML) |
| `STR910211DT2` | KEEPONGREEN (factura como SUMINISTRO TRANSAMERICANO DE REFACCIONES) | `KG` | `KR-1095WP` |

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
- ⚠️ **El cruce con el detalle de colecta NO es por `# de venta`** (regla corregida por Gaby el 2026-06-08). Mercado Libre asigna a veces **2 folios distintos a la misma venta** (uno en cada reporte), así que el número no cruza fiable. **El cruce es por fecha + título**:
  - Ventas ML: fecha = col **B**, título = col **X**.
  - Colecta: fecha = col **A**, título = col **E**.
  - Implementado: `envios_colecta.num_venta_ml` (el num_venta canónico de ML) se resuelve **una vez** en `services/parser_colecta.py::resolver_cruce_ventas` (directo por num_venta → fallback fecha ±5 min + título fuzzy ≥85). Todos los JOIN venta↔colecta usan `e.num_venta_ml = v.num_venta`. Ver memoria [[project_cruce_fecha_titulo]].

### Facturas
- Cada proveedor sube **XML + PDF** desde su cuenta.
- El match concepto-venta (`services/matcher.py`) tiene **3 pasos en orden**:
  1. **Código exacto**: `NoIdentificacion` del XML == SKU de la venta (o substring). Ej. KIM: `23530559-Z` == `23530559-Z`.
  2. **ID interno normalizado** (agregado 2026-06-08): cada proveedor usa su propio esquema; el código de factura no es idéntico al SKU de ML. CAUPLAS vende `CAU2692` pero factura `2692  M2626339` — se cruza por el ID interno común (`_tokens_codigo`). Sin esto CAUPLAS daba 0 matches. Ver [[project_matcher_id_interno]].
  3. **Fuzzy** por descripción contra título de la venta (umbral 0.6).
- ⚠️ El matcher solo busca candidatas `WHERE e.proveedor_id = X`, así que **un envío sin proveedor asignado (col K = Agencia ML / Sin info) impide el match** aunque la factura sea correcta → Gaby debe reasignar la bodega (selector en `Ventas.jsx`).
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
envios_colecta    (num_envio PK, num_venta, num_venta_ml, match_cruce_confianza,
                   fecha_venta, titulo, lugar_indicado, lugar_real,
                   lugar_override, proveedor_id, cumplio_sla, excluido_analisis)
                   -- num_venta_ml = cruce canónico a ventas_ml por fecha+título
                   --   (NO por num_venta; ML usa 2 folios). Resuelto en el parser.
                   -- match_cruce_confianza: 1.0 directo, <1 fuzzy, 0.8 ambiguo.
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

## 8. Estado actual (último update: 2026-06-11, ENTREGADO A GABY — esperando sus comentarios)

### 📍 PRÓXIMA SESIÓN: PROCESAR COMENTARIOS DE GABY — arrancar aquí
**El portal ya se ENTREGÓ a Gaby y ella ya lo usó y pasó comentarios.** La próxima sesión arranca
directamente en esos comentarios (Mario los traerá). Para cada uno: clasificar (bug / mejora /
duda-capacitación) y resolver. Ver memoria [[project_comentarios_gaby]] (punto de entrada con todo
el contexto fresco para procesarlos).

### 📍 CIERRE SESIÓN 2026-06-10/11 (ENTREGA A GABY) — qué se hizo antes de entregar
**Contexto:** Mario quiso entregar el portal para que Gaby lo probara con datos 100% reales. Se
verificó EXHAUSTIVAMENTE cada pestaña/función (plan mode), se cerraron huecos, se agregaron filtros y
export, se dejó la BD de prod en blanco, y se le mandó una guía de usuario en PDF.

**Lo que se hizo y quedó cerrado (3 commits a `main`, deploys Railway+Vercel SUCCESS):**
- ✅ **Verificación E2E de las 8 pestañas** con datos reales de `prueba-junio/` (BD desechable local).
  Todo pasó: login, resumen, carga (875 cruces), reasignar bodega, match KIM 1.0 + CAUPLAS 12/28
  id_interno, dedup 409, incidencias, las 4 métricas, proveedores con KG STR910211DT2.
- ✅ **3 correcciones** (commit `fe70437`): (1) **formulario "Nueva incidencia"** (admin) en
  `Incidencias.jsx` — antes la pestaña no dejaba crear; (2) **validación de RFC** en
  `facturas.py::upload` — un proveedor ya no puede subir factura de otro RFC (400 + borra archivos);
  (3) script `backend/scripts/wipe_transaccional.py` + test `test_cauplas_e2e.py`.
- ✅ **Endpoint admin de wipe** (commit `6322f42`): `POST /api/admin/wipe-transaccional` body
  `{"confirmar":"VACIAR"}` — vacía transaccionales, conserva proveedores+usuarios, backup VACUUM INTO.
  Se agregó porque el Railway CLI no quedó logueado y el MCP de Railway no ejecuta comandos en el
  contenedor. **Para futuros wipes de prod usar ESTE endpoint.**
- ✅ **Filtros avanzados + export CSV en Ventas** (commit `0efc4a2`): filtros de facturación
  (Todas/Facturadas/Sin factura), SLA (a tiempo/tarde), cruce con colecta (con envío/sin envío/envío
  sin proveedor), proveedor (admin), rango por fecha de venta; + `GET /api/ventas/export.csv`
  (server-side, respeta filtros) y botón Exportar. Para que el portal sea operable a diario.
- ✅ **BD de prod VACIADA y verificada** (vía el endpoint, con backup en
  `/data/dropshipping.db.bak-20260610_185809`): ventas/envíos/facturas → 0; proveedores(5)+usuarios(6)
  intactos. **KG = STR910211DT2 confirmado en prod.**
- ✅ **Guía de usuario** entregada: `Guia_Usuario_Portal_RELUVSA.pdf` (+ `.html`) en la raíz del repo
  — manual visual paso a paso (paleta RELUVSA), generado con Chrome headless `--print-to-pdf`.

**Dónde ve Gaby cada cosa:** SLA a tiempo/tarde por venta = columna SLA en Ventas (check/X) +
"% a tiempo" por proveedor en Métricas. Facturadas/no = columna Factura + filtro. NO hay pantalla de
Colecta independiente (los envíos se ven vía Ventas). Pendiente higiene: Mario iba a **rotar la
password de admin** usada el 06-10 (quedó expuesta en chat) — confirmar.

Ver [[project_estado_sesion_2026-06-10]].

### 📍 CIERRE SESIÓN 2026-06-09 (LOS 5 PROVEEDORES VALIDADOS E2E) — histórico (superado por la entrega 06-10/11)
**Contexto:** Mario preguntó si el match de KIM/CAUPLAS/Vazlo "no fallaría" y se cerró la validación de los 5 proveedores con datos reales. Gaby entregó el XML de Vazlo y las facturas+reportes de AG y KG. Commits `c415797` y `43eea2d` pusheados a `main` (auto-deploy Railway disparado).

**Lo que se hizo y quedó cerrado hoy:**
- ✅ **LOS 5 PROVEEDORES VALIDADOS E2E** con datos reales. El matcher genérico cruza los 5 esquemas distintos sin código a la medida (tabla completa abajo en "Lo que sigue"). Patrón confirmado en los 5: el envío sale de ML con `proveedor_id=None` → SIN MATCH hasta reasignar la bodega (botón Ventas.jsx). **El cuello de botella es la asignación de proveedor en colecta, NO el matcher.** Scripts reproducibles: `backend/scripts/test_vazlo_e2e.py`, `backend/scripts/test_ag_kg_e2e.py`. Ver [[project_vazlo_cruce_validado]], [[project_ag_kg_rfc_y_codigos]].
- 🎯 **RFC real de KeepOnGreen descubierto: `STR910211DT2`** (factura como "Suministro Transamericano de Refacciones"; estaba "PENDIENTE"). Corregido en `database.py`: seed + migración idempotente `_migrar_rfc_keepongreen()`. **Deploy disparado por el push — falta que Mario verifique en prod con login (pantalla Proveedores: KG debe mostrar STR910211DT2).**
- ✅ **XML de AG cerrado como NO-bloqueante.** AG solo mandó PDF, pero su cruce ya se validó sacando el código del PDF. Decisión de Mario: NO inferir/fabricar el XML (un CFDI lleva sello+UUID del SAT, no falsificable; sería factura falsa). El XML real llegará cuando AG opere en el portal. La estructura CFDI 4.0 es idéntica para todos (un solo parser sirve), pero eso ≠ tener el documento timbrado real.
- ✅ **"Bug cosmético" del `GET /api/facturas/{id}` cerrado como NO-BUG.** El endpoint devuelve todo correcto; el frontend ni lo usa. El null de P4 fue artefacto de la consulta por curl. Commit `43eea2d`.

**Decisión vigente — entrega con BD EN BLANCO:** la BD de prod (prueba-junio + overrides) es solo validación interna. Entregar a Gaby vacía (vaciar `ventas_ml`, `envios_colecta`, `facturas`, `factura_conceptos`, `incidencias`; conservar `proveedores` + `usuarios`) como ÚLTIMO paso antes de entregar. Ver [[project_entrega_bd_en_blanco]].

**Lo que sigue (próxima sesión — arrancar aquí):**
- **Verificar RFC de KG en prod** (Mario, con login): pantalla Proveedores → KEEPONGREEN debe mostrar `STR910211DT2`.
- **Módulo 2** (publicaciones masivas): no iniciado — es el único bloque grande que falta. Mario lo pospuso; cuando se retome, primero descubrir/documentar su alcance.
- **Pedir XML de AG a Gaby** cuando AG vaya a operar de verdad (no bloqueante).
- **Limpieza BD en blanco**: último paso antes de entregar.
- Menores sin probar con datos reales: flujo de incidencias E2E.

El Módulo 1 (conciliación ventas↔envíos↔facturas) está **completo y validado para los 5 proveedores**.

---

### 📍 CIERRE SESIÓN 2026-06-08 (P4 VALIDADO EN PROD) — histórico (superado por 2026-06-09)
**Contexto:** Sesión dedicada a validar P4 en prod. Mario subió por el portal los 2 Excels de `prueba-junio/` (mismo periodo). Se subieron los 3 XML como proveedor contra el portal REAL vía API conducida por Claude. **P4 quedó validado end-to-end en producción** (antes solo estaba probado en local).

**Resultados (verificados en prod):**
- Envíos: 1789 → **2265** (entró la colecta de prueba-junio). KIM: 13 → **52 envíos**, 38 ventas cruzadas.
- **KIM ✅**: K26802 (`23530559-Z`) y K26804 (`23542930-Z`) → 2/2 conceptos cruzan a su venta por `codigo_exact` conf 1.0.
- **CAUPLAS ✅**: I_8075 → **12/28** conceptos por `codigo_id_interno` conf 0.9 (ej. `2692 M2626339` → venta `CAU2692`). Exactamente lo predicho.
- **Reasigné 105 envíos CAUPLAS por API** (`PATCH /api/envios/{num_envio}/reasignar` body `{"lugar_override":"CAUPLAS"}`) — eran ventas SKU `CAU*` que salían como "Agencia de Mercado Libre" con prov=None. Esto es lo que hace el botón nuevo de Ventas.jsx. Pasaron de 0 → 105 ventas CAUPLAS cruzadas. ⚠️ **Estos overrides quedaron persistidos en prod — avisar a Gaby.**
- **Los 16 conceptos CAUPLAS sin match NO son bug**: son piezas MATIZ (`5487, 5502, 5543...`) facturadas que no tienen venta cruzable (no existen en el reporte ML o la venta no trae envío). Se reflejaron como **16 errores de facturación** en métricas de CAUPLAS. Justo la señal de valor para Gaby.
- **Las 4 métricas se poblaron en prod**: KIM facturación 2.8 días / SLA 100% / 0 errores; CAUPLAS facturación 7.8 días / SLA 96% / 16 errores. Facturas totales = 3.

**Gotcha API (anotar):** `POST /api/auth/login` devuelve el JWT en el campo **`token`**, NO `access_token`.

**~~Bug cosmético del `GET /api/facturas/{id}`~~ → CERRADO como NO-BUG (2026-06-09).** Se investigó en código: el endpoint `detalle()` hace `SELECT *` y devuelve TODOS los campos correctos (uuid/serie/folio/total/rfc_receptor), anidados bajo `{"factura": {...}, "conceptos": [...]}`. Además el frontend NO usa ese endpoint — no existe pantalla de detalle de factura; `Facturas.jsx` solo consume el listado `GET /api/facturas` (tabla). El `null` reportado en la sesión P4 fue por cómo se consultó por curl (probablemente se miró el campo en el nivel equivocado de la respuesta anidada), no un bug. Verificado reproduciendo el insert+detalle con la factura real de KIM K26802: todos los campos salen correctos. (Nota: el "total 4114.26" de la nota P4 era de la factura de CAUPLAS, no de KIM —K26802 totaliza $8.40, correcto según su XML.) Si en el futuro se quiere que Gaby vea el detalle de una factura con sus conceptos y el cruce a venta, eso sería una **pantalla nueva** (mejora), no un arreglo.

**Lo que sigue (próxima sesión — arrancar aquí):**
- ✅ **LOS 5 PROVEEDORES VALIDADOS E2E (2026-06-09)**. El matcher genérico cruza los 5 esquemas de código distintos sin código a la medida:
  | Proveedor | Factura | ML publica | Cruza por | Conf |
  |---|---|---|---|---|
  | KIM | `23530559-Z` | `23530559-Z` | exacto | 1.0 |
  | CAUPLAS | `2692 M2626339` | `CAU2692` | id_interno | 0.9 |
  | Vazlo | `30-578` | `VAZLO-30-578&30-578` | exacto substring | 1.0 |
  | AG | `P2172292` | `AG P2172292-2` | exacto substring | 1.0 |
  | KG | `KR-1095WP` | `KR-1095WP` | exacto | 1.0 |
  - **Vazlo**: Mario entregó el XML (`VIM990605M8A_FMX0069127`). Validado en local con `backend/scripts/test_vazlo_e2e.py`. Ver [[project_vazlo_cruce_validado]].
  - **AG y KG**: Gaby entregó facturas + reportes (ventas+colecta) en `prueba-junio/AG/` y `prueba-junio/KG/` (cada carpeta trae su propio par; el archivo "2" es ventas en AG y colecta en KG). Validados con `backend/scripts/test_ag_kg_e2e.py`. AG cruzó aunque solo hay PDF (concepto armado del PDF) → **falta su XML para subirla por el portal**. Ver [[project_ag_kg_rfc_y_codigos]].
  - **Patrón confirmado en los 5**: el envío sale de ML con `proveedor_id=None` → SIN MATCH hasta reasignar la bodega (botón Ventas.jsx). El cuello de botella es la asignación de proveedor en colecta, NO el matcher.
  - 🎯 **RFC real de KeepOnGreen descubierto: `STR910211DT2`** (factura como "Suministro Transamericano de Refacciones"). Estaba `"PENDIENTE"`. Corregido en `database.py`: seed actualizado + migración idempotente `_migrar_rfc_keepongreen()`. **Pusheado en `c415797` (deploy disparado); falta que Mario verifique en prod con login.**
- **Entrega con BD en blanco** (decidido 2026-06-09): la BD de prod actual (prueba-junio + 105 overrides CAUPLAS) es solo validación interna. Entregar a Gaby con la BD VACÍA (vaciar `ventas_ml`, `envios_colecta`, `facturas`, `factura_conceptos`, `incidencias`; conservar `proveedores` + `usuarios`) como ÚLTIMO paso antes de entregar. Esto cierra como **obsoleta** la nota "avisar a Gaby de los 105 overrides" (se borran, nunca llegan a Gaby). Ver [[project_entrega_bd_en_blanco]].
- ~~Arreglar el bug cosmético del `GET /api/facturas/{id}` detalle~~ → cerrado como NO-BUG (2026-06-09): el endpoint devuelve todo correcto y el frontend no lo usa. Ver bloque de cierre arriba.
- **Módulo 2** (publicaciones masivas): no iniciado.
- Menores sin probar con datos reales: flujo de incidencias E2E.

Ver memoria [[project_estado_sesion_2026-06-08-p4]].

---

### 📍 CIERRE SESIÓN 2026-06-08 (primera, despliegue) — histórico
**Contexto:** Mario tuvo la junta con Gaby. Demo OK. Gaby entregó por fin los 3 reportes del **mismo periodo** en `prueba-junio/` (raíz del repo, ignorado por PII): Ventas ML (corte 4-jun) + Colecta (corte 1-jun, ambos cubren ventas de mayo 9–12) + **facturas en XML** (KIM x2, CAUPLAS x1). Marcó 6 ventas en amarillo en ambos Excels.

**Lo que se hizo hoy — TODO DESPLEGADO Y VIVO EN PROD** (Railway verificado: `GET /`→200, `/api/proveedores`→401):
- 🔑 **Cambio de regla de cruce (Gaby): ventas↔colecta por fecha+título, NO por # de venta.** ML asigna 2 folios a la misma venta. Verificado: por num_venta cruzan 456/944 envíos; por fecha+título **875/944**. Nueva columna `num_venta_ml` + `match_cruce_confianza`, resueltas en `resolver_cruce_ventas()`. 6 JOINs cambiados. Migración idempotente. Commit `ffe4e19`.
- 🔧 **Matcher CAUPLAS**: paso nuevo por ID interno (`CAU2692` venta vs `2692 M2626339` factura). Antes 0 matches, ahora 12/28.
- 🖱️ **UI**: selector de bodega en `Ventas.jsx` para reasignar envíos sin proveedor (col K = Agencia ML / Sin info).
- 🐛 **Fix crash Railway (commit `3447912`)**: el `CREATE INDEX idx_envios_venta_ml` estaba en el SCHEMA (corre con executescript ANTES de las migraciones); en el volumen la columna aún no existía → reventaba todo el script → crash loop. Movido a `_migrar_columnas_cruce()` tras el ALTER. **Lección: probar migraciones con BD VIEJA, no solo nueva.** La migración aplicó OK en prod sin perder datos.
- 🔒 `.gitignore`: `prueba-junio/` (PII).

**P4 — probado end-to-end en LOCAL con datos reales** (FALTA validar en prod):
- KIM: 2/2 facturas cruzan concepto→venta por código exacto ✅.
- CAUPLAS: 12/28 por id_interno cuando los envíos están asignados. El cuello de botella NO es el matcher sino la **asignación de proveedor en colecta** (las ventas CAUPLAS de prueba-junio salieron como "Agencia de Mercado Libre" → requieren override de Gaby con el botón nuevo).

**Cargas repetidas (Gaby subiendo a diario):** ambos parsers hacen **upsert por clave** (ventas=`num_venta`, colecta=`num_envio`): fila nueva→INSERT, existente→UPDATE. Respeta `lugar_override` al re-subir. Nada se borra (BD solo crece). Apto para subidas frecuentes. Pendiente menor: prueba E2E de doble carga con solape.

**Lo que sigue (próxima sesión — arrancar aquí):**
- ▶️ **VALIDAR P4 EN PROD (sesión dedicada, lo más importante)**: subir los 2 Excels de `prueba-junio/` + los 3 XML como proveedor en el portal real; confirmar cruces + matches end-to-end (incluye probar el botón de reasignar bodega con las 2 ventas CAUPLAS). No requiere tocar código.
- **Pedir XML de Vazlo** a Gaby (2 de las 6 ventas amarillas son Vazlo y no llegó su XML).
- **Módulo 2** (publicaciones masivas): no iniciado.
- Menores sin probar con datos reales: flujo de incidencias E2E; la métrica "frecuencia actualización de stock" sale vacía hasta que exista Módulo 2.

**P2 (seguridad): ✅ CERRADO.** Mario confirmó el 2026-06-08 que ya rotó las passwords y limpió las vars de bootstrap. Ya NO es pendiente.

---

### 📍 CIERRE SESIÓN 2026-06-03 — (histórico, superado por el de 2026-06-08)
**Lo que se hizo hoy:**
- ✅ **P1 cerrado**: Mario subió los 2 Excels en prod; números verificados vía API (2053 ventas, 1789 envíos, CAUPLAS 121/94.2%, KIM 13/100%).
- ✅ **P3 cerrado**: los 5 usuarios proveedor entran (`cauplas`/`kim`/`ag`/`vazlo`/`kg` + password). Se implementó bootstrap por env var + endpoint admin `POST /api/admin/proveedor-password` (se necesitó porque Railway corta líneas al pegar multi-línea y CAUPLAS quedó mal 2 veces).
- 🚨 **P4 bloqueado por DATOS** (no por código): desfase de periodos (envíos con proveedor=abril, ventas=mayo → 0 cruces con proveedor) + facturas de ejemplo sin XML. Diagnóstico completo abajo.

**Lo que sigue (próxima sesión):**
- **Reunión Mario↔Gaby el 2026-06-04** (día siguiente). Mario hará demo del portal y pedirá los 3 insumos del MISMO periodo (idealmente abril): Ventas ML + Colecta + Facturas en **XML**. Guion en `~/Desktop/GUION_DEMO_GABY.md` (fuera del repo).
- Cuando Gaby entregue esos datos → **ejecutar P4** (subir XML como proveedor, ver match concepto→venta). Sin tocar código.
- ⚠️ **P2 (seguridad) PENDIENTE Y URGENTE**: rotar password de Gaby (`bXubgXKQQsxxFz6e`, expuesta en chat) + las 5 de proveedores (también expuestas) usando el endpoint admin; borrar `ADMIN_BOOTSTRAP_PASSWORD` y `PROVEEDOR_BOOTSTRAP` de Railway. Mario pospuso esto el 2026-06-03.
- **Módulo 2** (publicaciones masivas): no iniciado.

**Archivos temporales en el escritorio de Mario (fuera del repo, contienen secretos/PII — recordar borrar):**
- `~/Desktop/PROVEEDOR_BOOTSTRAP.txt` (passwords de proveedor en claro).
- `~/Desktop/GUION_DEMO_GABY.md` (guion de la demo).

### ✅ P1 CERRADO — Paso D confirmado end-to-end en producción (2026-06-03)
- Mario subió los 2 Excels desde el portal en prod (`gaby@reluvsa.com`). Resultados del uploader:
  - Ventas ML → `{"inserted": 2053, "updated": 0, "skipped": 0}`.
  - Colecta → `{"sheet_used": "Últimas 4 semanas", "inserted": 1789, "updated": 0, "envios_sin_proveedor_inferido": 533}`.
- Verificado vía API con token de admin (`/api/metricas/resumen` + `/api/metricas/proveedores`) — **todos los números cuadran**:
  - Ventas = **2053** ✅, Envíos = **1789** ✅, Proveedores activos = 5 ✅.
  - QUALITY HOSES (CAUPLAS) = **121 envíos, 94.2% a tiempo** ✅; KIMS AUTO (KIM) = **13 envíos, 100% a tiempo** ✅.
  - AG / KG / VAZLO = 0 envíos este corte (sus envíos cayeron en MATRIZ o "Sin información"; esperado).
  - El motor completo corre en prod: parseo → asignación por col K → cálculo de SLA. La métrica de SLA ya se puebla.
- Nota: los "217 cruces envío↔venta" del Paso D no se exponen por endpoint; el desglose 121+13=134 con proveedor dropshipping confirma que el parseo y la asignación por col K funcionan. ⚠️ PERO ver P4: esos 134 con proveedor NO son los mismos que los 217 que cruzan venta (periodos disjuntos).

### 🚨 P4 BLOQUEADO POR DATOS — desfase de periodos entre los 2 Excels (2026-06-03)
- Al preparar P4 (facturas) se descubrió que **0 ventas tienen envío cruzado con proveedor**, aunque hay 2053 ventas, 1789 envíos y 134 envíos con proveedor (CAUPLAS 121 + KIM 13). El match de facturas cruzaría contra 0.
- **Causa raíz (NO es bug de código, es desfase de datos)**: los dos Excels cubren periodos distintos.
  - Envíos **con proveedor identificado** (col K = CAUPLAS/KIM): **TODOS de ABRIL** (11–29 abr).
  - Envíos que **cruzan** con una venta (217): todos de **mayo** (1–8 may), y esos tienen proveedor NULL.
  - Ventas ML: **todas de mayo** (1–13 may).
  - En mayo, **ningún envío tiene proveedor dropshipping** (0 CAUPLAS, 0 KIM): los de mayo son MATRIZ (140), "Sin info" (189) o "Agencia de Mercado Libre" (93).
  - → Intersección (envío con proveedor ∩ venta cargada) = **0**. Por eso `GET /api/ventas?proveedor_id=N` y el matcher de facturas dan 0.
- Verificación: `JOIN envios e ON v.num_venta=e.num_venta` da 217, pero `... WHERE e.proveedor_id IS NOT NULL` da 0.
- Aclaración: "Agencia de Mercado Libre" (185 envíos, col K) NO es un proveedor faltante — es recolección por agencia ML, correcto que no mapee.
- Los 3 PDFs de `facturas-ejemplos/` son CFDIs reales con texto extraíble (pdfplumber), receptor GRUPO PEMIT ✅, traen NoIdentificacion (M2622638, 9030175-Z, 4905967) — pero **no hay XML** y el endpoint exige XML. El parser CFDI 4.0 se validó con un XML sintético y funciona (extrae UUID/RFC/conceptos).
- **DESBLOQUEO (pedido a Gaby)**: exportar Ventas ML y Detalle de colecta cubriendo **el mismo rango de fechas** (idealmente abril completo, donde SÍ hay proveedores identificados, + sus ventas). Con periodos solapados, cruces y match de facturas funcionarán. Además, conseguir el **XML** (no solo PDF) de las facturas de ejemplo.
- ⚠️ **P2 ahora es URGENTE**: la password del admin (`bXubgXKQQsxxFz6e`) quedó expuesta en el historial del chat de esta sesión. Rotar password de `gaby@reluvsa.com` Y borrar `ADMIN_BOOTSTRAP_PASSWORD` de Railway en la próxima sesión (Mario eligió posponerlo el 2026-06-03).

### ✅ Pasos A, B, C COMPLETADOS + Paso D validado en local (2026-06-02)
- **Paso B** ✅: admin Gaby creado vía bootstrap por env vars (`gaby@reluvsa.com`), login verificado. Los 5 proveedores se sembraron OK.
- **Paso C** ✅: Vercel `REACT_APP_API_URL` = `https://reluvsa-dropshipping-production.up.railway.app/api`, bundle de prod verificado apuntando a Railway (no localhost), login real funciona desde el navegador.
- **Paso D (parsers vs datos reales)** — 3 bugs encontrados y arreglados (commit `3fbc087`):
  1. Fechas Ventas ML en español largo ("13 de mayo de 2026 23:43"), no ISO → `_parse_fecha` con regex de mes español. Sin esto, fecha_venta quedaba None en las 2053 ventas.
  2. Celdas numéricas con espacios (' ') y floats ('1.0') → helpers `_to_int`/`_to_float` defensivos (antes `int(' ')` abortaba todo el parseo).
  3. `envios_colecta.num_venta` era FK estricta a ventas_ml; 88% de envíos reales no tienen su venta en el reporte ML (cortes de fecha distintos) → se quitó la FK + migración idempotente `_migrar_envios_sin_fk` (preserva filas y `lugar_override`).
  - **Cifras esperadas con los archivos reales** (corte 14-may-2026): Ventas ML = **2053** (100% con fecha), Envíos colecta = **1789**, cruces envío↔venta = **217**, proveedor CAUPLAS = 121, KIM = 13. El resto sin proveedor = MATRIZ (bodega propia) o "Sin información".
  - **PENDIENTE**: que Mario suba los 2 Excels desde el portal en prod y confirme estos números end-to-end. Facturas (XML+PDF) aún no probadas con datos reales.

### ✅ Paso A COMPLETADO — Backend desplegado a Railway (2026-06-02)
- Proyecto Railway: `reluvsa-dropshipping` (antes nombre random `zoological-youthfulness`); servicio conectado a `rushdatamx/reluvsa-dropshipping`, branch `main`, auto-deploy ON.
- Root Directory = `/backend`. Builder: Railpack 0.25.0 (Railway ya no usa Nixpacks por default en proyectos nuevos; igual buildea Python por `requirements.txt`).
- 3 variables configuradas: `JWT_SECRET_KEY`, `CORS_ORIGINS=https://reluvsa-dropshipping-ghov.vercel.app`, `DATABASE_PATH=/data/dropshipping.db`.
- Volumen persistente montado en `/data` (se adjunta con **clic derecho sobre el servicio en el canvas → Attach Volume**, NO desde Settings → la UI nueva no tiene sección Volumes en Settings).
- URL pública: **`https://reluvsa-dropshipping-production.up.railway.app`**.
- Health-checks OK: `GET /` → 200 JSON; `GET /api/proveedores` sin token → 401; preflight CORS desde el origen Vercel devuelve el `access-control-allow-origin` correcto.

---

## 8.bis Estado anterior (histórico, 2026-06-01 — superado por la sección 8)

> ⚠️ Esta subsección es un snapshot del 2026-06-01, antes del deploy. Lo que aquí aparece como "pendiente" (backend a Railway, usuarios, REACT_APP_API_URL) **ya está resuelto** — ver sección 8. Se conserva solo como registro histórico.

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

> **Pasos A, B, C completados y Paso D validado en local el 2026-06-02.** Detalle del cómo en la sección 8. Aquí abajo queda SOLO lo pendiente. Los pasos A–C originales (deploy, crear admin, conectar frontend) ya están hechos — su procedimiento histórico se conserva en la sección 8.bis y en las memorias `project_railway_deploy.md`.

### ▶️ Pendiente inmediato (arrancar aquí la próxima sesión)

**P1 — Confirmar Paso D end-to-end en el portal. ✅ CERRADO 2026-06-03.** Ver sección 8: números confirmados en prod (2053 / 1789 / CAUPLAS 121 a 94.2% / KIM 13 a 100%).

**P2 — Higiene de seguridad. ✅ CERRADO 2026-06-08.** Mario rotó la password de `gaby@reluvsa.com` y las 5 de proveedores, y limpió las vars de bootstrap (`ADMIN_BOOTSTRAP_PASSWORD` / `PROVEEDOR_BOOTSTRAP`) de Railway. (Las passwords que aparecen más abajo en bloques históricos ya no son válidas.)

**P3 — Crear los 5 usuarios proveedor. ✅ COMPLETADO 2026-06-03. Los 5 entran (cauplas/kim/ag/vazlo/kg + password).**
- ⚠️ La UI de Railway **corta líneas al pegar variables multi-línea**: la primera línea (`CAUPLAS:...`) se perdió DOS veces, y en un intento el usuario `cauplas` se creó con una password alterada. Como el bootstrap es idempotente (no recrea passwords de usuarios existentes), reeditar la variable NO lo arreglaba.
- **Solución definitiva**: se agregó `POST /api/admin/proveedor-password` (router `routers/admin.py`, solo admin) que crea O resetea la password de un proveedor por `codigo_bodega` vía API. Con esto se arregló CAUPLAS (acción "reseteada") sin depender de pegar en Railway. Reutilizable para rotar passwords a futuro. Commit `4d6b2b1`.
- Uso: `POST /api/admin/proveedor-password` con token admin, body `{"codigo_bodega":"CAUPLAS","password":"..."}`.
- **Lección**: para proveedores nuevos o rotación de passwords, usar el endpoint admin, NO la variable PROVEEDOR_BOOTSTRAP (que sigue sirviendo solo para el primer alta masiva, y aún así hay que verificar que las 5 líneas hayan quedado).

**P3.bis (histórico) — Bootstrap por env var implementado 2026-06-03.**
- Se implementó `database._bootstrap_proveedores()` análogo al del admin (commit pendiente de push). Idempotente, se ejecuta en `init_database()` al arrancar.
- **Los proveedores entran con USERNAME, no con correo real** (decisión de Mario): el username es el código de bodega en minúsculas (`cauplas`, `kim`, `ag`, `vazlo`, `kg`). El login (`username_a_email()` en `database.py`) expande cualquier identificador sin `@` a `<user>@reluvsa.local`; los correos reales (admin Gaby) siguen funcionando igual. El frontend Login.jsx cambió de `type=email` a `type=text` para permitirlo.
- **Para activarlos**: en Railway agregar la variable `PROVEEDOR_BOOTSTRAP` (multi-línea, una por proveedor, formato `CODIGO:password`). Ejemplo:
  ```
  CAUPLAS:<pass>
  KIM:<pass>
  AG:<pass>
  VAZLO:<pass>
  KG:<pass>
  ```
  Al redeploy, los 5 usuarios se crean solos. Definir las passwords reales con Mario/Gaby. Tras crear, se puede borrar la var (como con el admin) — pero ojo: a diferencia del admin, dejar `PROVEEDOR_BOOTSTRAP` no recrea passwords (es idempotente por email existente), así que para **rotar** una password hay que borrar el usuario y volver a bootstrappear, o usar `scripts/crear_usuario.py`.
- Probado en local: los 5 se crean, idempotencia OK, login con `cauplas`/`CAUPLAS` + password OK, password incorrecta rechazada, asociación a proveedor correcta.
- Alternativa CLI (sigue disponible): `python3 scripts/crear_usuario.py proveedor <CODIGO_BODEGA> <email> "<password>"` desde la Console de Railway (rompe formato al pegar — preferir el bootstrap).

**P4 — Probar facturas con datos reales. ✅ VALIDADO EN PROD (2026-06-08, segunda sesión).**
- Los 2 bloqueos del 06-03 (sin XML + desfase de periodos) se resolvieron: Gaby entregó en `prueba-junio/` los 3 reportes del mismo periodo + facturas en XML. Además se descubrió que el cruce ni siquiera era por num_venta sino por fecha+título (ver sección 3 y [[project_cruce_fecha_titulo]]).
- **Validado end-to-end en PROD** (ver bloque de cierre arriba): KIM 2/2 por `codigo_exact`; CAUPLAS 12/28 por `codigo_id_interno`. Se reasignaron 105 envíos CAUPLAS por API (botón Ventas.jsx en la UI). Las 4 métricas se poblaron. Los 16 conceptos CAUPLAS sin match son piezas MATIZ sin venta cruzable → señal de error de facturación correcta, no bug.
- **Falta el XML de Vazlo** (2 ventas amarillas son Vazlo) → pedírselo a Gaby.

### Paso E — Módulo 2: publicaciones masivas (no iniciado)
Diseño preliminar en este CLAUDE.md (sección 3, Reglas de Gaby). A construir:
- Uploader de catálogos de proveedor (LISTA PRECIOS KG y similares).
- Detector de SKUs faltantes contra `Publicaciones ML` (col Q = `Att_SellerSKU`).
- Editor de plantilla ML con campos fijos por proveedor (la fila amarilla de `PENDIENTES ACDELCO.xlsx`).
- Export a CSV en formato Mercado Libre.
- Archivos fuente en `archivos/publicaciones-masivas/`: `LISTA PRECIOS KG.xlsx`, `Publicaciones - ML_...xlsx`, `PENDIENTES ACDELCO.xlsx`.

### Otros pendientes menores
- UI para reasignación manual de bodega (botón en Ventas.jsx) — el backend ya lo soporta (`PATCH /api/envios/{id}/reasignar` + `lugar_override`).
- Logo real de RELUVSA (hoy placeholder de texto).

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
- **La Console web de Railway rompe el formato al pegar comandos largos.** Por eso el admin se crea por **bootstrap de env vars** (`ADMIN_BOOTSTRAP_EMAIL` + `ADMIN_BOOTSTRAP_PASSWORD`) que `init_database()` lee al arrancar (commit `bbdbe34`, idempotente). Borrar la password de Railway tras crear el admin. Para proveedores aún no hay bootstrap análogo (ver P3).
- **Gotchas de la UI nueva de Railway** (gastamos tiempo): Root Directory está en **Service Settings → Source** (no en Project Settings); el **volumen** se adjunta con **clic derecho sobre la cajita del servicio en el canvas → Attach Volume** (no hay sección Volumes en Settings); builder por default es Railpack (no Nixpacks). Detalle en `project_railway_deploy.md`.
- **Datos reales rompen los parsers de formas que la sintaxis no detecta** (commit `3fbc087`): fechas en español ("13 de mayo de 2026" en ventas, "viernes 8 may 2026" en colecta — dos formatos distintos), celdas numéricas con espacios sueltos (' ') y floats ('1.0') que `int()/float()` directos no toleran, y la FK `envios_colecta.num_venta → ventas_ml` que rechaza el 88% de envíos reales (cortes de fecha distintos). Detalle en `project_datos_reales_parsers.md`.
- **`CREATE TABLE IF NOT EXISTS` no altera tablas ya creadas.** Cualquier cambio de schema sobre una BD existente (el volumen de Railway) requiere migración explícita idempotente en `init_database()`. Patrón aplicado en `_migrar_envios_sin_fk`: detectar con `PRAGMA foreign_key_list`, rename → recrear → copiar filas (preservando `lugar_override`) → drop. Apagar `PRAGMA foreign_keys` durante el swap.

---

## 11. Memorias persistentes relacionadas

En `~/.claude/projects/-Users-jmariopgarcia-Desktop-2026-RushData-RELUVSA-dropshipping-reluvsa/memory/`:
- `project_reluvsa_dropshipping.md` — objetivo y métricas
- `project_proveedores_dropshipping.md` — los 5 proveedores con detalle
- `project_receptor_pemit.md` — relación RELUVSA / GRUPO PEMIT
- `project_reglas_gaby.md` — reglas de cada archivo según notas de Gaby
- `project_backend_python_compat.md` — Python 3.9 local vs 3.11 Railway (PEP 604)
- `project_backend_kwarg_duplicado.md` — bug pattern kwarg duplicado en routers
- `project_railway_deploy.md` — deploy Railway: URLs, env vars, volumen, gotchas de la UI
- `project_datos_reales_parsers.md` — bugs de parsers con datos reales y cifras de referencia
- `reference_catalogo_reluvsa.md` — repo base copiado
- `user_mario_rushdata.md` — perfil del usuario
