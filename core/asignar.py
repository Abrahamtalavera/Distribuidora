"""
Cambio #23: agregar facturas seleccionadas a una Carga que ya existe.

Desde la lista de Facturas del admin se marcan facturas y se elige la acción
"Agregar seleccionadas a una Carga existente". Solo se puede agregar a una
carga Planeada o En ruta. Cada factura se revisa:

- No se agregan las anuladas, las ya entregadas completas, las que ya están
  en esa misma carga, ni las que van cargadas en el camión de otra carga
  que ya salió a ruta.
- Una factura que estaba en otra carga Planeada se mueve (sale de la otra).
- En la carga destino la factura entra "por cargar": el repartidor la marca
  como cargada al subirla al camión (Cambio #17). Si la unidad pide
  paquetes y costo (Cambio #18), hay que escribirlos aquí.
"""

from django.db import transaction

from core.models import Carga, Factura

ESTADOS_DESTINO = ("PLANEADA", "EN_RUTA")


class AsignarError(Exception):
    pass


def cargas_destino():
    return (
        Carga.objects.filter(estado__in=ESTADOS_DESTINO)
        .select_related("ruta", "conductor", "vehiculo")
        .order_by("-fecha_planeada", "-id")
    )


def revisar(facturas, destino=None):
    """
    Anota en cada factura .puede (bool), .motivo (por qué no) y .aviso (lo
    que va a pasar, p. ej. que sale de otra carga). Devuelve (aptas, no_aptas).
    """
    aptas, no_aptas = [], []
    for f in facturas:
        f.puede, f.motivo, f.aviso = True, "", ""
        actual = f.carga
        if f.estado_factura == "ANULADA":
            f.puede, f.motivo = False, "Está anulada"
        elif f.estado_entrega == "COMPLETA":
            f.puede, f.motivo = False, "Ya fue entregada completa"
        elif destino is not None and f.carga_id == destino.id:
            f.puede, f.motivo = False, "Ya está en esta carga"
        elif actual is not None and actual.estado == "EN_RUTA" and f.esta_cargada:
            f.puede, f.motivo = False, f"Va cargada en {actual.codigo_reporte}, que está en ruta"
        elif actual is not None and actual.estado in ("PLANEADA", "EN_RUTA"):
            f.aviso = f"Sale de {actual.codigo_reporte}"
        (aptas if f.puede else no_aptas).append(f)
    return aptas, no_aptas


@transaction.atomic
def agregar_a_carga(destino, facturas, paquetes_costo=None):
    """
    Pasa las facturas aptas a `destino`. `paquetes_costo` = {factura_id:
    (paquetes, costo)}, obligatorio si la unidad de la carga lo pide.
    Devuelve (agregadas, no_aptas).
    """
    destino = Carga.objects.select_for_update().select_related("vehiculo").get(pk=destino.pk)
    if destino.estado not in ESTADOS_DESTINO:
        raise AsignarError(
            f"{destino.codigo_reporte} está {destino.get_estado_display().lower()}: "
            "solo se agregan facturas a cargas Planeadas o En ruta."
        )
    aptas, no_aptas = revisar(facturas, destino)
    if not aptas:
        raise AsignarError("Ninguna de las facturas seleccionadas se puede agregar a esta carga.")
    paquetes_costo = paquetes_costo or {}
    if destino.pide_paquetes_costo:
        faltan = [f.numero_factura for f in aptas if f.id not in paquetes_costo]
        if faltan:
            raise AsignarError(
                f"La unidad {destino.vehiculo.placa} pide paquetes y costo: falta en "
                + ", ".join(faltan) + "."
            )
    for f in aptas:
        paquetes, costo = paquetes_costo.get(f.id, (None, None)) if destino.pide_paquetes_costo else (None, None)
        Factura.objects.filter(pk=f.pk).update(
            carga=destino, cargada_en=None, cargada_por="", paquetes=paquetes, costo_flete=costo
        )
    return aptas, no_aptas
