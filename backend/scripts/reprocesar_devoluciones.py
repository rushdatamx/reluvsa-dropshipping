"""Refresca claims existentes desde Mercado Libre para completar orden, SKU y almacén."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.sync_devoluciones import reprocesar_existentes, actualizar_historial
from services import ml_client
from database import get_db

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--cuenta", choices=("principal", "secundaria"), help="Limita el reprocesamiento a una cuenta")
parser.add_argument("--solo-historial", action="store_true", help="Completa únicamente historiales pendientes")
args = parser.parse_args()
if not args.solo_historial:
    print(reprocesar_existentes(args.cuenta))
else:
    with get_db() as conn:
        casos = conn.execute("""SELECT cuenta_ml, claim_id FROM ml_devoluciones
            WHERE mediacion_historial_consultado_en IS NULL AND (? IS NULL OR cuenta_ml=?)
            ORDER BY cuenta_ml, claim_id""", (args.cuenta, args.cuenta)).fetchall()
    resultado = {"procesados": 0, "atribuidos": 0, "sin_apertura": 0, "no_identificados": 0, "errores": 0}
    for caso in casos:
        try:
            actor = actualizar_historial(caso["cuenta_ml"], caso["claim_id"])
            resultado["procesados"] += 1
            resultado["sin_apertura" if actor is None else "no_identificados" if actor == "no_identificado" else "atribuidos"] += 1
        except ml_client.MLError:
            resultado["errores"] += 1
    print(resultado)
