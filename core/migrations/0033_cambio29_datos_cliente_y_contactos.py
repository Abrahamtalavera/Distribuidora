# Cambio #29: llena el cliente de las devoluciones sin factura que ya existían
# (el de su sucursal) y carga la lista inicial de contactos en sucursales.
# Si un contacto ya existe, no se duplica. Las devoluciones anteriores que
# tenían escrito un nombre igual a uno de la lista quedan ligadas a él.

from django.db import migrations

CONTACTOS = [
    "Santos Ocampo",
    "Marklin Arias",
    "Maria Brenes",
    "Jazmina Hernández",
    "Karen Vargas",
    "Giselle Carrión",
    "Xiomara Polanco",
    "Omar Amador",
    "Andriel Báez",
    "Franklin Manzanarez",
    "Karla López",
    "Diana Chavez",
    "Gladys Moralez",
    "Wilmara Briceño",
    "Maria Delgado",
    "Flor Diaz",
    "Melissa Velásquez",
    "Meyling Olivas",
    "Maria Serrano",
    "Samirlly Aguirre",
    "Scarleth Carter",
    "Franklin Flores",
    "Melissa Chavarria",
    "Lidia Gaitán",
    "Pedro Garcia",
    "Isolda Reyes",
    "Xiomara Rocha",
    "Ervin Alvarez",
    "Indiana Martinez",
    "Byron Espinoza",
]


def aplicar(apps, schema_editor):
    Devolucion = apps.get_model("core", "DevolucionSinFactura")
    Contacto = apps.get_model("core", "ContactoSucursal")

    for d in Devolucion.objects.filter(cliente__isnull=True).select_related("sucursal"):
        d.cliente_id = d.sucursal.cliente_id
        d.save(update_fields=["cliente"])

    por_nombre = {}
    for nombre in CONTACTOS:
        contacto, _ = Contacto.objects.get_or_create(nombre=nombre)
        por_nombre[nombre.casefold()] = contacto

    for d in Devolucion.objects.filter(contacto__isnull=True).exclude(contacto_sucursal=""):
        contacto = por_nombre.get(" ".join(d.contacto_sucursal.split()).casefold())
        if contacto:
            d.contacto = contacto
            d.save(update_fields=["contacto"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_cambio29_cliente_y_contactos"),
    ]

    operations = [
        migrations.RunPython(aplicar, migrations.RunPython.noop),
    ]
