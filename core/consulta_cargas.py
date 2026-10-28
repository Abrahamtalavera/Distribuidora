"""
Cambio #24: consulta de Cargas para Administración y Supervisores.

Filtra Cargas por fecha planeada, id, ruta, conductor y estado, con un
resumen de facturas (completas, con devolución, no entregadas, sin
visitar) en el encabezado y el detalle de facturas de cada Carga para la
fila que se despliega al hacer clic. Vive bajo /tablero/ porque es para el
mismo público que el resto del tablero (permiso core.ver_tablero): no hace
falta un permiso nuevo.

Solo lee: no cambia nada. Reutiliza core.cierre.calcular_resultado -la
misma lógica que usan el cierre de la carga y la consulta de ventas- para
que los números coincidan en toda la aplicación.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from core.cierre import calcular_resultado
from core.models import Factura

CERO = Decimal("0")
CENTAVO = Decimal("0.01")
DIAS_POR_DEFECTO = 7
MAX_DIAS_LISTADO = 366
MAX_CARGAS_LISTADO = 300

# calcular_resultado() devuelve 5 códigos posibles; esta pantalla los reduce
# a las 4 categorías que pidió el requerimiento (Completa / Con devolución /
# No entregada / Sin visitar). Una factura "Parcial" (algo entregado, algo
# todavía pendiente en una sola visita) cuenta como "Sin visitar": a esa
# Carga le sigue faltando trabajo por terminar.
_BUCKET = {
    "SIN_VISITAR": "sin_visitar",
    "PARCIAL": "sin_visitar",
    "COMPLETA": "completa",
    "CON_DEVOLUCION": "devolucion",
    "NO_ENTREGADA": "no_entregada",
}
_NOMBRE_BUCKET = {
    "completa": "Completa",
    "devolucion": "Con devolución",
    "no_entregada": "No entregada",
    "sin_visitar": "Sin visitar",
}
# Mismas clases de badge que ya usa el resto de la aplicación
# (core/bodega/base.html trae badge-pendiente/badge-completa/badge-noentregada;
# badge-parcial la define cada pantalla de consulta, igual que aquí) para que
# los colores coincidan en toda la app.
_CSS_BUCKET = {
    "completa": "completa",
    "devolucion": "parcial",
    "no_entregada": "noentregada",
    "sin_visitar": "pendiente",
}


def _r(valor):
    return (valor or CERO).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def periodo_por_defecto():
    hasta = timezone.localdate()
    return hasta - timedelta(days=DIAS_POR_DEFECTO - 1), hasta


def listado_cargas(desde, hasta, carga_id=None, ruta_id=None, conductor_id=None, estado=""):
    """
    Devuelve (cargas, resumen, recortado).

    `cargas`: Cargas que cumplen los filtros (más recientes primero), cada
    una con .total_facturas, .completa, .devolucion, .no_entregada,
    .sin_visitar, .monto (total facturado de la Carga), .avance (% o None
    si no tiene facturas activas) y .detalle_facturas (para la fila que se
    despliega).
    `resumen`: los mismos totales sumados entre todas las cargas, para el
    encabezado.
    `recortado`: True si había más de MAX_CARGAS_LISTADO cargas con estos
    filtros y se muestran solo las primeras.
    """
    from core.models import Carga

    qs = Carga.objects.filter(fecha_planeada__gte=desde, fecha_planeada__lte=hasta)
    if carga_id:
        qs = qs.filter(pk=carga_id)
    if ruta_id:
        qs = qs.filter(ruta_id=ruta_id)
    if conductor_id:
        qs = qs.filter(conductor_id=conductor_id)
    if estado:
        qs = qs.filter(estado=estado)
    qs = qs.select_related("ruta", "conductor").order_by("-fecha_planeada", "-id")

    total_encontradas = qs.count()
    recortado = total_encontradas > MAX_CARGAS_LISTADO
    cargas = list(qs[:MAX_CARGAS_LISTADO])

    facturas = (
        Factura.objects.filter(carga_id__in=[c.id for c in cargas], estado_factura="ACTIVA")
        .select_related("cliente")
        .prefetch_related("lineas")
        .order_by("numero_factura")
    )
    por_carga = defaultdict(list)
    for f in facturas:
        por_carga[f.carga_id].append(f)

    resumen = {
        "cargas": 0,
        "facturas": 0,
        "completa": 0,
        "devolucion": 0,
        "no_entregada": 0,
        "sin_visitar": 0,
    }
    for c in cargas:
        conteos = {"completa": 0, "devolucion": 0, "no_entregada": 0, "sin_visitar": 0}
        monto = CERO
        detalle = []
        for f in por_carga.get(c.id, []):
            resultado, _valor_ent, _valor_dev = calcular_resultado(list(f.lineas.all()))
            bucket = _BUCKET[resultado]
            conteos[bucket] += 1
            monto += f.total
            detalle.append(
                {
                    "pk": f.pk,
                    "numero_factura": f.numero_factura,
                    "css": _CSS_BUCKET[bucket],
                    "estado_nombre": _NOMBRE_BUCKET[bucket],
                    "cliente_codigo": f.cliente.codigo,
                    "cliente_nombre": f.cliente.nombre,
                    "bultos": f.paquetes,
                    "monto": _r(f.total),
                }
            )
        c.total_facturas = len(detalle)
        c.completa = conteos["completa"]
        c.devolucion = conteos["devolucion"]
        c.no_entregada = conteos["no_entregada"]
        c.sin_visitar = conteos["sin_visitar"]
        c.monto = _r(monto)
        c.avance = (
            round((c.completa + c.devolucion) * 100 / c.total_facturas)
            if c.total_facturas
            else None
        )
        c.detalle_facturas = detalle

        resumen["cargas"] += 1
        resumen["facturas"] += c.total_facturas
        resumen["completa"] += c.completa
        resumen["devolucion"] += c.devolucion
        resumen["no_entregada"] += c.no_entregada
        resumen["sin_visitar"] += c.sin_visitar

    return cargas, resumen, recortado
