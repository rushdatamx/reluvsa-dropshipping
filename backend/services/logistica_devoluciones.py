"""Contrato de retornos ML: sólo estados explícitos del envío, nunca del reclamo.

Referencia: developers.mercadolibre.com.mx/es_mx/gestionar-devoluciones.
La salida es una allowlist sin direcciones ni nombres de destinatarios.
"""
import json

ESTADOS = {
    "pending": "preparacion", "ready_to_ship": "preparacion",
    "shipped": "en_camino", "delivered": "entregado",
    "not_delivered": "incidencia", "cancelled": "cancelado",
}
TIPOS_RETORNO = {"return", "return_from_triage"}


def _texto(value):
    return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else None


def normalizar_devolucion(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = None
    raw = raw if isinstance(raw, dict) else {}
    shipments = raw.get("shipments")
    legacy = not isinstance(shipments, list)
    if legacy:
        shipments = [raw["shipping"]] if isinstance(raw.get("shipping"), dict) else []
    envios = []
    for shipment in shipments:
        shipment = shipment if isinstance(shipment, dict) else {}
        tipo = _texto(shipment.get("type")) or ("return" if legacy else None)
        estado = _texto(shipment.get("status"))
        destino = shipment.get("destination")
        destino = destino.get("name") if isinstance(destino, dict) else destino
        # Legacy documenta destination como nombre en la raíz del retorno.
        if legacy and destino is None:
            destino = raw.get("destination")
        envios.append({
            "id": _texto(shipment.get("shipment_id") if shipment.get("shipment_id") is not None else shipment.get("id")),
            "tipo": tipo, "destino": _texto(destino), "estado": estado,
            "seguimiento": _texto(shipment.get("tracking_number")),
            "estado_logistico": ESTADOS.get(estado, "desconocido") if tipo in TIPOS_RETORNO else "desconocido",
        })
    estados = {s["estado_logistico"] for s in envios}
    # Cualquier tramo de retorno explícitamente shipped mantiene el caso en camino.
    # Un tramo desconocido impide afirmar que todos los tramos han terminado.
    clasificacion = next((e for e in ("en_camino", "incidencia", "preparacion", "desconocido", "entregado", "cancelado") if e in estados), "desconocido")
    return {"estado_logistico": clasificacion, "envios": envios}
