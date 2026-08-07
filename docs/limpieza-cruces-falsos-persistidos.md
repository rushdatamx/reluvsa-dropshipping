# 🧹 TAREA ABIERTA — Limpiar los 243 cruces falsos que quedaron persistidos en prod

> **Estado:** MEDIDO Y DOCUMENTADO, **NO EJECUTADO**. Cero cambios en prod.
> **Fecha:** 2026-08-07. **Decidido por:** Mario.
> **Prerequisito ya hecho:** el fix de fecha (commit `f699577`, desplegado y verificado).

---

## 0. Qué hay que hacer, en una línea

Poner en `NULL` los **243 conceptos** cuyo cruce es demostrablemente imposible (la venta es
POSTERIOR a su propia factura) y correr el recruce, que ya con el filtro nuevo los reasignará
bien o los dejará pendientes. **Simular contra copia de prod ANTES de tocar nada.**

---

## 1. Por qué hace falta

El fix de fecha (`f699577`) corrige los cruces **de aquí en adelante**, pero **no limpia los
que ya estaban mal guardados**: `recruzar_conceptos_sin_match` sólo reintenta conceptos con
`num_venta_match IS NULL`, así que nunca toca uno ya cruzado —aunque esté mal.

Medido sobre la BD de prod del 2026-08-07:

```
243 de 966 conceptos cruzados (25%) tienen la venta fechada DESPUÉS de su factura
```

Eso es **imposible por definición**: nadie factura mercancía que todavía no ha vendido. No es
una heurística ni una estimación — es una contradicción aritmética en el propio dato.

Ahí dentro están los **17 casos que le marcan a Gaby "✓ Facturado" sobre mercancía que no ha
salido**, que es justo lo que la hizo desconfiar del portal.

### Reparto

| Proveedor | Método del cruce falso | n |
|---|---|---|
| KIM | `codigo_exact` | 225 |
| KIM | `kit_componente` | 9 |
| KIM | `codigo_id_interno` | 3 |
| CAUPLAS | `kit_componente` | 6 |
| **TOTAL** | | **243** |

Distancia venta-después-de-factura: **min 1 día, mediana 13, máx 45**.

---

## 2. ⚠️ Lo ya verificado — no volver a investigarlo

1. **Los 21 casos "a 1 día" NO son ruido de zona horaria.** Se revisaron las horas exactas:
   las facturas son de las 18:00 y las ventas del día siguiente al mediodía/tarde
   (`factura 2026-06-10 18:13 → venta 2026-06-11 23:12`). Son 20+ horas de diferencia real.
   Si fueran zona horaria, las ventas caerían de madrugada. **Los 243 son sólidos, se limpian
   todos.**
2. **`facturas.fecha_factura` NULL: cero filas.** Ninguna factura cae al comportamiento viejo
   por falta de fecha, así que la limpieza cubre el universo completo.
3. **La columna es `facturas.fecha_factura`**, no `facturas.fecha` (el CLAUDE.md §7 la lista
   mal). El parser la puebla desde `parsed["fecha"]` (`routers/facturas.py:258`).

---

## 3. Método obligatorio (el mismo del fix de kits y del fix de fecha)

1. **Bajar copia de prod** — sólo lecturas, nunca `railway run`, nunca lanzar el sync por SSH
   (§8.ssh del CLAUDE.md). Receta que funcionó: `src.backup(dst)` sobre
   `file:/data/dropshipping.db?mode=ro` → extraer las 6 tablas a una BD reducida → gzip →
   base64 por fragmentos de 1 MB → reensamblar y **verificar SHA-256**. Los temporales se
   escriben en `/tmp` del contenedor (efímero), **jamás en `/data`**.
2. **Simular la limpieza sobre la copia** y medir:
   - cuántos de los 243 se reasignan a una venta distinta y cuántos quedan pendientes;
   - **que los cruces buenos NO se toquen** (la consulta sólo debe seleccionar los imposibles);
   - el resultado contra el cruce manual de Gaby (ver §4).
3. **Sólo entonces** ejecutar en prod, con backup previo.

---

## 4. Cómo medir que la limpieza mejora (no sólo que "corre")

La fuente de verdad son los 2 archivos del cruce manual de Gaby. **Son PII, NO van al repo:**

- `~/Downloads/ventas_cruces (2).csv` — CAUPLAS, 37 filas.
  Col `Num factura` = lo que asignó el portal; col `Factura que yo asigne manualmente` = verdad.
- `~/Downloads/VENTAS KIMS .xlsx` — KIM, 106 filas.
  Col 11 = portal; **col 12 va SIN encabezado** = verdad; trae el texto libre
  `"No la envían aun"` → filtrarlo (marca mercancía no enviada, no un folio).

⚠️ **Normalizar folios antes de comparar**: el portal escribe `970096331 CD` (Serie del XML)
y Gaby `970096331`. Comparar en crudo da "0 coincidencias" (error ya cometido una vez).
Los scripts que ya hacen todo esto quedaron en el scratchpad de la sesión del 08-07
(`build_truth.py`, `baseline.py`, `verifica_fix.py`) — **si ya no existen, reconstruirlos es
~30 min**; la lógica está descrita aquí.

**Línea base tras el fix de fecha (código real, copia de prod, 143 casos):**

| | antes del fix | tras el fix | tras la limpieza |
|---|---|---|---|
| aciertos | 26 | 64 | ⬜ medir |
| errores | 76 | 29 | ⬜ medir |
| falsos "✓ Facturado" | 28 | 10 | ⬜ **debe bajar** |

---

## 5. 🔴 Trampas de esta limpieza

1. **NO borrar filas de `factura_conceptos`.** Sólo poner `num_venta_match`, `match_method` y
   `match_confidence` en `NULL`. Borrar la fila perdería el concepto de la factura, que es
   dato real del XML.
2. **El criterio de selección debe ser la comparación fecha-venta vs fecha-factura, NO el
   método ni la confianza.** 225 de los 243 son `codigo_exact` con confianza 1.0 — filtrar
   por confianza baja no los tocaría.
3. **Comparar con `date()`, no con el timestamp.** Una factura emitida a las 10:00 del mismo
   día en que se vendió a las 14:00 es legítima; comparar timestamps la marcaría como falsa
   y se limpiarían cruces buenos.
4. **Correr el recruce DESPUÉS de limpiar, en la misma operación.** Si no, los 243 quedan
   pendientes hasta la próxima carga de ventas/colecta y Gaby ve un bajón temporal.
5. **Backup antes de tocar prod.** Ya existe el patrón: `VACUUM INTO` a
   `/data/dropshipping.db.bak-<fecha>` (hay 4 backups previos ahí).
6. **Esperar que el recruce NO recupere la mayoría.** Muchos de los 243 son ventas del mismo
   SKU en días con varias facturas: el filtro nuevo, correctamente, **prefiere no cruzar**.
   Que queden pendientes **es el resultado deseado**, no una falla de la limpieza.

---

## 6. Después de limpiar: avisarle a Gaby

Se le va a mandar un mensaje explicando el fix (redactado, pendiente de enviar). Si la
limpieza se ejecuta, **agregar una línea**: que va a ver correcciones en cruces viejos —
algunas facturas cambiarán de venta y otras volverán a "Pendiente", y eso es lo correcto.

---

## 7. Contexto que NO hay que volver a descubrir

- **`docs/hallazgo-cruce-factura-venta.md`** — el diagnóstico completo, las 3 hipótesis
  descartadas y las 3 correcciones medidas (entre ellas: la vía del `M######` de CAUPLAS es
  falsa, da 0 casos de diferencia; y el "KIM 27%" real era 5%).
- **KIM no se puede resolver del todo con los datos actuales** (~23 facturas/día, 141
  combinaciones SKU+día con varias ventas). Se le pidió a Gaby que los proveedores pongan el
  **# de venta** en la factura. Las 3 reglas de diseño de ese cruce están en el CLAUDE.md §8
  — en particular: **nunca buscar en `order.id` y `pack_id` a la vez** (268 números son ambas
  cosas para ventas distintas).
- **BUG A sigue abierto:** 1,042 ventas de carrito sin envío → invisibles para el matcher.
