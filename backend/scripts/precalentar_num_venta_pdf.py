"""
Precalienta `facturas.num_venta_pdf` leyendo los PDF FUERA de la transacción del portal.

POR QUÉ EXISTE (hallazgo del api-guardian, 2026-08-12)
------------------------------------------------------
El paso 0 del matcher lee el # de venta impreso en el PDF de KIM y lo cachea en
`facturas.num_venta_pdf`. La caché hace que cada PDF se abra UNA sola vez en su vida…
pero esa primera vez, si ocurre dentro del recruce, ocurre con el **write lock de
SQLite tomado**: `recruzar_conceptos_sin_match` corre dentro del `get_db()` que ya
escribió al subir ventas/colecta.

Medido por el guardián: ~114 ms por PDF × 548 facturas ≈ **62 s de lock**. Un escritor
concurrente (un webhook de ML, la sync automática) recibiría `database is locked` —el
`busy_timeout` es de 5 s—, y a Gaby se le colgaría la primera carga que hiciera.

No corrompe nada (el rollback es atómico y la caché se recalcularía), pero es un minuto
de portal trabado que se puede evitar por completo: este script hace exactamente el
mismo trabajo ANTES del deploy, commiteando por lotes, sin lock largo.

⚠️ Sólo ESCRIBE `facturas.num_venta_pdf`. No toca cruces, ni ventas, ni conceptos: si
algo saliera mal, lo peor que pasa es que la caché quede a medias y el portal la
complete solo (que es su comportamiento normal).

USO
---
    python precalentar_num_venta_pdf.py --db <ruta>             # simula
    python precalentar_num_venta_pdf.py --db <ruta> --ejecutar  # escribe la caché

Idempotente: una 2a corrida no reabre ningún PDF ya leído.
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services.num_venta_pdf import (  # noqa: E402
    CODIGO_BODEGA_KIM,
    extraer_numeros_pdf,
    resolver_pdf,
)

LOTE = 50   # commit cada N facturas: transacciones cortas, nunca un lock largo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--uploads", default="/data/uploads/facturas")
    ap.add_argument("--ejecutar", action="store_true", help="sin esto sólo simula")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    cols = {c["name"] for c in conn.execute("PRAGMA table_info(facturas)")}
    if "num_venta_pdf" not in cols:
        raise SystemExit(
            "La columna facturas.num_venta_pdf no existe todavía.\n"
            "Corre este script DESPUÉS de desplegar (la migración la crea al arrancar)."
        )

    pendientes = conn.execute(
        """SELECT f.id, f.pdf_path
           FROM facturas f
           JOIN proveedores p ON p.id = f.proveedor_id
           WHERE p.codigo_bodega = ?
             AND f.pdf_path IS NOT NULL
             AND f.num_venta_pdf IS NULL""",
        (CODIGO_BODEGA_KIM,),
    ).fetchall()

    print(f"facturas {CODIGO_BODEGA_KIM} por leer: {len(pendientes)}")
    if not args.ejecutar:
        print("\n[SIMULACIÓN] no se escribió nada. Usa --ejecutar para precalentar.")
        return 0

    con_num = sin_num = ausentes = 0
    for i, f in enumerate(pendientes, start=1):
        ruta = resolver_pdf(f["pdf_path"], args.uploads)
        if not ruta:
            # NO se cachea: el archivo puede llegar después (XML primero, PDF luego).
            ausentes += 1
            continue
        numeros = extraer_numeros_pdf(ruta)
        valor = numeros[0] if len(numeros) == 1 else ""
        conn.execute("UPDATE facturas SET num_venta_pdf = ? WHERE id = ?",
                     (valor, f["id"]))
        if valor:
            con_num += 1
        else:
            sin_num += 1
        if i % LOTE == 0:
            conn.commit()
            print(f"  {i}/{len(pendientes)}…")
    conn.commit()

    print(f"\nLISTO. con # impreso: {con_num} · sin #: {sin_num} · PDF ausente: {ausentes}")
    restantes = conn.execute(
        """SELECT COUNT(*) FROM facturas f JOIN proveedores p ON p.id = f.proveedor_id
           WHERE p.codigo_bodega = ? AND f.pdf_path IS NOT NULL
             AND f.num_venta_pdf IS NULL""",
        (CODIGO_BODEGA_KIM,),
    ).fetchone()[0]
    print(f"quedan sin leer (sólo PDF ausentes): {restantes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
