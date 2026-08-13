# ✅ EJECUTADA — Corrección de cruces con el # de venta que KIM imprime en el PDF

> ⚠️ **SUPERADO POR `docs/paso0-num-venta-pdf-kim.md` (2026-08-12/13). Leer ése primero.**
> Lo de aquí abajo **sigue siendo cierto y no se revirtió**, pero se quedó corto por una
> razón que entonces no se conocía: **KIM teclea ceros de más** (15-19 dígitos contra los
> 16 del `order.id`). El script de este documento exige **16 exactos**, y por eso sólo veía
> el **19%** de los PDF; con la tolerancia a ceros se lee el **54.5%**, lo que produjo otras
> **228 correcciones** (commit `0161ade`).
>
> Además, lo de aquí fue una **corrección de datos puntual**: hoy el motor ya lo hace solo
> (**paso 0 del matcher**, commit `0104d32`). La conclusión de abajo de que "al no estar en
> el XML el matcher NO puede leerlo" **quedó resuelta leyendo el PDF**, porque KIMS confirmó
> que no puede timbrarlo.
>
> **Estado:** **EJECUTADA EN PRODUCCIÓN el 2026-08-07 22:53 UTC.** Backup en
> `/data/dropshipping.db.bak-20260807_225337`.
> **Reportado por:** Gaby. **Decidido y aprobado por:** Mario.
> **Script:** `backend/scripts/corregir_cruces_num_venta_pdf.py` (idempotente).
>
> ## Resultado (simulación y prod coincidieron exactamente)
>
> | | n |
> |---|---|
> | conceptos cruzados a la venta **EQUIVOCADA** → reasignados | **102** |
> | conceptos pendientes → cruzados | **8** |
> | **total corregido** | **110** |
> | ocupantes ajenos liberados a pendiente | 28 |
> | conceptos que ya estaban correctos (no se tocaron) | 33 |
>
> **98 de los 102 tenían `codigo_exact` con confianza 1.0**: le decían a Gaby
> "✓ Facturado" con la máxima seguridad sobre la venta que no era.
>
> **Garantías verificadas en prod:** `factura_conceptos` 1081 → 1081 filas (ninguna
> borrada) · `PRAGMA integrity_check` = ok · `ventas_ml`, `envios_colecta`, `facturas`,
> `lugar_override`, `albaran` y `kit_componentes` intactos · sync automática apagada
> durante la escritura y **reencendida** (`sync_auto_activo = 1`).
>
> **Medido contra el cruce manual de Gaby (143 casos), estado persistido:**
>
> | | antes | después |
> |---|---|---|
> | aciertos | 59 | **61** |
> | errores | 23 | 23 |
> | falsos "✓ Facturado" | 5 | **4** |
> | KIM | 42% | **45%** |
>
> 🔴 **Ser honestos al reportarlo:** el movimiento en esa tabla es **pequeño y no refleja
> el beneficio real**. Los 143 casos de Gaby son de mayo–junio y el número sólo aparece en
> el 19% de las facturas, así que **la mayoría de las 110 correcciones cayeron fuera de su
> muestra de control**. Las 102 correcciones son verificables una por una contra el PDF del
> proveedor, pero **la muestra disponible sólo prueba una mejora chica**. No presentarlo
> como "KIM ya quedó resuelto".

---

## 1. El hallazgo de Gaby, verificado

Gaby reportó: *"no me lo va a creer, kims si ponen el número de venta en la factura, todo
este tiempo jaja y nunca lo noté"*, con una imagen del PDF mostrando `#200000017783469806`.

**Tiene razón en lo esencial, con dos matices que cambian la implementación.** Verificado
contra las 840 facturas de KIM en prod:

| Pregunta | Respuesta medida |
|---|---|
| ¿Existe el número? | ✅ Sí, es el **`order.id` de ML** (= `ventas_ml.num_venta`) |
| ¿Está en el **XML**? | 🔴 **NO. Cero de 750.** KIM no lo timbra como dato estructurado |
| ¿Está en el **PDF**? | ✅ Sí, pero en **144 de 748 (19.3%)**, no en todos |
| ¿Contra qué cruza? | **135 de 144 contra `num_venta`**. **Cero** contra `pack_id`, **cero** colisiones |

⚠️ **Los dos matices importan:**

1. **Al no estar en el XML, el matcher NO puede leerlo hoy** (sólo parsea XML). Por eso
   esto fue una **corrección de datos puntual**, no una regla permanente del matcher.
2. **No viene en todas** (19%, no 100%). Gaby probablemente vio varias seguidas del mismo
   lote. Los 604 restantes simplemente no lo traen — no es un fallo de lectura del PDF.

✅ **Lo que sí quedó resuelto y vale para el futuro:** el número **nunca cruzó contra
`pack_id`** y **no hubo una sola colisión**. Eso elimina en la práctica el riesgo de los
**268 números que son `order.id` de una venta y `pack_id` de otra** (CLAUDE.md §8, regla 3)
para el caso de KIM.

---

## 2. Por qué esta fuente es más fuerte que las anteriores

Las correcciones previas (fix de fecha `f699577`, limpieza de los 243 `bf6b392`) se apoyaban
en un criterio **aritmético**: una venta posterior a su propia factura es imposible. Eso
identifica cruces falsos pero **no dice cuál es el correcto**.

El # de venta es **la verdad declarada por el propio proveedor**: es el dato que él mismo
asocia a su factura. Por eso aquí sí se puede **reasignar**, no sólo liberar.

**Consistencia verificada con el fix de fecha:** de las 111 ventas destino, **cero son
posteriores a su factura** y **111 de 111 tienen envío con proveedor KIM**. El número del
proveedor no contradice al filtro de fecha ni depende del BUG A.

---

## 3. 🔴 Las 5 garantías del script — no romperlas en un refactor

1. **NUNCA se borra una fila de `factura_conceptos`.** Sólo se reescriben `num_venta_match`,
   `match_method` y `match_confidence`. El concepto es dato real del XML.
2. **Sólo se tocan facturas cuyo PDF trae el número Y cuya venta existe.** Todo lo demás
   queda intacto.
3. **La venta destino no puede ser posterior a su factura** (se revalida en el script).
4. **Los ocupantes ajenos se liberan a PENDIENTE, no se reasignan a ciegas.** Su factura no
   trae el número → no hay evidencia de a qué venta pertenecen. Inventarla reintroduciría el
   mismo bug que se está arreglando.
5. **Todo en UNA transacción** (`BEGIN IMMEDIATE` + un solo commit, con rollback).

---

## 4. ⚠️ Las 2 trampas que la simulación atrapó — leer antes de reusar el script

Ambas hacían que el script **no convergiera** (la 2a corrida seguía encontrando trabajo).
Se detectaron **sólo por correrlo dos veces sobre la copia**; ninguna habría sido visible en
una sola pasada.

1. 🔴 **"Ocupante ajeno" es POR FACTURA, no por concepto.** Una factura puede tener varios
   conceptos que apuntan legítimamente a la **misma venta** (el proveedor facturó 2 piezas de
   una sola venta). Al corregir uno, el hermano **no es un intruso**. La primera versión
   comparaba sólo por `concepto_id` → corregía un concepto y liberaba a su hermano,
   **alternando en cada corrida** (4 conceptos oscilando, facturas 204, 599 y 720).
2. 🔴 **Un mismo # de venta puede aparecer en DOS facturas distintas.** Medido: `K28023`
   (pieza `96476708-Z`) y `K28069` (pieza `96476709-Z`) comparten la venta
   `2000016875229280` — KIM partió una venta en dos facturas. **Las dos tienen razón** y el
   número no dice cuál "posee" la venta → **el dato es ambiguo y no se toca ninguna**
   (regla del proyecto: ante ambigüedad real, preferir el pendiente al cruce inventado).

⚠️ **Las 2 guardas se sostienen mutuamente** (lo señaló el api-guardian): sin la de
ambigüedad, el dict `facturas_destino` haría que dos facturas hacia la misma venta se
pisaran. **Quien toque una, debe revisar la otra.**

---

## 5. Auditoría y verificación

**`api-guardian`: APROBADO** (7/7, con evidencia archivo:línea). Verificó: cero red, cero
`DELETE`/`INSERT`, todos los parámetros vinculados (sin SQL injection), no toca
`lugar_override`/`albaran`/`ventas_ml`/`envios_colecta`/`kit_componentes`, transacción
atómica con rollback probado empíricamente, y que las 2 guardas del punto 4 resisten.

**Regresiones (venv del backend):** `test_fecha_factura_venta` ✅ · `test_kit_id_interno` ✅ ·
`test_recruce_retroactivo` ✅ · `test_metricas_excluir_full` ✅.

⚠️ **Los tests del matcher requieren `backend/.venv/bin/python`** — el Python del sistema no
trae `rapidfuzz`.

---

## 6. ⬜ Lo que queda abierto

### 6.1 El pedido a KIMS — pedir que lo pongan **en el XML**, no sólo en el PDF

Mario ya decidió pedirle a KIMS que lo ponga **sí o sí en TODAS las facturas**. Al pedirlo,
**incluir estos dos puntos**, que hoy no se cumplen:

1. 🔴 **Que venga en el XML** (dato timbrado), no sólo en la representación impresa. Es lo
   que convierte esto en automático: hoy el matcher no puede verlo.
2. **Que si parten una venta en varias facturas, se pueda distinguir** (ver trampa 4.2).

Mientras no esté en el XML, la cobertura seguirá siendo del ~19% y cada lote nuevo requiere
correr este script a mano.

### 6.2 El paso 0 del matcher — decisión pendiente

Cuando el número llegue en el XML, implementar el **paso 0** descrito en
`docs/estado-cruce-factura-venta.md` §5 (las 3 reglas de diseño siguen vigentes; la medición
de KIM las confirma: buscar primero en `num_venta`, nunca en `num_venta` y `pack_id` a la vez).

**Alternativa disponible ya:** leerlo del PDF con `pdfplumber` (la infra existe, se usa para
el UUID). Da 19% de cobertura. **Decisión pendiente de Mario**; la recomendación fue esperar
al XML y reusar este script mientras tanto.

### 6.3 Los 28 liberados — medir tras el próximo recruce

Lo señaló el api-guardian: `recruzar_conceptos_sin_match` filtra
`WHERE fc.num_venta_match IS NULL`, así que **no puede pisar las 110 correcciones**
(protegidas por su método `num_venta_proveedor`), pero **sí volverá a cruzar los 28
liberados** por heurística en la próxima sync. Los protege de volver a la venta que se les
quitó el `AND fc.id IS NULL` de los 4 pasos (esas ventas ya están ocupadas).

**"Liberado a pendiente" es un estado transitorio, no final.** Vale la pena medir cuántos de
los 28 volvieron a cruzarse y con qué calidad.

---

## 7. Cómo reusar el script cuando entren facturas nuevas

```bash
# 1. Simular SIEMPRE primero (no escribe nada)
python corregir_cruces_num_venta_pdf.py --db /data/dropshipping.db

# 2. Apagar la sync automática (POST /api/ml/sync-auto {"activo": false})
# 3. Backup:  VACUUM INTO '/data/dropshipping.db.bak-<fecha>'
# 4. Ejecutar
python corregir_cruces_num_venta_pdf.py --db /data/dropshipping.db --ejecutar
# 5. ⚠️ REENCENDER la sync automática (Trampa 5, CLAUDE.md §8)
```

⚠️ **Usar `--cache` con un JSON `{archivo_pdf: [numeros]}`** para acortar la ventana entre el
cálculo del plan y la transacción (el api-guardian lo marcó como riesgo B: leer ~748 PDF
tarda minutos, y en esa ventana una sync podría mover el estado). Con la sync apagada el
riesgo desaparece.

**El script es idempotente:** una segunda corrida encuentra 0 correcciones (verificado).
