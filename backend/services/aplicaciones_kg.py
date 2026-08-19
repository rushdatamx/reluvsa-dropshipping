"""
Intérprete de la columna "Aplicaciones Principales" del catálogo de proveedor.

Es el corazón del Módulo 2: de esta columna sale TODO lo que hace masiva una
publicación — los títulos (una publicación por aplicación), la sección
"Compatibilidades" de la descripción y el conteo de filas que se generan.

El formato real de KeepOnGreen (medido sobre 3,676 piezas / 7,219 aplicaciones):

    GM AVALANCHE V8 5.3L 2007-2010 | V8 6.0L 2007-2009 | SILVERADO 1500 V8 4.8L 2007-2010

o sea `MARCA MODELO MOTOR AÑOS`, separado por `|` (2,144 piezas) o por salto de
línea (9 piezas). 1,523 piezas traen una sola aplicación y no llevan separador.

🔴 LA MARCA Y EL MODELO SE HEREDAN de la aplicación anterior. En el ejemplo de
arriba, `V8 6.0L 2007-2009` NO es un modelo llamado "V8": es el MISMO Avalanche
con otro motor. Y `SILVERADO 1500` sigue siendo GM aunque no lo repita. Sin la
herencia se generan títulos basura ("Bomba de agua V8 6.0L") y compatibilidades
que mienten sobre a qué coche le sirve la pieza.

⚠️ El catálogo trae ~95 aplicaciones CORTADAS a media palabra (el export del
proveedor recorta la celda a 90 caracteres): quedan restos como 'CA', 'V8' o
'KA L4 1.6L 2'. No se adivina lo que falta — se marcan como `truncada` para que
la UI las muestre aparte y Gaby decida. Publicar una pieza diciendo que sirve
para menos autos de los que sirve es peor que no publicarla.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

# Separadores reales del catálogo: pipe (lo normal) y salto de línea (9 piezas).
_SEPARADORES = re.compile(r"[|\n]+")

# Un motor: 'L4 1.6L', 'V6 3.5L', 'V8 5.3L', y variantes con sufijo ('TFSI', 'Diesel').
# El punto decimal a veces viene con la letra O en vez del cero ('2.OL'): se tolera.
_MOTOR = re.compile(
    r"\b(?P<cil>[LVWIH]\s?\d{1,2})\s+(?P<lts>\d[.,][\dOo])\s*L\b",
    re.IGNORECASE,
)

# Los años: '2007-2010', '2012/2017', '98-00' (2 dígitos) o un año suelto '2002'.
_RANGO_ANIOS = re.compile(r"\b(\d{4}|\d{2})\s*[-/]\s*(\d{4}|\d{2})\b")
_ANIO_SUELTO = re.compile(r"\b(19|20)\d{2}\b")

# Una aplicación que quedó en un resto sin sentido tras el corte del export.
# Son fragmentos cortos y sin años; no se intenta reconstruirlos.
_LARGO_MIN_UTIL = 6

# Marcas que el catálogo abrevia. Se necesitan para saber si un fragmento TRAE su
# propia marca o si la hereda del anterior:
#     'ST CORDOBA ... | IBIZA ... | VW CROSSFOX ...'
#      ^marca propia    ^hereda ST   ^marca propia (NO es un Seat)
# Sin esta lista el tercero saldría como 'ST VW Crossfox' — una marca que se
# contradice a sí misma — y 'DGE ATOS | DGE ATOS' duplicaría la marca.
# 🔴 Es una lista CERRADA a propósito: el primer token de una aplicación no
# siempre es marca. En el catálogo de KG hay filas que arrancan con el código de
# la pieza ('MANG', 'RAD', 'TA', 'BA'); tomarlos por marca metería basura en el
# título. Ante la duda, se prefiere heredar.
_MARCAS = {
    "GM", "FD", "CHR", "NSN", "DGE", "VW", "TY", "HD", "AUD", "PEU", "MIT",
    "BUI", "BMW", "CAD", "JP", "ST", "ACU", "MZD", "REN", "FT", "KC", "MB",
    "INF", "VOL", "LR", "SUB", "MINI", "PON", "LIN", "KIA", "MER", "ISU",
    "SZK", "PGA", "CHEVROLET", "FORD", "VOLKSWAGEN", "HYUNDAI", "NISSAN",
    "DODGE", "HONDA", "CATERPILLAR", "MERCEDES", "CUMMINS", "FREIGHTLINER",
    "SEAT", "AUDI", "TOYOTA", "MAZDA", "JEEP", "RAM", "FIAT", "PORSCHE",
    "JAGUAR", "VOLVO", "SUZUKI", "INFINITI", "ACURA", "BUICK", "CADILLAC",
    "LINCOLN", "PONTIAC", "RENAULT", "PEUGEOT", "MITSUBISHI", "SUBARU",
}


@dataclass
class Aplicacion:
    """Una aplicación = una publicación de Mercado Libre."""

    texto: str                       # el fragmento original, tal cual vino
    marca: Optional[str] = None      # heredada si el fragmento no la repite
    modelo: Optional[str] = None     # heredado si sólo cambia el motor
    motor: Optional[str] = None      # 'L4 1.6L'
    anio_desde: Optional[int] = None
    anio_hasta: Optional[int] = None
    truncada: bool = False           # el export del proveedor la cortó

    @property
    def anios(self) -> str:
        """Los años como los escribe Gaby en el título: '2016 2017 2018'."""
        if self.anio_desde is None:
            return ""
        if self.anio_hasta is None or self.anio_hasta == self.anio_desde:
            return str(self.anio_desde)
        # Rangos largos se abrevian con '/' como hace ella ('2012/2017').
        if self.anio_hasta - self.anio_desde >= 5:
            return f"{self.anio_desde}/{self.anio_hasta}"
        return " ".join(str(a) for a in range(self.anio_desde, self.anio_hasta + 1))

    @property
    def vehiculo(self) -> str:
        """'Truck H200 2.5' — lo que identifica al coche en el título."""
        partes = [p for p in (self.marca, self.modelo) if p]
        return " ".join(partes).strip()


@dataclass
class PiezaAplicaciones:
    """El resultado de interpretar la celda de una pieza."""

    aplicaciones: List[Aplicacion] = field(default_factory=list)
    truncadas: int = 0

    @property
    def utiles(self) -> List[Aplicacion]:
        """Las que sirven para generar publicación (las cortadas no)."""
        return [a for a in self.aplicaciones if not a.truncada]


def _normalizar_litros(txt: str) -> str:
    """'2.OL' -> '2.0L'. El catálogo confunde la O con el cero."""
    return re.sub(r"(\d[.,])[Oo]", r"\g<1>0", txt)


def _expandir_anio(v: str, referencia: Optional[int] = None) -> int:
    """'98' -> 1998, '07' -> 2007, '2016' -> 2016."""
    n = int(v)
    if len(v) == 4:
        return n
    # Dos dígitos: 90-99 son los noventa, el resto son los dos miles.
    return 1900 + n if n >= 90 else 2000 + n


def _parse_una(texto: str) -> Aplicacion:
    """Extrae motor y años de un fragmento; marca/modelo los resuelve la herencia."""
    limpio = _normalizar_litros(re.sub(r"\s+", " ", texto.strip()))
    app = Aplicacion(texto=limpio)

    resto = limpio
    m = _MOTOR.search(limpio)
    if m:
        cil = re.sub(r"\s+", "", m.group("cil")).upper()
        lts = m.group("lts").replace(",", ".")
        app.motor = f"{cil} {lts}L"
        resto = limpio[: m.start()]

    rango = _RANGO_ANIOS.search(limpio)
    if rango:
        app.anio_desde = _expandir_anio(rango.group(1))
        app.anio_hasta = _expandir_anio(rango.group(2))
    else:
        suelto = _ANIO_SUELTO.search(limpio)
        if suelto:
            app.anio_desde = app.anio_hasta = int(suelto.group(0))

    # Lo que queda antes del motor es marca + modelo.
    cabeza = resto.strip(" -,")
    app.modelo = cabeza or None

    # Truncada: SIN AÑOS no sirve para publicar. El título de Gaby siempre lleva
    # años ('... 2016 2017 2018') y la compatibilidad sin años no le dice nada al
    # comprador. Los restos del corte a 90 caracteres ('CA', 'V8', 'SEBRING V6
    # 3.5L') caen todos aquí: el export cortó justo antes del rango de años.
    if app.anio_desde is None:
        app.truncada = True

    return app


def parse_aplicaciones(celda) -> PiezaAplicaciones:
    """Interpreta la celda 'Aplicaciones Principales' de UNA pieza.

    Devuelve una `Aplicacion` por cada publicación que habría que crear, ya con
    la marca y el modelo heredados de la aplicación anterior cuando el catálogo
    no los repite.
    """
    resultado = PiezaAplicaciones()
    if celda is None:
        return resultado

    fragmentos = [f.strip() for f in _SEPARADORES.split(str(celda)) if f.strip()]
    if not fragmentos:
        return resultado

    marca_actual: Optional[str] = None
    modelo_actual: Optional[str] = None

    for i, frag in enumerate(fragmentos):
        app = _parse_una(frag)

        cabeza = app.modelo or ""
        tokens = cabeza.split()

        if i == 0:
            # La primera aplicación SÍ trae la marca: es el primer token.
            if tokens:
                marca_actual = tokens[0]
                modelo_actual = " ".join(tokens[1:]) or None
            app.marca, app.modelo = marca_actual, modelo_actual
        elif tokens:
            if tokens[0].upper() in _MARCAS:
                # Trae su PROPIA marca: cambia de marca y de modelo.
                marca_actual = tokens[0]
                modelo_actual = " ".join(tokens[1:]) or None
            else:
                # Modelo nuevo de la MISMA marca ('IBIZA' tras 'ST CORDOBA').
                modelo_actual = cabeza
            app.marca, app.modelo = marca_actual, modelo_actual
        else:
            # Sin texto propio ('V8 6.0L 2007-2009'): mismo coche, otro motor.
            app.marca, app.modelo = marca_actual, modelo_actual

        resultado.aplicaciones.append(app)

    resultado.truncadas = sum(1 for a in resultado.aplicaciones if a.truncada)
    return resultado
