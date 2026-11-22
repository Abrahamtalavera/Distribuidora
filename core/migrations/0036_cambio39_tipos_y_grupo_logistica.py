# Cambio #39: tipos de documento de flota y grupo "Logística" con el
# permiso "Puede gestionar la flota (logística)". También se le da a
# "Administración" y "Supervisores". No quita nada de lo que ya exista.

from django.db import migrations

PERMISO = "gestionar_flota"
GRUPOS = ("Logística", "Administración", "Supervisores")
TIPOS = [
    ("Tarjeta de circulación", "VEHICULO", 1),
    ("Permiso de circulación", "VEHICULO", 2),
    ("Seguro", "VEHICULO", 3),
    ("Revisión técnica", "VEHICULO", 4),
    ("Permiso de carga", "VEHICULO", 5),
    ("Licencia de conducir", "CONDUCTOR", 1),
]


def aplicar(apps, schema_editor):
    Tipo = apps.get_model("core", "TipoDocumentoFlota")
    for nombre, aplica, orden in TIPOS:
        Tipo.objects.get_or_create(nombre=nombre, aplica_a=aplica, defaults={"orden": orden})

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    tipo, _ = ContentType.objects.get_or_create(app_label="core", model="documentoflota")
    permiso, _ = Permission.objects.get_or_create(
        content_type=tipo, codename=PERMISO,
        defaults={"name": "Puede gestionar la flota (logística)"},
    )
    for nombre in GRUPOS:
        grupo, _ = Group.objects.get_or_create(name=nombre)
        grupo.permissions.add(permiso)


def deshacer(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    permiso = Permission.objects.filter(content_type__app_label="core", codename=PERMISO).first()
    if permiso:
        for grupo in Group.objects.filter(name__in=GRUPOS):
            grupo.permissions.remove(permiso)


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0035_cambio39_flota"),
    ]

    operations = [migrations.RunPython(aplicar, deshacer)]
