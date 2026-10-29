# Cambio #25: catálogo de Sucursales (direcciones de entrega) de cada
# Cliente, y el enlace de cada Factura con la sucursal a la que corresponde.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_cambio22_horas_llegadas_y_gps"),
    ]

    operations = [
        migrations.CreateModel(
            name="Sucursal",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "nombre",
                    models.CharField(
                        max_length=150, verbose_name="Nombre de la sucursal"
                    ),
                ),
                ("direccion", models.CharField(blank=True, max_length=255)),
                ("departamento", models.CharField(blank=True, max_length=100)),
                ("municipio", models.CharField(blank=True, max_length=100)),
                ("territorio", models.CharField(blank=True, max_length=100)),
                ("canal", models.CharField(blank=True, max_length=100)),
                (
                    "cod_grupo",
                    models.PositiveIntegerField(
                        blank=True, null=True, verbose_name="Código de grupo"
                    ),
                ),
                (
                    "grupo",
                    models.CharField(blank=True, max_length=100, verbose_name="Grupo"),
                ),
                ("activo", models.BooleanField(default=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "cliente",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sucursales",
                        to="core.cliente",
                    ),
                ),
            ],
            options={
                "verbose_name": "Sucursal",
                "verbose_name_plural": "Sucursales",
                "ordering": ["cliente__codigo", "nombre"],
                "unique_together": {("cliente", "nombre")},
            },
        ),
        migrations.AddField(
            model_name="factura",
            name="sucursal",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="facturas",
                to="core.sucursal",
            ),
        ),
    ]
