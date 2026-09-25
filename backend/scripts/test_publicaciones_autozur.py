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
    ["Jeep", "Liberty", "2002", "Sport", "3.7", "6", "SUV", "Automática", "4WD", "", "V6", "Aspirado"],
    ["Jeep", "Liberty", "2003", "Limited", "3.7", "6", "SUV", "Automática", "4WD", "", "V6", "Aspirado"],
    ["Jeep", "Liberty", "2004", "Renegade", "3.7", "6", "SUV", "Automática", "4WD", "", "V6", "Aspirado"],
    ["Ford", "Ranger", "2003", "Base", "3.7", "6", "SUV", "Automática", "4WD", "", "V6", "Aspirado"],
]
indice = {
    ("CHEVROLET", "CAMARO"): vehiculos[:3],
    ("CHEVROLET", "SPARK"): [vehiculos[3]],
    ("VOLKSWAGEN", "JETTA"): [vehiculos[4]],
    ("JEEP", "LIBERTY"): vehiculos[5:8],
    ("FORD", "RANGER"): [vehiculos[8]],
}
catalogo = CatalogoIndexado(
    por_fabricante_modelo=indice,
    modelos_por_fabricante={"CHEVROLET": ["CAMARO", "SPARK"], "VOLKSWAGEN": ["JETTA"], "JEEP": ["LIBERTY"], "FORD": ["RANGER"]},
    fabricantes_por_modelo={"CAMARO": ["CHEVROLET"], "SPARK": ["CHEVROLET"], "JETTA": ["VOLKSWAGEN"], "LIBERTY": ["JEEP"], "RANGER": ["FORD", "JEEP"]},
    fabricantes=["VOLKSWAGEN", "CHEVROLET", "JEEP", "FORD"],
    filas=9,
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
check("fabricante único inferido queda listo", r_inferida["estado"] == "lista", r_inferida)
check("la inferencia explica el criterio", "Fabricante inferido como CHEVROLET" in r_inferida["motivo"], r_inferida)
check("la lista conserva propuesta", len(r_inferida["candidatos"]) == 1, r_inferida)

jeep = ["4", "Base P/ Liberty 3.7 2002-2004", "L-1"] + [""] * 14
r_jeep = analizar([jeep], catalogo)["resultados"][0]
check("modelo único con variantes queda listo", r_jeep["estado"] == "lista", r_jeep)
check("lista todas las variantes de submodelo", len(r_jeep["candidatos"]) == 3, r_jeep)

compartido = ["5", "Base P/ Ranger 3.7 2003", "L-2"] + [""] * 14
r_compartido = analizar([compartido], catalogo)["resultados"][0]
check("modelo compartido entre marcas queda en revisión", r_compartido["estado"] == "revision", r_compartido)

sin_litros = ["6", "Base P/ Spark 2017", "S-2"] + [""] * 14
check("cilindrada ausente queda en revisión", analizar([sin_litros], catalogo)["resultados"][0]["estado"] == "revision")

motor_aprox = ["7", "Base P/ Chevrolet Camaro 4.0 1975/1976", "C-2"] + [""] * 14
r_aprox = analizar([motor_aprox], catalogo)["resultados"][0]
check("cilindrada sin coincidencia usa propuesta relajada en revisión", r_aprox["estado"] == "revision" and len(r_aprox["candidatos"]) == 3, r_aprox)

v6_incorrecto = ["8", "Base P/ Spark V6 1.2 2017", "S-3"] + [""] * 14
check("cilindros explícitos no coincidentes quedan en revisión", analizar([v6_incorrecto], catalogo)["resultados"][0]["estado"] == "revision")

sin_cilindros = ["9", "Base P/ Spark 1.2 2017", "S-4"] + [""] * 14
check("cilindros ausentes no bloquean cilindrada exacta", analizar([sin_cilindros], catalogo)["resultados"][0]["estado"] == "lista")

submodelo = ["3", "Bomba P/ Volkswagen Jetta Trendline 2.0 2018", "J-1"] + [""] * 14
r_submodelo = analizar([submodelo], catalogo)["resultados"][0]
check("detalle adicional del modelo va a revisión", r_submodelo["estado"] == "revision", r_submodelo)

print("\n=== ARCHIVO DE SALIDA ===")
analisis = analizar([base, inferida, jeep, submodelo], catalogo)
session = {"publicaciones": [base, inferida, jeep, submodelo], **analisis}
with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
    ruta = Path(tmp.name)
try:
    total = escribir_resultado(session, [], [], ruta)
    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    ws = wb.active
    headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    filas = list(ws.iter_rows(min_row=2, values_only=True))
    check("conserva exactamente 17 columnas", tuple(headers) == ENCABEZADOS_PUBLICACIONES, headers)
    check("una fila por compatibilidad", total == 6 and len(filas) == 6, total)
    check("excluye revisiones no aprobadas", all(fila[0] != submodelo[0] for fila in filas), filas)
    check("repite UserProductID, título y SKU", filas[0][:3] == filas[1][:3] == tuple(base[:3]), filas[:2])
    check("llena los atributos disponibles del vehículo", filas[0][3:9] == tuple(vehiculos[0][:6]), filas[0])
    check("conserva combustible vacío sin inventarlo", filas[0][12] is None, filas[0])
    wb.close()
finally:
    ruta.unlink(missing_ok=True)

total_checks = 25
print(f"\n{total_checks - fallos} pasaron · {fallos} fallaron")
raise SystemExit(1 if fallos else 0)
