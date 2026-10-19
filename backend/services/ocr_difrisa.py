"""Lectura OCR local y acotada de CFDI PDF de DIFIRSA.

No hace llamadas de red. El UUID se usa para emparejar PDF/XML y las referencias
solo se guardan como propuestas para revisión humana.
"""
import re
from pathlib import Path
from typing import Optional

import pdfplumber

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# En el ejemplo DIFIRSA imprime los 11 dígitos finales del order.id, que empieza
# con 20000. Las referencias se separan por '/' en una línea del encabezado.
REF_RE = re.compile(r"(?<!\d)(?:20000)?(18\d{9})(?!\d)")


def leer_pdf_difrisa(path: Path) -> dict:
    """Devuelve texto y referencias del PDF. Falla de forma explícita si OCR falta."""
    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError("El OCR local de DIFIRSA no está instalado") from exc

    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("El PDF excede el límite de 20 MB para OCR")
    paginas = []
    confianzas = []
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) > 5:
            raise ValueError("El PDF excede el límite de 5 páginas para OCR")
        for page in pdf.pages:
            if page.width > 1440 or page.height > 1440:
                raise ValueError("El PDF contiene una página demasiado grande para OCR")
            texto = page.extract_text() or ""
            if len(texto.strip()) < 40:
                imagen = page.to_image(resolution=300).original
                try:
                    texto = pytesseract.image_to_string(imagen, lang="spa+eng", config="--psm 6", timeout=30)
                except Exception:
                    texto = pytesseract.image_to_string(imagen, lang="eng", config="--psm 6", timeout=30)
                try:
                    datos = pytesseract.image_to_data(
                        imagen, lang="spa+eng", config="--psm 6",
                        output_type=pytesseract.Output.DICT,
                        timeout=30,
                    )
                    confianzas.extend(float(c) for c, t in zip(datos["conf"], datos["text"])
                                      if t.strip() and float(c) >= 0)
                except Exception:
                    pass
            paginas.append(texto)

    texto = "\n".join(paginas)
    referencias = []
    vistos = set()
    for match in REF_RE.finditer(texto):
        original = match.group(0)
        sufijo = match.group(1)
        if sufijo in vistos:
            continue
        vistos.add(sufijo)
        referencias.append({
            "valor_original": original,
            "num_venta_origen": "20000" + sufijo,
            "confianza": (round(sum(confianzas) / len(confianzas) / 100, 3)
                          if confianzas else None),
        })

    uuid = UUID_RE.search(texto)
    return {
        "texto": texto,
        "uuid": uuid.group(0).upper() if uuid else None,
        "referencias": referencias,
        "estado": "leido" if texto.strip() else "sin_texto",
    }


def es_factura_difrisa(conn, proveedor_id: Optional[int]) -> bool:
    if proveedor_id is None:
        return False
    row = conn.execute(
        "SELECT codigo_bodega FROM proveedores WHERE id=?", (proveedor_id,)
    ).fetchone()
    return bool(row and (row["codigo_bodega"] or "").strip().upper() == "DIFIRSA")
