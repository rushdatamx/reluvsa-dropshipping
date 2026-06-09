"""Prueba E2E del cruce de las facturas de AG y KG contra sus ventas.

Cada carpeta prueba-junio/{AG,KG}/ trae su propio par de reportes (Ventas MX + Colecta).
Reutiliza parsers + matcher REALES. BD aislada, no toca prod ni la BD local del 3-jun.

- KG: tiene XML real -> se parsea con parse_cfdi_xml.
- AG: solo PDF -> se arma el concepto a mano con los datos extraídos del PDF
       (mismo formato que produce parse_cfdi_xml), para poder probar el cruce.

Uso:
    DATABASE_PATH=$(pwd)/../data/test_agkg.db python3 scripts/test_ag_kg_e2e.py
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
REPO = BACKEND.parent
PRUEBA = REPO / "prueba-junio"

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


# Qué archivo es ventas y cuál colecta en cada carpeta (el "2" está invertido)
CASOS = {
    "AG": {
        "rfc": "ARG041025AU2",
        "ventas": "Hoja de cálculo Office Open XML 2.xlsx",
        "colecta": "Hoja de cálculo Office Open XML.xlsx",
        "xml": None,  # no hay XML, concepto desde el PDF
        "conceptos": [
            {"codigo": "P2172292",
             "descripcion": "Pierna Del Ram 1500 09-10 / Ram 1500 11-18 / Ram 1500 Classic 19-24",
             "cantidad": 2.0, "precio_unitario": 1769.0, "importe": 3538.0},
        ],
        "busca_sku": "P2172292",
    },
    "KG": {
        "rfc": "STR910211DT2",  # RFC real de KeepOnGreen descubierto hoy
        "ventas": "Hoja de cálculo Office Open XML.xlsx",
        "colecta": "Hoja de cálculo Office Open XML 2.xlsx",
        "xml": "Texto XML.xml",
        "conceptos": None,  # se llena del XML
        "busca_sku": "KR-1095WP",
    },
}


def main():
    banner(f"BD de prueba: {database.DATABASE_PATH}")
    dbp = Path(database.DATABASE_PATH)
    for p in [dbp, Path(str(dbp) + "-shm"), Path(str(dbp) + "-wal")]:
        p.unlink(missing_ok=True)
    init_database()

    # Parchar el RFC de KG en la BD (en el seed está 'PENDIENTE')
    with get_db() as conn:
        conn.execute("UPDATE proveedores SET rfc = ? WHERE codigo_bodega = 'KG'", ("STR910211DT2",))
        print("RFC de KG actualizado a STR910211DT2 en la BD de prueba")

    for cod, caso in CASOS.items():
        base = PRUEBA / cod
        banner(f"PROVEEDOR {cod}")

        print(f"1) Cargar ventas: {caso['ventas']}")
        print("  ", parse_ventas_ml(base / caso["ventas"]))
        print(f"2) Cargar colecta: {caso['colecta']}")
        print("  ", parse_colecta(base / caso["colecta"]))

        with get_db() as conn:
            prov = conn.execute(
                "SELECT id, nombre, codigo_bodega FROM proveedores WHERE rfc = ?",
                (caso["rfc"],),
            ).fetchone()
            if not prov:
                print(f"  !! No hay proveedor con RFC {caso['rfc']}")
                continue
            prov_id = prov["id"]
            print(f"\nProveedor: id={prov_id} {prov['nombre']} ({prov['codigo_bodega']})")

            # Venta objetivo
            venta = conn.execute(
                "SELECT num_venta, sku, titulo FROM ventas_ml WHERE sku LIKE ?",
                (f"%{caso['busca_sku']}%",),
            ).fetchall()
            print("Ventas con código de la factura:")
            for v in venta:
                print("  ", v["num_venta"], "| sku:", repr(v["sku"]), "|", v["titulo"][:45])

            # Envío de esa venta
            for v in venta:
                e = conn.execute(
                    "SELECT num_envio, proveedor_id, lugar_real, lugar_override FROM envios_colecta WHERE num_venta_ml = ?",
                    (v["num_venta"],),
                ).fetchall()
                for row in e:
                    print(f"   envío {row['num_envio']} | prov_id={row['proveedor_id']} | lugar_real={row['lugar_real']} | override={row['lugar_override']}")

        # Conceptos: del XML o los hardcodeados del PDF
        if caso["xml"]:
            parsed = parse_cfdi_xml(base / caso["xml"])
            conceptos = parsed["conceptos"]
            print(f"\nXML: UUID {parsed['uuid_cfdi']} | emisor {parsed.get('rfc_emisor')} | total {parsed.get('total')}")
        else:
            conceptos = caso["conceptos"]
            print(f"\n(AG sin XML — concepto extraído del PDF)")

        def correr(etapa):
            print(f"\n>> matcher ({etapa}):")
            with get_db() as conn:
                for c in conceptos:
                    m = match_conceptos_a_ventas(conn, prov_id, c)
                    if m:
                        vt = conn.execute("SELECT sku, titulo FROM ventas_ml WHERE num_venta = ?", (m["num_venta"],)).fetchone()
                        print(f"   ✅ '{c['codigo']}' → venta {m['num_venta']} por {m['method']} conf {m['confidence']}")
                        print(f"      venta sku={vt['sku']} | {vt['titulo'][:45]}")
                    else:
                        print(f"   ❌ '{c['codigo']}' SIN MATCH")

        correr("tal cual quedó el envío")

        # Reasignar el envío al proveedor si salió sin él, y reintentar
        with get_db() as conn:
            sin = conn.execute(
                """SELECT e.num_envio FROM envios_colecta e
                   JOIN ventas_ml v ON v.num_venta = e.num_venta_ml
                   WHERE v.sku LIKE ? AND (e.proveedor_id IS NULL OR e.proveedor_id != ?)""",
                (f"%{caso['busca_sku']}%", prov_id),
            ).fetchall()
            for row in sin:
                conn.execute(
                    "UPDATE envios_colecta SET lugar_override = ?, proveedor_id = ? WHERE num_envio = ?",
                    (caso["rfc"], prov_id, row["num_envio"]),
                )
            if sin:
                print(f"\n   (reasignados {len(sin)} envío(s) a {cod})")
        if sin:
            correr("tras reasignar envío")


if __name__ == "__main__":
    main()
