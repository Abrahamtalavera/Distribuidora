# Cambio #18: si en el catálogo de Vehículos ya existe la unidad M000003, se
# deja marcada como "Pide paquetes y costo". Si no existe (o tiene otra
# placa), no se toca nada: el usuario marca la casilla en el admin.

from django.db import migrations


def aplicar(apps, schema_editor):
    Vehiculo = apps.get_model("core", "Vehiculo")
    Vehiculo.objects.filter(placa__iexact="M000003").update(pide_paquetes_costo=True)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_cambio18_paquetes_costo"),
    ]

    operations = [
        migrations.RunPython(aplicar, migrations.RunPython.noop),
    ]
