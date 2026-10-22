# Master AG — Publicaciones masivas

ARGENPARTS se identifica en el portal como `AG` y se muestra como **ARGENPARTS**. Es un flujo local Excel → Excel: no llama a Mercado Libre.

## Archivos obligatorios

1. `Combos por aplicaciones AG.xlsx`, detectado por encabezados aunque cambie el nombre de hoja: `Codigo`, `Producto`, `Aplicación`, `Año desde`, `Año hasta`, `Stock` y `Precio`. `Codigo` es el SKU, `Producto` el nombre y `Precio` el costo proveedor. Las columnas `Componente` o `Componente N` se consolidan por SKU.
2. `FOTOS AG.xlsx`, leído hasta en sus diez hojas. Cada fila aporta Servidor, Carpeta, Nombre y Extensión para formar la URL.

El formato anterior `Master AG.xlsx` sigue aceptado como legado. Costo o stock distintos, vacíos o inválidos en filas del mismo SKU excluyen el SKU completo. Stock debe ser un entero no negativo. Una fila válida es una compatibilidad; años ausentes, inválidos o invertidos se excluyen y se reportan. En Combos se consolidan aplicaciones repetidas y componentes sin duplicados.

En el formato legado, las equivalencias SYD/GROB/KYB/BOGE/VAZLO y todas las compatibilidades se consolidan en la descripción. Su título conserva el formato `Producto Posición para Marca Aplicación Años`. En Combos, la descripción agrega todos los componentes y compatibilidades, y el título es `Producto para Aplicación Años`, sin posición. En ambos formatos se quitan notas parentéticas sólo del título si hace falta; si aún supera 60 caracteres, los bloques de años se subdividen y los años que individualmente no quepan se reportan. Nunca se corta una palabra.

Una compatibilidad de varios años genera un título con el rango completo y dos títulos con bloques cronológicos balanceados. Los bloques se subdividen recursivamente si exceden 60 caracteres; si un año individual tampoco cabe, queda reportado como excedido. La deduplicación conserva SKU, producto, posición, marca y aplicación, y elimina títulos repetidos de filas equivalentes del master.

## Fotos

En el formato legado, el nombre completo del SKU es Imagen1 y también se aceptan `SKU_2`…`SKU_10` y `SKU-2`…`SKU-10`. En Combos sólo se acepta coincidencia exacta entre el código completo y el nombre de foto; no se usan imágenes de componentes ni sufijos. Duplicados, posiciones mayores a 10 y SKU que no existen en el master se omiten; análisis devuelve detectadas, cruzadas, omitidas, sin match y SKU sin fotos.

Al generar, las URLs pasan el candado común: únicamente HTTPS en `apimagenes.s3.amazonaws.com`, sin redirecciones, respuesta `image/*` y al menos 1200×1200. Las no verificadas se vacían sin mover las posiciones de la galería.

La plantilla conserva sus 37 columnas. `Cantidad` y `AG` reciben stock; las demás bodegas quedan en cero. El reporte de Publicaciones ML es opcional. Para AG se filtra primero por SKU y después exige producto y aplicación compatibles; el legado también exige posición y marca. Un título histórico anual, con rango o con un bloque de años cubre los años explícitos que contiene. Si falta o resulta ambigua cualquiera de esas partes, la variante queda pendiente.

La regresión está en `backend/scripts/test_publicaciones_ag.py`.
