"""Validación acotada y segura de imágenes de catálogos de proveedores.

Las URLs vienen de Excel: sólo se consulta un host declarado en el perfil y
se leen los primeros bytes necesarios para confirmar formato y resolución.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Callable, Dict, Iterable, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

TIMEOUT_SEGUNDOS = 5
MAX_TRABAJADORES = 16
MAX_URLS_POR_DESCARGA = 25_000
LIMITE_TOTAL_SEGUNDOS = 240
BYTES_METADATOS = 65_536
MINIMO_PIXELES = 1200

VALIDA = "valida"
NO_DISPONIBLE = "no_disponible"
RESOLUCION_INSUFICIENTE = "resolucion_insuficiente"
DOMINIO_NO_AUTORIZADO = "dominio_no_autorizado"
FORMATO_NO_VERIFICABLE = "formato_no_verificable"


class _NoSeguirRedirecciones(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _es_url_permitida(url: str, dominios_permitidos: Iterable[str]) -> bool:
    """Valida el destino antes de abrir una conexión (protección SSRF)."""
    hosts = {str(host).casefold() for host in dominios_permitidos}
    try:
        partes = urlsplit(str(url).strip())
        return (partes.scheme == "https" and partes.hostname is not None
                and partes.hostname.casefold() in hosts
                and partes.port in (None, 443)
                and not partes.username and not partes.password)
    except ValueError:
        return False


def _dimensiones_jpeg(datos: bytes) -> Optional[Tuple[int, int]]:
    if not datos.startswith(b"\xff\xd8"):
        return None
    pos = 2
    while pos + 9 <= len(datos):
        if datos[pos] != 0xff:
            pos += 1
            continue
        while pos < len(datos) and datos[pos] == 0xff:
            pos += 1
        if pos >= len(datos):
            break
        marcador = datos[pos]
        pos += 1
        if marcador in (0xd8, 0xd9) or 0xd0 <= marcador <= 0xd7:
            continue
        if pos + 2 > len(datos):
            break
        largo = int.from_bytes(datos[pos:pos + 2], "big")
        if largo < 2 or pos + largo > len(datos):
            break
        if marcador in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
                        0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf} and largo >= 8:
            return (int.from_bytes(datos[pos + 5:pos + 7], "big"),
                    int.from_bytes(datos[pos + 3:pos + 5], "big"))
        pos += largo
    return None


def dimensiones_imagen(datos: bytes) -> Optional[Tuple[int, int]]:
    """Extrae resolución de JPEG, PNG, WebP o GIF sin decodificar la foto."""
    if datos.startswith(b"\x89PNG\r\n\x1a\n") and len(datos) >= 24 and datos[12:16] == b"IHDR":
        return (int.from_bytes(datos[16:20], "big"), int.from_bytes(datos[20:24], "big"))
    if datos.startswith((b"GIF87a", b"GIF89a")) and len(datos) >= 10:
        return (int.from_bytes(datos[6:8], "little"), int.from_bytes(datos[8:10], "little"))
    if datos.startswith(b"RIFF") and len(datos) >= 30 and datos[8:12] == b"WEBP":
        tipo = datos[12:16]
        if tipo == b"VP8X":
            return (int.from_bytes(datos[24:27], "little") + 1,
                    int.from_bytes(datos[27:30], "little") + 1)
        if tipo == b"VP8 " and datos[23:26] == b"\x9d\x01\x2a":
            return (int.from_bytes(datos[26:28], "little") & 0x3fff,
                    int.from_bytes(datos[28:30], "little") & 0x3fff)
        if tipo == b"VP8L" and len(datos) >= 25 and datos[20] == 0x2f:
            bits = int.from_bytes(datos[21:25], "little")
            return ((bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1)
    return _dimensiones_jpeg(datos)


def validar_imagen(url: str, dominios_permitidos: Iterable[str]) -> str:
    if not _es_url_permitida(url, dominios_permitidos):
        return DOMINIO_NO_AUTORIZADO
    request = Request(url, method="GET", headers={"User-Agent": "RELUVSA-Portal/1.0",
                                                    "Range": f"bytes=0-{BYTES_METADATOS - 1}"})
    try:
        with build_opener(_NoSeguirRedirecciones).open(request, timeout=TIMEOUT_SEGUNDOS) as respuesta:
            content_type = respuesta.headers.get_content_type().casefold()
            if not (200 <= respuesta.status < 300 and content_type.startswith("image/")):
                return NO_DISPONIBLE
            dimensiones = dimensiones_imagen(respuesta.read(BYTES_METADATOS))
    except (HTTPError, URLError, OSError, ValueError):
        return NO_DISPONIBLE
    if dimensiones is None:
        return FORMATO_NO_VERIFICABLE
    return VALIDA if min(dimensiones) >= MINIMO_PIXELES else RESOLUCION_INSUFICIENTE


def filtrar_imagenes(filas: Iterable, dominios_permitidos: Iterable[str],
                     comprobar_url: Callable[[str], object] = None,
                     limite_urls: int = MAX_URLS_POR_DESCARGA,
                     limite_segundos: int = LIMITE_TOTAL_SEGUNDOS) -> Dict[str, int]:
    """Quita URLs inválidas sin desplazar las posiciones Imagen 1–5."""
    filas = list(filas)
    urls = {url.strip() for fila in filas for url in fila.imagenes if isinstance(url, str) and url.strip()}
    conteo = {"revisadas": len(urls), "validas": 0, "no_disponibles": 0,
              "resolucion_insuficiente": 0, "dominio_no_autorizado": 0,
              "formato_no_verificable": 0, "eliminadas": 0}
    if not urls:
        return conteo
    if len(urls) > limite_urls:
        raise ValueError(f"El catálogo contiene {len(urls):,} URLs únicas; el máximo es {limite_urls:,}. Divide la descarga por categoría.")

    def comprobar(url):
        resultado = comprobar_url(url) if comprobar_url else validar_imagen(url, dominios_permitidos)
        return VALIDA if resultado is True else (NO_DISPONIBLE if resultado is False else resultado)

    resultados = {}
    executor = ThreadPoolExecutor(max_workers=MAX_TRABAJADORES)
    try:
        futuros = {executor.submit(comprobar, url): url for url in urls}
        try:
            for futuro in as_completed(futuros, timeout=limite_segundos):
                try:
                    resultados[futuros[futuro]] = futuro.result()
                except Exception:
                    resultados[futuros[futuro]] = NO_DISPONIBLE
        except FuturesTimeoutError:
            pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    # Al vencer el plazo global, las URLs pendientes también se excluyen y se
    # reflejan en el desglose, no sólo en el total de eliminadas.
    for url in urls:
        resultados.setdefault(url, NO_DISPONIBLE)
    for estado in resultados.values():
        if estado == VALIDA:
            conteo["validas"] += 1
        elif estado == RESOLUCION_INSUFICIENTE:
            conteo["resolucion_insuficiente"] += 1
        elif estado == DOMINIO_NO_AUTORIZADO:
            conteo["dominio_no_autorizado"] += 1
        elif estado == FORMATO_NO_VERIFICABLE:
            conteo["formato_no_verificable"] += 1
        else:
            conteo["no_disponibles"] += 1
    conteo["eliminadas"] = conteo["revisadas"] - conteo["validas"]
    validas = {url for url, estado in resultados.items() if estado == VALIDA}
    for fila in filas:
        fila.imagenes = [url if not isinstance(url, str) or not url.strip() or url.strip() in validas else "" for url in fila.imagenes]
    return conteo


# Compatibilidad para consumidores previos; la política nueva vive en perfiles.
def filtrar_imagenes_kg(filas: Iterable, comprobar_url: Callable[[str], object] = None, **kwargs) -> Dict[str, int]:
    return filtrar_imagenes(filas, ("kgmedia.mx",), comprobar_url, **kwargs)
