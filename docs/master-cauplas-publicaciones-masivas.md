# Master CAUPLAS en Publicaciones Masivas

> Referencia técnica canónica para cualquier cambio relacionado con el master
> CAUPLAS dentro del Módulo 2. Leer completa antes de tocar su detección, parser,
> títulos, descripción, métricas, filtros o generación de plantilla.
>
> Esta regla pertenece exclusivamente al transformador Excel → Excel. No autoriza
> cambios en el Módulo 1 ni llamadas a la API de Mercado Libre.

## 1. Regla operativa que no se debe romper

CAUPLAS tiene un lector exclusivo identificado como `master_cauplas`. Se detecta
por encabezados normalizados y se despacha antes de intentar reconocer el master
KG. Los dos formatos comparten la expansión, el cruce y la plantilla final donde
corresponde, pero no comparten el parser de origen.

Las reglas fijas son:

1. El master CAUPLAS se reconoce por contenido, no por nombre de archivo u hoja.
2. Un archivo con huella fuerte pero incompleto falla con los encabezados faltantes.
3. El parser KG conserva su ruta y comportamiento anteriores.
4. `fecha` (K) valida `inicio`/`fin` (L–M); `compatibilidad` (AB) no da los años.
5. `All` significa compatibilidad universal y genera `Producto P/ Universal`.
6. Una fila con años inválidos o contradictorios se excluye y reporta; no se adivina.
7. Un costo ausente, no cacheado o contradictorio queda en `None`, nunca en cero.
8. La columna O se ignora porque sólo contiene nombres `.png`; las imágenes llegan
   exclusivamente del CSV obligatorio de ImageKit y se cruzan por SKU.
9. La plantilla conserva exactamente 36 columnas y el stock va sólo en CAUPLAS.
10. No se crean tablas ni migraciones. La única red permitida es la validación
    controlada de `https://ik.imagekit.io` descrita en §11.

Estas reglas están fijadas en
`backend/scripts/test_publicaciones_cauplas.py` y conviven con las 77 regresiones
de KG en `backend/scripts/test_publicaciones_masivas.py`.

## 2. Archivo real y línea base

Archivo local de referencia, ignorado por Git:

`archivos/publicaciones-masivas/master-cauplas.xlsx`

Medición vigente del archivo real:

- 15,612 filas de datos.
- 4,214 SKU observados antes de excluir filas inválidas.
- 4,211 SKU con al menos una compatibilidad utilizable.
- 42 categorías de producto normalizadas.
- 370 filas universales (`All`).
- 15,202 filas con años válidos.
- 40 filas excluidas por años inválidos, mal formados o contradictorios.
- 43,436 variantes generables después de deduplicar SKU+título.
- 3 variantes adicionales no caben en 60 caracteres sin cortar modelo o años y
  se reportan como exclusiones de título.

Los 4,214 SKU son la métrica del master recibido. Tres SKU sólo aparecen en filas
inválidas y por eso no producen publicaciones; no debe ocultarse ninguna de las dos
cifras ni convertirlas artificialmente en una sola.

## 3. Contrato de columnas

Los encabezados se normalizan sin distinguir mayúsculas, acentos ni espacios
repetidos. Las columnas operativas son:

- B, `armadora`: armadora del vehículo.
- C, `modelo`: modelo completo; nunca se corta.
- E, `cilindrada`: cilindrada usada en título y compatibilidad.
- I, `uso`: producto/categoría y comienzo del título.
- J, `especificaciones`: especificación asociada al producto.
- K, `fecha`: texto que valida el rango o contiene `All`.
- L, `inicio`: primer año.
- M, `fin`: último año.
- N, `cauplas`: SKU del proveedor.
- O, `imagen`: nombre de archivo PNG; se ignora deliberadamente.
- P–S, `alto`, `largo`, `diametro1`, `diametro2`: medidas sin unidad añadida.
- U–Z: equivalencias Continental, Dayco, Gates, KeepOnGreen, Meisterzats y Tepeyac.
- AA, `oe`: códigos OEM.
- AB, `compatibilidad`: texto informativo; no es fuente de años.
- AF, `Precio`: costo base sin IVA usado por la fórmula vigente.

La detección exige los encabezados utilizados por el parser. Una hoja se considera
un CAUPLAS incompleto cuando conserva al menos cinco anclas entre armadora, modelo,
uso, fecha, cauplas, oe y Precio. Eso evita que un archivo parcialmente exportado
caiga en KG o en el formato legado.

## 4. Normalización y agrupamiento por SKU

El SKU se toma de N y pasa por `_texto`:

- `7656` permanece `7656`.
- `7656.0` se convierte en `7656`.
- un SKU textual conserva sus caracteres y ceros iniciales.
- un SKU vacío excluye la fila con número y motivo.

Las piezas se agrupan internamente por SKU en mayúsculas. Dentro del grupo se
conservan todos los valores únicos; no se escoge silenciosamente uno cuando los
datos difieren.

Se consolidan:

- combinaciones únicas de producto I + especificación J;
- compatibilidades validadas;
- códigos OEM de AA separados por `|`;
- equivalencias U–Z, separadas por marca y sin duplicados;
- todas las medidas no vacías y distintas de cero;
- todos los costos positivos observados.

Si hay exactamente un costo positivo, ése es el costo del SKU. Si no hay ninguno,
el costo queda vacío. Si hay más de uno, se reporta `Precios diferentes dentro del
SKU` y también queda vacío.

## 5. Validación de años

`_validar_anios_cauplas` aplica este orden:

1. Si K normalizado es `All`, la fila es universal; L–M no son necesarios.
2. En cualquier otro caso, L y M deben ser enteros entre 1900 y 2100.
3. Inicio no puede ser mayor que Fin.
4. Se extraen de K los años completos de cuatro dígitos.
5. El menor y mayor año de K deben coincidir exactamente con L y M.

Ejemplos válidos:

- K `1997`, L `1997`, M `1997`.
- K `2004-2008`, L `2004`, M `2008`.
- K `All`, L/M vacíos.

Ejemplos excluidos:

- K `2011-2015`, L `2011`, M `2012`.
- K `2014-217`, L `2014`, M `2017`.
- L/M vacíos sin `All`.
- Inicio posterior a Fin.

Cada exclusión conserva fila, SKU, armadora, modelo, K, L, M y motivo. AB nunca se
usa para corregir una contradicción.

## 6. Categorías y filtros

I (`uso`) se normaliza con `_producto_canonico`, igual que el campo Producto del
master KG. El contrato HTTP conserva los nombres históricos `linea`, `por_linea`
y `lineas`.

Un mismo SKU puede pertenecer a más de un producto. Por eso CAUPLAS guarda
`lineas` y cada compatibilidad conserva su propio `producto`. Al filtrar una tanda,
el router clona la pieza y limita únicamente sus compatibilidades a los productos
seleccionados; no debe generar categorías hermanas que el usuario no eligió.

## 7. Construcción de títulos

Una compatibilidad normal intenta esta cascada:

1. `I P/ B C E años`.
2. Si supera 60 caracteres, `I P/ C E años`.
3. Si todavía supera 60, abreviar únicamente I con un alias controlado.

Aliases permitidos:

- `Depósito de Refrigerante de Motor` → `Depósito Refrigerante`.
- `Manguera Radiador` → `Mang. Radiador`.
- `Manguera Calefacción` → `Mang. Calef.`.
- `Manguera Refrigeración` → `Mang. Refrig.`.
- `Manguera Descarga de Gases` → `Mang. Descarga Gases`.
- `Manguera Para Frenos Hidráulicos` → `Mang. Freno Hidráulico`.
- `Manguera Circulación de Aire` → `Mang. Circulación Aire`.
- `Refrigeración` → `Refrig.`.

Nunca se recortan modelo, cilindrada, años o palabras. Si ninguna alternativa cabe,
la variante se excluye con `Título excede 60 caracteres`.

Una fila universal genera un solo título `Producto P/ Universal`, usando el alias
únicamente si fuera indispensable para respetar el límite.

## 8. Expansión cronológica

CAUPLAS replica la expansión del master KG sin llamar al parser legado KG:

- un solo año genera una publicación;
- dos o más años generan primero `Inicio/Fin`;
- también generan dos bloques cronológicos balanceados;
- un bloque que no cabe se divide recursivamente;
- un año individual que todavía no cabe se excluye.

Después se deduplica globalmente por `SKU + título normalizado`. La normalización
del título ignora caso, acentos y espacios, pero no elimina contenido del vehículo.

## 9. Descripción consolidada

Todas las variantes de un SKU comparten la misma descripción, en este orden:

1. `Productos y especificaciones`, con cada combinación única I/J.
2. `OEM`, con códigos únicos separados por `|`.
3. `Equivalencias`, una línea por marca y códigos únicos separados por `|`.
4. `Medidas`, sólo si algún producto I del SKU contiene `manguera`.
5. `Compatibilidades`, construidas sólo con B, C, E y L–M ya validados.
6. Descripción base de RELUVSA.

En medidas se muestran Alto, Largo, Diámetro 1 y Diámetro 2. Los ceros y vacíos se
omiten y no se inventan unidades. Si una medida tiene varios valores, se conservan
todos separados por `|`.

## 10. Precio, Envío Gratis y stock

El costo AF alimenta `calcular_precio` sin una fórmula especial para CAUPLAS:

```text
base = costo × (1 + IVA) × (1 + utilidad)
precio = (base + envío) / (1 − comisión)
```

AF contiene fórmulas externas en el archivo real, pero conserva valores cacheados.
`openpyxl` se abre con `data_only=True`, por lo que consume ese valor. Si un archivo
futuro trae la fórmula sin caché, el costo y Precio quedan vacíos.

`Envio Gratis(si,no)` continúa dependiendo exclusivamente del precio final:

- menor a 299.00 → `No`;
- desde 299.00 → `Si`;
- precio vacío → Envío Gratis vacío.

La salida mantiene las 36 columnas canónicas. `Cantidad` y la columna CAUPLAS
reciben el stock elegido; AG, KG, KIM, MATRIZ y VAZLO reciben cero.

## 11. Imágenes

O contiene valores como `7656.png`, no URLs públicas aceptadas por Mercado Libre.
No se copian ni se construyen rutas a partir de ellos. CAUPLAS exige un CSV de
ImageKit con las columnas `Name` y `URL` en ambos endpoints. El nombre se cruza
contra los SKU consolidados en este orden:

1. `SKU.ext` es Imagen1.
2. Se intenta primero el nombre completo (`SKU-2.ext` puede ser un SKU real).
3. Sólo si no existe se interpreta `SKU-2.ext`, `SKU-3.ext`, etc. como Imagen2,
   Imagen3 y así sucesivamente.

Se guardan hasta diez posiciones, se omiten duplicados, colisiones, posiciones
mayores a 10 y nombres sin SKU. La misma galería se copia a todas las variantes
del SKU. Las URL se validan después con `filtrar_imagenes`: exclusivamente HTTPS
en el host exacto `ik.imagekit.io`, sin redirecciones, con contenido de imagen y
resolución mínima 1200×1200. Una foto inválida o inaccesible queda vacía; no
interrumpe la descarga ni desplaza las demás. Ver
`docs/validacion-imagenes-catalogo.md` para los límites y defensas SSRF.

## 12. Cruce contra Publicaciones ML

El reporte opcional conserva el contrato existente:

- título en B;
- Att_SellerSKU en Q;
- SKU de paquetes separados por `&`;
- llave de cruce `SKU + título normalizado`.

Sólo se omite la variante exacta que ya existe. La presencia de otra publicación
del mismo SKU no elimina sus demás títulos.

## 13. API e interfaz

`GET /api/publicaciones/proveedores` devuelve CAUPLAS y KG, además de la marca
predeterminada de cada uno.

`POST /api/publicaciones/analizar` devuelve `formato: master_cauplas` y conserva los
campos usados por KG. Añade `universales` y reporta filas, SKU, compatibilidades,
duplicados, precios, variantes, exclusiones y desglose por producto. Exige
`imagenes_cauplas` y devuelve además `fotos` con los contadores del cruce.

`POST /api/publicaciones/generar` exige el mismo campo `imagenes_cauplas`. La marca
vacía usa `CAUPLAS`; el campo sigue siendo editable. Sus headers de validación
reportan las URLs realmente aceptadas o eliminadas.

La interfaz muestra para CAUPLAS:

- compatibilidades universales;
- aviso de equivalencias y medidas;
- uploader obligatorio del CSV de ImageKit y resumen de su cruce;
- aviso de que Imagen1–Imagen10 se llenan sólo después de validar las URLs;
- SKU sin costo o con costos contradictorios;
- filtro por categoría de producto.

## 14. Aislamiento respecto de KG y Módulo 1

No mover la lógica CAUPLAS dentro de `aplicaciones_kg.py`. El dispatch vive en
`leer_catalogo_detallado`: CAUPLAS primero, master KG después y legado al final.

Este desarrollo no toca base de datos, autenticación, ventas, facturas, matcher,
OAuth, webhooks ni API de Mercado Libre. Publicaciones Masivas sigue siendo un
transformador local Excel → Excel.

## 15. Verificación obligatoria

Desde `backend/`:

```bash
/usr/bin/python3 scripts/test_publicaciones_cauplas.py
/usr/bin/python3 scripts/test_publicaciones_masivas.py
```

Resultados fijados tras el CSV de imágenes:

- CAUPLAS: 37/37.
- KG: 81/81.

Desde `frontend/`:

```bash
npm run build
```

Con los archivos reales, verificar además el flujo HTTP analizar → generar con
CAUPLAS y KG. No declarar una prueba visual en producción si no hubo una sesión
autenticada disponible.

## 16. Archivos involucrados

- `backend/services/perfiles_catalogo.py`: registro y marca predeterminada.
- `backend/services/parser_catalogo.py`: detección, validación y consolidación.
- `backend/services/generador_plantilla.py`: títulos, descripción y variantes.
- `backend/services/imagenes_cauplas.py`: lectura, cruce y asignación del CSV.
- `backend/routers/publicaciones.py`: API, métricas, cruce y filtro.
- `frontend/src/pages/Publicaciones.jsx`: presentación específica por formato.
- `backend/scripts/test_publicaciones_cauplas.py`: contrato de regresión CAUPLAS.
- `backend/scripts/test_publicaciones_masivas.py`: línea base que protege KG.

## 17. Alcance excluido

Quedan fuera de esta regla:

- publicar imágenes o modificar publicaciones mediante la API de Mercado Libre;
- definir categorías MLM automáticamente;
- inventar costo de envío por producto;
- corregir el master de origen;
- modificar el Módulo 1.
