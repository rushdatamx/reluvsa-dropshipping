# Master KIMS — Publicaciones masivas

> Contrato canónico del formato `master_kims`. Leer antes de cambiar su lector,
> títulos, descripción, precio, filtros, marca o fotografías.

KIMS usa el código interno `KIM` y se muestra como “KIMS”. Este flujo es sólo
Excel → Excel: no usa base de datos ni llama a la API de Mercado Libre.

## Detección y agrupación

El lector se ejecuta antes de CAUPLAS y KG y detecta encabezados normalizados,
sin depender del nombre de la hoja. Agrupa por `original`; los SKU numéricos se
convierten a texto sin `.0`.

Se excluye el SKU completo si stock (`cantidad`) o precio USD (`PRECIO`) es cero,
negativo, inválido o inconsistente; si la marca está vacía o es conflictiva; si
`descripcion` está vacía o es contradictoria; o si falta un campo estructural.
Los contadores de causas son independientes y pueden solaparse.

Una fila con años inválidos o invertidos excluye sólo esa compatibilidad. Las
compatibilidades se deduplican por SKU, armadora, modelo, versión, motor y rango.
Motor vacío es válido.

## Consolidación y publicaciones

Por SKU se consolidan OEM (`alterna_1`–`alterna_4`, columnas B:E; `original` es el SKU), combustible,
comentarios, posiciones, sistemas, fotos y todas las compatibilidades. Cilindros,
tipo, múltiplo, dimensiones y UPC no se exportan en esta versión.

Cada compatibilidad de varios años produce un título por año y una variante
adicional con el rango completo: `2018`, `2019`, `2020` y `2018/2020`. Una
compatibilidad de un solo año produce una única publicación. No se generan bloques
intermedios de años.

### Contrato de expansión y deduplicación

`generar_filas_con_reporte` delega la expansión en `_variantes_kims` de
`backend/services/generador_plantilla.py`. La función devuelve pares
`(etiqueta, compatibilidad)` en orden cronológico: un diccionario copiado por cada
año, con `inicio == fin == año`, seguido del diccionario original copiado para el
rango. La etiqueta de este último es `Inicio/Fin`. Las validaciones de años viven
en el lector (`parser_catalogo.py`); el generador recibe sólo compatibilidades
válidas y no debe corregir ni invertir rangos.

El generador construye cada título por separado llamando a `_titulo_kims`. Para
cada una prueba, en este orden: título con armadora; sin armadora; y sin armadora
ni versión. Producto, modelo, motor cuando existe y año son estructurales. No se
abrevia ni recorta el texto. Si los tres intentos exceden `MAX_TITULO` (60), se
crea una exclusión para esa variante y continúa con las demás; la fila pendiente
conserva el candidato largo y sus datos comerciales para la hoja de corrección.

La clave de deduplicación es `(SKU en mayúsculas, título normalizado)`. El título
se normaliza quitando acentos, convirtiendo a minúsculas y colapsando espacios.
Esto permite que compatibilidades distintas que terminan en el mismo título no
creen publicaciones duplicadas, sin colapsar años o rangos cuyo título sí difiere.
Las variantes conservadas repiten precio calculado, descripción completa, fotos,
stock y marca del SKU; sólo cambian título y etiqueta de aplicación. La descripción
no se reduce a la compatibilidad que originó cada publicación.

La regresión `backend/scripts/test_publicaciones_kims.py` fija el ejemplo de
2018–2020 (tres títulos anuales más el rango), el caso de un año (una publicación),
la exclusión de años invertidos en el lector, el límite de 60 caracteres y la
plantilla XLSX resultante. La verificación del libro debe comprobar encabezados y
37 columnas, además de la propagación de precio, stock, marca e imágenes.

Cada variante produce el título `Producto para Armadora Modelo Versión Motor Años`.
Si no cabe en 60 caracteres se omite armadora; después, versión. Producto,
modelo, motor presente y años nunca se eliminan ni se cortan. Si aun así no cabe,
esa variante se excluye y queda en el reporte de errores, sin impedir las otras
variantes de la compatibilidad. Las variantes se deduplican por SKU y título
normalizado.

La descripción incluye producto, OEM, combustible, comentarios, posiciones,
compatibilidades completas y la descripción base de RELUVSA. La marca de cada
fila sale de la columna `marca`; el ajuste global no aplica a KIMS.

`PRECIO` está en USD. Primero se multiplica por `tipo_cambio_usd` (18.50 por
default, finito y mayor que cero) y luego se aplica la fórmula compartida de IVA,
utilidad, envío y comisión. Stock se escribe en `Cantidad` y `KIM`; las otras
bodegas quedan en cero.

## Imágenes, API interna y filtros

`FOTO 1`–`FOTO 4` conservan su posición. Sólo se permite HTTPS con host exacto
`www.kimsauto.com.mx`; la validación común exige imagen disponible y mínimo
1200×1200. Una imagen inválida se vacía sin bloquear ni desplazar las demás.

`GET /api/publicaciones/proveedores` expone `KIM` y nombre visible `KIMS`.
`POST /analizar` devuelve `formato: master_kims`, métricas, `por_linea` y
`por_sistema`, incluyendo los productos asociados. En `POST /generar`, `lineas`
(productos) y `sistemas` son listas JSON opcionales y se combinan por intersección.

## Línea base real (2026-09-23)

- 19,289 filas; 7,945 SKU observados.
- 3,027 SKU con precio/stock inválido; 95 con marca vacía/conflictiva; 2 con
  producto contradictorio (contadores con posibles solapes).
- 4,852 SKU utilizables; 7 compatibilidades utilizables con años inválidos.
- 10,799 variantes finales únicas bajo la política de títulos.

Pruebas obligatorias: `test_publicaciones_kims.py`, las suites KG/CAUPLAS e
imágenes, build del frontend y checklist completo de `api-seguridad`.

## Mapa de implementación

- Lector y detección: `backend/services/parser_catalogo.py`.
- Perfil, código `KIM` y host autorizado: `backend/services/perfiles_catalogo.py`.
- Títulos, descripción, precio y XLSX de 37 columnas:
  `backend/services/generador_plantilla.py`.
- Contrato HTTP, métricas y filtros: `backend/routers/publicaciones.py`.
- Selector, tipo de cambio y marca por catálogo: `frontend/src/pages/Publicaciones.jsx`.
- Regresión reproducible: `backend/scripts/test_publicaciones_kims.py`.

Al cerrar cambios, actualizar también la regla breve en `CLAUDE.md` y el relato en
`docs/bitacora-sesiones.md`; este documento debe permanecer como referencia técnica,
no como bitácora cronológica.
