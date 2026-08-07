# 📍 ESTADO DEL CRUCE FACTURA ↔ VENTA — punto de entrada

> **Última actualización:** 2026-08-07 (tras ejecutar la limpieza de los 243).
> **Este documento es el índice.** Dice qué está cerrado, qué sigue abierto y dónde está el
> detalle de cada cosa. Léelo primero; los otros 3 documentos son el detalle.
>
> ⚠️ **Antes de tocar `backend/services/matcher.py`, leer también
> `docs/hallazgo-cruce-factura-venta.md`** (trae las hipótesis ya descartadas).

---

## 0. En una línea

El portal cruza la factura del proveedor con la venta de ML. Ese cruce **falló de tres
maneras distintas**; dos ya están arregladas y **una sigue abierta**. Además hay una cuarta
causa que **no depende de nosotros**: que los proveedores pongan el # de venta en la factura.

---

## 1. Tablero: qué está cerrado y qué no

| # | Problema | Estado | Depende de |
|---|---|---|---|
| 1 | Los kits no cruzaban (`CAU11370` vs `11370 M2650963`) | ✅ **CERRADO** `1be1f99` | — |
| 2 | **BUG B**: la factura se iba a la venta equivocada (no se miraba la fecha de la factura) | ✅ **CERRADO** `f699577` | — |
| 3 | 243 cruces falsos **ya persistidos** en la BD | ✅ **CERRADO** `bf6b392` (ejecutado en prod) | — |
| 4 | **BUG A**: 1,042 ventas de carrito **sin envío** → invisibles para el matcher | 🔴 **ABIERTO** | nosotros |
| 5 | Los 5 falsos de KIM (facturas del mismo día) | ⬜ **BLOQUEADO** | **los proveedores** |

⚠️ **4 y 5 son independientes.** El # de venta (5) **no arregla** el BUG A (4): una venta sin
envío no tiene proveedor, y el matcher sólo busca `WHERE e.proveedor_id = ?` — no la ve
aunque la factura traiga el número.

---

## 2. Dónde está el detalle de cada cosa

| Documento | Qué contiene |
|---|---|
| `docs/hallazgo-cruce-factura-venta.md` | El diagnóstico de fondo. **Las 3 hipótesis descartadas** (no repetirlas) y la descripción del BUG A |
| `docs/limpieza-cruces-falsos-persistidos.md` | La limpieza de los 243: resultado, garantías, las 3 lecciones de la ejecución |
| `CLAUDE.md` §3 y §8 | Reglas de negocio y las 3 reglas de diseño del cruce por # de venta |

Tests que fijan lo ya arreglado (correrlos antes de commitear cualquier cambio al matcher):
`test_fecha_factura_venta.py` (13/13) · `test_kit_id_interno.py` (20/20) ·
`test_recruce_retroactivo.py` · `test_metricas_excluir_full.py` (13/13).

---

## 3. Dónde quedó la calidad del cruce (medido, no estimado)

Contra el cruce manual de Gaby (143 casos: CAUPLAS 37, KIM 106). **Estado persistido en prod**,
antes y después de la limpieza del 08-07:

| | antes | después |
|---|---|---|
| aciertos | 49 | **59** |
| errores | 43 | **22** |
| falsos "✓ Facturado" sobre mercancía no enviada | 17 | **5** |
| CAUPLAS | 68% | **70%** |
| KIM | 27% | **42%** |

🔴 **Ser honestos al reportarlo:** KIM mejoró mucho pero **más de la mitad de sus cruces
siguen sin coincidir** con lo que Gaby asigna a mano. Parte lo cerrará el # de venta, parte
es el BUG A. **No decirle a Gaby que "ya quedó resuelto".**

ℹ️ **Lo que la limpieza SÍ garantizó** (verificado): que no dañó nada — 0 cruces buenos
alterados, 0 filas borradas. **Lo que NO se verificó:** que los 192 reasignados apunten todos
a la venta correcta. Eso depende de la calidad del matcher; el api-guardian lo marcó
explícitamente como límite de su auditoría.

---

## 4. 🔴 TAREA #1 — BUG A: propagar el envío del carrito

**Es lo de mayor impacto que queda y NO depende de terceros.**

### Qué pasa
ML crea **UN solo shipment por carrito** y lo cuelga de **UNA sola** de las N órdenes del
pack. Las demás ventas quedan **sin envío → sin proveedor → invisibles para el matcher**.

### Magnitud (medida en prod)
- 776 packs multi-venta = 1,670 ventas; **1,042 de ellas sin envío**.
- **776 de 776 packs** siguen el patrón, sin una sola excepción.

### El caso que lo reportó (Gaby)
| order.id | pack_id | SKU | envío | factura |
|---|---|---|---|---|
| 2000017706291186 | 2000014310099713 | `CAU11608` (Figo) | 1 | 1 |
| 2000017706296700 | 2000014310099713 | `CAU19535` (Jeep) | **0** | **0** |

### Decisión de negocio YA TOMADA por Gaby
> *"como 1 retraso porque en general me cuenta toda la venta como 1 retrasada"*

→ **Propagar el envío a las N ventas del pack** (para que el matcher las vea), pero **contar
el SLA una sola vez por envío**, NO por venta. Sin la segunda parte, un carrito de 6 que
llega tarde pesaría 6 veces en el SLA del proveedor.

⚠️ **Toca `routers/metricas.py`, que está blindado (commit `935c998`). Releer sus 3 trampas
antes de modificarlo** — en particular que el filtro de FULL es
`IS NULL OR != 'fulfillment'`, nunca `= 'cross_docking'`.

---

## 5. ⬜ Cuando los proveedores pongan el # de venta

**Estado: pedido a Gaby, ella ya se lo pidió a KIM y CAUPLAS (2026-08-07). Esperando que
lleguen facturas con el dato.**

### Por qué hacía falta pedirlo
KIM emite **~23 facturas al día** y hay **141 combinaciones (SKU, día) con varias ventas**.
El caso que lo prueba: `K29936` y `K29944` salieron el mismo día con **25 minutos** de
diferencia para el mismo SKU. **Ningún criterio de fecha las distingue** porque el dato para
separarlas no existe en el portal. No es un defecto del matcher.

### 🔴 Las 3 reglas de diseño — la medición ya las fijó, no diseñarlo de otro modo

1. **Buscar primero en `num_venta` (order.id). Si hay match exacto, gana y no se sigue.**
2. **Sólo si no hay, buscar en `pack_id`** — y desambiguar con el código de la pieza:
   **778 packs tienen varias ventas** (el caso de Gaby: un pack con una Jeep y una Figo).
3. 🔴 **NUNCA buscar "en order.id O en pack_id" a la vez**: hay **268 números que son
   order.id de una venta y pack_id de OTRA distinta** → devolvería 2 ventas y reintroduciría
   el mismo bug, pero **peor, porque vendría con confianza 1.0 y nadie lo revisaría**.

### Cómo implementarlo
Un **paso 0** en `matcher.py`, antes del código exacto. Hoy **cero conceptos traen números de
12+ dígitos**, así que detectar uno es señal inequívoca de que el proveedor lo puso.
⚠️ **Validar el formato contra el primer XML real antes de confiar** (mismo criterio que con
los kits: el formato asumido resultó equivocado dos veces).

### Alternativa más barata que nadie ha probado
**El albarán.** El uploader existe desde junio (`parser_albaran.py`, commit `7408bfd`) y hay
**0 ventas con albarán** en prod — Gaby nunca lo ha usado. Si el albarán aparece impreso en
las facturas de KIM, resuelve lo mismo **sin pedirle nada a los proveedores**. No se pudo
verificar por falta de datos cargados. **Vale la pena preguntárselo a Gaby antes de esperar
a los proveedores.**

---

## 6. Pendientes menores (no del cruce)

- 🔴 **Rotar la password del admin `gaby@reluvsa.com`** — higiene, pendiente desde junio.
- ⬜ **Avisar POR ESCRITO al cliente** que la app de ML debe quedarse en "Sin acceso" en
  *Publicación y sincronización* del DevCenter.
- ⬜ **UI `/mercadolibre`:** la columna "Procesada" muestra ✅ para notificaciones
  **descartadas** — se lee al revés. Cambiar por la etiqueta "descartada".
- ⬜ **Módulo 2** (publicaciones masivas): nunca iniciado.
- ⬜ **Mejora de UI:** las N ventas de un pack muestran el mismo número (`pack_id`) en la
  columna Venta → se leen como duplicados. **Es lo que Gaby reportó originalmente.**

---

## 7. Método que funcionó — repetirlo en la próxima corrección de datos

Los 3 arreglos de este bloque (kits, fecha, limpieza) salieron bien con el mismo método:

1. **Bajar copia verificada de la BD de prod** (SHA-256), sólo lecturas.
   ⚠️ **Nunca lanzar el sync por SSH** (§8.ssh del CLAUDE.md).
2. **Simular el cambio con el CÓDIGO REAL** sobre esa copia — no reimplementar el criterio.
3. **Medir contra el cruce manual de Gaby**, no contra intuición.
4. **Pasar por el agente `api-guardian`.**
5. Para escribir en prod: **apagar la sync automática** → backup `VACUUM INTO` → ejecutar →
   **reencenderla** → verificar.

### 3 lecciones caras de este bloque

1. 🔴 **Comparar fechas por `date()`, NUNCA por timestamp.** Medido: por timestamp se habrían
   seleccionado 258 conceptos en vez de 243, y los 15 de diferencia eran **cruces legítimos
   con confianza 1.0** (venta 19:02, factura 15:47 del mismo día).
2. 🔴 **Un cruce falso es PEOR que un pendiente.** El pendiente se ve y se corrige; el falso
   dice "ya facturado" y nadie lo vuelve a mirar. **Ante ambigüedad real: NO cruzar.**
3. **El estado de prod NO es reproducible por simulación** (es el sedimento de meses de
   eventos intercalados). Comparar siempre viejo-vs-nuevo en igualdad de condiciones.
