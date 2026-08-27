# Módulo 2 — Publicaciones masivas

> Leer antes de tocar `services/aplicaciones_kg.py`, `generador_plantilla.py`,
> `precio_publicacion.py`, `parser_catalogo.py`, `perfiles_catalogo.py` o
> `routers/publicaciones.py`.

**Qué es:** un transformador **Excel → Excel**. Toma el catálogo de un proveedor
y devuelve la plantilla de 36 columnas que Gaby sube a Mercado Libre.
🔴 **No toca la API de ML ni ningún dato del Módulo 1** — las 4 reglas de API no
aplican aquí porque no hay una sola llamada de red.

## Actualización 2026-08-27 — nuevo master KG

El formato vigente es `nuevo-master-kg.xlsx`: hoja `BD_Catalogo`, una fila por
compatibilidad y agrupación por `Clave`. Se detecta por hoja + 17 encabezados;
el catálogo KG anterior sigue aceptado por su perfil independiente.

- Línea base real: **29,216 filas**, **4,023 SKU** y **9 filas con años
  inválidos**. Las filas inválidas se excluyen y se muestran con fila, datos y
  motivo; las filas repetidas se deduplican.
- `Precio` es una columna final opcional (costo sin IVA). Si falta, el análisis
  y la descarga continúan, con precio vacío y advertencia visible. Si una Clave
  tiene costos distintos tampoco se elige uno arbitrariamente.
- Una compatibilidad de un año genera una publicación. Con dos o más genera el
  rango `Inicio/Fin` y bloques cronológicos balanceados; los bloques se
  subdividen cuando haga falta para conservar el límite de 60 caracteres.
- El título es `Producto P/ Armadora Modelo Cilindrada Años`. Nunca lleva OEM ni
  Clave. Para caber, primero omite Armadora y después usa alias controlados del
  producto; nunca corta Modelo, motor, años o palabras.
- La descripción reúne todos los OEM, guías de compradores, especificaciones y
  características del SKU. Imagen 1–5 se copian; Imagen 6–10 quedan vacías.
- El cruce con Publicaciones ML es por **`Att_SellerSKU + Título`**, normalizado
  sólo en caso, acentos y espacios. Los paquetes de Q separados por `&` asocian
  el mismo título a cada SKU. Sólo se omite la variante que ya existe.
- `/analizar` reporta filas, SKU, compatibilidades válidas/inválidas,
  duplicados, precios, variantes y desglose por Producto. La UI permite filtrar
  por Producto y muestra una sección de registros excluidos.

**El pedido de Gaby (2026-08-19), textual:**
> *"que se unan ciertas columnas para formar un titulo, después el tema de
> descripción, que pueda ponerse una sola descripción donde yo pueda poner mi
> descripción base, pero lo que cambie sea el principio que es el tema de
> equivalencias o compatibilidades […] cargar el excel por proveedor y
> dependiendo de los campos, me sirva para generar una plantilla lista para subir
> a mercado libre, con el fin de no hacerlo tan manual"*

---

## 1. Lo que hace, medido contra los archivos reales

| | |
|---|---|
| Catálogo KeepOnGreen | **3,676 piezas** |
| Ya publicadas | **69** |
| **Faltan por publicar** | **3,607** |
| **Publicaciones que genera** | **6,296** (×1.75) |
| Aplicaciones descartadas por venir cortadas | 755 |

---

## 2. ⭐ UNA PIEZA GENERA N PUBLICACIONES

Es lo que vuelve masivo el módulo. En la plantilla real de Gaby, **83 filas
salieron de sólo 22 SKUs (×3.8)**: cada aplicación de la columna "Aplicaciones
Principales" es una publicación distinta, el SKU / precio / descripción /
imágenes se repiten **idénticos** y **lo único que cambia es el TÍTULO**.

```
39300-4A800-Z  ->  Sensor Map ... P/ Truck H200 2.5 2016 2017 2018
               ->  Sensor Map ... P/ Truck H200 2.5 2019 2020
               ->  Sensor Map ... P/ Solati H350 2020 2021 2022
```

Así la misma pieza compite por más búsquedas en ML.

---

## 3. 🔴 Las 5 reglas que no se pueden rediseñar

Fijadas en `backend/scripts/test_publicaciones_masivas.py` (**45/45**), con
**4 mutaciones verificadas** que lo ponen en rojo.

### 3.1 La marca y el modelo SE HEREDAN

```
GM AVALANCHE V8 5.3L 2007-2010 | V8 6.0L 2007-2009 | SILVERADO 1500 V8 4.8L ...
                                 ^^^^^^^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^^^^^^^
                                 el MISMO Avalanche   otro modelo, sigue GM
```

`V8 6.0L 2007-2009` **no es un modelo llamado "V8"**: es el mismo Avalanche con
otro motor. Sin la herencia el título sale `Bomba de agua P/ V8 6.0` — basura.

⚠️ **Pero si el fragmento trae su PROPIA marca, ésta REEMPLAZA a la heredada:**
en `ST CORDOBA … | IBIZA … | VW CROSSFOX …` el Crossfox es un **VW, no un Seat**.
Sin esa distinción salía `ST VW Crossfox`, una marca que se contradice sola.

🔴 **`_MARCAS` es una lista CERRADA a propósito.** El primer token **no siempre
es marca**: el catálogo de KG trae filas que arrancan con el código de la pieza
(`MANG` 110, `RAD` 70, `TA` 121, `BA` 38). Tomarlos por marca metería basura en
el título. **Ante la duda, se hereda.**

### 3.2 Sin años NO se publica

El export del proveedor **recorta la celda a 90 caracteres** y deja restos a
media palabra (`'CA'`, `'V8'`, `'SEBRING V6 3.5L'`, `'KA L4 1.6L 2'`).

Se marcan `truncada` y **se excluyen**. El criterio es **la ausencia de años**,
no la longitud: el título de Gaby siempre los lleva y una compatibilidad sin años
no le dice nada al comprador.

🔴 **Publicar una pieza diciendo que sirve para menos autos de los que sirve es
peor que no publicarla.** No se adivina lo que falta: la UI las reporta para que
Gaby le pida a KG el archivo completo.

### 3.3 El 13% de comisión SE DIVIDE, no se suma

La fórmula que describió Gaby tiene un **problema circular**: la comisión de ML
se cobra sobre el precio FINAL, pero el precio final depende de la comisión.

```
base   = costo × (1 + iva) × (1 + utilidad)
precio = (base + envio) / (1 - comision)      <- DIVIDE
```

Medido con `KGP-1449` (costo 346.84, envío 100):

| Método | Precio | Comisión real | Utilidad |
|---|---|---|---|
| sumando 13% | $786 | $102 | ❌ **$14 corta** |
| **dividiendo** | **$801** | $104 | ✅ los 50% completos |

Son ~$15 por pieza; **sobre 3,607 publicaciones no es un redondeo**.
⬜ **Falta decírselo a Gaby**: hoy publica con el método que le deja la utilidad corta.

### 3.4 Precio incalculable → celda VACÍA, nunca 0.0

`None` y `0.0` son **estados distintos**: un 0.0 se subiría a ML como precio real
y la pieza se vendería regalada. Mismo criterio que `total_neto` en el Módulo 1.

### 3.5 La columna Q trae paquetes con `&`

`'NO625HB&NO625HB'` — hay que partirla, o un SKU publicado **dentro de un
paquete** se contaría como faltante. Medido: 14,946 publicaciones traen sólo
**626 SKU únicos** (porque N publicaciones comparten pieza — la misma expansión
que genera este módulo).

---

## 4. La descripción: cabeza variable + cuerpo fijo

Exactamente como lo pidió Gaby:

```
BOMBA DE AGUA                 <- nombre de la pieza      VARIABLE
OEM: KGP-1441                 <- la clave del proveedor  VARIABLE
Compatibilidades:             <- TODAS las aplicaciones  VARIABLE
FT PANDA L4 1.2L 2007 2008
──────────────────────────────────────────────
IMPORTANTE / GARANTÍA / …     <- ~2,000 caracteres       FIJO
```

El cuerpo se guarda una vez por proveedor y no se vuelve a tocar. Las
compatibilidades listan **todas** las aplicaciones de la pieza, no sólo la de esa
fila: el comprador quiere ver si le sirve a su coche.

---

## 5. ⬜ Lo que queda PENDIENTE

### 5.1 🔴 EL COSTO DE ENVÍO — decisión de Mario: avanzar sin él

`ENVIO_POR_LINEA` está **vacío** y el default es **0.0**, así que hoy
**el precio sale SIN envío y queda por debajo del real**. Es un hueco
**consciente y visible** (la UI se lo advierte a Gaby), no un olvido.

**El eje correcto es la LÍNEA de producto** (col B: RADIADOR, TOMA DE AGUA…),
**NO el precio ni el peso.** Gaby lo razonó así:
> *"podría ser por categoría, radiadores por ejemplo seguro es más de 120, tomas
> de agua 100, algo así"*

⚠️ **No se puede derivar del catálogo: NO trae peso ni dimensiones.** Tiene que
darlo ella. Son ~30 líneas; llenando las 10 más grandes se cubre el 80%.

❌ **Se evaluó y se DESCARTÓ estimarlo por rango de precio** con la columna AA
`CostoEnvio` del reporte de Publicaciones ML (9,924 filas, mediana $59.6–$110).
Los datos son de llantas y sensores — **sólo 69 de las 3,676 claves de KG están
publicadas** — así que sería aplicar el patrón de *otros* productos a un catálogo
nuevo. **No implementarlo sin datos de KG.**

**Para conectarlo:** llenar `ENVIO_POR_LINEA` en `precio_publicacion.py`. El
motor ya lo consume.

### 5.2 Imágenes según formato

Confirmado por Gaby:
> *"las subo a autozur, copio el link que me arroja y lo pongo, pero esto
> seguiría siendo manual, es decir, que no venga en el excel creado"*

El catálogo legado no trae fotos y en ese formato siguen vacías. El master nuevo
sí incluye Imagen 1–5 y el generador las copia automáticamente; Imagen 6–10
permanece vacía.

### 5.3 Sólo hay perfil de KG

`PERFILES` en `perfiles_catalogo.py` tiene **sólo KeepOnGreen**. Los otros 4
proveedores dan **400 con mensaje claro**, no un archivo mal armado.

**Agregar uno es escribir un perfil (dónde vive cada columna), no un módulo
nuevo:** el parseo del catálogo es lo único específico por proveedor; la
expansión, la descripción, el cruce y la generación son motor común.

### 5.4 La categoría de ML se escribe a mano

Hoy Gaby teclea `MLM163963` y aplica **a todo el archivo**. Si un catálogo mezcla
familias (radiadores y bombas no van en la misma categoría), habría que mapear
**línea → categoría**. No se implementó porque no sabemos si le estorba.

---

## 6. Cómo lo usa Gaby

1. **Publicaciones masivas** en el menú → elige proveedor
2. Sube el **catálogo** y (opcional) el reporte de **Publicaciones ML**
3. Ve variantes estimadas, existentes y faltantes, y **filtra por Producto**
4. Ajusta marca, categoría y los % del precio
5. **Descarga la plantilla**, revisa exclusiones/precios y la sube a ML

---

## 7. Verificación

- `backend/scripts/test_publicaciones_masivas.py` → **65/65** (conserva las 45
  pruebas originales y suma 20 del master nuevo)
- **4 mutaciones** lo ponen en rojo: sumar el 13%, quitar la herencia de marca,
  no marcar truncadas, devolver 0.0 en vez de None
- Flujo HTTP analizar → filtrar → generar probado contra el master real
- Build de CRA limpio
- 🔴 Ruta protegida con `AdminOnly` + `require_admin`: **un proveedor no debe ver
  los costos de otro**

⚠️ **Gotcha de entorno:** el Python del sistema (3.9.6) **no tiene `rapidfuzz`**,
así que los tests del Módulo 1 no corren en local. Es **preexistente** y no afecta
a este módulo: no importa nada del Módulo 1 (verificado con grep).
