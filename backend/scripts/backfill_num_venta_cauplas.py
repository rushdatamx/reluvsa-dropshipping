"""Audita y, con --ejecutar, aplica el # de venta timbrado por CAUPLAS.

Por defecto es una simulación estrictamente de solo lectura. No llama a Mercado Libre.
"""
import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services.matcher import METODO_NUM_CAUPLAS, resolver_num_venta_cauplas
from services.parser_cfdi import parse_cfdi_xml


def _resolver_archivo(path_guardado, uploads):
    if not path_guardado:
        return None
    path = Path(path_guardado)
    if path.is_file():
        return path
    candidato = uploads / "facturas" / path.name
    return candidato if candidato.is_file() else None


def auditar(conn, uploads: Path, ejecutar=False):
    conn.row_factory = sqlite3.Row
    columnas = {c["name"] for c in conn.execute("PRAGMA table_info(factura_conceptos)")}
    requeridas = {"num_venta_proveedor", "cruce_numero_estado"}
    if not requeridas <= columnas:
        raise RuntimeError("Falta ejecutar la migración de columnas CAUPLAS antes del backfill")

    facturas = conn.execute(
        """SELECT f.id, f.folio, f.fecha_factura, f.xml_path, f.proveedor_id
           FROM facturas f JOIN proveedores p ON p.id=f.proveedor_id
           WHERE p.codigo_bodega='CAUPLAS' ORDER BY f.id"""
    ).fetchall()
    conteos = Counter()
    cambios = []
    for factura in facturas:
        xml = _resolver_archivo(factura["xml_path"], uploads)
        if not xml:
            conteos["conflicto"] += 1
            continue
        try:
            parsed = parse_cfdi_xml(xml)
        except Exception:
            conteos["conflicto"] += 1
            continue
        filas = conn.execute(
            "SELECT * FROM factura_conceptos WHERE factura_id=? ORDER BY id", (factura["id"],)
        ).fetchall()
        if len(filas) != len(parsed["conceptos"]):
            conteos["conflicto"] += len(filas) or 1
            continue

        for fila, concepto in zip(filas, parsed["conceptos"]):
            numero = concepto.get("num_venta_proveedor")
            estado = concepto.get("cruce_numero_estado")
            if not numero:
                conteos["sin_numero"] += 1
                continue
            if estado == "numero_invalido":
                categoria, resultado = "invalido", {"estado": estado}
            else:
                resultado = resolver_num_venta_cauplas(
                    conn, factura["proveedor_id"], numero, concepto.get("codigo") or "",
                    factura["fecha_factura"],
                )
                if resultado.get("num_venta") == fila["num_venta_match"]:
                    categoria = "ya_correcto"
                elif resultado.get("num_venta"):
                    categoria = "corregible"
                elif resultado["estado"] == "numero_ambiguo":
                    categoria = "ambiguo"
                else:
                    categoria = "conflicto"
            conteos[categoria] += 1
            destino = resultado.get("num_venta")
            if categoria == "corregible":
                cambios.append((factura["folio"], fila["id"], fila["num_venta_match"], destino))
            if ejecutar:
                # El conjunto con evidencia incluye válidos e inválidos; los conflictos
                # se documentan, pero jamás se asignan automáticamente.
                params = [concepto.get("codigo"), numero, resultado["estado"]]
                sql = ("UPDATE factura_conceptos SET codigo_prov=?, num_venta_proveedor=?, "
                       "cruce_numero_estado=?")
                if resultado.get("num_venta"):
                    sql += ", num_venta_match=?, match_method=?, match_confidence=?"
                    params += [destino, METODO_NUM_CAUPLAS, 1.0]
                sql += " WHERE id=?"
                params.append(fila["id"])
                conn.execute(sql, params)
    if ejecutar:
        conn.commit()
    return conteos, cambios


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--uploads", type=Path)
    parser.add_argument("--ejecutar", action="store_true")
    args = parser.parse_args()
    uploads = args.uploads or args.db.parent / "uploads"
    conn = sqlite3.connect(str(args.db))
    try:
        conteos, cambios = auditar(conn, uploads, args.ejecutar)
        print("MODO:", "EJECUCIÓN" if args.ejecutar else "SIMULACIÓN (sin escrituras)")
        for clave in ("ya_correcto", "corregible", "invalido", "ambiguo", "conflicto", "sin_numero"):
            print(f"{clave}: {conteos[clave]}")
        for folio, concepto_id, anterior, nuevo in cambios:
            print(f"factura={folio} concepto={concepto_id}: {anterior or 'pendiente'} -> {nuevo}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
