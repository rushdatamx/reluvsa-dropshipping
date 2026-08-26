#!/usr/bin/env python3
"""Regresión de una factura por venta, sin depender del matcher específico."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.ocupacion_facturas import (  # noqa: E402
    arbitrar_ocupacion, CONFLICTO_DESPLAZADA, CONFLICTO_EXPLICITO_DUPLICADO,
    CONFLICTO_VENTA_OCUPADA, es_numero_explicito,
)


def bd():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE factura_conceptos(
        id INTEGER PRIMARY KEY, factura_id INTEGER, num_venta_match TEXT,
        match_method TEXT, match_confidence REAL, conflicto_factura TEXT);
      CREATE TRIGGER unica_i BEFORE INSERT ON factura_conceptos
      WHEN NEW.num_venta_match IS NOT NULL AND EXISTS(
        SELECT 1 FROM factura_conceptos x WHERE x.num_venta_match=NEW.num_venta_match
        AND x.factura_id!=NEW.factura_id)
      BEGIN SELECT RAISE(ABORT, 'venta ocupada por otra factura'); END;
    """)
    return c


def inferencia(venta="V1"):
    return {"num_venta": venta, "method": "codigo_exact", "confidence": 1.0}


def explicito(venta="V1"):
    return {"num_venta": venta, "method": "num_venta_proveedor_cauplas", "confidence": 1.0}


c = bd()
assert es_numero_explicito("num_venta_proveedor")  # KIM: número impreso en PDF
assert es_numero_explicito("num_venta_proveedor_cauplas")  # CAUPLAS: número en XML
assert not es_numero_explicito("codigo_exact")
c.execute("INSERT INTO factura_conceptos VALUES(1,10,'V1','codigo_exact',1,NULL)")
r = arbitrar_ocupacion(c, 20, inferencia())
assert r == {"match": None, "conflicto": CONFLICTO_VENTA_OCUPADA}

r = arbitrar_ocupacion(c, 20, explicito())
assert r["match"] == explicito()
vieja = c.execute("SELECT * FROM factura_conceptos WHERE id=1").fetchone()
assert vieja["num_venta_match"] is None and vieja["conflicto_factura"] == CONFLICTO_DESPLAZADA
c.execute("INSERT INTO factura_conceptos VALUES(2,20,'V1','num_venta_proveedor_cauplas',1,NULL)")

r = arbitrar_ocupacion(c, 30, explicito())
assert r == {"match": None, "conflicto": CONFLICTO_EXPLICITO_DUPLICADO}

# Varios conceptos de la misma factura sí pueden compartir venta.
r = arbitrar_ocupacion(c, 20, explicito())
assert r["match"]
c.execute("INSERT INTO factura_conceptos VALUES(3,20,'V1','num_venta_proveedor_cauplas',1,NULL)")

# La barrera SQLite rechaza el bypass directo.
try:
    c.execute("INSERT INTO factura_conceptos VALUES(4,40,'V1','codigo_exact',1,NULL)")
    raise AssertionError("el trigger permitió dos facturas")
except sqlite3.IntegrityError:
    pass

# Idempotencia: repetir no desplaza ni oscila.
r = arbitrar_ocupacion(c, 20, explicito(), 2)
assert r["match"] and c.execute("SELECT COUNT(*) FROM factura_conceptos WHERE num_venta_match='V1'").fetchone()[0] == 2
print("✅ TODO OK — ocupación única, prioridades, misma factura, trigger e idempotencia")
