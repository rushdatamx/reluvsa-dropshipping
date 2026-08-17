"""Cómo una venta encuentra su envío — incluidas las ventas de carrito (BUG A).

EL PROBLEMA (reportado por Gaby el 2026-08-17)
----------------------------------------------
Mercado Libre crea **UN SOLO envío por carrito** y lo cuelga de **UNA SOLA** de las N
órdenes del pack. Las demás ventas del mismo paquete quedan sin envío, y como el
matcher sólo busca candidatas con `WHERE e.proveedor_id = ?`, quedan **invisibles**:
nunca cruzan factura, no muestran logística ni SLA, y en la pestaña Ventas salen como
"Sin envío" — sin selector para que Gaby las corrigiera a mano.

El caso con el que lo reportó (verificado contra prod):

    order.id           pack_id            SKU        envío   factura
    2000017891678512   2000014490469643   CAU4218      1       1
    2000017891680168   2000014490469643   CAU3832      0       0      <- invisible

Ella lo describió como "este número de venta sólo viene asociado a un sku cuando la
venta es de 2 skus".

Magnitud medida en prod: **796 packs multi-venta = 1,714 ventas, 918 sin envío.**

LA REGLA
--------
Un envío cubre una venta si:
  (a) está cruzado directamente a ella (`e.num_venta_ml = v.num_venta`), o
  (b) ambos pertenecen al mismo paquete (`e.pack_id = v.pack_id`).

Es correcto semánticamente: un carrito es **un solo paquete físico**, que sale de un
solo proveedor. Verificado en prod: de los 1,073 pares de ventas que comparten pack,
**1,073 comparten depósito y 0 difieren** — nunca hay dos proveedores en un carrito.
Es el mismo criterio ya aceptado para el albarán (commit `3b2cf79`), donde Mario
decidió que la nota de entrega del carrito ampara todo lo que viaja dentro.

🔴 LAS 4 TRAMPAS — no rediseñar esto sin leerlas
------------------------------------------------
1. **NO se duplican filas de `envios_colecta`.** La alternativa "obvia" —insertar un
   envío por venta del pack— **rompe el SLA**: las métricas cuentan
   `COUNT(*) FROM envios_colecta`, así que un carrito de 6 que llega tarde pesaría
   **6 veces** contra el proveedor. Gaby lo dijo explícitamente: *"como 1 retraso
   porque en general me cuenta toda la venta como 1 retrasada"*. Al propagar el
   VÍNCULO y no la FILA, el SLA sigue contando una vez por paquete y `metricas.py`
   —que está blindado— no se toca.

2. 🔴 **La condición de pack exige `pack_id IS NOT NULL` en AMBOS lados.** Sin esa
   guarda, `e.pack_id = v.pack_id` con dos NULL no une nada en SQL (NULL = NULL es
   NULL)... pero un `IS` mal escrito uniría **todas** las ventas sin pack contra
   **todos** los envíos sin pack: 22 mil ventas por 21 mil envíos. Es un producto
   cartesiano que tumba el portal, no un error de negocio sutil.

3. 🔴 **NUNCA cruzar `pack_id` contra `num_venta`.** Hay **268 números que son
   `order.id` de una venta y `pack_id` de OTRA distinta** (medido en prod). Cruzar
   las dos llaves en un OR reintroduciría el bug del albarán, pero peor: con
   confianza 1.0 y sin que nadie lo revise. El vínculo por pack es SIEMPRE
   `pack_id` contra `pack_id`.

4. **Una venta puede resolver a MÁS DE UN envío.** Ya pasaba antes de este cambio:
   en prod hay 2 ventas con 2 envíos (reexpediciones, ambas FULL). Por eso el
   listado de Ventas agrega por venta en vez de hacer un JOIN suelto — si no,
   una venta saldría en dos filas y Gaby las leería como duplicados.

EL DESEMPATE DETERMINISTA — por qué `matcher.py` ordena por `num_venta` además de fecha
---------------------------------------------------------------------------------------
Al volverse visibles las ventas hermanas, los 5 pasos del matcher pasan a tener varias
candidatas del MISMO paquete, y sus consultas eligen con
`ORDER BY v.fecha_venta DESC LIMIT 1`.

🔴 **Las hermanas comparten la fecha al segundo: 1,071 de 1,073 pares en prod.** O sea
que ese `ORDER BY` es un EMPATE, y SQLite puede devolver cualquiera de las dos — el
resultado cambiaría entre corridas sin que nadie lo note. Por eso las 5 consultas
ordenan ahora por `v.fecha_venta DESC, v.num_venta DESC`: no hace el cruce "más
correcto", lo hace **reproducible**, que es la condición para poder auditarlo.

Lo que de verdad separa a las hermanas es el SKU, y para eso ya estaban los pasos 1-2:
**1,037 de los 1,073 pares tienen SKU distinto**, así que el código de la factura elige
a la hermana correcta por mérito propio. Quedan **31 pares con el MISMO SKU**, donde no
existe dato que las distinga; ahí el desempate es arbitrario pero estable, y como los
conceptos ya cruzados se excluyen (`fc.id IS NULL`), dos conceptos de la misma factura
caen en las dos hermanas en vez de pelearse por una.
"""

# Condición SQL que decide si el envío `e` cubre la venta `v`. Se inyecta como
# fragmento (no lleva parámetros) para que las consultas la compartan literal y no
# se desincronicen: hoy la usan el listado y el CSV de Ventas, el matcher (sus 5
# pasos), el detalle de venta y las incidencias.
#
# El orden de los OR no cambia el resultado, pero se deja el cruce directo primero
# porque es el caso normal (~97% de los envíos) y SQLite corta antes.
ENVIO_CUBRE_VENTA = """(
    e.num_venta_ml = v.num_venta
    OR (e.pack_id IS NOT NULL AND v.pack_id IS NOT NULL AND e.pack_id = v.pack_id)
)"""


def envio_cubre_venta(alias_envio: str = "e", alias_venta: str = "v") -> str:
    """La condición de arriba con alias de tabla a medida.

    Útil donde las consultas no usan los alias `e`/`v` (p. ej. subqueries con `e2`).
    Devuelve un fragmento SQL sin parámetros, seguro de interpolar: no toma nada
    del exterior más que los nombres de alias que le pasa el propio código.
    """
    return (
        f"({alias_envio}.num_venta_ml = {alias_venta}.num_venta"
        f" OR ({alias_envio}.pack_id IS NOT NULL AND {alias_venta}.pack_id IS NOT NULL"
        f" AND {alias_envio}.pack_id = {alias_venta}.pack_id))"
    )
