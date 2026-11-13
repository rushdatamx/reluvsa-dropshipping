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

La pantalla incluye un apartado **Suspensión** que recibe los mismos archivos y
procesa sólo títulos con “horquilla”, “amortiguador(es)” o “resorte(s)”. En ese
apartado se proponen vehículos por modelo y cada año del rango detectado, de
todas las marcas del catálogo incluso si el título menciona una. No se filtra por
fabricante, litros ni cilindros. Todas las coincidencias de todos los modelos
reales detectados quedan **Listas para descargar**, sin aprobaciones. La conjunción
Y se ignora entre componentes o modelos y antes de Para/P/; un modelo vehicular
Y identificado solo o con fabricante se conserva. Los nombres compuestos tienen
prioridad sobre el modelo que contienen.

Los años completos conservan el rango mínimo–máximo. Las abreviaturas se contrastan
con el catálogo entre 1900 y 2099; sólo se acepta una interpretación válida. Un
rango mixto usa el siglo del año completo como ancla y no se invierten rangos.
Sin modelo, año inequívoco o coincidencias queda **Sin coincidencia**, con motivo.
Nunca hay revisión en Suspensión (`revision = 0`). La exclusión manual prevalece.

Las listas muestran el desglose por marca y todos los modelos detectados. Consultar
las compatibilidades completas es opcional: todas se exportan aunque sólo se vea
una muestra de cinco. El Excel conserva 17 columnas, posición y notas y elimina
vehículos idénticos por publicación. Sin filas exportables se informa «No hay
compatibilidades para descargar».

Las sesiones de Suspensión llevan `reglas_suspension = 2`; las anteriores se
rechazan en consulta y exportación y piden volver a analizar. Las sesiones generales
conservan su funcionamiento. No cambian endpoints ni campos existentes.

## Regla de seguridad del cruce general

Una publicación queda lista automáticamente cuando un único modelo, años de cuatro
dígitos y una cilindrada explícita producen coincidencias exactas. Los cilindros filtran adicionalmente
cuando aparecen en el título. Si falta fabricante, se aplican primero esos
filtros y sólo se infiere la marca cuando queda una sola; si quedan varias,
las propuestas se muestran para decisión individual.

Se manda a revisión cuando:

- el fabricante o la combinación vehicular sigue siendo ambigua;
- hay varios modelos independientes, marcas contradictorias o motores distintos;
- la cilindrada se propone a partir de un entero sin unidad;
- aparecen rangos de años abreviados;
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

## Lectura flexible de títulos (2026-11-06)

La búsqueda recorre el título completo, con o sin `P/` o `Para`, sin depender del
orden de modelo, fabricante, motor y años. Los nombres se comparan como secuencias
completas, normalizando mayúsculas, acentos y espacios y conservando guiones.
Un nombre compuesto desplaza sólo las apariciones que contiene; varios nombres
independientes requieren revisión. Una marca explícita restringe las propuestas a
sus modelos. Se mantienen los alias `VW` y `MERCEDES BENZ`.

Los modelos numéricos necesitan marca explícita y una aparición separada de motor
y años. `2.0`, `2,0`, `2.0L` y `2 L` son equivalentes. Varias cilindradas distintas
se conservan como alternativas en revisión. `L4` y `V6` filtran cilindros;
`Tsuru I 1.6` no genera un cilindro `I1`. Los números inmediatamente posteriores
al modelo y los sufijos `I`, `II`, `III` se consideran generación. Otros enteros
entre 1 y 12 sólo se proponen como litros si existen en el catálogo para el modelo
y años detectados, siempre sujetos a revisión. Sin coincidencia exacta se ofrecen
otros motores del mismo modelo y años, con advertencia explícita.

`extraido` mantiene sus campos y añade `modelos`, `fabricantes_detectados`,
`litros_detectados`, `origen_litros` (`explicita`, `entero_inferido` o vacío) y
`ambiguedades`. No cambian endpoints, aprobación por publicación, 17 columnas,
posición, notas, exclusiones ni sesiones. Los resultados guardados anteriormente
no se recalculan: hay que volver a analizar los archivos.

### Verificación con los archivos reales

Se reprodujo la base previa: 836 listas, 478 revisiones, 458 sin coincidencia,
26,268 compatibilidades automáticas, sobre 1,772 publicaciones y 349,106 vehículos.
La lectura flexible produjo 864 listas, 547 revisiones, 361 sin coincidencia y
26,205 compatibilidades automáticas. Se compararon estados y filas de candidatos
de cada publicación, no sólo los totales.

Se inspeccionaron las 46 entradas nuevas a listas (índices de análisis desde cero):
24, 73, 135, 183, 222, 241, 244, 318, 355, 358, 405, 469, 510, 575, 597,
675, 693, 710, 717, 718, 813, 818, 840, 853, 975, 980, 996, 998, 1009, 1065,
1104, 1116, 1232, 1279, 1315, 1338, 1352, 1418, 1427, 1527, 1547, 1657,
1670, 1680, 1686 y 1741. Se comprobó fabricante/modelo, motor y rango de cada
conjunto propuesto. Recuperan modelos numéricos Mazda separados del motor,
VAM/AMC con el modelo más adelante, títulos `Para` o sin marcador, Malibu con
marca mal escrita inferida únicamente por coincidencias exactas y Tsuru I sin
cilindros falsos. El índice 675 conserva sólo 2017–2020: el token `206` no se
corrige ni se interpreta como año. Econoline (575) conserva el comportamiento de
submodelos: no filtra carrocería ni versión E-450. La aprobación se mantiene por
publicación y las variantes del catálogo se exportan completas.

Dieciocho listas previas pasan a revisión por múltiples modelos o motores (por
ejemplo Spark junto a otros nombres del catálogo y Onix 1.0/1.2). El descenso de
compatibilidades automáticas es deliberado. Las propuestas revisables nuevas
incluyen títulos sin marcador y múltiples modelos, sin aprobación automática.

Regresión sin red: `PYTHONPATH=backend python3 backend/scripts/test_publicaciones_autozur.py`
(58 verificaciones contadas realmente). Frontend: `npm run build` satisfactorio.

## Verificación de Suspensión sin aprobaciones (2026-11-13)

Regresión: 124 comprobaciones sin red y build de frontend satisfactorio. Sobre los
archivos reales: 276 publicaciones de suspensión, 263 listas, 0 revisiones,
13 sin coincidencia y 11,354 filas exportadas con `aprobadas_revision=[]`, sin abrir
detalles. Swift y Corolla Cross conservan todas sus variantes. Se comparó el
resultado íntegro del modo general contra el commit anterior: idéntico para las
1,772 publicaciones (864 listas, 547 revisiones, 361 sin coincidencia y 26,205
compatibilidades automáticas). Casos sintéticos cubren Attitude 2011, G3 2006–2007,
Y real/conjunción, varios modelos, años ambiguos e invertidos, duplicados,
exclusiones, muestras y sesiones antiguas. Los títulos completos de Gaby con Y
no están en los archivos disponibles; se reprodujeron las combinaciones indicadas
en el plan y se verificaron los títulos G3 presentes en el Excel real.
