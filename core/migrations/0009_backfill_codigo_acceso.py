# Cambio #12: rellena codigo_acceso para todas las cargas que ya existían
# antes de este cambio, generando un código único para cada una (mismo
# alfabeto y longitud que Carga.generar_codigo_acceso_unico, reimplementado
# aquí para no depender del estado futuro del modelo).

import random

from django.db import migrations

ALFABETO_CODIGO_ACCESO = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def generar_codigos(apps, schema_editor):
    Carga = apps.get_model("core", "Carga")
    existentes = set(
        Carga.objects.exclude(codigo_acceso="").values_list("codigo_acceso", flat=True)
    )
    for carga in Carga.objects.filter(codigo_acceso=""):
        while True:
            candidato = "".join(random.choice(ALFABETO_CODIGO_ACCESO) for _ in range(5))
            if candidato not in existentes:
                existentes.add(candidato)
                break
        carga.codigo_acceso = candidato
        carga.save(update_fields=["codigo_acceso"])


def revertir(apps, schema_editor):
    # No hay nada que revertir: al bajar la migración 0008 el campo
    # desaparece de todas formas.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_entrega_repartidor_acceso_campos"),
    ]

    operations = [
        migrations.RunPython(generar_codigos, revertir),
    ]
