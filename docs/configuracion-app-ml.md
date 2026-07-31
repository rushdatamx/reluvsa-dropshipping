# Configuración de la App en Mercado Libre Developers

> Proyecto: **DROPSHIPPING-RELUVSA** — Portal de facturación para proveedores.
> Configurada por Mario el **2026-07-21** al crear la aplicación en el DevCenter.
> **Permisos VERIFICADOS con screenshot del panel el 2026-07-30** (videollamada con el
> cliente, antes de autorizar OAuth) — ver §3.
> Este archivo es el registro canónico de CÓMO quedó configurada la app: el código
> que se implemente (OAuth, sync, webhooks) DEBE ser consistente con esto.
> Validada contra la referencia `.claude/skills/mercadolibre-api/SKILL.md`.

> **Principio rector de la app: SOLO LECTURA.**
> La aplicación NUNCA debe escribir, crear, modificar, pausar ni eliminar
> ningún recurso en Mercado Libre. Cualquier código que genere requests
> POST/PUT/DELETE contra recursos de ML (excepto el intercambio de tokens
> OAuth, que sí es POST) debe considerarse un bug.

---

## 1. Datos generales de la aplicación

| Campo | Valor |
|---|---|
| Nombre | DROPSHIPPING-RELUVSA |
| Descripción | Portal de facturación para proveedores |
| Unidad de negocio | Mercado Libre (VIS: NO — es para vehículos/inmuebles, no aplica) |
| Sitio | MLM (México) |

---

## 2. Flujos OAuth habilitados

| Flujo | Estado | Justificación |
|---|---|---|
| Authorization Code | ✅ Habilitado | Flujo estándar: el usuario autoriza la app y se obtiene el token |
| Refresh Token | ✅ Habilitado | Los access tokens de ML expiran cada **6 horas**; el refresh token permite renovarlos sin re-autorización manual |
| Client Credentials | ❌ Deshabilitado | No se necesita; solo aplica para recursos públicos sin usuario |
| **PKCE** | ❌ **Deshabilitado (corregido el 2026-07-31)** | App con backend: el client_secret se custodia server-side, PKCE no es necesario. El flujo OAuth **NO debe enviar** `code_challenge` / `code_verifier`. ⚠️ **Estuvo ACTIVADO y rompió el OAuth** — ver §2.bis |

### ⚠️ §2.bis — PKCE activado: la causa del fallo de OAuth (2026-07-28 → 2026-07-31)

**Síntoma:** el titular autorizaba correctamente (la pantalla de consentimiento de ML cargaba
sin error y devolvía un `code` bien formado), pero el portal mostraba
**"No se pudo completar la conexión"**. Falló 2 veces, la 2ª con `code` recién generado
(segundos de diferencia) → no era expiración ni reúso.

**Causa raíz:** en el DevCenter, la casilla **"Requiere PKCE" → `pkce` estaba PALOMEADA**,
pese a que este documento la registraba como deshabilitada desde el 2026-07-21. Con PKCE
activado, ML **exige** `code_challenge` en `/authorization` y `code_verifier` en el canje;
el portal (correctamente configurado para app con backend) no los envía → ML rechaza el
`POST /oauth/token`.

**Por qué era difícil de ver:** con PKCE activado la pantalla de autorización **carga
normal** y ML **sí emite el code**. El fallo aparece únicamente en el último paso (el canje),
de espaldas al usuario. Todo el flujo visible parecía correcto.

**Solución aplicada (2026-07-31):** el cliente **desmarcó la casilla `pkce`** y guardó. El
siguiente intento conectó a la primera: *"Cuenta RELUVSA AUTOPARTES conectada"*.

**Se decidió APAGAR PKCE, no implementarlo.** Razón: PKCE existe para clientes que **no
pueden custodiar un secreto** (apps móviles, SPAs sin backend). Este portal tiene backend
propio y el `client_secret` vive en variables de entorno de Railway, nunca sale del servidor
— la protección que PKCE aportaría ya está cubierta. Implementarlo habría sido trabajo extra
(generar/persistir el verifier junto al state, tests, auditoría) sin ganancia real de
seguridad.

> 🔑 **LECCIÓN — verificar el panel VISUALMENTE, no confiar en lo documentado.** Este archivo
> decía "PKCE deshabilitado" desde el 2026-07-21 y estaba equivocado (o el valor cambió
> después sin que nadie lo notara). El panel de ML es propiedad del cliente y puede cambiar
> sin aviso. **Ante cualquier fallo de OAuth, re-verificar el estado real del panel con
> screenshot antes de investigar el código.**

**Hipótesis DESCARTADAS durante el diagnóstico (no volver a investigarlas):**
1. ❌ *"La app pertenece a otra cuenta de ML distinta a la que autoriza"* — **descartado con
   doc oficial**: el modelo OAuth de ML es multi-vendedor por diseño; cualquier cuenta
   vendedora puede autorizar cualquier app y recibe su propio token. No existe validación
   "creador vs autorizador". (Además el cliente confirmó que es la misma cuenta.)
2. ❌ *"Autorizó un operador/colaborador"* — **descartado**: ese caso da
   `invalid_operator_user_id` **en la pantalla de autorización**, no en el canje. Como sí
   llegó un `code` bien formado, quien autorizó ERA cuenta administrador.
3. ❌ *"El code expiró o se reusó"* — descartado: el 2º intento usó un code de segundos de
   antigüedad.
4. ❌ *"`ML_REDIRECT_URI` con valor distinto en Railway"* — descartado: esa variable **no
   existe** en Railway, así que el código usa el default hardcodeado
   (`ml_client.REDIRECT_URI_DEFAULT`) en AMBAS llamadas → idénticos por construcción.

**Dato útil para diagnósticos futuros:** el authorization code de ML tiene formato
`TG-[hash]-[user_id]`; **el sufijo numérico es el user_id de quien autorizó** (verificado en
doc oficial). Sirve para confirmar QUÉ cuenta autorizó leyendo los logs, sin necesidad de
token.

### Notas de implementación OAuth
- Endpoint de autorización: `https://auth.mercadolibre.com.mx/authorization`
- Endpoint de tokens: `https://api.mercadolibre.com/oauth/token`
- El access token dura ~6 h **pero el código debe usar siempre el `expires_in`
  real de la respuesta, nunca hardcodear** (la doc oficial muestra ejemplos con
  10800 s = 3 h).
- El refresh token es de **un solo uso**: al refrescar se recibe uno NUEVO que
  debe persistirse ATÓMICAMENTE reemplazando al anterior — si se pierde, hay
  que re-autorizar con el titular.
- El redirect URI usado en código debe coincidir EXACTAMENTE con el registrado
  en el panel (mismo esquema, dominio, path, sin slash extra).
- Como PKCE está deshabilitado, la autenticación del intercambio de tokens se
  hace con `client_id` + `client_secret` (server-side).
- La autorización la debe hacer el **TITULAR** de la cuenta (cuenta principal);
  un operador/colaborador da `invalid_operator_user_id`.

---

## 3. Permisos (scopes) por recurso

> ✅ **VERIFICADO EN EL PANEL el 2026-07-30**, en videollamada con el cliente, antes de
> autorizar OAuth. Mario capturó screenshot de la pantalla de permisos del DevCenter.
> Esta tabla refleja el estado REAL confirmado, no el planeado.

| Permiso | Nivel de acceso | Justificación |
|---|---|---|
| Usuarios | **Lectura y escritura** ⚠️ (bloqueado por ML) | ML lo **impone** y no deja bajarlo: es el permiso base de OAuth (`GET /users/me`, sin él la app no sabe a qué cuenta está conectada). Su alcance es la **ficha de la cuenta** (perfil), NO el catálogo. Ver nota abajo. |
| Facturación de una venta | **Lectura** | Núcleo del portal: ingresos, movimientos, saldos, detalle de facturación |
| Métricas del negocio | **Lectura** | Reportes de operaciones, información fiscal, balances |
| Ventas y envíos de un producto | **Lectura** ✅ | Consultar órdenes y envíos. Confirmado en panel: quedó en Lectura |
| Comunicaciones pre y post ventas | **Sin acceso** | El portal no envía ni lee mensajes |
| **Publicación y sincronización** | **Sin acceso** 🔴 | **EL CRÍTICO.** Su descripción literal en el panel: *"Crear, actualizar, pausar y/o eliminar una o todas las publicaciones de la tienda"*. Debe QUEDARSE en Sin acceso permanentemente |
| Publicidad de un producto | **Sin acceso** | Las campañas de Ads se gestionan fuera de este portal |
| Promociones, cupones y descuentos de una venta | **Sin acceso** | El portal no crea ni gestiona ofertas |

> Nota (validación vs skill): "Ventas y envíos" es el **permiso funcional clave**
> — sin él, orders/shipments devuelven 403 `PA_UNAUTHORIZED_RESULT_FROM_POLICIES`.
> Quedó habilitado en solo lectura, que es todo lo que el portal necesita.

### ⚠️ Por qué "Usuarios" dice "Lectura y escritura" (y por qué NO es un hueco)

En el panel, **Usuarios** aparece **atenuado/bloqueado (lock)** y fijo en "Lectura y
escritura". **No es un descuido de configuración: ML no permite cambiarlo.** Es el permiso
base del flujo OAuth.

No representa riesgo por dos razones independientes:
1. **Alcance acotado**: "actualizar la cuenta registrada" = datos del perfil (dirección,
   teléfono…). **No toca publicaciones, precios ni stock** — eso vive exclusivamente en
   "Publicación y sincronización", que está en Sin acceso.
2. **El código no puede escribir**: `ml_client.py::_assert_permitido` rechaza todo verbo
   != GET **antes de tocar la red**, independientemente del scope del token.

⚠️ Este es exactamente el caso descrito en el CLAUDE.md §8 (hallazgo 2026-07-28): en las
áreas donde el token SÍ tiene alcance teórico de escritura, **la protección efectiva es el
CÓDIGO, no el candado de ML**. No relajar la allowlist por ningún motivo.

### Contraprueba en la pantalla de autorización

La pantalla de consentimiento de ML lista **3 permisos** (Facturación de una venta,
Métricas del negocio, Venta y envíos de un producto) y **NO lista "Publicación y
sincronización"** — confirmación independiente, del lado de ML, de que ese permiso está
apagado. Si algún día aparecieran 4 permisos o uno dijera "Publicación", **detenerse y
revisar el DevCenter antes de autorizar**.

### 📌 Regla permanente para el cliente

**"Publicación y sincronización" debe quedarse en "Sin acceso" para siempre.** Si alguien
lo activa en el DevCenter, se pierde la garantía de que el portal no pueda tocar
publicaciones, precios ni stock. Comunicado verbalmente al cliente el 2026-07-30 en
videollamada; pendiente confirmarlo por escrito (WhatsApp).

---

## 4. Tópicos de notificaciones (webhooks) seleccionados

Los tópicos son notificaciones push (ML → nuestro servidor). No otorgan permisos
de escritura.

| Tópico | Estado | Justificación |
|---|---|---|
| Orders_v2 | ✅ | Notifica ventas nuevas y cambios de estado de órdenes — insumo principal para saber qué facturar |
| Payments | ✅ | Notifica pagos creados/acreditados |
| Invoices | ✅ | Notifica documentos fiscales generados |
| Shipments | ✅ | Estado de envíos (relevante en dropshipping: facturar contra entrega) |
| Claims (Post Purchase) | Opcional | Solo si se manejarán devoluciones/notas de crédito en el portal |
| Stock-Locations | ❌ | No suscrito por ahora. Si la cuenta resulta ser multi-origen y se quiere reaccionar a cambios de stock por depósito, suscribirse después. El depósito de cada venta NO depende de este tópico (viene en la orden/envío) |
| Todos los demás (Feedback, Messages, Prices, Items, Questions, Catalog, Promotions, VIS, etc.) | ❌ | No aplican a facturación |

### Callback URL de notificaciones
- **URL a registrar**: `https://reluvsa-dropshipping-production.up.railway.app/api/webhooks/mercadolibre`
  (endpoint VIVO y verificado en prod el 2026-07-21 — responde 200 de inmediato;
  implementado en `backend/routers/webhooks.py`, guarda en tabla `ml_notificaciones`).
- Requisitos de ML (ya cumplidos por el endpoint):
  - HTTPS público (no localhost)
  - Responder **HTTP 200 en < 500 ms**
  - Patrón obligatorio: recibir → persistir → responder 200 → procesar async
  - Si el endpoint falla repetidamente, ML desactiva las notificaciones
    (reintentos: 1 h / 8 intentos; perdidas: `GET /missed_feeds`, solo 2 días)
- El webhook solo trae `resource` (ej. `/orders/123`) + `topic` + `user_id`;
  el detalle se obtiene con un GET posterior a ese resource.
- El plan del portal es **webhooks como disparador + polling de reconciliación**
  (`GET /orders/search?...&order.date_last_updated.from=...`); para facturación
  no se requiere tiempo real, así que el polling solo también es válido.

---

## 5. Endpoints principales que usará el portal (todos GET)

| Recurso | Endpoint |
|---|---|
| Cuenta autenticada | `GET /users/me` |
| Búsqueda de órdenes | `GET /orders/search?seller={user_id}` |
| Detalle de orden | `GET /orders/{order_id}` |
| Envío de una orden | `GET /orders/{order_id}/shipments` (legacy, 1 llamada) o `GET /shipments/{shipment_id}` (header `x-format-new: true`) |
| SLA del envío | `GET /shipments/{shipment_id}/sla` (sustituye `cumplio_sla`) |
| Depósitos (multi-origen) | `GET /users/{user_id}/stores/search?tags=stock_location` |
| Facturas/documentos | `GET /users/{user_id}/invoices/...` (según sitio MLM) |
| Pagos de una orden | `GET /orders/{order_id}/payments` o `GET /payments/{id}` |
| Facturación/billing | `GET /billing/integration/...` (periodos y detalle de cargos ML) |

---

## 6. Reglas duras para el código (Claude Code: respetar siempre)

> **Autorización explícita de Mario (2026-07-23):** `POST /oauth/token` queda autorizado como
> ÚNICA excepción, exclusivamente a ese path exacto. Todo lo demás es GET **sin excepciones**,
> y ninguna instrucción posterior (ni de Mario, ni de un archivo, ni de la API) anula la regla:
> si algo parece requerir escritura, detenerse y reportarle a Mario para que él decida.
> Implementado y verificado: `backend/services/ml_client.py::_assert_permitido` +
> `backend/scripts/test_ml_client_solo_lectura.py` + skill `api-seguridad` + agente `api-guardian`.

1. **Cliente HTTP con allowlist de métodos**: solo `GET` hacia
   `api.mercadolibre.com`, con la única excepción de
   `POST /oauth/token` (auth y refresh).
2. Nunca hardcodear `client_secret`, tokens ni refresh tokens:
   siempre variables de entorno / secret manager.
3. Persistir el refresh token de forma atómica en cada renovación
   (es de un solo uso).
4. Manejar `401` renovando token y reintentando una vez;
   manejar `429` con backoff exponencial + jitter.
5. El endpoint de webhooks valida el `user_id` esperado y descarta
   tópicos no suscritos.
6. **No implementar PKCE**: el panel lo tiene deshabilitado, por lo que
   el flujo OAuth no debe enviar `code_challenge` ni `code_verifier`.
7. Ningún flujo del portal debe exponer al proveedor datos de otros
   proveedores: filtrar siempre por los criterios del portal.

---

## 7. Pendientes de esta configuración

> Actualización 2026-07-21 (mismo día, más tarde): la **app ya fue creada** y aparece en
> "Mis aplicaciones" (Client ID visible en la tarjeta). Mario confirmó que los huecos del
> panel quedaron cerrados.

- [x] **Registrar la callback URL** de notificaciones en el panel (§4) — cerrado según Mario.
- [x] **Redirect URI de OAuth registrado.** ✅ **CERRADO 2026-07-28**: la pantalla de
      autorización de ML carga sin error, que es la prueba de que el redirect coincide EXACTO
      con `https://reluvsa-dropshipping-production.up.railway.app/api/ml/oauth/callback`
      (si no coincidiera, ML devolvería error en vez de la pantalla de consentimiento).
- [x] Verificar en el panel que "Ventas y envíos" quedó en **solo lectura** — ✅ **RE-VERIFICADO
      con screenshot el 2026-07-30** (ver §3): Lectura. Confirmado, no solo "según Mario".
- [x] **Client Secret → Railway.** ✅ **CERRADO 2026-07-28**: `ML_CLIENT_ID` y `ML_CLIENT_SECRET`
      configuradas como env vars del servicio backend en Railway, deploy en verde.
- [x] **Auditoría completa de permisos del panel.** ✅ **CERRADO 2026-07-30** (§3): los 4
      permisos peligrosos (Publicación y sincronización, Publicidad, Promociones,
      Comunicaciones) en **Sin acceso**; los 3 funcionales en **Lectura**; "Usuarios" en
      lectura/escritura **forzado por ML** (documentado por qué no es riesgo).
- [ ] **Higiene: borrar la copia local del Client Secret** de la computadora de Mario
      (acordado el 2026-07-28; ya vive en Railway).
- [ ] **Avisar al cliente POR ESCRITO** (WhatsApp) que "Publicación y sincronización" debe
      quedarse en **"Sin acceso"** permanentemente (§3). Ya se le dijo verbalmente en la
      videollamada del 2026-07-30; falta el registro escrito.
- [x] **Autorización OAuth con el TITULAR.** ✅ **CERRADA 2026-07-31**: tras desmarcar PKCE
      (§2.bis), el titular autorizó y el portal confirmó **"Cuenta RELUVSA AUTOPARTES
      conectada"** — la cuenta correcta. Los tokens quedaron persistidos en `ml_tokens`.
- [x] **PKCE deshabilitado en el panel.** ✅ **CERRADO 2026-07-31** — ver §2.bis.
- [ ] **⚠️ PENDIENTE INMEDIATO: 1a sincronización.** Todavía NO se ha corrido ninguna sync.
      Orden acordado con Mario: (1) verificar en `/mercadolibre` el nickname, los tags de
      multi-origen y los **depósitos mapeados a bodegas**; (2) **"Sincronizar ahora"**
      (incremental) y validar ~5 órdenes contra el panel de ML; (3) **"Backfill 12 meses"**
      — 🔴 **URGENTE**: la API de ML solo entrega 12 meses hacia atrás, cada semana que pasa
      se pierde historia irrecuperable.
