"""
Parser de CFDI 4.0 (XML) emitido por proveedores.

Extrae datos del comprobante, emisor, receptor, conceptos y UUID del TimbreFiscalDigital.
"""
from datetime import datetime
from pathlib import Path
from typing import Optional

from lxml import etree

NS = {
    "cfdi": "http://www.sat.gob.mx/cfd/4",
    "cfdi3": "http://www.sat.gob.mx/cfd/3",
    "tfd": "http://www.sat.gob.mx/TimbreFiscalDigital",
}


def _attr(node, *names) -> Optional[str]:
    if node is None:
        return None
    for n in names:
        v = node.get(n)
        if v is not None:
            return v
    return None


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def parse_cfdi_xml(path: Path) -> dict:
    tree = etree.parse(str(path))
    root = tree.getroot()

    # Soporta CFDI 4.0 y 3.3
    ns_uri = root.tag.split("}", 1)[0].lstrip("{") if "}" in root.tag else ""
    ns = {"cfdi": ns_uri} if ns_uri else NS

    emisor = root.find("cfdi:Emisor", ns)
    receptor = root.find("cfdi:Receptor", ns)
    conceptos_node = root.find("cfdi:Conceptos", ns)

    timbre = root.find(".//tfd:TimbreFiscalDigital", NS)
    uuid = _attr(timbre, "UUID")

    conceptos = []
    if conceptos_node is not None:
        for c in conceptos_node.findall("cfdi:Concepto", ns):
            conceptos.append({
                "codigo": _attr(c, "NoIdentificacion"),
                "descripcion": _attr(c, "Descripcion"),
                "cantidad": float(_attr(c, "Cantidad") or 0),
                "precio_unitario": float(_attr(c, "ValorUnitario") or 0),
                "importe": float(_attr(c, "Importe") or 0),
                "clave_prod_serv": _attr(c, "ClaveProdServ"),
                "clave_unidad": _attr(c, "ClaveUnidad"),
            })

    return {
        "uuid_cfdi": uuid,
        "serie": _attr(root, "Serie"),
        "folio": _attr(root, "Folio"),
        "fecha": _parse_dt(_attr(root, "Fecha")),
        "total": float(_attr(root, "Total") or 0),
        "moneda": _attr(root, "Moneda"),
        "rfc_emisor": _attr(emisor, "Rfc"),
        "nombre_emisor": _attr(emisor, "Nombre"),
        "rfc_receptor": _attr(receptor, "Rfc"),
        "nombre_receptor": _attr(receptor, "Nombre"),
        "conceptos": conceptos,
    }
