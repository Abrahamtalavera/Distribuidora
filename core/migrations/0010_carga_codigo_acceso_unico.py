# Cambio #12: ahora que todas las cargas tienen un codigo_acceso relleno
# (migración 0009), se puede exigir que sea único.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_backfill_codigo_acceso"),
    ]

    operations = [
        migrations.AlterField(
            model_name="carga",
            name="codigo_acceso",
            field=models.CharField(
                blank=True,
                editable=False,
                help_text=(
                    "Código corto y no consecutivo (distinto del número de carga) "
                    "que, junto con el PIN del repartidor, permite entrar desde el "
                    "celular a registrar las entregas de esta carga. Se genera "
                    "automáticamente al crear la carga."
                ),
                max_length=10,
                unique=True,
            ),
        ),
    ]
