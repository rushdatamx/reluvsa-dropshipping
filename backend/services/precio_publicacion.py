"""
Cálculo del precio de venta en Mercado Libre a partir del costo del proveedor.

La fórmula que describió Gaby (2026-08-19):
  *"el costo es gran mayoreo pero eso yo lo tendría que multiplicar por 1.16
  (iva), sumarle el costo de envío que varía entre 80-150 dependiendo de que tan
  grande es, el costo de la publicación que siempre es el 13% del precio final,
  y pues mi utilidad que pensaría en poner un porcentaje de 50%"*

🔴 EL 13% SE DIVIDE, NO SE SUMA. Es la trampa de este cálculo. La comisión de ML
se cobra sobre el precio FINAL, así que sumar 13% al costo deja corta la
utilidad: el 13% de lo que se cobra es más que el 13% de la base. Hay que
despejar el precio:

    base   = costo × (1 + iva) × (1 + utilidad)
    precio = (base + envio) / (1 - comision)

Medido con KGP-1449 (costo 346.84, envío 100):
    sumando  13%: $786 -> ML cobra $102 -> la utilidad queda $14 CORTA
    dividiendo   : $801 -> ML cobra $104 -> los 50% completos ✅

Son ~$15 por pieza; sobre 3,607 publicaciones no es un redondeo.

⬜ PENDIENTE — EL COSTO DE ENVÍO (decisión de Mario, 2026-08-19: avanzar sin él).
   `envio` entra como parámetro y hoy vale 0.0 por default, así que el precio
   calculado sale SIN envío y queda por debajo del real. NO es un olvido: es un
   hueco consciente y visible.
   Cuando Gaby dé los valores, el eje correcto es la LÍNEA de producto (col B del
   catálogo: RADIADOR, TOMA DE AGUA, BOMBA DE AGUA...), NO el precio ni el peso:
   ella misma razonó *"podría ser por categoría, radiadores por ejemplo seguro es
   más de 120, tomas de agua 100"*. El catálogo NO trae peso ni dimensiones, así
   que no se puede derivar — tiene que darlo ella.
   Para conectarlo basta llenar `ENVIO_POR_LINEA`; el motor ya lo consume.
"""
from dataclasses import dataclass

# Valores por default de la fórmula. Editables por proveedor desde la UI.
IVA_DEFAULT = 0.16
UTILIDAD_DEFAULT = 0.50
COMISION_ML_DEFAULT = 0.13

# ⬜ PENDIENTE: costo de envío por línea de producto. Vacío = 0.0 (sin envío).
# Llenar con los valores que dé Gaby, p.ej. {"RADIADOR": 120.0, "TOMA DE AGUA": 100.0}
ENVIO_POR_LINEA: dict = {}
ENVIO_DEFAULT = 0.0


@dataclass
class ParametrosPrecio:
    iva: float = IVA_DEFAULT
    utilidad: float = UTILIDAD_DEFAULT
    comision_ml: float = COMISION_ML_DEFAULT
    envio_default: float = ENVIO_DEFAULT


def envio_de_linea(linea: str, params: ParametrosPrecio) -> float:
    """Costo de envío de una línea de producto. ⬜ Hoy siempre el default (0.0)."""
    if not linea:
        return params.envio_default
    return ENVIO_POR_LINEA.get(linea.strip().upper(), params.envio_default)


def calcular_precio(costo, linea: str = "", params: ParametrosPrecio = None):
    """Precio de venta en ML. Devuelve None si no hay costo utilizable.

    None y 0.0 son DISTINTOS a propósito: None = "no se pudo calcular" y deja la
    celda vacía para que Gaby la llene; un 0.0 se subiría a ML como precio real.
    """
    params = params or ParametrosPrecio()
    try:
        costo = float(costo)
    except (TypeError, ValueError):
        return None
    if costo <= 0:
        return None

    base = costo * (1.0 + params.iva) * (1.0 + params.utilidad)
    envio = envio_de_linea(linea, params)

    divisor = 1.0 - params.comision_ml
    if divisor <= 0:            # comisión mal configurada: no inventamos un precio
        return None

    return round((base + envio) / divisor, 2)


def envio_pendiente() -> bool:
    """True mientras no se hayan cargado los costos de envío por línea.

    La UI lo usa para avisarle a Gaby que el precio sugerido va SIN envío, en vez
    de que ella lo descubra comparando contra lo que ML le cobra.
    """
    return not ENVIO_POR_LINEA
