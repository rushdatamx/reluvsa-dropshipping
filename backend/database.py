"""
Conexión SQLite + schema para portal dropshipping RELUVSA.
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    str(Path(__file__).parent.parent / "data" / "dropshipping.db"),
)


def get_connection():
    Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS proveedores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    rfc TEXT UNIQUE NOT NULL,
    codigo_bodega TEXT UNIQUE NOT NULL,
    contacto_email TEXT,
    contacto_nombre TEXT,
    activo INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    rol TEXT NOT NULL CHECK(rol IN ('admin', 'proveedor')),
    proveedor_id INTEGER REFERENCES proveedores(id) ON DELETE CASCADE,
    activo INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ventas_ml (
    num_venta TEXT PRIMARY KEY,
    sku TEXT,
    fecha_venta TIMESTAMP,
    estado TEXT,
    titulo TEXT,
    unidades INTEGER,
    total REAL,
    comprador TEXT,
    comprador_estado TEXT,
    forma_entrega TEXT,
    fecha_subida TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    factura_adjunta_ml TEXT,
    devolucion_unidades INTEGER DEFAULT 0,
    reclamo_abierto INTEGER DEFAULT 0,
    reclamo_cerrado INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS envios_colecta (
    num_envio TEXT PRIMARY KEY,
    num_venta TEXT REFERENCES ventas_ml(num_venta) ON DELETE SET NULL,
    fecha_venta TIMESTAMP,
    titulo TEXT,
    tiempo_max_envio TEXT,
    tiempo_real_envio TEXT,
    lugar_indicado TEXT,
    lugar_real TEXT,
    lugar_override TEXT,
    proveedor_id INTEGER REFERENCES proveedores(id) ON DELETE SET NULL,
    cumplio_sla INTEGER,
    excluido_analisis INTEGER DEFAULT 0,
    fecha_subida TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS facturas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id INTEGER REFERENCES proveedores(id) ON DELETE CASCADE,
    uuid_cfdi TEXT UNIQUE,
    serie TEXT,
    folio TEXT,
    rfc_emisor TEXT,
    rfc_receptor TEXT,
    fecha_factura TIMESTAMP,
    total REAL,
    moneda TEXT,
    pdf_path TEXT,
    xml_path TEXT,
    fecha_subida TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    subido_por INTEGER REFERENCES usuarios(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS factura_conceptos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    factura_id INTEGER REFERENCES facturas(id) ON DELETE CASCADE,
    codigo_prov TEXT,
    descripcion TEXT,
    cantidad REAL,
    precio_unitario REAL,
    importe REAL,
    num_venta_match TEXT REFERENCES ventas_ml(num_venta) ON DELETE SET NULL,
    match_method TEXT,
    match_confidence REAL
);

CREATE TABLE IF NOT EXISTS incidencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    num_venta TEXT REFERENCES ventas_ml(num_venta) ON DELETE CASCADE,
    proveedor_id INTEGER REFERENCES proveedores(id) ON DELETE SET NULL,
    tipo TEXT NOT NULL CHECK(tipo IN ('devolucion', 'producto_equivocado', 'no_entregado', 'factura_tardia', 'factura_incorrecta', 'otro')),
    descripcion TEXT,
    estado TEXT DEFAULT 'abierta' CHECK(estado IN ('abierta', 'en_revision', 'resuelta')),
    creada_por INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS catalogos_proveedor (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id INTEGER REFERENCES proveedores(id) ON DELETE CASCADE,
    nombre_archivo TEXT,
    fecha_subida TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    subido_por INTEGER REFERENCES usuarios(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS catalogo_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalogo_id INTEGER REFERENCES catalogos_proveedor(id) ON DELETE CASCADE,
    sku_proveedor TEXT NOT NULL,
    descripcion TEXT,
    codigo_barras TEXT,
    precio_mayoreo REAL,
    precio_publico REAL,
    extra_json TEXT
);

CREATE TABLE IF NOT EXISTS publicaciones_ml (
    id_ml TEXT PRIMARY KEY,
    titulo TEXT,
    sku_seller TEXT,
    att_seller_sku TEXT,
    marca TEXT,
    proveedor_id INTEGER REFERENCES proveedores(id) ON DELETE SET NULL,
    status TEXT,
    precio REAL,
    cantidad INTEGER,
    stock_matriz INTEGER,
    fecha_subida TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plantillas_ml (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id INTEGER REFERENCES proveedores(id) ON DELETE CASCADE,
    nombre TEXT NOT NULL,
    campos_fijos_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ventas_sku ON ventas_ml(sku);
CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON ventas_ml(fecha_venta);
CREATE INDEX IF NOT EXISTS idx_envios_venta ON envios_colecta(num_venta);
CREATE INDEX IF NOT EXISTS idx_envios_proveedor ON envios_colecta(proveedor_id);
CREATE INDEX IF NOT EXISTS idx_facturas_proveedor ON facturas(proveedor_id);
CREATE INDEX IF NOT EXISTS idx_conceptos_factura ON factura_conceptos(factura_id);
CREATE INDEX IF NOT EXISTS idx_conceptos_venta ON factura_conceptos(num_venta_match);
CREATE INDEX IF NOT EXISTS idx_incidencias_proveedor ON incidencias(proveedor_id);
CREATE INDEX IF NOT EXISTS idx_publicaciones_sku ON publicaciones_ml(att_seller_sku);
"""


SEED_PROVEEDORES = [
    ("QUALITY HOSES", "QHO180116NW0", "CAUPLAS", None, None),
    ("KIMS AUTO CORPORATION", "KAC1601193F6", "KIM", None, None),
    ("ARGENPARTS", "ARG041025AU2", "AG", None, None),
    ("VAZLO COMERCIAL", "VIM990605M8A", "VAZLO", None, None),
    ("KEEPONGREEN", "PENDIENTE", "KG", None, None),
]


def init_database():
    """Crea tablas y siembra los 5 proveedores conocidos."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.executescript(SCHEMA)

        cursor.execute("SELECT COUNT(*) as c FROM proveedores")
        if cursor.fetchone()["c"] == 0:
            cursor.executemany(
                "INSERT INTO proveedores (nombre, rfc, codigo_bodega, contacto_email, contacto_nombre) VALUES (?, ?, ?, ?, ?)",
                SEED_PROVEEDORES,
            )

        _bootstrap_admin(cursor)


def _bootstrap_admin(cursor):
    """Crea el admin inicial desde variables de entorno (ADMIN_BOOTSTRAP_EMAIL /
    ADMIN_BOOTSTRAP_PASSWORD) si todavía no existe ningún admin. Idempotente:
    no duplica en redeploys. Evita tener que usar la Console de Railway.
    """
    email = (os.getenv("ADMIN_BOOTSTRAP_EMAIL") or "").strip().lower()
    password = os.getenv("ADMIN_BOOTSTRAP_PASSWORD") or ""
    if not email or not password:
        return

    cursor.execute("SELECT id FROM usuarios WHERE email = ?", (email,))
    if cursor.fetchone():
        return  # ya existe ese email; no tocar

    import bcrypt

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    cursor.execute(
        "INSERT INTO usuarios (email, password_hash, rol) VALUES (?, ?, 'admin')",
        (email, password_hash),
    )
    print(f"[bootstrap] Admin {email} creado desde variables de entorno.")


if __name__ == "__main__":
    init_database()
    print(f"Base de datos inicializada en: {DATABASE_PATH}")
