"""
Cambio #17: estado "Cargada" de la factura y salida a ruta.

Antes de salir, el repartidor marca una por una las facturas cuya
mercadería sube al camión. Solo una factura cargada se puede entregar. Al
tocar "Salir a ruta", la Carga pasa a "En ruta" y las facturas que no se
marcaron como cargadas salen de la Carga (quedan "Sin carga asignada", para
otra ruta) y se anotan en la Carga para que el cierre las siga mostrando.
"""

from django.db import transaction
from django.utils import timezone

from core.models import Factura

# Campos que se limpian cuando una factura sale de su Carga: su mercadería
# vuelve a bodega, así que en la próxima Carga hay que cargarla de nuevo.
SIN_CARGAR = {"cargada_en": None, "cargada_por": ""}


class SalidaError(Exception):
    pass


def descripcion_quien(request, carga):
    """Texto de quién hizo la acción: el usuario de oficina o el repartidor."""
    if request.user.is_authenticated and request.user.is_staff:
        return f"Oficina: {request.user.get_username()}"
    if carga.conductor:
        return f"Repartidor: {carga.conductor.nombre}"
    return "Repartidor"


def marcar_cargada(factura, *, carga, quien):
    if factura.carga_id != carga.id:
        raise SalidaError("La factura no pertenece a esta carga.")
    if carga.estado not in ("PLANEADA", "EN_RUTA"):
        raise SalidaError("La carga ya no está en ruta: no se pueden marcar facturas.")
    if factura.esta_cargada:
        return factura
    factura.cargada_en = timezone.now()
    factura.cargada_por = (quien or "")[:150]
    factura.save(update_fields=["cargada_en", "cargada_por"])
    return factura


def quitar_cargada(factura, *, carga):
    if factura.carga_id != carga.id:
        raise SalidaError("La factura no pertenece a esta carga.")
    if not carga.en_fase_de_carga:
        raise SalidaError(
            "La carga ya salió a ruta: ya no se puede quitar la marca de cargada."
        )
    if factura.entregas.exists():
        raise SalidaError("Esta factura ya tiene una entrega registrada.")
    factura.cargada_en = None
    factura.cargada_por = ""
    factura.save(update_fields=["cargada_en", "cargada_por"])
    return factura


def facturas_por_cargar(carga):
    return list(
        carga.facturas.filter(estado_factura="ACTIVA", cargada_en__isnull=True)
        .select_related("cliente", "modalidad_pago")
        .order_by("cliente__nombre", "numero_factura")
    )


@transaction.atomic
def salir_a_ruta(carga):
    """
    Pone la Carga "En ruta". Las facturas no cargadas salen de la Carga y
    quedan anotadas en carga.facturas_no_cargadas. Devuelve la lista de
    facturas liberadas.
    """
    if not carga.en_fase_de_carga:
        raise SalidaError("Esta carga ya salió a ruta.")
    activas = carga.facturas.filter(estado_factura="ACTIVA")
    if not activas.filter(cargada_en__isnull=False).exists():
        raise SalidaError("Marca como cargada al menos una factura antes de salir a ruta.")

    liberadas = facturas_por_cargar(carga)
    if liberadas:
        carga.facturas_no_cargadas.add(*liberadas)
        Factura.objects.filter(id__in=[f.id for f in liberadas]).update(carga=None, **SIN_CARGAR)

    carga.estado = "EN_RUTA"
    carga.salio_a_ruta_en = timezone.now()
    carga.save(update_fields=["estado", "salio_a_ruta_en"])
    return liberadas
