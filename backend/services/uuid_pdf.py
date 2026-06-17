"""
Extrae el UUID (folio fiscal) impreso dentro de un PDF de CFDI.

Todo CFDI timbrado lleva su UUID impreso en el PDF (requisito del SAT, junto al
sello digital). Lo usamos para emparejar un PDF con su XML cuando el proveedor sube
varios archivos juntos y los nombres no coinciden (p. ej. KG: 'Documento PDF.pdf' vs
'Texto XML.xml'). El XML sigue siendo la fuente de verdad; esto solo ata el documento
legible a la factura correcta.

Si el PDF es una imagen escaneada (sin capa de texto) no habrá UUID extraíble →
devuelve None y el emparejado cae al fallback por nombre de archivo.
"""
import re
from pathlib import Path
from typing import Optional

import pdfplumber

# UUID del SAT: 8-4-4-4-12 hex. Tolerante a mayúsculas/minúsculas.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def extraer_uuid_de_pdf(path: Path) -> Optional[str]:
    """Devuelve el UUID en MAYÚSCULAS hallado en el texto del PDF, o None.

    No revienta nunca: si el PDF es ilegible/imagen/corrupto, devuelve None para
    que el llamador caiga al fallback por nombre.
    """
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                texto = page.extract_text() or ""
                m = _UUID_RE.search(texto)
                if m:
                    return m.group(0).upper()
    except Exception:
        return None
    return None
