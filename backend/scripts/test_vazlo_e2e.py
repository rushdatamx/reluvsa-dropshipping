"""Prueba E2E del cruce de la factura de Vazlo contra sus ventas (BD local fresca).

Reutiliza los parsers y el matcher REALES. No toca prod ni la BD local del 3-jun.
Apuntar a una BD aislada con DATABASE_PATH antes de importar database.

Uso:
    DATABASE_PATH=$(pwd)/../data/test_vazlo.db python3 scripts/test_vazlo_e2e.py
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
REPO = BACKEND.parent
PRUEBA = REPO / "prueba-junio"

# XML+PDF de Vazlo que dejó Mario (en caches de Drag de macOS)
VAZLO_XML = Path(
    "/Users/jmariopgarcia/Library/Caches/com.apple.SwiftUI.Drag-C3E7028B-6B91-44C5-99E2-347B36C7337D/VIM990605M8A_FMX0069127.xml"
)

import database
from database import get_db, init_database
from services.parser_ventas_ml import parse_ventas_ml
from services.parser_colecta import parse_colecta
from services.parser_cfdi import parse_cfdi_xml
from services.matcher import match_conceptos_a_ventas


def banner(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def main():
    banner(f"BD de prueba: {database.DATABASE_PATH}")
    # Empezar limpio
    dbp = Path(database.DATABASE_PATH)
    for p in [dbp, Path(str(dbp) + "-shm"), Path(str(dbp) + "-wal")]:
        p.unlink(missing_ok=True)
    init_database()

    banner("1) Cargar Ventas ML")
    ventas_xlsx = next(PRUEBA.glob("*Ventas*Mercado*.xlsx"))
    print("archivo:", ventas_xlsx.name)
    print(parse_ventas_ml(ventas_xlsx))

    banner("2) Cargar Colecta")
    colecta_xlsx = next(PRUEBA.glob("*colecta*.xlsx"))
    print("archivo:", colecta_xlsx.name)
    print(parse_colecta(colecta_xlsx))

    # Identificar proveedor Vazlo y la venta de la horquilla
    with get_db() as conn:
        vaz = conn.execute(
            "SELECT id, nombre, codigo_bodega FROM proveedores WHERE rfc = 'VIM990605M8A'"
        ).fetchone()
        prov_id = vaz["id"]
        banner(f"Proveedor Vazlo: id={prov_id} {vaz['nombre']} ({vaz['codigo_bodega']})")

        # ¿Dónde está la venta 30-578?
        venta = conn.execute(
            "SELECT num_venta, sku, titulo FROM ventas_ml WHERE sku LIKE '%30-578%'"
        ).fetchall()
        print("\nVentas con SKU 30-578:")
        for v in venta:
            print(" ", v["num_venta"], "|", v["sku"], "|", v["titulo"][:50])

        # ¿Su envío tiene proveedor asignado?
        for v in venta:
            e = conn.execute(
                """SELECT num_envio, proveedor_id, lugar_real, lugar_override
                   FROM envios_colecta WHERE num_venta_ml = ?""",
                (v["num_venta"],),
            ).fetchall()
            print(f"\nEnvíos de la venta {v['num_venta']}:")
            for row in e:
                print("  envio", row["num_envio"], "| prov_id =", row["proveedor_id"],
                      "| lugar_real =", row["lugar_real"], "| override =", row["lugar_override"])

    banner("3) Parsear el XML de Vazlo")
    if not VAZLO_XML.exists():
        print("!! No encuentro el XML en:", VAZLO_XML)
        return
    parsed = parse_cfdi_xml(VAZLO_XML)
    print("UUID:", parsed["uuid_cfdi"], "| serie/folio:", parsed.get("serie"), parsed.get("folio"))
    print("emisor:", parsed.get("rfc_emisor"), "| receptor:", parsed.get("rfc_receptor"))
    print("total:", parsed.get("total"))
    print("conceptos:")
    for c in parsed["conceptos"]:
        print("  cod:", repr(c.get("codigo")), "| desc:", c.get("descripcion"))

    def correr_match(etapa):
        banner(f"4) Correr matcher REAL ({etapa})")
        with get_db() as conn:
            for c in parsed["conceptos"]:
                m = match_conceptos_a_ventas(conn, prov_id, c)
                if m:
                    vt = conn.execute(
                        "SELECT sku, titulo FROM ventas_ml WHERE num_venta = ?",
                        (m["num_venta"],),
                    ).fetchone()
                    print(f"  ✅ '{c.get('codigo')}' → venta {m['num_venta']} "
                          f"por {m['method']} conf {m['confidence']}")
                    print(f"     venta sku={vt['sku']} | {vt['titulo'][:50]}")
                else:
                    print(f"  ❌ '{c.get('codigo')}' SIN MATCH")

    correr_match("envío tal cual quedó")

    # Si el envío de Vazlo no tiene proveedor, reasignarlo (como el botón de Ventas.jsx) y reintentar
    with get_db() as conn:
        sin_prov = conn.execute(
            """SELECT e.num_envio FROM envios_colecta e
               JOIN ventas_ml v ON v.num_venta = e.num_venta_ml
               WHERE v.sku LIKE '%30-578%' AND (e.proveedor_id IS NULL OR e.proveedor_id != ?)""",
            (prov_id,),
        ).fetchall()
        if sin_prov:
            banner(f"Reasignando {len(sin_prov)} envío(s) de la horquilla a Vazlo (simula el botón)")
            for row in sin_prov:
                conn.execute(
                    "UPDATE envios_colecta SET lugar_override = ?, proveedor_id = ? WHERE num_envio = ?",
                    ("VAZLO", prov_id, row["num_envio"]),
                )
                print("  reasignado envío", row["num_envio"])

    if sin_prov:
        correr_match("tras reasignar el envío a Vazlo")


if __name__ == "__main__":
    main()
