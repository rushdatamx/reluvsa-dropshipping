# Corrección de fecha efectiva de Mercado Libre

## Alcance

Desde este cambio, las ventas nuevas usan `order.date_closed` como `fecha_venta` y
`order.date_created` como fallback. `fecha_creacion_ml` conserva `date_created`
para auditoría. Las ventas históricas cuyo `fecha_creacion_ml` permanece `NULL`
no cambian de fecha al reaparecer en una sincronización.

No se autoriza backfill. En la operación puntual se corrige exclusivamente la
venta `2000018209888406`. Las otras **63,208 ventas existentes no se modifican**.

## Runbook de producción

Requiere un veredicto nuevo `APROBADO` del `api-guardian` inmediatamente antes
de ejecutar estos pasos. La revisión de desarrollo no sustituye esa aprobación.

1. Desactivar la sincronización automática desde el control administrativo de
   Mercado Libre (`POST /api/ml/sync-auto`, `activo=false`) y confirmar que no
   exista una corrida `en_curso`.
2. Respaldar `/data/dropshipping.db` con nombre fechado y conservar el original.
3. Registrar antes de desplegar:
   - conteo total de `ventas_ml`;
   - conteo con `fecha_creacion_ml IS NULL` (si la columna ya existe);
   - valores completos de la venta `2000018209888406`.
4. Desplegar y verificar que `init_database()` agregó la columna nullable sin
   poblarla.
5. Dentro del contenedor, simular (sin `--ejecutar`):

   ```bash
   /opt/venv/bin/python backend/scripts/corregir_fecha_venta_ml.py 2000018209888406
   ```

6. Confirmar que la propuesta sea `2026-09-02 10:56:40` y aplicar:

   ```bash
   /opt/venv/bin/python backend/scripts/corregir_fecha_venta_ml.py 2000018209888406 --ejecutar
   ```

7. Repetir la simulación para comprobar idempotencia. Verificar que la fila sólo
   cambió en `fecha_creacion_ml` y `fecha_venta`, que el conteo total no cambió y
   que exactamente las otras 63,208 filas siguen con sus valores previos.
8. Ejecutar `PRAGMA integrity_check;` y exigir resultado `ok`.
9. Reactivar la sincronización automática y comprobar la siguiente corrida.

La herramienta sólo hace `GET /orders/{id}` a Mercado Libre y escribe localmente
las dos columnas de fecha dentro de una transacción SQLite.
