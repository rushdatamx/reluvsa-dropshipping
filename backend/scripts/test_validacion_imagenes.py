"""Casos unitarios del candado universal de imágenes para publicaciones."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import services.validacion_imagenes as validacion
from services.generador_plantilla import FilaPublicacion
from services.validacion_imagenes import (
    DOMINIO_NO_AUTORIZADO, FORMATO_NO_VERIFICABLE, RESOLUCION_INSUFICIENTE,
    VALIDA, _es_url_permitida, dimensiones_imagen, filtrar_imagenes,
)

ok = fail = 0
def check(nombre, condicion, detalle=""):
    global ok, fail
    if condicion:
        ok += 1; print("✅", nombre)
    else:
        fail += 1; print("❌", nombre, detalle)

def png(ancho, alto):
    return b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + ancho.to_bytes(4, "big") + alto.to_bytes(4, "big")

def gif(ancho, alto): return b"GIF89a" + ancho.to_bytes(2, "little") + alto.to_bytes(2, "little")
def webp(ancho, alto): return b"RIFF" + b"\0\0\0\0WEBPVP8X" + b"\0" * 8 + (ancho - 1).to_bytes(3, "little") + (alto - 1).to_bytes(3, "little")
def jpeg(ancho, alto): return b"\xff\xd8\xff\xc0\x00\x11\x08" + alto.to_bytes(2, "big") + ancho.to_bytes(2, "big") + b"\x03" + b"\0" * 9

for nombre, datos in (("PNG", png(1200, 1200)), ("GIF", gif(1200, 1200)),
                      ("WebP", webp(1200, 1200)), ("JPEG", jpeg(1200, 1200))):
    check(f"{nombre} reconoce 1200×1200", dimensiones_imagen(datos) == (1200, 1200), dimensiones_imagen(datos))
check("JPEG de 1199×1200 queda bajo el mínimo", min(dimensiones_imagen(jpeg(1199, 1200))) < 1200)
check("PNG de 1200×1199 queda bajo el mínimo", min(dimensiones_imagen(png(1200, 1199))) < 1200)
check("datos ilegibles no inventan resolución", dimensiones_imagen(b"no es imagen") is None)

permitidos = ("proveedor.example",)
check("acepta host exacto HTTPS", _es_url_permitida("https://proveedor.example/a.jpg", permitidos))
for etiqueta, url in (("host ajeno", "https://otro.example/a.jpg"), ("HTTP", "http://proveedor.example/a.jpg"),
                      ("puerto alterno", "https://proveedor.example:444/a.jpg"),
                      ("credenciales", "https://u:p@proveedor.example/a.jpg"),
                      ("subdominio", "https://cdn.proveedor.example/a.jpg")):
    check(f"rechaza {etiqueta}", not _es_url_permitida(url, permitidos))

class Headers:
    def get_content_type(self): return "image/png"
class Respuesta:
    status = 206
    headers = Headers()
    def __init__(self, datos): self.datos = datos
    def read(self, limite): return self.datos[:limite]
    def __enter__(self): return self
    def __exit__(self, *args): return False
class Opener:
    def __init__(self, datos): self.datos = datos; self.request = None
    def open(self, request, timeout): self.request = request; return Respuesta(self.datos)

original_opener = validacion.build_opener
opener = Opener(png(1200, 1200)); validacion.build_opener = lambda *args: opener
try:
    check("GET parcial acepta una imagen de resolución suficiente", validacion.validar_imagen("https://proveedor.example/a.png", permitidos) == VALIDA)
    check("GET pide únicamente el rango de metadatos", opener.request.get_header("Range") == "bytes=0-65535", opener.request.headers)
    opener.datos = png(1199, 1200)
    check("GET rechaza resolución insuficiente", validacion.validar_imagen("https://proveedor.example/b.png", permitidos) == RESOLUCION_INSUFICIENTE)
    opener.datos = b"imagen no interpretable"
    check("GET rechaza formato no verificable", validacion.validar_imagen("https://proveedor.example/c.png", permitidos) == FORMATO_NO_VERIFICABLE)
    check("host no autorizado no abre conexión", validacion.validar_imagen("https://ajeno.example/x", permitidos) == DOMINIO_NO_AUTORIZADO)
finally:
    validacion.build_opener = original_opener

filas = [FilaPublicacion("uno", "A", "", 1, "", "", imagenes=["https://proveedor.example/a", "https://otro.example/b", "https://proveedor.example/c", "https://proveedor.example/d"])]
llamadas = []
estados = {"https://proveedor.example/a": VALIDA, "https://otro.example/b": DOMINIO_NO_AUTORIZADO,
           "https://proveedor.example/c": RESOLUCION_INSUFICIENTE, "https://proveedor.example/d": FORMATO_NO_VERIFICABLE}
def falso(url): llamadas.append(url); return estados[url]
r = filtrar_imagenes(filas, permitidos, falso)
check("deduplica y conserva posiciones", filas[0].imagenes == ["https://proveedor.example/a", "", "", ""], filas[0].imagenes)
check("desglosa causas", r["validas"] == 1 and r["dominio_no_autorizado"] == 1 and r["resolucion_insuficiente"] == 1 and r["formato_no_verificable"] == 1 and r["eliminadas"] == 3, r)

print(f"{ok} pasaron · {fail} fallaron")
sys.exit(bool(fail))
