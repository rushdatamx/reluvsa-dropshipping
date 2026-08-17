# CAUPLAS: la factura no puede ser anterior a la venta que ampara

> **Fecha:** 2026-08-17 · **Commit:** `18e0ed4` · **Reportado por:** Gaby
> **Alcance:** SÓLO CAUPLAS (decisión de Mario). Los otros 4 proveedores no se tocan.
>
> ⚠️ **Leer antes de tocar `_orden_candidatas` o `_filtro_fecha` en `services/matcher.py`.**

---

## 1. Qué reportó Gaby

Mandó el CSV de Ventas de CAUPLAS descargado del portal, pero con **una columna AGREGADA A
MANO**: el folio que viene impreso en cada PDF. Es una **verdad de campo de 22 ventas**, y
resultó ser la pieza que permitió medir todo lo demás.

Su reporte textual:

> *"disculpe este es de cauplas y noté dif tmb en esas facturas que se asignaron a esas
> ventas"* … *"2000017906137676 por ejemplo esta venta en la pagina hizo cruce con esta fac
> 970096782, pero en la factura viene con la 970096819"*

Y su pregunta:

> *"de hecho quería preguntarle, en cauplas como se hace el cruce?? si es con ese número??
> lo puede conseguir"*

Se refería al `M2653287` que señaló en la foto.

**Estado del portal medido contra sus 22 anotaciones:**

| | Cuántas |
|---|---|
| Folio correcto | 15 |
| **Folio EQUIVOCADO** | **6** |
| Decía "no facturada" estándolo | 1 |

✅ **Sus 22 anotaciones se validaron una por una:** las 22 facturas que ella nombra **sí
contienen un concepto de esa pieza**. Sus datos son confiables.

---

## 2. La causa

CAUPLAS **reusa el ID interno de la pieza en TODAS sus facturas** y sólo cambia el folio de
pedido que va al lado (`M######`). El matcher comparaba nada más el ID interno:

```
factura 970096782 (12-ago 16:17) -> "7694 M2653006"  <- se llevó la venta
factura 970096819 (13-ago 16:19) -> "7694 M2653287"  <- la de verdad, quedó huérfana
```

Y el detalle que lo delata: **la venta de Gaby ocurrió el 12-ago a las 18:10**, o sea **dos
horas DESPUÉS** de que se emitiera la factura que el portal le colgó. Físicamente imposible:
nadie factura lo que todavía no ha vendido.

**Por qué pasaba:** el `ORDER BY v.fecha_venta DESC` elegía "la venta más reciente" sin mirar
si esa venta ya existía cuando se emitió la factura. El filtro de `_filtro_fecha` compara con
`date()` y no con la hora — decisión **correcta** y deliberada (ver §4) — así que las ventas
del mismo día pasaban el filtro y competían entre sí sin criterio.

Un caso todavía más claro, dos ventas del mismo SKU `CAU3895` **cruzadas entre sí**:

```
ventas:    ...883899170 (11-ago 15:09)   ...898219858 (12-ago 10:53)
portal:    970096819 (13-ago) ✗           970096782 (12-ago) ✗
Gaby dice: 970096782              y       970096819
```

---

## 3. Respuesta a la pregunta de Gaby: sí sirve, y no hay que conseguir nada

El `M######` **ya viene en el XML**, dentro del mismo campo `NoIdentificacion` que el portal
ya lee. Sólo se estaba tirando al comparar. Medido sobre los 339 conceptos de CAUPLAS en prod:

| | |
|---|---|
| Códigos M distintos | 175 |
| **Que viven en más de una factura** | **0** |
| Conceptos SIN código M | 7 (de 339) |

O sea: **cada `M######` pertenece a una sola factura** y agrupa los conceptos de un mismo
pedido (grupos de 1 a 11 conceptos).

⚠️ **NO confundir con la vía descartada del CLAUDE.md.** Ahí se midió "agrupar CAUPLAS por
`M######`" y dio **0 casos** — pero aquello intentaba atar la factura al **carrito** (falló:
un pedido reparte conceptos entre packs distintos). Esto es lo contrario: usar el M para
**desempatar entre dos conceptos con el mismo ID interno**, donde el dato sí es único.
**No cerrar este tema citando aquella nota.**

---

## 4. 🔴 Las 4 reglas que no se pueden rediseñar

Fijadas en `backend/scripts/test_cauplas_factura_anterior.py` (23/23), con **6 mutaciones**
que lo ponen en rojo.

### R1. Es una PREFERENCIA, no un filtro

`_orden_candidatas` antepone las ventas que ya existían cuando se emitió la factura, pero
**no descarta a las posteriores**. Si la única candidata es posterior, **se cruza igual**.

🔴 **Por qué importa:** en prod la venta `2000017927450774` **no tiene ninguna otra factura
con su pieza**. Con un filtro duro quedaría **pendiente para siempre** — perder información
sin poner nada mejor.

### R2. El `date()` de `_filtro_fecha` se CONSERVA

🔴 **NO cambiarlo a comparación por timestamp para "arreglar" este caso.** CAUPLAS y KIM
facturan a lo largo del día y muchas facturas legítimas se emiten antes de la venta que
amparan. Es el mismo criterio que ya salvó 15 cruces con confianza 1.0 en la limpieza de los
243 (ver `docs/limpieza-cruces-falsos-persistidos.md`).

### R3. El script exige un huérfano de la MISMA pieza y POSTERIOR a la venta

Sin alternativa **no se toca nada**. No se libera un cruce a ciegas: dejaría la venta
pendiente sin haber puesto nada en su lugar.

Además el token compartido debe medir **>= 4 caracteres** (misma guarda que el matcher): un
componente como `409` vive en 21 kits distintos y robaría conceptos ajenos.

### R4. El concepto que cede la venta queda PENDIENTE, no se borra

`num_venta_match = NULL`, la fila **nunca** se elimina. Así el recruce puede volver a
colocarlo si aparece su venta. Verificado por mutación (un `DELETE` en vez del `UPDATE` pone
3 asserts en rojo).

---

## 5. Resultado medido (copia de la BD de prod, con el código real)

| | ANTES | DESPUÉS |
|---|---|---|
| Aciertos contra las 22 de Gaby | 15 | **16** |
| Folios equivocados | 6 | **5** |
| **Cruces buenos rotos** | — | **0** |
| Conceptos CAUPLAS cruzados | 244 | 244 (no se pierde ninguno) |
| Filas de `factura_conceptos` | 339 | 339 (**cero borrados**) |

**El caso que reportó Gaby** (`2000017906137676`) queda en **970096819** — exactamente el
folio que ella dijo.

**Idempotente:** la 2ª corrida reporta "Nada que corregir".

---

## 6. ⬜ Lo que este arreglo NO cierra — decirlo tal cual

De los **6 casos imposibles** en CAUPLAS, el script corrige **2**. Los otros 4 **no tienen un
huérfano de su pieza** que los reciba, así que tocarlos sería adivinar (R3).

Y de los **6 folios equivocados** que marcó Gaby, esto arregla **1**. Los otros 5 exigen
**desplazar cruces existentes en cadena** — la factura correcta está ocupada por otra venta,
que a su vez tendría que moverse.

🔴 **Por qué NO se hizo la reasignación global** (medida y descartada, no por pereza):

| | Aciertos | Errores | Conceptos que mueve |
|---|---|---|---|
| Hoy | 15 | 6 | — |
| **Quirúrgico** (lo implementado) | 16 | 5 | **2** |
| Global (reasignar todo por fecha real) | 19 | 3 | **96** (y 4 pierden cruce) |

El global se ve mejor, pero **la muestra de Gaby cubre sólo 21 de 339 conceptos (6%)**. De
los 96 que movería, **~75 no tienen ninguna verificación**. Es exactamente el riesgo que en
la limpieza de los 243 casi destruye 15 cruces legítimos con confianza 1.0.

> **Un cruce falso es PEOR que un pendiente:** el pendiente se ve y se corrige; el falso dice
> "✓ Facturado" y nadie lo vuelve a mirar.

⬜ **Para cerrarlo hace falta más verdad de campo.** Se le pidió a Gaby una tanda de **60-80
ventas** anotadas como las 22 que ya mandó. Con eso el global se valida de verdad.

---

## 7. Qué se tocó

| Archivo | Qué |
|---|---|
| `services/matcher.py` | `_orden_candidatas()` nueva + aplicada en los **5** sitios de consulta |
| `scripts/corregir_cruces_cauplas_factura_anterior.py` | Corrección histórica, sólo CAUPLAS, idempotente, simula por default |
| `scripts/test_cauplas_factura_anterior.py` | 23/23 + 6 mutaciones |

⚠️ **El riesgo más alto del cambio fue el orden de los parámetros.** El `ORDER BY` nuevo
agrega un `?` **al final** en 5 consultas distintas; un desfase entre los `?` y la tupla
**no da error de SQL, da resultados equivocados en silencio**. Por eso el caso "3.bis" del
test ejercita los 4 pasos a la vez, y hay una mutación que lo verifica.

**api-guardian: APROBADO** (5/5 puntos — sin inyección SQL, orden de parámetros verificado
por ejecución en los 5 sitios, sin pérdida de datos, sin red, acotado a CAUPLAS).
**20 suites de regresión verdes.**

---

## 8. ⚠️ Gotcha de entorno que costó tiempo

Durante las pruebas de mutación el test se quedaba en rojo **con el código ya correcto**. La
causa no era el fix: macOS cachea el bytecode en

```
~/Library/Caches/com.apple.python/<ruta absoluta del repo>/
```

que está **FUERA del repo**, así que borrar los `__pycache__` no lo limpia. Si un test
contradice lo que dice el archivo en disco, purgar esa ruta antes de seguir investigando.
