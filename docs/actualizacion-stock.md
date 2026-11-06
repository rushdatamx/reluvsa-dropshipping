# Actualización de stock AutoAzur

Ruta administrativa `/actualizacion-stock`. Herramienta independiente de archivos: no usa
Mercado Libre, la BD operativa, relaciones de kits, tablas nuevas ni migraciones.

## Contrato de entrada

`POST /api/actualizacion-stock/analizar` devuelve 202 con el identificador aleatorio del trabajo.
Multipart: `cuenta` (`reluvsa` o `nereus`), `exporte` XLSX, `proveedores` como arreglo JSON
(por ejemplo, `["KG"]`), y tantos campos `stocks` como proveedores,
en el mismo orden. No se permiten proveedores repetidos ni ajenos a la cuenta.

Hasta 100,000,000 bytes por archivo y 250,000,000 por recepción multipart. El middleware
cuenta bytes de la petición y de cada parte mientras llegan; la copia a disco vuelve a
verificar los tamaños. La UI revisa los tamaños antes de subir.

El primer worksheet del exporte debe conservar E=Status, H=Cantidad, Q=Att_SellerSKU y
S=Marca. Se verifican todas las bodegas contra los encabezados de los exportes reales:

- Nereus: MATRIZ_75741158, KG_79675692 (AF), Difirsa_79236833 (AG).
- RELUVSA: MATRIZ_42210569, KIM_73524263, CAUPLAS_73524265, AG_73522745,
  KG_79605734 y VAZLO_74477853. Se ubican por encabezado, no por posición.

Un cambio de depósitos en AutoAzur exige revisar este contrato antes de aceptarlo.
Los originales nunca se sobrescriben.

## Lectura del stock y cruce

- KG: SKU/STOCK, primera hoja. DIFIRSA: Clave/Exis, CSV o Excel.
- VAZLO: primera columna SKU, segunda columna total (aunque el encabezado esté vacío).
- CAUPLAS: hoja `stock`; ignora las auxiliares.
- KIMS: hojas `STOCK` y `KITS`, Att_SellerSKU y STOCK. No infiere componentes de las
  columnas auxiliares. Los errores de Excel del stock quedan pendientes.
- AG: Artículo/Disponibilidad, primera hoja.
- También acepta dos columnas SKU/stock con encabezados libres y CSV UTF-8 o Windows-1252,
  delimitados por coma, punto y coma o tabulador. La hoja principal se usa si no hay hoja
  reconocida del proveedor. Stock entero no negativo obligatorio; vacío nunca significa cero.

Normalización: trim y mayúsculas, números enteros de Excel sin `.0`. Conserva ceros iniciales
textuales, guiones internos y sufijos. Primero código exacto; después únicamente equivalencias
CAU/CAUPLAS-, VAZLO-, AG espacio o KG- según proveedor. Más de un candidato queda ambiguo.
Duplicados iguales se consolidan; cualquier stock contradictorio o inválido bloquea ese SKU.

Para `&`, primero stock del código completo; sólo si no existe se resuelven los componentes.
Se cuentan repeticiones del código resuelto (KG-A&KG-A y KG-A&A necesitan dos A) y se toma el
mínimo de stock dividido entero entre repeticiones. Cada fila/variante recibe esa disponibilidad,
sin repartir entre publicaciones. Los kits con código propio requieren stock directo válido.

En Nereus sólo KG+KeepOnGreen o DIFIRSA+Engine Fil (trim y mayúsculas). RELUVSA cruza Q sin
condición de marca. Q y S nunca se reescriben.

Sólo las bodegas cargadas con cruce válido se actualizan. H suma todas las bodegas, contando
vacíos como cero. Cualquier bodega original inválida bloquea la fila completa. Una ambigüedad
de cualquier proveedor cargado también la bloquea completa. Un proveedor sin coincidencia o
con componente faltante conserva su propia bodega; otro proveedor válido puede actualizarse.
Se cambia E sólo si actualmente es ACTIVO o PAUSADO: total 0–4 PAUSADO, total >=5 ACTIVO.
Los demás estados permanecen intactos, aun si cambian stock y total.

## Trabajos y resultados

Todos los endpoints usan `require_admin`, incluidas las descargas.

- `GET /api/actualizacion-stock/{id}`: estado `pendiente`, `en_proceso`, `completo` o `error`,
  fase `analisis`/`generacion`, error legible, resumen y archivos generados.
- `GET /api/actualizacion-stock/{id}/resultados`: `pagina` desde 1, `limite` 1–100 (25 default),
  `tipo` todos/pendiente/modificada/sin_cambios y `proveedor` opcional. Devuelve total y página.
  Muestra fila de origen, SKU, proveedor, motivo y stock/estado/total anterior y propuesto.
- `POST /api/actualizacion-stock/{id}/generar`: 202; se ejecuta en segundo plano. Si está
  ocupado devuelve 409. Un resultado sin cambios devuelve el resumen sin Excel vacío.
- `GET /api/actualizacion-stock/{id}/archivos/{nombre}`: sólo nombres publicados por el
  trabajo, con autenticación, sin rutas arbitrarias. Reporte disponible tras el análisis.

El resumen cuenta filas del exporte y filas modificadas únicas. Las métricas por proveedor
atribuyen una fila modificada a cada proveedor que tuvo cruce válido en ella; no se deben
sumar para reconstruir las filas únicas. Pendientes cuenta incidencias de stock de origen y
pendientes por fila/proveedor: una fila puede aportar varios. Coincidencias cuenta cruces
válidos aun si otra bodega o ambigüedad bloquea después la modificación.

Dos hilos procesan trabajos; hasta cuatro análisis pendientes/en proceso se admiten a la vez.
La pantalla consulta estado periódicamente: no mantiene una petición durante el procesamiento.
Los XLSX se leen `read_only` y se escriben `write_only`. Resultados y filas modificadas viven
en JSONL en disco; sólo los índices de stock y el resumen permanecen en memoria. La paginación
recorre el JSONL sin cargarlo completo. Fechas, fórmulas y errores fuera de columnas editables
se conservan como tipos Excel. Celdas vacías pueden serializarse como blancos equivalentes;
no se preserva formato visual del exporte, sólo encabezados, valores y orden.

Sesiones en memoria y directorios UUID bajo el temporal del sistema `reluvsa-stock-jobs/`,
con acceso al directorio restringido. Caducan a las 24 horas desde la carga (HTTP 410).
Un reinicio pierde las sesiones y exige volver a cargar. Un limpiador cada cinco minutos
elimina trabajos terminados caducados y temporales huérfanos viejos, incluso de reinicios.
La configuración actual usa un proceso Uvicorn: con varios procesos/servidores habría que
compartir el registro de trabajos o usar afinidad de sesión.

## Archivos de salida y verificación

Sólo filas modificadas, en orden original, con identificadores y columnas completos.
Cada XLSX se mide realmente después de guardarse. Si supera 9,500,000 bytes se divide por
filas y se vuelve a guardar/medir recursivamente, sin repetir ni cortar filas. Cada parte se
reabre para comprobar encabezados y cantidad de filas. Una única fila que supere el límite
produce un error explícito. ZIP de transporte con todos los Excel; el ZIP no se importa.
`reporte.csv` separado contiene resumen por proveedor en columnas e incidencias pendientes
con valores anteriores/propuestos; los prefijos de fórmula en texto se escapan para CSV.

## Validación

`PYTHONPATH=backend python3 backend/scripts/test_actualizacion_stock.py`: 14 pruebas con fixtures sintéticas,
reglas de ambas cuentas, marcas, umbrales, paquetes y repeticiones con prefijos, kits directos,
colisiones, inválidos/duplicados, bodegas, tipos, división/tamaños, identificadores/orden,
autorización, límites de recepción, caducidad y ciclo asíncrono/errores/descargas.
Frontend: `cd frontend && npm run build`.

Los archivos reales bajo `actualizacion-stock/` están excluidos de Git y no forman parte de
los tests del repositorio. Resultados de la prueba local de volumen en la bitácora.

Antes del uso diario queda pendiente una importación pequeña con Gaby en AutoAzur que incluya
una variante, un paquete, una activación y una pausa. Sólo esa comprobación externa confirma
la compatibilidad final de importación.
