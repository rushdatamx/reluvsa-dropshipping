#!/usr/bin/env python3
"""Corrige de forma puntual y auditable las fechas de UNA orden de Mercado Libre.

Simula por defecto. Sólo ``--ejecutar`` escribe localmente y únicamente las
columnas ventas_ml.fecha_creacion_ml y ventas_ml.fecha_venta. La API de ML se
consulta exclusivamente con GET a /orders/{id} mediante services.ml_client.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import get_db, init_database  # noqa: E402
from services import ml_client  # noqa: E402
from services.sync_ml import _fecha_api  # noqa: E402


def preparar_cambio(num_venta: str) -> dict:
    with get_db() as conn:
        actual = conn.execute(
            "SELECT num_venta, fecha_venta, fecha_creacion_ml FROM ventas_ml WHERE num_venta=?",
            (num_venta,),
        ).fetchone()
    if not actual:
        raise ValueError(f"La venta {num_venta} no existe en la base local")

    orden = ml_client.get(f"/orders/{num_venta}")
    if str(orden.get("id")) != num_venta:
        raise ValueError("Mercado Libre devolvió una orden distinta a la solicitada")

    fecha_creacion = _fecha_api(orden.get("date_created"))
    fecha_cierre = _fecha_api(orden.get("date_closed"))
    fecha_efectiva = fecha_cierre or fecha_creacion
    if fecha_creacion is None or fecha_efectiva is None:
        raise ValueError("La orden no contiene una fecha válida para la corrección")

    return {
        "num_venta": num_venta,
        "fecha_actual": actual["fecha_venta"],
        "date_created": fecha_creacion,
        "date_closed": fecha_cierre,
        "fecha_propuesta": fecha_efectiva,
    }


def aplicar_cambio(cambio: dict) -> int:
    """Actualiza exactamente las dos columnas autorizadas, en una transacción."""
    with get_db() as conn:
        cur = conn.execute(
            """UPDATE ventas_ml SET fecha_creacion_ml=?, fecha_venta=?
               WHERE num_venta=?""",
            (cambio["date_created"], cambio["fecha_propuesta"], cambio["num_venta"]),
        )
        if cur.rowcount != 1:
            raise RuntimeError("La venta dejó de existir antes de aplicar la corrección")
        return cur.rowcount


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("num_venta", help="order.id exacto de Mercado Libre")
    parser.add_argument("--ejecutar", action="store_true", help="aplica el cambio local; sin esta bandera sólo simula")
    args = parser.parse_args(argv)

    init_database()
    cambio = preparar_cambio(args.num_venta)
    print(f"Venta:             {cambio['num_venta']}")
    print(f"Fecha actual:      {cambio['fecha_actual']}")
    print(f"date_created (MX): {cambio['date_created']}")
    print(f"date_closed (MX):  {cambio['date_closed']}")
    print(f"Fecha propuesta:   {cambio['fecha_propuesta']}")
    if not args.ejecutar:
        print("SIMULACIÓN: no se modificó la base de datos.")
        return 0
    aplicar_cambio(cambio)
    print("APLICADO: se actualizaron sólo fecha_creacion_ml y fecha_venta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
