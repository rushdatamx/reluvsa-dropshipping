"""Limpieza de los cruces falsos persistidos — VERSION QUE CORRE DENTRO DEL CONTENEDOR.

Se ejecuta con:
    railway ssh --service reluvsa-dropshipping "cd /app && /opt/venv/bin/python /tmp/ejecutar_prod.py"

Hace, en orden:
  1. Backup con VACUUM INTO a /data/dropshipping.db.bak-<fecha>   (trampa 5)
  2. Libera (NULL, sin borrar filas) los conceptos con cruce imposible  (trampas 1, 2, 3)
  3. Corre el recruce en la MISMA operacion                        (trampa 4)
  4. Reporta el antes/despues y verifica las garantias

NO toca la red, ni la API de ML, ni el scheduler de sync.
"""
import datetime
import sqlite3
import sys

sys.path.insert(0, "/app")

DB = "/data/dropshipping.db"

SQL_IMPOSIBLES = """
    SELECT fc.id
    FROM factura_conceptos fc
    JOIN facturas f   ON f.id = fc.factura_id
    JOIN ventas_ml v  ON v.num_venta = fc.num_venta_match
    WHERE fc.num_venta_match IS NOT NULL
      AND date(v.fecha_venta) > date(f.fecha_factura)
"""


def main():
    from services.matcher import recruzar_conceptos_sin_match

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # --- 1. Backup -----------------------------------------------------------
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = f"{DB}.bak-{stamp}"
    conn.execute("VACUUM INTO ?", (bak,))
    print(f"backup -> {bak}")

    # --- 2. Estado previo ----------------------------------------------------
    antes = {r["id"]: r["num_venta_match"] for r in
             conn.execute("SELECT id, num_venta_match FROM factura_conceptos")}
    n_filas_antes = len(antes)
    cruzados_antes = sum(1 for v in antes.values() if v is not None)

    ids = [r["id"] for r in conn.execute(SQL_IMPOSIBLES)]
    print(f"\nconceptos con cruce imposible: {len(ids)}")
    print(f"estado previo: {n_filas_antes} conceptos, {cruzados_antes} cruzados")

    if not ids:
        print("nada que limpiar; salgo sin escribir.")
        return

    # --- 3. Liberar + recruzar, en UNA SOLA TRANSACCION -----------------------
    # ⚠️ Un solo commit al final (condicion 1 del api-guardian). Con commits separados,
    # un fallo entre el UPDATE y el recruce dejaria 243 conceptos liberados y sin
    # recruzar: ventas que hoy dicen "Facturado" pasarian a "Pendiente" hasta que
    # alguien volviera a correr esto. No es corrupcion, pero es un estado visible para
    # Gaby que no hace falta arriesgar.
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "UPDATE factura_conceptos SET num_venta_match=NULL, match_method=NULL, "
            "match_confidence=NULL WHERE id=?",
            [(i,) for i in ids])
        res = recruzar_conceptos_sin_match(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        print("ERROR: se revirtio todo, la BD queda como estaba")
        raise
    print(f"recruce: {res['conceptos_sin_match']} pendientes -> "
          f"{res['conceptos_recruzados']} recruzados")

    # --- 4. Verificacion -----------------------------------------------------
    despues = {r["id"]: r["num_venta_match"] for r in
               conn.execute("SELECT id, num_venta_match FROM factura_conceptos")}

    sel = set(ids)
    reasignado = sum(1 for i in ids if despues[i] and despues[i] != antes[i])
    igual = sum(1 for i in ids if despues[i] == antes[i])
    pendiente = sum(1 for i in ids if despues[i] is None)
    rotos = [i for i, a in antes.items()
             if i not in sel and a is not None and despues[i] != a]
    ganados = [i for i, a in antes.items()
               if i not in sel and a is None and despues[i] is not None]
    residuo = conn.execute(f"SELECT COUNT(*) FROM ({SQL_IMPOSIBLES})").fetchone()[0]

    print(f"\nde los {len(ids)} liberados:")
    print(f"    reasignados a otra venta : {reasignado}")
    print(f"    volvieron a la misma     : {igual}")
    print(f"    quedaron pendientes      : {pendiente}")
    print(f"\ncruces buenos preexistentes alterados : {len(rotos)} "
          f"{'OK' if not rotos else '<-- REVISAR'}")
    print(f"conceptos antes pendientes que ahora cruzan: {len(ganados)} (ganancia)")
    print(f"cruces imposibles restantes           : {residuo} "
          f"{'OK' if residuo == 0 else '<-- REVISAR'}")
    print(f"filas de factura_conceptos            : {n_filas_antes} -> {len(despues)} "
          f"{'OK (ninguna borrada)' if len(despues) == n_filas_antes else '<-- REVISAR'}")
    print(f"conceptos cruzados                    : {cruzados_antes} -> "
          f"{sum(1 for v in despues.values() if v is not None)}")
    conn.close()


if __name__ == "__main__":
    main()
