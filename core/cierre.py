"""
Cambio #14: lógica del cierre de una Carga (salida de reparto).

El cierre es en dos pasos:
  1. El repartidor marca "Terminé mi ruta" desde el celular (terminar_ruta).
  2. Oficina revisa, recibe el dinero y las devoluciones, y cierra
     (cerrar_carga). Una carga cerrada queda bloqueada; solo oficina puede
     reabrirla (reabrir_carga).

Igual que core/entregas.py, la lógica vive aquí y no en las vistas para
poder probarla sin pasar por HTTP.
"""

from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from core.bodega import devoluciones_de_la_carga, resumen_recepcion
from core.entregas import valor_unitario
from core.models import CargaCuadre, Factura, Pago

CENTAVO = Decimal("0.01")
CERO = Decimal("0")

# La nota de crédito es un documento, no dinero: cuenta como cobrado pero no
# entra en lo que el repartidor entrega en oficina.
FORMA_NOTA_CREDITO = "NOTA_CREDITO"
FORMAS_DINERO = [
    (codigo, nombre) for codigo, nombre in Pago.FORMA_CHOICES if codigo != FORMA_NOTA_CREDITO
]
NOMBRE_FORMA = dict(Pago.FORMA_CHOICES)

# Etiqueta del número de documento que acompaña a cada forma de pago.
ETIQUETA_DOCUMENTO = {
    "EFECTIVO": "Recibo",
    "CHEQUE": "N.º",
    "TRANSFERENCIA": "Ref.",
    "TARJETA": "Ref.",
    "NOTA_CREDITO": "N.º",
}

# Resultado real de una factura dentro de la carga (4 etiquetas acordadas;
# "PARCIAL" solo puede aparecer si alguien editó cantidades a mano en el
# admin y dejó algo pendiente).
RESULTADOS = {
    "COMPLETA": ("Completa", "completa"),
    "CON_DEVOLUCION": ("Con devolución", "parcial"),
    "NO_ENTREGADA": ("No entregada", "noentregada"),
    "SIN_VISITAR": ("Sin visitar", "pendiente"),
    "PARCIAL": ("Parcial", "parcial"),
}


class CierreError(Exception):
    pass


def _redondear(valor):
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def calcular_resultado(lineas):
    """
    Devuelve (codigo, valor_entregado, valor_devuelto) a partir de las líneas
    de una factura.
    """
    cant_entregada = sum((l.cantidad_entregada for l in lineas), CERO)
    cant_devuelta = sum((l.cantidad_devuelta for l in lineas), CERO)
    cant_pendiente = sum((l.pendiente_entrega for l in lineas), CERO)
    valor_ent = _redondear(sum((l.cantidad_entregada * valor_unitario(l) for l in lineas), CERO))
    valor_dev = _redondear(sum((l.cantidad_devuelta * valor_unitario(l) for l in lineas), CERO))

    if cant_entregada <= 0 and cant_devuelta <= 0:
        codigo = "SIN_VISITAR"
    elif cant_pendiente > 0:
        codigo = "PARCIAL"
    elif cant_entregada <= 0:
        codigo = "NO_ENTREGADA"
    elif cant_devuelta > 0:
        codigo = "CON_DEVOLUCION"
    else:
        codigo = "COMPLETA"
    return codigo, valor_ent, valor_dev


def anotar_resultado(factura):
    """Le pone a la factura .resultado, .resultado_nombre y .resultado_css."""
    codigo, valor_ent, valor_dev = calcular_resultado(list(factura.lineas.all()))
    factura.resultado = codigo
    factura.resultado_nombre, factura.resultado_css = RESULTADOS[codigo]
    factura.valor_entregado = valor_ent
    factura.valor_devuelto = valor_dev
    return factura


def facturas_de_la_carga(carga):
    """
    Facturas activas de la carga, ya anotadas con su resultado. Si la carga
    está cerrada, incluye además las que se liberaron al cerrar (siguen
    figurando en el cierre como "Sin visitar").
    """
    base = Factura.objects.select_related("cliente", "modalidad_pago").prefetch_related("lineas")
    facturas = list(
        base.filter(carga=carga, estado_factura="ACTIVA").order_by("numero_factura")
    )
    for f in facturas:
        f.liberada = False
    if carga.pk:
        liberadas = list(
            base.filter(cargas_que_la_liberaron=carga).order_by("numero_factura")
        )
        en_carga = {f.id for f in facturas}
        for f in liberadas:
            if f.id not in en_carga:
                f.liberada = True
                facturas.append(f)
    for f in facturas:
        if f.liberada:
            # Una factura liberada puede haberse entregado después en otra
            # carga; en ESTA carga siempre quedó sin visitar.
            f.resultado = "SIN_VISITAR"
            f.resultado_nombre, f.resultado_css = RESULTADOS["SIN_VISITAR"]
            f.valor_entregado = CERO
            f.valor_devuelto = CERO
        else:
            anotar_resultado(f)
    return facturas


def pagos_de_la_carga(carga):
    return list(
        Pago.objects.filter(entrega__carga=carga)
        .select_related("cartera__factura", "entrega")
        .order_by("id")
    )


def numero_documento(pago):
    """Número de documento del pago según su forma (recibo o referencia)."""
    return pago.numero_recibo if pago.forma_pago == "EFECTIVO" else pago.referencia


def resumen_cierre(carga):
    """
    Arma todo lo que muestran la pantalla de cierre de oficina, el resumen
    del repartidor y el reporte imprimible. No guarda nada.
    """
    facturas = facturas_de_la_carga(carga)
    pagos = pagos_de_la_carga(carga)

    pagos_por_factura = {}
    for pago in pagos:
        pagos_por_factura.setdefault(pago.cartera.factura_id, []).append(pago)

    # Documento dejado al cliente: el de la última visita que entregó algo.
    dejo_por_factura = {}
    for entrega in carga.entregas.exclude(documento_cliente="").order_by("fecha_entrega", "id"):
        dejo_por_factura[entrega.factura_id] = entrega.get_documento_cliente_display()

    filas = []
    totales = {
        "facturado": CERO,
        "entregado": CERO,
        "devuelto": CERO,
        "cobrado": CERO,
        "paquetes": 0,
        "costo_flete": CERO,
    }
    conteo = OrderedDict(
        (codigo, {"cantidad": 0, "entregado": CERO, "devuelto": CERO}) for codigo in RESULTADOS
    )
    contado = {"entregado": CERO, "dinero": CERO, "nota_credito": CERO, "falto": CERO, "detalle": []}
    sin_visitar = []

    for f in facturas:
        pagos_f = pagos_por_factura.get(f.id, [])
        cobrado = sum((p.monto for p in pagos_f), CERO)
        es_contado = f.modalidad_pago.codigo == "CONTADO"
        filas.append(
            {
                "factura": f,
                "es_contado": es_contado,
                "dejo": dejo_por_factura.get(f.id, ""),
                "facturado": f.total,
                "entregado": f.valor_entregado,
                "devuelto": f.valor_devuelto,
                "cobrado": cobrado,
                # Cambio #18 (solo se muestran si la unidad los pide).
                "paquetes": f.paquetes,
                "costo_flete": f.costo_flete,
                "pagos": [
                    {
                        "forma": NOMBRE_FORMA.get(p.forma_pago, p.forma_pago),
                        "monto": p.monto,
                        "etiqueta": ETIQUETA_DOCUMENTO.get(p.forma_pago, "N.º"),
                        "numero": numero_documento(p),
                    }
                    for p in pagos_f
                ],
            }
        )
        totales["facturado"] += f.total
        totales["entregado"] += f.valor_entregado
        totales["devuelto"] += f.valor_devuelto
        totales["cobrado"] += cobrado
        totales["paquetes"] += f.paquetes or 0
        totales["costo_flete"] += f.costo_flete or CERO
        c = conteo[f.resultado]
        c["cantidad"] += 1
        c["entregado"] += f.valor_entregado
        c["devuelto"] += f.valor_devuelto
        if f.resultado == "SIN_VISITAR":
            sin_visitar.append(f)
        if es_contado:
            dinero = sum((p.monto for p in pagos_f if p.forma_pago != FORMA_NOTA_CREDITO), CERO)
            nota = sum((p.monto for p in pagos_f if p.forma_pago == FORMA_NOTA_CREDITO), CERO)
            falto = max(CERO, f.valor_entregado - dinero - nota)
            contado["entregado"] += f.valor_entregado
            contado["dinero"] += dinero
            contado["nota_credito"] += nota
            contado["falto"] += falto
            if falto > 0:
                contado["detalle"].append({"factura": f, "falto": falto})

    # Cuadre de dinero por forma de pago.
    sistema = OrderedDict((codigo, CERO) for codigo, _ in FORMAS_DINERO)
    nota_credito_total = CERO
    documentos = OrderedDict((codigo, []) for codigo, _ in Pago.FORMA_CHOICES)
    for pago in pagos:
        if pago.forma_pago == FORMA_NOTA_CREDITO:
            nota_credito_total += pago.monto
        elif pago.forma_pago in sistema:
            sistema[pago.forma_pago] += pago.monto
        documentos.setdefault(pago.forma_pago, []).append(
            {
                "numero": numero_documento(pago),
                "factura": pago.cartera.factura.numero_factura,
                "monto": pago.monto,
            }
        )

    guardado = {c.forma_pago: c for c in carga.cuadre.all()} if carga.pk else {}
    cuadre = []
    for codigo, nombre in FORMAS_DINERO:
        fila_guardada = guardado.get(codigo)
        if carga.esta_cerrada and fila_guardada:
            # Carga cerrada: se muestra lo que quedó registrado al cerrar.
            segun_sistema = fila_guardada.segun_sistema
            recibido = fila_guardada.recibido
        else:
            segun_sistema = sistema[codigo]
            recibido = fila_guardada.recibido if fila_guardada else None
        cuadre.append(
            {
                "codigo": codigo,
                "nombre": nombre,
                "segun_sistema": segun_sistema,
                "recibido": recibido,
                "diferencia": (recibido - segun_sistema) if recibido is not None else None,
                "cantidad": len(documentos.get(codigo, [])),
            }
        )
    total_sistema = sum((c["segun_sistema"] for c in cuadre), CERO)
    recibidos = [c["recibido"] for c in cuadre if c["recibido"] is not None]
    total_recibido = sum(recibidos, CERO) if len(recibidos) == len(cuadre) else None

    # Cambio #16: las devoluciones y su recepción las registra bodega.
    devoluciones = devoluciones_de_la_carga(carga)
    recepcion = resumen_recepcion(carga, devoluciones)
    docs_devolucion = {
        d.entrega.documento_devolucion for d in devoluciones if d.entrega.documento_devolucion
    }

    km_recorrido = None
    if carga.km_inicial is not None and carga.km_final is not None:
        km_recorrido = carga.km_final - carga.km_inicial

    visitadas = sum(1 for f in facturas if f.resultado != "SIN_VISITAR")

    return {
        "filas": filas,
        "totales": totales,
        "total_facturas": len(facturas),
        "visitadas": visitadas,
        "conteo": conteo,
        "sin_visitar": sin_visitar,
        "contado": contado,
        "cuadre": cuadre,
        "total_sistema": total_sistema,
        "total_recibido": total_recibido,
        "total_diferencia": (
            (total_recibido - total_sistema) if total_recibido is not None else None
        ),
        "nota_credito_total": nota_credito_total,
        "nota_credito_cantidad": len(documentos.get(FORMA_NOTA_CREDITO, [])),
        "documentos": [
            {"nombre": NOMBRE_FORMA[codigo], "codigo": codigo, "items": items}
            for codigo, items in documentos.items()
            if items
        ],
        "devoluciones": devoluciones,
        "recepcion": recepcion,
        # Cambio #17: facturas que no se cargaron y salieron de la carga al
        # tocar "Salir a ruta". No cuentan en los totales de la carga.
        "no_cargadas": (
            list(
                carga.facturas_no_cargadas.select_related("cliente", "modalidad_pago").order_by(
                    "numero_factura"
                )
            )
            if carga.pk
            else []
        ),
        "valor_devuelto": totales["devuelto"],
        "docs_devolucion": len(docs_devolucion),
        "km_recorrido": km_recorrido,
    }


@transaction.atomic
def terminar_ruta(carga, *, km_final, observaciones=""):
    """Paso 1: el repartidor marca que terminó su ruta."""
    if carga.estado == "CERRADA":
        raise CierreError("Esta carga ya está cerrada.")
    if carga.estado == "RUTA_TERMINADA":
        raise CierreError("La ruta de esta carga ya estaba marcada como terminada.")
    if km_final is None:
        raise CierreError("Escribe el kilometraje final.")
    if km_final < 0:
        raise CierreError("El kilometraje final no puede ser negativo.")
    if carga.km_inicial is not None and km_final < carga.km_inicial:
        raise CierreError(
            f"El kilometraje final no puede ser menor que el inicial ({carga.km_inicial:,.1f})."
        )
    carga.km_final = km_final
    carga.observaciones_repartidor = (observaciones or "").strip()
    carga.estado = "RUTA_TERMINADA"
    carga.ruta_terminada_en = timezone.now()
    carga.save(
        update_fields=["km_final", "observaciones_repartidor", "estado", "ruta_terminada_en"]
    )
    return carga


@transaction.atomic
def devolver_a_ruta(carga):
    """Oficina devuelve la carga al repartidor para que siga registrando."""
    if carga.estado != "RUTA_TERMINADA":
        raise CierreError("Solo se puede devolver a ruta una carga con la ruta terminada.")
    carga.estado = "EN_RUTA"
    carga.ruta_terminada_en = None
    # Cambio #16: si el repartidor vuelve a ruta puede traer más
    # devoluciones, así que bodega debe volver a confirmar la recepción
    # (las cantidades que ya había escrito se conservan).
    carga.devoluciones_recibidas_en = None
    carga.devoluciones_recibidas_por = None
    carga.save(
        update_fields=[
            "estado",
            "ruta_terminada_en",
            "devoluciones_recibidas_en",
            "devoluciones_recibidas_por",
        ]
    )
    return carga


@transaction.atomic
def cerrar_carga(
    carga,
    *,
    usuario,
    recibido_por_forma,
    observaciones="",
    km_final=None,
):
    """
    Paso 2: oficina cierra la carga.

    - recibido_por_forma: dict {codigo_forma: Decimal} con lo que oficina
      recibió por cada forma de pago en dinero. Todas son obligatorias.
    - Si en cualquier forma lo recibido no coincide con el sistema, la
      observación de oficina es obligatoria.
    - La recepción de las devoluciones la registra bodega en su propia
      pantalla (Cambio #16); que falte no bloquea el cierre.
    - Las facturas que siguen sin visitar salen de la carga (quedan "Sin
      carga asignada") y se anotan en carga.facturas_liberadas.
    """
    if carga.estado == "CERRADA":
        raise CierreError("Esta carga ya está cerrada.")

    observaciones = (observaciones or "").strip()
    if km_final is not None:
        if km_final < 0 or (carga.km_inicial is not None and km_final < carga.km_inicial):
            raise CierreError("El kilometraje final no puede ser menor que el inicial.")
        carga.km_final = km_final

    resumen = resumen_cierre(carga)
    hay_diferencia = False
    filas_cuadre = []
    for fila in resumen["cuadre"]:
        recibido = recibido_por_forma.get(fila["codigo"])
        if recibido is None:
            raise CierreError(f"Escribe cuánto se recibió en {fila['nombre']}.")
        recibido = _redondear(recibido)
        if recibido < 0:
            raise CierreError(f"Lo recibido en {fila['nombre']} no puede ser negativo.")
        if recibido != fila["segun_sistema"]:
            hay_diferencia = True
        filas_cuadre.append((fila["codigo"], fila["segun_sistema"], recibido))

    if hay_diferencia and not observaciones:
        raise CierreError(
            "El dinero recibido no cuadra con el sistema: escribe el motivo en las "
            "observaciones para poder cerrar."
        )

    for codigo, segun_sistema, recibido in filas_cuadre:
        CargaCuadre.objects.update_or_create(
            carga=carga,
            forma_pago=codigo,
            defaults={"segun_sistema": segun_sistema, "recibido": recibido},
        )

    liberadas = [f for f in resumen["sin_visitar"] if not f.liberada]
    if liberadas:
        carga.facturas_liberadas.add(*liberadas)
        # Cambio #17: al salir de la carga su mercadería vuelve a bodega, así
        # que en la próxima carga habrá que marcarla como cargada de nuevo.
        Factura.objects.filter(id__in=[f.id for f in liberadas]).update(
            carga=None, cargada_en=None, cargada_por=""
        )

    carga.estado = "CERRADA"
    carga.cerrada_en = timezone.now()
    carga.cerrada_por = usuario
    carga.observaciones_cierre = observaciones
    if carga.ruta_terminada_en is None:
        carga.ruta_terminada_en = carga.cerrada_en
    carga.save(
        update_fields=[
            "estado",
            "cerrada_en",
            "cerrada_por",
            "observaciones_cierre",
            "km_final",
            "ruta_terminada_en",
        ]
    )
    return carga


@transaction.atomic
def reabrir_carga(carga):
    """
    Oficina reabre una carga cerrada para corregir. Vuelve a "Ruta terminada"
    (el repartidor sigue bloqueado; oficina puede corregir o devolverla a
    ruta). Las facturas liberadas regresan a la carga solo si siguen sin
    carga asignada; las que ya se pusieron en otra carga se quedan allá.
    """
    if carga.estado != "CERRADA":
        raise CierreError("Solo se puede reabrir una carga cerrada.")
    liberadas = list(carga.facturas_liberadas.all())
    regresan = [f.id for f in liberadas if f.carga_id is None]
    if regresan:
        Factura.objects.filter(id__in=regresan, carga__isnull=True).update(carga=carga)
    carga.facturas_liberadas.clear()
    carga.estado = "RUTA_TERMINADA"
    carga.cerrada_en = None
    carga.cerrada_por = None
    carga.save(update_fields=["estado", "cerrada_en", "cerrada_por"])
    return carga
