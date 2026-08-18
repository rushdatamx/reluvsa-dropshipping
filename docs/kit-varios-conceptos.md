# Una venta-kit recibe TODOS sus componentes

> **Fecha:** 2026-08-17/18 · **Reportado por:** Gaby · **Alcance:** CAUPLAS y KIM (la causa
> era estructural del matcher, no de un proveedor).
>
> ⚠️ **Leer antes de tocar `_match_por_kit`, `_tokens_pieza` o `_componente_ya_cubierto`.**

---

## 1. Qué reportó Gaby

> *"de esta factura se vinculó a 2 ventas con el mismo sku pero sólo debería vincularse a
> la venta con terminación 9104, sabe como pudo pasar esto??"*

Reproducido en prod (factura `970096936`, pedido `M2653711`, KIT0207):

```
"13351 M2653711" (CALEF ENTRADA) -> venta ...786110
"13353 M2653711" (CALEF SALIDA)  -> venta ...639104   <- OTRA venta del mismo kit
```

Los **dos componentes del mismo kit**, del **mismo pedido**, cantidad 1 cada uno —o sea
**un solo kit**— se repartieron entre **dos ventas distintas**.

---

## 2. La causa (era estructural, no de CAUPLAS)

Los pasos del matcher excluyen con `AND fc.id IS NULL` a cualquier venta que **ya tenga un
concepto cruzado**. Para una venta normal es correcto —una pieza, una factura— pero **una
venta-kit necesita N conceptos, uno por componente**. El primero ocupaba la venta y los
demás se iban a otras ventas del mismo kit.

**Magnitud medida en prod antes del arreglo:** de las **141 ventas-kit facturadas, 130
estaban incompletas**.

| Proveedor | Kits completos | Incompletos |
|---|---|---|
| CAUPLAS | **0** | **115** |
| KIM | 10 | 15 |

Había ventas de `KIT03561` marcadas "✓ Facturado" con **1 de sus 8 piezas**. Y **85 de los
95 conceptos huérfanos** de CAUPLAS eran componentes de kit que no encontraban dónde caer:
contaban como *error de facturación* del proveedor **sin serlo**.

---

## 3. 🔴 Las 4 reglas del arreglo

Fijadas en `backend/scripts/test_kit_varios_conceptos.py` (12 casos) con **8 mutaciones**
que lo ponen en rojo.

### R1. Varios conceptos por venta-kit, pero NUNCA la misma pieza dos veces
Si el proveedor factura dos veces la misma pieza son **dos kits vendidos**, y el segundo
pertenece a otra venta. La guarda es `_componente_ya_cubierto`.

### R2. `_tokens_pieza` excluye el folio de pedido — y el folio se ancla
`_tokens_codigo` captura también los dígitos del folio (`13351 M2653711` →
`{'13351','2653711'}`), así que dos componentes **distintos** del mismo pedido parecían la
misma pieza. Ése era justo el bug que partía el kit de Gaby.

🔴 **El ancla `(?<![A-Z])` del patrón NO es cosmética** (hallazgo del api-guardian): sin
ella la regex muerde el **prefijo de bodega** pegado al número y la pieza desaparece —
`CAU11370` veía el "folio" `U11370`, y `P2172292` (esquema real de ARGENPARTS) se anulaba
entero.

### R3. El desempate por pedido exige conjunto EXACTO
Cuando dos kits comparten vehículo la descripción no distingue (el caso de Gaby enfrentaba
`KIT0207` contra `KIT03555`, ambos "Spark/Beat"). Lo que sí decide es **qué piezas trae el
pedido**: `{13351, 13353}` es exactamente `KIT0207`.

🔴 **Coincidencia exacta, no subconjunto:** un pedido suele ser un **lote mixto** — medido
en prod, `M2649748` trae piezas de 2 kits **más 2 piezas sueltas**. Con "subconjunto"
calzaría contra cualquiera de ellos y volveríamos a adivinar.

### R4. Los conceptos de un mismo pedido van a la misma venta
Sin esto resuelven bien el kit pero caen en ventas distintas — exactamente lo reportado.

---

## 4. 🔴 El bug que el api-guardian encontró en el propio arreglo

La primera versión **fue RECHAZADA**. `_componente_ya_cubierto` hacía `return False`
cuando no había tokens de pieza, y eso **apagaba la guarda**: N facturas de la misma pieza
se apilaban **todas en una venta** con confianza 0.95 — el **defecto inverso** al que
reportó Gaby, y con el mismo final (un dato que nadie vuelve a revisar).

> **Regla general del proyecto:** ante ausencia de dato el matcher **se abstiene**.
> `return False` ahí era el lado inseguro. Ahora cae a comparación por **texto
> normalizado**.

**Alcance medido:** con el ancla de R2, sólo **1 de 1,400 conceptos** de prod depende del
fallback por texto. El ancla hace el trabajo; el fallback es red de seguridad.

---

## 5. Resultado medido y — importante — lo que NO cierra

**Hay dos caminos, y dan resultados muy distintos:**

| CAUPLAS | kits completos | incompletos | huérfanos |
|---|---|---|---|
| Hoy en prod | 0 | 115 | 95 |
| **A) Sólo desplegar** (recruce automático) | **17** | 102 | **7** |
| B) Re-cruce total (requiere script) | 43 | 13 | 10 |

⚠️ **El recruce automático sólo rellena huérfanos: NUNCA reacomoda un cruce existente.**
Por eso el caso concreto de Gaby **no se arregla con sólo desplegar** — sus dos componentes
ya están cruzados a ventas distintas y el recruce no los mueve.

**Lo que sí gana el despliegue solo:** los huérfanos bajan de 95 a 7 (se limpian los ~85
falsos "errores de facturación" de CAUPLAS) y **0 conceptos pierden un cruce que ya
tenían**.

⬜ **Pendiente (paso 2):** un script de corrección que reacomode los cruces existentes.
Tocaría ~130 ventas y **Gaby vería movimiento**, así que se simula y se aprueban los
números antes de ejecutarlo.

ℹ️ **Aun con el script, hay un límite que los datos no resuelven:** Gaby dice que la
factura va a la venta del 14-ago; el motor elige la del 16-ago. Ambos componentes van
juntos —eso sí se arregla— pero *cuál* de las 4 ventas idénticas de `KIT0207` en 5 días le
toca es un empate. Sólo lo cierra que CAUPLAS ponga el # de venta en la factura, igual que
se le pidió a KIM.

---

## 6. Rendimiento: es MÁS rápido, no más lento

Preocupaba que las consultas extra alargaran el write lock (el recruce corre dentro de la
transacción de subir ventas/colecta). Medido sobre copia de prod:

| | recruce CAUPLAS |
|---|---|
| Código actual (sin el fix) | 10.55 s — y cruza **0** conceptos |
| Con el fix | **5.76 s** — y cruza **88** |

Los matches resuelven antes, así que se recorren menos candidatas.

---

## 7. Gotcha de entorno (ya documentado, se repitió)

macOS cachea el bytecode en `~/Library/Caches/com.apple.python/<ruta del repo>/`, **fuera
del repo**: borrar los `__pycache__` no lo limpia. Si un test contradice lo que dice el
archivo en disco, purgar esa ruta antes de investigar.
