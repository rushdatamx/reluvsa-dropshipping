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

Cada rango produce un título `Producto P/ Armadora Modelo Versión Motor Años`.
Si no cabe en 60 caracteres se omite armadora; después, versión. Producto,
modelo, motor presente y años nunca se eliminan ni se cortan. Si aun así no cabe,
la variante se excluye y queda en el reporte de errores.

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
- Títulos, descripción, precio y XLSX de 36 columnas:
  `backend/services/generador_plantilla.py`.
- Contrato HTTP, métricas y filtros: `backend/routers/publicaciones.py`.
- Selector, tipo de cambio y marca por catálogo: `frontend/src/pages/Publicaciones.jsx`.
- Regresión reproducible: `backend/scripts/test_publicaciones_kims.py`.

Al cerrar cambios, actualizar también la regla breve en `CLAUDE.md` y el relato en
`docs/bitacora-sesiones.md`; este documento debe permanecer como referencia técnica,
no como bitácora cronológica.
