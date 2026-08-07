# ⭐ HALLAZGO — El matcher asigna la factura a la venta EQUIVOCADA

> 📍 **El índice del bloque es `docs/estado-cruce-factura-venta.md`.** Este documento es el
> DIAGNÓSTICO de fondo; empieza por el índice si sólo quieres saber qué falta.
>
> **Estado:** BUG B **ARREGLADO** (`f699577`) + los 243 cruces falsos **LIMPIADOS en prod**
> (`bf6b392`, 2026-08-07). 🔴 **BUG A sigue abierto y es la tarea #1.**
> **Fecha:** 2026-08-05 / 08-07. **Reportado por:** Gaby (vía Mario).
> **Leer esto ANTES de tocar `backend/services/matcher.py`.**
>
> ✅ **2026-08-07: Gaby ya le pidió a KIM y CAUPLAS que pongan el # de venta en la factura.**
> Se está a la espera de que lleguen facturas con el dato. Las 3 reglas de diseño de ese
> cruce están en el índice §5 y en el CLAUDE.md §8.

---

## ✅ LO QUE SE HIZO (2026-08-07) — leer antes que el resto del documento

**Se simuló contra copia verificada de la BD de prod y se implementó el filtro por fecha.**
`_filtro_fecha` en `matcher.py` descarta las ventas POSTERIORES a la factura (y las más
viejas que `VENTANA_FACTURACION_DIAS`), aplicado a **los 4 pasos y al recruce**. El parámetro
`fecha_factura` es **opcional**: sin él, el comportamiento es el de siempre.

**Resultado, con el CÓDIGO REAL sobre copia de prod, contra los 143 casos de Gaby:**

| | antes | después |
|---|---|---|
| aciertos | 26 | **64** |
| errores | 76 | **29** |
| falsos "✓ Facturado" sobre mercancía no enviada | 28 | **10** |

Fijado en `backend/scripts/test_fecha_factura_venta.py` (13/13). Las 14 suites pasan.

### ⚠️ 3 correcciones a este documento, medidas contra prod

1. **Las cifras de §5 (CAUPLAS 62% / KIM 27%) NO son reproducibles.** Describen el ESTADO de
   prod, que es el sedimento de meses de eventos intercalados (facturas en 9 lotes, ventas
   por sync progresivo, `recruzar` corriendo muchas veces). Al re-correr el criterio viejo
   sobre el universo de hoy, KIM da **5%**. El 27% tuvo suerte histórica: **el bug era peor
   de lo que este documento decía.** Por eso la comparación válida es viejo-vs-nuevo en
   igualdad de condiciones, nunca contra el estado de prod.
2. ❌ **La vía 6.1 (agrupar CAUPLAS por `M######`) es FALSA.** Simulada: **0 casos** de
   diferencia. 45 de 75 pedidos tienen un solo concepto, cada `M######` vive en **una sola
   factura** (75/75), y el pedido `M2650288` reparte sus conceptos entre **2 packs
   distintos**. Es un folio interno de CAUPLAS, no un ancla al carrito. **No implementarla.**
   CAUPLAS llega a 73-89% sólo con la fecha.
3. **§6.3 decía que "la fecha sola no basta". Es al revés: la fecha es lo que más aporta**
   (26→64 aciertos). Lo que no basta es la fecha **para KIM**, por la razón de abajo.
   El temor de Gaby ("falla si el proveedor se atrasa 5 días") no se materializa: en sus
   propios cruces correctos, CAUPLAS factura a 0-3 días y KIM a 0-5, **sin un solo caso
   por encima de 5**.

### ⬜ Lo que NO se resolvió y por qué

**KIM se topa con ambigüedad irreducible.** Emite **~23 facturas/día** (hasta 56 en un día) y
hay **141 combinaciones (SKU, día) con varias ventas**. El caso que lo prueba: `K29936` y
`K29944` se emitieron **el mismo día con 25 minutos de diferencia** para el mismo SKU. No hay
criterio de fecha que las separe porque el dato para separarlas no existe en el portal.

→ **Se le pidió a Gaby que KIM y CAUPLAS pongan el # de venta en la factura.** Ver las 3
reglas de diseño en el CLAUDE.md (§8, "Cruce por # de venta") — en particular: **NUNCA buscar
en order.id y pack_id a la vez**, hay 268 números que son ambas cosas para ventas distintas.

**Alternativa más barata, sin pedirle nada al proveedor: el albarán.** El uploader existe
desde junio y hay **0 ventas con albarán** en prod. Si el albarán aparece en las facturas de
KIM, resuelve lo mismo. **No se pudo verificar por falta de datos cargados.**

---

## 0. Resumen en 5 líneas

Cuando un mismo SKU se vende **varias veces**, el matcher le asigna la factura a la
**venta pendiente más antigua**, no a la que le toca. Medido contra el cruce manual de
Gaby: **CAUPLAS acierta 62%, KIM acierta 27%**. La causa es una sola línea de criterio
(`ORDER BY v.fecha_venta DESC` + `LIMIT 1`) repetida en los 4 pasos del matcher. Hay un
segundo bug independiente (envíos de carrito no propagados, 894 ventas). **Ninguno de los
dos está arreglado.**

---

## 1. Cómo empezó

Gaby reportó la venta `2000014310099713` (pack_id): *"se supone lleva 2 SKUs, pero en la
página sólo se ve 1"*. Screenshot de ML: carrito de 2 piezas CAUPLAS, $676.32, entregado.

Al investigar salieron **DOS bugs distintos e independientes**. No confundirlos.

---

## 2. ⚠️ HIPÓTESIS DESCARTADAS — no volver a investigarlas

Se perdió tiempo en las 3. Están aquí para que nadie las repita.

| # | Hipótesis | Por qué es FALSA | Evidencia |
|---|---|---|---|
| 1 | **El sync pierde ítems**: `sync_ml.py:449` toma `items[0]` y descarta el resto → se perdería el 2º SKU | **CERO casos en prod.** Las órdenes de ML traen 1 solo ítem; los carritos llegan como **N órdenes separadas** con el mismo `pack_id` | Muestra de 60 ventas con `unidades>1` consultadas contra la API: **60/60 con un solo `order_item`** |
| 2 | **"Los packs casi no se facturan" (99.5% sin factura)** — parecía anomalía | **Falso alarmismo por baseline inventado.** La tasa real de facturación de TODO el portal es **1.5%** (876 de 57,684), porque sólo hay 775 facturas cargadas y desde el 27-may. Los packs están en línea con el resto | `SELECT COUNT(DISTINCT num_venta_match) FROM factura_conceptos` = 876 |
| 3 | **"El problema son los kits"** | **Incompleto.** En CAUPLAS 12/14 fallos eran kits, pero **KIM falla igual sin kits** (SKUs limpios tipo `96591481-Z`). El bug es de *elección de candidata*, no de kits | Cruce KIM: 35 errores, la mayoría NO son `KIT*` |

⚠️ **La #1 sigue siendo código frágil** (si ML algún día manda una orden multi-ítem, se
pierde en silencio) pero **hoy no causa daño**. Blindar si se toca el archivo; no es urgente.

---

## 3. BUG A — El envío del carrito no se propaga (894 ventas)

### Qué pasa
ML crea **UN solo shipment por carrito** y lo cuelga de **UNA sola** de las N órdenes. Las
demás ventas del pack quedan **sin envío → sin proveedor → invisibles para el matcher**
(que sólo busca `WHERE e.proveedor_id = ?`).

### Evidencia — el caso de Gaby
| order.id | pack_id | SKU | envío | factura |
|---|---|---|---|---|
| 2000017706291186 | 2000014310099713 | `CAU11608` (Figo) | **1** | **1** |
| 2000017706296700 | 2000014310099713 | `CAU19535` (Jeep) | **0** | **0** |

Verificado contra la API: ambas son órdenes de 1 ítem, mismo pack, entregadas el 4-ago.

### Magnitud (medido en prod, 2026-08-05)
- **776 packs** multi-venta = **1,670 ventas**.
- **894 de ellas sin envío** = el **67%** de las 1,343 ventas sin envío de todo el portal.
- **776 de 776 packs** siguen el patrón: el envío está en **exactamente 1** de sus N ventas.
  Cero excepciones (681 packs de 2, 79 de 3, 11 de 4, 3 de 5, 2 de 6).

### Consecuencia en cadena
Sin envío → sin proveedor → el matcher **no puede verla nunca** → su factura se va a otra
venta (esto **alimenta el BUG B**) → la venta queda "Pendiente" para siempre aunque esté
pagada y facturada.

### Decisión de negocio YA TOMADA por Gaby
> *"como 1 retraso porque en general me cuenta toda la venta como 1 retrasada"*

→ **Propagar el envío a las N ventas del pack** (para que el matcher las vea), pero **contar
el SLA una sola vez por envío**, NO por venta. Sin esa segunda parte, un carrito de 6 que
llega tarde pesaría 6 veces en el SLA del proveedor.

⚠️ Eso toca `routers/metricas.py`, blindado el 2026-08-05 (commit `935c998`). Releer sus 3
trampas antes de modificarlo.

---

## 4. BUG B — La factura se asigna a la venta equivocada ⭐ (el grande)

### La causa raíz, con archivo y línea
`backend/services/matcher.py` — **los 4 pasos** eligen candidata igual:

```sql
ORDER BY v.fecha_venta DESC
LIMIT 1
```

Líneas: **69-70, 201-202, 221, 311-312, 341**.

Con `fc.id IS NULL` (sólo ventas sin factura), el efecto real es: **de todas las ventas
pendientes de ese SKU, se queda con una arbitraria** — en la práctica la más antigua sin
cruzar se lleva la factura nueva. **El criterio no considera la fecha de la FACTURA contra
la fecha de la VENTA.** Ese es el bug.

### Evidencia 1 — Facturas literalmente intercambiadas (KIM)
```
2000017743440322  96591481-Z  portal=K29971  gaby=K29973
2000014346474159  96591481-Z  portal=K29973  gaby=K29971   ← INVERTIDAS
```
**4 pares** así en la muestra de KIM.

### Evidencia 2 — Sesgo sistemático hacia lo viejo
En **26 de 34** errores de KIM el portal eligió un folio **ANTERIOR** al correcto. No es
ruido, es dirección.

### Evidencia 3 — Se concentra en SKUs repetidos
`96591481-Z` mal asignado en **6** ventas; `96413748-Z` en **5**. Cuando el SKU se vendió
**una sola vez, acierta**. La ambigüedad es la condición del fallo.

### Evidencia 4 — CAUPLAS: un pedido repartido entre 6 carritos
El concepto `M2650281` cruzó a **6 ventas de 6 packs distintos**, todas `KIT03561`. Un
pedido real no se parte en 6 carritos: el matcher regó los componentes.

### Evidencia 5 — El caso `CAU19535`
Factura del **1-ago** (`cod=19535 M2650520`, folio 970096290) → cruzó a una venta del
**13-jul**. La venta correcta (1-ago, la de Gaby) estaba invisible por el BUG A.
No es falso positivo (mismo SKU/producto) pero **el dinero quedó mal atribuido** e infla el
"tiempo de facturación" de CAUPLAS (~19 días en vez de ~0).

---

## 5. LA MEDICIÓN — cruce manual de Gaby (la fuente de verdad)

Gaby cruzó a mano factura↔venta. **Es lo único que detecta este bug**: el portal cree que
acertó, así que ninguna prueba interna lo revela.

### CAUPLAS — 37 ventas (`ventas_cruces (2).csv`)
| Resultado | n | % |
|---|---|---|
| ✅ Acertó | 23 | **62%** |
| ❌ Factura equivocada | 7 | 19% |
| ⬜ Pendiente (Gaby sí la tiene) | 5 | 14% |
| ~~2 casos~~ | 2 | **DESCARTADOS** ↓ |

⚠️ `CAU11414` y `CAU11316` **NO eran bug**: Gaby subió esa factura *después* de bajar el
Excel. No investigarlos.
⚠️ **El sufijo ` CD` NO es error**: el portal escribe `970096331 CD` (Serie del XML) y Gaby
`970096331`. **Normalizar antes de comparar** o salen "0 coincidencias" (error cometido).
📌 De los 12 fallos reales, **12 involucran `KIT*`** → por eso se creyó (mal) que era de kits.

### KIM — 106 ventas (`VENTAS KIMS .xlsx`)
| Resultado | n | % |
|---|---|---|
| ✅ Acertó | 24 | **23%** |
| ❌ Factura equivocada | 35 | 33% |
| ⬜ Pendiente (Gaby sí la tiene) | 14 | 13% |
| ⚠️ **Factura asignada a venta NO ENVIADA** | **17** | 16% |
| Sin factura aún (correcto) | 16 | 15% |

**Sólo 24 de 90 evaluables = 27% de acierto.** KIM está mucho peor que CAUPLAS.

🔴 **Los 17 "No la envían aun" son lo más grave:** el portal marca **"✓ Facturado" sobre
mercancía que no ha salido**. Esas facturas pertenecen a otras ventas. Le da a Gaby una
señal falsa de "ya está resuelto" y distorsiona el tiempo de facturación de KIM.

📁 **Los 2 archivos de Gaby son PII y NO están en el repo.** Rutas originales:
`~/Downloads/ventas_cruces (2).csv` y
`~/Library/Caches/com.apple.SwiftUI.Drag-*/VENTAS KIMS .xlsx`.
Formato: col `Num factura` = lo que asignó el portal; la **siguiente** columna (en KIM va
**sin encabezado**) = lo que Gaby asigna a mano. En KIM esa columna trae también el texto
libre `"No la envían aun"` — filtrarlo antes de comparar.

---

## 6. VÍAS DE SOLUCIÓN — estado de cada una

### 6.1 CAUPLAS: el `M######` (VERIFICADO ✅, es la vía buena)
El "# orden" que mencionó Gaby **ya viene en la factura y ya lo guardamos**:
```
codigo_prov = "11608 M2650521"   →   SKU 11608  +  pedido M2650521
```
Hoy el matcher **parte el código y TIRA el `M######`** (`_tokens_codigo`).

Es un **identificador de pedido**: `M2650821` aparece en **9 conceptos** de la misma
factura, `M2650798` en 5. **Agrupando por `M######` y cruzando el pedido completo contra
UNA venta, los kits dejan de repartirse.**

⚠️ **Es un folio interno de CAUPLAS. NO se ha verificado que exista en la data de ML**
(Gaby preguntó justo eso). Sirve para agrupar conceptos **entre sí**, que basta para el fix.

### 6.2 KIM: **NO tiene `M######`** (VERIFICADO ✅ — vía distinta)
- Sólo **16 de 884** conceptos KIM traen 2 partes. Sus códigos son limpios (`96591481-Z`).
- **796 de 840 facturas KIM tienen 1 solo concepto** = una factura por venta (confirma lo
  que dijo Gaby).
- ⚠️ **Esto matiza lo que ya se le dijo a Gaby** ("nos ajustamos nosotros, no hay que
  pedirle nada a los proveedores"): **cierto para CAUPLAS, NO resuelto para KIM.**

### 6.3 Ordenar por proximidad de fecha (SIN VERIFICAR ⬜)
Criterio de Gaby: *"por la fecha, normalmente me facturan al día siguiente o el mismo día"*.
⚠️ **Ella misma advierte que su método falla** cuando el proveedor se atrasa 5 días.
Un mismo SKU tiene hasta **28 facturas** en 2 meses (`96413748`) → la fecha sola no basta.
**Falta simular** contra copia de prod: ¿arregla los 35 sin romper los 24 buenos?

### 6.4 El albarán (SIN VERIFICAR ⬜)
Cruzaría 1:1 sin adivinar. **Pero en prod hay 0 ventas con albarán** — el uploader existe
(`parser_albaran.py`, commit `7408bfd`) y **Gaby nunca lo ha usado**. Falta ver si el
albarán aparece en las facturas de KIM.

---

## 7. LO QUE SIGUE (en orden)

1. ⬜ **Simular el fix contra COPIA de la BD de prod** — método obligatorio, el mismo del
   arreglo de kits del 2026-08-05. **Los 23 (CAUPLAS) + 24 (KIM) aciertos actuales NO se
   pueden romper.**
2. ⬜ Decidir criterio por proveedor: CAUPLAS → `M######`; KIM → fecha y/o albarán.
3. ⬜ Arreglar BUG A (propagar envío del pack) — habilita ventas hoy invisibles.
4. ⬜ Ajustar `metricas.py` para contar SLA **por envío**, no por venta (decisión de Gaby).
5. ⬜ Mejora de UI: las N ventas de un pack muestran **el mismo número** (`pack_id`) en la
   columna Venta (`Ventas.jsx:270` pinta `pack_id || num_venta`) → se leen como duplicados.
   **Es lo que Gaby reportó originalmente.**

### Regla de oro (heredada del arreglo de kits)
🔴 **Un cruce falso es PEOR que un pendiente.** El pendiente se ve y se corrige; el falso
dice "ya facturado" y nadie lo vuelve a mirar. **Ante ambigüedad real: NO cruzar.**
Los 17 casos de KIM son exactamente este daño.

---

## 8. Contexto de prod (2026-08-07)

```
57,684 ventas · 55,938 envíos · 775 facturas · 954 conceptos (876 cruzados)
876 ventas con factura = 1.5% del total   <- baseline REAL, no inventar otro
facturas cargadas desde 2026-05-27 (las ventas van desde 2025-07-31)
0 ventas con albarán
```

Consultar prod: ver **§8.ssh del CLAUDE.md** (`railway ssh`, python en
`/opt/venv/bin/python`, BD en `/data/dropshipping.db`). **NUNCA lanzar el sync por SSH.**

---

## 9. Estado de la conversación con Gaby

**Ya se le dijo (y es correcto):**
- Sus 2 productos SÍ están en el portal; uno quedó "suelto" sin envío.
- El "# orden" de CAUPLAS es la pieza que faltaba y **no hay que pedirle nada a los
  proveedores**. ⚠️ **Válido sólo para CAUPLAS** — KIM quedó pendiente de resolver.

**Ella respondió:**
1. CAUPLAS y KIM facturan el carrito completo en **una sola factura**.
2. Un carrito tarde = **1 retraso**, no N.
3. Cruza **por fecha**, y reconoce que falla si el proveedor se atrasa.

**Pendiente con ella:** confirmarle cómo queda KIM (aún no se le ha prometido solución).
**No prometerle nada hasta simular.**
