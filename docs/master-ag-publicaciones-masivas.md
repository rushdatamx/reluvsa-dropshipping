# Master AG — Publicaciones masivas

ARGENPARTS se identifica en el portal como `AG` y se muestra como **ARGENPARTS**. Es un flujo local Excel → Excel: no llama a Mercado Libre.

## Archivos obligatorios

1. `Master AG.xlsx`, detectado por los encabezados `Producto`, `Código`, `SYD`, `GROB`, `KYB`, `BOGE`, `VAZLO`, `Marca`, `Aplicación`, `Desde`, `Hasta`, `Posición`, `Distribuidor` y `stock`.
2. `FOTOS AG.xlsx`, leído hasta en sus diez hojas. Cada fila aporta Servidor, Carpeta, Nombre y Extensión para formar la URL.

`Código` es el SKU; `Distribuidor` es costo sin IVA y `stock` es un entero obligatorio mayor o igual a cero. Costo o stock distintos, vacíos o inválidos en filas del mismo SKU excluyen el SKU completo. Una fila válida es una compatibilidad; años ausentes, inválidos o invertidos se excluyen y se reportan.

Las equivalencias SYD/GROB/KYB/BOGE/VAZLO y todas las compatibilidades del SKU se consolidan en la descripción. Cada rango válido `Desde–Hasta` se expande a una publicación por año: `2012–2015` genera 2012, 2013, 2014 y 2015. El título es `Producto Posición para Marca Aplicación Año`; siempre contiene un solo año, nunca el rango. Usa sólo abreviaturas controladas. Cuando es necesario se quita el paréntesis de Aplicación sólo del título. Si aún supera 60 caracteres se excluye: nunca se corta una palabra. La descripción conserva el texto completo.

La deduplicación de AG es por SKU + producto + posición + marca + aplicación + año. Así dos filas equivalentes del master no producen dos publicaciones anuales.

## Fotos

El nombre completo del SKU es Imagen1; después se aceptan `SKU_2`…`SKU_10` y `SKU-2`…`SKU-10`. El nombre completo tiene prioridad para no interpretar un SKU legítimo terminado en número como sufijo. Se ignoran combos, kits, duplicados, posiciones mayores a 10 y SKU que no existen en el master; análisis devuelve detectadas, cruzadas, omitidas, sin match y SKU sin fotos.

Al generar, las URLs pasan el candado común: únicamente HTTPS en `apimagenes.s3.amazonaws.com`, sin redirecciones, respuesta `image/*` y al menos 1200×1200. Las no verificadas se vacían sin mover las posiciones de la galería.

La plantilla conserva sus 37 columnas. `Cantidad` y `AG` reciben stock; las demás bodegas quedan en cero. El reporte de Publicaciones ML es opcional. Para AG se filtra primero por SKU y después exige producto, posición, marca, aplicación y año compatibles. Un título histórico con rango cubre cada año que contiene; un título anual sólo cubre ese año. Si falta o resulta ambigua cualquiera de esas partes, la variante queda pendiente: no se asume que está publicada.

La regresión está en `backend/scripts/test_publicaciones_ag.py`.
