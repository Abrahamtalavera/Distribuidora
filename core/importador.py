"""
Importa facturas desde el Excel de detalle de factura (export de SAP).

Regla de negocio (Cambio #2):
- Solo se agregan facturas NUEVAS (por Número_Factura). Las que ya existen
  en el sistema no se modifican, aunque el Excel traiga datos distintos.
- Los códigos de cliente "OCASIONAL" se agrupan en un único cliente genérico.
"""
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import openpyxl
from django.db import transaction

from core.models import (
    Cliente,
    Factura,
    FacturaDetalle,
    ModalidadPago,
    Producto,
    Ruta,
    Vendedor,
)

CLIENTE_OCASIONAL_CODIGO = "OCASIONAL"
CLIENTE_OCASIONAL_NOMBRE = "Cliente Ocasional"

MODALIDAD_MAP = {
    "crédito": "CREDITO",
    "credito": "CREDITO",
    "contado": "CONTADO",
    "consignación": "CONSIGNACION",
    "consignacion": "CONSIGNACION",
}

COLUMNAS_ESPERADAS = [
    "Código_Cliente",
    "Nombre_Cliente",
    "Municipio",
    "Sucursal",
    "Canal",
    "Territorio",
    "Fecha",
    "Vendedor",
    "Nombre de serie",
    "Número_Factura",
    "Familia",
    "Número de artículo",
    "Descripción artículo/serv.",
    "Cantidad",
    "Prec_Antes_Desc",
    "Descuento",
    "PrecioConDesc",
    "Total líneas",
    "IVA",
    "Tipo_Pago",
    "Costo del artículo",
]


class ImportacionError(Exception):
    pass


def _dec(valor, default="0"):
    if valor is None or valor == "":
        return Decimal(default)
    try:
        return Decimal(str(valor))
    except InvalidOperation:
        return Decimal(default)


def _leer_filas(archivo):
    wb = openpyxl.load_workbook(archivo, data_only=True)
    ws = wb[wb.sheetnames[0]]
    headers = [c.value for c in ws[1]]
    faltantes = [c for c in COLUMNAS_ESPERADAS if c not in headers]
    if faltantes:
        raise ImportacionError(
            "El Excel no tiene las columnas esperadas. Faltan: " + ", ".join(faltantes)
        )
    col_idx = {h: i for i, h in enumerate(headers) if h}

    filas = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[col_idx["Código_Cliente"]] is None:
            continue  # fila vacía de relleno al final del archivo
        filas.append({h: row[col_idx[h]] for h in COLUMNAS_ESPERADAS})
    return filas


def _obtener_cliente(cache, codigo, nombre, municipio, sucursal, canal, territorio):
    codigo = (codigo or "").strip()
    if codigo.upper() == CLIENTE_OCASIONAL_CODIGO:
        codigo_final = CLIENTE_OCASIONAL_CODIGO
        nombre_final = CLIENTE_OCASIONAL_NOMBRE
    else:
        codigo_final = codigo
        nombre_final = nombre or codigo

    if codigo_final in cache:
        return cache[codigo_final]

    cliente, _creado = Cliente.objects.get_or_create(
        codigo=codigo_final,
        defaults={
            "nombre": nombre_final,
            "municipio": municipio or "",
            "sucursal": sucursal or "",
            "canal": canal or "",
            "territorio": territorio or "",
        },
    )
    cache[codigo_final] = cliente
    return cliente


def _obtener_vendedor(cache, nombre):
    nombre = (nombre or "").strip()
    if not nombre:
        return None
    if nombre in cache:
        return cache[nombre]
    vendedor, _creado = Vendedor.objects.get_or_create(
        nombre=nombre, defaults={"tipo": "VENDEDOR"}
    )
    cache[nombre] = vendedor
    return vendedor


def _obtener_producto(cache, codigo, nombre, familia, costo):
    codigo = (codigo or "").strip()
    if codigo in cache:
        return cache[codigo]
    producto, _creado = Producto.objects.get_or_create(
        codigo=codigo,
        defaults={
            "nombre": nombre or codigo,
            "familia": familia or "",
            "costo": _dec(costo),
        },
    )
    cache[codigo] = producto
    return producto


def _obtener_modalidad(cache, tipo_pago):
    clave = (tipo_pago or "").strip().lower()
    codigo = MODALIDAD_MAP.get(clave, "CONTADO")
    if codigo in cache:
        return cache[codigo]
    modalidad = ModalidadPago.objects.get(codigo=codigo)
    cache[codigo] = modalidad
    return modalidad


@transaction.atomic
def importar_excel(archivo, nombre_archivo=""):
    """
    Procesa el Excel y crea únicamente las facturas nuevas.
    Devuelve un resumen (dict) con contadores para mostrar al usuario.
    """
    filas = _leer_filas(archivo)

    # Agrupar líneas por Número_Factura conservando el orden de aparición
    facturas_excel = OrderedDict()
    for fila in filas:
        nf = str(fila["Número_Factura"]).strip()
        facturas_excel.setdefault(nf, []).append(fila)

    numeros_en_excel = list(facturas_excel.keys())
    existentes = set(
        Factura.objects.filter(numero_factura__in=numeros_en_excel).values_list(
            "numero_factura", flat=True
        )
    )

    cache_clientes, cache_vendedores, cache_productos, cache_modalidades = {}, {}, {}, {}

    facturas_omitidas_existentes = 0
    clientes_creados_antes = Cliente.objects.count()
    productos_creados_antes = Producto.objects.count()
    vendedores_creados_antes = Vendedor.objects.count()

    # Paso 1: preparar en memoria los encabezados de factura nuevos (sin
    # tocar la base de datos todavía), resolviendo/creando cliente, vendedor,
    # modalidad y producto por el camino (estos catálogos son pocos miles de
    # registros como mucho, así que get_or_create uno por uno es aceptable).
    facturas_a_crear = []  # lista de (numero_factura, Factura sin guardar, lineas_excel)
    for numero_factura, lineas in facturas_excel.items():
        if numero_factura in existentes:
            facturas_omitidas_existentes += 1
            continue

        primera = lineas[0]
        cliente = _obtener_cliente(
            cache_clientes,
            primera["Código_Cliente"],
            primera["Nombre_Cliente"],
            primera["Municipio"],
            primera["Sucursal"],
            primera["Canal"],
            primera["Territorio"],
        )
        vendedor = _obtener_vendedor(cache_vendedores, primera["Vendedor"])
        modalidad = _obtener_modalidad(cache_modalidades, primera["Tipo_Pago"])

        fecha = primera["Fecha"]
        fecha_emision = fecha.date() if hasattr(fecha, "date") else fecha

        subtotal_total = Decimal("0")
        impuestos_total = Decimal("0")
        total_total = Decimal("0")
        lineas_preparadas = []
        for fila in lineas:
            producto = _obtener_producto(
                cache_productos,
                fila["Número de artículo"],
                fila["Descripción artículo/serv."],
                fila["Familia"],
                fila["Costo del artículo"],
            )
            cantidad = _dec(fila["Cantidad"])
            precio_lista = _dec(fila["Prec_Antes_Desc"])
            descuento = _dec(fila["Descuento"])
            precio_unitario = _dec(fila["PrecioConDesc"])
            subtotal_linea = _dec(fila["Total líneas"])
            iva_linea = _dec(fila["IVA"])
            subtotal_total += subtotal_linea
            impuestos_total += iva_linea
            total_total += subtotal_linea + iva_linea
            lineas_preparadas.append(
                dict(
                    producto=producto,
                    cantidad_facturada=cantidad,
                    precio_lista=precio_lista,
                    descuento=descuento,
                    precio_unitario=precio_unitario,
                    subtotal=subtotal_linea,
                    iva=iva_linea,
                )
            )

        factura = Factura(
            numero_factura=numero_factura,
            serie=primera.get("Nombre de serie") or "",
            cliente=cliente,
            vendedor=vendedor,
            modalidad_pago=modalidad,
            fecha_emision=fecha_emision,
            # Valor por defecto (Cambio #8): un día después de la fecha de
            # emisión. El planeador la puede cambiar después, factura por
            # factura, desde el listado de Facturas.
            fecha_sugerida_entrega=fecha_emision + timedelta(days=1),
            subtotal=subtotal_total,
            impuestos=impuestos_total,
            total=total_total,
            origen_importacion=nombre_archivo,
        )
        facturas_a_crear.append((factura, lineas_preparadas))

    # Paso 2: insertar todos los encabezados de factura en lotes grandes.
    # bulk_create en PostgreSQL devuelve los ids asignados, así que después
    # podemos usar factura.id para las líneas de detalle.
    LOTE = 1000
    Factura.objects.bulk_create(
        [f for f, _ in facturas_a_crear], batch_size=LOTE
    )

    # Paso 3: insertar todas las líneas de detalle en lotes grandes.
    detalles = []
    for factura, lineas_preparadas in facturas_a_crear:
        for datos in lineas_preparadas:
            detalles.append(FacturaDetalle(factura=factura, **datos))
    FacturaDetalle.objects.bulk_create(detalles, batch_size=LOTE)

    return {
        "facturas_en_excel": len(facturas_excel),
        "facturas_creadas": len(facturas_a_crear),
        "facturas_omitidas_existentes": facturas_omitidas_existentes,
        "lineas_creadas": len(detalles),
        "clientes_nuevos": Cliente.objects.count() - clientes_creados_antes,
        "productos_nuevos": Producto.objects.count() - productos_creados_antes,
        "vendedores_nuevos": Vendedor.objects.count() - vendedores_creados_antes,
    }
