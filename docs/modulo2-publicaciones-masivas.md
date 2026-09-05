# Módulo 2 — Publicaciones masivas

> Leer antes de tocar `services/aplicaciones_kg.py`, `generador_plantilla.py`,
> `precio_publicacion.py`, `parser_catalogo.py`, `perfiles_catalogo.py` o
> `routers/publicaciones.py`.
>
> Para detección del master y el contrato `Producto` → `linea` → `por_linea`,
> leer también `docs/master-kg-categorias-producto.md` completo.
> Para tocar el master CAUPLAS, su validación, títulos, descripción o métricas,
> leer también `docs/master-cauplas-publicaciones-masivas.md` completo.
> Para tocar Precio, `Envio Gratis(si,no)`, las 36 columnas o `CONSTANTES`, leer
> también `docs/envio-gratis-precio-final.md` completo.
> Para tocar URLs, red, resolución o columnas Imagen 1–10, leer también
> `docs/validacion-imagenes-catalogo.md` completo.

**Qué es:** un transformador **Excel → Excel**. Toma el catálogo de un proveedor
y devuelve la plantilla de 36 columnas que Gaby sube a Mercado Libre.
🔴 **No toca la API de ML ni ningún dato del Módulo 1**. Cuando el catálogo o el
CSV auxiliar aporta URLs, existe una excepción controlada: las valida contra los hosts
exactos declarados en su perfil, confirma que respondan como imagen y que ambos
lados midan al menos 1200 px. Usa `GET` parcial, sin redirecciones ni destinos
arbitrarios del Excel; no abre una capacidad genérica de red ni toca ML. Tiene
un máximo de 25,000 URLs únicas y 4 minutos: una URL que no se confirma queda
vacía, nunca sale como no verificada.

## Actualización 2026-08-31 — master CAUPLAS

CAUPLAS está soportado mediante el formato independiente `master_cauplas`, detectado
por encabezados antes de KG. La regla completa —columnas, años, `All`, títulos,
descripción, precios, imágenes, filtros, métricas y aislamiento— vive en
`docs/master-cauplas-publicaciones-masivas.md` y debe leerse completa antes de tocarlo.

## Actualización 2026-09-03 — fotos CAUPLAS por CSV

CAUPLAS exige además el CSV exportado de ImageKit con columnas `Name` y `URL`, tanto
en análisis como en generación. `SKU.ext` ocupa Imagen1 y `SKU-2.ext`…`SKU-10.ext`
sus posiciones posteriores. Primero se intenta el nombre completo contra un SKU existente;
sólo después se interpreta el sufijo, para no romper SKU legítimos con guion numérico.
La galería se copia a cada variante del SKU y el archivo puede llenar Imagen1–Imagen10.

El CSV no crea SKU ni bloquea por una foto mala: URL repetida, más allá de la 10,
sin match o inválida se omite y se reporta. La red sólo admite el host exacto
`ik.imagekit.io`, HTTPS, sin redirecciones, respuesta de imagen y mínimo 1200×1200.
KG conserva sus primeras cinco imágenes y no acepta el CSV. Contrato completo:
`docs/master-cauplas-publicaciones-masivas.md` §11 y
`docs/validacion-imagenes-catalogo.md`.

## Actualización 2026-09-05 — stock por SKU desde el master

El inventario dejó de ser un ajuste global de la pantalla. `stock` es ahora una
columna obligatoria del catálogo para CAUPLAS, KG y cualquier perfil futuro; se
detecta por encabezado normalizado (mayúsculas, espacios, acentos y posición no
importan). El lector conserva el valor en cada SKU consolidado y lo propaga a
todas sus publicaciones derivadas.

La celda debe contener un entero mayor o igual a cero. Un valor numérico `10.0`
de Excel se acepta como `10`; vacío, texto, negativo o decimal se rechaza. Si
un SKU repetido trae stocks distintos, se excluye el SKU completo. Todas las
exclusiones se devuelven con fila, clave y motivo para mostrarlas en la interfaz.
Stock `0` es válido y se publica como agotado.

En el XLSX de salida, `Cantidad` y la columna de bodega del proveedor reciben el
stock del SKU; las otras bodegas reciben `0`. El endpoint `/api/publicaciones/generar`
y la pantalla ya no aceptan ni envían una cantidad global. El endpoint de análisis
expone también `errores_total` y `errores` para perfiles legado/futuros.

## Actualización 2026-08-27 — nuevo master KG

El formato vigente es `nuevo-master-kg.xlsx`: una fila por compatibilidad y
agrupación por `Clave`. Se detecta por sus 17 encabezados estructurales más
`stock`, aunque
la hoja ya no se llame `BD_Catalogo`; ese nombre conserva preferencia. Un archivo
que parece master pero está incompleto se rechaza con los encabezados faltantes,
en vez de interpretarse como legado. El catálogo KG anterior sigue aceptado por
su perfil independiente.

- Línea base real: **29,216 filas**, **4,021 SKU utilizables** y **9 filas con años
  inválidos**. Las filas inválidas se excluyen y se muestran con fila, datos y
  motivo; las filas repetidas se deduplican.
- `Gran Mayoreo` y `Precio` son encabezados equivalentes para la columna final
  opcional de costo sin IVA. `Gran Mayoreo` no es el precio final de Mercado
  Libre; la plantilla generada sigue usando `Precio`. Si ambos faltan, el
  análisis y la descarga continúan, con precio vacío y advertencia visible. Si
  una Clave tiene costos distintos tampoco se elige uno arbitrariamente.
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

## 3. 🔴 Las 6 reglas que no se pueden rediseñar

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

### 3.6 “Envío Gratis” se deriva del precio final

`Envio Gratis(si,no)` ya no es una constante de la plantilla. Se decide por cada
publicación usando exclusivamente su precio final ya calculado —después de Gran
Mayoreo, IVA, utilidad, comisión y el envío configurado—: menos de `$299.00`
genera `No`, y desde `$299.00` inclusive genera `Si`. Si el precio no se puede
calcular, tanto `Precio` como `Envio Gratis(si,no)` quedan vacíos.

Este umbral no modifica la fórmula de precio ni resuelve el costo de envío por
línea que continúa pendiente en §5.1.

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

El catálogo legado no trae fotos y en ese formato siguen vacías. Si un master
aporta Imagen 1–5, el generador conserva cada posición sólo cuando la URL usa un
dominio autorizado para ese proveedor, responde correctamente y mide al menos
1200×1200. Se revisa una vez por URL única y no se desplazan las demás imágenes.
Imagen 6–10 permanece vacía. La pantalla informa las válidas y cada causa de
exclusión. Regla técnica completa: `docs/validacion-imagenes-catalogo.md`.

### 5.3 Hay perfiles de KG y CAUPLAS

`PERFILES` en `perfiles_catalogo.py` tiene **KeepOnGreen y CAUPLAS**. KIM, AG y
VAZLO dan **400 con mensaje claro**, no un archivo mal armado.

**Agregar uno es escribir un perfil (dónde vive cada columna), no un módulo
nuevo:** el parseo del catálogo es lo único específico por proveedor; la
expansión, la descripción, el cruce y la generación son motor común.

El master CAUPLAS se detecta por encabezados antes del detector KG y se reporta
como `master_cauplas`. Su línea base es 15,612 filas, 4,214 SKU observados, 370
universales, 15,202 compatibilidades con años y 40 exclusiones. Consolida I/J,
OEM, equivalencias U–Z y medidas de mangueras. La columna PNG del master sigue
sin usarse; las fotos provienen exclusivamente del CSV obligatorio de ImageKit.

### 5.4 La categoría de ML se escribe a mano

Hoy Gaby teclea `MLM163963` y aplica **a todo el archivo**. Si un catálogo mezcla
familias (radiadores y bombas no van en la misma categoría), habría que mapear
**línea → categoría**. No se implementó porque no sabemos si le estorba.

---

## 6. Cómo lo usa Gaby

1. **Publicaciones masivas** en el menú → elige proveedor
2. Sube el **catálogo**, para CAUPLAS también el CSV obligatorio de ImageKit, y
   opcionalmente el reporte de **Publicaciones ML**
3. Ve variantes estimadas, existentes y faltantes, y **filtra por Producto**
4. Ajusta marca, categoría y los % del precio
5. **Descarga la plantilla**, revisa exclusiones/precios y la sube a ML

---

## 7. Verificación

- `backend/scripts/test_publicaciones_masivas.py` → **77/77**, incluyendo el
  umbral de Envío Gratis y la validación del Excel generado
- **4 mutaciones** lo ponen en rojo: sumar el 13%, quitar la herencia de marca,
  no marcar truncadas, devolver 0.0 en vez de None
- Flujo HTTP analizar → filtrar → generar probado contra el master real
- Build de CRA limpio
- 🔴 Ruta protegida con `AdminOnly` + `require_admin`: **un proveedor no debe ver
  los costos de otro**

⚠️ **Gotcha de entorno:** el Python del sistema (3.9.6) **no tiene `rapidfuzz`**,
así que los tests del Módulo 1 no corren en local. Es **preexistente** y no afecta
a este módulo: no importa nada del Módulo 1 (verificado con grep).
