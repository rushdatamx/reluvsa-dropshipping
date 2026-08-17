# 🛒 BUG A — el envío del carrito no llegaba a las ventas hermanas

> **Estado: ✅ ARREGLADO** (2026-08-17). Reportado por Gaby. Era la **tarea #1 abierta** del
> Módulo 1 y la única grande que no dependía de terceros.
>
> Leer junto a `docs/estado-cruce-factura-venta.md` (el índice del bloque factura↔venta).

---

## 1. Cómo lo reportó Gaby

> *"Buenos días! no recuerdo si me confirmó que esto se solucionó pero en los reportes de
> venta este número de venta sólo viene asociado a un sku cuando la venta es de 2 skus"*

Con dos capturas: el portal de ML mostrando la venta `#2000014490469643` como
**"Paquete de 2 productos"** (`CAU3832` $113.45 + `CAU4218` $129.21), y el reporte del
portal mostrando **una sola fila**, la de `CAU4218` con su factura `970096782 CD`.

Su lectura del síntoma era exacta: el número aparecía ligado a un solo SKU.

---

## 2. La causa (verificada contra la BD de prod)

Las **dos** ventas estaban cargadas. Lo que faltaba era el envío:

| order.id | pack_id | SKU | envío | proveedor | factura |
|---|---|---|---|---|---|
| `2000017891678512` | 2000014490469643 | CAU4218 | ✅ `47748773289` | CAUPLAS | ✅ `970096782 CD` |
| `2000017891680168` | 2000014490469643 | CAU3832 | 🔴 **ninguno** | 🔴 ninguno | 🔴 ninguna |

**Mercado Libre crea UN SOLO envío por carrito y lo cuelga de UNA SOLA de las N órdenes.**
Las demás quedan sin envío → sin proveedor → **invisibles para el matcher**, que sólo busca
candidatas con `WHERE e.proveedor_id = ?`. Sin envío tampoco tenían logística ni SLA, y en
la pestaña Ventas salían como *"Sin envío"* — **sin selector de bodega**, así que Gaby no
podía ni corregirlas a mano.

### Magnitud medida en prod

- **796 packs multi-venta = 1,714 ventas**, de las cuales **918 sin envío**.
- De los 1,073 pares de ventas que comparten pack, **1,073 comparten depósito y 0 difieren**
  → un carrito nunca trae dos proveedores. Eso es lo que hace correcto compartir el envío.

---

## 3. La solución: se propaga el VÍNCULO, no la FILA

Se agregó la columna **`envios_colecta.pack_id`** (migración idempotente que la rellena
desde la venta ya cruzada) y una condición SQL compartida en
**`backend/services/envio_pack.py`**:

```sql
(   e.num_venta_ml = v.num_venta                      -- cruce directo (el caso normal)
 OR (e.pack_id IS NOT NULL AND v.pack_id IS NOT NULL
     AND e.pack_id = v.pack_id) )                     -- mismo paquete físico
```

Con eso los **5 pasos del matcher** ven las ventas hermanas sin tocar su lógica de cruce.

### 🔴 Por qué NO se duplicaron filas de envío

La alternativa "obvia" era insertar un envío por venta del pack. **Eso rompe el SLA:** las
métricas cuentan `COUNT(*) FROM envios_colecta`, así que un carrito de 6 que llega tarde
pesaría **6 veces** contra el proveedor. Es exactamente lo que Gaby pidió evitar:

> *"como 1 retraso porque en general me cuenta toda la venta como 1 retrasada"*

Al propagar el vínculo, `envios_colecta` conserva **una fila por envío real** y el SLA sigue
contando una vez por paquete **sin tocar `metricas.py`** (que está blindado, commit `935c998`).

---

## 4. Las 5 trampas fijadas en `backend/scripts/test_envio_pack_carrito.py` (24/24)

1. 🔴 **El SLA no se infla.** El test verifica `total_envios == 1` con 2 ventas en el
   carrito, y que `envios_colecta` sigue con UNA fila. Es la garantía que le dimos a Gaby.
2. 🔴 **NUNCA se cruza `pack_id` contra `num_venta`.** Hay **268 números que son `order.id`
   de una venta y `pack_id` de OTRA** (misma trampa ya fijada para el albarán, `3b2cf79`):
   un OR entre las dos llaves heredaría el envío equivocado, con confianza alta y sin que
   nadie lo revise. El vínculo es **siempre `pack_id` contra `pack_id`**.
3. 🔴 **Dos `pack_id` NULL no se unen.** Sin la guarda `IS NOT NULL` en ambos lados, un
   `IS` mal escrito uniría las ~22k ventas sin pack contra los ~21k envíos sin pack:
   producto cartesiano que tumba el portal, no un error de negocio sutil.
4. **Una venta puede resolver a más de un envío** (en prod hay 2: reexpediciones). El
   listado agrupa por `v.num_venta` y, mediante el truco documentado de `MAX()` en SQLite,
   **elige de forma determinista el envío que SÍ trae proveedor**. Sin agrupar, la venta
   saldría en 2 filas y se leería como duplicada.
5. **El desempate del matcher es determinista.** Las hermanas comparten fecha **al segundo**
   (1,071 de 1,073 pares), así que `ORDER BY v.fecha_venta DESC LIMIT 1` era un **empate**
   que SQLite podía resolver distinto entre corridas. Ahora ordena por
   `fecha_venta DESC, num_venta DESC`. No hace el cruce "más correcto": lo hace
   **reproducible**, que es la condición para poder auditarlo.
   ℹ️ Lo que de verdad separa a las hermanas es el SKU, y de eso ya se encargan los pasos
   1-2: **1,037 de 1,073 pares tienen SKU distinto**. Quedan 31 pares con el mismo SKU,
   donde no existe dato que las distinga.

### Verificado por mutación (5 mutaciones lo ponen en rojo)

Un test que pasa sin ejercitar el código no prueba nada — el error del `total_neto` y de la
liberación de ocupantes, dos veces en este proyecto. Se comprobó que el test **falla de
verdad** al: (1) quitar el vínculo por pack, (2) meter el OR prohibido, (3) quitar la guarda
`IS NOT NULL`, (4) hacer que el SLA cuente por venta, (5) quitar el desempate. La mutación 5
es la más valiosa: destapó la no-determinación (`{'V-GEM-1','V-GEM-2'}`) que si no habría
llegado a producción como un bug silencioso e irreproducible.

---

## 5. Lo que Gaby va a ver

- Las **918 ventas** que decían *"Sin envío"* ahora muestran **su envío, proveedor,
  logística y SLA** (los que el paquete tenga).
- **Marca de carrito nueva:** las N ventas de un pack muestran el **mismo número** (el
  `pack_id`, que es el que ML enseña) y ahora también el mismo proveedor y SLA, así que sin
  marca se leerían como filas repetidas. Se agregó un badge **"🛒 N productos"** y la
  columna **"Productos del paquete"** en el CSV. El **SKU de cada fila** dice qué pieza es
  cada una — que es justo lo que ella echaba en falta.
- El filtro **"Cruce con colecta → sin envío"** devolverá **muchas menos** filas, y
  **"con envío"** muchas más. Es el efecto esperado del arreglo.

### 🔴 Lo que este arreglo NO resuelve — hay que ser honestos al reportarlo

**Sólo 42 de los 796 envíos de pack tienen proveedor asignado.** Los otros 754 no lo tienen
(133 son FULL, donde es correcto que no lo tengan; el resto es el residuo ya documentado del
Hallazgo 1). Propagar el envío hace **visibles** las ventas hermanas y les da logística,
pero **una venta cuyo paquete no tiene bodega sigue sin poder cruzar factura**.

La diferencia práctica: antes esas ventas no tenían ni envío ni forma de arreglarse; ahora
**aparecen con el selector "⚠ Asignar bodega"** y Gaby puede resolverlas a mano, y el
recruce retroactivo cierra el cruce solo. **No decirle que "los carritos ya quedaron
resueltos"** — quedó resuelta la mitad que dependía de nosotros.

---

## 6. Verificación

- **19 suites de regresión en verde**, incluida la nueva (24/24).
- **`api-guardian` APROBADO** 13/13 puntos: cero llamadas de red nuevas (el `pack_id` sale
  de `order.pack_id`, dato que el sync ya recibía), inyección SQL imposible (la condición es
  una constante literal probada por AST; todo valor de usuario sigue vinculado), migración
  idempotente sin pérdida de datos, `NO_ES_FULL` intacta, paginación consistente.
- El guardián confirmó además que el cambio de `COUNT(*)` a **`COUNT(DISTINCT fc.id)`** en la
  métrica de errores **arregla un doble conteo real** que el JOIN nuevo habría introducido.
- Build del frontend (CRA) limpio.

⚠️ **Invariante anotada en `_migrar_envio_pack_id`:** la migración es un **espejo** de
`ventas_ml.pack_id`, no un COALESCE. Hoy es seguro porque el sync protege la fuente de verdad
con COALESCE y es el único escritor. Si algún día algo puebla `envios_colecta.pack_id` por
fuera del sync, esta migración lo borraría en el siguiente arranque.

---

## 7. El patrón que ya se repitió CUATRO veces

Cuando un dato de ML "no cruza", preguntarse **cuál de los dos números es** (`order.id` vs
`pack_id`) antes de asumir que el dato falta:

| # | Caso | Commit |
|---|---|---|
| 1 | La búsqueda de Ventas no encontraba el # que ML muestra | `f325b2f` |
| 2 | El # de venta de KIM | `9a72d98` |
| 3 | Los albaranes ("79 actualizados, 52 no encontrados") | `3b2cf79` |
| 4 | **Este** — el envío del carrito | — |

Los 4 salieron del mismo malentendido: ML muestra el `pack_id` y guarda el `order.id`.
