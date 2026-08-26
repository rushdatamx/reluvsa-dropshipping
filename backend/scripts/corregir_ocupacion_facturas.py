#!/usr/bin/env python3
"""Simula o aplica la corrección inicial de ocupaciones inequívocas.

Por defecto sólo reporta. ``--apply`` crea primero un backup consistente de SQLite y
modifica exclusivamente columnas de ``factura_conceptos``; nunca elimina filas ni
ejecuta el matcher. Los casos inferencia-vs-inferencia quedan fuera deliberadamente.
"""
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


VENTA_KIM = "2000018053646644"


def candidatos(conn):
    filas = []
    kim = conn.execute(
        """SELECT fc.id, fc.factura_id, fc.num_venta_match, fc.match_method,
                  p.codigo_bodega, f.serie, f.folio
             FROM factura_conceptos fc JOIN facturas f ON f.id=fc.factura_id
             JOIN proveedores p ON p.id=f.proveedor_id
            WHERE fc.num_venta_match=? AND p.codigo_bodega='KIM'
              AND f.serie='K' AND f.folio IN ('30588','30618')""",
        (VENTA_KIM,),
    ).fetchall()
    ganadora = {r["factura_id"] for r in kim if str(r["folio"]) == "30618"}
    if len(ganadora) == 1:
        filas.extend((r, "desplazada_por_numero_explicito") for r in kim
                     if str(r["folio"]) == "30588")

    cauplas = conn.execute(
        """WITH explicitas AS (
               SELECT DISTINCT fc.num_venta_match venta, fc.factura_id
                 FROM factura_conceptos fc JOIN facturas f ON f.id=fc.factura_id
                 JOIN proveedores p ON p.id=f.proveedor_id
                WHERE p.codigo_bodega='CAUPLAS' AND fc.num_venta_match IS NOT NULL
                  AND fc.match_method='num_venta_proveedor_cauplas'
           )
           SELECT inf.*, p.codigo_bodega, f.serie, f.folio
             FROM factura_conceptos inf JOIN facturas f ON f.id=inf.factura_id
             JOIN proveedores p ON p.id=f.proveedor_id
             JOIN explicitas e ON e.venta=inf.num_venta_match
                              AND e.factura_id!=inf.factura_id
            WHERE p.codigo_bodega='CAUPLAS'
              AND COALESCE(inf.match_method,'')!='num_venta_proveedor_cauplas'
              AND (SELECT COUNT(*) FROM explicitas x WHERE x.venta=e.venta)=1"""
    ).fetchall()
    filas.extend((r, "desplazada_por_numero_explicito") for r in cauplas)
    # Evita duplicar por joins o por una eventual coincidencia con el caso fijo.
    return {r["id"]: (r, conflicto) for r, conflicto in filas}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", required=True, type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.database)
    conn.row_factory = sqlite3.Row
    total_antes = conn.execute("SELECT COUNT(*) FROM factura_conceptos").fetchone()[0]
    casos = candidatos(conn)
    print(f"modo={'APLICAR' if args.apply else 'SIMULACIÓN'} candidatos={len(casos)}")
    for r, conflicto in casos.values():
        print(dict(r), "=>", conflicto)
    if not args.apply:
        return
    backup = args.database.with_name(
        f"{args.database.stem}.backup-ocupacion-{datetime.now():%Y%m%d-%H%M%S}{args.database.suffix}"
    )
    destino = sqlite3.connect(backup)
    conn.backup(destino)
    destino.close()
    print(f"backup={backup}")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for concepto_id, (_, conflicto) in casos.items():
            conn.execute(
                """UPDATE factura_conceptos SET num_venta_match=NULL,
                          match_method=NULL, match_confidence=NULL,
                          conflicto_factura=? WHERE id=?""",
                (conflicto, concepto_id),
            )
        total_despues = conn.execute("SELECT COUNT(*) FROM factura_conceptos").fetchone()[0]
        assert total_despues == total_antes, "cambió el número de filas"
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print(f"aplicados={len(casos)} filas_antes={total_antes} filas_despues={total_despues}")


if __name__ == "__main__":
    main()
