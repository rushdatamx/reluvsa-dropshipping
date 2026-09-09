"""Regresión del módulo Publicaciones Autozur (sin red ni API de ML)."""
import tempfile
from pathlib import Path

import openpyxl

from services.autozur_compatibilidades import (
    CatalogoIndexado, ENCABEZADOS_PUBLICACIONES, analizar, escribir_resultado,
    interpretar_titulo,
)


fallos = 0


def check(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  ✓ {nombre}")
    else:
        fallos += 1
        print(f"  ✗ {nombre}: {detalle}")


vehiculos = [
    ["Chevrolet", "Camaro", "1975", "Base", "4.1", "6", "Coupe", "Manual", "RWD", "", "OHV", "Aspirado"],
    ["Chevrolet", "Camaro", "1976", "Sport", "4.1", "6", "Coupe", "Manual", "RWD", "", "OHV", "Aspirado"],
    ["Chevrolet", "Camaro", "1976", "Base", "5.0", "8", "Coupe", "Manual", "RWD", "", "OHV", "Aspirado"],
    ["Chevrolet", "Spark", "2017", "LT", "1.2", "4", "Hatchback", "Manual", "FWD", "", "DOHC", "Aspirado"],
    ["Volkswagen", "Jetta", "2018", "Trendline", "2.0", "4", "Sedan", "Manual", "FWD", "", "SOHC", "Aspirado"],
]
indice = {
    ("CHEVROLET", "CAMARO"): vehiculos[:3],
    ("CHEVROLET", "SPARK"): [vehiculos[3]],
    ("VOLKSWAGEN", "JETTA"): [vehiculos[4]],
}
catalogo = CatalogoIndexado(
    por_fabricante_modelo=indice,
    modelos_por_fabricante={"CHEVROLET": ["CAMARO", "SPARK"], "VOLKSWAGEN": ["JETTA"]},
    fabricantes_por_modelo={"CAMARO": ["CHEVROLET"], "SPARK": ["CHEVROLET"], "JETTA": ["VOLKSWAGEN"]},
    fabricantes=["VOLKSWAGEN", "CHEVROLET"],
    filas=5,
)

print("\n=== PARSER Y CRUCE CONSERVADOR ===")
p = interpretar_titulo("Bomba De Agua P/ Chevrolet Camaro 4.1 1975/1976", catalogo)
check("extrae fabricante", p["fabricante"] == "CHEVROLET", p)
check("extrae modelo", p["modelo"] == "CAMARO", p)
check("extrae litros y rango", p["litros"] == "4.1" and p["anio_desde"] == 1975 and p["anio_hasta"] == 1976, p)

base = ["1", "Bomba De Agua P/ Chevrolet Camaro 4.1 1975/1976", "B-1"] + [""] * 14
resultado = analizar([base], catalogo)
check("cruce exacto queda listo", resultado["resultados"][0]["estado"] == "lista", resultado)
check("una publicación obtiene dos compatibilidades", len(resultado["resultados"][0]["candidatos"]) == 2, resultado)

inferida = ["2", "Base P/ Spark 1.2 2017", "S-1"] + [""] * 14
r_inferida = analizar([inferida], catalogo)["resultados"][0]
check("fabricante inferido va a revisión", r_inferida["estado"] == "revision", r_inferida)
check("la revisión conserva propuesta", len(r_inferida["candidatos"]) == 1, r_inferida)

submodelo = ["3", "Bomba P/ Volkswagen Jetta Trendline 2.0 2018", "J-1"] + [""] * 14
r_submodelo = analizar([submodelo], catalogo)["resultados"][0]
check("detalle adicional del modelo va a revisión", r_submodelo["estado"] == "revision", r_submodelo)

print("\n=== ARCHIVO DE SALIDA ===")
analisis = analizar([base, inferida], catalogo)
session = {"publicaciones": [base, inferida], **analisis}
with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
    ruta = Path(tmp.name)
try:
    total = escribir_resultado(session, [1], [], ruta)
    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    ws = wb.active
    headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    filas = list(ws.iter_rows(min_row=2, values_only=True))
    check("conserva exactamente 17 columnas", tuple(headers) == ENCABEZADOS_PUBLICACIONES, headers)
    check("una fila por compatibilidad", total == 3 and len(filas) == 3, total)
    check("repite UserProductID, título y SKU", filas[0][:3] == filas[1][:3] == tuple(base[:3]), filas[:2])
    check("llena los atributos disponibles del vehículo", filas[0][3:9] == tuple(vehiculos[0][:6]), filas[0])
    check("conserva combustible vacío sin inventarlo", filas[0][12] is None, filas[0])
    wb.close()
finally:
    ruta.unlink(missing_ok=True)

print(f"\n{13 - fallos} pasaron · {fallos} fallaron")
raise SystemExit(1 if fallos else 0)
