# Cambio #12: campos nuevos para el acceso del repartidor por código de
# carga + PIN, y para distinguir cantidad devuelta de cantidad entregada.
# El campo codigo_acceso se agrega aquí SIN unique=True todavía: la tabla
# Carga ya tiene registros y no se puede exigir unicidad antes de rellenar
# un valor real para cada uno (eso lo hace la migración de datos que sigue,
# 0009). La restricción unique=True se agrega recién en 0010.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_seed_motivos_devolucion"),
    ]

    operations = [
        migrations.AddField(
            model_name="vendedor",
            name="pin_acceso",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Clave corta que usa el repartidor, junto con el código de la "
                    "carga, para entrar a registrar entregas desde su celular. "
                    "Solo aplica a repartidores."
                ),
                max_length=10,
                verbose_name="PIN de acceso (repartidor)",
            ),
        ),
        migrations.AddField(
            model_name="facturadetalle",
            name="cantidad_devuelta",
            field=models.DecimalField(
                decimal_places=3,
                default=0,
                help_text=(
                    "Cantidad de esta línea que se marcó como devuelta/rechazada en "
                    "alguna entrega (Cambio #12). Se resta del pendiente igual que "
                    "lo entregado, para no dejarla como 'pendiente' para siempre."
                ),
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="carga",
            name="codigo_acceso",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                help_text=(
                    "Código corto y no consecutivo (distinto del número de carga) "
                    "que, junto con el PIN del repartidor, permite entrar desde el "
                    "celular a registrar las entregas de esta carga. Se genera "
                    "automáticamente al crear la carga."
                ),
                max_length=10,
            ),
            preserve_default=False,
        ),
    ]
