"""Regresión del flujo ARGENPARTS de publicaciones masivas."""
import os
import sys
import tempfile
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.generador_plantilla import COLUMNAS, ConfiguracionProveedor, escribir_xlsx, generar_filas_con_reporte
from services.imagenes_ag import asignar_imagenes_ag, leer_imagenes_ag
from services.parser_catalogo import leer_catalogo_detallado
from services.perfiles_catalogo import perfil_de
from services.validacion_imagenes import _es_url_permitida

ok = bad = 0
def check(nombre, condicion):
    global ok, bad
    print(("  ✓ " if condicion else "  ✗ ") + nombre)
    ok += bool(condicion); bad += not bool(condicion)

headers = ["Producto", "Código", "SYD", "GROB", "KYB", "BOGE", "VAZLO", "Marca", "Aplicación", "Desde", "Hasta", "Posición", "Distribuidor", "stock"]
wb = openpyxl.Workbook(); ws = wb.active; ws.append(headers)
ws.append(["Amortiguador delantero", 1234, "S1", "", "K1", "", "V1", "Ford", "Focus (Europa)", 2012, 2015, "Delantero izquierdo", 100, 3])
ws.append(["Amortiguador delantero", "1234", "S2", "", "K1", "", "V1", "Ford", "Focus", 2016, 2018, "Delantero derecho", 100, 3])
ws.append(["Amortiguador", "BAD", "", "", "", "", "", "Ford", "Fiesta", 2020, 2019, "Delantero", 50, 1])
f = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False); f.close(); wb.save(f.name)
r = leer_catalogo_detallado(f.name, perfil_de("AG")); os.unlink(f.name)
check("detecta master AG por encabezados", r.formato == "master_ag")
check("Código numérico conserva SKU textual", r.piezas[0]["clave"] == "1234")
check("consolida equivalencias y compatibilidades", len(r.piezas[0]["compatibilidades"]) == 2 and r.piezas[0]["equivalencias"]["SYD"] == ["S1", "S2"])
check("años invertidos se excluyen y reportan", r.compatibilidades_invalidas == 1 and r.errores)
filas, reporte = generar_filas_con_reporte(r.piezas, ConfiguracionProveedor(codigo_bodega="AG", marca="ARGENPARTS"))
check("precio usa costo antes de IVA y genera variantes", len(filas) == 2 and filas[0].precio is not None)
check("títulos AG no exceden 60 y conservan P/", all(len(x.titulo) <= 60 and "P/" in x.titulo for x in filas))
check("descripción conserva notas parentéticas", "(Europa)" in filas[0].descripcion)

fotos = openpyxl.Workbook(); hoja = fotos.active; hoja.append(["Servidor", "Carpeta", "Nombre", "Extensión"])
for n in ("1234", "1234_2", "1234-3", "1234_11", "KIT 1234", "SINMATCH"):
    hoja.append(["https://apimagenes.s3.amazonaws.com", "AG", n, "jpg"])
ff = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False); ff.close(); fotos.save(ff.name)
galeria, resumen = leer_imagenes_ag(ff.name, ["1234"]); os.unlink(ff.name)
check("fotos: principal, guion bajo y guion respetan orden", galeria["1234"][:3] == ["https://apimagenes.s3.amazonaws.com/AG/1234.jpg", "https://apimagenes.s3.amazonaws.com/AG/1234_2.jpg", "https://apimagenes.s3.amazonaws.com/AG/1234-3.jpg"])
check("fotos omite kit, fuera de límite y SKU inexistente", resumen["urls_omitidas"] == 3)

# Encabezados reales de FOTOS AG.xlsx. La primera hoja trae ``2`` en vez de
# ``URL servidor``: URL FINAL debe funcionar aunque ese campo no sea usable.
fotos_reales = openpyxl.Workbook(); hoja = fotos_reales.active
hoja.append([2, "Nombre de la carpeta", "Nombre de la foto", "Formato de la foto", "URL FINAL"])
hoja.append(["https://servidor-antiguo.example", "confort2024", "1234", ".png",
             "https://apimagenes.s3.amazonaws.com/confort2024/1234.jpg"])
hoja.append(["https://servidor-antiguo.example", "confort2024", "1234_2", ".png",
             "https://apimagenes.s3.amazonaws.com/confort2024/1234_2.jpg"])
ff = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False); ff.close(); fotos_reales.save(ff.name)
galeria_real, resumen_real = leer_imagenes_ag(ff.name, ["1234"]); os.unlink(ff.name)
check("fotos reconoce encabezados reales de AG y URL FINAL", galeria_real["1234"][:2] == [
    "https://apimagenes.s3.amazonaws.com/confort2024/1234.jpg",
    "https://apimagenes.s3.amazonaws.com/confort2024/1234_2.jpg",
] and resumen_real["urls_cruzadas"] == 2)
check("fotos AG sólo permiten el host HTTPS exacto", _es_url_permitida(
    "https://apimagenes.s3.amazonaws.com/AG/1234.jpg", ("apimagenes.s3.amazonaws.com",)) and not _es_url_permitida(
    "https://cdn.apimagenes.s3.amazonaws.com/AG/1234.jpg", ("apimagenes.s3.amazonaws.com",)))
asignar_imagenes_ag(filas, galeria)
out = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False); out.close(); escribir_xlsx(filas, ConfiguracionProveedor(codigo_bodega="AG", marca="ARGENPARTS"), out.name)
salida = openpyxl.load_workbook(out.name, data_only=True); hoja = salida.active
check("XLSX conserva 37 columnas, Cantidad y bodega AG", [c.value for c in hoja[1]] == COLUMNAS and hoja.cell(2, 5).value == 3 and hoja.cell(2, 31).value == 3 and hoja.cell(2, 15).value)
salida.close(); os.unlink(out.name)
print(f"\n{ok} pasaron · {bad} fallaron")
raise SystemExit(bool(bad))
