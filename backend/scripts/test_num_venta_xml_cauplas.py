"""Regresión del paso 0 CAUPLAS por NoIdentificacion (sin red)."""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
tmp = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(tmp.name) / "test.db")

import database
from services.matcher import match_conceptos_a_ventas, resolver_num_venta_cauplas
from services.parser_cfdi import parse_cfdi_xml, separar_no_identificacion_cauplas
from scripts.backfill_num_venta_cauplas import auditar


def venta(conn, numero, sku, fecha="2026-08-20T10:00:00", pack=None, deposito=None):
    conn.execute(
        "INSERT INTO ventas_ml(num_venta,sku,fecha_venta,titulo,pack_id,deposito) VALUES(?,?,?,?,?,?)",
        (numero, sku, fecha, sku, pack, deposito),
    )


def main():
    database.init_database()
    xml = Path(tmp.name) / "970097327.xml"
    conceptos = [f'<cfdi:Concepto NoIdentificacion="CAU{i} 200000000000{i:04d}" '
                 f'Descripcion="Pieza {i}" Cantidad="1" ValorUnitario="1" Importe="1" '
                 f'ClaveProdServ="1" ClaveUnidad="H87"/>' for i in range(1, 19)]
    conceptos.append('<cfdi:Concepto NoIdentificacion="CAU99 200018024092132" '
                     'Descripcion="Inválido" Cantidad="1" ValorUnitario="1" Importe="1" '
                     'ClaveProdServ="1" ClaveUnidad="H87"/>')
    xml.write_text(
        '<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" Fecha="2026-08-21T10:00:00" Total="19">'
        '<cfdi:Emisor Rfc="QHO180116NW0"/><cfdi:Receptor Rfc="GPE230915JWA"/>'
        '<cfdi:Conceptos>' + ''.join(conceptos) + '</cfdi:Conceptos></cfdi:Comprobante>',
        encoding="utf-8",
    )
    parsed = parse_cfdi_xml(xml)
    assert len(parsed["conceptos"]) == 19
    assert sum(c["cruce_numero_estado"] == "numero_valido" for c in parsed["conceptos"]) == 18
    assert parsed["conceptos"][-1]["num_venta_proveedor"] == "200018024092132"
    assert parsed["conceptos"][-1]["cruce_numero_estado"] == "numero_invalido"
    for numero in ("20001802409213", "200018024092132", "20000180240921327"):
        separado = separar_no_identificacion_cauplas(f"CAU99 {numero}")
        assert separado["num_venta_proveedor"] == numero
        assert separado["cruce_numero_estado"] == "numero_invalido"

    with database.get_db() as conn:
        cau = conn.execute("SELECT id FROM proveedores WHERE codigo_bodega='CAUPLAS'").fetchone()[0]
        kim = conn.execute("SELECT id FROM proveedores WHERE codigo_bodega='KIM'").fetchone()[0]
        venta(conn, "2000000000000001", "CAU100")
        venta(conn, "2000000000000002", "CAU200", pack="2000000000000099")
        venta(conn, "2000000000000003", "CAU300", pack="2000000000000099")
        # Colisión: este order.id también es pack_id de otra venta; order.id debe ganar.
        venta(conn, "2000000000000099", "CAU999")
        venta(conn, "2000000000000004", "CAU400", pack="2000000000000099")
        venta(conn, "2000000000000005", "CAU500", deposito="KIM")
        venta(conn, "2000000000000006", "CAU600", fecha="2026-08-22T10:00:00")
        conn.execute("INSERT INTO kit_componentes(kit_sku,componente_codigo,cantidad) VALUES('KIT1','CAU7000',1)")
        conn.execute("INSERT INTO kit_componentes(kit_sku,componente_codigo,cantidad) VALUES('KIT1','CAU7001',1)")
        venta(conn, "2000000000000007", "KIT1")
        venta(conn, "2000000000000008", "CAU800", pack="2000000000000077")
        venta(conn, "2000000000000009", "CAU800", pack="2000000000000077")
        venta(conn, "2000000000000011", None)

        ctx = {"codigo_bodega": "CAUPLAS"}
        def cruzar(num, codigo, estado="numero_valido"):
            c = {"codigo": codigo, "descripcion": codigo,
                 "num_venta_proveedor": num, "cruce_numero_estado": estado}
            return match_conceptos_a_ventas(conn, cau, c, "2026-08-21T12:00:00", ctx), c

        m, _ = cruzar("2000000000000001", "CAU100")
        assert m["num_venta"] == "2000000000000001" and m["confidence"] == 1.0
        # Dos conceptos pueden compartir la venta explícita.
        assert cruzar("2000000000000001", "CAU100")[0]["num_venta"] == m["num_venta"]
        assert cruzar("2000000000000099", "CAU999")[0]["num_venta"] == "2000000000000099"
        assert cruzar("2000000000000099", "CAU400")[1]["cruce_numero_estado"] == "conflicto_pieza"
        assert cruzar("2000000000000007", "CAU7000")[0]["num_venta"] == "2000000000000007"
        assert cruzar("2000000000000007", "CAU7001")[0]["num_venta"] == "2000000000000007"
        assert cruzar("2000000000000005", "CAU500")[1]["cruce_numero_estado"] == "conflicto_proveedor"
        assert cruzar("2000000000000006", "CAU600")[1]["cruce_numero_estado"] == "conflicto_fecha"
        assert cruzar("2000000000000888", "CAU100")[1]["cruce_numero_estado"] == "numero_no_resuelve"
        assert cruzar("2000000000000011", "CAU100")[1]["cruce_numero_estado"] == "conflicto_pieza"
        assert resolver_num_venta_cauplas(conn, cau, "2000000000000077", "CAU800",
                                          "2026-08-21")["estado"] == "numero_ambiguo"
        assert cruzar("200018024092132", "CAU100", "numero_invalido")[0] is None
        # Sin número conserva el matcher legacy.
        conn.execute("INSERT INTO envios_colecta(num_envio,num_venta_ml,proveedor_id) VALUES('E1','2000000000000001',?)", (cau,))
        legacy = match_conceptos_a_ventas(conn, cau, {"codigo": "CAU100", "descripcion": ""},
                                          "2026-08-21T12:00:00", ctx)
        assert legacy and legacy["num_venta"] == "2000000000000001"

        # Evidencia explícita de proveedor en envío bloquea aun si depósito es desconocido.
        conn.execute("INSERT INTO envios_colecta(num_envio,num_venta_ml,proveedor_id) VALUES('E5','2000000000000001',?)", (kim,))
        assert resolver_num_venta_cauplas(conn, cau, "2000000000000001", "CAU100",
                                          "2026-08-21")["estado"] == "conflicto_proveedor"

        # Backfill: simulación no escribe; ejecución converge y la segunda no corrige nada.
        venta(conn, "2000000000000010", "CAU1000")
        backfill_xml = Path(tmp.name) / "backfill.xml"
        backfill_xml.write_text(
            '<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" Fecha="2026-08-21T10:00:00" Total="1">'
            '<cfdi:Emisor Rfc="QHO180116NW0"/><cfdi:Receptor Rfc="GPE230915JWA"/>'
            '<cfdi:Conceptos><cfdi:Concepto NoIdentificacion="CAU1000 2000000000000010" '
            'Descripcion="Pieza" Cantidad="1" ValorUnitario="1" Importe="1"/></cfdi:Conceptos>'
            '</cfdi:Comprobante>', encoding="utf-8")
        fac_id = conn.execute(
            "INSERT INTO facturas(proveedor_id,folio,fecha_factura,xml_path) VALUES(?,?,?,?)",
            (cau, "BACKFILL", "2026-08-21T10:00:00", str(backfill_xml)),
        ).lastrowid
        concepto_id = conn.execute(
            "INSERT INTO factura_conceptos(factura_id,codigo_prov,descripcion) VALUES(?,?,?)",
            (fac_id, "CAU1000 2000000000000010", "Pieza"),
        ).lastrowid
        conteos, cambios = auditar(conn, Path(tmp.name), ejecutar=False)
        assert conteos["corregible"] == 1 and len(cambios) == 1
        assert conn.execute("SELECT num_venta_match FROM factura_conceptos WHERE id=?",
                            (concepto_id,)).fetchone()[0] is None
        auditar(conn, Path(tmp.name), ejecutar=True)
        conteos2, cambios2 = auditar(conn, Path(tmp.name), ejecutar=True)
        assert conteos2["ya_correcto"] == 1 and cambios2 == []
    print("test_num_venta_xml_cauplas: 19/19 ✅")


if __name__ == "__main__":
    main()
