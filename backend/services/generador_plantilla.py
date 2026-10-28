"""
Generador de la plantilla de publicaciones masivas para Mercado Libre.

Convierte el catálogo de un proveedor en el .xlsx que Gaby sube a
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

# Las columnas de la plantilla, en el orden EXACTO que espera Mercado Libre.
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
    "AG", "CAUPLAS", "KG", "KIM", "MATRIZ", "VAZLO", "GONHER", "DIFIRSA",
]

# Las 8 bodegas del final: el stock va en la columna del proveedor, 0 en las demás.
BODEGAS = ["AG", "CAUPLAS", "KG", "KIM", "MATRIZ", "VAZLO", "GONHER", "DIFIRSA"]

# Las constantes que Gaby marcó en AMARILLO PURO: siempre van iguales.
# "Envio Gratis" no pertenece aquí: se deriva del precio final de cada fila.
CONSTANTES = {
    "Moneda(MXN,ARS,COP)": "MXN",
    "Tipo Publicacion (clasica,premium)": "Clasica",
    "Condición(nuevo,usado)": "Nuevo",
    "Garantia(CANTIDAD [días, meses, años]_[FABRICA,VENDEDOR])": "2meses_vendedor",
    "Modo Envio(me1,me2)": "me2",
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
    tipo_cambio_usd: float = 18.50
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
    imagenes: List[str] = field(default_factory=list)
    fila_origen: Optional[int] = None
    stock: Optional[int] = 0
    marca: str = ""
    # Metadatos estructurados para cruces conservadores. Sólo GONHER los usa;
    # los demás proveedores siguen cruzando por SKU + título normalizado.
    compatibilidad: dict = field(default_factory=dict)


# Alias aprobados para las líneas reales del master. Sólo se usan si el nombre
# completo no cabe; modelo, cilindrada y años nunca se recortan.
ALIAS_PRODUCTO = {
    "banda de accesorios": "Banda Accesorios", "banda de tiempo": "Banda Tiempo",
    "bomba de agua": "Bomba Agua", "bomba de agua auxiliar": "Bomba Agua Aux",
    "deposito de anticongelante": "Deposito Anticongelante", "fan clutch": "Fan Clutch",
    "kit de banda de accesorios": "Kit Banda Accesorios",
    "kit de banda de distribucion": "Kit Banda Distribucion",
    "kit de banda de distribucion c/bomba": "Kit Banda Distrib c/Bomba",
    "kit de cadena de distribucion": "Kit Cadena Distribucion",
    "manguera moldeada": "Manguera Moldeada", "motoventilador": "Motoventilador",
    "polea de accesorios": "Polea Accesorios", "polea de distribucion": "Polea Distribucion",
    "sensor de temperatura": "Sensor Temperatura", "tapon de deposito": "Tapon Deposito",
    "tapon de radiador": "Tapon Radiador",
    "tensor hidraulico de distribucion": "Tensor Hidraulico Distrib",
    "tensor de accesorios": "Tensor Accesorios", "tensor de accesorios hd": "Tensor Accesorios HD",
    "tensor de accesorios hidraulico": "Tensor Accesorios Hidraulico",
    "toma de agua": "Toma Agua", "toma de agua housing": "Toma Agua Housing",
    "toma de agua con termostato": "Toma Agua c/Termostato",
    "tubo de enfriamiento": "Tubo Enfriamiento",
}

# Alias exclusivos del master CAUPLAS. Son la tercera y última alternativa:
# primero se intenta el nombre completo con armadora, luego sin armadora y sólo
# entonces se abrevia el producto. Nunca se recorta el vehículo ni los años.
ALIAS_PRODUCTO_CAUPLAS = {
    "deposito de refrigerante de motor": "Depósito Refrigerante",
    "manguera radiador": "Mang. Radiador",
    "manguera calefaccion": "Mang. Calef.",
    "manguera refrigeracion": "Mang. Refrig.",
    "manguera descarga de gases": "Mang. Descarga Gases",
    "manguera para frenos hidraulicos": "Mang. Freno Hidráulico",
    "manguera circulacion de aire": "Mang. Circulación Aire",
    "refrigeracion": "Refrig.",
}

# Nombres aprobados por AG; se usan sólo para intentar cumplir el límite de ML.
ALIAS_PRODUCTO_AG = {
    "amortiguador": "Amort.", "amortiguador delantero": "Amort. Del.",
    "amortiguador trasero": "Amort. Tras.", "base amortiguador": "Base Amort.",
    "soporte amortiguador": "Soporte Amort.", "kit amortiguador": "Kit Amort.",
    "resorte": "Resorte",
    "kit rotulas": "Kit Rótulas", "kit amortiguadores": "Kit Amort.",
    "kit amortiguadores y resortes": "Kit Amort. y Resortes",
    "kit balatas": "Kit Balatas", "kit amortiguadores y bases": "Kit Amort. y Bases",
    "kit amortiguadores c/resortes y bases amortiguador": "Kit Amort. c/Resortes y Bases",
    "kit resortes": "Kit Resortes", "kit bases amortiguador": "Kit Bases Amort.",
    "kit amortiguadores resorte y base": "Kit Amort. Resorte y Base",
    "kit resortes amortiguadores deportivos y bases": "Kit Resortes Amort. Deport. y Bases",
    "resortes deportivos y amortiguadores": "Resortes Deport. y Amort.",
    "amortiguadores deportivos y bases": "Amort. Deport. y Bases",
    "kit resortes y amortiguadores deportivos": "Kit Resortes y Amort. Deport.",
    "kit amortiguadores resortes deportivos y bases": "Kit Amort. Resortes Deport. y Bases",
    "kit amortiguadores deportivos y resortes": "Kit Amort. Deport. y Resortes",
    "kit amortiguadores delanteros": "Kit Amort. Delanteros",
    "kit amortiguadores p/achaparrar": "Kit Amort. p/Achaparrar",
}
ALIAS_POSICION_AG = {"delantero": "Del.", "trasero": "Tras.", "izquierdo": "Izq.", "derecho": "Der."}


def _sin_acentos(txt):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(txt).lower())
                   if unicodedata.category(c) != "Mn")


# Estas unidades pertenecen a la descripción variable del catálogo, no al cuerpo
# fijo que Gaby escribe para cada proveedor.  La etiqueta se reconoce sin
# importar mayúsculas ni acentos; Diámetro conserva opcionalmente su índice.
_ETIQUETA_MEDIDA_RE = re.compile(
    r"(?P<etiqueta>(?:alto|altura|largo|ancho)|(?:di[aá]metro)(?:\s+\d+)?)"
    r"(?P<separador>\s*(?::|=)\s*|\s+)"
    r"(?P<valores>-?\d+(?:[.,]\d+)?(?:\s*(?:mm|cm|pulgadas?))?"
    r"(?:\s*\|\s*-?\d+(?:[.,]\d+)?(?:\s*(?:mm|cm|pulgadas?))?)*)",
    re.IGNORECASE,
)
_NUMERO_CON_UNIDAD_RE = re.compile(
    r"(?P<numero>-?\d+(?:[.,]\d+)?)(?P<unidad>\s*(?:mm|cm|pulgadas?))?",
    re.IGNORECASE,
)


def _unidad_de_medida(etiqueta: str) -> str:
    """Devuelve la unidad acordada para una etiqueta de medida del catálogo."""
    etiqueta = _sin_acentos(etiqueta)
    return "mm" if etiqueta.startswith("diametro") else "cm"


def _formatear_valores_medida(etiqueta: str, valores) -> str:
    """Agrega la unidad a cada número sin unidad de una medida conocida.

    Se usa tanto para la estructura de CAUPLAS como para texto libre de otros
    masters. No transforma texto sin una cifra ni reescribe unidades existentes.
    """
    unidad = _unidad_de_medida(etiqueta)

    def reemplazar(numero):
        return numero.group(0) if numero.group("unidad") else f"{numero.group('numero')} {unidad}"

    return _NUMERO_CON_UNIDAD_RE.sub(reemplazar, str(valores))


def _normalizar_medidas_en_texto(texto) -> str:
    """Normaliza sólo fragmentos `Alto/…: número` de datos variables.

    Requerir una cifra junto a la etiqueta evita modificar expresiones como
    ``Alto Flujo``. Cada cifra separada por ``|`` recibe su propia unidad.
    """
    def reemplazar(coincidencia):
        return (coincidencia.group("etiqueta") + coincidencia.group("separador") +
                _formatear_valores_medida(coincidencia.group("etiqueta"),
                                           coincidencia.group("valores")))

    return _ETIQUETA_MEDIDA_RE.sub(reemplazar, str(texto or ""))


def _cilindrada(motor):
    m = re.search(r"(\d[.,]\d)", str(motor or ""))
    return m.group(1).replace(",", ".") if m else ""


def _titulo_master(producto, compat, anios, permitir_exceso=False) -> Optional[str]:
    producto = " ".join(str(producto or "").split())
    armadora = " ".join(str(compat.get("armadora") or "").split())
    modelo = " ".join(str(compat.get("modelo") or "").split())
    motor = _cilindrada(compat.get("motor"))
    sufijo = " ".join(x for x in (modelo, motor, anios) if x)
    alias = ALIAS_PRODUCTO.get(_sin_acentos(producto), producto)
    intentos = [f"{producto} para {armadora} {sufijo}", f"{producto} para {sufijo}",
                f"{alias} para {sufijo}"]
    ultimo = ""
    for titulo in intentos:
        titulo = re.sub(r"\s+", " ", titulo).strip()
        ultimo = titulo
        if len(titulo) <= MAX_TITULO: return titulo
    return ultimo if permitir_exceso else None


def _variantes_master(producto, compat):
    inicio, fin = compat["inicio"], compat["fin"]
    if inicio == fin:
        titulo = _titulo_master(producto, compat, str(inicio))
        return ([titulo] if titulo else []), ([] if titulo else [(str(inicio), "Título excede 60 caracteres")])
    variantes, excluidas = [], []
    rango = f"{inicio}/{fin}"
    titulo = _titulo_master(producto, compat, rango)
    (variantes if titulo else excluidas).append(titulo or (rango, "Título excede 60 caracteres"))
    anios = list(range(inicio, fin + 1)); corte = (len(anios) + 1) // 2

    def agregar_bloque(bloque):
        texto = " ".join(map(str, bloque)); t = _titulo_master(producto, compat, texto)
        if t: variantes.append(t)
        elif len(bloque) > 1:
            mitad = (len(bloque) + 1) // 2
            agregar_bloque(bloque[:mitad]); agregar_bloque(bloque[mitad:])
        else: excluidas.append((texto, "Título excede 60 caracteres"))
    agregar_bloque(anios[:corte]); agregar_bloque(anios[corte:])
    return variantes, excluidas


def _titulo_cauplas(producto, compat, anios, permitir_exceso=False) -> Optional[str]:
    producto = " ".join(str(producto or "").split())
    armadora = " ".join(str(compat.get("armadora") or "").split())
    modelo = " ".join(str(compat.get("modelo") or "").split())
    cilindrada = " ".join(str(compat.get("motor") or "").split())
    if compat.get("universal"):
        sufijo = "Universal"
        intentos = [f"{producto} para {sufijo}"]
    else:
        sufijo = " ".join(x for x in (modelo, cilindrada, anios) if x)
        intentos = [f"{producto} para {armadora} {sufijo}", f"{producto} para {sufijo}"]
    alias = ALIAS_PRODUCTO_CAUPLAS.get(_sin_acentos(producto))
    if alias:
        intentos.append(f"{alias} para {sufijo}")
    ultimo = ""
    for titulo in intentos:
        titulo = re.sub(r"\s+", " ", titulo).strip()
        ultimo = titulo
        if len(titulo) <= MAX_TITULO:
            return titulo
    return ultimo if permitir_exceso else None


def _variantes_cauplas(producto, compat):
    if compat.get("universal"):
        titulo = _titulo_cauplas(producto, compat, "")
        return ([titulo] if titulo else []), ([] if titulo else [("All", "Título excede 60 caracteres")])
    inicio, fin = compat["inicio"], compat["fin"]
    if inicio == fin:
        titulo = _titulo_cauplas(producto, compat, str(inicio))
        return ([titulo] if titulo else []), ([] if titulo else [(str(inicio), "Título excede 60 caracteres")])
    variantes, excluidas = [], []
    rango = f"{inicio}/{fin}"
    titulo = _titulo_cauplas(producto, compat, rango)
    (variantes if titulo else excluidas).append(titulo or (rango, "Título excede 60 caracteres"))
    anios = list(range(inicio, fin + 1))
    corte = (len(anios) + 1) // 2

    def agregar_bloque(bloque):
        texto = " ".join(map(str, bloque))
        titulo_bloque = _titulo_cauplas(producto, compat, texto)
        if titulo_bloque:
            variantes.append(titulo_bloque)
        elif len(bloque) > 1:
            mitad = (len(bloque) + 1) // 2
            agregar_bloque(bloque[:mitad])
            agregar_bloque(bloque[mitad:])
        else:
            excluidas.append((texto, "Título excede 60 caracteres"))

    agregar_bloque(anios[:corte])
    agregar_bloque(anios[corte:])
    return variantes, excluidas


def _titulo_kims(producto, compat, permitir_exceso=False) -> Optional[str]:
    producto = " ".join(str(producto or "").split())
    armadora = " ".join(str(compat.get("armadora") or "").split())
    modelo = " ".join(str(compat.get("modelo") or "").split())
    version = " ".join(str(compat.get("version") or "").split())
    motor = " ".join(str(compat.get("motor") or "").split())
    inicio, fin = compat["inicio"], compat["fin"]
    anios = str(inicio) if inicio == fin else f"{inicio}/{fin}"
    intentos = [
        (producto, "para", armadora, modelo, version, motor, anios),
        (producto, "para", modelo, version, motor, anios),
        (producto, "para", modelo, motor, anios),
    ]
    ultimo = ""
    for partes in intentos:
        titulo = " ".join(x for x in partes if x)
        titulo = re.sub(r"\s+", " ", titulo).strip()
        ultimo = titulo
        # Producto, modelo y años son estructurales; motor se conserva cuando existe.
        if titulo and len(titulo) <= MAX_TITULO:
            return titulo
    return ultimo if permitir_exceso else None


def _variantes_kims(producto, compat):
    """Devuelve títulos anuales y, para rangos, una variante con el rango completo."""
    inicio, fin = compat["inicio"], compat["fin"]
    anios = [inicio] if inicio == fin else list(range(inicio, fin + 1)) + [None]
    variantes = []
    for anio in anios:
        variante = dict(compat)
        if anio is not None:
            variante.update(inicio=anio, fin=anio)
            etiqueta = str(anio)
        else:
            etiqueta = f"{inicio}/{fin}"
        variantes.append((etiqueta, variante))
    return variantes


def _motor_gonher_reducido(motor):
    texto = " ".join(str(motor or "").split())
    arquitectura = re.search(r"\b(?:L|V|H|I|B|W)\s*\d{1,2}\b", texto, re.IGNORECASE)
    cilindrada = re.search(r"\b\d+(?:[.,]\d+)?\s*L\b", texto, re.IGNORECASE)
    partes = []
    for coincidencia in (arquitectura, cilindrada):
        if coincidencia:
            valor = re.sub(r"\s+", "", coincidencia.group(0)).replace(",", ".").upper()
            if valor not in partes:
                partes.append(valor)
    return " ".join(partes)


def _titulo_gonher(producto, compat, permitir_exceso=False) -> Optional[str]:
    producto = " ".join(str(producto or "").split())
    marca = " ".join(str(compat.get("armadora") or "").split())
    modelo = " ".join(str(compat.get("modelo") or "").split())
    motor = " ".join(str(compat.get("motor") or "").split())
    anios = " ".join(str(compat.get("anios") or "").split())
    intentos = [
        (producto, "para", marca, modelo, motor, anios),
        (producto, "para", marca, modelo, _motor_gonher_reducido(motor), anios),
        (producto, "para", modelo, _motor_gonher_reducido(motor), anios),
    ]
    ultimo = ""
    for partes in intentos:
        titulo = re.sub(r"\s+", " ", " ".join(x for x in partes if x)).strip()
        ultimo = titulo
        if titulo and len(titulo) <= MAX_TITULO:
            return titulo
    return ultimo if permitir_exceso else None


def _anios_gonher(compat):
    """Expande el rango de la hoja en años publicables individuales."""
    inicio, fin = compat.get("inicio"), compat.get("fin")
    if not isinstance(inicio, int) or not isinstance(fin, int) or inicio > fin:
        return []
    return list(range(inicio, fin + 1))


def _titular(nombre_pieza: str, app: Aplicacion) -> str:
    """Arma el título: 'Pieza para Coche Motor Años'.

    Formato copiado de su plantilla real:
        'Sensor Map De Aire Admision para Truck H200 2.5 2016 2017 2018'
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

    cabeza = f"{pieza} para {vehiculo}".strip()
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


def _describir_master(pieza, config):
    partes = [pieza.get("producto", "")]
    if pieza.get("oems"):
        partes += ["", "OEM: " + ", ".join(pieza["oems"])]
    guias = [c.get("guia") for c in pieza.get("compatibilidades", []) if c.get("guia")]
    if guias: partes += ["", "Compatibilidades:"] + list(dict.fromkeys(guias))
    if pieza.get("especificaciones"):
        partes += ["", "Especificaciones:"] + [
            _normalizar_medidas_en_texto(valor) for valor in pieza["especificaciones"]
        ]
    if pieza.get("caracteristicas"):
        partes += ["", "Características:"] + [
            _normalizar_medidas_en_texto(valor) for valor in pieza["caracteristicas"]
        ]
    if config.descripcion_base: partes += ["", config.descripcion_base.strip()]
    return "\n".join(str(x).strip() for x in partes)


def _describir_cauplas(pieza, config):
    partes = ["Productos y especificaciones:"]
    for par in pieza.get("productos_especificaciones", []):
        producto, especificacion = par.get("producto", ""), par.get("especificacion", "")
        texto = f"{producto} — {especificacion}" if especificacion else producto
        partes.append(_normalizar_medidas_en_texto(texto))
    if pieza.get("oems"):
        partes += ["", "OEM: " + " | ".join(pieza["oems"])]
    equivalencias = [(marca, codigos) for marca, codigos in pieza.get("equivalencias", {}).items()
                     if codigos]
    if equivalencias:
        partes += ["", "Equivalencias:"]
        partes += [f"{marca}: {' | '.join(codigos)}" for marca, codigos in equivalencias]
    productos = [x.get("producto", "") for x in pieza.get("productos_especificaciones", [])]
    if any("manguera" in _sin_acentos(producto) for producto in productos):
        medidas = [(nombre, valores) for nombre, valores in pieza.get("medidas", {}).items() if valores]
        if medidas:
            partes += ["", "Medidas:"]
            partes += [f"{nombre}: {' | '.join(_formatear_valores_medida(nombre, valor) for valor in valores)}"
                       for nombre, valores in medidas]
    compatibilidades = []
    for compat in pieza.get("compatibilidades", []):
        if compat.get("universal"):
            texto = "Universal"
        else:
            anios = (str(compat["inicio"]) if compat["inicio"] == compat["fin"]
                     else f"{compat['inicio']}-{compat['fin']}")
            texto = " ".join(x for x in (compat.get("armadora"), compat.get("modelo"),
                                         compat.get("motor"), anios) if x)
        if _sin_acentos(texto) not in {_sin_acentos(x) for x in compatibilidades}:
            compatibilidades.append(texto)
    if compatibilidades:
        partes += ["", "Compatibilidades:"] + compatibilidades
    if config.descripcion_base:
        partes += ["", config.descripcion_base.strip()]
    return "\n".join(str(x).strip() for x in partes)


def _describir_difrisa(pieza, config):
    partes = [pieza.get("producto", ""), "", "Número de parte: " + str(pieza.get("clave", ""))]
    if pieza.get("equivalencias"):
        partes += ["", "Equivalencias: " + " | ".join(pieza["equivalencias"])]
    if pieza.get("oems"):
        partes += ["", "OEM: " + " | ".join(pieza["oems"])]
    compatibles = []
    for c in pieza.get("compatibilidades", []):
        anios = str(c["inicio"]) if c["inicio"] == c["fin"] else f"{c['inicio']}-{c['fin']}"
        compatibles.append(" ".join(x for x in (c.get("armadora"), c.get("modelo"), c.get("motor"), anios) if x))
    if compatibles:
        partes += ["", "Compatibilidades:"] + compatibles
    if config.descripcion_base:
        partes += ["", config.descripcion_base.strip()]
    return "\n".join(str(x).strip() for x in partes)


def _describir_kims(pieza, config):
    partes = [pieza.get("producto", "")]
    if pieza.get("oems"): partes += ["", "OEM: " + " | ".join(pieza["oems"])]
    if pieza.get("combustibles"): partes += ["", "Combustible: " + " | ".join(pieza["combustibles"])]
    if pieza.get("comentarios"): partes += ["", "Comentarios:"] + pieza["comentarios"]
    if pieza.get("posiciones"): partes += ["", "Posiciones: " + " | ".join(pieza["posiciones"])]
    compatibilidades = []
    for c in pieza.get("compatibilidades", []):
        anios = str(c["inicio"]) if c["inicio"] == c["fin"] else f"{c['inicio']}/{c['fin']}"
        texto = " ".join(x for x in (c.get("armadora"), c.get("modelo"), c.get("version"),
                                      c.get("motor"), anios) if x)
        if _sin_acentos(texto) not in {_sin_acentos(x) for x in compatibilidades}:
            compatibilidades.append(texto)
    if compatibilidades: partes += ["", "Compatibilidades:"] + compatibilidades
    if config.descripcion_base: partes += ["", config.descripcion_base.strip()]
    return "\n".join(str(x).strip() for x in partes)


def _describir_gonher(pieza, config):
    productos = pieza.get("productos", []) or [pieza.get("producto", "")]
    encabezado = " y ".join(productos)
    tipos = pieza.get("tipos", [])
    if tipos:
        encabezado += " y " + " | ".join(tipos)
    partes = [encabezado]
    if pieza.get("codigos_actuales"):
        partes += ["", "Código actual: " + " | ".join(pieza["codigos_actuales"])]
    partes += ["Número de parte: " + str(pieza.get("clave", ""))]
    if pieza.get("roscas"):
        partes += ["Rosca: " + " | ".join(pieza["roscas"])]
    medidas = [(nombre, valores) for nombre, valores in pieza.get("medidas", {}).items() if valores]
    if medidas:
        partes += ["Medidas:"]
        partes += [f"{nombre}: {' | '.join(str(v) + (' cm' if not re.search(r'[a-zA-Z]', str(v)) else '') for v in valores)}"
                   for nombre, valores in medidas]
    if pieza.get("oems"):
        partes += ["OEM: " + " | ".join(pieza["oems"])]
    for marca, codigos in pieza.get("equivalencias", {}).items():
        if codigos:
            partes += [f"{marca}: {' | '.join(codigos)}"]
    compatibilidades = []
    for c in pieza.get("compatibilidades", []):
        texto = " ".join(x for x in (c.get("armadora"), c.get("modelo"), c.get("motor"), c.get("anios")) if x)
        if _sin_acentos(texto) not in {_sin_acentos(x) for x in compatibilidades}:
            compatibilidades.append(texto)
    if compatibilidades:
        partes += ["", "Compatibilidades:"] + compatibilidades
    if config.descripcion_base:
        partes += ["", config.descripcion_base.strip()]
    return "\n".join(str(x).strip() for x in partes)


def _titulo_ag(producto, compat, anio, permitir_exceso=False):
    if compat.get("tipo_ag") == "combos":
        producto = " ".join(str(producto or "").split())
        aplicacion = " ".join(str(compat.get("aplicacion") or "").split())
        # En el master de combos Aplicación suele traer también el rango; los
        # años canónicos vienen en columnas separadas y se agregan una vez aquí.
        aplicacion = re.sub(
            r"\s+(?:19|20)\d{2}(?:\s*[-–/a]\s*(?:(?:19|20)\d{2}|\d{2}))?\s*$",
            "", aplicacion, flags=re.IGNORECASE,
        ).strip()
        titulo = " ".join(x for x in (producto, "para", aplicacion, str(anio)) if x)
        if len(titulo) > MAX_TITULO:
            corto = ALIAS_PRODUCTO_AG.get(_sin_acentos(producto), producto)
            titulo = " ".join(x for x in (corto, "para", aplicacion, str(anio)) if x)
        return titulo if permitir_exceso or len(titulo) <= MAX_TITULO else None
    producto = " ".join(str(producto or "").split())
    posicion = " ".join(str(compat.get("posicion") or "").split())
    marca = " ".join(str(compat.get("marca") or "").split())
    aplicacion = " ".join(str(compat.get("aplicacion") or "").split())
    def armar(prod, pos, app): return " ".join(x for x in (prod, pos, "para", marca, app, str(anio)) if x)
    intentos = [(producto, posicion, aplicacion),
        (ALIAS_PRODUCTO_AG.get(_sin_acentos(producto), producto), posicion, aplicacion),
        (ALIAS_PRODUCTO_AG.get(_sin_acentos(producto), producto),
         " ".join(ALIAS_POSICION_AG.get(_sin_acentos(x), x) for x in posicion.split()), aplicacion)]
    ultimo = ""
    for prod, pos, app in intentos:
        titulo = armar(prod, pos, app)
        ultimo = titulo
        if len(titulo) <= MAX_TITULO: return titulo
        # Las notas explicativas sólo se retiran para el título.
        sin_parentesis = re.sub(r"\s*\([^)]*\)", "", app).strip()
        titulo = armar(prod, pos, sin_parentesis)
        ultimo = titulo
        if len(titulo) <= MAX_TITULO: return titulo
    return ultimo if permitir_exceso else None


def _variantes_ag(producto, compat):
    """Rango completo más dos bloques balanceados, subdivididos si exceden 60."""
    inicio, fin = compat["inicio"], compat["fin"]
    if inicio == fin:
        titulo = _titulo_ag(producto, compat, str(inicio))
        return ([(titulo, [inicio])] if titulo else []), ([] if titulo else [(str(inicio), "Título excede 60 caracteres")])
    variantes, excluidas = [], []
    rango = f"{inicio}-{fin}"
    titulo = _titulo_ag(producto, compat, rango)
    if titulo: variantes.append((titulo, list(range(inicio, fin + 1))))
    else: excluidas.append((rango, "Título excede 60 caracteres"))
    anios = list(range(inicio, fin + 1)); corte = (len(anios) + 1) // 2
    def agregar(bloque):
        texto = " ".join(map(str, bloque))
        titulo_bloque = _titulo_ag(producto, compat, texto)
        if titulo_bloque: variantes.append((titulo_bloque, bloque))
        elif len(bloque) > 1:
            mitad = (len(bloque) + 1) // 2
            agregar(bloque[:mitad]); agregar(bloque[mitad:])
        else: excluidas.append((texto, "Título excede 60 caracteres"))
    agregar(anios[:corte]); agregar(anios[corte:])
    return variantes, excluidas


def _describir_ag(pieza, config):
    partes = [pieza.get("producto", ""), "", "Número de parte: " + str(pieza.get("clave", ""))]
    if pieza.get("tipo_ag") == "combos" and pieza.get("componentes"):
        partes += ["", "Componentes: " + " | ".join(pieza["componentes"])]
    equivalencias = [(marca, codigos) for marca, codigos in pieza.get("equivalencias", {}).items() if codigos]
    if equivalencias:
        partes += ["", "Equivalencias:"] + [f"{marca}: {' | '.join(codigos)}" for marca, codigos in equivalencias]
    compatibles = []
    for c in pieza.get("compatibilidades", []):
        anios = str(c["inicio"]) if c["inicio"] == c["fin"] else f"{c['inicio']}-{c['fin']}"
        texto = " ".join(x for x in (c.get("marca"), c.get("aplicacion"), c.get("posicion"), anios) if x)
        if _sin_acentos(texto) not in {_sin_acentos(x) for x in compatibles}: compatibles.append(texto)
    if compatibles: partes += ["", "Compatibilidades:"] + compatibles
    if config.descripcion_base: partes += ["", config.descripcion_base.strip()]
    return "\n".join(str(x).strip() for x in partes)


def _exclusion_titulo(codigo, clave, fila, titulo, compat, anio, producto=""):
    """Contrato común de pendientes cuyo único motivo es exceder el título."""
    proveedor = {"AG": "ARGENPARTS", "KIM": "KIMS"}.get(codigo, codigo)
    return {
        "titulo_candidato": titulo,
        "caracteres": len(titulo),
        "limite": MAX_TITULO,
        "sku": str(clave or "").strip(),
        "proveedor": proveedor,
        "fila_origen": fila,
        "compatibilidad": " ".join(str(compat.get(k) or "").strip()
                                     for k in ("armadora", "marca", "modelo", "aplicacion", "motor") if compat.get(k)),
        "anio": str(anio),
        "motivo": "Título excede 60 caracteres",
    }


def _fila_pendiente(exclusion, pieza, titulo, precio, descripcion, aplicacion,
                    imagenes=None, stock=0, marca="", linea=None, compatibilidad=None):
    exclusion["fila_publicacion"] = FilaPublicacion(
        titulo, str(pieza.get("clave") or "").strip(), linea or pieza.get("producto", ""),
        precio, descripcion, str(aplicacion or ""), False, imagenes or [],
        exclusion.get("fila"), stock, marca, compatibilidad or {},
    )
    return exclusion


def generar_filas_con_reporte(piezas, config):
    filas, exclusiones, deduplicadas, vistos = [], [], 0, set()
    for pieza in piezas:
        if pieza.get("formato") == "master_difrisa":
            descripcion = _describir_difrisa(pieza, config)
            precio = calcular_precio(pieza.get("costo"), pieza.get("linea", ""), config.params_precio)
            for compat in pieza.get("compatibilidades", []):
                titulos, fuera = _variantes_cauplas(pieza.get("producto"), compat)
                for anios, motivo in fuera:
                    candidato = _titulo_cauplas(pieza.get("producto"), compat, anios, permitir_exceso=True)
                    exclusion = {**_exclusion_titulo("DIFIRSA", pieza.get("clave"), compat.get("fila"), candidato, compat, anios),
                                 "fila": compat.get("fila"), "clave": pieza.get("clave"), "motivo": motivo}
                    exclusiones.append(_fila_pendiente(exclusion, pieza, candidato, precio, descripcion, anios,
                        stock=None, marca="Engine Fil", linea=pieza.get("linea")))
                for titulo in titulos:
                    par = (str(pieza.get("clave")).upper(), " ".join(_sin_acentos(titulo).split()))
                    if par in vistos:
                        deduplicadas += 1
                        continue
                    vistos.add(par)
                    filas.append(FilaPublicacion(titulo, pieza["clave"], pieza["linea"], precio, descripcion,
                        f"{compat.get('inicio')}-{compat.get('fin')}", False, [], compat.get("fila"),
                        stock=None, marca="Engine Fil", compatibilidad=compat))
            continue
        if pieza.get("formato") == "master_ag":
            descripcion = _describir_ag(pieza, config)
            precio = calcular_precio(pieza.get("costo"), pieza.get("linea", ""), config.params_precio)
            for compat in pieza.get("compatibilidades", []):
                compat = dict(compat, tipo_ag=pieza.get("tipo_ag", "legacy"))
                variantes, fuera = _variantes_ag(pieza.get("producto"), compat)
                for anios_txt, motivo in fuera:
                    candidato = _titulo_ag(pieza.get("producto"), compat, anios_txt, permitir_exceso=True)
                    for anio in (int(anios_txt),) if anios_txt.isdigit() else (int(x) for x in re.findall(r"\d{4}", anios_txt)):
                        compat_anual = dict(compat, producto=pieza.get("producto", ""), inicio=anio, fin=anio, formato="master_ag")
                        exclusion = {**_exclusion_titulo("AG", pieza.get("clave"), compat.get("fila"), candidato, compat, anios_txt),
                            "fila": compat.get("fila"), "clave": pieza.get("clave"), "armadora": compat.get("marca"),
                            "modelo": compat.get("aplicacion"), "inicio": anio, "fin": anio, "motivo": motivo}
                        exclusiones.append(_fila_pendiente(exclusion, pieza, candidato, precio, descripcion,
                            anio, stock=pieza["stock"], compatibilidad=compat_anual))
                for titulo, anios in variantes:
                    compat_titulo = dict(compat, producto=pieza.get("producto", ""),
                                         anios_cubiertos=list(anios), formato="master_ag")
                    par = (str(pieza.get("clave")).upper(), _sin_acentos(compat_titulo["producto"]).strip(),
                           _sin_acentos(compat_titulo.get("posicion") or "").strip(),
                           _sin_acentos(compat_titulo.get("marca") or "").strip(),
                           _sin_acentos(compat_titulo.get("aplicacion") or "").strip(), tuple(anios), titulo)
                    if par in vistos: deduplicadas += 1; continue
                    vistos.add(par)
                    etiqueta_anios = str(anios[0]) if len(anios) == 1 else f"{anios[0]}-{anios[-1]}"
                    filas.append(FilaPublicacion(titulo, str(pieza.get("clave") or "").strip(), pieza.get("producto", ""),
                        precio, descripcion, etiqueta_anios, False, [], compat.get("fila"), stock=pieza["stock"], compatibilidad=compat_titulo))
            continue
        if pieza.get("formato") == "master_gonher":
            descripcion = _describir_gonher(pieza, config)
            precio = calcular_precio(pieza.get("costo"), pieza.get("linea", ""), config.params_precio)
            for compat in pieza.get("compatibilidades", []):
                for anio in _anios_gonher(compat):
                    compat_anual = dict(compat, anios=str(anio), inicio=anio, fin=anio)
                    titulo = _titulo_gonher(compat.get("producto"), compat_anual)
                    if not titulo:
                        candidato = _titulo_gonher(compat.get("producto"), compat_anual, permitir_exceso=True)
                        exclusion = {**_exclusion_titulo("GONHER", pieza.get("clave"), compat.get("fila"),
                            candidato, compat, anio), "fila": compat.get("fila"), "clave": pieza.get("clave"),
                            "armadora": compat.get("armadora"), "modelo": compat.get("modelo"),
                            "inicio": anio, "fin": anio}
                        exclusiones.append(_fila_pendiente(exclusion, pieza, candidato, precio, descripcion,
                            anio, stock=None, linea=compat.get("linea", ""), compatibilidad=compat_anual))
                        continue
                    # La identidad de una variante no depende de cómo se
                    # escriba el título: vehículo + motor + año son obligatorios.
                    par = (str(pieza.get("clave")).upper(),
                           _sin_acentos(str(compat.get("armadora") or "")).strip().casefold(),
                           _sin_acentos(str(compat.get("modelo") or "")).strip().casefold(),
                           _sin_acentos(str(compat.get("motor") or "")).strip().casefold(),
                           anio)
                    if par in vistos:
                        deduplicadas += 1
                        continue
                    vistos.add(par)
                    filas.append(FilaPublicacion(titulo, str(pieza.get("clave") or "").strip(),
                        compat.get("linea", ""), precio, descripcion, str(anio), False,
                        [], compat.get("fila"), stock=None, compatibilidad=compat_anual))
            continue
        if pieza.get("formato") == "master_kims":
            descripcion = _describir_kims(pieza, config)
            precio = calcular_precio(pieza.get("costo_usd") * config.tipo_cambio_usd,
                                     pieza.get("linea", ""), config.params_precio)
            for compat in pieza.get("compatibilidades", []):
                for anio, variante in _variantes_kims(pieza.get("producto"), compat):
                    titulo = _titulo_kims(pieza.get("producto"), variante)
                    if not titulo:
                        candidato = _titulo_kims(pieza.get("producto"), variante, permitir_exceso=True)
                        exclusion = {**_exclusion_titulo("KIM", pieza.get("clave"), compat.get("fila"),
                            candidato, variante, anio), "fila": compat.get("fila"), "clave": pieza.get("clave"),
                            "armadora": compat.get("armadora"), "modelo": compat.get("modelo"),
                            "inicio": variante.get("inicio"), "fin": variante.get("fin")}
                        exclusiones.append(_fila_pendiente(exclusion, pieza, candidato, precio, descripcion,
                            anio, imagenes=pieza.get("imagenes", []), stock=pieza["stock"], marca=pieza.get("marca", "")))
                        continue
                    par = (str(pieza.get("clave")).upper(), " ".join(_sin_acentos(titulo).split()))
                    if par in vistos:
                        deduplicadas += 1; continue
                    vistos.add(par)
                    filas.append(FilaPublicacion(titulo, str(pieza.get("clave") or "").strip(),
                        pieza.get("producto", ""), precio, descripcion, anio, False,
                        pieza.get("imagenes", []), compat.get("fila"), stock=pieza["stock"],
                        marca=pieza.get("marca", "")))
            continue
        if pieza.get("formato") == "master_cauplas":
            descripcion = _describir_cauplas(pieza, config)
            for compat in pieza.get("compatibilidades", []):
                producto = compat.get("producto", "")
                precio = calcular_precio(pieza.get("costo"), producto, config.params_precio)
                titulos, fuera = _variantes_cauplas(producto, compat)
                for anios, motivo in fuera:
                    candidato = _titulo_cauplas(producto, compat, "" if compat.get("universal") else anios,
                                                permitir_exceso=True)
                    exclusion = {**_exclusion_titulo("CAUPLAS", pieza.get("clave"), compat.get("fila"),
                        candidato, compat, anios), "fila": compat.get("fila"), "clave": pieza.get("clave"),
                        "armadora": compat.get("armadora"), "modelo": compat.get("modelo"),
                        "inicio": compat.get("inicio"), "fin": compat.get("fin"), "motivo": motivo}
                    if motivo == "Título excede 60 caracteres":
                        exclusiones.append(_fila_pendiente(exclusion, pieza, candidato, precio, descripcion,
                            "Universal" if compat.get("universal") else anios, stock=pieza["stock"], linea=producto))
                    else:
                        exclusiones.append(exclusion)
                for titulo in titulos:
                    par = (str(pieza.get("clave")).upper(), " ".join(_sin_acentos(titulo).split()))
                    if par in vistos:
                        deduplicadas += 1
                        continue
                    vistos.add(par)
                    filas.append(FilaPublicacion(
                        titulo, str(pieza.get("clave") or "").strip(), producto, precio,
                        descripcion, "Universal" if compat.get("universal") else
                        f"{compat.get('inicio')}-{compat.get('fin')}", False, [], compat.get("fila"),
                        stock=pieza["stock"],
                    ))
            continue
        if pieza.get("formato") != "master_kg":
            for f in generar_filas([pieza], config):
                par = (f.sku.upper(), " ".join(_sin_acentos(f.titulo).split()))
                if par in vistos: deduplicadas += 1
                else: vistos.add(par); filas.append(f)
            continue
        precio = calcular_precio(pieza.get("costo"), pieza.get("linea", ""), config.params_precio)
        descripcion = _describir_master(pieza, config)
        for compat in pieza.get("compatibilidades", []):
            titulos, fuera = _variantes_master(pieza.get("producto"), compat)
            for anios, motivo in fuera:
                candidato = _titulo_master(pieza.get("producto"), compat, anios, permitir_exceso=True)
                exclusion = {**_exclusion_titulo("KG", pieza.get("clave"), compat.get("fila"),
                    candidato, compat, anios), "fila": compat.get("fila"), "clave": pieza.get("clave"),
                    "armadora": compat.get("armadora"), "modelo": compat.get("modelo"),
                    "inicio": compat.get("inicio"), "fin": compat.get("fin"), "motivo": motivo}
                if motivo == "Título excede 60 caracteres":
                    exclusiones.append(_fila_pendiente(exclusion, pieza, candidato, precio, descripcion,
                        compat.get("guia", ""), imagenes=pieza.get("imagenes", []), stock=pieza["stock"]))
                else:
                    exclusiones.append(exclusion)
            for titulo in titulos:
                par = (str(pieza.get("clave")).upper(), " ".join(_sin_acentos(titulo).split()))
                if par in vistos: deduplicadas += 1; continue
                vistos.add(par)
                filas.append(FilaPublicacion(titulo, str(pieza.get("clave") or "").strip(),
                    pieza.get("producto", ""), precio, descripcion, compat.get("guia", ""), False,
                    pieza.get("imagenes", []), compat.get("fila"), stock=pieza["stock"]))
    return filas, {"exclusiones": exclusiones, "deduplicadas": deduplicadas}


def generar_filas(piezas, config: ConfiguracionProveedor,
                  incluir_truncadas: bool = False) -> List[FilaPublicacion]:
    """Expande cada pieza del catálogo en sus N publicaciones."""
    if piezas and piezas[0].get("formato") in {"master_kg", "master_cauplas", "master_kims", "master_gonher", "master_ag", "master_difrisa"}:
        return generar_filas_con_reporte(piezas, config)[0]
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
                stock=pieza["stock"],
            ))

    return filas


def escribir_xlsx(filas: List[FilaPublicacion], config: ConfiguracionProveedor,
                  destino, titulos_por_corregir=None) -> int:
    """Escribe el .xlsx listo para subir a ML."""
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
            ws.cell(f, idx["Envio Gratis(si,no)"], "Si" if fila.precio >= 299.00 else "No")
        ws.cell(f, idx["SKU"], fila.sku)
        ws.cell(f, idx["Descripcion"], fila.descripcion).alignment = Alignment(wrap_text=True)
        ws.cell(f, idx["Marca"], fila.marca or config.marca)
        ws.cell(f, idx["Modelo"], fila.sku)
        limite_imagenes = 10 if bodega in {"CAUPLAS", "AG"} else 5
        for numero, imagen in enumerate(fila.imagenes[:limite_imagenes], 1):
            if imagen: ws.cell(f, idx[f"Imagen{numero}"], imagen)

        for nombre, valor in CONSTANTES.items():
            ws.cell(f, idx[nombre], valor)
        if fila.stock is not None:
            ws.cell(f, idx["Cantidad"], fila.stock)

        # Stock en la bodega del proveedor, 0 en las demás.
        for b in BODEGAS:
            if fila.stock is None and b == bodega:
                continue
            ws.cell(f, idx[b], fila.stock if b == bodega else 0)

        # KG conserva Imagen1..5; CAUPLAS puede usar la galería completa de 10.

    ws.freeze_panes = "A2"
    for col, ancho in (("A", 58), ("M", 60), ("L", 18)):
        ws.column_dimensions[col].width = ancho

    if config.codigo_bodega.strip().upper() in {"AG", "CAUPLAS", "GONHER", "KG", "KIM", "DIFIRSA"}:
        pendientes = wb.create_sheet("Títulos por corregir")
        for col, nombre in enumerate(COLUMNAS, 1):
            celda = pendientes.cell(1, col, nombre)
            celda.font = encabezado
        for numero_fila, caso in enumerate(titulos_por_corregir or [], 2):
            fila = caso.get("fila_publicacion")
            if fila is None:
                continue
            _escribir_fila_publicacion(pendientes, numero_fila, fila, config, idx, bodega)
        pendientes.freeze_panes = "A2"
        for col, ancho in (("A", 58), ("M", 60), ("L", 18)):
            pendientes.column_dimensions[col].width = ancho

    wb.save(destino)
    return len(filas)


def _escribir_fila_publicacion(ws, numero_fila, fila, config, idx, bodega):
    ws.cell(numero_fila, idx["Titulo"], fila.titulo)
    ws.cell(numero_fila, idx["Categoria"], config.categoria_ml)
    if fila.precio is not None:
        ws.cell(numero_fila, idx["Precio"], fila.precio)
        ws.cell(numero_fila, idx["Envio Gratis(si,no)"], "Si" if fila.precio >= 299.00 else "No")
    ws.cell(numero_fila, idx["SKU"], fila.sku)
    ws.cell(numero_fila, idx["Descripcion"], fila.descripcion).alignment = Alignment(wrap_text=True)
    ws.cell(numero_fila, idx["Marca"], fila.marca or config.marca)
    ws.cell(numero_fila, idx["Modelo"], fila.sku)
    limite_imagenes = 10 if bodega in {"CAUPLAS", "AG"} else 5
    for numero, imagen in enumerate(fila.imagenes[:limite_imagenes], 1):
        if imagen:
            ws.cell(numero_fila, idx[f"Imagen{numero}"], imagen)
    for nombre, valor in CONSTANTES.items():
        ws.cell(numero_fila, idx[nombre], valor)
    if fila.stock is not None:
        ws.cell(numero_fila, idx["Cantidad"], fila.stock)
    for b in BODEGAS:
        if fila.stock is None and b == bodega:
            continue
        ws.cell(numero_fila, idx[b], fila.stock if b == bodega else 0)
