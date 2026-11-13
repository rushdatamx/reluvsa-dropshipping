"""Cruce Excel -> Excel de publicaciones con el catálogo vehicular de Autozur.

Este módulo NO conoce ni llama la API de Mercado Libre.  Lee los dos archivos que
entrega Autozur, propone compatibilidades a partir del título y deja cualquier
caso incompleto o ambiguo para revisión humana.
"""
from __future__ import annotations

import json
import re
import tempfile
import time
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import openpyxl


ENCABEZADOS_CATALOGO = (
    "FABRICANTE", "MODELO", "AÑO", "SUBMODELO", "LITROS", "CILINDROS",
    "CARROCERÍA", "TIPO DE TRANSMISIÓN", "TIPO DE TRACCIÓN",
    "TIPO DE COMBUSTIBLE", "TIPO DE MOTOR", "TIPO DE ASPIRACIÓN",
)

ENCABEZADOS_PUBLICACIONES = (
    "UserProductID", "TITULO", "SKU", *ENCABEZADOS_CATALOGO,
    "ASIGNACIÓN DE POSICIÓN(Conductor, Acompañante, Izquierda, Derecha, "
    "Delantera, Trasera, Interno, Externo, Superior, Inferior, Intermedio, Centro)",
    "NOTAS",
)

SESSION_DIR = Path(tempfile.gettempdir()) / "reluvsa_autozur"
SESSION_DIR.mkdir(parents=True, exist_ok=True)
SESSION_TTL_SEGUNDOS = 24 * 60 * 60

_ANIO = re.compile(r"\b(?:19|20)\d{2}\b")
_MOTOR = re.compile(r"\b((?:[LVHWR]\s*\d{1,2}|I\d{1,2}))\b", re.IGNORECASE)
_LITROS_CON_L = re.compile(r"\b(\d{1,2}(?:[.,]\d+)?)\s*[lL]\b")
_DECIMAL = re.compile(r"\b(\d{1,2}[.,]\d+)\b")


def normalizar(valor) -> str:
    texto = "" if valor is None else str(valor).strip()
    texto = "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    texto = texto.upper().replace("–", "-").replace("—", "-")
    texto = re.sub(r"[^A-Z0-9./-]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _texto_excel(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def _encabezados(ws) -> Tuple[str, ...]:
    return tuple(_texto_excel(c.value) for c in next(ws.iter_rows(min_row=1, max_row=1)))


def _validar_encabezados(actuales: Sequence[str], esperados: Sequence[str], nombre: str) -> None:
    if tuple(actuales[:len(esperados)]) == tuple(esperados):
        return
    faltantes = [h for h in esperados if h not in actuales]
    if faltantes:
        raise ValueError(f"El {nombre} no tiene las columnas requeridas: {', '.join(faltantes)}.")
    raise ValueError(f"El {nombre} contiene las columnas esperadas, pero no están en el orden de Autozur.")


def _anio_entero(valor) -> Optional[int]:
    texto = _texto_excel(valor)
    if not re.fullmatch(r"(?:19|20)\d{2}", texto):
        return None
    return int(texto)


def _numero_normalizado(valor) -> str:
    texto = _texto_excel(valor).replace(",", ".")
    try:
        numero = float(texto)
    except (TypeError, ValueError):
        return normalizar(texto)
    return f"{numero:g}"


@dataclass
class CatalogoIndexado:
    por_fabricante_modelo: Dict[Tuple[str, str], List[List[str]]]
    modelos_por_fabricante: Dict[str, List[str]]
    fabricantes_por_modelo: Dict[str, List[str]]
    fabricantes: List[str]
    filas: int


def leer_catalogo(ruta) -> CatalogoIndexado:
    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    ws = wb.active
    _validar_encabezados(_encabezados(ws), ENCABEZADOS_CATALOGO, "catálogo de vehículos")

    indice: Dict[Tuple[str, str], List[List[str]]] = defaultdict(list)
    modelos: Dict[str, set] = defaultdict(set)
    filas = 0
    for valores in ws.iter_rows(min_row=2, max_col=len(ENCABEZADOS_CATALOGO), values_only=True):
        fila = [_texto_excel(v) for v in valores]
        fabricante, modelo = normalizar(fila[0]), normalizar(fila[1])
        anio = _anio_entero(fila[2])
        if not fabricante or not modelo or anio is None:
            continue
        fila[2] = str(anio)
        indice[(fabricante, modelo)].append(fila)
        modelos[fabricante].add(modelo)
        filas += 1
    wb.close()
    return CatalogoIndexado(
        por_fabricante_modelo=dict(indice),
        modelos_por_fabricante={k: sorted(v, key=len, reverse=True) for k, v in modelos.items()},
        fabricantes_por_modelo={
            modelo: sorted(fabricantes)
            for modelo, fabricantes in _invertir_modelos(modelos).items()
        },
        fabricantes=sorted(modelos, key=len, reverse=True),
        filas=filas,
    )


def _invertir_modelos(modelos: Dict[str, set]) -> Dict[str, set]:
    resultado = defaultdict(set)
    for fabricante, nombres in modelos.items():
        for modelo in nombres:
            resultado[modelo].add(fabricante)
    return resultado


def leer_publicaciones(ruta) -> List[List[str]]:
    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    ws = wb.active
    _validar_encabezados(_encabezados(ws), ENCABEZADOS_PUBLICACIONES, "archivo de publicaciones")
    filas = []
    vistos = set()
    for numero, valores in enumerate(
        ws.iter_rows(min_row=2, max_col=len(ENCABEZADOS_PUBLICACIONES), values_only=True), start=2
    ):
        fila = [_texto_excel(v) for v in valores]
        if not any(fila):
            continue
        if not all(fila[i] for i in range(3)):
            raise ValueError(f"La fila {numero} necesita UserProductID, TITULO y SKU.")
        if fila[0] in vistos:
            raise ValueError(f"El UserProductID {fila[0]} está repetido en el archivo de entrada.")
        vistos.add(fila[0])
        filas.append(fila)
    wb.close()
    if not filas:
        raise ValueError("El archivo de publicaciones no contiene registros.")
    return filas


@lru_cache(maxsize=8192)
def _patron_nombre(opcion: str):
    return re.compile(r"(?<![A-Z0-9.,-])" + re.escape(opcion) + r"(?![A-Z0-9.,-])")


def _apariciones(texto: str, opcion: str):
    # Una palabra completa no puede nacer dentro de un decimal o código.
    return list(_patron_nombre(opcion).finditer(texto))


def interpretar_titulo(titulo: str, catalogo: CatalogoIndexado, ignorar_marca: bool = False) -> dict:
    vehiculo = normalizar(titulo.replace(",", "."))
    anios = [int(a) for a in _ANIO.findall(vehiculo)]
    desde, hasta = (min(anios), max(anios)) if anios else (None, None)
    motores = list(_MOTOR.finditer(vehiculo))
    cilindros_detectados = sorted({re.sub(r"\s+", "", m.group(1)) for m in motores})
    medidas = list(_LITROS_CON_L.finditer(vehiculo)) + list(_DECIMAL.finditer(vehiculo))
    litros_detectados = sorted({_numero_normalizado(m.group(1)) for m in medidas})
    alias = {"VW": "VOLKSWAGEN", "MERCEDES BENZ": "MERCEDES-BENZ"}
    marcas = sorted({alias.get(op, op) for op in set(catalogo.fabricantes) | set(alias)
                     if _apariciones(vehiculo, op)})
    fabricante = marcas[0] if len(marcas) == 1 else ""
    ocupados = [m.span() for m in medidas + motores] + [m.span() for m in _ANIO.finditer(vehiculo)]
    hallados = []
    for modelo in catalogo.fabricantes_por_modelo:
        if not ignorar_marca and marcas and not set(marcas).intersection(catalogo.fabricantes_por_modelo[modelo]):
            continue
        for m in _apariciones(vehiculo, modelo):
            if any(m.start() < fin and m.end() > ini for ini, fin in ocupados):
                continue
            if modelo.isdigit() and not marcas and not ignorar_marca:
                continue
            hallados.append((modelo, m.start(), m.end()))
    # Sólo elimina nombres contenidos en la misma aparición de uno compuesto.
    hallados = [h for h in hallados if not any(
        otro[1] <= h[1] and h[2] <= otro[2] and len(otro[0]) > len(h[0])
        for otro in hallados)]
    modelos = sorted({h[0] for h in hallados})
    ambiguos = []
    if len(marcas) > 1:
        ambiguos.append("El título contiene marcas contradictorias: " + ", ".join(marcas) + ".")
    if len(modelos) > 1:
        ambiguos.append("El título contiene múltiples modelos: " + ", ".join(modelos) + ".")
    if len(litros_detectados) > 1 or len(cilindros_detectados) > 1:
        ambiguos.append("El título contiene múltiples motores; requiere revisión.")
    if re.search(r"(?<![A-Z0-9.,-])\d{2}\s*[-/]\s*\d{2}(?![A-Z0-9.,-])", vehiculo):
        ambiguos.append("El título contiene años abreviados; requiere revisión.")
    origen = "explicita" if litros_detectados else ""
    # Un entero inmediatamente posterior al modelo es generación, no litros.
    if not litros_detectados and modelos:
        generaciones = []
        for _, _, fin in hallados:
            m = re.match(r"\s+(?:I{1,3}|[0-9]+)\b", vehiculo[fin:])
            if m:
                generaciones.append((fin, fin + m.end()))
        opciones = set()
        generaciones_con_motor = set()
        for m in re.finditer(r"(?<![A-Z0-9.,-])(?:1[0-2]|[1-9])(?![A-Z0-9.,-])", vehiculo):
            if any(m.start() < fin and m.end() > ini for ini, fin in
                   ocupados + [(h[1], h[2]) for h in hallados]):
                continue
            valor = _numero_normalizado(m.group())
            es_generacion = any(m.start() < fin and m.end() > ini for ini, fin in generaciones)
            coincide = any(_numero_normalizado(f[4]) == valor and
                   (not es_generacion or re.search(r"[.,]\d", str(f[4]))) and
                   (desde is None or desde <= int(f[2]) <= hasta)
                   for (marca, mod), filas in catalogo.por_fabricante_modelo.items()
                   if mod in modelos and (not marcas or marca in marcas) for f in filas)
            if coincide:
                opciones.add(valor)
                if es_generacion:
                    generaciones_con_motor.add(valor)
        litros_detectados = sorted(opciones)
        if opciones:
            origen = "entero_inferido"
            ambiguos.append("Cilindrada propuesta a partir de un motor entero sin unidad; requiere revisión.")
            if generaciones_con_motor:
                ambiguos.append("El entero también puede indicar una generación; se propone como posible cilindrada y requiere confirmación.")
            if len(opciones) > 1:
                ambiguos.append("Existen múltiples motores enteros posibles.")
    return {
        "fabricante": fabricante, "modelo": modelos[0] if len(modelos) == 1 else "",
        "litros": litros_detectados[0] if len(litros_detectados) == 1 else "",
        "cilindros": cilindros_detectados[0] if len(cilindros_detectados) == 1 else "",
        "anio_desde": desde, "anio_hasta": hasta, "texto_vehiculo": vehiculo,
        "modelo_detectado": ", ".join(modelos), "detalle_modelo": "",
        "fabricante_inferido": False,
        "modelo_ambiguo": len(modelos) > 1 or bool(modelos and not fabricante and
            len(catalogo.fabricantes_por_modelo[modelos[0]]) > 1),
        "modelos": modelos, "fabricantes_detectados": marcas,
        "litros_detectados": litros_detectados, "origen_litros": origen,
        "ambiguedades": ambiguos,
    }


def _filtrar_candidatos(parsed: dict, catalogo: CatalogoIndexado) -> List[List[str]]:
    fabricante, modelo = parsed["fabricante"], parsed["modelo"]
    if parsed.get("modelos") and len(parsed["modelos"]) > 1:
        return [f for mod in parsed["modelos"] for f in _filtrar_candidatos(
            {**parsed, "modelo": mod, "modelos": []}, catalogo)]
    if not modelo:
        return []
    candidatos = (catalogo.por_fabricante_modelo.get((fabricante, modelo), []) if fabricante else
                  [f for (marca, mod), filas in catalogo.por_fabricante_modelo.items() if mod == modelo for f in filas])
    marcas = parsed.get("fabricantes_detectados", [])
    if marcas:
        candidatos = [f for f in candidatos if normalizar(f[0]) in marcas]
    if parsed["anio_desde"] is not None:
        candidatos = [
            f for f in candidatos
            if parsed["anio_desde"] <= int(f[2]) <= parsed["anio_hasta"]
        ]
    if parsed.get("litros_detectados"):
        candidatos = [f for f in candidatos if _numero_normalizado(f[4]) in parsed["litros_detectados"]]
    if parsed["litros"]:
        candidatos = [f for f in candidatos if _numero_normalizado(f[4]) == parsed["litros"]]
    if parsed["cilindros"]:
        numero = re.sub(r"\D", "", parsed["cilindros"])
        if numero:
            candidatos = [f for f in candidatos if _numero_normalizado(f[5]) == numero]

    unicos = []
    vistos = set()
    for fila in candidatos:
        llave = tuple(normalizar(v) for v in fila)
        if llave not in vistos:
            vistos.add(llave)
            unicos.append(fila)
    return unicos


_SUSPENSION = re.compile(r"\b(?:HORQUILLA|AMORTIGUADOR(?:ES)?|RESORTES?)\b", re.IGNORECASE)


def es_publicacion_suspension(fila: Sequence[str]) -> bool:
    return bool(_SUSPENSION.search(normalizar(fila[1])))


def analizar(publicaciones: List[List[str]], catalogo: CatalogoIndexado, modo: str = "general") -> dict:
    if modo not in ("general", "suspension"):
        raise ValueError("El apartado solicitado no es válido.")
    if modo == "suspension":
        publicaciones = [fila for fila in publicaciones if es_publicacion_suspension(fila)]
        if not publicaciones:
            raise ValueError("No se encontraron publicaciones de suspensión (horquillas, amortiguadores o resortes).")
    resultados = []
    conteos = defaultdict(int)
    compatibilidades = 0

    for posicion, fila in enumerate(publicaciones):
        parsed = interpretar_titulo(fila[1], catalogo, ignorar_marca=modo == "suspension")
        if modo == "suspension":
            parsed["litros"] = ""
            parsed["litros_detectados"] = []
            parsed["cilindros"] = ""
            # La marca del título es informativa: buscar en todo el catálogo.
            candidatos = _filtrar_candidatos(
                {**parsed, "fabricante": "", "fabricantes_detectados": []}, catalogo
            ) if parsed["modelos"] and parsed["anio_desde"] is not None else []
            bloqueos = [
                "Las propuestas corresponden al modelo y año detectados y pueden incluir varias marcas. "
                "Revisa y aprueba las compatibilidades antes de descargarlas.",
                *parsed["ambiguedades"],
            ]
            if len(parsed["modelos"]) != 1:
                bloqueos.append("El modelo no es inequívoco; confirma el modelo del vehículo.")
            if parsed["anio_desde"] is None:
                bloqueos.append("El título no contiene un año utilizable.")
            if not candidatos and parsed["modelo"]:
                bloqueos.append("No se encontraron vehículos de ese modelo y año en el catálogo.")
            estado = "revision" if candidatos else "sin_coincidencia"
            conteos[estado] += 1
            resultados.append({
                "id": posicion, "user_product_id": fila[0], "titulo": fila[1], "sku": fila[2],
                "estado": estado, "aprobacion_masiva_segura": False,
                "motivo": " ".join(bloqueos), "extraido": parsed, "candidatos": candidatos,
            })
            continue

        candidatos = _filtrar_candidatos(parsed, catalogo)
        marcas_candidatas = sorted({normalizar(c[0]) for c in candidatos})
        marca_ambigua = not parsed["fabricante"] and len(marcas_candidatas) > 1
        if (not parsed["fabricante"] and not parsed["ambiguedades"]
                and parsed["litros"] and parsed["anio_desde"] is not None
                and len(marcas_candidatas) == 1 and candidatos):
            parsed["fabricante"] = marcas_candidatas[0]
            parsed["fabricante_inferido"] = True
            parsed["modelo_ambiguo"] = False
        elif marca_ambigua:
            parsed["modelo_ambiguo"] = True
            # Una aprobación por publicación incluiría todas estas filas; no
            # mostrar propuestas de marcas distintas como un único paquete.
            candidatos = []
        bloqueos = list(parsed["ambiguedades"])
        observaciones = []
        if marca_ambigua:
            bloqueos.append(
                "El modelo, motor y años coinciden con varias marcas; se requiere identificar "
                "la marca. Se ocultaron las propuestas para evitar aprobar compatibilidades de otra marca."
            )
        if parsed["fabricante_inferido"]:
            observaciones.append(
                f"Fabricante inferido como {parsed['fabricante']} porque {parsed['modelo']} "
                "sólo corresponde a esta marca en el catálogo."
            )
        elif not parsed["fabricante"]:
            if len(marcas_candidatas) > 1:
                bloqueos.append("El modelo aparece asociado a varias marcas en el catálogo.")
            else:
                bloqueos.append("No se identificó un fabricante inequívoco en el título.")
        if parsed["fabricante"] and not parsed["modelos"]:
            bloqueos.append("El modelo del título no coincide exactamente con el catálogo.")
        if parsed["anio_desde"] is None:
            bloqueos.append("El título no contiene un año utilizable.")
        if not parsed["litros_detectados"]:
            bloqueos.append("El título no contiene la cilindrada en litros.")
        if not candidatos and not marca_ambigua and (parsed["modelo"] or parsed["modelos"]):
            propuestas = _filtrar_candidatos({**parsed, "litros": "", "litros_detectados": [], "cilindros": ""}, catalogo)
            if propuestas:
                candidatos = propuestas
                bloqueos.append(
                    "El motor solicitado no coincide con estas propuestas; se muestran otros motores del mismo modelo y años."
                )
            elif not bloqueos:
                bloqueos.append("No se encontraron vehículos con la combinación exacta del título.")
        if parsed["detalle_modelo"] and candidatos and (not parsed["litros"] or parsed["anio_desde"] is None):
            bloqueos.append(f"El título agrega «{parsed['detalle_modelo']}» después del modelo; revisa submodelo o carrocería.")

        # La lista automática exige fabricante/modelo/años/cilindrada exactos.
        # Cilindros/configuración sólo restringen cuando aparecen explícitamente.
        # Las variantes de submodelo son candidatas válidas: se exportan todas.
        estado = "lista" if candidatos and not bloqueos else (
            "revision" if candidatos or parsed["modelo_ambiguo"] else "sin_coincidencia"
        )
        conteos[estado] += 1
        if estado == "lista":
            compatibilidades += len(candidatos)

        resultados.append({
            "id": posicion,
            "user_product_id": fila[0],
            "titulo": fila[1],
            "sku": fila[2],
            "estado": estado,
            "aprobacion_masiva_segura": estado == "lista" and bool(candidatos),
            "motivo": " ".join(observaciones + bloqueos),
            "extraido": parsed,
            "candidatos": candidatos,
        })

    return {
        "resultados": resultados,
        "resumen": {
            "publicaciones": len(publicaciones),
            "listas": conteos["lista"],
            "revision": conteos["revision"],
            "sin_coincidencia": conteos["sin_coincidencia"],
            "compatibilidades_automaticas": compatibilidades,
            "filas_catalogo": catalogo.filas,
            "modo": modo,
        },
    }


def guardar_sesion(publicaciones: List[List[str]], analisis: dict) -> str:
    ahora = time.time()
    for antigua in SESSION_DIR.glob("*.json"):
        try:
            if ahora - antigua.stat().st_mtime > SESSION_TTL_SEGUNDOS:
                antigua.unlink(missing_ok=True)
        except OSError:
            pass
    session_id = uuid.uuid4().hex
    ruta = SESSION_DIR / f"{session_id}.json"
    payload = {"publicaciones": publicaciones, **analisis}
    ruta.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return session_id


def cargar_sesion(session_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", session_id or ""):
        raise ValueError("La sesión de análisis no es válida.")
    ruta = SESSION_DIR / f"{session_id}.json"
    if not ruta.exists():
        raise ValueError("La sesión de análisis expiró. Vuelve a cargar los dos archivos.")
    return json.loads(ruta.read_text(encoding="utf-8"))


def resumen_para_cliente(session_id: str, analisis: dict) -> dict:
    items = []
    for r in analisis["resultados"]:
        marcas_propuestas = defaultdict(int)
        for candidato in r["candidatos"]:
            marcas_propuestas[candidato[0]] += 1
        items.append({
            k: r[k] for k in ("id", "user_product_id", "titulo", "sku", "estado", "motivo", "extraido", "aprobacion_masiva_segura")
        } | {
            "compatibilidades": len(r["candidatos"]),
            "muestra": r["candidatos"][:5],
            "marcas_propuestas": [
                {"fabricante": fabricante, "compatibilidades": cantidad}
                for fabricante, cantidad in sorted(marcas_propuestas.items(), key=lambda item: normalizar(item[0]))
            ],
        })
    return {"session_id": session_id, **analisis["resumen"], "resultados": items}


def escribir_resultado(session: dict, aprobadas_revision: Iterable[int], excluidas: Iterable[int], destino) -> int:
    aprobadas = {int(i) for i in aprobadas_revision}
    excluidas_ids = {int(i) for i in excluidas}
    resultados = {r["id"]: r for r in session["resultados"]}

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Compatibilidades"
    ws.append(list(ENCABEZADOS_PUBLICACIONES))

    total = 0
    for posicion, original in enumerate(session["publicaciones"]):
        resultado = resultados[posicion]
        incluir = posicion not in excluidas_ids and (resultado["estado"] == "lista" or (
            resultado["estado"] == "revision" and posicion in aprobadas
        ))
        if not incluir:
            continue
        for compatibilidad in resultado["candidatos"]:
            # Autozur pide una fila por compatibilidad. Se repiten los tres datos
            # de publicación y se conservan posición/notas del archivo original.
            ws.append([
                original[0], original[1], original[2], *compatibilidad,
                original[15], original[16],
            ])
            total += 1

    if not total:
        raise ValueError("No hay compatibilidades aprobadas para generar el archivo.")

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for columna, ancho in {"A": 18, "B": 62, "C": 18, "D": 20, "E": 24, "F": 10,
                           "G": 24, "H": 10, "I": 11, "J": 18, "P": 28, "Q": 28}.items():
        ws.column_dimensions[columna].width = ancho
    wb.save(destino)
    return total
