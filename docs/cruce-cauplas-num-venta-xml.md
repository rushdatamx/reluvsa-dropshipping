# Cruce CAUPLAS por número de venta timbrado en XML

> **Vigente desde:** 2026-08-25 · **Commit:** `a97aa4a` · **Producción:** Railway `SUCCESS`
>
> 🔴 Leer completo antes de tocar `services/parser_cfdi.py`, `services/matcher.py`,
> `routers/facturas.py`, la tabla `factura_conceptos` o
> `scripts/backfill_num_venta_cauplas.py`.

## 1. Problema que resuelve

El código de pieza identifica el producto, pero no distingue entre varias ventas del
mismo producto. Antes, el matcher elegía una candidata mediante fecha, disponibilidad y
similitud; podía asociar una factura correcta a la venta equivocada.

CAUPLAS ahora timbra por concepto:

```text
NoIdentificacion="<código de pieza> <número ML>"
```

Ejemplo conceptual:

```text
11370 M2650963 2000017906137676
```

El sufijo numérico es evidencia declarada por el proveedor. Para CAUPLAS tiene prioridad
sobre código, ID interno, kits, fecha y fuzzy.

## 2. Alcance y compatibilidad

- Aplica exclusivamente al RFC `QHO180116NW0` y bodega `CAUPLAS`.
- KIM conserva su paso 0 basado en el número impreso en PDF.
- AG, VAZLO, KG y cualquier otro emisor conservan exactamente su parser y matcher.
- Un CFDI CAUPLAS legacy sin sufijo numérico conserva los pasos históricos.
- No realiza llamadas a Mercado Libre. Todo se resuelve con XML y SQLite.
- Nunca escribe bodega, envío, proveedor, depósito, SLA ni datos de la venta.

## 3. Parser CFDI

`services/parser_cfdi.py::separar_no_identificacion_cauplas` separa el último grupo
numérico precedido por espacio. El parser devuelve por concepto:

```python
{
    "codigo": "11370 M2650963",
    "num_venta_proveedor": "2000017906137676",
    "cruce_numero_estado": "numero_valido",
}
```

La longitud válida es exactamente 16 dígitos. Cualquier otra longitud explícita se
conserva para mostrarla y se marca `numero_invalido`; no se oculta ni se interpreta como
legacy. Esto incluye números de 14, 15 y 17 dígitos.

Si no hay sufijo numérico separado, `num_venta_proveedor` y el estado quedan en `NULL` y
el concepto sigue por el comportamiento legacy.

## 4. Persistencia

La migración idempotente de `database.py` agrega a `factura_conceptos`:

- `num_venta_proveedor TEXT`: valor declarado por CAUPLAS, incluso si es inválido.
- `cruce_numero_estado TEXT`: diagnóstico de la evidencia.

Estados usados:

- `numero_valido`: parseado, pendiente de resolución inicial.
- `numero_invalido`: longitud distinta de 16.
- `numero_no_resuelve`: no existe como `num_venta` ni como `pack_id`.
- `numero_ambiguo`: varias órdenes del pack siguen siendo compatibles.
- `conflicto_pieza`: el SKU o los componentes del kit no corresponden.
- `conflicto_fecha`: la venta es posterior al día de la factura.
- `conflicto_proveedor`: existe evidencia explícita de otro proveedor.
- `cruzado`: todas las guardas pasaron.

Un cruce exitoso persiste:

```text
match_method = num_venta_proveedor_cauplas
match_confidence = 1.0
```

## 5. Algoritmo del paso 0 CAUPLAS

Por cada concepto con número explícito:

1. Exigir `^\d{16}$`; si falla, bloquear.
2. Ejecutar `SELECT ... FROM ventas_ml WHERE num_venta = ?`.
3. Sólo cuando la consulta anterior no devuelve filas, ejecutar
   `SELECT ... FROM ventas_ml WHERE pack_id = ?`.
4. Rechazar ventas posteriores al día del CFDI.
5. Confirmar la pieza contra el SKU normalizado o contra un componente de kit.
6. Rechazar evidencia explícita de otro proveedor.
7. Si queda exactamente una candidata, cruzar con confianza `1.0`; de lo contrario,
   dejar pendiente y registrar el estado.

### Invariante crítica: nunca usar `OR`

Está prohibido reemplazar los pasos 2–3 por:

```sql
WHERE num_venta = ? OR pack_id = ?
```

Existen números que son `order.id` de una venta y `pack_id` de otra. `num_venta` siempre
gana y `pack_id` es exclusivamente el fallback.

### Bodega desconocida

Número+piezas+fecha pueden cruzar aunque la venta no tenga bodega o envío asignado. La
ausencia de evidencia no equivale a evidencia contraria. Si aparece un proveedor
explícito distinto, el resultado es `conflicto_proveedor`.

### Kits y varios conceptos

El número es por concepto, pero varios componentes pueden declarar la misma venta-kit.
Eso es válido: el paso explícito no excluye una venta porque otro concepto ya la use.
Cada componente debe confirmar por sí mismo que pertenece al kit.

## 6. Regla de abstención

Si CAUPLAS declaró un número, el sistema no debe contradecirlo con una heurística. Los
siguientes estados dejan `num_venta_match = NULL` y bloquean código/fecha/fuzzy:

- número inválido;
- número inexistente;
- ambigüedad;
- pieza incompatible;
- fecha incompatible;
- proveedor incompatible.

Un pendiente es visible y reclamable; un falso positivo queda oculto como “Facturado”.
Por eso abstenerse es el comportamiento seguro.

## 7. Interfaz de Facturas

El detalle muestra columnas separadas para:

- código limpio de pieza;
- número declarado por CAUPLAS;
- venta finalmente cruzada.

`numero_invalido` muestra el badge rojo **“Número inválido”**. Los conceptos legacy sin
número mantienen la presentación normal de “sin cruce”; no se etiquetan como error de
formato.

## 8. Backfill histórico

Script:

```bash
python scripts/backfill_num_venta_cauplas.py \
  --db /ruta/dropshipping.db \
  --uploads /ruta/uploads
```

Sin `--ejecutar` es una simulación de solo lectura. Clasifica:

- `ya_correcto`;
- `corregible`;
- `invalido`;
- `ambiguo`;
- `conflicto`;
- `sin_numero`.

Reporta cada cambio propuesto `venta anterior → venta declarada`. No resuelve conflictos
automáticamente y no modifica conceptos sin evidencia.

### Procedimiento obligatorio antes de `--ejecutar`

1. Revisar conteos y una muestra concreta con Gaby/Mario.
2. Apagar temporalmente la sincronización automática.
3. Crear backup fechado de SQLite.
4. Ejecutar el backfill aprobado con `--ejecutar`.
5. Verificar conteos, filas, cruces e `integrity_check`.
6. Ejecutarlo de nuevo: debe proponer cero correcciones.
7. Reactivar la sincronización automática incluso si hubo un error intermedio.

🔴 La ejecución productiva no formó parte del despliegue inicial y sigue requiriendo
aprobación explícita.

## 9. Pruebas que protegen la regla

`scripts/test_num_venta_xml_cauplas.py` cubre:

- 19 conceptos, 18 números válidos y el inválido `200018024092132`;
- longitudes 14, 15, 16 y 17;
- venta exacta y fallback por `pack_id`;
- colisión `order.id`/`pack_id` con prioridad de la orden;
- SKU normalizado y SKU `NULL`;
- componentes de kit compartiendo una venta;
- pack ambiguo;
- venta posterior a factura;
- bodega desconocida;
- proveedor explícito incompatible;
- número inexistente e inválido bloqueando heurísticas;
- factura legacy conservando el matcher anterior;
- backfill simulado, ejecución e idempotencia de segunda corrida.

También deben permanecer verdes las suites de CAUPLAS, KIM, kits, carritos, fecha,
recruce y las obligatorias de seguridad:

```bash
backend/.venv/bin/python backend/scripts/test_ml_client_solo_lectura.py
backend/.venv/bin/python backend/scripts/test_sync_ml_e2e.py
```

## 10. Seguridad y auditoría

El cambio no toca `ml_client.py`, `sync_ml.py`, routers ML, OAuth ni webhooks. No agrega
clientes HTTP ni verbos de escritura. El `api-guardian` rechazó correctamente una primera
versión por dos casos de integridad —SKU vacío y números cortos tratados como legacy—;
ambos se corrigieron y el dictamen final fue **APROBADO**.

Si un cambio futuro introduce red o toca datos de ML, deja de estar dentro de este diseño
y debe pasar nuevamente por la skill `api-seguridad` y el `api-guardian` antes del commit.
