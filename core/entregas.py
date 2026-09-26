"""
Cambio #12: lógica de negocio para registrar una entrega (parcial o total)
de una factura, incluida la actualización de las cantidades entregadas /
devueltas, el estado de entrega de la factura, y — si corresponde — el
registro del cobro de una factura de contado en la misma operación.

Se aísla en este módulo (en vez de escribirla directamente en la vista) para
poder probarla con tests sin pasar por HTTP, siguiendo el mismo patrón que
core/importador.py.
"""

from decimal import Decimal

from django.db import transaction

from core.models import CarteraCobro, Entrega, EntregaDetalle, Pago


class RegistroEntregaError(Exception):
    pass


def _a_decimal(valor, por_defecto="0"):
    if valor is None or valor == "":
        return Decimal(por_defecto)
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor))
    except Exception:
        return Decimal(por_defecto)


@transaction.atomic
def registrar_entrega(
    *,
    factura,
    repartidor=None,
    cantidades_entregadas,
    motivo_general_id=None,
    motivo_por_linea=None,
    observaciones="",
    cobro=None,
):
    """
    Registra una entrega para `factura`.

    - cantidades_entregadas: dict {factura_detalle_id: cantidad_entregada_ingresada}
      Solo se esperan las líneas que todavía tenían algo pendiente; cualquier
      cantidad se recorta al rango [0, pendiente_de_esa_línea] — nunca se
      confía en el valor que llega del cliente para calcular lo devuelto ni
      para superar lo que en realidad estaba pendiente.
    - motivo_por_linea: dict opcional {factura_detalle_id: motivo_devolucion_id}
      para las líneas donde queda una cantidad_devuelta > 0.
    - cobro: dict opcional {"monto", "forma_pago", "referencia", "observaciones"}.
      Solo tiene efecto si factura.modalidad_pago.codigo == "CONTADO". La
      CarteraCobro de esa factura se crea en este momento si todavía no
      existe (Cambio #12: se decidió crearla "sobre la marcha" al primer
      cobro, en vez de crear una cartera para cada factura desde que se
      importa).

    Devuelve la Entrega creada.
    """
    motivo_por_linea = motivo_por_linea or {}

    lineas_por_id = {linea.id: linea for linea in factura.lineas.select_for_update()}
    if not lineas_por_id:
        raise RegistroEntregaError("La factura no tiene líneas.")

    entrega = Entrega.objects.create(
        factura=factura,
        tipo_entrega="PARCIAL",  # se corrige abajo una vez sabemos cómo quedó
        repartidor=repartidor,
        motivo_devolucion_general_id=motivo_general_id or None,
        observaciones=observaciones,
    )

    for factura_detalle_id, cantidad_cruda in cantidades_entregadas.items():
        linea = lineas_por_id.get(int(factura_detalle_id))
        if linea is None:
            # Línea que no pertenece a esta factura, o ya no existe: se
            # ignora en vez de fallar toda la entrega por un dato suelto.
            continue

        pendiente_antes = linea.pendiente_entrega
        if pendiente_antes <= 0:
            continue

        entregada = _a_decimal(cantidad_cruda)
        if entregada < 0:
            entregada = Decimal("0")
        if entregada > pendiente_antes:
            entregada = pendiente_antes
        devuelta = pendiente_antes - entregada

        motivo_id = motivo_por_linea.get(factura_detalle_id) or motivo_por_linea.get(
            str(factura_detalle_id)
        )

        EntregaDetalle.objects.create(
            entrega=entrega,
            factura_detalle=linea,
            cantidad_entregada=entregada,
            cantidad_devuelta=devuelta,
            motivo_devolucion_id=motivo_id if devuelta > 0 else None,
        )

        linea.cantidad_entregada = linea.cantidad_entregada + entregada
        linea.cantidad_devuelta = linea.cantidad_devuelta + devuelta
        linea.save(update_fields=["cantidad_entregada", "cantidad_devuelta"])

    # Estado de la factura, recalculado con las cantidades ya actualizadas.
    lineas_actuales = list(factura.lineas.all())
    total_facturado = sum((l.cantidad_facturada for l in lineas_actuales), Decimal("0"))
    total_pendiente = sum((l.pendiente_entrega for l in lineas_actuales), Decimal("0"))

    if total_pendiente <= 0:
        factura.estado_entrega = "COMPLETA"
        tipo_entrega = "TOTAL"
    elif total_pendiente < total_facturado:
        factura.estado_entrega = "PARCIAL"
        tipo_entrega = "PARCIAL"
    else:
        factura.estado_entrega = "PENDIENTE"
        tipo_entrega = "PARCIAL"

    factura.save(update_fields=["estado_entrega"])

    entrega.tipo_entrega = tipo_entrega
    entrega.save(update_fields=["tipo_entrega"])

    if cobro and factura.modalidad_pago.codigo == "CONTADO":
        monto = _a_decimal(cobro.get("monto"))
        if monto > 0:
            cartera, _creada = CarteraCobro.objects.get_or_create(
                factura=factura,
                defaults={
                    "modalidad_pago": factura.modalidad_pago,
                    "monto_total": factura.total,
                    "saldo_pendiente": factura.total,
                    "estado": "VIGENTE",
                },
            )
            Pago.objects.create(
                cartera=cartera,
                monto=monto,
                forma_pago=cobro.get("forma_pago") or "EFECTIVO",
                referencia=cobro.get("referencia", ""),
                cobrador=repartidor,
                observaciones=cobro.get("observaciones", ""),
            )
            cartera.saldo_pendiente = max(Decimal("0"), cartera.saldo_pendiente - monto)
            cartera.estado = "PAGADA" if cartera.saldo_pendiente <= 0 else cartera.estado
            cartera.save(update_fields=["saldo_pendiente", "estado"])

    return entrega
