import os

from django.contrib.auth.hashers import make_password
from django.db import migrations


def crear_superusuario(apps, schema_editor):
    User = apps.get_model("auth", "User")
    if User.objects.filter(is_superuser=True).exists():
        return
    username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
    password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "PBHlIb5gGDxLT2")
    User.objects.create(
        username=username,
        email=os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com"),
        password=make_password(password),
        is_staff=True,
        is_superuser=True,
        is_active=True,
    )


def eliminar_superusuario(apps, schema_editor):
    User = apps.get_model("auth", "User")
    User.objects.filter(username=os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_seed_modalidades_pago"),
    ]

    operations = [
        migrations.RunPython(crear_superusuario, eliminar_superusuario),
    ]
