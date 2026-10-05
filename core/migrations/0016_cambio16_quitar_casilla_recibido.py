# Cambio #16 (paso 3 de 3): se quita la casilla "Recibido en bodega" del
# Cambio #14. Sus datos ya se convirtieron en el paso 2 a las cantidades
# nuevas (recibido a inventario / merma).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_cambio16_grupo_bodega_y_conversion"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="entregadetalle",
            name="recibido_bodega",
        ),
    ]
