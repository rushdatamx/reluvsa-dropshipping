"""
Perfiles de catálogo: lo ÚNICO que cambia entre proveedores.

Gaby lo dijo textual: *"cada proveedor me pasa su información diferente"*. Un
perfil describe dónde vive cada dato en el Excel de ESE proveedor; de ahí en
adelante (expansión de títulos, descripción, cruce, generación del .xlsx) el
motor es común. Agregar CAUPLAS o KIM después es escribir un perfil, no un
módulo nuevo.

Las columnas van por índice 0-based sobre la hoja del catálogo.
"""
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class PerfilCatalogo:
    codigo_bodega: str          # llave del proveedor (CAUPLAS, KG, KIM, AG, VAZLO)
    nombre_hoja: Optional[str]  # None = la primera hoja
    fila_header: int            # 1-based, la fila con los nombres de columna
    col_clave: int              # SKU del proveedor
    col_linea: int              # línea/familia del producto ('BOMBA DE AGUA')
    col_aplicaciones: int       # la columna que genera las publicaciones
    col_precio_costo: int       # el costo con el que se calcula el precio
    col_codigo_barras: Optional[int] = None
    col_precio_publico: Optional[int] = None
    # Ancla para reconocer el archivo por su contenido (candado de tipo).
    anclas_header: tuple = ()
    marca_ml: str = ""          # lo que va en la columna Marca de la plantilla


PERFILES: Dict[str, PerfilCatalogo] = {
    # KeepOnGreen — 'LISTA PRECIOS KG.xlsx', hoja 'KeepOnGreen® Ecommerce'.
    # Header en la fila 3; los datos arrancan en la 4. Medido: 3,676 piezas.
    "KG": PerfilCatalogo(
        codigo_bodega="KG",
        nombre_hoja=None,
        fila_header=3,
        col_clave=2,             # C  'Clave KeepOnGreen®'
        col_linea=1,             # B  'Nombre de la Linea'
        col_aplicaciones=6,      # G  'Aplicaciones Principales'
        col_precio_costo=8,      # I  ' Gran Mayoreo'  (sin IVA)
        col_codigo_barras=3,     # D  'Código de Barras'
        col_precio_publico=9,    # J  'Precio Sugerido PMS (incluye IVA)'
        anclas_header=("clave", "aplicaciones principales", "gran mayoreo"),
        marca_ml="KeepOnGreen",
    ),
}


def perfil_de(codigo_bodega: str) -> Optional[PerfilCatalogo]:
    return PERFILES.get((codigo_bodega or "").strip().upper())


def proveedores_soportados() -> list:
    return sorted(PERFILES.keys())
