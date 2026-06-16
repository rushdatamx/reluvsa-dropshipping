"""
Formato del «# de factura» tal como cada proveedor lo muestra en su PDF.

El portal NO lee el PDF: extrae Serie y Folio del XML (CFDI) y los recombina aquí
según cómo cada proveedor presenta su número de factura. Reglas confirmadas por
Gaby (2026-06-16). Verificadas con XML reales: KIM, CAUPLAS, KG. Deducidas por
patrón (pendiente verificar con su primer XML real): AG, Vazlo.

Llave = codigo_bodega del proveedor (estable; ver seed en database.py).

  KIM      Serie+Folio          'K' + '26804'        -> 'K26804'
  CAUPLAS  Folio + ' ' + Serie  '970091508' + 'CD'   -> '970091508 CD'   (orden invertido)
  KG       Serie + ' ' + Folio  'S' + '464516'       -> 'S 464516'
  AG       Folio                '1000030...'         -> '1000030...'      (deducido)
  VAZLO    Serie+Folio          'FVC' + '02755'      -> 'FVC02755'        (deducido)
"""


def formatear_folio(codigo_bodega, serie, folio):
    """Devuelve el # de factura como lo ve el proveedor en su PDF.

    Robusto a None/espacios. Si no hay regla para el codigo_bodega, cae al
    formato genérico Serie+Folio.
    """
    s = (serie or "").strip()
    f = (folio or "").strip()
    cod = (codigo_bodega or "").strip().upper()

    if not s and not f:
        return ""

    if cod == "CAUPLAS":
        # El proveedor muestra el folio primero y la serie después.
        return f"{f} {s}".strip()
    if cod == "KG":
        return f"{s} {f}".strip()
    if cod == "AG":
        # AG presenta solo el folio.
        return f or s
    # KIM, VAZLO y fallback genérico: serie pegada al folio.
    return f"{s}{f}"
