"""Lectura de catálogos KG (master normalizado y formato legado) y cruce ML."""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

import openpyxl

from services.perfiles_catalogo import PerfilCatalogo

COL_ATT_SELLER_SKU, COL_TITULO = 16, 1
MASTER_HOJA = "BD_Catalogo"
MASTER_HEADERS = ("Armadora", "Modelo", "Motor", "Producto", "Año", "Inicio", "Fin",
                  "Clave", "Especificaciones", "Características", "Guía de Compradores",
                  "OEM", "Imagen 1", "Imagen 2", "Imagen 3", "Imagen 4", "Imagen 5")
MASTER_ANCLAS = ("Armadora", "Modelo", "Producto", "Clave", "Inicio", "Fin")


def _texto(val) -> str:
    if val is None: return ""
    if isinstance(val, float) and val.is_integer(): return str(int(val))
    return re.sub(r"\s+", " ", str(val).strip())


def _normalizar(val) -> str:
    txt = unicodedata.normalize("NFD", _texto(val).casefold())
    return " ".join("".join(c for c in txt if unicodedata.category(c) != "Mn").split())


def normalizar_titulo(val) -> str:
    return _normalizar(val)


@dataclass
class ResultadoCatalogo:
    piezas: List[Dict]
    formato: str
    filas_master: int
    compatibilidades_validas: int
    compatibilidades_invalidas: int = 0
    duplicados_descartados: int = 0
    precio_presente: bool = True
    sku_sin_precio: int = 0
    sku_precio_inconsistente: int = 0
    errores: List[Dict] = field(default_factory=list)
    sku_unicos_master: int = 0


def _error(fila, valores, motivo):
    return {"fila": fila, "clave": _texto(valores.get("clave")),
            "armadora": _texto(valores.get("armadora")), "modelo": _texto(valores.get("modelo")),
            "anio": _texto(valores.get("anio")), "inicio": _texto(valores.get("inicio")),
            "fin": _texto(valores.get("fin")), "motivo": motivo}


def _anio_entero(valor):
    if isinstance(valor, bool): return None
    if isinstance(valor, int): return valor
    if isinstance(valor, float) and valor.is_integer(): return int(valor)
    txt = _texto(valor)
    return int(txt) if re.fullmatch(r"\d{4}", txt) else None


def _producto_canonico(valor) -> str:
    conectores = {"de", "del", "con", "c/bomba"}
    return " ".join(p.lower() if p.lower() in conectores else p.capitalize()
                    for p in _texto(valor).split())


def _headers_master(ws):
    valores = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1, max_col=18))]
    mapa = {_normalizar(v): i for i, v in enumerate(valores) if _texto(v)}
    return all(_normalizar(h) in mapa for h in MASTER_HEADERS), mapa, _normalizar("Precio") in mapa


def _parece_master(ws) -> bool:
    """Distingue un master incompleto de un catálogo legado.

    Cuatro de las seis columnas de identidad son suficientes para no interpretar
    accidentalmente Modelo como la línea de producto del formato anterior.
    """
    _, columnas, _ = _headers_master(ws)
    return sum(_normalizar(h) in columnas for h in MASTER_ANCLAS) >= 4


def _hoja_master(wb):
    # El nombre canónico sigue ganando cuando existe. La estructura permite que
    # el proveedor renombre la hoja sin cambiar el formato del archivo.
    canonica = wb[MASTER_HOJA] if MASTER_HOJA in wb.sheetnames else None
    if canonica is not None and _headers_master(canonica)[0]:
        return canonica
    completas = [ws for ws in wb.worksheets if _headers_master(ws)[0]]
    if completas:
        return completas[0]
    if canonica is not None:
        return canonica
    incompletas = [ws for ws in wb.worksheets if _parece_master(ws)]
    return incompletas[0] if incompletas else None


def _leer_master(ws) -> ResultadoCatalogo:
    valido, columnas, precio_presente = _headers_master(ws)
    if not valido:
        faltan = [h for h in MASTER_HEADERS if _normalizar(h) not in columnas]
        raise ValueError(
            f"La hoja «{ws.title}» parece ser el master KG, pero le faltan "
            "los encabezados requeridos: " + ", ".join(faltan)
        )
    if precio_presente and columnas[_normalizar("Precio")] != 17:
        raise ValueError("Precio debe ser la última columna, inmediatamente después de Imagen 5")
    grupos, errores, vistos, claves_master = {}, [], set(), set()
    filas_master = validas = duplicados = 0

    def val(fila, nombre):
        i = columnas.get(_normalizar(nombre))
        return fila[i] if i is not None and i < len(fila) else None

    for numero, fila in enumerate(ws.iter_rows(min_row=2, max_col=max(columnas.values()) + 1,
                                                values_only=True), 2):
        if not any(_texto(x) for x in fila): break
        filas_master += 1
        datos = {k: val(fila, n) for k, n in (("armadora", "Armadora"), ("modelo", "Modelo"),
                 ("motor", "Motor"), ("producto", "Producto"), ("anio", "Año"),
                 ("inicio", "Inicio"), ("fin", "Fin"), ("clave", "Clave"))}
        clave = _texto(datos["clave"]); inicio = _anio_entero(datos["inicio"]); fin = _anio_entero(datos["fin"])
        if clave: claves_master.add(clave.upper())
        if not clave:
            errores.append(_error(numero, datos, "Clave vacía")); continue
        if inicio is None or fin is None or not (1900 <= inicio <= 2100 and 1900 <= fin <= 2100):
            errores.append(_error(numero, datos, "Inicio y Fin deben ser años enteros entre 1900 y 2100")); continue
        if inicio > fin:
            errores.append(_error(numero, datos, "Inicio es mayor que Fin")); continue
        armadora, modelo, motor = (_texto(datos[x]) for x in ("armadora", "modelo", "motor"))
        producto = _producto_canonico(datos["producto"]); guia = _texto(val(fila, "Guía de Compradores"))
        firma = tuple(_normalizar(x) for x in fila)
        if firma in vistos: duplicados += 1; continue
        vistos.add(firma)
        g = grupos.setdefault(clave.upper(), {"clave": clave, "linea": producto, "producto": producto,
            "formato": "master_kg", "compatibilidades": [], "oems": [], "especificaciones": [],
            "caracteristicas": [], "imagenes": ["", "", "", "", ""], "_imagenes_sets": set(),
            "_costos": set(), "filas_origen": []})
        g["filas_origen"].append(numero)
        firma_compat = (_normalizar(armadora), _normalizar(modelo), _normalizar(motor), inicio, fin,
                        _normalizar(guia))
        existentes = {(_normalizar(c["armadora"]), _normalizar(c["modelo"]),
                       _normalizar(c["motor"]), c["inicio"], c["fin"], _normalizar(c["guia"]))
                      for c in g["compatibilidades"]}
        if firma_compat not in existentes:
            validas += 1
            g["compatibilidades"].append({"armadora": armadora, "modelo": modelo, "motor": motor,
                                          "inicio": inicio, "fin": fin, "guia": guia, "fila": numero})
        else:
            duplicados += 1
        for nombre, destino in (("OEM", "oems"), ("Especificaciones", "especificaciones"),
                                ("Características", "caracteristicas")):
            texto = _texto(val(fila, nombre))
            if texto and texto != "-" and _normalizar(texto) not in {_normalizar(x) for x in g[destino]}:
                g[destino].append(texto)
        imagenes = tuple(_texto(val(fila, "Imagen %d" % i)) for i in range(1, 6))
        if any(imagenes):
            g["_imagenes_sets"].add(imagenes)
            if not any(g["imagenes"]): g["imagenes"] = list(imagenes)
        if precio_presente:
            try:
                costo = float(val(fila, "Precio"))
                if costo > 0: g["_costos"].add(costo)
            except (TypeError, ValueError): pass

    inconsistentes = sin_precio = 0
    for g in grupos.values():
        if len(g["_costos"]) == 1: g["costo"] = next(iter(g["_costos"]))
        else:
            g["costo"] = None
            if len(g["_costos"]) > 1:
                inconsistentes += 1
                errores.append(_error(g["filas_origen"][0], {"clave": g["clave"]},
                                      "Precios diferentes dentro del SKU"))
            else: sin_precio += 1
        if len(g["_imagenes_sets"]) > 1:
            errores.append(_error(g["filas_origen"][0], {"clave": g["clave"]},
                                  "Imágenes inconsistentes dentro del SKU"))
        del g["_costos"], g["_imagenes_sets"]
    invalidas = sum(1 for e in errores if "Inicio" in e["motivo"])
    return ResultadoCatalogo(list(grupos.values()), "master_kg", filas_master, validas, invalidas,
                             duplicados, precio_presente, sin_precio, inconsistentes, errores,
                             len(claves_master))


def _leer_legado(wb, perfil):
    ws = wb[perfil.nombre_hoja] if perfil.nombre_hoja else wb.worksheets[0]; piezas = []
    for fila in ws.iter_rows(min_row=perfil.fila_header + 1, values_only=True):
        clave = _texto(fila[perfil.col_clave]) if len(fila) > perfil.col_clave else ""
        if not clave: continue
        celda = lambda i: fila[i] if i is not None and len(fila) > i else None
        piezas.append({"clave": clave, "linea": _texto(celda(perfil.col_linea)),
            "aplicaciones": celda(perfil.col_aplicaciones), "costo": celda(perfil.col_precio_costo),
            "codigo_barras": _texto(celda(perfil.col_codigo_barras)),
            "precio_publico": celda(perfil.col_precio_publico), "formato": "legado"})
    return ResultadoCatalogo(piezas, "legado", len(piezas), len(piezas))


def leer_catalogo_detallado(ruta, perfil: PerfilCatalogo) -> ResultadoCatalogo:
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    try:
        hoja_master = _hoja_master(wb)
        return _leer_master(hoja_master) if hoja_master is not None else _leer_legado(wb, perfil)
    finally: wb.close()


def leer_catalogo(ruta, perfil): return leer_catalogo_detallado(ruta, perfil).piezas


def leer_publicaciones(ruta) -> Set[Tuple[str, str]]:
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    try:
        pares = set()
        for fila in wb.worksheets[0].iter_rows(min_row=2, values_only=True):
            if len(fila) <= COL_ATT_SELLER_SKU: continue
            titulo = normalizar_titulo(fila[COL_TITULO] if len(fila) > COL_TITULO else "")
            for parte in _texto(fila[COL_ATT_SELLER_SKU]).split("&"):
                if parte.strip() and titulo: pares.add((parte.strip().upper(), titulo))
        return pares
    finally: wb.close()


def leer_skus_publicados(ruta): return {sku for sku, _ in leer_publicaciones(ruta)}


def cruzar_variantes(filas, publicados):
    pendientes, existentes, vistos, deduplicadas = [], [], set(), 0
    for fila in filas:
        par = (fila.sku.upper(), normalizar_titulo(fila.titulo))
        if par in vistos: deduplicadas += 1; continue
        vistos.add(par); (existentes if par in publicados else pendientes).append(fila)
    return {"pendientes": pendientes, "existentes": existentes, "deduplicadas": deduplicadas}


def cruzar(piezas: List[Dict], publicados: Set[str]) -> Dict:
    faltantes, ya = [], []
    for p in piezas: (ya if p["clave"].upper() in publicados else faltantes).append(p)
    return {"total_catalogo": len(piezas), "ya_publicadas": len(ya), "faltantes": len(faltantes),
            "piezas_faltantes": faltantes, "piezas_publicadas": ya}
