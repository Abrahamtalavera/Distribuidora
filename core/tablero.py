"""
Cambio #19: tablero de operación para Administración y Supervisores.

Dos partes:
  - "Cómo va el día": indicadores de un día (por defecto hoy) — facturas por
    entregar, cargas por estado, facturado, entregado, cobrado y cuadre.
  - "Alertas": lo que requiere acción hoy, sin importar el día elegido —
    facturas atrasadas sin carga, cargas sin cerrar o atrasadas, diferencias
    de cuadre, devoluciones sin recibir en bodega y cartera vencida.

Igual que cierre.py y bodega.py, la lógica vive aquí y no en la vista para
poder probarla sin pasar por HTTP. El tablero solo lee: no cambia nada.
"""

from collections import OrderedDict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, F, Sum
from django.utils import timezone

from core.bodega import cargas_para_bodega
from core.entregas import valor_unitario
from core.models import Carga, CargaCuadre, CarteraCobro, EntregaDetalle, Factura, Pago

CERO = Decimal("0")
CENTAVO = Decimal("0.01")
PERMISO_TABLERO = "core.ver_tablero"

# Máximo de filas que se listan en cada alerta (el total siempre se muestra).
LIMITE_FILAS = 15
# Días hacia atrás en los que se buscan diferencias de cuadre.
DIAS_DIFERENCIAS = 7

FORMA_NOTA_CREDITO = "NOTA_CREDITO"
NOMBRE_FORMA = dict(Pago.FORMA_CHOICES)
ESTADOS_POR_ENTREGAR = ("PENDIENTE", "PARCIAL")


def puede_ver_tablero(usuario):
    return bool(
        usuario
        and usuario.is_authenticated
        and usuario.is_active
        and usuario.has_perm(PERMISO_TABLERO)
    )


def _redondear(valor):
    return (valor or CERO).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _conteo_y_monto(qs):
    datos = qs.aggregate(n=Count("id"), monto=Sum("total"))
    return {"n": datos["n"] or 0, "monto": _redondear(datos["monto"])}


# ---------------------------------------------------------------------------
# Cómo va el día
# ---------------------------------------------------------------------------


def facturas_por_entregar():
    """
    Facturas activas que todavía tienen producto por entregar (no dependen
    del día: es lo que hay pendiente ahora), separadas por dónde están.
    """
    base = Factura.objects.filter(
        estado_factura="ACTIVA", estado_entrega__in=ESTADOS_POR_ENTREGAR
    )
    return {
        "total": _conteo_y_monto(base),
        "sin_carga": _conteo_y_monto(base.filter(carga__isnull=True)),
        "en_carga_planeada": _conteo_y_monto(base.filter(carga__estado="PLANEADA")),
        "en_ruta": _conteo_y_monto(base.filter(carga__estado="EN_RUTA")),
        "ruta_terminada": _conteo_y_monto(
            base.filter(carga__estado__in=("RUTA_TERMINADA", "CERRADA"))
        ),
    }


def cargas_del_dia(dia):
    conteos = dict(
        Carga.objects.filter(fecha_planeada=dia)
        .values_list("estado")
        .annotate(n=Count("id"))
    )
    estados = OrderedDict(
        (codigo, {"nombre": nombre, "n": conteos.get(codigo, 0)})
        for codigo, nombre in Carga.ESTADO_CHOICES
    )
    return {"total": sum(e["n"] for e in estados.values()), "estados": estados}


def facturado_del_dia(dia):
    return _conteo_y_monto(
        Factura.objects.filter(estado_factura="ACTIVA", fecha_emision=dia)
    )


def entregado_del_dia(dia):
    """
    Valor (con IVA) de lo entregado y lo devuelto en las visitas de ese día,
    calculado igual que en el cierre de la carga.
    """
    lineas = EntregaDetalle.objects.filter(
        entrega__fecha_entrega__date=dia, entrega__estado="CONFIRMADA"
    ).select_related("factura_detalle", "entrega")
    valor_ent = CERO
    valor_dev = CERO
    facturas = set()
    facturas_con_devolucion = set()
    for linea in lineas:
        unitario = valor_unitario(linea.factura_detalle)
        valor_ent += linea.cantidad_entregada * unitario
        valor_dev += linea.cantidad_devuelta * unitario
        facturas.add(linea.entrega.factura_id)
        if linea.cantidad_devuelta > 0:
            facturas_con_devolucion.add(linea.entrega.factura_id)
    valor_ent = _redondear(valor_ent)
    valor_dev = _redondear(valor_dev)
    total = valor_ent + valor_dev
    return {
        "valor_entregado": valor_ent,
        "valor_devuelto": valor_dev,
        "facturas_visitadas": len(facturas),
        "facturas_con_devolucion": len(facturas_con_devolucion),
        # % del valor visitado que se quedó con el cliente.
        "pct_efectividad": (
            (valor_ent * 100 / total).quantize(Decimal("0.1")) if total else None
        ),
    }


def cobrado_del_dia(dia):
    filas = (
        Pago.objects.filter(fecha_pago__date=dia)
        .values("forma_pago")
        .annotate(monto=Sum("monto"), n=Count("id"))
    )
    por_forma = {f["forma_pago"]: f for f in filas}
    formas = []
    total_dinero = CERO
    for codigo, nombre in Pago.FORMA_CHOICES:
        fila = por_forma.get(codigo)
        if not fila:
            continue
        monto = _redondear(fila["monto"])
        formas.append({"codigo": codigo, "nombre": nombre, "monto": monto, "n": fila["n"]})
        if codigo != FORMA_NOTA_CREDITO:
            total_dinero += monto
    return {"formas": formas, "total_dinero": total_dinero}


def cuadre_del_dia(dia):
    """Cuadre de dinero de las cargas que oficina cerró ese día."""
    cuadres = CargaCuadre.objects.filter(carga__cerrada_en__date=dia)
    datos = cuadres.aggregate(sistema=Sum("segun_sistema"), recibido=Sum("recibido"))
    sistema = _redondear(datos["sistema"])
    recibido = _redondear(datos["recibido"])
    cargas_con_dif = (
        cuadres.exclude(recibido=F("segun_sistema")).values("carga").distinct().count()
    )
    return {
        "cargas_cerradas": Carga.objects.filter(cerrada_en__date=dia).count(),
        "segun_sistema": sistema,
        "recibido": recibido,
        "diferencia": recibido - sistema,
        "cargas_con_diferencia": cargas_con_dif,
    }


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------


def _alerta(clave, titulo, ayuda, filas, total=None, monto=None, nivel="warn"):
    return {
        "clave": clave,
        "titulo": titulo,
        "ayuda": ayuda,
        "filas": filas[:LIMITE_FILAS],
        "total": len(filas) if total is None else total,
        "monto": monto,
        "nivel": nivel,
    }


def alerta_facturas_atrasadas(hoy):
    qs = (
        Factura.objects.filter(
            estado_factura="ACTIVA",
            estado_entrega__in=ESTADOS_POR_ENTREGAR,
            carga__isnull=True,
            fecha_sugerida_entrega__lt=hoy,
        )
        .select_related("cliente")
        .order_by("fecha_sugerida_entrega", "numero_factura")
    )
    datos = qs.aggregate(n=Count("id"), monto=Sum("total"))
    filas = list(qs[:LIMITE_FILAS])
    for f in filas:
        f.dias_atraso = (hoy - f.fecha_sugerida_entrega).days
    return _alerta(
        "facturas_atrasadas",
        "Facturas atrasadas sin carga",
        "Ya pasó su fecha sugerida de entrega y no están en ninguna carga.",
        filas,
        total=datos["n"] or 0,
        monto=_redondear(datos["monto"]),
        nivel="error",
    )


def _cargas(qs):
    return list(qs.select_related("ruta", "conductor", "vehiculo").order_by("fecha_planeada", "id"))


def alerta_cargas_sin_cierre():
    filas = _cargas(Carga.objects.filter(estado="RUTA_TERMINADA"))
    ahora = timezone.now()
    for c in filas:
        c.horas_espera = (
            int((ahora - c.ruta_terminada_en).total_seconds() // 3600)
            if c.ruta_terminada_en
            else None
        )
    return _alerta(
        "cargas_sin_cierre",
        "Rutas terminadas sin cierre de oficina",
        "El repartidor ya marcó 'Terminé mi ruta'; falta recibir el dinero y cerrar.",
        filas,
    )


def alerta_cargas_en_ruta_atrasadas(hoy):
    return _alerta(
        "cargas_en_ruta_atrasadas",
        "Cargas en ruta de días anteriores",
        "Salieron a ruta otro día y el repartidor todavía no marca 'Terminé mi ruta'.",
        _cargas(Carga.objects.filter(estado="EN_RUTA", fecha_planeada__lt=hoy)),
    )


def alerta_cargas_planeadas_atrasadas(hoy):
    return _alerta(
        "cargas_planeadas_atrasadas",
        "Cargas planeadas que no salieron",
        "Su fecha ya pasó y siguen en 'Planeada' (no salieron a ruta).",
        _cargas(Carga.objects.filter(estado="PLANEADA", fecha_planeada__lt=hoy)),
    )


def alerta_diferencias_cuadre(hoy):
    desde = hoy - timedelta(days=DIAS_DIFERENCIAS)
    ids = (
        CargaCuadre.objects.filter(carga__cerrada_en__date__gte=desde)
        .exclude(recibido=F("segun_sistema"))
        .values_list("carga_id", flat=True)
        .distinct()
    )
    filas = list(
        Carga.objects.filter(id__in=list(ids))
        .select_related("ruta", "conductor")
        .annotate(
            sistema=Sum("cuadre__segun_sistema"), recibido=Sum("cuadre__recibido")
        )
        .order_by("-cerrada_en")
    )
    for c in filas:
        c.diferencia = _redondear(c.recibido) - _redondear(c.sistema)
    return _alerta(
        "diferencias_cuadre",
        f"Diferencias de dinero (últimos {DIAS_DIFERENCIAS} días)",
        "Cargas cerradas donde lo recibido en oficina no cuadra con el sistema.",
        filas,
        monto=sum((c.diferencia for c in filas), CERO),
        nivel="error",
    )


def alerta_devoluciones_bodega():
    pendientes, _ = cargas_para_bodega()
    return _alerta(
        "devoluciones_bodega",
        "Devoluciones sin recibir en bodega",
        "Cargas con producto devuelto que bodega todavía no confirma.",
        pendientes,
    )


def alerta_cartera_vencida(hoy):
    qs = (
        CarteraCobro.objects.filter(
            saldo_pendiente__gt=0,
            factura__fecha_vencimiento__lt=hoy,
        )
        .exclude(estado__in=("PAGADA", "ANULADA"))
        .select_related("factura__cliente")
        .order_by("factura__fecha_vencimiento")
    )
    datos = qs.aggregate(n=Count("id"), monto=Sum("saldo_pendiente"))
    filas = list(qs[:LIMITE_FILAS])
    for c in filas:
        c.dias_vencida = (hoy - c.factura.fecha_vencimiento).days
    return _alerta(
        "cartera_vencida",
        "Cartera vencida",
        "Facturas de crédito con saldo y fecha de vencimiento pasada.",
        filas,
        total=datos["n"] or 0,
        monto=_redondear(datos["monto"]),
    )


def alertas(hoy):
    return [
        alerta_facturas_atrasadas(hoy),
        alerta_cargas_sin_cierre(),
        alerta_diferencias_cuadre(hoy),
        alerta_devoluciones_bodega(),
        alerta_cargas_en_ruta_atrasadas(hoy),
        alerta_cargas_planeadas_atrasadas(hoy),
        alerta_cartera_vencida(hoy),
    ]


def resumen_tablero(dia=None):
    hoy = timezone.localdate()
    dia = dia or hoy
    lista_alertas = alertas(hoy)
    return {
        "dia": dia,
        "hoy": hoy,
        "es_hoy": dia == hoy,
        "por_entregar": facturas_por_entregar(),
        "cargas": cargas_del_dia(dia),
        "facturado": facturado_del_dia(dia),
        "entregado": entregado_del_dia(dia),
        "cobrado": cobrado_del_dia(dia),
        "cuadre": cuadre_del_dia(dia),
        "alertas": lista_alertas,
        "alertas_activas": sum(1 for a in lista_alertas if a["total"]),
    }
