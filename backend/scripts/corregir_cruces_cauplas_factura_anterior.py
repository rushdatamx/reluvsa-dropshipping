"""
Corrige los cruces factura↔venta de CAUPLAS donde la factura se emitió ANTES de que la
venta existiera — físicamente imposible, nadie factura lo que todavía no ha vendido.

⚠️ EXCLUSIVO DE CAUPLAS (`codigo_bodega = 'CAUPLAS'`). Mandato de Mario (2026-08-17):
el reporte de Gaby es de CAUPLAS y el desempate por folio de pedido (`M######`) es un
formato de código propio de este proveedor. Aplicarlo a otro sería extrapolar.

QUÉ REPORTÓ GABY (2026-08-17)
-----------------------------
Mandó el CSV de Ventas de CAUPLAS con una columna AGREGADA A MANO: el folio que viene
impreso en cada PDF. Es una verdad de campo de 22 ventas. Su ejemplo textual:

    "2000017906137676 por ejemplo esta venta en la pagina hizo cruce con esta fac
     970096782, pero en la factura viene con la 970096819"

Medido contra sus 22 anotaciones, el portal acertaba 15, ponía 6 folios equivocados y
marcaba 1 como no facturada estándolo.

LA CAUSA
--------
CAUPLAS reusa el ID interno de la pieza en TODAS sus facturas y sólo cambia el folio de
pedido que va al lado. El matcher comparaba nada más el ID interno:

    factura 970096782 (12-ago 16:17) -> "7694 M2653006"  <- se llevó la venta
    factura 970096819 (13-ago 16:19) -> "7694 M2653287"  <- la de verdad, huérfana

Y la venta de Gaby ocurrió el 12-ago a las **18:10**, o sea DOS HORAS DESPUÉS de que se
emitiera la factura que el portal le colgó. El filtro de fecha compara con `date()` y no
con la hora (decisión correcta: comparar timestamps rompería cruces legítimos, ver
`services/matcher._filtro_fecha`), así que ese caso pasaba.

ESTADO QUE CORRIGE (medido sobre copia de la BD de prod, 2026-08-17)
--------------------------------------------------------------------
De los 244 conceptos CAUPLAS cruzados, sólo **6** toman una venta POSTERIOR a su propia
factura. Los 6 son del mismo día (ninguno cruza días, eso ya lo impedía el filtro).

De esos 6, Gaby anotó 3 — y **los 3 están mal según ella**, y en los 3 el folio correcto
que ella señala es de una factura POSTERIOR. Verdad de campo a favor, 3 de 3.

LAS 4 REGLAS DE ESTE SCRIPT
---------------------------
R1. Sólo se toca un cruce cuyo `fecha_venta > fecha_factura` (timestamp). Es la condición
    imposible; todo lo demás se queda como está.
R2. Debe existir un concepto HUÉRFANO de la MISMA pieza en una factura cuya fecha sea
    >= la fecha de la venta. Se reasigna al más antiguo de ésos: es la primera factura
    que pudo amparar la venta.
    🔴 Si no existe, NO SE TOCA. Medido: la venta 2000017927450774 no tiene ninguna
    factura posterior con su pieza, así que liberarla la dejaría pendiente para siempre
    — perder información sin poner nada mejor.
R3. La pieza se compara por token de >= 4 caracteres (misma normalización que el
    matcher). Un token corto como '409' vive en 21 kits distintos y robaría conceptos
    ajenos.
R4. El concepto que cede la venta queda PENDIENTE (`num_venta_match = NULL`), no
    borrado. Nada se elimina: el recruce puede volver a colocarlo si aparece su venta.

Ante cualquier duda se prefiere el PENDIENTE al cruce inventado (regla del proyecto: un
pendiente se ve y se corrige; un falso "✓ Facturado" nadie lo vuelve a mirar).

USO
---
    python corregir_cruces_cauplas_factura_anterior.py --db <ruta>             # simula
    python corregir_cruces_cauplas_factura_anterior.py --db <ruta> --ejecutar  # escribe

Idempotente: una segunda corrida no encuentra nada que corregir.
"""
import argparse
import re
import sqlite3
import sys

CODIGO_BODEGA = "CAUPLAS"      # ⚠️ EXCLUSIVO DE CAUPLAS — ver encabezado
LARGO_TOKEN_MIN = 4            # R3: un token más corto es demasiado genérico


def tokens_pieza(s):
    """Tokens del ID interno de una pieza, sin el prefijo de bodega.

    Misma normalización que `matcher._tokens_componente`: se recorta el prefijo
    alfabético ('CAU11370' -> '11370') ANTES de tokenizar, o la regex de códigos-M se
    come la última letra del prefijo y saca un token fantasma.
    """
    limpio = re.sub(r"^[A-Z]{2,6}(?=\d)", "", (s or "").strip().upper())
    return set(re.findall(r"\d{3,}", limpio))


def _misma_pieza(a, b):
    """R3: comparten al menos un token de >= 4 caracteres."""
    return bool({t for t in (a & b) if len(t) >= LARGO_TOKEN_MIN})


def analizar(conn):
    """Devuelve la lista de correcciones a aplicar. No escribe nada."""
    prov = conn.execute(
        "SELECT id FROM proveedores WHERE UPPER(TRIM(codigo_bodega)) = ?",
        (CODIGO_BODEGA,),
    ).fetchone()
    if not prov:
        return []
    pid = prov["id"]

    conceptos = conn.execute(
        """SELECT fc.id, fc.codigo_prov, fc.num_venta_match, fc.match_method,
                  fc.match_confidence, f.id AS factura_id, f.folio, f.fecha_factura
           FROM factura_conceptos fc
           JOIN facturas f ON f.id = fc.factura_id
           WHERE f.proveedor_id = ?""",
        (pid,),
    ).fetchall()

    # Huérfanos disponibles como destino, del más antiguo al más nuevo (R2).
    huerfanos = sorted(
        (c for c in conceptos if c["num_venta_match"] is None and c["fecha_factura"]),
        key=lambda c: c["fecha_factura"],
    )
    tomados = set()
    correcciones = []

    for c in conceptos:
        nv = c["num_venta_match"]
        if not nv or not c["fecha_factura"]:
            continue
        venta = conn.execute(
            "SELECT num_venta, fecha_venta, sku FROM ventas_ml WHERE num_venta = ?",
            (nv,),
        ).fetchone()
        if not venta or not venta["fecha_venta"]:
            continue

        # R1: sólo los imposibles — la factura se emitió antes de que la venta existiera.
        if venta["fecha_venta"] <= c["fecha_factura"]:
            continue

        pieza = tokens_pieza(c["codigo_prov"])
        if not pieza:
            continue

        # R2: el huérfano más antiguo de la misma pieza, en una factura que ya podía
        # amparar la venta.
        destino = next(
            (h for h in huerfanos
             if h["id"] not in tomados
             and h["fecha_factura"] >= venta["fecha_venta"]
             and _misma_pieza(pieza, tokens_pieza(h["codigo_prov"]))),
            None,
        )
        if destino is None:
            # R2: sin alternativa NO se toca. Liberar dejaría la venta pendiente para
            # siempre: perder información sin poner nada mejor.
            continue

        tomados.add(destino["id"])
        correcciones.append({
            "num_venta": nv,
            "fecha_venta": venta["fecha_venta"],
            "sku": venta["sku"],
            "concepto_libera": c["id"],
            "folio_antes": c["folio"],
            "fecha_factura_antes": c["fecha_factura"],
            "metodo_antes": c["match_method"],
            "confianza_antes": c["match_confidence"],
            "concepto_recibe": destino["id"],
            "folio_despues": destino["folio"],
            "fecha_factura_despues": destino["fecha_factura"],
            "codigo_antes": c["codigo_prov"],
            "codigo_despues": destino["codigo_prov"],
        })

    return correcciones


def aplicar(conn, correcciones):
    """Escribe las correcciones. R4: el que cede queda PENDIENTE, no se borra."""
    for x in correcciones:
        # El concepto equivocado suelta la venta y vuelve a la cola del recruce.
        conn.execute(
            """UPDATE factura_conceptos
               SET num_venta_match = NULL, match_method = NULL, match_confidence = NULL
               WHERE id = ?""",
            (x["concepto_libera"],),
        )
        # El concepto de la factura correcta la recibe, conservando el método que le
        # corresponde por origen (ID interno / componente de kit según su código).
        conn.execute(
            """UPDATE factura_conceptos
               SET num_venta_match = ?, match_method = ?, match_confidence = ?
               WHERE id = ?""",
            (x["num_venta"], x["metodo_antes"], x["confianza_antes"],
             x["concepto_recibe"]),
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--ejecutar", action="store_true",
                    help="escribe los cambios (sin esta bandera sólo simula)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    antes_cruzados = conn.execute(
        """SELECT COUNT(*) FROM factura_conceptos fc JOIN facturas f ON f.id = fc.factura_id
           JOIN proveedores p ON p.id = f.proveedor_id
           WHERE UPPER(TRIM(p.codigo_bodega)) = ? AND fc.num_venta_match IS NOT NULL""",
        (CODIGO_BODEGA,),
    ).fetchone()[0]
    antes_filas = conn.execute("SELECT COUNT(*) FROM factura_conceptos").fetchone()[0]

    correcciones = analizar(conn)

    print(f"Conceptos CAUPLAS cruzados: {antes_cruzados}")
    print(f"Correcciones detectadas   : {len(correcciones)}")
    print()
    for x in correcciones:
        print(f"  venta {x['num_venta']}  (vendida {x['fecha_venta'][:16]}, sku {x['sku']})")
        print(f"     ANTES   fact {x['folio_antes']}  {x['fecha_factura_antes'][:16]}"
              f"  [{x['codigo_antes']}]  <- emitida ANTES de la venta")
        print(f"     DESPUES fact {x['folio_despues']}  {x['fecha_factura_despues'][:16]}"
              f"  [{x['codigo_despues']}]")
    print()

    if not correcciones:
        print("Nada que corregir.")
        return 0

    if not args.ejecutar:
        print("SIMULACIÓN — no se escribió nada. Usa --ejecutar para aplicar.")
        return 0

    aplicar(conn, correcciones)
    conn.commit()

    despues_filas = conn.execute("SELECT COUNT(*) FROM factura_conceptos").fetchone()[0]
    despues_cruzados = conn.execute(
        """SELECT COUNT(*) FROM factura_conceptos fc JOIN facturas f ON f.id = fc.factura_id
           JOIN proveedores p ON p.id = f.proveedor_id
           WHERE UPPER(TRIM(p.codigo_bodega)) = ? AND fc.num_venta_match IS NOT NULL""",
        (CODIGO_BODEGA,),
    ).fetchone()[0]

    print(f"APLICADO. Conceptos CAUPLAS cruzados: {antes_cruzados} -> {despues_cruzados}")
    print(f"Filas de factura_conceptos: {antes_filas} -> {despues_filas}"
          f"  ({'sin borrados' if antes_filas == despues_filas else '⚠️ CAMBIÓ'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
