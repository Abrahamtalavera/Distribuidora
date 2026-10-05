# Cambio #14: rellena los enlaces nuevos para los datos que ya existían
# antes del cierre de Carga:
#   - Entrega.carga  = la carga que tiene hoy la factura de esa entrega.
#   - Pago.entrega   = la visita de entrega de esa factura durante la cual se
#                      registró el pago (la más reciente que no sea posterior
#                      al pago; si no hay ninguna así, la primera).
# No borra ni cambia montos; solo agrega esos dos enlaces.

from datetime import timedelta

from django.db import migrations


def rellenar(apps, schema_editor):
    Entrega = apps.get_model("core", "Entrega")
    Pago = apps.get_model("core", "Pago")

    for entrega in Entrega.objects.filter(carga__isnull=True).select_related("factura"):
        if entrega.factura.carga_id:
            entrega.carga_id = entrega.factura.carga_id
            entrega.save(update_fields=["carga"])

    margen = timedelta(minutes=5)
    for pago in Pago.objects.filter(entrega__isnull=True).select_related("cartera"):
        entregas = list(
            Entrega.objects.filter(factura_id=pago.cartera.factura_id).order_by("fecha_entrega", "id")
        )
        if not entregas:
            continue
        candidatas = [e for e in entregas if e.fecha_entrega <= pago.fecha_pago + margen]
        elegida = candidatas[-1] if candidatas else entregas[0]
        pago.entrega_id = elegida.id
        pago.save(update_fields=["entrega"])


def deshacer(apps, schema_editor):
    # Los enlaces son opcionales; al revertir no hace falta vaciarlos.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_cambio14_cierre_carga"),
    ]

    operations = [
        migrations.RunPython(rellenar, deshacer),
    ]
