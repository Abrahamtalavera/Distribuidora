# Cambio #16 (paso 2 de 3): datos.
#
# 1. Crea el grupo "Bodega PT" con el permiso "Puede recibir devoluciones en
#    bodega". Un usuario que se agregue a ese grupo desde el admin puede
#    entrar a la pantalla de recepción de devoluciones y a nada más.
# 2. Convierte las devoluciones que oficina ya había marcado con la casilla
#    "Recibido" del Cambio #14: quedan como recibidas completas (a
#    inventario, porque la casilla no decía el destino). Si TODAS las
#    devoluciones de una carga estaban marcadas, la carga queda con la
#    recepción confirmada y una nota que explica de dónde salió.
# No borra nada ni cambia montos.

from django.db import migrations
from django.utils import timezone

GRUPO = "Bodega PT"
PERMISO = "recibir_devoluciones"
NOTA = (
    "Recepción marcada por oficina con la casilla anterior al Cambio #16; "
    "no se registró el destino del producto."
)


def aplicar(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Carga = apps.get_model("core", "Carga")
    EntregaDetalle = apps.get_model("core", "EntregaDetalle")

    # Los permisos se crean normalmente al terminar de migrar; aquí se crea
    # este por adelantado para poder dárselo al grupo en la misma migración.
    tipo, _ = ContentType.objects.get_or_create(app_label="core", model="carga")
    permiso, _ = Permission.objects.get_or_create(
        content_type=tipo,
        codename=PERMISO,
        defaults={"name": "Puede recibir devoluciones en bodega"},
    )
    grupo, _ = Group.objects.get_or_create(name=GRUPO)
    grupo.permissions.add(permiso)

    marcadas = EntregaDetalle.objects.filter(recibido_bodega=True, cantidad_devuelta__gt=0)
    cargas_tocadas = set()
    for detalle in marcadas.select_related("entrega"):
        detalle.recibido_inventario = detalle.cantidad_devuelta
        detalle.recibido_merma = 0
        detalle.save(update_fields=["recibido_inventario", "recibido_merma"])
        if detalle.entrega.carga_id:
            cargas_tocadas.add(detalle.entrega.carga_id)

    for carga in Carga.objects.filter(id__in=cargas_tocadas):
        pendientes = EntregaDetalle.objects.filter(
            entrega__carga_id=carga.id,
            cantidad_devuelta__gt=0,
            recibido_inventario__isnull=True,
            recibido_merma__isnull=True,
        )
        if pendientes.exists():
            continue  # bodega terminará de recibirla en la pantalla nueva
        carga.devoluciones_recibidas_en = carga.cerrada_en or timezone.now()
        carga.devoluciones_recibidas_por_id = carga.cerrada_por_id
        carga.observaciones_bodega = NOTA
        carga.save(
            update_fields=[
                "devoluciones_recibidas_en",
                "devoluciones_recibidas_por",
                "observaciones_bodega",
            ]
        )


def deshacer(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GRUPO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_cambio16_recepcion_bodega"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(aplicar, deshacer),
    ]
