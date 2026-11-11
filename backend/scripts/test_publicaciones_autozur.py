"""Regresión del módulo Publicaciones Autozur (sin red ni API de ML)."""
import tempfile
from pathlib import Path

import openpyxl

from services.autozur_compatibilidades import (
    CatalogoIndexado, ENCABEZADOS_PUBLICACIONES, analizar, escribir_resultado,
    interpretar_titulo,
)


fallos = 0
total_checks = 0


def check(nombre, condicion, detalle=""):
    global fallos, total_checks
    total_checks += 1
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
    ["Renault", "Duster", "2019", "Intens", "2.0", "4", "SUV", "Manual", "FWD", "", "DOHC", "Aspirado"],
    ["Renault", "Trafic", "2018", "II", "1.6", "4", "Van", "Manual", "FWD", "", "DOHC", "Turbo"],
]
indice = {
    ("CHEVROLET", "CAMARO"): vehiculos[:3],
    ("CHEVROLET", "SPARK"): [vehiculos[3]],
    ("VOLKSWAGEN", "JETTA"): [vehiculos[4]],
    ("JEEP", "LIBERTY"): vehiculos[5:8],
    ("FORD", "RANGER"): [vehiculos[8]],
    ("RENAULT", "DUSTER"): [vehiculos[9]],
    ("RENAULT", "TRAFIC"): [vehiculos[10]],
    ("OPEL", "DUSTER"): [["Opel", "Duster", "2019", "Base", "1.6", "4", "SUV", "Manual", "FWD", "", "DOHC", "Aspirado"]],
}
catalogo = CatalogoIndexado(
    por_fabricante_modelo=indice,
    modelos_por_fabricante={"CHEVROLET": ["CAMARO", "SPARK"], "VOLKSWAGEN": ["JETTA"], "JEEP": ["LIBERTY"], "FORD": ["RANGER"], "RENAULT": ["DUSTER", "TRAFIC"], "OPEL": ["DUSTER"]},
    fabricantes_por_modelo={"CAMARO": ["CHEVROLET"], "SPARK": ["CHEVROLET"], "JETTA": ["VOLKSWAGEN"], "LIBERTY": ["JEEP"], "RANGER": ["FORD", "JEEP"], "DUSTER": ["RENAULT", "OPEL"], "TRAFIC": ["RENAULT"]},
    fabricantes=["VOLKSWAGEN", "CHEVROLET", "JEEP", "FORD", "RENAULT", "OPEL"],
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

suspension = ["S1", "Horquilla Chevrolet Camaro 1976", "S-SKU"] + [""] * 14
r_suspension = analizar([suspension, base], catalogo, "suspension")
check("apartado suspensión descarta publicaciones generales", r_suspension["resumen"]["publicaciones"] == 1 and r_suspension["resultados"][0]["titulo"] == suspension[1], r_suspension)
check("horquilla propone todas las variantes de motor por modelo y año", {f[4] for f in r_suspension["resultados"][0]["candidatos"]} == {"4.1", "5.0"}, r_suspension)
sin_marca_suspension = ["S2", "Amortiguador Camaro 1976", "S-SKU"] + [""] * 14
check("marca ausente queda para revisión", analizar([sin_marca_suspension], catalogo, "suspension")["resultados"][0]["estado"] == "revision")
check("resortes se detectan como suspensión", analizar([["S3", "Resortes Volkswagen Jetta 2018", "S-SKU"] + [""] * 14], catalogo, "suspension")["resumen"]["publicaciones"] == 1)

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
check("modelo compartido se desambigua con motor/año", r_compartido["estado"] == "lista", r_compartido)

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
check("descriptor de submodelo con motor exacto queda listo", r_submodelo["estado"] == "lista", r_submodelo)

duster = ["10", "Base P/ Duster 2.0 2019", "D-1"] + [""] * 14
r_duster = analizar([duster], catalogo)["resultados"][0]
check("Duster sin marca infiere Renault al filtrar motor/año", r_duster["estado"] == "lista" and r_duster["extraido"]["fabricante"] == "RENAULT", r_duster)
trafic = ["11", "Bomba P/ Renault Trafic II 1.6 2018", "T-1"] + [""] * 14
r_trafic = analizar([trafic], catalogo)["resultados"][0]
check("Trafic II conserva coincidencia exacta", r_trafic["estado"] == "lista" and len(r_trafic["candidatos"]) == 1, r_trafic)

# Catálogo sintético adicional para colisiones y generaciones.
for marca, modelo, litros in [("NISSAN", "TSURU", "1.6"), ("NISSAN", "TSURU", "2"),
                              ("MAZDA", "2", "1.5"), ("VOLKSWAGEN", "CRAFTER", "2"),
                              ("AUDI", "A4", "2"), ("CHEVROLET", "GRAND CAMARO", "4.1")]:
    fila = [marca, modelo, "1999", "Base", litros, "4", "", "", "", "", "", ""]
    catalogo.por_fabricante_modelo.setdefault((marca, modelo), []).append(fila)
    catalogo.fabricantes_por_modelo.setdefault(modelo, []).append(marca)
    if marca not in catalogo.fabricantes:
        catalogo.fabricantes.append(marca)

# El entero contiguo a un modelo puede proponerse como cilindrada sólo con respaldo del catálogo.
for anio in (1996, 1997, 1998):
    catalogo.por_fabricante_modelo.setdefault(("FORD", "BRONCO"), []).append(
        ["Ford", "Bronco", str(anio), "Base", "5.0", "8", "SUV", "", "", "", "", ""])
catalogo.fabricantes_por_modelo["BRONCO"] = ["FORD"]
catalogo.modelos_por_fabricante.setdefault("FORD", []).append("BRONCO")
catalogo.fabricantes.append("FORD")

def resolver(titulo):
    return analizar([["X", titulo, "SKU"] + [""] * 14], catalogo)["resultados"][0]

from itertools import permutations
for partes in permutations(["Tsuru", "1.6", "1999"]):
    r = resolver("Bomba " + " ".join(partes))
    check("permutación " + " ".join(partes), r["estado"] == "lista" and len(r["candidatos"]) == 1, r)
for titulo in ["Para Nissan Tsuru I 1.6 1999", "Tsuru 2 1.6 1999", "1999 1,6L Tsuru", "Tsuru 1.6 1999 Nissan"]:
    r = resolver(titulo)
    check(titulo, r["estado"] == "lista" and not r["extraido"]["cilindros"], r)
for titulo in ["Tsuru GSR 2 1999", "Tsuru 2.0 1999", "Tsuru 1.6 2.0 1999", "Nissan Mazda Tsuru 1.6 1999", "Tsuru Crafter 2 L 1999"]:
    r = resolver(titulo)
    esperado = "lista" if titulo == "Tsuru 2.0 1999" else "revision"
    check(titulo, r["estado"] == esperado, r)
check("entero sólo propone motor existente", {f[4] for f in resolver("Tsuru GSR 2 1999")["candidatos"]} == {"2"})
bronco = resolver("Bomba de agua P/ Ford Bronco 5 1996/1998")
check("Bronco 5 propone sólo filas 5.0 y queda en revisión",
      bronco["estado"] == "revision" and bronco["extraido"]["litros"] == "5"
      and {f[4] for f in bronco["candidatos"]} == {"5.0"}, bronco)
check("Bronco explica la ambigüedad generación/cilindrada",
      "también puede indicar una generación" in bronco["motivo"], bronco["motivo"])
sin_50 = [f for f in catalogo.por_fabricante_modelo[("FORD", "BRONCO")] if f[2] != "1996"]
catalogo.por_fabricante_modelo[("FORD", "BRONCO")] = sin_50
check("Bronco 5 sin motor de catálogo no inventa coincidencia",
      resolver("Bomba de agua P/ Ford Bronco 5 1996")["estado"] == "sin_coincidencia")
catalogo.por_fabricante_modelo[("FORD", "BRONCO")].insert(0,
    ["Ford", "Bronco", "1996", "Base", "5.0", "8", "SUV", "", "", "", "", ""])
for motor in ["2.0", "2,0", "2.0L", "2 L"]:
    check("cilindrada equivalente " + motor, resolver("Crafter " + motor + " 1999")["estado"] == "lista")
check("años abreviados requieren revisión", resolver("Tsuru 1.6 1999 00-02")["estado"] == "revision")
check("generación no infiere litros", resolver("Tsuru 2 1999")["extraido"]["litros"] == "")
check("Crafter no se convierte en Mazda 2", resolver("Crafter 2.0 1999")["extraido"]["modelos"] == ["CRAFTER"])
check("marca VW restringe A4", all(f[0] == "Volkswagen" for f in resolver("VW Jetta A4 2.0 2018")["candidatos"]))
check("modelo compuesto más específico", resolver("Grand Camaro 4.1 1999")["extraido"]["modelos"] == ["GRAND CAMARO"])
check("modelo compuesto conserva orden", resolver("Camaro Grand 4.1 1999")["extraido"]["modelos"] == ["CAMARO"])
check("modelo numérico exige marca", not resolver("2 1.5 1999")["candidatos"])
check("modelo numérico separado del motor", resolver("Mazda 2 1.5 1999")["estado"] == "lista")
check("código L4 explícito", resolver("Tsuru L4 1.6 1999")["extraido"]["cilindros"] == "L4")
# Sin el motor 2.0 en catálogo, sus propuestas 1.6 nunca son automáticas.
solo16 = catalogo.por_fabricante_modelo[("NISSAN", "TSURU")].pop()
check("otro motor requiere revisión", resolver("Tsuru 2.0 1999")["estado"] == "revision")
catalogo.por_fabricante_modelo[("NISSAN", "TSURU")].append(solo16)
base[15:17] = ["Delantera", "Nota original"]

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
    check("una fila por compatibilidad", total == 7 and len(filas) == 7, total)
    check("incluye descriptor con match exacto", any(fila[0] == submodelo[0] for fila in filas), filas)
    check("repite UserProductID, título y SKU", filas[0][:3] == filas[1][:3] == tuple(base[:3]), filas[:2])
    check("llena los atributos disponibles del vehículo", filas[0][3:9] == tuple(vehiculos[0][:6]), filas[0])
    check("conserva combustible vacío sin inventarlo", filas[0][12] is None, filas[0])
    check("conserva posición y notas", filas[0][15:17] == ("Delantera", "Nota original"))
    wb.close()
    revision = ["R", "Tsuru GSR 2 1999", "R-SKU"] + [""] * 14
    datos = [base, revision]
    sesion_revision = {"publicaciones": datos, **analizar(datos, catalogo)}
    check("revisión excluida por defecto", escribir_resultado(sesion_revision, [], [], ruta) == 2)
    check("aprobación por publicación incluye revisión", escribir_resultado(sesion_revision, [1], [], ruta) == 3)
    check("exclusión gana a aprobación", escribir_resultado(sesion_revision, [1], [1], ruta) == 2)
finally:
    ruta.unlink(missing_ok=True)

print(f"\n{total_checks - fallos} pasaron · {fallos} fallaron")
raise SystemExit(1 if fallos else 0)
