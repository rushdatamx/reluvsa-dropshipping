# Publicaciones Autozur

## Objetivo

Completar la plantilla `publicaciones-compatibilidades.xlsx` con el catálogo
`catalogo-vehiculos-mexico.xlsx`. Es un flujo local **Excel → Excel** y no usa la
API de Mercado Libre.

Autozur espera una fila por compatibilidad. Por eso `UserProductID`, `TITULO` y
`SKU` se repiten tantas veces como vehículos compatibles tenga la publicación.

## Flujo

1. Un administrador abre **Compatibilidades Autozur**.
2. Carga la plantilla de publicaciones y el catálogo vehicular.
3. El portal extrae del título fabricante, modelo, años, litros y cilindros.
4. Cruza esos campos contra el catálogo de 12 atributos vehiculares.
5. Separa las publicaciones en listas, a revisión y sin coincidencia.
6. Gaby aprueba o excluye los casos revisables.
7. Descarga un Excel con las mismas 17 columnas y una fila por compatibilidad.

## Regla de seguridad del cruce

Una publicación queda lista automáticamente cuando modelo, rango de años y
litros producen coincidencias exactas. Los cilindros filtran adicionalmente
cuando aparecen en el título. Si falta fabricante, se aplican primero esos
filtros y sólo se infiere la marca cuando queda una sola; si quedan varias,
las propuestas se muestran para decisión individual.

Se manda a revisión cuando:

- el fabricante o la combinación vehicular sigue siendo ambigua;
- falta la cilindrada;
- el motor exacto no existe, pero hay vehículos del mismo modelo y años.

Los descriptores posteriores al modelo (incluidos sufijos de generación como
“II”) no fuerzan revisión cuando años y motor encuentran coincidencias exactas.
Las variantes de submodelo compatibles se conservan como propuestas. La acción
masiva sólo aplica a resultados exactos clasificados como seguros; nunca incluye
propuestas aproximadas ni registros ambiguos. El Excel mantiene las 17 columnas
y genera una fila por vehículo compatible.

Una revisión no entra al archivo de salida hasta que Gaby la aprueba. Los casos
sin propuesta se excluyen. El motor no inventa fabricantes, años, cilindrada ni
atributos ausentes.

## Arquitectura

- `backend/services/autozur_compatibilidades.py`: lectura, parser, índice,
  cruce, sesión temporal y escritura del Excel.
- `backend/routers/publicaciones_autozur.py`: endpoints administrativos.
- `frontend/src/pages/PublicacionesAutozur.jsx`: carga, métricas, revisión y
  descarga.
- `backend/scripts/test_publicaciones_autozur.py`: regresión sin red.

Endpoints:

```text
POST /api/publicaciones-autozur/analizar
POST /api/publicaciones-autozur/generar
GET  /api/publicaciones-autozur/sesiones/{session_id}/resultados/{resultado_id}
```

Las sesiones viven temporalmente en el servidor y no escriben en la base de
datos. Si la sesión ya no está disponible, la pantalla pide volver a analizar.

## Línea base de los archivos recibidos (2026-09-09)

- Catálogo: 349,106 filas utilizables.
- Publicaciones: 1,772.
- Listas automáticas: 615.
- A revisión: 698.
- Sin coincidencia: 459.
- Compatibilidades automáticas generadas: 20,778.

Estos números son una línea base para detectar cambios accidentales en el
parser. Pueden cambiar cuando Autozur entregue archivos nuevos.
