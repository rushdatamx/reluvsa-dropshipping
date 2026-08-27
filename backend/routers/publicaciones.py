"""
Módulo 2 — Publicaciones masivas (admin).

Convierte el catálogo de un proveedor en la plantilla .xlsx que Gaby sube a
Mercado Libre. NO toca la API de ML ni los datos del Módulo 1: es un
transformador Excel -> Excel, todo en memoria/temporal.

Flujo de 3 pasos que ve Gaby:
  1. POST /api/publicaciones/analizar  -> catálogo (+ publicaciones ML opcional)
  2. revisa el cruce: cuántas faltan, por línea de producto
  3. POST /api/publicaciones/generar   -> descarga el .xlsx listo para ML
"""
import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from routers.auth import require_admin
from services.generador_plantilla import (
    ConfiguracionProveedor, escribir_xlsx, generar_filas, generar_filas_con_reporte,
)
from services.parser_catalogo import (cruzar, cruzar_variantes, leer_catalogo_detallado,
                                      leer_publicaciones, leer_skus_publicados)
from services.perfiles_catalogo import perfil_de, proveedores_soportados
from services.precio_publicacion import ParametrosPrecio, envio_pendiente

router = APIRouter(prefix="/api/publicaciones", tags=["publicaciones"])

TMP_DIR = Path(tempfile.gettempdir()) / "reluvsa_publicaciones"
TMP_DIR.mkdir(parents=True, exist_ok=True)


def _guardar_tmp(file: UploadFile) -> Path:
    suffix = Path(file.filename or "catalogo.xlsx").suffix or ".xlsx"
    fd, ruta = tempfile.mkstemp(suffix=suffix, dir=TMP_DIR)
    with open(fd, "wb") as out:
        shutil.copyfileobj(file.file, out)
    return Path(ruta)


def _perfil_o_400(codigo_bodega: str):
    perfil = perfil_de(codigo_bodega)
    if perfil is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Todavía no hay un perfil de catálogo para «{codigo_bodega}». "
                f"Hoy están listos: {', '.join(proveedores_soportados())}. "
                "Cada proveedor manda su información distinta, así que hay que "
                "configurar cómo viene su Excel."
            ),
        )
    return perfil


def _leer_o_400(ruta, perfil):
    try:
        resultado = leer_catalogo_detallado(ruta, perfil)
    except KeyError:
        raise HTTPException(
            status_code=400,
            detail=(f"El archivo no tiene la hoja «{perfil.nombre_hoja}» que "
                    f"esperamos del catálogo de {perfil.codigo_bodega}."),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="No pude leer este archivo como Excel. Verifica que sea el catálogo del proveedor.",
        )
    if not resultado.piezas:
        raise HTTPException(
            status_code=400,
            detail=("No encontré piezas en el archivo. ¿Es el catálogo de "
                    f"{perfil.codigo_bodega}? Esperamos la clave en la columna "
                    f"{chr(65 + perfil.col_clave)}."),
        )
    return resultado


@router.get("/proveedores")
def listar_proveedores_soportados(_=Depends(require_admin)):
    """Proveedores con perfil de catálogo listo."""
    return {
        "soportados": proveedores_soportados(),
        "envio_pendiente": envio_pendiente(),
    }


@router.post("/analizar")
async def analizar(
    codigo_bodega: str = Form(...),
    catalogo: UploadFile = File(...),
    publicaciones_ml: UploadFile = File(None),
    _=Depends(require_admin),
):
    """Lee el catálogo y lo cruza contra lo ya publicado.

    `publicaciones_ml` es opcional: sin él se reporta el catálogo completo como
    faltante (no se asume que nada está publicado — se dice explícitamente que
    no se pudo cruzar).
    """
    perfil = _perfil_o_400(codigo_bodega)
    ruta_cat = _guardar_tmp(catalogo)
    ruta_pub = _guardar_tmp(publicaciones_ml) if publicaciones_ml else None

    try:
        lectura = _leer_o_400(ruta_cat, perfil)
        piezas = lectura.piezas

        publicados = set()
        if ruta_pub:
            try:
                publicados = (leer_publicaciones(ruta_pub) if lectura.formato == "master_kg"
                              else leer_skus_publicados(ruta_pub))
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail=("No pude leer el reporte de Publicaciones de Mercado "
                            "Libre. Debe ser el Excel que descargas de ML."),
                )

        cfg = ConfiguracionProveedor(codigo_bodega=codigo_bodega)
        if lectura.formato == "master_kg":
            candidatos, reporte = generar_filas_con_reporte(piezas, cfg)
            cruce_v = cruzar_variantes(candidatos, publicados)
            filas = cruce_v["pendientes"]
            sku_publicados = {sku for sku, _ in publicados}
            por_producto = []
            for producto in sorted({p["linea"] for p in piezas}):
                pp = [p for p in piezas if p["linea"] == producto]
                cand = [f for f in candidatos if f.linea == producto]
                pend = [f for f in filas if f.linea == producto]
                por_producto.append({"linea": producto, "producto": producto, "piezas": len(pp),
                    "compatibilidades": sum(len(p["compatibilidades"]) for p in pp),
                    "publicaciones": len(cand), "publicaciones_faltantes": len(pend)})
            errores = lectura.errores + reporte["exclusiones"]
            return {"proveedor": codigo_bodega, "formato": lectura.formato,
                "cruce_realizado": ruta_pub is not None, "filas_master": lectura.filas_master,
                "total_catalogo": len(piezas), "sku_unicos": lectura.sku_unicos_master or len(piezas),
                "compatibilidades_validas": lectura.compatibilidades_validas,
                "compatibilidades_invalidas": lectura.compatibilidades_invalidas,
                "duplicados_descartados": lectura.duplicados_descartados,
                "precio_presente": lectura.precio_presente, "sku_sin_precio": lectura.sku_sin_precio,
                "sku_precio_inconsistente": lectura.sku_precio_inconsistente,
                "sku_con_alguna_publicacion": len(sku_publicados & {p['clave'].upper() for p in piezas}),
                "variantes_estimadas": len(candidatos), "variantes_existentes": len(cruce_v["existentes"]),
                "variantes_faltantes": len(filas), "variantes_deduplicadas": reporte["deduplicadas"] + cruce_v["deduplicadas"],
                "variantes_excluidas": len(reporte["exclusiones"]), "publicaciones_estimadas": len(filas),
                "ya_publicadas": len(cruce_v["existentes"]), "faltantes": len(filas),
                "aplicaciones_truncadas": 0, "envio_pendiente": envio_pendiente(),
                "errores_total": len(errores), "errores": errores[:200], "por_linea": por_producto}

        resultado = cruzar(piezas, publicados)
        por_linea = Counter(p["linea"] or "(sin línea)" for p in resultado["piezas_faltantes"])
        filas = generar_filas(resultado["piezas_faltantes"], cfg)
        truncadas = generar_filas(resultado["piezas_faltantes"], cfg, incluir_truncadas=True)

        return {
            "proveedor": codigo_bodega,
            "cruce_realizado": ruta_pub is not None,
            "total_catalogo": resultado["total_catalogo"],
            "filas_master": lectura.filas_master, "sku_unicos": resultado["total_catalogo"],
            "ya_publicadas": resultado["ya_publicadas"],
            "faltantes": resultado["faltantes"],
            "publicaciones_estimadas": len(filas),
            "aplicaciones_truncadas": len(truncadas) - len(filas),
            "envio_pendiente": envio_pendiente(),
            "por_linea": [
                {"linea": l, "piezas": n} for l, n in por_linea.most_common()
            ],
        }
    finally:
        ruta_cat.unlink(missing_ok=True)
        if ruta_pub:
            ruta_pub.unlink(missing_ok=True)


@router.post("/generar")
async def generar(
    codigo_bodega: str = Form(...),
    catalogo: UploadFile = File(...),
    publicaciones_ml: UploadFile = File(None),
    descripcion_base: str = Form(""),
    marca: str = Form(""),
    categoria_ml: str = Form(""),
    cantidad: int = Form(10),
    iva: float = Form(0.16),
    utilidad: float = Form(0.50),
    comision_ml: float = Form(0.13),
    envio: float = Form(0.0),
    lineas: str = Form(""),          # JSON con las líneas elegidas; vacío = todas
    solo_faltantes: bool = Form(True),
    _=Depends(require_admin),
):
    """Genera el .xlsx con las 36 columnas listo para subir a Mercado Libre."""
    perfil = _perfil_o_400(codigo_bodega)
    ruta_cat = _guardar_tmp(catalogo)
    ruta_pub = _guardar_tmp(publicaciones_ml) if publicaciones_ml else None

    try:
        lectura = _leer_o_400(ruta_cat, perfil)
        piezas = lectura.piezas

        if ruta_pub and solo_faltantes and lectura.formato == "legado":
            publicados = leer_skus_publicados(ruta_pub)
            piezas = cruzar(piezas, publicados)["piezas_faltantes"]

        # `lineas` se conserva por compatibilidad con el frontend/API, pero en el
        # master su valor es exclusivamente Producto (RADIADOR, BOMBA DE AGUA...).
        if lineas.strip():
            try:
                elegidas = {l.strip().upper() for l in json.loads(lineas)}
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="El filtro de líneas no es una lista válida.")
            if elegidas:
                piezas = [p for p in piezas if (p["linea"] or "").upper() in elegidas]

        if not piezas:
            raise HTTPException(
                status_code=400,
                detail="No quedaron piezas por publicar con esos filtros.",
            )

        config = ConfiguracionProveedor(
            codigo_bodega=codigo_bodega,
            descripcion_base=descripcion_base,
            marca=marca or perfil.marca_ml,
            categoria_ml=categoria_ml,
            cantidad=cantidad,
            params_precio=ParametrosPrecio(
                iva=iva, utilidad=utilidad, comision_ml=comision_ml,
                envio_default=envio,
            ),
        )

        filas = generar_filas(piezas, config)
        if ruta_pub and solo_faltantes and lectura.formato == "master_kg":
            filas = cruzar_variantes(filas, leer_publicaciones(ruta_pub))["pendientes"]
        if not filas:
            raise HTTPException(
                status_code=400,
                detail=("Ninguna pieza tiene aplicaciones utilizables: todas vienen "
                        "cortadas en el catálogo del proveedor. Pídele el archivo completo."),
            )

        fd, destino = tempfile.mkstemp(suffix=".xlsx", dir=TMP_DIR)
        Path(destino).unlink(missing_ok=True)
        escribir_xlsx(filas, config, destino)

        return FileResponse(
            destino,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=f"publicaciones_{codigo_bodega.lower()}_{len(filas)}.xlsx",
        )
    finally:
        ruta_cat.unlink(missing_ok=True)
        if ruta_pub:
            ruta_pub.unlink(missing_ok=True)
