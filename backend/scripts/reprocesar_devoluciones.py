"""Refresca claims existentes desde Mercado Libre para completar orden, SKU y almacén."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.sync_devoluciones import reprocesar_existentes

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--cuenta", choices=("principal", "secundaria"), help="Limita el reprocesamiento a una cuenta")
args = parser.parse_args()
print(reprocesar_existentes(args.cuenta))
