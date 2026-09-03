# Validación de imágenes de catálogo

> Leer completo antes de tocar `backend/services/validacion_imagenes.py`,
> `backend/services/perfiles_catalogo.py`, la generación de publicaciones o las
> columnas Imagen 1–10. Regla vigente desde 2026-09-03.

## Contrato funcional

Una URL del Excel o CSV auxiliar sólo se escribe en su posición Imagen si cumple
**todas** estas reglas:

1. El host está declarado de forma exacta en `PerfilCatalogo.dominios_imagenes`.
2. Usa HTTPS, sin credenciales, sin puerto alterno y sin redirecciones.
3. El servidor responde 2xx con `Content-Type: image/*`.
4. Se puede confirmar su resolución como JPEG, PNG, WebP o GIF.
5. Ancho y alto son **>= 1200 px**.

Ante error, timeout, formato desconocido o metadatos incompletos, se deja la
celda vacía. No se mueve una imagen válida de Imagen 2 a Imagen 1, ni de ninguna
otra posición: conservar el orden del master es parte del contrato.

## Perfiles y proveedores futuros

Los dominios no se descubren desde el Excel. Al crear un perfil de catálogo hay
que decidir y declarar explícitamente `dominios_imagenes`, con hosts exactos y
en minúsculas. Ejemplos vigentes: KG usa `("kgmedia.mx",)` y CAUPLAS
`("ik.imagekit.io",)`. Una URL de un proveedor sin dominios configurados se
elimina y cuenta como `dominio_no_autorizado`.

No autorizar subdominios mediante sufijos o comodines. Si un proveedor migra de
CDN, añadir el host nuevo tras confirmarlo con el proveedor y conservar el viejo
sólo mientras siga siendo necesario.

## Implementación y seguridad

`filtrar_imagenes` deduplica todas las URLs de las filas antes de abrir
conexiones. Conserva los límites: 16 trabajadores, máximo 25,000 URLs, timeout
por conexión de 5 s y plazo global de 240 s. Las que no terminan en ese plazo se
clasifican como no disponibles y se eliminan.

La consulta usa `GET` con `Range: bytes=0-65535` y sólo lee ese máximo. No usar
`HEAD`: no entrega los metadatos de resolución. No descargar la imagen completa.
El handler de urllib no sigue redirecciones; una redirección se clasifica como no
disponible. El allowlist se valida **antes** de abrir conexión para evitar SSRF.

`dimensiones_imagen` interpreta cabeceras JPEG (segmentos SOF), PNG (IHDR), WebP
(VP8X, VP8 y VP8L) y GIF. No incorporar un decodificador de imágenes ni aceptar
formatos cuya dimensión no se pueda demostrar sin revisar antes límites de
memoria y seguridad.

## Contadores y API

`POST /api/publicaciones/generar` continúa devolviendo el `.xlsx` y añade estos
headers, contados por URL única:

| Header | Motivo |
|---|---|
| `X-Imagenes-Revisadas` | URLs únicas observadas |
| `X-Imagenes-Validas` | pasan todas las reglas |
| `X-Imagenes-No-Disponibles` | error HTTP/red, contenido no imagen o timeout |
| `X-Imagenes-Resolucion-Insuficiente` | alguno de sus lados es menor a 1200 |
| `X-Imagenes-Dominio-No-Autorizado` | URL fuera de la política del perfil |
| `X-Imagenes-Formato-No-Verificable` | no se pudo extraer dimensión |

`X-Imagenes-Eliminadas = Revisadas - Validas`. La interfaz presenta el mismo
desglose después de descargar el archivo.

## CAUPLAS: CSV de ImageKit

CAUPLAS no toma imágenes de la columna `imagen` de su master: ésa contiene sólo
nombres locales. En `/analizar` y `/generar` exige el archivo `.csv` con
encabezados `Name` y `URL`. `SKU.ext` se asigna a Imagen1 y `SKU-2.ext` en
adelante a su número de imagen; el nombre completo siempre se intenta primero
contra el SKU del master. Se permiten como máximo diez por SKU, se deduplican y
se replican iguales a todas las variantes. El análisis informa SKU con fotos,
URLs detectadas, cruzadas, omitidas, sin match y SKU del master sin foto.

Después de ese cruce, las URLs pasan por la misma validación de este documento.
Una inválida se vacía sin desplazar posiciones ni cancelar el XLSX. KG sigue
limitado a Imagen1–Imagen5 y no recibe este CSV.

## Pruebas obligatorias

- `backend/.venv/bin/python backend/scripts/test_validacion_imagenes.py`
- `backend/.venv/bin/python backend/scripts/test_publicaciones_masivas.py`
- `backend/.venv/bin/python backend/scripts/test_publicaciones_cauplas.py`
- `cd frontend && npm run build`

Como hay solicitudes HTTPS controladas por datos de catálogo, aplicar también la
skill `api-seguridad` y pedir auditoría `api-guardian` sobre el diff.
