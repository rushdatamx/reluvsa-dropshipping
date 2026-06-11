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
    deposito TEXT,
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
    -- num_venta NO es FK a ventas_ml a propósito: los reportes de colecta y de
    -- ventas ML llegan en cortes de fecha distintos, así que un envío puede
    -- existir antes de que su venta esté cargada (~88% de los casos en datos
    -- reales). El cruce se resuelve por JOIN cuando ambas filas existan.
    num_venta TEXT,
    -- num_venta_ml: el # de venta del reporte de Ventas ML, resuelto al parsear.
    -- ML asigna a veces 2 folios distintos a la misma venta (uno en el reporte de
    -- ventas, otro en el de colecta), así que num_venta NO cruza de forma fiable.
    -- Regla de Gaby: cruzar por fecha + título. Resolvemos ese cruce una sola vez
    -- aquí y guardamos el num_venta canónico de ML para que los JOIN sean por
    -- igualdad de columna indexada (rápidos), no fuzzy en cada query.
    num_venta_ml TEXT,
    -- Confianza del cruce envío->venta: 1.0 = num_venta directo, <1 = fecha+título
    -- fuzzy, NULL = sin cruce. Sirve para que Gaby revise los cruces dudosos.
    match_cruce_confianza REAL,
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
-- idx_envios_venta_ml NO va aquí: en una BD existente la columna num_venta_ml aún
-- no existe cuando corre este SCHEMA (CREATE TABLE IF NOT EXISTS no la agrega), y el
-- índice reventaría todo el executescript antes de la migración. Lo crea
-- _migrar_columnas_cruce() después del ALTER TABLE ADD COLUMN.
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
    # KeepOnGreen factura a través de SUMINISTRO TRANSAMERICANO DE REFACCIONES
    # (RFC confirmado con su CFDI real el 2026-06-09; antes estaba "PENDIENTE").
    ("KEEPONGREEN", "STR910211DT2", "KG", None, None),
]


def init_database():
    """Crea tablas y siembra los 5 proveedores conocidos."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.executescript(SCHEMA)

        _migrar_envios_sin_fk(cursor)
        _migrar_columnas_cruce(cursor)
        _migrar_rfc_keepongreen(cursor)
        _migrar_proveedor_desde_lugar_indicado(cursor)
        _migrar_columna_deposito(cursor)

        cursor.execute("SELECT COUNT(*) as c FROM proveedores")
        if cursor.fetchone()["c"] == 0:
            cursor.executemany(
                "INSERT INTO proveedores (nombre, rfc, codigo_bodega, contacto_email, contacto_nombre) VALUES (?, ?, ?, ?, ?)",
                SEED_PROVEEDORES,
            )

        _bootstrap_admin(cursor)
        _bootstrap_proveedores(cursor)


# Dominio interno para usuarios proveedor que entran con username (no email real).
# El login normaliza "cauplas" -> "cauplas@reluvsa.local" antes de buscar.
PROVEEDOR_LOGIN_DOMAIN = "reluvsa.local"


def username_a_email(identificador: str) -> str:
    """Normaliza un identificador de login: si no trae '@', le agrega el dominio
    interno de proveedores. Así un proveedor entra con 'cauplas' (su código en
    minúsculas) en lugar de un correo real."""
    ident = (identificador or "").strip().lower()
    if not ident or "@" in ident:
        return ident
    return f"{ident}@{PROVEEDOR_LOGIN_DOMAIN}"


def _migrar_envios_sin_fk(cursor):
    """Migración idempotente: versiones viejas crearon envios_colecta con una FK
    num_venta -> ventas_ml(num_venta), que rechaza envíos cuya venta aún no está
    cargada (cortes de fecha distintos). Si la tabla todavía tiene esa FK, la
    recreamos sin ella preservando las filas existentes. CREATE TABLE IF NOT
    EXISTS por sí solo no altera una tabla ya creada, por eso hace falta esto.
    """
    fks = cursor.execute("PRAGMA foreign_key_list(envios_colecta)").fetchall()
    tiene_fk_ventas = any(fk["table"] == "ventas_ml" for fk in fks)
    if not tiene_fk_ventas:
        return  # ya está migrada (o es BD nueva con el schema correcto)

    # foreign_keys está ON; hay que apagarlo para el swap de tablas.
    cursor.execute("PRAGMA foreign_keys=OFF")
    cursor.execute("ALTER TABLE envios_colecta RENAME TO envios_colecta_old")
    # Recreamos solo esta tabla (sin la FK a ventas_ml), no todo el SCHEMA.
    cursor.execute(
        """CREATE TABLE envios_colecta (
            num_envio TEXT PRIMARY KEY,
            num_venta TEXT,
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
        )"""
    )
    cursor.execute(
        """INSERT INTO envios_colecta
           (num_envio, num_venta, fecha_venta, titulo, tiempo_max_envio, tiempo_real_envio,
            lugar_indicado, lugar_real, lugar_override, proveedor_id, cumplio_sla,
            excluido_analisis, fecha_subida)
           SELECT num_envio, num_venta, fecha_venta, titulo, tiempo_max_envio, tiempo_real_envio,
                  lugar_indicado, lugar_real, lugar_override, proveedor_id, cumplio_sla,
                  excluido_analisis, fecha_subida
           FROM envios_colecta_old"""
    )
    cursor.execute("DROP TABLE envios_colecta_old")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_envios_venta ON envios_colecta(num_venta)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_envios_proveedor ON envios_colecta(proveedor_id)")
    cursor.execute("PRAGMA foreign_keys=ON")
    print("[migracion] envios_colecta recreada sin FK a ventas_ml.")


def _migrar_columnas_cruce(cursor):
    """Migración idempotente: agrega las columnas num_venta_ml y
    match_cruce_confianza a envios_colecta si la BD existente (el volumen de
    Railway) aún no las tiene. CREATE TABLE IF NOT EXISTS no altera una tabla ya
    creada, por eso hace falta ALTER TABLE ADD COLUMN explícito.
    """
    cols = {c["name"] for c in cursor.execute("PRAGMA table_info(envios_colecta)").fetchall()}
    if "num_venta_ml" not in cols:
        cursor.execute("ALTER TABLE envios_colecta ADD COLUMN num_venta_ml TEXT")
        print("[migracion] envios_colecta.num_venta_ml agregada.")
    if "match_cruce_confianza" not in cols:
        cursor.execute("ALTER TABLE envios_colecta ADD COLUMN match_cruce_confianza REAL")
        print("[migracion] envios_colecta.match_cruce_confianza agregada.")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_envios_venta_ml ON envios_colecta(num_venta_ml)")


def _migrar_rfc_keepongreen(cursor):
    """Migración idempotente: actualiza el RFC de KeepOnGreen de 'PENDIENTE' al
    real (STR910211DT2, confirmado con su CFDI el 2026-06-09) en BDs ya sembradas
    —el seed de proveedores no re-corre si la tabla ya tiene filas, así que el
    cambio en SEED_PROVEEDORES no alcanza al volumen de Railway por sí solo.
    """
    cursor.execute(
        "UPDATE proveedores SET rfc = ? WHERE codigo_bodega = 'KG' AND rfc = 'PENDIENTE'",
        ("STR910211DT2",),
    )
    if cursor.rowcount:
        print("[migracion] RFC de KeepOnGreen actualizado a STR910211DT2.")


def _migrar_columna_deposito(cursor):
    """Migración idempotente: agrega ventas_ml.deposito si la BD existente aún no la
    tiene. El reporte de Ventas ML trae una columna 'Depósito' (MATRIZ/KIM/CAUPLAS/...)
    que marca la bodega de cada venta; la usamos para ocultar el ruido de MATRIZ
    (bodega propia, no dropshipping) por defecto. CREATE TABLE IF NOT EXISTS no altera
    una tabla ya creada, por eso hace falta ALTER TABLE ADD COLUMN. El índice va aquí
    (no en el SCHEMA) porque la columna recién existe tras el ALTER.
    """
    cols = {c["name"] for c in cursor.execute("PRAGMA table_info(ventas_ml)").fetchall()}
    if "deposito" not in cols:
        cursor.execute("ALTER TABLE ventas_ml ADD COLUMN deposito TEXT")
        print("[migracion] ventas_ml.deposito agregada.")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ventas_deposito ON ventas_ml(deposito)")


def _migrar_proveedor_desde_lugar_indicado(cursor):
    """Migración idempotente: re-resuelve envios_colecta.proveedor_id desde la
    columna J (lugar_indicado) en vez de K (lugar_real). Cambio de regla de Gaby
    (2026-06-11): ML casi nunca llena bien la K, así que el proveedor se deriva del
    'Lugar indicado'. Las BDs ya cargadas tienen el proveedor resuelto con la lógica
    vieja (K); aquí lo recalculamos. RESPETA lugar_override: si un envío fue
    reasignado a mano, su proveedor sale del override, no de J.

    Se ejecuta en cada arranque (idempotente: el resultado converge). En prod la BD
    está vacía hoy, así que no toca filas hasta que se vuelva a subir colecta.
    """
    # ¿Hay envíos? (evita trabajo si la tabla está vacía, p.ej. prod recién entregado)
    cols = {c["name"] for c in cursor.execute("PRAGMA table_info(envios_colecta)").fetchall()}
    if "lugar_indicado" not in cols:
        return  # tabla aún no migrada con esa columna; nada que hacer

    # codigo_bodega -> id (los valores que no matchean, como MATRIZ/Agencia ML/Sin
    # información, dejan proveedor_id en NULL, que es justo lo correcto).
    prov = {
        r["codigo_bodega"]: r["id"]
        for r in cursor.execute("SELECT id, codigo_bodega FROM proveedores").fetchall()
    }

    envios = cursor.execute(
        "SELECT num_envio, lugar_indicado, lugar_override, proveedor_id FROM envios_colecta"
    ).fetchall()

    actualizados = 0
    for e in envios:
        # El override manda sobre J; si no hay override, se usa el lugar indicado (J).
        fuente = (e["lugar_override"] or e["lugar_indicado"] or "").strip().upper()
        nuevo_prov = prov.get(fuente)  # None si no es un código de proveedor conocido
        if nuevo_prov != e["proveedor_id"]:
            cursor.execute(
                "UPDATE envios_colecta SET proveedor_id = ? WHERE num_envio = ?",
                (nuevo_prov, e["num_envio"]),
            )
            actualizados += 1

    if actualizados:
        print(f"[migracion] proveedor_id re-resuelto desde 'Lugar indicado' (J): {actualizados} envíos.")


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


def _bootstrap_proveedores(cursor):
    """Crea los usuarios proveedor desde la variable PROVEEDOR_BOOTSTRAP.

    Cada proveedor entra con un username simple (su código de bodega en
    minúsculas, p.ej. 'cauplas') + password, sin necesidad de un correo real.
    Internamente el username se guarda en la columna email como
    'cauplas@reluvsa.local' para no tocar el schema ni el login.

    Formato (una línea por proveedor, ':' como separador):
        CODIGO:password
    p.ej.:
        CAUPLAS:Pass1
        KIM:Pass2
    También se acepta CODIGO:username:password si en el futuro se quiere un
    username distinto del código de bodega.

    Idempotente: no duplica usuarios ya existentes en redeploys. Solo crea
    proveedores cuyo codigo_bodega ya esté sembrado en la tabla proveedores.
    """
    import bcrypt

    raw = os.getenv("PROVEEDOR_BOOTSTRAP") or ""
    if not raw.strip():
        return

    for linea in raw.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue

        partes = [p.strip() for p in linea.split(":")]
        if len(partes) == 2:
            codigo, password = partes
            username = codigo.lower()
        elif len(partes) == 3:
            codigo, username, password = partes
            username = username.lower()
        else:
            print(f"[bootstrap] Línea PROVEEDOR_BOOTSTRAP ignorada (formato inválido): {linea}")
            continue

        codigo = codigo.upper()
        if not codigo or not password:
            print(f"[bootstrap] Línea PROVEEDOR_BOOTSTRAP ignorada (código o password vacío): {linea}")
            continue

        prov = cursor.execute(
            "SELECT id, nombre FROM proveedores WHERE codigo_bodega = ?", (codigo,)
        ).fetchone()
        if not prov:
            print(f"[bootstrap] No existe proveedor con codigo_bodega={codigo}; usuario no creado.")
            continue

        email = username_a_email(username)
        existing = cursor.execute("SELECT id FROM usuarios WHERE email = ?", (email,)).fetchone()
        if existing:
            continue  # ya existe; no tocar (idempotente)

        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cursor.execute(
            "INSERT INTO usuarios (email, password_hash, rol, proveedor_id) VALUES (?, ?, 'proveedor', ?)",
            (email, password_hash, prov["id"]),
        )
        print(f"[bootstrap] Proveedor '{username}' creado para {prov['nombre']} ({codigo}).")


if __name__ == "__main__":
    init_database()
    print(f"Base de datos inicializada en: {DATABASE_PATH}")
