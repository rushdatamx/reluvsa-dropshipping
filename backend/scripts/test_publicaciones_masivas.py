"""
Regresión del Módulo 2 (publicaciones masivas).

Fija las trampas que un refactor rompería EN SILENCIO — es decir, las que no
truenan: generan un Excel que se ve bien y se sube a Mercado Libre con datos
equivocados. Correr antes de commitear cambios en aplicaciones_kg.py,
generador_plantilla.py, precio_publicacion.py o parser_catalogo.py.

    /usr/bin/python3 scripts/test_publicaciones_masivas.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.aplicaciones_kg import parse_aplicaciones
from services.generador_plantilla import (
    COLUMNAS, ConfiguracionProveedor, MAX_TITULO, generar_filas,
)
from services.parser_catalogo import cruzar
from services.precio_publicacion import ParametrosPrecio, calcular_precio

_ok = _fail = 0


def check(nombre, cond, extra=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  ✓ {nombre}")
    else:
        _fail += 1
        print(f"  ✗ {nombre}  {extra}")


print("\n=== 1. HERENCIA de marca y modelo ===")
# 🔴 'V8 6.0L 2007-2009' NO es un modelo llamado V8: es el mismo Avalanche con
# otro motor. Sin herencia el título sale 'Bomba de agua P/ V8 6.0' — basura.
r = parse_aplicaciones("GM AVALANCHE V8 5.3L 2007-2010 | V8 6.0L 2007-2009 | SILVERADO 1500 V8 4.8L 2007-2010")
check("un fragmento sin modelo hereda marca Y modelo",
      r.aplicaciones[1].vehiculo == "GM AVALANCHE", r.aplicaciones[1].vehiculo)
check("un modelo nuevo hereda sólo la marca",
      r.aplicaciones[2].vehiculo == "GM SILVERADO 1500", r.aplicaciones[2].vehiculo)

# 🔴 Si el fragmento trae su PROPIA marca, no se le antepone la heredada.
r = parse_aplicaciones("ST CORDOBA L4 1.6L 2004-2009 | IBIZA L4 1.6L 2003-2008 | VW CROSSFOX L4 1.6L 2005-2012")
check("marca propia REEMPLAZA a la heredada (no 'ST VW Crossfox')",
      r.aplicaciones[2].vehiculo == "VW CROSSFOX", r.aplicaciones[2].vehiculo)
check("sin marca propia sí hereda (IBIZA es un Seat)",
      r.aplicaciones[1].vehiculo == "ST IBIZA", r.aplicaciones[1].vehiculo)

# ⚠️ El primer token NO siempre es marca: el catálogo trae códigos de pieza
# ('MANG', 'RAD', 'TA'). Tomarlos por marca metería basura en el título.
r = parse_aplicaciones("GM ASTRA L4 1.8L 2000-2003 | MANG L4 1.4L 2002-2004")
check("un código de pieza NO se toma por marca",
      r.aplicaciones[1].marca == "GM", r.aplicaciones[1].marca)

print("\n=== 2. TRUNCADO del catálogo (el export corta a 90 caracteres) ===")
# 🔴 Sin años no se puede publicar: el título de Gaby SIEMPRE los lleva y una
# compatibilidad sin años no le dice nada al comprador. Publicar una pieza
# diciendo que sirve para menos autos de los que sirve es peor que no publicarla.
r = parse_aplicaciones("CHR 300 V6 3.5L 2005-2010 | PACIFICA V6 3.5L 2005-2006 | SEBRING V6 3.5L")
check("una aplicación SIN años se marca truncada", r.aplicaciones[2].truncada)
check("las completas NO se marcan truncadas",
      not r.aplicaciones[0].truncada and not r.aplicaciones[1].truncada)
check("utiles excluye las truncadas", len(r.utiles) == 2, len(r.utiles))

r = parse_aplicaciones("GM AVALANCHE V8 5.3L 2007-2010 | V8")
check("un resto de 2 letras se marca truncado", r.aplicaciones[1].truncada)

print("\n=== 3. SEPARADORES y formatos del catálogo ===")
check("separa por pipe", len(parse_aplicaciones("FD KA L4 1.3L 1998-2000 | FT PALIO L4 1.6L 2004-2006").utiles) == 2)
check("separa por salto de línea (9 piezas del catálogo)",
      len(parse_aplicaciones("FD KA L4 1.3L 1998-2000\nFT PALIO L4 1.6L 2004-2006").utiles) == 2)
check("una sola aplicación sin separador (1,523 piezas)",
      len(parse_aplicaciones("FT PALIO L4 1.6L 2004-2006").utiles) == 1)
check("celda vacía no truena", len(parse_aplicaciones(None).aplicaciones) == 0)

# Años de 2 dígitos: '98-00' es 1998-2000, no 98 d.C.
a = parse_aplicaciones("FD FIESTA L4 1.3L 98-00").aplicaciones[0]
check("años de 2 dígitos se expanden al siglo correcto",
      (a.anio_desde, a.anio_hasta) == (1998, 2000), (a.anio_desde, a.anio_hasta))
# El catálogo escribe '2.OL' con la letra O en vez del cero.
a = parse_aplicaciones("AUDI TT QUATTRO L4 2.OL TFSI 2015-2018").aplicaciones[0]
check("'2.OL' (letra O) se lee como 2.0L", a.motor == "L4 2.0L", a.motor)

print("\n=== 4. PRECIO: el 13% se DIVIDE, no se suma ===")
# 🔴 La comisión de ML se cobra sobre el precio FINAL. Sumar 13% al costo deja
# la utilidad corta ~$15 por pieza; sobre 3,607 publicaciones no es redondeo.
p = ParametrosPrecio(iva=0.16, utilidad=0.50, comision_ml=0.13, envio_default=100.0)
precio = calcular_precio(346.84, "BOMBA DE AGUA", p)
base = 346.84 * 1.16 * 1.50
check("el precio despeja la comisión (divide)", abs(precio - (base + 100) / 0.87) < 0.02, precio)
check("dividir da MÁS que sumar (la diferencia es la utilidad perdida)",
      precio > base + 100 + base * 0.13)
comision_real = precio * 0.13
check("tras pagar comisión y envío queda la utilidad completa",
      abs((precio - comision_real - 100) - base) < 0.02)

# None y 0.0 son distintos: un 0.0 se publicaría en ML como precio real.
check("costo no numérico -> None (celda vacía), NUNCA 0.0", calcular_precio("n/a") is None)
check("costo 0 -> None", calcular_precio(0) is None)
check("costo negativo -> None", calcular_precio(-5) is None)
check("comisión del 100% no inventa precio",
      calcular_precio(100, "", ParametrosPrecio(comision_ml=1.0)) is None)

# ⬜ El envío está PENDIENTE: por default no suma. Que el default sea 0 y no un
# valor inventado es deliberado — un envío falso descuadra el precio en silencio.
check("sin tabla de envío el default es 0 (hueco visible, no inventado)",
      calcular_precio(100.0, "RADIADOR", ParametrosPrecio()) == round(100 * 1.16 * 1.5 / 0.87, 2))

print("\n=== 5. EXPANSIÓN: una pieza -> N publicaciones ===")
cfg = ConfiguracionProveedor(codigo_bodega="KG", marca="KeepOnGreen", categoria_ml="MLM163963")
piezas = [{
    "clave": "KGP-1106", "linea": "BOMBA DE AGUA", "costo": 500.0,
    "aplicaciones": "GM AVALANCHE V8 5.3L 2007-2010 | V8 6.0L 2007-2009 | SILVERADO 1500 V8 4.8L 2007-2010",
}]
filas = generar_filas(piezas, cfg)
check("3 aplicaciones -> 3 publicaciones", len(filas) == 3, len(filas))
check("todas comparten el MISMO SKU", len({f.sku for f in filas}) == 1)
check("todas comparten el MISMO precio", len({f.precio for f in filas}) == 1)
check("cada una tiene título DISTINTO", len({f.titulo for f in filas}) == 3)

# Las truncadas no generan publicación salvo que se pidan explícitamente.
piezas_t = [{"clave": "K1", "linea": "BOMBA DE AGUA", "costo": 100.0,
             "aplicaciones": "GM AVALANCHE V8 5.3L 2007-2010 | V8"}]
check("las truncadas NO se publican por default", len(generar_filas(piezas_t, cfg)) == 1)
check("incluir_truncadas=True sí las trae (para revisión)",
      len(generar_filas(piezas_t, cfg, incluir_truncadas=True)) == 2)

print("\n=== 6. TÍTULO: tope de 60 caracteres de Mercado Libre ===")
largo = [{"clave": "K", "linea": "KIT BANDA DISTRIBUCION C/BOMBA", "costo": 100.0,
          "aplicaciones": "VOLKSWAGEN CROSSFOX SPORTLINE L4 1.6L 2005-2018"}]
f = generar_filas(largo, cfg)[0]
check(f"título recortado a {MAX_TITULO}", len(f.titulo) <= MAX_TITULO, f"{len(f.titulo)}: {f.titulo}")
check("no se corta a media palabra (suelta años del final)", not f.titulo.endswith(("-", " 2")), f.titulo)

print("\n=== 7. DESCRIPCIÓN: cabeza variable + cuerpo fijo ===")
cfg_desc = ConfiguracionProveedor(codigo_bodega="KG", descripcion_base="GARANTÍA RELUVSA\nHorarios: L-V")
f = generar_filas(piezas, cfg_desc)[0]
check("lleva el nombre de la pieza", "BOMBA DE AGUA" in f.descripcion)
check("lleva el OEM (la clave del proveedor)", "KGP-1106" in f.descripcion)
check("lleva la sección Compatibilidades", "Compatibilidades:" in f.descripcion)
check("lleva el cuerpo fijo del proveedor", "GARANTÍA RELUVSA" in f.descripcion)
check("las compatibilidades listan TODAS las aplicaciones, no sólo la de su fila",
      "SILVERADO" in f.descripcion and "AVALANCHE" in f.descripcion)
# Las hermanas comparten descripción: es la misma pieza.
check("todas las filas de una pieza comparten descripción",
      len({x.descripcion for x in generar_filas(piezas, cfg_desc)}) == 1)

print("\n=== 8. PLANTILLA: las 36 columnas de Mercado Libre ===")
check("son exactamente 36 columnas", len(COLUMNAS) == 36, len(COLUMNAS))
check("no hay columnas repetidas", len(set(COLUMNAS)) == 36)
check("el orden arranca con Titulo/Categoria/Precio",
      COLUMNAS[:3] == ["Titulo", "Categoria", "Precio"])
check("las 6 bodegas van al final",
      COLUMNAS[-6:] == ["AG", "CAUPLAS", "KG", "KIM", "MATRIZ", "VAZLO"])
check("Imagen1..10 existen (van vacías, Gaby las pega)",
      all(f"Imagen{i}" in COLUMNAS for i in range(1, 11)))

print("\n=== 9. CRUCE contra lo ya publicado ===")
# ⚠️ La columna Q trae paquetes con '&': sin partirla, un SKU publicado dentro
# de un paquete se contaría como faltante.
cat = [{"clave": "KGP-1449"}, {"clave": "KGP-9999"}, {"clave": "NO625HB"}]
res = cruzar(cat, {"KGP-1449", "NO625HB"})
check("separa publicadas de faltantes", (res["ya_publicadas"], res["faltantes"]) == (2, 1))
check("el total cuadra", res["total_catalogo"] == 3)
check("el cruce ignora mayúsculas", cruzar([{"clave": "kgp-1449"}], {"KGP-1449"})["ya_publicadas"] == 1)

print(f"\n{'='*54}")
print(f"  {_ok} pasaron · {_fail} fallaron")
print(f"{'='*54}\n")
sys.exit(1 if _fail else 0)
