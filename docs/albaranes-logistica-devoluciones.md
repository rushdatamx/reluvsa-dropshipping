# Albaranes pendientes y logística de devoluciones

## Albaranes

`POST /api/uploads/albaran` admite archivos mixtos de ambas cuentas. Resuelve primero
`num_venta_origen` en todas las cuentas, con PK legacy de principal sólo si falta
el origen. Sólo sin coincidencias de venta consulta carrito. Más de una cuenta en
el paso elegido implica ambigüedad sin escritura. El carrito único actualiza todas
sus ventas dentro de esa cuenta. Celdas vacías conservan valores y duplicados se
procesan en orden. Sólo se actualizan ventas locales existentes.

Se conservan contadores anteriores; `ambiguos`, `sin_aplicar`, `filas_sin_aplicar` y
`reporte_sin_aplicar_base64` agregan detalle completo y XLSX efímero. El reporte incluye
posición, valores originales, número recibido, albarán, fecha, resultado y candidatas.
Todos sus valores son texto literal para preservar identificadores y evitar fórmulas.
La UI muestra cinco filas y permite descargar todas. Para recuperar un reporte se
vuelve a cargar el archivo; no existe histórico nuevo.

## Logística

`normalizar_devolucion` admite `shipments[]` y legacy `shipping`. Expone únicamente
id, tipo, destino, estado y seguimiento, sin direcciones. Los tipos `return` y
`return_from_triage` son retornos conocidos; legacy implica retorno. Estados:
`pending`/`ready_to_ship` preparación, `shipped` en camino, `delivered` entregado,
`not_delivered` incidencia y `cancelled` cancelado. Otros valores permanecen desconocidos.
La prioridad del caso es en camino, incidencia, preparación, desconocido, entregado,
cancelado; cualquier tramo de retorno shipped lo mantiene en camino aunque el reclamo
esté cerrado. Se muestran todos los tramos; `seller_address` es vendedor y `warehouse`
es depósito de ML, nunca prueba de recepción del proveedor.

`GET /api/devoluciones` usa `estado_logistico=en_camino` por defecto, acepta todos y
cada categoría, fechas de solicitud opcionales (`fecha_desde`/`fecha_hasta`, inclusivas)
y vencimiento. Listado y contadores aplican el mismo universo. No existe corte fijo.
Reclamo, devolución y logística son campos independientes.

`estado_logistico=entregado_al_vendedor` selecciona casos con al menos un tramo
de retorno entregado a `seller_address`, usando los envíos guardados, incluidos
los del formato legacy. Un caso con otro tramo en tránsito también aparece en
**En camino** y conserva ese estado general. **Entregado** sigue consultando
el estado general, que puede incluir entregas a depósitos de ML o sin destino.

La migración idempotente añade `logistica_consultada_en`, inicialmente NULL. El worker
encola todos los existentes una vez y luego preparación, tránsito e incidencia cada
seis horas, incluso sin cambio del reclamo. Reutiliza cola, deduplicación y backoff.
Los errores preservan el snapshot previo y su fecha; no alteran revisión ni evidencias.
La fecha del conjunto corresponde a la consulta más antigua y queda pendiente si algún
caso no ha sido consultado. Las consultas ML pasan exclusivamente por `ml_client`.

## Validación

Suites: test_albaran_multicuenta, test_albaran_pack_id, test_filtro_albaran,
test_devoluciones_e2e, test_logistica_devoluciones, test_sync_ml_e2e,
test_sync_automatica y test_ml_client_solo_lectura. Frontend: npm run build.
Antes de desplegar: revisión api-guardian, GET de ambas cuentas, respaldo SQLite con
integrity_check. Las muestras reales confirmaron shipments[] y destination.name.
La cuenta secundaria mostró retorno shipped con tramo delivered: manda el tramo.
