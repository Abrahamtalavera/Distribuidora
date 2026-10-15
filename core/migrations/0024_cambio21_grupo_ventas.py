# Cambio #21: crea el grupo "Ventas" con el permiso "Puede usar la consulta
# de ventas". También se lo da a "Administración" y "Supervisores" (Cambio
# #19), para que puedan ver lo mismo que ve un vendedor. Si los grupos ya
# existían, solo se les agrega este permiso; no se les quita nada.

from django.db import migrations

PERMISO = "consultar_ventas"
GRUPO_VENTAS = "Ventas"
GRUPOS = (GRUPO_VENTAS, "Administración", "Supervisores")


def aplicar(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    tipo, _ = ContentType.objects.get_or_create(app_label="core", model="factura")
    permiso, _ = Permission.objects.get_or_create(
        content_type=tipo,
        codename=PERMISO,
        defaults={"name": "Puede usar la consulta de ventas"},
    )
    for nombre in GRUPOS:
        grupo, _ = Group.objects.get_or_create(name=nombre)
        grupo.permissions.add(permiso)


def deshacer(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    permiso = Permission.objects.filter(
        content_type__app_label="core", codename=PERMISO
    ).first()
    if permiso:
        for grupo in Group.objects.filter(name__in=GRUPOS):
            grupo.permissions.remove(permiso)


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0023_cambio21_permiso_consulta_ventas"),
    ]

    operations = [
        migrations.RunPython(aplicar, deshacer),
    ]
