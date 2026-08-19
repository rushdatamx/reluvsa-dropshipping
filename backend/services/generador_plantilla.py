"""
Generador de la plantilla de publicaciones masivas para Mercado Libre.

Convierte el catálogo de un proveedor en el .xlsx de 36 columnas que Gaby sube a
ML — el mismo formato de 'PENDIENTES ACDELCO.xlsx', que es la plantilla real que
ella usa hoy.

⭐ UNA PIEZA GENERA N PUBLICACIONES. Es lo que vuelve masivo el módulo: medido en
su archivo, 83 filas salieron de sólo 22 SKUs (~3.8x). Cada aplicación de la
columna "Aplicaciones Principales" es una publicación distinta; el SKU, el
precio, la descripción y las imágenes se repiten IDÉNTICOS y lo único que cambia
es el TÍTULO (el coche al que le sirve). Así compite por más búsquedas con la
misma pieza.

LA DESCRIPCIÓN es cabeza variable + cuerpo fijo, tal como ella lo pidió:
*"que pueda ponerse una sola descripción donde yo pueda poner mi descripción
base, pero lo que cambie sea el principio que es el tema de equivalencias o
compatibilidades"*. El cuerpo (garantía, horarios, facturación) se guarda una vez
por proveedor y no se vuelve a tocar.

⬜ LAS IMÁGENES VAN VACÍAS a propósito (confirmado por Gaby el 2026-08-19):
*"las subo a autozur, copio el link que me arroja y lo pongo, pero esto seguiría
siendo manual"*. El catálogo del proveedor no trae fotos. Ella las pega después
de generar el archivo.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from services.aplicaciones_kg import Aplicacion, parse_aplicaciones
from services.perfiles_catalogo import PerfilCatalogo
from services.precio_publicacion import ParametrosPrecio, calcular_precio

# Las 36 columnas de la plantilla, en el orden EXACTO que espera Mercado Libre.
# 🔴 No reordenar ni renombrar: ML lee por posición y encabezado.
COLUMNAS = [
    "Titulo", "Categoria", "Precio", "Moneda(MXN,ARS,COP)", "Cantidad",
    "Tipo Publicacion (clasica,premium)", "Condición(nuevo,usado)",
    "Garantia(CANTIDAD [días, meses, años]_[FABRICA,VENDEDOR])",
    "Modo Envio(me1,me2)", "Dimensiones(ALTcm,ANCHcm,LONGcm,PESOgr)",
    "Envio Gratis(si,no)", "SKU", "Descripcion", "Tienda Oficial",
    "Imagen1", "Imagen2", "Imagen3", "Imagen4", "Imagen5",
    "Imagen6", "Imagen7", "Imagen8", "Imagen9", "Imagen10",
    "UPC", "Marca", "Talla", "Color", "Modelo",
    "Canal(mercadolibre,mshops,ambos)",
    "AG", "CAUPLAS", "KG", "KIM", "MATRIZ", "VAZLO",
]

# Las 6 bodegas del final: el stock va en la columna del proveedor, 0 en las demás.
BODEGAS = ["AG", "CAUPLAS", "KG", "KIM", "MATRIZ", "VAZLO"]

# Las constantes que Gaby marcó en AMARILLO PURO: siempre van iguales.
CONSTANTES = {
    "Moneda(MXN,ARS,COP)": "MXN",
    "Cantidad": 10,
    "Tipo Publicacion (clasica,premium)": "Clasica",
    "Condición(nuevo,usado)": "Nuevo",
    "Garantia(CANTIDAD [días, meses, años]_[FABRICA,VENDEDOR])": "2meses_vendedor",
    "Modo Envio(me1,me2)": "me2",
    "Envio Gratis(si,no)": "Si",
    "Canal(mercadolibre,mshops,ambos)": "mercadolibre",
}

# Tope real de Mercado Libre para el título de una publicación.
MAX_TITULO = 60


@dataclass
class ConfiguracionProveedor:
    """Lo que Gaby edita por proveedor, sin tocar código."""

    codigo_bodega: str
    descripcion_base: str = ""      # el cuerpo fijo (garantía, horarios, facturación)
    marca: str = ""
    categoria_ml: str = ""          # 'MLM163963' — la categoría de ML
    cantidad: int = 10
    params_precio: ParametrosPrecio = field(default_factory=ParametrosPrecio)


@dataclass
class FilaPublicacion:
    """Una fila del Excel = una publicación de ML."""

    titulo: str
    sku: str
    linea: str
    precio: Optional[float]
    descripcion: str
    aplicacion: str
    truncada: bool = False


def _titular(nombre_pieza: str, app: Aplicacion) -> str:
    """Arma el título como lo escribe Gaby: 'Pieza P/ Coche Motor Años'.

    Formato copiado de su plantilla real:
        'Sensor Map De Aire Admision P/ Truck H200 2.5 2016 2017 2018'
    Se recorta a 60 caracteres (tope de ML) quitando años del final, nunca a
    media palabra: un título cortado se ve como error en la publicación.
    """
    pieza = " ".join(w.capitalize() for w in str(nombre_pieza or "").split())
    vehiculo = " ".join(w.capitalize() if w.isupper() and len(w) > 3 else w
                        for w in app.vehiculo.split())
    # Del motor sólo interesan los litros ('L4 1.6L' -> '1.6').
    litros = ""
    if app.motor:
        m = re.search(r"(\d[.,]\d)", app.motor)
        if m:
            litros = m.group(1).replace(",", ".")

    cabeza = f"{pieza} P/ {vehiculo}".strip()
    if litros:
        cabeza = f"{cabeza} {litros}"

    anios = app.anios.split()
    titulo = f"{cabeza} {' '.join(anios)}".strip()
    # Si no cabe, se van soltando años del final (el rango sigue siendo válido).
    while len(titulo) > MAX_TITULO and anios:
        anios.pop()
        titulo = f"{cabeza} {' '.join(anios)}".strip()
    return titulo[:MAX_TITULO].strip()


def _describir(nombre_pieza: str, clave: str, apps: List[Aplicacion],
               config: ConfiguracionProveedor) -> str:
    """Cabeza variable (pieza + OEM + compatibilidades) + cuerpo fijo.

    Las compatibilidades listan TODAS las aplicaciones de la pieza, no sólo la de
    esta fila: el comprador quiere ver si le sirve a su coche.
    """
    partes = [str(nombre_pieza or "").strip(), "", "OEM:", str(clave or "").strip()]

    utiles = [a for a in apps if not a.truncada]
    if utiles:
        partes += ["", "Compatibilidades:"]
        for a in utiles:
            linea = " ".join(x for x in (a.vehiculo, a.motor, a.anios) if x)
            partes.append(linea)

    if config.descripcion_base:
        partes += ["", config.descripcion_base.strip()]

    return "\n".join(partes)


def generar_filas(piezas, config: ConfiguracionProveedor,
                  incluir_truncadas: bool = False) -> List[FilaPublicacion]:
    """Expande cada pieza del catálogo en sus N publicaciones."""
    filas: List[FilaPublicacion] = []

    for pieza in piezas:
        res = parse_aplicaciones(pieza.get("aplicaciones"))
        apps = res.aplicaciones if incluir_truncadas else res.utiles
        if not apps:
            continue

        precio = calcular_precio(pieza.get("costo"), pieza.get("linea", ""),
                                 config.params_precio)
        descripcion = _describir(pieza.get("linea"), pieza.get("clave"),
                                 res.aplicaciones, config)

        for app in apps:
            filas.append(FilaPublicacion(
                titulo=_titular(pieza.get("linea"), app),
                sku=str(pieza.get("clave") or "").strip(),
                linea=str(pieza.get("linea") or "").strip(),
                precio=precio,
                descripcion=descripcion,
                aplicacion=app.texto,
                truncada=app.truncada,
            ))

    return filas


def escribir_xlsx(filas: List[FilaPublicacion], config: ConfiguracionProveedor,
                  destino) -> int:
    """Escribe el .xlsx con las 36 columnas listo para subir a ML."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Publicaciones"

    encabezado = Font(bold=True)
    relleno = PatternFill("solid", fgColor="FFFF00")
    for c, nombre in enumerate(COLUMNAS, 1):
        celda = ws.cell(1, c, nombre)
        celda.font = encabezado
        if nombre in CONSTANTES:
            celda.fill = relleno

    idx = {nombre: i + 1 for i, nombre in enumerate(COLUMNAS)}
    bodega = config.codigo_bodega.strip().upper()

    for f, fila in enumerate(filas, start=2):
        ws.cell(f, idx["Titulo"], fila.titulo)
        ws.cell(f, idx["Categoria"], config.categoria_ml)
        # Precio None deja la celda VACÍA (no 0): un 0 se publicaría como precio real.
        if fila.precio is not None:
            ws.cell(f, idx["Precio"], fila.precio)
        ws.cell(f, idx["SKU"], fila.sku)
        ws.cell(f, idx["Descripcion"], fila.descripcion).alignment = Alignment(wrap_text=True)
        ws.cell(f, idx["Marca"], config.marca)
        ws.cell(f, idx["Modelo"], fila.sku)

        for nombre, valor in CONSTANTES.items():
            ws.cell(f, idx[nombre], valor)
        ws.cell(f, idx["Cantidad"], config.cantidad)

        # Stock en la bodega del proveedor, 0 en las demás.
        for b in BODEGAS:
            ws.cell(f, idx[b], config.cantidad if b == bodega else 0)

        # ⬜ Imagen1..10 quedan VACÍAS: Gaby las pega tras subirlas a autozur.

    ws.freeze_panes = "A2"
    for col, ancho in (("A", 58), ("M", 60), ("L", 18)):
        ws.column_dimensions[col].width = ancho

    wb.save(destino)
    return len(filas)
