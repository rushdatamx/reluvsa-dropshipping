"""Simula (y opcionalmente ejecuta) la limpieza de los cruces falsos persistidos.

CRITERIO: un concepto cuyo cruce apunta a una venta fechada DESPUES de su propia
factura es imposible por definicion -> se libera (NULL) y se deja que el recruce,
ya con el filtro de fecha del commit f699577, decida de nuevo.

NO borra filas de factura_conceptos: solo pone num_venta_match, match_method y
match_confidence en NULL. El concepto es dato real del XML.

Uso:
    python3 limpiar.py --db <ruta>            # simula sobre esa copia
    python3 limpiar.py --db <ruta> --aplicar  # escribe en esa BD
"""
import argparse
import shutil
import sqlite3
import sys
from collections import Counter

BACKEND = "/Users/jmariopgarcia/Desktop/2026/RushData/RELUVSA/dropshipping-reluvsa/backend"
sys.path.insert(0, BACKEND)
from services.matcher import recruzar_conceptos_sin_match  # noqa: E402

# El criterio, en un solo lugar. Se compara con date() y no con el timestamp: una
# factura emitida a las 10:00 del mismo dia en que se vendio a las 14:00 es legitima
# (trampa 3 del documento).
SQL_IMPOSIBLES = """
    SELECT fc.id, fc.num_venta_match, fc.match_method, fc.match_confidence,
           f.fecha_factura, v.fecha_venta, p.codigo_bodega
    FROM factura_conceptos fc
    JOIN facturas f   ON f.id = fc.factura_id
    JOIN ventas_ml v  ON v.num_venta = fc.num_venta_match
    LEFT JOIN proveedores p ON p.id = f.proveedor_id
    WHERE fc.num_venta_match IS NOT NULL
      AND date(v.fecha_venta) > date(f.fecha_factura)
"""


def snapshot(conn):
    """Estado de cada concepto cruzado: id -> num_venta_match."""
    return {r["id"]: r["num_venta_match"] for r in conn.execute(
        "SELECT id, num_venta_match FROM factura_conceptos").fetchall()}


def limpiar(conn, verbose=True):
    antes = snapshot(conn)

    imposibles = conn.execute(SQL_IMPOSIBLES).fetchall()
    ids = [r["id"] for r in imposibles]
    if verbose:
        print(f"conceptos con cruce imposible: {len(ids)}")
        rep = Counter((r["codigo_bodega"], r["match_method"]) for r in imposibles)
        for (bod, met), n in rep.most_common():
            print(f"    {bod:8s} {met:18s} {n:4d}")

    # Liberar. NUNCA DELETE: el concepto es dato del XML.
    conn.executemany(
        "UPDATE factura_conceptos SET num_venta_match=NULL, match_method=NULL, "
        "match_confidence=NULL WHERE id=?",
        [(i,) for i in ids])
    conn.commit()

    # Recruce, en la misma operacion (trampa 4). Usa el filtro de fecha.
    res = recruzar_conceptos_sin_match(conn)
    conn.commit()
    if verbose:
        print(f"\nrecruce: {res['conceptos_sin_match']} pendientes -> "
              f"{res['conceptos_recruzados']} recruzados")

    despues = snapshot(conn)

    # Que paso con CADA uno de los liberados.
    reasignado = igual = pendiente = 0
    for i in ids:
        a, d = antes[i], despues[i]
        if d is None:
            pendiente += 1
        elif d == a:
            igual += 1
        else:
            reasignado += 1

    # Garantia que importa: ningun cruce BUENO (que existia y no fue seleccionado)
    # puede cambiar ni perderse.
    #
    # ⚠️ Hay que distinguir dos cosas que un chequeo ingenuo confunde:
    #   - un concepto que YA estaba cruzado y cambia  -> regresion, inaceptable.
    #   - un concepto que estaba PENDIENTE (None) y ahora cruza -> ganancia: la limpieza
    #     libero la venta que necesitaba. Es efecto deseado del recruce, no dano.
    sel = set(ids)
    rotos = [i for i, a in antes.items()
             if i not in sel and a is not None and despues[i] != a]
    ganados = [i for i, a in antes.items()
               if i not in sel and a is None and despues[i] is not None]

    if verbose:
        print(f"\nde los {len(ids)} liberados:")
        print(f"    reasignados a otra venta : {reasignado}")
        print(f"    volvieron a la misma     : {igual}")
        print(f"    quedaron pendientes      : {pendiente}")
        print(f"\ncruces buenos preexistentes alterados: {len(rotos)} "
              f"{'<- OK' if not rotos else '<- REVISAR'}")
        print(f"conceptos antes pendientes que ahora cruzan: {len(ganados)} (ganancia)")

        # Los que vuelven a la misma venta seguirian siendo imposibles: no deberia haber.
        residuo = conn.execute(f"SELECT COUNT(*) FROM ({SQL_IMPOSIBLES})").fetchone()[0]
        print(f"cruces imposibles restantes: {residuo} "
              f"{'<- OK' if residuo == 0 else '<- REVISAR'}")

    return {"liberados": len(ids), "reasignado": reasignado, "igual": igual,
            "pendiente": pendiente, "rotos": rotos, "ganados": ganados}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--aplicar", action="store_true",
                    help="escribe sobre --db; sin esto trabaja sobre una copia temporal")
    args = ap.parse_args()

    db = args.db
    if not args.aplicar:
        db = args.db + ".sim"
        shutil.copy(args.db, db)
        print(f"simulacion sobre copia: {db}\n")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    limpiar(conn)
    conn.close()


if __name__ == "__main__":
    main()
