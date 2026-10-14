# Cambio #19: crea los grupos "Administración" y "Supervisores" con el
# permiso "Puede ver el tablero de operación". Si los grupos ya existían
# (con otros permisos), solo se les agrega este; no se les quita nada.
# Un usuario que se agregue a cualquiera de los dos grupos desde el admin
# puede entrar a /tablero/.

from django.db import migrations

GRUPOS = ("Administración", "Supervisores")
PERMISO = "ver_tablero"


def aplicar(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    # Igual que en el Cambio #16: el permiso se crea por adelantado para
    # poder dárselo a los grupos en esta misma migración.
    tipo, _ = ContentType.objects.get_or_create(app_label="core", model="carga")
    permiso, _ = Permission.objects.get_or_create(
        content_type=tipo,
        codename=PERMISO,
        defaults={"name": "Puede ver el tablero de operación"},
    )
    for nombre in GRUPOS:
        grupo, _ = Group.objects.get_or_create(name=nombre)
        grupo.permissions.add(permiso)


def deshacer(apps, schema_editor):
    # Solo se quita el permiso; los grupos pueden tener otros usos.
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
        ("core", "0021_cambio19_permiso_tablero"),
    ]

    operations = [
        migrations.RunPython(aplicar, deshacer),
    ]
