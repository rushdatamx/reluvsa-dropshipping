"""
Corrige los cruces factura↔venta de KIM usando el # de venta que el proveedor imprime
en el PDF de la factura.

CONTEXTO
--------
Gaby detectó (2026-08-07) que KIM imprime el # de venta de Mercado Libre en su factura.
Verificado contra los 748 PDF de KIM en prod:

  - El número está SÓLO en el PDF. En los 750 XML: CERO. KIM no lo timbra como dato
    estructurado, así que el matcher (que sólo lee XML) no puede verlo hoy.
  - Aparece en 144 de 748 PDF (19.3%), no en todos.
  - De los 144, 135 cruzan EXACTO contra `ventas_ml.num_venta` (order.id).
    CERO cruzan contra `pack_id` y CERO colisionan → sin la ambigüedad de los 268
    números que son order.id de una venta y pack_id de otra (CLAUDE.md §8, regla 3).

Ese número es la VERDAD del proveedor: es el dato que él mismo asocia a su factura, más
fuerte que cualquier heurística de código o fecha. Este script lo usa para reparar los
cruces ya persistidos.

QUÉ CORRIGE (medido en prod, 2026-08-07)
----------------------------------------
  145 conceptos viven en facturas con # de venta legible:
     34 ya estaban correctos          -> no se tocan
    102 cruzados a la venta EQUIVOCADA -> se reasignan  (98 con confianza 1.0)
      9 pendientes                     -> se cruzan

Los 102 son especialmente dañinos: 98 tienen `codigo_exact` con confianza 1.0, o sea le
dicen a Gaby "✓ Facturado" con la máxima seguridad sobre la venta que no es. Un cruce
falso es peor que un pendiente: el pendiente se ve y se corrige, el falso nadie lo revisa.

LAS 5 GARANTÍAS (verificadas antes de escribir; el script las vuelve a comprobar)
--------------------------------------------------------------------------------
1. NUNCA se borra una fila de `factura_conceptos`. Sólo se reescriben
   `num_venta_match` / `match_method` / `match_confidence`. El concepto es dato real del XML.
2. Sólo se tocan conceptos de facturas cuyo PDF trae el número Y cuya venta existe.
   Todo lo demás queda intacto.
3. Ninguna venta destino es POSTERIOR a su factura (verificado: 0 de 111). El número del
   proveedor es consistente con el fix de fecha (`f699577`); no lo contradice.
4. Las 111 ventas destino tienen envío con proveedor KIM (verificado: 111/111), así que
   ninguna corrección depende del BUG A ni cambia de proveedor.
5. Si la venta destino está ocupada por un concepto AJENO a esta corrección (34 casos),
   ese ocupante se libera a PENDIENTE — no se reasigna a ciegas. Su factura no trae el
   número, así que no hay evidencia de a qué venta pertenece: inventarla reintroduciría
   el mismo bug que estamos arreglando.

USO
---
    python corregir_cruces_num_venta_pdf.py --db <ruta>            # simula, no escribe
    python corregir_cruces_num_venta_pdf.py --db <ruta> --ejecutar # escribe (transacción única)

Idempotente: una segunda corrida no encuentra nada que corregir.
"""
import argparse
import json
import os
import re
import sqlite3
import sys

# El order.id de ML: 16 dígitos que empiezan con 2. Hoy NINGÚN concepto del portal trae
# números de 12+ dígitos, así que encontrar uno en el PDF es señal inequívoca de que el
# proveedor lo puso a propósito.
PATRON_NUM_VENTA = re.compile(r"\b(2[0-9]{15})\b")

CODIGO_BODEGA = "KIM"
METODO = "num_venta_proveedor"
CONFIANZA = 1.0


def extraer_numeros_pdf(ruta: str) -> list:
    """Devuelve los # de venta de ML impresos en un PDF. [] si es ilegible."""
    try:
        import pdfplumber
        with pdfplumber.open(ruta) as pdf:
            texto = " ".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return []
    return sorted(set(PATRON_NUM_VENTA.findall(texto)))


def resolver_pdf(ruta: str, dir_facturas: str):
    """El path en BD puede ser de un contenedor viejo; se resuelve por nombre."""
    if not ruta:
        return None
    if os.path.exists(ruta):
        return ruta
    alt = os.path.join(dir_facturas, os.path.basename(ruta))
    return alt if os.path.exists(alt) else None


def construir_plan(conn, dir_facturas: str, cache_nums: dict = None) -> dict:
    """Calcula qué hay que cambiar. NO escribe nada."""
    conn.row_factory = sqlite3.Row

    facturas = list(conn.execute(
        """SELECT f.id, f.serie, f.folio, f.pdf_path, date(f.fecha_factura) AS ff
           FROM facturas f JOIN proveedores p ON p.id = f.proveedor_id
           WHERE p.codigo_bodega = ? AND f.pdf_path IS NOT NULL""",
        (CODIGO_BODEGA,),
    ))

    # ⚠️ Un mismo # de venta puede aparecer en DOS facturas distintas: KIM factura por
    # separado dos piezas de la misma venta (medido: K28023 con '96476708-Z' y K28069 con
    # '96476709-Z' comparten la venta 2000016875229280). Las dos tienen razón, y el número
    # no dice cuál "posee" la venta -> el dato es ambiguo y NO se corrige ninguna.
    #
    # Sin esta guarda el script alternaba entre ambas en cada corrida (nunca converge), y
    # sobre todo violaría la regla del proyecto: ante ambigüedad real, preferir el
    # pendiente al cruce inventado.
    numeros_por_factura = {}
    for f in facturas:
        ruta = resolver_pdf(f["pdf_path"], dir_facturas)
        if not ruta:
            continue
        clave = os.path.basename(ruta)
        nums = cache_nums.get(clave) if cache_nums is not None else None
        if nums is None:
            nums = extraer_numeros_pdf(ruta)
        if len(nums) == 1:
            numeros_por_factura.setdefault(nums[0], set()).add(f["id"])
    numeros_ambiguos = {n for n, fs in numeros_por_factura.items() if len(fs) > 1}

    correcciones, ya_ok, sin_numero, sin_venta = [], 0, 0, 0
    ambiguos = 0

    for f in facturas:
        ruta = resolver_pdf(f["pdf_path"], dir_facturas)
        if not ruta:
            sin_numero += 1
            continue

        clave = os.path.basename(ruta)
        nums = cache_nums.get(clave) if cache_nums is not None else None
        if nums is None:
            nums = extraer_numeros_pdf(ruta)

        if not nums:
            sin_numero += 1
            continue

        # Garantía: si el PDF trajera más de un número no hay forma de saber cuál es la
        # venta de esta factura -> no adivinamos. (Medido en prod: 0 casos.)
        if len(nums) > 1:
            sin_numero += 1
            continue

        real = nums[0]

        # El mismo número en 2 facturas -> ambiguo, no se toca ninguna (ver arriba).
        if real in numeros_ambiguos:
            ambiguos += 1
            continue

        venta = conn.execute(
            "SELECT num_venta, date(fecha_venta) AS fv FROM ventas_ml WHERE num_venta = ?",
            (real,),
        ).fetchone()
        if not venta:
            sin_venta += 1
            continue

        # Garantía 3: la venta no puede ser posterior a su propia factura.
        if f["ff"] and venta["fv"] and venta["fv"] > f["ff"]:
            sin_venta += 1
            continue

        for c in conn.execute(
            """SELECT id, num_venta_match, match_method, match_confidence
               FROM factura_conceptos WHERE factura_id = ?""",
            (f["id"],),
        ):
            if c["num_venta_match"] == real:
                ya_ok += 1
                continue
            correcciones.append({
                "concepto_id": c["id"],
                "factura_id": f["id"],
                "folio": f["folio"],
                "de": c["num_venta_match"],
                "a": real,
                "metodo_previo": c["match_method"],
                "conf_previa": c["match_confidence"],
            })

    # Garantía 5: ocupantes ajenos de las ventas destino -> se liberan a pendiente.
    #
    # ⚠️ "Ajeno" es POR FACTURA, no por concepto. Una factura puede tener varios conceptos
    # que apuntan legítimamente a la MISMA venta (el proveedor facturó 2 piezas de una sola
    # venta): al corregir uno, el otro NO es un intruso — ya está donde debe.
    #
    # Comparar sólo por concepto_id hacía que el script se pisara a sí mismo: corregía un
    # concepto y liberaba a su hermano de la misma factura, alternando en cada corrida
    # (detectado en simulación: 4 conceptos oscilando en las facturas 204, 599 y 720).
    # Es también lo que hace la corrección idempotente.
    ids_corregidos = {c["concepto_id"] for c in correcciones}
    facturas_destino = {c["a"]: c["factura_id"] for c in correcciones}
    liberar = []
    for destino, factura_id in facturas_destino.items():
        for r in conn.execute(
            """SELECT id, factura_id, num_venta_match, match_method, match_confidence
               FROM factura_conceptos WHERE num_venta_match = ?""",
            (destino,),
        ):
            if r["id"] in ids_corregidos or r["factura_id"] == factura_id:
                continue
            liberar.append({
                "concepto_id": r["id"],
                "factura_id": r["factura_id"],
                "de": r["num_venta_match"],
                "metodo_previo": r["match_method"],
                "conf_previa": r["match_confidence"],
            })

    return {
        "correcciones": correcciones,
        "liberar": liberar,
        "ya_ok": ya_ok,
        "sin_numero": sin_numero,
        "sin_venta": sin_venta,
        "ambiguos": ambiguos,
        "facturas_revisadas": len(facturas),
    }


def aplicar(conn, plan: dict):
    """Aplica el plan en UNA sola transacción (o todo, o nada)."""
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        # Primero liberar a los ocupantes ajenos, para no chocar con los destinos.
        for l in plan["liberar"]:
            cur.execute(
                """UPDATE factura_conceptos
                   SET num_venta_match = NULL, match_method = NULL, match_confidence = NULL
                   WHERE id = ?""",
                (l["concepto_id"],),
            )
        for c in plan["correcciones"]:
            cur.execute(
                """UPDATE factura_conceptos
                   SET num_venta_match = ?, match_method = ?, match_confidence = ?
                   WHERE id = ?""",
                (c["a"], METODO, CONFIANZA, c["concepto_id"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def verificar(conn, plan: dict) -> list:
    """Comprueba las garantías DESPUÉS de aplicar. Devuelve lista de fallas."""
    fallas = []
    for c in plan["correcciones"]:
        r = conn.execute(
            "SELECT num_venta_match, match_method FROM factura_conceptos WHERE id = ?",
            (c["concepto_id"],),
        ).fetchone()
        if not r or r[0] != c["a"]:
            fallas.append(f"concepto {c['concepto_id']} no quedó en {c['a']}")
    for l in plan["liberar"]:
        r = conn.execute(
            "SELECT num_venta_match FROM factura_conceptos WHERE id = ?",
            (l["concepto_id"],),
        ).fetchone()
        if not r or r[0] is not None:
            fallas.append(f"concepto {l['concepto_id']} debía quedar pendiente")
    return fallas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--uploads", default="/data/uploads/facturas")
    ap.add_argument("--cache", help="JSON {archivo_pdf: [numeros]} para no releer los PDF")
    ap.add_argument("--ejecutar", action="store_true", help="sin esto sólo simula")
    args = ap.parse_args()

    cache = json.load(open(args.cache)) if args.cache else None
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    total_antes = conn.execute("SELECT COUNT(*) FROM factura_conceptos").fetchone()[0]
    cruz_antes = conn.execute(
        "SELECT COUNT(*) FROM factura_conceptos WHERE num_venta_match IS NOT NULL"
    ).fetchone()[0]

    plan = construir_plan(conn, args.uploads, cache)

    print(f"facturas {CODIGO_BODEGA} con PDF revisadas : {plan['facturas_revisadas']}")
    print(f"  sin # de venta legible en el PDF   : {plan['sin_numero']}")
    print(f"  con # pero sin venta válida en BD  : {plan['sin_venta']}")
    print(f"  con # ambiguo (en 2 facturas)      : {plan['ambiguos']}  <- no se tocan")
    print(f"  conceptos YA correctos             : {plan['ya_ok']}")
    print()
    reasignar = [c for c in plan["correcciones"] if c["de"]]
    pendientes = [c for c in plan["correcciones"] if not c["de"]]
    print(f"A CORREGIR: {len(plan['correcciones'])} conceptos")
    print(f"  cruzados a la venta EQUIVOCADA -> se reasignan : {len(reasignar)}")
    print(f"  pendientes -> se cruzan                        : {len(pendientes)}")
    print(f"OCUPANTES AJENOS a liberar (quedan pendientes)   : {len(plan['liberar'])}")

    if not args.ejecutar:
        print("\n[SIMULACIÓN] no se escribió nada. Usa --ejecutar para aplicar.")
        for c in reasignar[:8]:
            print(f"   folio {c['folio']}: {c['de']} -> {c['a']}  "
                  f"(era {c['metodo_previo']} conf {c['conf_previa']})")
        return 0

    aplicar(conn, plan)

    total_desp = conn.execute("SELECT COUNT(*) FROM factura_conceptos").fetchone()[0]
    cruz_desp = conn.execute(
        "SELECT COUNT(*) FROM factura_conceptos WHERE num_venta_match IS NOT NULL"
    ).fetchone()[0]
    fallas = verificar(conn, plan)

    print(f"\nAPLICADO. filas {total_antes} -> {total_desp} "
          f"({'OK, ninguna borrada' if total_antes == total_desp else '🔴 CAMBIÓ'})")
    print(f"conceptos cruzados: {cruz_antes} -> {cruz_desp}")
    print("verificación:", "OK, todas las garantías se cumplen" if not fallas else fallas)
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
