# Master GONHER — Publicaciones masivas

Referencia canónica del formato `master_gonher`. Este flujo es exclusivamente
Excel → Excel: no usa base de datos, imágenes ni API de Mercado Libre.

## Contrato

- Proveedor y marca canónica: `GONHER`.
- Se procesa la hoja `Gonher`, o una hoja renombrada con la huella completa. Se
  ignoran `Hoja1` y `GC`. Una huella incompleta falla enumerando encabezados.
- Se agrupa por D `Filtro`, usado como SKU, número de parte y `Modelo`.
- A+B forma el producto (`Filtro de aceite`, etc.). C es código actual, J tipo,
  K:Q rosca/medidas, R:S OEM, T FRAM, U Interfill y X costo. Código de barras y
  código SAT no se exportan.
- Las dos columnas `Ø Int. cm` se leen por ocurrencia. Vacíos, `^^`, `- NA -`,
  `N/A`, `ND` y `-` se omiten. Las medidas se expresan en cm.
- Año contiene uno o dos años completos entre 1900 y 2100; un rango invertido se
  excluye por fila sin eliminar otras aplicaciones del SKU.
- Cada fila válida produce una variante con su año/rango original. El título
  intenta producto + marca + modelo + motor; después reduce el motor a
  arquitectura/cilindrada; después omite marca. Nunca recorta. Si no cabe en 60,
  la variante se excluye con fila, SKU y motivo.
- La deduplicación es por SKU + título normalizado. La descripción consolida por
  SKU producto/tipo, código actual, número de parte, rosca, medidas, OEM,
  equivalencias, compatibilidades y descripción base.
- Un solo costo positivo usa la fórmula compartida. Costo vacío, inválido o
  conflictivo deja Precio y Envío Gratis vacíos y genera advertencia.
- Stock es `None`: `Cantidad` y `GONHER` quedan vacías; AG, CAUPLAS, KG, KIM,
  MATRIZ y VAZLO reciben cero. No se generan imágenes.
- La plantilla tiene 37 columnas: las 36 posiciones anteriores no cambian y
  `GONHER` se agrega al final.

## API e interfaz

`GET /api/publicaciones/proveedores` incluye GONHER. Analizar y generar aceptan
`codigo_bodega=GONHER`; no requieren stock ni archivo auxiliar. El análisis
expone filas, SKU, aplicaciones válidas/inválidas, duplicados, exclusiones de
título, SKU sin precio y `por_linea`. Las tandas se filtran por Línea (ACEITE,
AIRE, GASOLINA, CABINA y COMBUSTIBLE). La UI recuerda que stock e imágenes son
manuales y que `GC` no se procesa.

## Línea base real

Archivo `archivos/publicaciones-masivas/master-gohner.xlsx`: 20,054 filas, 861
SKU, 4 aplicaciones con años inválidos y 26 SKU sin costo positivo. Con la regla
estricta de no recortar ni abreviar el modelo, el archivo disponible produjo 131
títulos imposibles, 71 duplicados y 19,848 publicaciones. La estimación previa era
130/71/19,849; la diferencia es una fila cuyo título mínimo todavía mide 61 caracteres.

## Verificación

Ejecutar `test_publicaciones_gonher.py`, las suites KG/CAUPLAS/KIMS y el build del
frontend. Con el master real, repetir la línea base anterior.

## Regla de mantenimiento

GONHER no debe mezclarse con lectores legado, KG, CAUPLAS o KIMS ni activar llamadas
externas. La cascada del título sólo puede quitar marca después de reducir el motor a
arquitectura/cilindrada; modelo y años son obligatorios y nunca se recortan. Los casos
que sigan sobre 60 caracteres se conservan como exclusiones visibles. La discrepancia
histórica de una fila de 61 caracteres no autoriza una abreviatura especial: si se cambia
la política, deben actualizarse las métricas de línea base, esta referencia y la prueba.
