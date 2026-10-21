# Master AG — Publicaciones masivas

ARGENPARTS se identifica en el portal como `AG` y se muestra como **ARGENPARTS**. Es un flujo local Excel → Excel: no llama a Mercado Libre.

## Archivos obligatorios

1. `Master AG.xlsx`, detectado por los encabezados `Producto`, `Código`, `SYD`, `GROB`, `KYB`, `BOGE`, `VAZLO`, `Marca`, `Aplicación`, `Desde`, `Hasta`, `Posición`, `Distribuidor` y `stock`.
2. `FOTOS AG.xlsx`, leído hasta en sus diez hojas. Cada fila aporta Servidor, Carpeta, Nombre y Extensión para formar la URL.

`Código` es el SKU; `Distribuidor` es costo sin IVA y `stock` es un entero obligatorio mayor o igual a cero. Costo o stock distintos, vacíos o inválidos en filas del mismo SKU excluyen el SKU completo. Una fila válida es una compatibilidad; años ausentes, inválidos o invertidos se excluyen y se reportan.

Las equivalencias SYD/GROB/KYB/BOGE/VAZLO y todas las compatibilidades del SKU se consolidan en la descripción. El título conserva el formato `Producto Posición para Marca Aplicación Años`, usando sólo abreviaturas controladas. Cuando es necesario se quita el paréntesis de Aplicación sólo del título. Si aún supera 60 caracteres, los bloques de años se subdividen; los años que individualmente no quepan se reportan. Nunca se corta una palabra. La descripción conserva el texto completo.

Una compatibilidad de varios años genera un título con el rango completo y dos títulos con bloques cronológicos balanceados. Los bloques se subdividen recursivamente si exceden 60 caracteres; si un año individual tampoco cabe, queda reportado como excedido. La deduplicación conserva SKU, producto, posición, marca y aplicación, y elimina títulos repetidos de filas equivalentes del master.

## Fotos

El nombre completo del SKU es Imagen1; después se aceptan `SKU_2`…`SKU_10` y `SKU-2`…`SKU-10`. El nombre completo tiene prioridad para no interpretar un SKU legítimo terminado en número como sufijo. Se ignoran combos, kits, duplicados, posiciones mayores a 10 y SKU que no existen en el master; análisis devuelve detectadas, cruzadas, omitidas, sin match y SKU sin fotos.

Al generar, las URLs pasan el candado común: únicamente HTTPS en `apimagenes.s3.amazonaws.com`, sin redirecciones, respuesta `image/*` y al menos 1200×1200. Las no verificadas se vacían sin mover las posiciones de la galería.

La plantilla conserva sus 37 columnas. `Cantidad` y `AG` reciben stock; las demás bodegas quedan en cero. El reporte de Publicaciones ML es opcional. Para AG se filtra primero por SKU y después exige producto, posición, marca, aplicación y año compatibles. Un título histórico anual, con rango o con un bloque de años cubre los años explícitos que contiene. Si falta o resulta ambigua cualquiera de esas partes, la variante queda pendiente: no se asume que está publicada.

La regresión está en `backend/scripts/test_publicaciones_ag.py`.
