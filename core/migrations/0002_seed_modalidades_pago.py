from django.db import migrations


def crear_modalidades(apps, schema_editor):
    ModalidadPago = apps.get_model("core", "ModalidadPago")
    datos = [
        ("CONTADO", "Contado", "Pago completo al momento de la entrega"),
        ("CREDITO", "Crédito a plazos", "Pago en cuotas después de la entrega"),
        ("CONSIGNACION", "Consignación", "Se cobra según lo que el cliente venda o use"),
    ]
    for codigo, nombre, descripcion in datos:
        ModalidadPago.objects.get_or_create(
            codigo=codigo, defaults={"nombre": nombre, "descripcion": descripcion}
        )


def eliminar_modalidades(apps, schema_editor):
    ModalidadPago = apps.get_model("core", "ModalidadPago")
    ModalidadPago.objects.filter(
        codigo__in=["CONTADO", "CREDITO", "CONSIGNACION"]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(crear_modalidades, eliminar_modalidades),
    ]
