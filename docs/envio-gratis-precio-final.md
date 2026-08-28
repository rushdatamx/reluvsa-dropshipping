# Envío Gratis según el precio final

> 🔴 Leer completo antes de tocar `backend/services/generador_plantilla.py`, en especial
> `COLUMNAS`, `CONSTANTES`, `FilaPublicacion` o `escribir_xlsx`.

## Contrato operativo

La columna de Mercado Libre `Envio Gratis(si,no)` es un campo **derivado por publicación**:

- `fila.precio < 299.00` → `No`
- `fila.precio >= 299.00` → `Si`
- `fila.precio is None` → celda vacía

El umbral es continuo e inclusivo desde `$299.00`. Los valores se escriben como `Si` y `No`,
sin acento, por compatibilidad con la plantilla vigente de Mercado Libre.

## Única fuente de verdad

La decisión usa **exclusivamente `fila.precio`**. Ese atributo ya contiene el precio final
producido por `calcular_precio`: Gran Mayoreo/costo, IVA, utilidad, comisión de ML y el envío
configurado.

No se debe:

- volver a incluir `Envio Gratis(si,no)` en `CONSTANTES`;
- recalcular el precio dentro de `escribir_xlsx`;
- decidir por costo base, línea, peso, dimensiones o costo de envío;
- inventar `No` o `Si` cuando el precio sea `None`;
- cambiar el umbral a un rango ambiguo entre 298 y 299.

La regla no modifica la fórmula de precio ni resuelve `ENVIO_POR_LINEA`, que sigue pendiente
de datos de Gaby. Es independiente del formato de entrada: master KG y catálogo KG legado
terminan ambos en `escribir_xlsx`.

## Implementación

`CONSTANTES` contiene únicamente campos que siempre se repiten. Dentro del ciclo de
`escribir_xlsx`, Precio y Envío Gratis se escriben juntos sólo cuando `fila.precio` no es
`None`:

```python
if fila.precio is not None:
    ws.cell(f, idx["Precio"], fila.precio)
    ws.cell(f, idx["Envio Gratis(si,no)"],
            "Si" if fila.precio >= 299.00 else "No")
```

No extraer este criterio desde `fila.linea` ni desde `config.params_precio`: el valor final
de la fila es el contrato entre el cálculo y el Excel.

## Plantilla y presentación

La plantilla conserva exactamente 36 columnas. `Envio Gratis(si,no)` permanece en la
posición 11, entre Dimensiones y SKU. Al dejar de ser constante también deja de recibir el
relleno amarillo reservado para constantes; esto es intencional porque su valor cambia por
fila.

## Regresiones obligatorias

Ejecutar desde `backend/`:

```bash
/usr/bin/python3 scripts/test_publicaciones_masivas.py
```

La suite debe generar y volver a leer el Excel para verificar:

1. `$298.99` produce `No`.
2. `$299.00` produce `Si`.
3. Un precio superior produce `Si`.
4. `None` deja vacíos Precio y Envío Gratis.
5. Los 36 encabezados conservan exactamente su nombre y orden.

Línea base al implementar esta regla: **77/77**. El cambio original corresponde al commit
`1503b55`, desplegado en producción el 2026-08-28.
