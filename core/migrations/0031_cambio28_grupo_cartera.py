# Cambio #28: crea el grupo "Cartera" con el permiso "Puede dar por enterada
# una devolución (cartera)". También se lo da a "Administración" y
# "Supervisores" (Cambio #19). Si los grupos ya existían, solo se les agrega
# este permiso; no se les quita nada.

from django.db import migrations

PERMISO = "revisar_devoluciones_cartera"
GRUPO_CARTERA = "Cartera"
GRUPOS = (GRUPO_CARTERA, "Administración", "Supervisores")


def aplicar(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    tipo, _ = ContentType.objects.get_or_create(app_label="core", model="devolucionsinfactura")
    permiso, _ = Permission.objects.get_or_create(
        content_type=tipo,
        codename=PERMISO,
        defaults={"name": "Puede dar por enterada una devolución (cartera)"},
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
        ("core", "0030_cambio28_lote_vencimiento_y_cartera"),
    ]

    operations = [
        migrations.RunPython(aplicar, deshacer),
    ]
