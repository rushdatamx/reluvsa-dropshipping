"""
Fija las garantías del cruce de albaranes en DOS PASOS (num_venta -> pack_id).

Contexto: Gaby captura el número que ML le muestra EN PANTALLA, que es el `pack_id`
cuando la venta viene de un carrito y el `order.id` cuando no. Su archivo trae los
dos tipos mezclados. Antes el parser sólo buscaba por num_venta y reportaba el resto
como "no encontrados" (79/52 en su archivo real de KIMS).

Las trampas que fija este test — romper cualquiera reintroduce un bug silencioso:

 1. num_venta GANA sobre pack_id. Hay 268 números en prod que son order.id de una
    venta y pack_id de OTRA: si el orden se invierte (o peor, si se hace un OR en
    una sola consulta), el albarán se escribe en la venta equivocada.
 2. NUNCA un `WHERE num_venta = ? OR pack_id = ?`. Verificado leyendo el código.
 3. Un pack multi-venta escribe el albarán en TODAS sus ventas (el carrito es un
    solo paquete físico). Decisión de Mario 2026-08-12.
 4. Sigue siendo sólo UPDATE: un número que no existe NO crea venta huérfana.
 5. Una fila con albarán vacío NO borra el albarán existente (re-subida parcial).
 6. Los números llegan de Excel como int/float: '2000014366425187.0' debe cruzar.

Uso:  python3 backend/scripts/test_albaran_pack_id.py
"""
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

# BD desechable ANTES de importar database
_tmpdir = tempfile.mkdtemp(prefix="test_albaran_")
os.environ["DATABASE_PATH"] = str(Path(_tmpdir) / "test.db")

import openpyxl  # noqa: E402

import database  # noqa: E402
from database import get_db, init_database  # noqa: E402
from services.parser_albaran import parse_albaran  # noqa: E402

fallos = []
pasados = 0


def check(cond, msg):
    global pasados
    if cond:
        pasados += 1
        print(f"  ✓ {msg}")
    else:
        fallos.append(msg)
        print(f"  ✗ {msg}")


def escribir_excel(filas, path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["# de venta", " # de albarán"])
    for v, a in filas:
        ws.append([v, a])
    wb.save(path)
    return Path(path)


def sembrar_ventas(ventas):
    """ventas: lista de (num_venta, pack_id)."""
    with get_db() as conn:
        for num, pack in ventas:
            conn.execute(
                "INSERT INTO ventas_ml (num_venta, pack_id, sku, titulo) VALUES (?,?,?,?)",
                (num, pack, f"SKU-{num}", f"Titulo {num}"),
            )


def albaran_de(num_venta):
    with get_db() as conn:
        r = conn.execute(
            "SELECT albaran FROM ventas_ml WHERE num_venta = ?", (num_venta,)
        ).fetchone()
    return r["albaran"] if r else None


def main():
    init_database()
    tmp = Path(_tmpdir)

    # ── Escenario base ────────────────────────────────────────────────────────
    # V1: venta simple, se captura por su propio order.id
    # V2: venta de carrito, Gaby captura el pack_id P2
    # V3/V4: dos ventas del MISMO carrito P34 (multi-venta)
    # V5: su num_venta es, a la vez, el pack_id de V6  <- la trampa de los 268
    sembrar_ventas([
        ("1001", None),
        ("1002", "P2"),
        ("1003", "P34"),
        ("1004", "P34"),
        ("2005", None),      # 2005 es order.id de esta venta...
        ("1006", "2005"),    # ...y pack_id de esta otra
    ])

    print("\n[1] Cruce en dos pasos: num_venta y pack_id")
    xl = escribir_excel([("1001", "A-100"), ("P2", "A-200")], tmp / "base.xlsx")
    res = parse_albaran(xl)
    check(res["por_num_venta"] == 1, f"1 cruce por num_venta (dio {res['por_num_venta']})")
    check(res["por_pack_id"] == 1, f"1 cruce por pack_id (dio {res['por_pack_id']})")
    check(res["no_encontrados"] == 0, f"0 no encontrados (dio {res['no_encontrados']})")
    check(albaran_de("1001") == "A-100", "la venta simple recibió su albarán")
    check(albaran_de("1002") == "A-200", "la venta del carrito recibió el albarán por pack_id")

    print("\n[2] TRAMPA 1: num_venta GANA sobre pack_id")
    # '2005' es order.id de V5 y pack_id de V6. Debe escribirse SÓLO en V5.
    xl = escribir_excel([("2005", "A-999")], tmp / "colision.xlsx")
    res = parse_albaran(xl)
    check(albaran_de("2005") == "A-999", "el albarán fue a la venta cuyo order.id coincide")
    check(albaran_de("1006") is None,
          "NO se escribió en la venta que sólo lo tiene como pack_id")
    check(res["por_num_venta"] == 1 and res["por_pack_id"] == 0,
          "se contabilizó como cruce por num_venta, no por pack")

    print("\n[3] TRAMPA 3: un pack multi-venta escribe en TODAS sus ventas")
    xl = escribir_excel([("P34", "A-777")], tmp / "multi.xlsx")
    res = parse_albaran(xl)
    check(albaran_de("1003") == "A-777" and albaran_de("1004") == "A-777",
          "las 2 ventas del carrito recibieron el albarán")
    check(res["por_pack_id"] == 1, "cuenta 1 fila del Excel cruzada...")
    check(res["ventas_actualizadas"] == 2, "...pero reporta 2 ventas actualizadas")

    print("\n[3.bis] El pack unifica: pisa un albarán distinto de una venta hermana")
    # Decisión de Mario 2026-08-12: el carrito es UN paquete físico, así que su nota de
    # entrega ampara todas sus ventas. Si una hermana traía otro albarán, el archivo de
    # Gaby (fuente de verdad) las unifica. Este assert existe para que un "arreglo" futuro
    # tipo `AND albaran IS NULL` no pase inadvertido: sería un cambio de decisión, no un fix.
    sembrar_ventas([("4001", "P40"), ("4002", "P40"), ("4003", "P40")])
    with get_db() as conn:
        conn.execute("UPDATE ventas_ml SET albaran = 'VIEJO-A1' WHERE num_venta = '4001'")
    xl = escribir_excel([("P40", "A-888")], tmp / "unifica.xlsx")
    res = parse_albaran(xl)
    check(albaran_de("4001") == "A-888",
          "la hermana que tenía otro albarán queda UNIFICADA (a propósito)")
    check(albaran_de("4002") == "A-888" and albaran_de("4003") == "A-888",
          "las otras 2 ventas del pack también lo reciben")
    check(res["ventas_actualizadas"] == 3,
          f"un pack de 3 reporta 3 ventas actualizadas (dio {res['ventas_actualizadas']})")
    check(res["actualizados"] == 1,
          "pero sólo 1 fila del Excel, para que los contadores no se confundan")

    print("\n[4] TRAMPA 4: sólo UPDATE, no crea ventas huérfanas")
    with get_db() as conn:
        antes = conn.execute("SELECT COUNT(*) c FROM ventas_ml").fetchone()["c"]
    xl = escribir_excel([("9999999999", "A-XXX")], tmp / "huerfano.xlsx")
    res = parse_albaran(xl)
    with get_db() as conn:
        despues = conn.execute("SELECT COUNT(*) c FROM ventas_ml").fetchone()["c"]
    check(antes == despues, f"no se creó fila nueva ({antes} -> {despues})")
    check(res["no_encontrados"] == 1, "se reporta como no encontrado")
    check(res["ejemplos_no_encontrados"] == ["9999999999"],
          "el número no encontrado se devuelve para que Gaby lo revise")

    print("\n[5] TRAMPA 5: albarán vacío no borra el existente")
    xl = escribir_excel([("1001", None), ("1002", "")], tmp / "vacios.xlsx")
    res = parse_albaran(xl)
    check(albaran_de("1001") == "A-100", "el albarán previo por num_venta sigue intacto")
    check(albaran_de("1002") == "A-200", "el albarán previo por pack_id sigue intacto")
    check(res["sin_albaran"] == 2, f"se contaron 2 filas sin albarán (dio {res['sin_albaran']})")

    print("\n[6] TRAMPA 6: números que Excel entrega como int/float")
    sembrar_ventas([("2000014366425187", None), ("3007", "2000014386551255")])
    xl = escribir_excel([
        (2000014366425187, 144009),          # int puro
        ("2000014386551255.0", "144010.0"),  # float con .0
    ], tmp / "tipos.xlsx")
    res = parse_albaran(xl)
    check(albaran_de("2000014366425187") == "144009",
          "un num_venta int cruza y el albarán se guarda sin '.0'")
    check(albaran_de("3007") == "144010",
          "un pack_id con '.0' cruza y el albarán se guarda limpio")

    print("\n[7] TRAMPA 2 (estática): no existe un OR entre num_venta y pack_id")
    # Se revisa el CÓDIGO EJECUTABLE, no los comentarios: el docstring menciona el
    # patrón prohibido a propósito, para advertir de él.
    import ast
    src = (BASE / "services" / "parser_albaran.py").read_text()
    arbol = ast.parse(src)
    # Los docstrings SÍ mencionan el patrón prohibido (para advertir de él), así que
    # se excluyen por identidad de nodo: son el primer statement de módulo/función.
    nodos_doc = set()
    for n in ast.walk(arbol):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            cuerpo = getattr(n, "body", None)
            if cuerpo and isinstance(cuerpo[0], ast.Expr) \
                    and isinstance(cuerpo[0].value, ast.Constant) \
                    and isinstance(cuerpo[0].value.value, str):
                nodos_doc.add(id(cuerpo[0].value))
    sql_real = " ".join(
        " ".join(n.value.split()).lower()
        for n in ast.walk(arbol)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and id(n) not in nodos_doc
    )
    check("or pack_id" not in sql_real and "or num_venta" not in sql_real,
          "ningún SQL ejecutable combina ambas llaves en un solo WHERE")
    check(sql_real.count("update ventas_ml set albaran") == 2,
          "hay exactamente 2 UPDATE separados (paso 1 y paso 2)")

    print("\n[7.bis] El Excel es entrada EXTERNA: un payload SQL no se ejecuta")
    xl = escribir_excel([
        ("1001'; UPDATE ventas_ml SET albaran='PWNED'; --", "A-EVIL"),
        ("1001", "'; DROP TABLE ventas_ml; --"),
    ], tmp / "injection.xlsx")
    res = parse_albaran(xl)
    with get_db() as conn:
        pwned = conn.execute(
            "SELECT COUNT(*) c FROM ventas_ml WHERE albaran = 'PWNED'"
        ).fetchone()["c"]
        viva = conn.execute("SELECT COUNT(*) c FROM ventas_ml").fetchone()["c"]
    check(pwned == 0, "el payload en el # de venta no ejecutó nada")
    check(viva > 0, "la tabla ventas_ml sigue existiendo")
    check(albaran_de("1001") == "'; DROP TABLE ventas_ml; --",
          "un payload en el albarán se guarda como TEXTO literal, no se ejecuta")
    # Dejar 1001 como estaba para no ensuciar los checks siguientes.
    with get_db() as conn:
        conn.execute("UPDATE ventas_ml SET albaran = 'A-100' WHERE num_venta = '1001'")

    print("\n[8] Idempotencia: re-subir el mismo archivo no rompe nada")
    xl = escribir_excel([("1001", "A-100"), ("P2", "A-200")], tmp / "repeat.xlsx")
    r1 = parse_albaran(xl)
    r2 = parse_albaran(xl)
    check(r1["actualizados"] == r2["actualizados"] == 2,
          "las dos corridas reportan lo mismo")
    check(albaran_de("1001") == "A-100" and albaran_de("1002") == "A-200",
          "los valores quedan iguales tras la segunda corrida")

    print("\n" + "=" * 60)
    total = pasados + len(fallos)
    if fallos:
        print(f"FALLOS: {len(fallos)}/{total}")
        for f in fallos:
            print(f"  - {f}")
        return 1
    print(f"TODO OK: {pasados}/{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
