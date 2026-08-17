# 📍 ESTADO DEL CRUCE FACTURA ↔ VENTA — punto de entrada

> **Última actualización:** 2026-08-17 — **CAUPLAS: la factura ya no puede ser anterior a la
> venta que ampara** (`18e0ed4`). Reporte de Gaby con verdad de campo de 22 ventas anotadas a
> mano. El caso que reportó quedó en el folio correcto; **el resto necesita una tanda más
> grande de anotaciones suyas** (ver #7 abajo).
> **Este documento es el índice.** Dice qué está cerrado, qué sigue abierto y dónde está el
> detalle de cada cosa. Léelo primero; los otros documentos son el detalle.
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
| 4 | **BUG A**: ventas de carrito **sin envío** → invisibles para el matcher | ✅ **CERRADO** 2026-08-17 | — |
| 5 | Los falsos de KIM (facturas del mismo día, indistinguibles por fecha) | ✅ **CERRADO** `0161ade` + `0104d32` | — |
| 6 | El ~45% de facturas de KIM **sin # impreso** degrada a fecha **en silencio** | 🟡 **ABIERTO** | **KIMS** (leyenda fija) |
| 7 | **CAUPLAS**: la factura se iba a una venta **posterior a su propia emisión** | 🟡 **PARCIAL** `18e0ed4` | **Gaby** (60-80 ventas anotadas) |

### El #7 (CAUPLAS) se arregló a medias el 2026-08-17 — ver `docs/cauplas-factura-anterior-a-la-venta.md`

Reportado por Gaby: *"esta venta en la pagina hizo cruce con esta fac 970096782, pero en la
factura viene con la 970096819"*. CAUPLAS **reusa el ID interno de la pieza** en todas sus
facturas y sólo cambia el folio de pedido (`M######`); el matcher comparaba nada más el ID
interno y podía llevarse una venta ocurrida **horas después** de emitirse la factura.

**Lo que se arregló:** el motor ahora **PREFIERE** las ventas que ya existían cuando se emitió
la factura (`_orden_candidatas`), y un script corrige hacia atrás los cruces imposibles.
El caso que reportó Gaby ya sale con el folio correcto; **0 cruces buenos rotos**.

🔴 **Lo que NO se arregló, y por qué:** de los 6 folios equivocados que ella marcó, esto cierra
**1**. Los otros 5 exigen **desplazar cruces en cadena**, y la reasignación global —que subiría
los aciertos de 15 a 19— movería **96 conceptos de los cuales ~75 no tienen verificación**
(su muestra cubre el 6%). Es el mismo riesgo que en la limpieza de los 243 casi destruye 15
cruces legítimos con conf 1.0. **Se le pidió a Gaby una tanda de 60-80 ventas anotadas** para
poder validarlo; sin eso no se toca.

⚠️ **4 y 5 eran independientes** y se cerraron por separado: el # de venta (5) **no arreglaba**
el BUG A (4), porque una venta sin envío no tiene proveedor y el matcher sólo busca
`WHERE e.proveedor_id = ?` — no la veía aunque la factura trajera el número.

### El #4 (BUG A) se cerró el 2026-08-17 — ver `docs/bug-a-envio-carrito.md`

Reportado por Gaby: *"este número de venta sólo viene asociado a un sku cuando la venta es de
2 skus"*. **Se propaga el VÍNCULO del envío, no la FILA** (columna `envios_colecta.pack_id` +
la condición `ENVIO_CUBRE_VENTA` de `services/envio_pack.py`), para que el SLA siga contando
una vez por paquete y `metricas.py` no se toque.

**Simulado contra copia de prod con el código real:** 918 ventas recuperan envío
(1,368 → 450 sin envío) · SLA sin moverse (CAUPLAS 76.0%, KIM 75.8%) · 0 colisiones de los
268 · 0 conceptos contados doble. Test 24/24 con 5 mutaciones en rojo · api-guardian 13/13.

🔴 **Pero sólo 43 de las 918 traen bodega.** Las otras 875 aparecen ahora con el selector
"⚠ Asignar bodega" (antes no tenían ni eso), pero **sin bodega no cruzan factura**. **No
reportarlo como "los carritos ya quedaron resueltos".**

### El #5 se cerró en DOS mitades (2026-08-12/13) — leerlas juntas

**La causa que lo desbloqueó fue de Gaby: KIM teclea CEROS DE MÁS** (15-19 dígitos contra
los 16 del `order.id`). Por eso el script de agosto, que exigía 16 exactos, sólo veía el
19% de los PDF; con la tolerancia se lee el **54.5%** (ago-2026 ya va en 95.8%).

1. **Hacia atrás** — `0161ade`, ejecutado en prod con backup `bak-20260812_224405`:
   **228 conceptos corregidos** (216 estaban en la venta EQUIVOCADA, 212 con conf 1.0),
   40 ocupantes ajenos liberados a pendiente, 0 filas borradas, idempotente.
2. **Hacia adelante** — `0104d32`, desplegado y verificado: **paso 0 del matcher**, que
   para KIM lee el # del PDF. La evidencia del proveedor le gana al cruce por fecha.

🔴 **Ojo con lo que NO cierra el #5:** sigue habiendo ~62 facturas cuyo número impreso no
resuelve (KIM metió un dígito que no es cero, p. ej. `K29628`). Ésas quedan **Pendiente a
propósito** — ver la regla en el doc de detalle. **No son un bug.**

---

## 2. Dónde está el detalle de cada cosa

| Documento | Qué contiene |
|---|---|
| `docs/hallazgo-cruce-factura-venta.md` | El diagnóstico de fondo. **Las 3 hipótesis descartadas** (no repetirlas) y la descripción del BUG A |
| `docs/limpieza-cruces-falsos-persistidos.md` | La limpieza de los 243: resultado, garantías, las 3 lecciones de la ejecución |
| `docs/correccion-cruces-num-venta-kim.md` | El # de venta que KIM imprime en su PDF: la corrección de agosto (110 cruces) y **qué pedirle a KIMS** |
| `docs/paso0-num-venta-pdf-kim.md` | ⭐ **LO MÁS RECIENTE.** Los ceros de más, las 228 correcciones y el **paso 0** que automatiza el cruce. Leerlo antes de tocar el matcher o el recruce |
| `CLAUDE.md` §3 y §8 | Reglas de negocio y las 3 reglas de diseño del cruce por # de venta |

Tests que fijan lo ya arreglado (correrlos antes de commitear cualquier cambio al matcher):
`test_fecha_factura_venta.py` (13/13) · `test_kit_id_interno.py` (20/20) ·
`test_recruce_retroactivo.py` · `test_metricas_excluir_full.py` (13/13) ·
**`test_paso0_num_venta_pdf.py` (35/35)** · **`test_num_venta_kim_ceros.py` (46/46)**.

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

## 4. ✅ BUG A — el envío del carrito (CERRADO 2026-08-17)

> ⭐ **El detalle completo está en `docs/bug-a-envio-carrito.md`.** Aquí queda el resumen.

### Qué pasaba
ML crea **UN solo shipment por carrito** y lo cuelga de **UNA sola** de las N órdenes del
pack. Las demás ventas quedaban **sin envío → sin proveedor → invisibles para el matcher**,
y en la pestaña Ventas salían como *"Sin envío"* **sin selector de bodega**, así que Gaby no
podía ni corregirlas a mano.

### Magnitud (medida en prod al cerrarlo)
- **796 packs multi-venta = 1,714 ventas; 918 de ellas sin envío.**
- De los 1,073 pares que comparten pack, **1,073 comparten depósito y 0 difieren** → un
  carrito nunca trae dos proveedores, que es lo que hace correcto compartir el envío.

### Cómo se resolvió, respetando la decisión de Gaby
> *"como 1 retraso porque en general me cuenta toda la venta como 1 retrasada"*

**Se propaga el VÍNCULO, no la FILA:** columna `envios_colecta.pack_id` + la condición
compartida `ENVIO_CUBRE_VENTA` (`services/envio_pack.py`), que acepta el cruce directo **o**
el mismo paquete. Así `envios_colecta` conserva **una fila por envío real**, el SLA sigue
contando una vez por paquete y **`metricas.py` (blindado, `935c998`) no se tocó** más que
para pasar `COUNT(*)` → `COUNT(DISTINCT fc.id)` en la métrica de errores, que el JOIN nuevo
habría inflado al doble.

**Verificado simulando contra copia de prod con el código real:** 918 ventas recuperan envío ·
SLA idéntico (58,333 filas de envío; CAUPLAS 76.0%, KIM 75.8%) · **0 colisiones** de los 268
(con el OR prohibido habrían sido **262**) · 0 conceptos contados doble. Test 24/24 con
**5 mutaciones en rojo** · **api-guardian APROBADO 13/13**.

🔴 **Lo que NO cerró:** sólo **43** de las 918 traen bodega. 732 son COLECTA sin bodega y
136 son FULL. Las de COLECTA ahora muestran el selector "⚠ Asignar bodega" y el recruce
retroactivo cierra el cruce en cuanto Gaby la asigna — pero **sin bodega no cruzan factura**.

---

## 5. 🟡 El # de venta en la factura — KIM YA LO PONE, pero sólo en el PDF

> ⭐ **NOVEDAD 2026-08-07: Gaby descubrió que KIM ya imprime el # de venta.** Verificado
> contra las 840 facturas de prod y **usado para corregir 110 cruces** (102 estaban en la
> venta equivocada, 98 con confianza 1.0). Detalle completo en
> **`docs/correccion-cruces-num-venta-kim.md`**.
>
> 🔴 **Pero está SÓLO en el PDF: en los 750 XML aparece CERO veces**, así que el matcher
> —que sólo parsea XML— **no puede leerlo automáticamente**. Y viene en **19% de las
> facturas**, no en todas.
>
> ✅ **Lo bueno, ya medido:** de 144 números, **135 cruzan exacto contra `num_venta`**,
> **cero contra `pack_id` y cero colisiones** → la regla 3 de abajo se confirma en la
> práctica para KIM.
>
> **Pedido a KIMS (decidido por Mario):** que lo pongan **sí o sí en TODAS** las facturas.
> ⚠️ **Añadir al pedido: que venga en el XML, no sólo impreso** — es lo que lo vuelve
> automático.

**Estado: pedido a Gaby, ella ya se lo pidió a KIM y CAUPLAS (2026-08-07). CAUPLAS aún no lo
pone; KIM lo pone en el PDF (19%) pero no en el XML.**

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
