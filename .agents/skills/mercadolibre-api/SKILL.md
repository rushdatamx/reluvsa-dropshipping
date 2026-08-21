---
name: mercadolibre-api
description: Experto en la API de Mercado Libre (MLM/México) para la migración del portal RELUVSA de reportes Excel a llamadas API. Usar cuando se trabaje con OAuth de ML, /orders/search, /shipments, multi-origen/depósitos, webhooks/notificaciones, rate limits, o el mapeo Excel↔API de ventas y colecta. Contiene la referencia verificada de la doc oficial (jul-2026) y el procedimiento paso a paso de cada llamada.
---

# API de Mercado Libre — Referencia experta para el portal RELUVSA

Investigado y verificado contra la documentación oficial (`developers.mercadolibre.com.mx`) el 2026-07-16.
Cada afirmación marcada ✅ fue leída textualmente en la doc; ⚠️ NO VERIFICADO = validar empíricamente.
La doc de ML cambia sin aviso (deprecaron `estimated_handling_limit` en 05/2025, PUT solo-precio rechazado desde 03/2026): **re-validar endpoints críticos al implementar**.

---

## 1. Setup y autenticación (OAuth 2.0)

### Crear la aplicación (una sola vez)
1. DevCenter → "Mis aplicaciones" → "Crear nueva aplicación". ✅ En México la app solo se crea **tras validación de los datos del titular** (deben coincidir con el registro). Crear bajo la entidad legal correcta (RushData o RELUVSA — decidir).
2. Configurar: nombre único, descripción (≤150 chars, se muestra al autorizar), **redirect URI HTTPS obligatoria** (sin partes variables), PKCE recomendado (si se activa, `code_challenge` es obligatorio), scopes `read` + `offline_access` (+ `write` si se actualizará stock).
3. **Habilitar el permiso funcional "Ventas y envíos"** (orders, shipments, claims, returns) en la config de la app — es autoservicio. Sin él → 403 `PA_UNAUTHORIZED_RESULT_FROM_POLICIES`. ✅ **NO se requiere aprobación de ML ni del KAM** para orders/shipments.
4. Guardar Client ID (APP_ID) y Secret. El Secret se puede rotar con coexistencia de hasta 7 días.

### Flujo de autorización
```
1) Navegador del TITULAR de la cuenta (cuenta principal — un operador/colaborador da error invalid_operator_user_id):
   https://auth.mercadolibre.com.mx/authorization?response_type=code&client_id=$APP_ID&redirect_uri=$URL&state=$RANDOM[&code_challenge=...&code_challenge_method=S256]

2) Canje del code (params por BODY, x-www-form-urlencoded):
   POST https://api.mercadolibre.com/oauth/token
   grant_type=authorization_code&client_id=...&client_secret=...&code=...&redirect_uri=...[&code_verifier=...]
   → {access_token, token_type, expires_in, scope, user_id, refresh_token}

3) Refresh (MISMO endpoint):
   grant_type=refresh_token&client_id=...&client_secret=...&refresh_token=...
```

### Reglas de tokens (críticas)
- ✅ Access token: **6 horas** según texto normativo, PERO el ejemplo oficial muestra `expires_in: 10800` (3 h) → **usar siempre el `expires_in` de la respuesta, nunca hardcodear**.
- ✅ Refresh token: **6 meses**, **de un solo uso, rotado en cada refresh** — persistir el nuevo **atómicamente** antes de usarlo; perderlo = re-autorizar con el titular.
- ✅ Invalidan tokens antes de expirar: cambio de password del usuario, rotación del Secret, revocación por el usuario, **4 meses sin llamadas a la API**.
- ✅ Token siempre por header `Authorization: Bearer ...`, nunca por query string.
- `invalid_grant` → rehacer la autorización completa con el titular.
- Auditoría: `GET /applications/{id}/grants`. Revocar: `DELETE /users/{uid}/applications/{app_id}`.

---

## 2. Orders API (sustituye el Excel de Ventas ML)

```
GET /orders/search?seller=$SELLER_ID&order.date_created.from=...&order.date_created.to=...
```
- Filtros: `order.status`, `order.date_created`, `order.date_closed`, **`order.date_last_updated`** (usar para sync incremental), `tags`/`tags.not`, `q`, `sort=date_desc`.
- Paginación `{total, offset, limit}`, default limit=50. ⚠️ Máximo de limit y tope de offset NO documentados — validar.
- ✅ **Ventana histórica: 12 MESES.** No hay backfill documentado (`/orders/search/archived` no está en la doc vigente ⚠️). **Sincronizar cuanto antes.**
- ✅ HTTP 206 + header `X-Content-Missing` si falta una sección de la respuesta.
- ⚠️ Venta "no concretada" por el vendedor se ve "Cancelada" en el front pero por API queda `status: confirmed`.

### Mapeo orden → `ventas_ml` (BD del portal)
| Campo API | Campo BD | Nota |
|---|---|---|
| `id` | `num_venta` (PK) | Verificar formato vs "# de venta" del Excel |
| `date_created`/`date_closed` | `fecha_venta` | ISO directo — ya no hay que parsear español |
| `status` | `estado` | Enum API ≠ texto del Excel (mapear) |
| `order_items[].item.seller_sku` | `sku` | ✅ El atributo `SELLER_SKU` es el vigente; `seller_custom_field` es legacy |
| `order_items[].item.title` | `titulo` | Clave del cruce fecha+título y del matcher fuzzy |
| `order_items[].quantity` | `unidades` | Sin la ambigüedad de las 3 columnas "Unidades" del Excel |
| `total_amount` | `total` | |
| `order_items[].stock.store_id` + `network_node_id` | `deposito` | ⭐ Multi-origen: cruzar contra stores (ver §5) |
| `buyer` | `comprador` | 🔴 RESTRINGIDO: la doc vigente solo da `{id}`. Nombre → `shipment.destination.receiver_name` |
| `shipping.id` | → shipment | La orden solo trae el ID del envío |
| `tags` (`delivered`, `fraud_risk_detected`...) | flags | `fraud_risk_detected` = NO despachar |
| `pack_id` | — | Carrito multi-ítem = varias órdenes bajo un pack; `GET /packs/$PACK_ID` |

RFC/datos fiscales del comprador (si se necesitara): `buyer.billing_info.id` → `GET /orders/billing-info/MLM/$BILLING_INFO_ID` (el viejo `/orders/{id}/billing_info` está deprecado, da 404).

---

## 3. Shipments API (sustituye el Excel de Colecta)

Dos formas:
- **Nueva**: `GET /shipments/$SHIPMENT_ID` con **header obligatorio `x-format-new: true`**.
- **Legacy (más eficiente para nuestro caso)**: `GET /orders/{order_id}/shipments` — en UNA llamada: `status_history` (date_handling, date_ready_to_ship, date_shipped, date_delivered...), `date_first_printed`, `delay[]`, `logistic_type`, `sender_address`, `receiver_address` (con `receiver_name`), `tracking_number`.

### Sub-recursos clave
| Recurso | Da | Uso en el portal |
|---|---|---|
| `GET /shipments/{id}/sla` | ✅ `{status: on_time\|delayed\|early\|insuficient_info, expected_date}` | ⭐ **Sustituye directo `cumplio_sla` (col L)** — ML ya lo calcula. No aplica a cancelados/fulfillment |
| `GET /shipments/{id}/lead_time` | fechas estimadas/límite, `buffering.date` (menciona "Drop Shipping, Cross Docking") | `estimated_handling_limit` deprecado 05/2025 → usar /sla |
| `GET /shipments/{id}/delays` | `[{type: handling_delayed\|sla_delayed\|shipping_delayed, date}]`, 404 si no hay | Detalle del retraso |
| `GET /shipments/{id}/history` | cronología `{status, substatus, date}` | Momento real de `picked_up` (colecta ejecutada) |

### Mapeo shipment → `envios_colecta` (BD del portal)
| Campo API | Campo BD | Nota |
|---|---|---|
| `shipment.id` | `num_envio` (PK) | = `orders.shipping.id` |
| orden asociada | `num_venta` / `num_venta_ml` | ⭐ El cruce venta↔envío es DIRECTO por ID — ya no se necesita el fuzzy fecha+título |
| `origin.shipping_address` (+ `origin.node.node_id`) | `lugar_indicado` → `proveedor_id` | ⭐ = la **columna J** (la regla de Gaby). Estructurado, con node_id — mejor que texto libre |
| — | `lugar_real` (col K) | 🔴 **NO EXISTE en la API** (confirmado por ausencia). No importa: la regla vigente usa J. Sustituto parcial: substatus del /history |
| `/sla` → `status` | `cumplio_sla` | `on_time`/`early` → 1, `delayed` → 0 |
| `logistic.type` | — | `cross_docking` = colecta; `xd_drop_off` = Places; `fulfillment` = Full; `self_service` = Flex |
| `destination.receiver_name` | (comprador) | Dirección oculta hasta pago confirmado; `receiver_phone` solo en ME1 |

- ⚠️ En **cross_docking** el "despachado" es `status: ready_to_ship` + substatus `picked_up` — NO `shipped`.
- Catálogo completo de estados: `GET /shipment_statuses`.
- Colectas programadas: `GET /users/{uid}/shipping/schedule/cross_docking` (solo el schedule VIGENTE; no hay histórico de colectas ejecutadas 🔴).

---

## 4. Items API

- `GET /items/{id}` — con token del dueño da `available_quantity` exacto (público = por rangos).
- Multiget: `GET /items?ids=...&attributes=...` — **máx. 20 IDs**.
- Listar ítems del seller: `GET /users/{uid}/items/search` (limit máx. 100); >1000 ítems → `?search_type=scan` + `scroll_id` (expira a los **5 min**; scrolls abandonados generan 429).
- Buscar por SKU: `?seller_sku=$SKU`.
- Actualizar stock: `PUT /items/{id}` `{"available_quantity": X}` — PERO si la cuenta tiene tag `warehouse_management` (multi-origen), se usa `PUT /user-products/{up_id}/stock/type/seller_warehouse` con header `x-version` (409 si versión vieja).
- ⚠️ PUT de variaciones: las variaciones omitidas SE BORRAN. ⚠️ Desde 03/2026, PUT que solo cambie `price` con automatización de precios activa → 400.

---

## 5. Multi-origen / depósitos (la columna "Depósito" del Excel) ⭐

1. **Detectar**: `GET /users/{uid}` → tags `warehouse_management` y `multiwarehouse`. La activación del esquema la controla ML.
2. **Catálogo de depósitos** (MATRIZ/KIM/CAUPLAS/VAZLO/AG/KG): `GET /users/{uid}/stores/search?tags=stock_location` → `{id, description, location, network_node_id, services.stock_location}`. `description` = el nombre que el Excel mostraba en "Depósito". Crear/editar depósitos: solo por panel (Ventas → Preferencias → Mis depósitos), no por API.
3. **De qué depósito salió cada venta**: `order_items[].stock.store_id` (en la orden) o `shipment.origin.node.node_id` (en el envío) → cruzar contra el catálogo de stores.
4. **Stock por depósito**: `GET /user-products/{up_id}/stock` → `locations[]` `{store_id, network_node_id, quantity}`.

**Primera verificación al tener token**: si la cuenta ES multi-origen, la asignación de proveedor se vuelve estructurada (adiós heurística de texto de la col J y gran parte de las reasignaciones manuales de Gaby).

---

## 6. Notificaciones (webhooks)

- Config en DevCenter: tópicos + callback URL pública. Tópicos para el portal: **`orders_v2`** (recomendado por ML) y **`shipments`** (+ `stock-location` si multi-origen).
- ML manda POST `{_id, resource, user_id, topic, attempts, sent, received}` → responder **HTTP 200 en ≤500 ms** o ML **desactiva los tópicos** (fallback; hay que re-suscribirse). Patrón: encolar → 200 → procesar async → `GET {resource}` para el estado completo (naturalmente idempotente por upsert).
- Reintentos: 1 hora / 8 intentos; perdidas: `GET /missed_feeds?app_id=...` (solo 2 días hacia atrás).
- Estrategia recomendada para el portal: **webhooks como disparador + polling de reconciliación** (p. ej. cada hora `/orders/search?order.date_last_updated.from=...`). Polling solo también funciona, pero hay eventos que solo se conocen por notificación.

## 7. Rate limits y errores

- ✅ La doc NO publica cifras. Control **por Client ID y por endpoint**. Aumento de cuota: se pide al equipo de integraciones comerciales con evidencia de uso.
- 429 (`local_rate_limited`): **backoff exponencial con jitter**, bajar concurrencia, multiget, distribuir en el tiempo, cerrar scrolls.
- 403: app bloqueada, permiso funcional faltante, IP no permitida (la app puede restringir IPs), scopes inválidos, token de otro usuario.

## 8. Pruebas

- **No hay sandbox.** Usuarios de prueba: `POST /users/test_user` `{"site_id":"MLM"}` → `{id, nickname, password}`. Máx. 10 por cuenta, se borran a los 60 días sin uso, **no hay endpoint para listarlos** (guardar credenciales). Compra/venta solo entre test users.

## 9. Procedimiento de sincronización propuesto (pseudocódigo)

```
# Sync incremental (job cada N min o disparado por webhook):
1. token = refresh_si_expirado()          # usar expires_in real; persistir refresh_token nuevo ATÓMICO
2. orders = GET /orders/search?seller=$UID&order.date_last_updated.from=$ULTIMA_SYNC&sort=date_desc
   (paginar por offset; si 429 → backoff exponencial + jitter)
3. por cada order:
   upsert ventas_ml (clave num_venta=order.id)      # misma lógica de upsert del parser actual
   ship_id = order.shipping.id
   ship = GET /orders/{order.id}/shipments           # legacy: todo en 1 llamada
   sla  = GET /shipments/{ship_id}/sla
   upsert envios_colecta (clave num_envio=ship_id):
     lugar_indicado ← origin (node_id → store.description → LUGAR_A_BODEGA → proveedor_id)
     cumplio_sla    ← sla.status (on_time/early=1, delayed=0, insuficient_info=NULL)
     num_venta_ml   ← order.id  (cruce DIRECTO, confianza 1.0)
   respetar lugar_override existente (regla actual)
4. recruzar_conceptos_sin_match()                    # cruce retroactivo de facturas (ya existe)
5. persistir ULTIMA_SYNC
```

## 10. Gaps confirmados / preguntas abiertas

1. ¿Cuenta RELUVSA multi-origen? (verificar tags al primer token; si no, preguntar a ML cómo activarlo).
2. Backfill >12 meses: sin vía documentada — pedir al KAM último corte histórico de reportes.
3. Enmascaramiento de datos del comprador post-venta: sin plazo documentado → **persistir todo al sincronizar, no confiar en re-consultas tardías**.
4. Cifras de rate limit y máximos de paginación de /orders/search: validar empíricamente.
5. Col K "Lugar real": no existe en API (irrelevante: la regla usa J).
6. Histórico de colectas ejecutadas: no hay endpoint (solo schedule vigente).

## Fuentes (doc oficial)

- Auth: `/es_ar/autenticacion-y-autorizacion`, `/es_mx/recomendaciones-de-autorizacion-y-token`
- App: `/es_ar/crea-una-aplicacion-en-mercado-libre-es`, `/es_ar/permisos-funcionales`
- Orders: `/es_ar/gestiona-ventas`, `/es_ar/gestion-packs`, `/es_ar/facturacion-billing-info`
- Shipments: `/es_ar/envios`, `/en_us/shipping`, `/en_us/shipment-handling`, `/en_us/shipping-colectas-places`, `/en_us/mercadoenvios-mode-2`
- Items/stock: `/es_ar/items-y-busquedas`, `/es_ar/variaciones`, `/es_ar/producto-sincroniza-modifica-publicaciones`, `/es_ar/stock-multi-origen`, `/es_ar/stock-distribuido`
- Webhooks: `/es_mx/productos-recibe-notificaciones`
- Límites: `/es_mx/rate-limit-error-429`, `/es_mx/error-403`
- Pruebas: `/es_ar/realiza-pruebas`
