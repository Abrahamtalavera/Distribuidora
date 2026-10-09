# Cambio #17: datos para no bloquear rutas en curso al instalar el estado
# "Cargada" (desde ahora solo una factura cargada se puede entregar).
#
# 1. Una Carga que sigue "Planeada" pero ya tiene entregas registradas está,
#    en la práctica, en ruta (antes el paso a "En ruta" era manual y a veces
#    no se hacía): pasa a "En ruta".
# 2. Todas las facturas de Cargas "En ruta", "Ruta terminada" o "Cerradas",
#    y toda factura que ya tenga una entrega registrada, quedan marcadas como
#    cargadas, con una nota que explica de dónde salió la marca.
# No borra nada ni cambia montos.

from django.db import migrations
from django.db.models import Min
from django.utils import timezone

NOTA = "Marcada automáticamente al instalar el Cambio #17"


def aplicar(apps, schema_editor):
    Carga = apps.get_model("core", "Carga")
    Entrega = apps.get_model("core", "Entrega")
    Factura = apps.get_model("core", "Factura")
    ahora = timezone.now()

    planeadas_con_entregas = (
        Carga.objects.filter(estado="PLANEADA", entregas__isnull=False)
        .annotate(primera=Min("entregas__fecha_entrega"))
        .distinct()
    )
    for carga in planeadas_con_entregas:
        carga.estado = "EN_RUTA"
        carga.salio_a_ruta_en = carga.primera or ahora
        carga.save(update_fields=["estado", "salio_a_ruta_en"])

    Factura.objects.filter(
        cargada_en__isnull=True,
        carga__estado__in=["EN_RUTA", "RUTA_TERMINADA", "CERRADA"],
    ).update(cargada_en=ahora, cargada_por=NOTA)

    con_entregas = Entrega.objects.values_list("factura_id", flat=True).distinct()
    Factura.objects.filter(cargada_en__isnull=True, id__in=con_entregas).update(
        cargada_en=ahora, cargada_por=NOTA
    )


def deshacer(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_cambio17_factura_cargada"),
    ]

    operations = [
        migrations.RunPython(aplicar, deshacer),
    ]
