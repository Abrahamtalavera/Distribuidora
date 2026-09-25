# Cambio #9: carga inicial de las áreas responsables y los motivos de
# devolución, tomados del archivo "Motivos de devoluciones.xlsx" que ya
# maneja el negocio. Así el usuario no tiene que volver a capturar los 18
# motivos a mano desde el admin.
from django.db import migrations


AREAS_Y_MOTIVOS = [
    ("Cruce de producto", "Bodega PT"),
    ("Faltante en caja", "Producción"),
    ("No solicitado cliente", "Ventas"),
    ("Por precio", "Ventas"),
    ("Calidad de presentación", "Producción"),
    ("Producto no cargado", "Logística"),
    ("Cliente sin dinero", "Ventas"),
    ("Producto vencido", "Bodega PT"),
    ("No entregado por BPT", "Bodega PT"),
    ("Cancelado por cliente", "Ventas"),
    ("Cliente en inventario", "Ventas"),
    ("No entregada en tiempo", "Logística"),
    ("Codigo de barra", "Ventas"),
    ("Cancelado por exceso de inventario", "Ventas"),
    ("Orden vencida", "Logística"),
    ("Local cerrado", "Ventas"),
    ("Duplicada", "Ventas"),
    ("Dirección incorrecta", "Ventas"),
]


def cargar_motivos(apps, schema_editor):
    AreaResponsable = apps.get_model("core", "AreaResponsable")
    MotivoDevolucion = apps.get_model("core", "MotivoDevolucion")

    areas_cache = {}
    for nombre_motivo, nombre_area in AREAS_Y_MOTIVOS:
        area = areas_cache.get(nombre_area)
        if area is None:
            area, _ = AreaResponsable.objects.get_or_create(nombre=nombre_area)
            areas_cache[nombre_area] = area
        MotivoDevolucion.objects.get_or_create(
            nombre=nombre_motivo, defaults={"area_responsable": area}
        )


def revertir(apps, schema_editor):
    # No se borran los catálogos al revertir para no perder datos que ya se
    # hayan usado en entregas reales; si hace falta limpiar, se hace a mano.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_motivos_devolucion"),
    ]

    operations = [
        migrations.RunPython(cargar_motivos, revertir),
    ]
