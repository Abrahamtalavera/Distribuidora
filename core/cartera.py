"""
Cambio #28: revisión de devoluciones por cartera.

Un analista de cartera revisa cada devolución y "da por enterado"; desde ese
momento la devolución muestra el estatus "Recibido por cartera", con quién y
cuándo. Solo se puede dar por enterado DESPUÉS de que bodega de producto
terminado recibió la devolución (Cambio #16 / #27).

Se revisan dos clases de devolución, en una sola lista:

- Con factura: lo devuelto en una visita (una Entrega con líneas devueltas).
  Se considera recibida en bodega cuando la carga tiene la recepción
  confirmada y todas las líneas devueltas de esa visita están registradas.
- Sin factura (Cambio #27): una DevolucionSinFactura en estado CERRADA (bodega
  ya confirmó su recepción).

Los analistas entran con su usuario y contraseña (grupo "Cartera"); no
necesitan ser "staff".
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from core.entregas import valor_unitario
from core.models import DevolucionSinFactura, DevolucionSinFacturaDetalle, Entrega, EntregaDetalle

PERMISO_CARTERA = "core.revisar_devoluciones_cartera"
GRUPO_CARTERA = "Cartera"
CERO = Decimal("0")
CENTAVO = Decimal("0.01")

# Estados que ve cartera.
ESPERANDO_BODEGA = "ESPERANDO_BODEGA"
PENDIENTE = "PENDIENTE"
RECIBIDA = "RECIBIDA"
NOMBRES_ESTADO = {
    ESPERANDO_BODEGA: "Esperando bodega",
    PENDIENTE: "Pendiente de revisar",
    RECIBIDA: "Recibido por cartera",
}
FILTROS = ("pendientes", "recibidas", "todas")


class CarteraError(Exception):
    pass


def puede_revisar(usuario):
    return bool(
        usuario
        and usuario.is_authenticated
        and usuario.is_active
        and usuario.has_perm(PERMISO_CARTERA)
    )


# --- Cuándo bodega ya recibió ------------------------------------------------


def entrega_recibida_en_bodega(entrega, lineas_devueltas=None):
    """La devolución de esta visita ya fue recibida en bodega."""
    if lineas_devueltas is None:
        lineas_devueltas = [l for l in entrega.lineas.all() if l.cantidad_devuelta > 0]
    if not lineas_devueltas or entrega.carga_id is None:
        return False
    if entrega.carga.devoluciones_recibidas_en is None:
        return False
    return all(l.recepcion_registrada for l in lineas_devueltas)


def devolucion_sf_recibida_en_bodega(devolucion):
    return devolucion.estado == "CERRADA"


def estado_cartera_entrega(entrega, lineas_devueltas=None):
    if entrega.revisada_cartera_en:
        return RECIBIDA
    if entrega_recibida_en_bodega(entrega, lineas_devueltas):
        return PENDIENTE
    return ESPERANDO_BODEGA


def estado_cartera_sf(devolucion):
    if devolucion.revisada_cartera_en:
        return RECIBIDA
    if devolucion_sf_recibida_en_bodega(devolucion):
        return PENDIENTE
    return ESPERANDO_BODEGA


# --- Lista para la pantalla de cartera ---------------------------------------


def _linea(producto, motivo, d, cantidad):
    return {
        "codigo": producto.codigo,
        "nombre": producto.nombre,
        "motivo": motivo.nombre if motivo else "",
        "lote": d.lote,
        "fecha_vencimiento": d.fecha_vencimiento,
        "esta_vencido": d.esta_vencido,
        "cantidad": cantidad,
        "inventario": d.recibido_inventario,
        "merma": d.recibido_merma,
        "recepcion_registrada": d.recepcion_registrada,
    }


def _item_entrega(e):
    devueltas = [l for l in e.lineas.all() if l.cantidad_devuelta > 0]
    estado = estado_cartera_entrega(e, devueltas)
    valor = sum(
        (l.cantidad_devuelta * valor_unitario(l.factura_detalle) for l in devueltas), CERO
    ).quantize(CENTAVO)
    sucursal = e.factura.sucursal
    return {
        "tipo": "e",
        "id": e.id,
        "clave": f"e-{e.id}",
        "folio": e.factura.numero_factura,
        "tipo_nombre": "Con factura",
        "documento": e.documento_devolucion,
        "fecha": timezone.localtime(e.fecha_entrega).date(),
        "cliente": e.factura.cliente.nombre,
        "sucursal": sucursal.nombre if sucursal else "",
        "carga": e.carga,
        "unidades": sum((l.cantidad_devuelta for l in devueltas), CERO),
        "valor": valor,
        "estado": estado,
        "estado_nombre": NOMBRES_ESTADO[estado],
        "revisada_en": e.revisada_cartera_en,
        "revisada_por": e.revisada_cartera_por,
        "comentario": e.comentario_cartera,
        "recibido_bodega_en": e.carga.devoluciones_recibidas_en if e.carga_id else None,
        "observaciones_bodega": e.carga.observaciones_bodega if e.carga_id else "",
        "lineas": [
            _linea(
                l.factura_detalle.producto,
                l.motivo_devolucion or e.motivo_devolucion_general,
                l,
                l.cantidad_devuelta,
            )
            for l in devueltas
        ],
    }


def _item_sf(d):
    estado = estado_cartera_sf(d)
    detalles = list(d.detalles.all())
    return {
        "tipo": "sf",
        "id": d.id,
        "clave": f"sf-{d.id}",
        "folio": d.codigo_reporte,
        "tipo_nombre": "Sin factura",
        "documento": "",
        "fecha": d.fecha_ingreso,
        "cliente": d.sucursal.cliente.nombre,
        "sucursal": d.sucursal.nombre,
        "carga": d.carga,
        "unidades": sum((x.cantidad_recolectada for x in detalles), CERO),
        "valor": None,
        "estado": estado,
        "estado_nombre": NOMBRES_ESTADO[estado],
        "revisada_en": d.revisada_cartera_en,
        "revisada_por": d.revisada_cartera_por,
        "comentario": d.comentario_cartera,
        "recibido_bodega_en": d.recibido_en,
        "observaciones_bodega": d.observaciones_bodega,
        "motivo_general": d.motivo.nombre if d.motivo_id else "",
        "lineas": [_linea(x.producto, x.motivo, x, x.cantidad_recolectada) for x in detalles],
    }


def _entregas_con_devolucion():
    return (
        Entrega.objects.filter(estado="CONFIRMADA", lineas__cantidad_devuelta__gt=0)
        .distinct()
        .select_related(
            "factura__cliente",
            "factura__sucursal",
            "carga",
            "revisada_cartera_por",
            "motivo_devolucion_general",
        )
        .prefetch_related(
            Prefetch(
                "lineas",
                queryset=EntregaDetalle.objects.select_related(
                    "factura_detalle__producto", "motivo_devolucion"
                ).order_by("id"),
            )
        )
    )


def _devoluciones_sf():
    return (
        DevolucionSinFactura.objects.filter(estado__in=("RECOLECTADA", "CERRADA"))
        .select_related("sucursal__cliente", "carga", "motivo", "revisada_cartera_por")
        .prefetch_related(
            Prefetch(
                "detalles",
                queryset=DevolucionSinFacturaDetalle.objects.select_related(
                    "producto", "motivo"
                ).order_by("id"),
            )
        )
    )


def periodo_por_defecto():
    hoy = timezone.localdate()
    return hoy - timedelta(days=30), hoy


def devoluciones_para_cartera(filtro="pendientes", desde=None, hasta=None, limite=300):
    """
    Lista de devoluciones (con y sin factura) para la pantalla de cartera,
    de la más reciente a la más vieja.

    - "pendientes": ya recibidas en bodega y todavía sin dar por enterado
      (sin límite de fechas: no se debe perder ninguna).
    - "recibidas": las que cartera ya dio por enterado, en el período.
    - "todas": todo el período, incluso lo que aún espera a bodega.
    """
    if filtro not in FILTROS:
        filtro = "pendientes"
    if desde is None or hasta is None:
        desde, hasta = periodo_por_defecto()

    entregas = _entregas_con_devolucion()
    sin_factura = _devoluciones_sf()
    if filtro == "pendientes":
        entregas = entregas.filter(
            revisada_cartera_en__isnull=True, carga__devoluciones_recibidas_en__isnull=False
        )
        sin_factura = sin_factura.filter(revisada_cartera_en__isnull=True, estado="CERRADA")
    else:
        entregas = entregas.filter(
            fecha_entrega__date__gte=desde, fecha_entrega__date__lte=hasta
        )
        sin_factura = sin_factura.filter(fecha_ingreso__gte=desde, fecha_ingreso__lte=hasta)
        if filtro == "recibidas":
            entregas = entregas.filter(revisada_cartera_en__isnull=False)
            sin_factura = sin_factura.filter(revisada_cartera_en__isnull=False)

    items = [_item_entrega(e) for e in entregas.order_by("-fecha_entrega")[:limite]]
    items += [_item_sf(d) for d in sin_factura.order_by("-fecha_ingreso", "-id")[:limite]]
    if filtro == "pendientes":
        items = [i for i in items if i["estado"] == PENDIENTE]
    items.sort(key=lambda i: (i["fecha"], i["id"]), reverse=True)
    recortado = len(items) > limite
    return items[:limite], recortado


def conteo_pendientes():
    items, _ = devoluciones_para_cartera("pendientes")
    return len(items)


def item_cartera(tipo, objeto_id):
    """Una devolución de la lista, por su tipo ("e" o "sf") e id; None si no existe."""
    if tipo == "e":
        e = _entregas_con_devolucion().filter(pk=objeto_id).first()
        return _item_entrega(e) if e else None
    if tipo == "sf":
        d = _devoluciones_sf().filter(pk=objeto_id).first()
        return _item_sf(d) if d else None
    return None


# --- Dar por enterado ---------------------------------------------------------


@transaction.atomic
def dar_por_enterado(tipo, objeto_id, *, usuario, comentario=""):
    """
    Marca la devolución como "Recibido por cartera". Solo si bodega ya la
    recibió y nadie de cartera la había dado por enterada antes.
    """
    if tipo == "e":
        objeto = (
            Entrega.objects.select_for_update(of=("self",))
            .select_related("carga")
            .filter(pk=objeto_id, estado="CONFIRMADA")
            .first()
        )
        if objeto is None or not objeto.lineas.filter(cantidad_devuelta__gt=0).exists():
            raise CarteraError("Esa devolución no existe.")
        recibida = entrega_recibida_en_bodega(objeto)
    elif tipo == "sf":
        objeto = DevolucionSinFactura.objects.select_for_update().filter(pk=objeto_id).first()
        if objeto is None:
            raise CarteraError("Esa devolución no existe.")
        recibida = devolucion_sf_recibida_en_bodega(objeto)
    else:
        raise CarteraError("Esa devolución no existe.")

    if objeto.revisada_cartera_en:
        raise CarteraError("Esta devolución ya estaba recibida por cartera.")
    if not recibida:
        raise CarteraError(
            "Bodega todavía no ha recibido esta devolución. Cartera puede darla "
            "por enterada solo después de la recepción en bodega."
        )

    objeto.revisada_cartera_en = timezone.now()
    objeto.revisada_cartera_por = usuario
    objeto.comentario_cartera = (comentario or "").strip()
    objeto.save(update_fields=["revisada_cartera_en", "revisada_cartera_por", "comentario_cartera"])
    return objeto
