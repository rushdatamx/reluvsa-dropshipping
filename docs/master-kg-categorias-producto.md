# Master KG: detección de formato y categorías de producto

> Referencia técnica canónica para cualquier cambio relacionado con la lectura
> del master KG, el campo `Producto`, el desglose `por_linea` o el filtro de
> categorías en Publicaciones masivas.

## 1. Regla operativa que no se debe romper

El formato del nuevo master KG se identifica por la estructura de encabezados,
no por el nombre de la hoja.

- `BD_Catalogo` conserva preferencia cuando existe y tiene la estructura completa.
- Si otra hoja contiene todos los encabezados del master, esa hoja se procesa como
  master aunque su nombre cambie.
- Mayúsculas, minúsculas, espacios repetidos y acentos no cambian la detección.
- Si una hoja parece master pero está incompleta, se rechaza con un mensaje que
  enumera los encabezados faltantes. Nunca debe caer silenciosamente al legado.
- En el master, la categoría operativa es exclusivamente la columna `Producto`.
  `Modelo` describe el vehículo y nunca puede usarse como categoría.
- Los nombres internos `linea`, `por_linea` y el parámetro HTTP `lineas` se
  conservan por compatibilidad. Para el master contienen valores de `Producto`.
- Esta regla no autoriza cambios en categorías Mercado Libre `MLM`, costos de
  envío, precios ni generación de títulos.

## 2. Por qué existe

El parser originalmente decidía entre master y legado con esta condición:

```python
MASTER_HOJA in wb.sheetnames
```

Cuando el proveedor renombraba `BD_Catalogo`, el archivo entraba al parser
legado. El perfil legado de KG toma la columna B como `linea`; en el master la
columna B es `Modelo`. El resultado no producía una excepción: mostraba botones
como `JETTA` o `C2500`, aparentemente válidos, en lugar de `Radiador`, `Toma de
Agua` o `Bomba de Agua`. Era un fallo silencioso de clasificación.

## 3. Contrato del master

Los 17 encabezados requeridos, definidos en
`backend/services/parser_catalogo.py::MASTER_HEADERS`, son:

1. `Armadora`
2. `Modelo`
3. `Motor`
4. `Producto`
5. `Año`
6. `Inicio`
7. `Fin`
8. `Clave`
9. `Especificaciones`
10. `Características`
11. `Guía de Compradores`
12. `OEM`
13. `Imagen 1`
14. `Imagen 2`
15. `Imagen 3`
16. `Imagen 4`
17. `Imagen 5`

`Gran Mayoreo` y `Precio` son nombres equivalentes para la columna opcional de
costo. Si uno aparece, debe ser la columna 18, inmediatamente después de
`Imagen 5`. `Gran Mayoreo` representa el costo base sin IVA, no el precio final
de Mercado Libre. Si ninguno aparece, el análisis continúa y el precio generado
queda vacío. La plantilla generada conserva el encabezado `Precio`.

La normalización de encabezados usa `_normalizar`: elimina espacios sobrantes,
compara sin distinguir mayúsculas y elimina diacríticos. Por ejemplo, ` AÑO `,
`año` y `ANO` se consideran el mismo encabezado.

## 4. Algoritmo de selección de hoja

`_hoja_master(wb)` aplica este orden:

1. Usar `BD_Catalogo` si existe y tiene los 17 encabezados.
2. Buscar la primera hoja, en orden del workbook, que tenga la estructura completa.
3. Si existe `BD_Catalogo` pero está incompleta y no hay otra hoja completa,
   devolverla para que `_leer_master` produzca el error detallado.
4. Buscar una hoja que parezca master incompleto.
5. Sólo cuando ninguna hoja parece master, usar `_leer_legado`.

Una hoja «parece master» cuando contiene al menos cuatro de estas seis anclas:
`Armadora`, `Modelo`, `Producto`, `Clave`, `Inicio`, `Fin`. El umbral evita que un
master parcialmente exportado sea interpretado como el catálogo anterior.

Si se modifica este algoritmo, deben conservarse dos propiedades:

- una hoja completa siempre gana sobre una incompleta;
- un archivo ambiguo con huella fuerte de master falla de forma visible.

## 5. Flujo del dato `Producto`

```text
Excel: Producto
  → _leer_master: producto = _producto_canonico(...)
  → pieza["producto"]
  → pieza["linea"] (alias interno compatible)
  → generar_filas: FilaPublicacion.linea
  → POST /analizar: por_linea[].linea y por_linea[].producto
  → Publicaciones.jsx: botones de categoría
  → POST /generar: lineas=[...]
  → filtro sobre pieza["linea"]
```

`_producto_canonico` compacta espacios y normaliza capitalización, conservando
conectores como `de`, `del`, `con` y `c/bomba`. El agrupamiento principal sigue
siendo por `Clave`; `Producto` clasifica cada SKU y determina el filtro por tanda.

No renombrar todavía el contrato `por_linea`/`lineas`: puede haber frontends o
clientes HTTP usando esos nombres. Una futura migración debería agregar primero
campos nuevos y mantener aliases durante una ventana de compatibilidad.

## 6. Backend y frontend involucrados

- `backend/services/parser_catalogo.py`
  - `_normalizar`
  - `_headers_master`
  - `_parece_master`
  - `_hoja_master`
  - `_leer_master`
  - `leer_catalogo_detallado`
- `backend/routers/publicaciones.py`
  - `POST /api/publicaciones/analizar`
  - `POST /api/publicaciones/generar`
- `backend/services/generador_plantilla.py`
  - propaga `linea` a las filas generadas; no cambiar sus reglas de títulos como
    parte de una corrección de categorías.
- `frontend/src/pages/Publicaciones.jsx`
  - consume `analisis.por_linea` y envía `lineas` como JSON.
- `backend/scripts/test_publicaciones_masivas.py`
  - regresiones del master, legado, categorías, variantes y plantilla.

## 7. Diferencia con el catálogo legado

El catálogo legado `LISTA PRECIOS KG.xlsx` no tiene los encabezados del master en
la primera fila. Su perfil comienza en la fila 3 y usa posiciones fijas:

- columna C: clave;
- columna B: línea de producto;
- columna G: aplicaciones principales;
- columna I: costo de gran mayoreo.

En el legado, `linea` sí proviene de la columna B. En el master, `linea` debe ser
un alias de `Producto`, independientemente de la posición física de la columna.
No unificar ambos parsers mediante índices compartidos.

## 8. Casos de error deliberados

- Falta cualquier encabezado requerido: HTTP 400 con la hoja y lista de faltantes.
- `Gran Mayoreo` o `Precio` existe fuera de la posición 18: HTTP 400; no se
  adivina su ubicación.
- `Clave` vacía: fila excluida y reportada.
- `Inicio`/`Fin` no son años enteros 1900–2100: fila excluida.
- `Inicio > Fin`: fila excluida.
- Costos diferentes dentro del mismo SKU: costo vacío y error visible.
- Imágenes distintas dentro del mismo SKU: se reporta inconsistencia.

## 9. Línea base real de regresión

Archivo: `archivos/publicaciones-masivas/nuevo-master-kg.xlsx` (ignorado por Git
porque contiene datos del cliente).

- 29,216 filas de datos.
- 4,023 claves observadas antes de descartar filas inválidas.
- 4,021 SKU utilizables agrupados.
- 408 `Radiador`.
- 384 `Toma de Agua`.
- 351 `Bomba de Agua`.

Catálogo legado real:

- 3,676 piezas.
- Debe seguir devolviendo `formato == "legado"`.

Prueba de filtro medida: seleccionar `Radiador` sobre el master real deja 408 SKU
y generó 3,772 variantes, todas con `linea == "Radiador"`.

## 10. Verificación obligatoria antes de desplegar

Desde `backend/`:

```bash
python3 scripts/test_publicaciones_masivas.py
```

La suite vigente después de esta corrección tiene 68 comprobaciones. Debe cubrir:

- nombre canónico;
- hoja renombrada;
- encabezados con mayúsculas, espacios y acentos distintos;
- categoría tomada de `Producto`, nunca `Modelo`;
- master incompleto rechazado;
- catálogo legado;
- filtro y generación por categoría.

Desde `frontend/`:

```bash
npm run build
```

Con los archivos reales, volver a comprobar la línea base del apartado 9. Tras
el push a `main`, confirmar Railway `SUCCESS`, salud del backend y que el bundle
activo de Vercel contiene `Filtra por categoría de producto`. Si hay una sesión
autenticada disponible, subir el master en producción y revisar visualmente los
botones. Si no existe sesión, dejar esa limitación explícita; no declarar una
prueba visual que no se hizo.

## 11. Alcance excluido

Esta regla resuelve detección, agrupación y filtrado por `Producto`. Quedan fuera:

- el identificador de categoría Mercado Libre (`MLM...`);
- el costo de envío por categoría;
- las fórmulas de precio;
- la construcción y acortamiento de títulos;
- cambios a publicaciones mediante la API de Mercado Libre.

El Módulo 2 continúa siendo un transformador Excel → Excel y no realiza llamadas
de red a Mercado Libre.

## 12. Entrega que originó esta regla

- Fecha: 2026-08-27.
- Commit: `dec7ecb` (`fix: detectar categorias del master KG por encabezados`).
- Rama: `main`.
- Railway: deployment `dd62f253-89d2-46cc-b52f-14736fb56a91`, `SUCCESS`.
- Vercel: bundle productivo comprobado con la nueva etiqueta.
- Limitación: no hubo navegador/sesión autenticada disponible para repetir la
  carga visual del archivo dentro del portal.
