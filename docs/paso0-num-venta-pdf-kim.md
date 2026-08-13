# ⭐ El # de venta de KIM: los ceros de más y el PASO 0 del matcher

> **2026-08-12/13.** Cierra el problema #5 del tablero (`docs/estado-cruce-factura-venta.md`)
> en sus dos mitades: la corrección **hacia atrás** (228 cruces, commit `0161ade`) y el
> arreglo del **motor** (paso 0, commit `0104d32`, desplegado y verificado en prod).
>
> **Leer esto antes de tocar `services/matcher.py`, `services/num_venta_pdf.py` o el
> recruce.** Trae las reglas de diseño y las trampas medidas.

---

## 1. El hallazgo, y de quién fue

**Gaby reportó** que veía facturas *"con diferente número de factura aunque en la factura
venga el número de venta"* y preguntó si *"se desfasaron"*. **Tenía razón: los folios
estaban corridos un lugar.**

Y **el dato que desbloqueó todo también fue suyo**: *"en la factura viene 6 ceros y en la
venta correcta es 4 ceros"*. **KIM teclea CEROS DE MÁS** al capturar el número.

El `order.id` de ML es de **16 dígitos**; los PDF de KIM traen de **15 a 19**. Por eso el
script de agosto (`corregir_cruces_num_venta_pdf.py`, regex de 16 exactos) sólo veía el
19% de los PDF. Medido sobre los 864 PDF legibles de KIM en prod:

| longitud | 15 | 16 | 17 | 18 | 19 |
|---|---|---|---|---|---|
| cuántos | 2 | 146 | **245** | 73 | 7 |

Con la tolerancia se lee el **54.5%** (473 de 864), casi el triple.

⭐ **Cobertura por mes — KIMS ya está cumpliendo:** jun 53.5% · jul 39.9% · **ago 95.8%**.
El 54.5% agregado arrastra los meses viejos. Mario acordó con KIMS que **siempre** lo
pongan y **en el mismo lugar** (junto al QR, precedido de `#`).

---

## 2. 🔴 Por qué NO basta con "quitar los ceros de adelante"

Fue la primera regla que se probó y **falla**. KIM no sólo mete ceros al principio:

```
K29628   impreso 20000117582609224  ->  colapsar da 2000117582609224 (NO existe)
                                        el real es  2000017582609224 (cero EN MEDIO)
K30109   impreso 2000017791201470   ->  el real es  2000017797201470
                                        (¡un DÍGITO distinto, no un cero!)
```

Y lo peligroso: esa regla **produce números de 16 dígitos con pinta válida**. Si por
casualidad existieran como venta de otro, escribirían un cruce falso **con confianza 1.0** —
el mismo bug que se venía arreglando, pero peor, porque nadie lo revisaría.

**Por eso el código NO reconstruye el número: busca la venta y exige evidencia.**

### Las 3 reglas (en `services/num_venta_pdf.py`)

| | Regla | Por qué |
|---|---|---|
| **R1** | Candidatas = las que salen **BORRANDO ceros** en cualquier posición. Nunca se agrega ni se cambia un dígito | Borrar un cero de más deshace un error observado; cambiar un dígito sería inventar |
| **R2** | La venta debe ser **de KIM** (su envío con `proveedor_id` de KIM) | Una venta de otro proveedor jamás la factura KIM |
| **R3** | El **SKU debe cuadrar** con algún código de la factura | Doble verificación: número + pieza |

🔴 **Si hay >1 candidata válida o el SKU no cuadra → NO se cruza.** R3 es lo que vuelve
seguro tolerar los ceros: un número mal reconstruido casi nunca cae en una venta del mismo
proveedor **Y** de la misma pieza.

⚠️ **EXCLUSIVO DE KIM** (mandato de Mario). Ningún otro proveedor imprime el número, y la
normalización de ceros es un error de captura observado sólo en él.

---

## 3. Mitad 1 — la corrección hacia atrás (`0161ade`, EJECUTADA)

Backup `/data/dropshipping.db.bak-20260812_224405`.

| | |
|---|---|
| correcciones | **228** (216 en la venta EQUIVOCADA — 212 con conf 1.0 — + 12 pendientes recuperados) |
| ocupantes ajenos liberados a pendiente | **40** |
| ya correctos | 194 |
| descartados por las guardas | **58** (29 SKU no cuadra · 27 sin variante · 4 ambiguos · 2 no son de KIM) |

**Filas 1288 → 1288: ninguna borrada.** Cruzados 1130 → 1102 (−28 = 40 liberados − 12
nuevos). `integrity_check` ok · 2ª corrida 0/0 (idempotente).

**Se midió la observación del api-guardian:** los 40 ocupantes ajenos son **todos de
facturas de KIM** (`{'KIM': 40}`), así que la guarda extra habría sido inalcanzable en prod
y no se agregó código.

### El test subió de 32 a 46 casos — y por qué importa

La liberación sólo se comprobaba con `len(liberar) == 0` en la prueba de idempotencia:
**verde por omisión**, el mismo error que tuvo el e2e de `total_neto`. Se agregaron dos
casos que la ejercitan en ambas direcciones, **verificados por mutación**:

- **(a)** un ocupante de **otra** factura de KIM que **sí** se libera (los 3 campos a NULL).
- **(b)** dos conceptos hermanos de la **misma** factura que **no** se liberan mutuamente,
  con un assert de 3 corridas extra sin movimiento. 🔴 Es el bug que hacía **oscilar** al
  script hermano, y en prod existe de verdad: `K28119` factura 2 piezas de la misma venta.

---

## 4. Mitad 2 — el PASO 0 del matcher (`0104d32`, DESPLEGADO)

**Decisión de Mario, textual:** *"lo que quiero es que en KIMS ahora se fije siempre en el
pdf, ahí aseguramos que es el número de venta, sólo tomar en cuenta que algunas veces
pueden poner 0s de más"*.

**KIMS no puede timbrar el número en el XML**, así que la única vía es leer el PDF (medido:
0 de 750 XML lo traen; el portal ya abre PDFs en `uuid_pdf.py` para el UUID).

### El criterio de fondo

> El # impreso es **EVIDENCIA del proveedor**. Los pasos 1-4 del matcher son **inferencias
> nuestras**. Cuando ambos existen, gana la evidencia.

| Situación | Qué hace |
|---|---|
| PDF con # que resuelve | **Cruza ahí**, `num_venta_proveedor` conf 1.0 |
| PDF con # que **NO** resuelve | 🔴 **Pendiente.** NO se cae a fecha (~62 facturas) |
| Sin # / sin PDF (~45%) | Cruza por fecha, como siempre |
| Cualquier otro proveedor | Ni se abre su PDF |

🔴 **La fila 2 es la decisión fina.** Si KIM declara *"esta venta es la 2000011758…"* y ese
número no existe, el dato está corrupto: cruzar por fecha ahí sería **adivinar contra la
evidencia que el propio proveedor dio**. Quedan visibles como Pendiente.

⚠️ **Asimetría deliberada, no un descuido:** en el **alta** un # corrupto deja el concepto
pendiente; en el **recruce** ese mismo # corrupto **NO destruye un cruce que ya existía** —
borrarlo quitaría información sin poner nada mejor en su lugar.

### El recruce ahora CORRIGE, no sólo enriquece (cambio de regla)

Hasta hoy `recruzar_conceptos_sin_match` *"sólo enriquecía, nunca rompía un match
existente"*. Esa regla dejaba abierto el hueco del **orden de subida**: si el proveedor
sube el XML hoy y el PDF mañana, el concepto **ya cruzó por fecha** —posiblemente a la
venta equivocada y con confianza 1.0— y nadie lo reintentaba.

Acotado: sólo reescribe cruces cuyo método **no** sea ya `num_venta_proveedor`, y sólo si
el # resuelve con las 3 reglas. Nunca pisa evidencia con evidencia ni degrada a algo más
débil.

---

## 5. ⚠️ La caché NO es una optimización: es lo que hace viable el diseño

El recruce corre **dentro de la transacción** de subir ventas/colecta, o sea **con el write
lock de SQLite tomado**. Sin caché habría abierto **548 PDF en CADA corrida**:

- ~114 ms por PDF × 548 ≈ **62 s de lock** (medido por el api-guardian, que además
  **reprodujo el `database is locked`** en un escritor concurrente; el `busy_timeout` es 5 s).
- Y el **~45% que no trae número se releería para siempre sin corregir nada nunca**.

Por eso existe `facturas.num_venta_pdf` (migración idempotente en `database.py`):

| valor | significa |
|---|---|
| `'<numero>'` | el # hallado |
| `''` | leído y **NO** trae número (o trae varios: ambiguo) |
| `NULL` | todavía no se ha leído |

🔴 **`''` y `NULL` son estados DISTINTOS a propósito.** Si "leído sin número" se guardara
como NULL, sería indistinguible de "sin leer" y el recruce reabriría ese PDF en cada
corrida — justo lo que la caché evita. Hay una mutación en el test que lo fija.

⚠️ **Un PDF que aún NO existe NO se cachea**: puede llegar después (es justamente el caso
XML-primero-PDF-después que esto vino a resolver). Son las 88 que reintentan lectura.

### ⬜ Al desplegar: correr el precalentado ANTES

```bash
railway ssh --service reluvsa-dropshipping "cd /app && /opt/venv/bin/python \
  scripts/precalentar_num_venta_pdf.py --db /data/dropshipping.db \
  --uploads /data/uploads/facturas --ejecutar"
```

Lee los PDF **fuera de transacción**, con commit por lotes de 50. Si se omite, la primera
carga de Gaby paga el minuto de lock.

---

## 6. Resultado real en prod (2026-08-13, backup `bak-20260813_001347`)

Orden ejecutado: push → Railway desplegó (md5 de los 4 archivos idéntico al local) →
migración aplicada al arrancar → precalentado → recruce de prueba.

| | |
|---|---|
| precalentado (952 PDF, fuera de transacción) | **70.8 s** — 469 con # · 395 sin # · 88 con PDF ausente |
| **recruce real después** | **3.1 s** (vs los ~62 s de lock sin precalentar) |
| **conceptos cambiados de venta** | **0** — tal como se simuló antes de desplegar |
| sellados `num_venta_proveedor` | 337 → **485** (los 148 que sólo se confirmaban) |
| conceptos / cruzados / integridad | 1288 / 1150 / ok — **idénticos al backup** |

⭐ **Gaby no vio movimiento, y eso era el objetivo:** el pasado ya lo había limpiado la
corrección de los 228, así que el paso 0 sólo actúa hacia adelante. Se simuló contra las
952 facturas reales **antes** de desplegar y dio exactamente el mismo `0`.

---

## 7. Verificación

- **`test_paso0_num_venta_pdf.py` 35/35**, con **PDF reales generados al vuelo**. Mockear
  la extracción habría dejado sin probar justo la parte que toca el mundo real.
- **5 mutaciones**, cada una mata sus asserts: el corrupto cayendo a fecha · la guarda de
  ambigüedad · el recruce sin corregir · el paso 0 dejando de ser exclusivo de KIM · la
  caché guardando NULL en vez de `''`.
- **13 suites de regresión** verdes, incluidas las de seguridad de ML.
- **Migración probada sobre copia de prod**: idempotente ×3, cero cambios en datos.
- **api-guardian APROBADO 7/7 + 8 puntos específicos**: cero red, SQL injection imposible
  (probó `2' OR '1'='1` → 0 candidatas), sin pérdida de información, sin oscilación en 8
  corridas, PDF corrupto de 20 MB no revienta, exclusividad probada en 7 proveedores.

⚠️ **Nota de arquitectura:** `matcher.py` importa `UPLOADS_DIR` de `database`, **no**
`FACTURAS_DIR` de `routers/facturas.py`. Un service que importa de un router invierte la
dependencia y produce import circular en cuanto el router importe el matcher.

---

## 8. ⬜ Lo que sigue abierto

### 🟡 El ~45% sin número degrada a fecha EN SILENCIO — depende de KIMS

Es el punto débil que queda, y **no es técnico**. Hoy "KIM no lo puso" y "KIM cambió el
formato y ya no lo encontramos" **se ven igual** desde el portal.

⭐ **Lo que hay que pedirle a KIMS** (idea de Mario): que cuando una factura **no** traiga
# de venta, **lo digan con una leyenda fija, idéntica siempre y en el mismo lugar**. Con
esa leyenda, la ausencia de ambas cosas se vuelve una **alerta** en vez de una degradación
silenciosa. Y que cuiden los ceros (16 dígitos).

### Lo que NO hay que volver a investigar

- ❌ **"Quitar los ceros de adelante"** — falla y además fabrica números con pinta válida
  (§2). Ya está probado.
- ❌ **El sufijo `-K` de los kits** — descartado el 2026-08-05: eran 8 de 1859 y el
  problema era el prefijo de bodega.
- ❌ **Agrupar CAUPLAS por `M######`** — medido: 0 casos de diferencia.

### El BUG A sigue siendo la tarea #1

1,042 ventas de carrito sin envío → sin proveedor → invisibles para el matcher. **No lo
arregla el # de venta**: el matcher sólo busca `WHERE e.proveedor_id = ?`. Ver §4 del
índice.
