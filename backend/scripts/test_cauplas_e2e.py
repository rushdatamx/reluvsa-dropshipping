"""Prueba E2E del cruce de la factura de CAUPLAS contra sus ventas (BD local fresca).

Reutiliza los parsers y el matcher REALES. No toca prod ni la BD del backend vivo.
Valida el match por ID interno: la factura trae '2692  M2626339' y la venta ML es
'CAU2692' → cruzan por codigo_id_interno. Las ventas CAUPLAS salen de ML como
'Agencia de Mercado Libre' sin proveedor, así que primero se reasignan (simula el
botón de Ventas.jsx) y luego se corre el matcher.

Uso:
    DATABASE_PATH=$(pwd)/../data/test_cauplas.db python3 scripts/test_cauplas_e2e.py
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
REPO = BACKEND.parent
PRUEBA = REPO / "prueba-junio"

CAUPLAS_XML = PRUEBA / "cfdi_timbrados_I_8075_CD_970091508.xml"

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

    with get_db() as conn:
        cau = conn.execute(
            "SELECT id, nombre, codigo_bodega FROM proveedores WHERE rfc = 'QHO180116NW0'"
        ).fetchone()
        prov_id = cau["id"]
        banner(f"Proveedor CAUPLAS: id={prov_id} {cau['nombre']} ({cau['codigo_bodega']})")

        # Reasignar TODOS los envíos de ventas CAU* a CAUPLAS (simula el botón de Ventas.jsx)
        sin_prov = conn.execute(
            """SELECT e.num_envio FROM envios_colecta e
               JOIN ventas_ml v ON v.num_venta = e.num_venta_ml
               WHERE v.sku LIKE 'CAU%' AND (e.proveedor_id IS NULL OR e.proveedor_id != ?)""",
            (prov_id,),
        ).fetchall()
        print(f"Reasignando {len(sin_prov)} envío(s) CAU* a CAUPLAS…")
        for row in sin_prov:
            conn.execute(
                "UPDATE envios_colecta SET lugar_override = ?, proveedor_id = ? WHERE num_envio = ?",
                ("CAUPLAS", prov_id, row["num_envio"]),
            )

    banner("3) Parsear el XML de CAUPLAS")
    parsed = parse_cfdi_xml(CAUPLAS_XML)
    print("UUID:", parsed["uuid_cfdi"], "| folio:", parsed.get("folio"))
    print("emisor:", parsed.get("rfc_emisor"), "| receptor:", parsed.get("rfc_receptor"))
    print("conceptos:", len(parsed["conceptos"]))

    banner("4) Correr matcher REAL sobre los 28 conceptos")
    matched = 0
    metodos = {}
    with get_db() as conn:
        for c in parsed["conceptos"]:
            m = match_conceptos_a_ventas(conn, prov_id, c)
            if m:
                matched += 1
                metodos[m["method"]] = metodos.get(m["method"], 0) + 1
                vt = conn.execute(
                    "SELECT sku FROM ventas_ml WHERE num_venta = ?", (m["num_venta"],)
                ).fetchone()
                print(f"  ✅ '{c.get('codigo')}' → venta {m['num_venta']} "
                      f"(sku {vt['sku']}) por {m['method']} conf {m['confidence']}")
            else:
                print(f"  ❌ '{c.get('codigo')}' SIN MATCH (pieza sin venta cruzable)")

    banner(f"RESULTADO: {matched}/{len(parsed['conceptos'])} conceptos cruzados | métodos: {metodos}")


if __name__ == "__main__":
    main()
