# Cambio #27: crea el grupo "Ventas — Devoluciones" con el permiso "Puede
# solicitar devoluciones sin factura". También se lo da a "Administración" y
# "Supervisores" (Cambio #19), para que puedan registrar/consultar lo mismo.
# Si los grupos ya existían, solo se les agrega este permiso; no se les
# quita nada.

from django.db import migrations

PERMISO = "solicitar_devoluciones_sin_factura"
GRUPO_VENTAS_DEVOLUCIONES = "Ventas — Devoluciones"
GRUPOS = (GRUPO_VENTAS_DEVOLUCIONES, "Administración", "Supervisores")


def aplicar(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    tipo, _ = ContentType.objects.get_or_create(app_label="core", model="devolucionsinfactura")
    permiso, _ = Permission.objects.get_or_create(
        content_type=tipo,
        codename=PERMISO,
        defaults={"name": "Puede solicitar devoluciones sin factura"},
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
        ("core", "0027_cambio27_devoluciones_sin_factura"),
    ]

    operations = [
        migrations.RunPython(aplicar, deshacer),
    ]
