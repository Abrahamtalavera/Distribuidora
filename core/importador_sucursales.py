"""
Cambio #25: importa el catálogo de sucursales (direcciones de entrega) de
los clientes, desde el maestro de clientes de SAP.

A diferencia de importar_excel (facturas, core/importador.py), este SÍ
actualiza los registros que ya existen: es un catálogo que cambia de vez en
cuando (direcciones corregidas, sucursales nuevas), no un historial de
transacciones que no se deba tocar. La clave es (código de cliente, nombre
de la sucursal); se puede volver a subir el mismo archivo o uno más
reciente sin duplicar nada.

Corrección (2026-10-29): la primera versión hacía una consulta a la base de
datos POR CADA FILA del Excel (get_or_create de Cliente + update_or_create
de Sucursal). Con el maestro real (~4,600 filas) eso son varios miles de
consultas una por una, y el servidor se quedaba sin tiempo a mitad de la
importación ("Internal Server Error" a los 20-30 segundos, el límite de
gunicorn). Ahora se hace en bloques: una consulta para ver qué Clientes ya
existen, un bulk_create para los que falten, y un solo bulk_create con
"upsert" (ON CONFLICT ... DO UPDATE) para todas las Sucursales del archivo.
"""

from django.db import transaction

from core.models import Cliente, Sucursal

COLUMNAS_ESPERADAS = [
    "Código SN",
    "Nombre SN",
    "Cod Grupo",
    "Grupo",
    "Nombre de dirección",
    "Departamento",
    "Territorio",
    "Municipio",
    "Direccion",
    "Canal",
    "EstadoCliente",
]

# Campos de Sucursal que se actualizan cuando el Excel trae una fila que ya
# existe (cliente + nombre). "cliente" y "nombre" no van aquí: son la clave.
CAMPOS_ACTUALIZABLES = [
    "direccion",
    "departamento",
    "municipio",
    "territorio",
    "canal",
    "cod_grupo",
    "grupo",
    "activo",
]

LOTE = 500


class ImportacionError(Exception):
    pass


def _leer_filas(archivo):
    import openpyxl

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
        if row[col_idx["Código SN"]] is None:
            continue  # fila vacía de relleno al final del archivo
        filas.append({h: row[col_idx[h]] for h in COLUMNAS_ESPERADAS})
    return filas


def _texto(valor):
    return str(valor).strip() if valor is not None else ""


def _cod_grupo(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


@transaction.atomic
def importar_excel(archivo, nombre_archivo=""):
    """
    Procesa el Excel y crea o actualiza las Sucursales (y los Clientes que
    todavía no existan). Devuelve un resumen (dict) con contadores para
    mostrar al usuario.
    """
    filas = _leer_filas(archivo)

    # Paso 1: separar las filas válidas (con código de cliente y nombre de
    # sucursal) de las que se omiten, sin tocar la base de datos todavía.
    validas = []
    filas_sin_codigo_o_nombre = 0
    for fila in filas:
        codigo = _texto(fila["Código SN"])
        nombre_sucursal = _texto(fila["Nombre de dirección"])
        if not codigo or not nombre_sucursal:
            filas_sin_codigo_o_nombre += 1
            continue
        validas.append((codigo, nombre_sucursal, fila))

    # Paso 2: asegurar los Clientes con dos consultas en bloque (una para
    # ver cuáles ya existen y un bulk_create para los que falten), en vez de
    # un get_or_create por cada una de las filas.
    codigos = {codigo for codigo, _, _ in validas}
    clientes_por_codigo = {
        c.codigo: c for c in Cliente.objects.filter(codigo__in=codigos)
    }
    nombre_cliente_por_codigo = {}
    for codigo, _, fila in validas:
        nombre_cliente_por_codigo.setdefault(codigo, _texto(fila["Nombre SN"]) or codigo)
    faltantes = [
        Cliente(codigo=codigo, nombre=nombre_cliente_por_codigo[codigo])
        for codigo in codigos
        if codigo not in clientes_por_codigo
    ]
    if faltantes:
        Cliente.objects.bulk_create(faltantes, batch_size=LOTE)
        for cliente in Cliente.objects.filter(codigo__in=[c.codigo for c in faltantes]):
            clientes_por_codigo[cliente.codigo] = cliente
    clientes_nuevos = len(faltantes)

    # Paso 3: una sola consulta para saber qué pares (cliente, sucursal) ya
    # existen -- así se puede contar creadas vs. actualizadas sin necesidad
    # de otra consulta por fila -- y un solo bulk_create "upsert" (inserta
    # las nuevas, actualiza las que ya existían) para todo el archivo.
    existentes = set(
        Sucursal.objects.filter(cliente_id__in=[c.id for c in clientes_por_codigo.values()])
        .values_list("cliente_id", "nombre")
    )

    # Diccionario en vez de lista: si el mismo cliente+sucursal aparece dos
    # veces en el archivo, se queda con la última fila (igual que antes con
    # update_or_create) y no se repite -- ON CONFLICT no admite tocar la
    # misma fila dos veces dentro de una sola operación.
    objetos_por_clave = {}
    for codigo, nombre_sucursal, fila in validas:
        cliente = clientes_por_codigo[codigo]
        clave = (cliente.id, nombre_sucursal)
        activo = _texto(fila["EstadoCliente"]).lower() != "inactivo"
        objetos_por_clave[clave] = Sucursal(
            cliente=cliente,
            nombre=nombre_sucursal,
            direccion=_texto(fila["Direccion"]),
            departamento=_texto(fila["Departamento"]),
            municipio=_texto(fila["Municipio"]),
            territorio=_texto(fila["Territorio"]),
            canal=_texto(fila["Canal"]),
            cod_grupo=_cod_grupo(fila["Cod Grupo"]),
            grupo=_texto(fila["Grupo"]),
            activo=activo,
        )

    sucursales_creadas = sum(1 for clave in objetos_por_clave if clave not in existentes)
    sucursales_actualizadas = len(objetos_por_clave) - sucursales_creadas

    if objetos_por_clave:
        Sucursal.objects.bulk_create(
            list(objetos_por_clave.values()),
            update_conflicts=True,
            unique_fields=["cliente", "nombre"],
            update_fields=CAMPOS_ACTUALIZABLES,
            batch_size=LOTE,
        )

    return {
        "filas_en_excel": len(filas),
        "sucursales_creadas": sucursales_creadas,
        "sucursales_actualizadas": sucursales_actualizadas,
        "clientes_nuevos": clientes_nuevos,
        "filas_omitidas": filas_sin_codigo_o_nombre,
    }
