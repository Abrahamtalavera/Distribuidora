"""
Cambio #16: recepción de devoluciones en bodega de producto terminado.

Quien recibe lo que regresa en el camión es bodega, no oficina. Por cada
producto devuelto, bodega escribe cuánto vuelve a inventario y cuánto es
merma; lo recibido es la suma, y la diferencia contra lo que reportó el
repartidor queda a la vista en el cierre de la carga y en su reporte.

La app NO lleva existencias: esto es solo el registro de lo recibido (el
inventario sigue en SAP).
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import Carga, EntregaDetalle

CERO = Decimal("0")
PERMISO_BODEGA = "core.recibir_devoluciones"
GRUPO_BODEGA = "Bodega PT"

# Una carga llega a bodega cuando el repartidor marcó "Terminé mi ruta" o
# cuando oficina la cerró.
ESTADOS_VISIBLES_BODEGA = ("RUTA_TERMINADA", "CERRADA")

ESTADOS_RECEPCION = {
    "SIN_DEVOLUCIONES": ("Sin devoluciones", "pendiente"),
    "PENDIENTE": ("Pendiente", "pendiente"),
    "COMPLETA": ("Recibida completa", "completa"),
    "CON_DIFERENCIA": ("Con diferencia", "noentregada"),
}


class RecepcionError(Exception):
    pass


def puede_recibir(usuario):
    return bool(
        usuario
        and usuario.is_authenticated
        and usuario.is_active
        and usuario.has_perm(PERMISO_BODEGA)
    )


def devoluciones_de_la_carga(carga):
    """Líneas devueltas en las visitas de esta carga, listas para mostrarse."""
    devoluciones = list(
        EntregaDetalle.objects.filter(entrega__carga=carga, cantidad_devuelta__gt=0)
        .select_related(
            "entrega__factura",
            "entrega__motivo_devolucion_general__area_responsable",
            "factura_detalle__producto",
            "motivo_devolucion__area_responsable",
        )
        .order_by("entrega__factura__numero_factura", "id")
    )
    for d in devoluciones:
        motivo = d.motivo_devolucion or d.entrega.motivo_devolucion_general
        d.motivo_nombre = motivo.nombre if motivo else ""
        d.area_nombre = (
            motivo.area_responsable.nombre if motivo and motivo.area_responsable else ""
        )
    return devoluciones


def resumen_recepcion(carga, devoluciones=None):
    """
    Estado de la recepción en bodega de una carga y sus totales.

    Estados: SIN_DEVOLUCIONES, PENDIENTE (bodega no ha confirmado, o falta
    algún producto por recibir), COMPLETA, CON_DIFERENCIA.
    """
    if devoluciones is None:
        devoluciones = devoluciones_de_la_carga(carga)

    totales = {"reportado": CERO, "inventario": CERO, "merma": CERO, "recibido": CERO}
    faltan = 0
    hay_diferencia = False
    for d in devoluciones:
        totales["reportado"] += d.cantidad_devuelta
        if not d.recepcion_registrada:
            faltan += 1
            continue
        totales["inventario"] += d.recibido_inventario or CERO
        totales["merma"] += d.recibido_merma or CERO
        totales["recibido"] += d.recibido_total
        if d.diferencia_recepcion != 0:
            hay_diferencia = True

    if not devoluciones:
        estado = "SIN_DEVOLUCIONES"
    elif carga.devoluciones_recibidas_en is None or faltan:
        estado = "PENDIENTE"
    elif hay_diferencia:
        estado = "CON_DIFERENCIA"
    else:
        estado = "COMPLETA"

    confirmada = estado in ("COMPLETA", "CON_DIFERENCIA")
    nombre, css = ESTADOS_RECEPCION[estado]
    return {
        "estado": estado,
        "nombre": nombre,
        "css": css,
        "confirmada": confirmada,
        "faltan": faltan,
        "hay_diferencia": hay_diferencia,
        "totales": totales,
        "diferencia_total": (totales["recibido"] - totales["reportado"]) if confirmada else None,
        "productos": len(devoluciones),
        # Bodega puede corregir mientras la carga no esté cerrada; si se cerró
        # sin recepción, todavía puede registrarla una vez.
        "editable": not (carga.esta_cerrada and confirmada),
    }


def cargas_para_bodega(dias_recibidas=7):
    """
    Devuelve (pendientes, recibidas): las cargas con devoluciones que bodega
    todavía no recibe y las recibidas en los últimos `dias_recibidas` días.
    """
    con_devolucion = (
        Carga.objects.filter(
            estado__in=ESTADOS_VISIBLES_BODEGA,
            entregas__lineas__cantidad_devuelta__gt=0,
        )
        .select_related("ruta", "conductor", "vehiculo", "devoluciones_recibidas_por")
        .distinct()
    )
    limite = timezone.now() - timedelta(days=dias_recibidas)
    pendientes, recibidas = [], []
    for carga in con_devolucion.order_by("fecha_planeada", "id"):
        carga.recepcion = resumen_recepcion(carga)
        if carga.recepcion["estado"] == "PENDIENTE":
            pendientes.append(carga)
        elif carga.recepcion["confirmada"] and carga.devoluciones_recibidas_en >= limite:
            recibidas.append(carga)
    recibidas.sort(key=lambda c: c.devoluciones_recibidas_en, reverse=True)
    return pendientes, recibidas


@transaction.atomic
def registrar_recepcion(carga, *, usuario, cantidades, observaciones=""):
    """
    Bodega confirma lo recibido.

    - cantidades: dict {id de EntregaDetalle: (a_inventario, merma)}; cada
      valor es Decimal o None (casilla vacía). Hay que dar al menos una de
      las dos cantidades en cada producto (0 si no llegó nada); la otra,
      si se deja vacía, cuenta como 0.
    - Si en algún producto lo recibido no coincide con lo reportado, la
      observación es obligatoria.
    """
    if carga.estado not in ESTADOS_VISIBLES_BODEGA:
        raise RecepcionError(
            "Esta carga todavía está en ruta: el repartidor no ha terminado."
        )
    devoluciones = devoluciones_de_la_carga(carga)
    if not devoluciones:
        raise RecepcionError("Esta carga no tiene devoluciones que recibir.")
    if not resumen_recepcion(carga, devoluciones)["editable"]:
        raise RecepcionError(
            "La carga ya está cerrada y su recepción quedó confirmada. "
            "Para corregirla, oficina debe reabrir la carga."
        )

    observaciones = (observaciones or "").strip()
    nuevos = []
    hay_diferencia = False
    for d in devoluciones:
        inventario, merma = cantidades.get(d.id, (None, None))
        if inventario is None and merma is None:
            raise RecepcionError(
                "Falta escribir las cantidades de todos los productos "
                "(si de alguno no llegó nada, escribe 0)."
            )
        inventario = inventario or CERO
        merma = merma or CERO
        if inventario < 0 or merma < 0:
            raise RecepcionError("Las cantidades recibidas no pueden ser negativas.")
        if inventario + merma != d.cantidad_devuelta:
            hay_diferencia = True
        nuevos.append((d, inventario, merma))

    if hay_diferencia and not observaciones:
        raise RecepcionError(
            "Hay diferencia entre lo reportado y lo recibido: escribe el motivo "
            "en las observaciones para poder confirmar."
        )

    for d, inventario, merma in nuevos:
        d.recibido_inventario = inventario
        d.recibido_merma = merma
        d.save(update_fields=["recibido_inventario", "recibido_merma"])

    carga.devoluciones_recibidas_en = timezone.now()
    carga.devoluciones_recibidas_por = usuario
    carga.observaciones_bodega = observaciones
    carga.save(
        update_fields=[
            "devoluciones_recibidas_en",
            "devoluciones_recibidas_por",
            "observaciones_bodega",
        ]
    )
    return carga
