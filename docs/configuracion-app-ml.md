# Configuración de la App en Mercado Libre Developers

> Proyecto: **DROPSHIPPING-RELUVSA** — Portal de facturación para proveedores.
> Configurada por Mario el **2026-07-21** al crear la aplicación en el DevCenter.
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
| PKCE | ❌ Deshabilitado | App con backend: el client_secret se custodia server-side, PKCE no es necesario. El flujo OAuth **NO debe enviar** `code_challenge` / `code_verifier` |

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

| Permiso | Nivel de acceso | Justificación |
|---|---|---|
| Usuarios | **Solo lectura** | Identificar la cuenta autenticada (`GET /users/me`) |
| Facturación de una venta | **Solo lectura** | Núcleo del portal: ingresos, movimientos, saldos, detalle de facturación |
| Métricas del negocio | **Solo lectura** | Reportes de operaciones, información fiscal, balances |
| Ventas y envíos de un producto | **Solo lectura** ⚠️ | Consultar órdenes y envíos. ⚠️ VERIFICAR en el panel que quedó en lectura y no en lectura/escritura |
| Comunicaciones pre y post ventas | Sin acceso | El portal no envía ni lee mensajes |
| Publicación y sincronización | Sin acceso | El portal jamás debe tocar publicaciones |
| Publicidad de un producto | Sin acceso | Las campañas de Ads se gestionan fuera de este portal |

> Nota (validación vs skill): "Ventas y envíos" es el **permiso funcional clave**
> — sin él, orders/shipments devuelven 403 `PA_UNAUTHORIZED_RESULT_FROM_POLICIES`.
> Quedó habilitado en solo lectura, que es todo lo que el portal necesita.

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

- [ ] **Registrar la callback URL** de notificaciones en el panel (arriba, §4) —
      en el formulario quedó pendiente.
- [ ] **Confirmar el redirect URI registrado** en el panel. El endpoint de
      callback OAuth del backend aún NO existe (se implementa en Fase 1);
      propuesta: `https://reluvsa-dropshipping-production.up.railway.app/api/ml/oauth/callback`.
      Lo que se registre y lo que se implemente deben coincidir EXACTO.
- [ ] Obtener **App ID (Client ID) + Client Secret** y guardarlos como env vars
      en Railway (`ML_CLIENT_ID`, `ML_CLIENT_SECRET`) — nunca en el repo.
- [ ] Verificar en el panel que "Ventas y envíos" quedó en **solo lectura**.
