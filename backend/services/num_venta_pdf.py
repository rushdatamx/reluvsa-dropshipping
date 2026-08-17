"""
Lee el # de venta que KIM imprime en el PDF de su factura y lo resuelve a una venta.

⚠️ EXCLUSIVO DE KIM (`codigo_bodega = 'KIM'`). Ningún otro proveedor imprime el # de
venta, y la normalización de ceros de aquí abajo es un error de captura observado SÓLO
en KIM. Aplicarla a otro proveedor sería inventar datos.

POR QUÉ LEEMOS EL PDF Y NO EL XML
----------------------------------
KIM sí pone el # de venta en su factura, pero **sólo impreso en el PDF**: medido sobre
las 840 facturas de prod, aparece en 0 de 750 XML. KIMS no puede timbrarlo (2026-08-12),
así que la única vía es el PDF. El XML sigue siendo la fuente de verdad de todo lo
demás (UUID, conceptos, importes); esto sólo aporta a QUÉ VENTA pertenece.

EL NÚMERO VIENE CON CEROS DE MÁS
---------------------------------
El order.id de ML es de 16 dígitos; los PDF de KIM traen de 15 a 19 porque teclean
ceros de más al capturar. Medido sobre los 473 números legibles de prod:

    15 (2) · 16 (146) · 17 (245) · 18 (73) · 19 (7)   -> el 69% trae ceros de más

🔴 NO BASTA CON "QUITAR LOS CEROS DE ADELANTE". Fue la primera regla probada y falla:
KIM también mete ceros EN MEDIO, y a veces cambia un dígito que no es cero (`K29628`
imprimió `20000117582609224` donde la venta real es `2000017582609224`). Y es
peligroso: esa regla PRODUCE números de 16 dígitos con pinta válida, que cruzarían
en falso con confianza 1.0.

Por eso aquí NO se reconstruye el número: se BUSCAN las ventas posibles y se EXIGE
evidencia (ver `resolver_num_venta_impreso`).

Este módulo es la versión de servicio de la lógica ya validada en
`scripts/corregir_cruces_num_venta_kim_ceros.py`, que corrigió 228 cruces históricos
(commit `0161ade`). Las reglas son las mismas a propósito: lo que se aplicó a mano
hacia atrás es lo que ahora corre solo hacia adelante.
"""
import os
import re
from itertools import combinations
from typing import Optional

from services.envio_pack import ENVIO_CUBRE_VENTA

# El # impreso: empieza con 2 y trae entre 15 y 22 dígitos. El rango es amplio A
# PROPÓSITO — la validación real la hacen las 3 reglas, no la longitud. Hoy ningún
# concepto del portal trae números de 12+ dígitos, así que hallar uno en el PDF sigue
# siendo señal inequívoca de que el proveedor lo puso.
PATRON_NUM_IMPRESO = re.compile(r"#\s*(2\d{14,21})\b")

CODIGO_BODEGA_KIM = "KIM"      # ⚠️ EXCLUSIVO DE KIM — ver encabezado
METODO = "num_venta_proveedor"
CONFIANZA = 1.0
LARGO_ORDER_ID = 16            # los order.id de ML son de 16 dígitos
MAX_CEROS_SOBRANTES = 6        # cota: más que esto es un dato demasiado corrupto


def extraer_numeros_pdf(ruta: str) -> list:
    """Devuelve los # de venta impresos en un PDF. [] si es ilegible o no trae.

    No revienta nunca: un PDF escaneado, corrupto o ausente devuelve [] y el llamador
    cae al cruce normal. Mismo criterio que `uuid_pdf.extraer_uuid_de_pdf`.
    """
    try:
        import pdfplumber
        with pdfplumber.open(ruta) as pdf:
            texto = " ".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return []
    return sorted(set(PATRON_NUM_IMPRESO.findall(texto)))


def resolver_pdf(ruta: Optional[str], dir_facturas: str) -> Optional[str]:
    """El path guardado en BD puede ser de un contenedor viejo; se resuelve por nombre.

    Mismo fallback que usa el endpoint de descarga de facturas.
    """
    if not ruta:
        return None
    if os.path.exists(ruta):
        return ruta
    alt = os.path.join(dir_facturas, os.path.basename(ruta))
    return alt if os.path.exists(alt) else None


def candidatos_por_ceros(impreso: str) -> set:
    """R1: los números de 16 dígitos que se obtienen BORRANDO ceros del impreso.

    Sólo se BORRA — nunca se agrega ni se sustituye un dígito. Borrar un cero de más es
    deshacer un error de tecleo observado; cambiar un dígito sería inventar un número.

    Se enumeran las posiciones de los ceros y se prueban todas las combinaciones de
    borrado que dejen 16 dígitos. Con <= 6 ceros sobrantes el espacio es chico.
    """
    if len(impreso) == LARGO_ORDER_ID:
        return {impreso}
    sobran = len(impreso) - LARGO_ORDER_ID
    if sobran <= 0 or sobran > MAX_CEROS_SOBRANTES:
        return set()

    pos_ceros = [i for i, ch in enumerate(impreso) if ch == "0"]
    if len(pos_ceros) < sobran:
        return set()

    out = set()
    for quitar in combinations(pos_ceros, sobran):
        quitar = set(quitar)
        v = "".join(ch for i, ch in enumerate(impreso) if i not in quitar)
        if len(v) == LARGO_ORDER_ID and v.startswith("2"):
            out.add(v)
    return out


def _tokens(texto: str) -> set:
    """Tokens alfanuméricos significativos de un código/SKU (>= 3 chars)."""
    if not texto:
        return set()
    return {t for t in re.split(r"[^A-Za-z0-9]+", texto.upper()) if len(t) >= 3}


def sku_cuadra(codigo_concepto: str, sku_venta: str) -> bool:
    """R3: el código facturado y el SKU de la venta deben ser la misma pieza.

    KIM publica en ML con sufijo (`96413748-Z`) y factura sin él (`96413748`), así que
    se comparan TOKENS, no texto crudo — la misma idea que `codigo_id_interno`.
    """
    a, b = _tokens(codigo_concepto), _tokens(sku_venta)
    if not a or not b:
        return False
    if a & b:
        return True
    # tolera el sufijo pegado: '96413748Z' vs '96413748'
    for x in a:
        for y in b:
            if len(x) >= 5 and len(y) >= 5 and (x.startswith(y) or y.startswith(x)):
                return True
    return False


def resolver_num_venta_impreso(conn, proveedor_id: int, impreso: str,
                               codigos_factura, fecha_factura: Optional[str] = None
                               ) -> Optional[str]:
    """Resuelve un # impreso a UN num_venta, o None si no hay evidencia suficiente.

    LAS 3 REGLAS (las mismas que corrigieron los 228 cruces históricos):

    R1. Candidatas = ventas cuyo num_venta sale del impreso BORRANDO ceros en cualquier
        posición. Nunca se agregan ni cambian dígitos.
    R2. La candidata debe ser de este proveedor: su envío debe tener su `proveedor_id`.
        Una venta de otro proveedor jamás la factura KIM.
    R3. El SKU de la venta debe cuadrar con algún código de la factura. Doble
        verificación: número + pieza.

    🔴 Si hay >1 candidata válida, o el SKU no cuadra, se devuelve None y NO se cruza.
    R3 es lo que vuelve seguro tolerar los ceros: un número mal reconstruido casi nunca
    cae en una venta del mismo proveedor Y de la misma pieza. Ante duda se prefiere el
    pendiente al cruce inventado — un pendiente se ve y se corrige; un falso
    "✓ Facturado" nadie lo vuelve a mirar.

    `codigos_factura` son TODOS los códigos de la factura (no sólo el del concepto en
    curso): KIM parte una venta en varias piezas y cualquiera de ellas identifica la
    venta correcta.
    """
    posibles = candidatos_por_ceros(impreso)
    if not posibles:
        return None

    # El JOIN acepta también el envío del PAQUETE: en un carrito ML cuelga el único
    # envío de una sola de las N órdenes, así que sin esto R2 fallaría para las ventas
    # hermanas y el número impreso por KIM no resolvería (BUG A). El DISTINCT ya estaba
    # y evita que un segundo envío del mismo pack duplique la candidata.
    marcadores = ",".join("?" for _ in posibles)
    ventas = conn.execute(
        f"""SELECT DISTINCT v.num_venta, v.sku, date(v.fecha_venta) AS fv
            FROM ventas_ml v
            JOIN envios_colecta e ON {ENVIO_CUBRE_VENTA}
            WHERE v.num_venta IN ({marcadores})
              AND COALESCE(e.proveedor_id, -1) = ?""",
        (*sorted(posibles), proveedor_id),
    ).fetchall()
    if not ventas:
        return None

    validas = [v for v in ventas
               if any(sku_cuadra(cod, v["sku"]) for cod in codigos_factura)]
    if len(validas) != 1:
        return None

    venta = validas[0]

    # Garantía heredada: la venta no puede ser POSTERIOR a su propia factura.
    # Nadie factura lo que todavía no ha vendido. Se compara por DÍA, nunca por
    # timestamp: hacerlo por timestamp destruiría cruces legítimos emitidos el mismo
    # día unas horas antes (medido: 15 con confianza 1.0).
    if fecha_factura and venta["fv"]:
        if venta["fv"] > str(fecha_factura)[:10]:
            return None

    return venta["num_venta"]
