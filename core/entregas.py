"""
Cambios #12 y #13: lógica de negocio para registrar una entrega (parcial o total)
de una factura, incluida la actualización de las cantidades entregadas /
devueltas, el estado de entrega de la factura, y — si corresponde — el
registro del cobro de una factura de contado en la misma operación.

Se aísla en este módulo (en vez de escribirla directamente en la vista) para
poder probarla con tests sin pasar por HTTP, siguiendo el mismo patrón que
core/importador.py.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from core.models import CarteraCobro, Entrega, EntregaDetalle, Pago


MAX_PAGOS = 3
CENTAVO = Decimal("0.01")


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
    pagos=None,
    documento_devolucion="",
    documento_cliente="",
    carga=None,
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
    - pagos: lista opcional (Cambio #13: hasta 3) de dicts
      {"monto", "forma_pago", "referencia"}. Aplica a facturas de contado y
      de crédito (en crédito es un abono). La CarteraCobro se crea "sobre la
      marcha" al primer pago (Cambio #12); si ya existe, se recalcula su
      deuda y saldo en cada entrega, aunque no traiga pagos, para que las
      devoluciones bajen lo que el cliente debe (Cambio #13).

    - documento_devolucion (Cambio #15): número de documento de devolución
      que escribe el repartidor. Solo se guarda si en esta visita de verdad
      quedó algo devuelto.
    - documento_cliente (Cambio #15): "ORIGINAL" o "COPIA", lo que se le dejó
      al cliente. Solo se guarda si en esta visita se entregó algo; si llega
      vacío o con un valor desconocido se toma "ORIGINAL" (es el valor que
      viene marcado en pantalla).

    - carga (Cambio #14): la carga desde la que se registra la visita. La
      Entrega y sus pagos quedan enlazados a ella para el cierre de la carga.

    Devuelve la Entrega creada.
    """
    motivo_por_linea = motivo_por_linea or {}

    lineas_por_id = {linea.id: linea for linea in factura.lineas.select_for_update()}
    if not lineas_por_id:
        raise RegistroEntregaError("La factura no tiene líneas.")

    entrega = Entrega.objects.create(
        factura=factura,
        carga=carga,
        tipo_entrega="PARCIAL",  # se corrige abajo una vez sabemos cómo quedó
        repartidor=repartidor,
        motivo_devolucion_general_id=motivo_general_id or None,
        observaciones=observaciones,
    )

    total_entregado_visita = Decimal("0")
    total_devuelto_visita = Decimal("0")

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
        total_entregado_visita += entregada
        total_devuelto_visita += devuelta

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
    if total_devuelto_visita > 0:
        entrega.documento_devolucion = (documento_devolucion or "").strip()[:30]
    if total_entregado_visita > 0:
        validos = {codigo for codigo, _ in Entrega.DOCUMENTO_CLIENTE_CHOICES}
        entrega.documento_cliente = (
            documento_cliente if documento_cliente in validos else "ORIGINAL"
        )
    entrega.save(update_fields=["tipo_entrega", "documento_devolucion", "documento_cliente"])

    pagos_validos = _normalizar_pagos(pagos)
    cartera_existente = CarteraCobro.objects.filter(factura=factura).first()
    if pagos_validos or cartera_existente:
        cartera = cartera_existente or CarteraCobro.objects.create(
            factura=factura,
            modalidad_pago=factura.modalidad_pago,
            monto_total=factura.total,
            saldo_pendiente=factura.total,
            estado="VIGENTE",
        )
        for pago in pagos_validos:
            Pago.objects.create(
                cartera=cartera,
                entrega=entrega,
                monto=pago["monto"],
                forma_pago=pago["forma_pago"],
                referencia=pago["referencia"],
                numero_recibo=pago["numero_recibo"],
                cobrador=repartidor,
                observaciones=pago.get("observaciones", ""),
            )
        recalcular_cartera(cartera)

    return entrega


def valor_unitario(linea):
    """Valor por unidad de una línea de factura, con IVA incluido."""
    if not linea.cantidad_facturada:
        return Decimal("0")
    return (linea.subtotal + linea.iva) / linea.cantidad_facturada


def valor_devuelto(factura):
    """Valor (con IVA) de todo lo devuelto hasta ahora en la factura."""
    total = sum(
        (l.cantidad_devuelta * valor_unitario(l) for l in factura.lineas.all()),
        Decimal("0"),
    )
    return total.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def valor_entregado(factura):
    """Valor (con IVA) de todo lo entregado hasta ahora en la factura."""
    total = sum(
        (l.cantidad_entregada * valor_unitario(l) for l in factura.lineas.all()),
        Decimal("0"),
    )
    return total.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def recalcular_cartera(cartera):
    """
    Cambio #13: la deuda de la factura es el total facturado MENOS el valor de
    lo devuelto (el cliente no debe lo que no recibió). El saldo es esa deuda
    menos todos los pagos registrados. Se recalcula desde cero cada vez, para
    que no se acumulen errores de redondeo entre entregas parciales.
    """
    factura = cartera.factura
    deuda = max(Decimal("0"), factura.total - valor_devuelto(factura))
    pagado = sum((p.monto for p in cartera.pagos.all()), Decimal("0"))
    cartera.monto_total = deuda
    cartera.saldo_pendiente = max(Decimal("0"), deuda - pagado)
    if cartera.estado != "ANULADA":
        if cartera.saldo_pendiente <= 0:
            cartera.estado = "PAGADA"
        elif cartera.estado == "PAGADA":
            cartera.estado = "VIGENTE"
    cartera.save(update_fields=["monto_total", "saldo_pendiente", "estado"])


def _normalizar_pagos(pagos):
    """
    Recibe la lista de pagos capturados en pantalla (hasta MAX_PAGOS) y
    devuelve solo los válidos: monto > 0 y forma de pago conocida. Cualquier
    fila de más se ignora.
    """
    formas_validas = {codigo for codigo, _ in Pago.FORMA_CHOICES}
    resultado = []
    for pago in (pagos or [])[:MAX_PAGOS]:
        monto = _a_decimal(pago.get("monto")).quantize(CENTAVO, rounding=ROUND_HALF_UP)
        if monto <= 0:
            continue
        forma = pago.get("forma_pago") or "EFECTIVO"
        if forma not in formas_validas:
            forma = "EFECTIVO"
        # Cambio #15: el efectivo lleva número de recibo; las demás formas
        # llevan referencia (en nota de crédito, el número de la nota).
        referencia = (pago.get("referencia") or "").strip()[:50]
        numero_recibo = (pago.get("numero_recibo") or "").strip()[:30]
        if forma == "EFECTIVO":
            referencia = ""
        else:
            numero_recibo = ""
        resultado.append(
            {"monto": monto, "forma_pago": forma, "referencia": referencia,
             "numero_recibo": numero_recibo,
             "observaciones": pago.get("observaciones", "")}
        )
    return resultado
