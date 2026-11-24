"""
Cambio #27: devoluciones de producto que el cliente regresa sin que estén
asociadas a una factura puntual.

Flujo:

1. Una persona de ventas (grupo "Ventas — Devoluciones") registra la
   solicitud con los datos generales: fecha de ingreso, sucursal a retirar,
   total de unidades estimado, contacto en la sucursal y motivo general.
2. Oficina la asigna a una Carga, desde el mismo planeador de Cargas que ya
   se usa para facturas (admin de Carga/Factura, Cambio #7/#23): se puede
   crear una Carga nueva o agregarla a una que ya existe.
3. El repartidor, al pasar por esa sucursal, captura el detalle de productos
   y cantidades que recibió (DevolucionSinFacturaDetalle).
4. Esas cantidades se entregan en bodega de producto terminado y se reciben
   por detalle (a inventario o como merma), igual que las devoluciones que
   sí vienen de una factura (Cambio #16, core.bodega).

Este módulo deja intactas las funciones de core.bodega que ya están en
producción (devoluciones_de_la_carga, resumen_recepcion, registrar_recepcion
y su uso en core.cierre): son del flujo de devoluciones CON factura. Aquí se
construye un flujo paralelo, con sus propios estados y su propia recepción,
que se muestra junto al anterior en la misma pantalla de bodega.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from core.bodega import ESTADOS_RECEPCION, ESTADOS_VISIBLES_BODEGA
from core.models import Carga, DevolucionSinFactura, DevolucionSinFacturaDetalle

CERO = Decimal("0")

PERMISO_SOLICITAR = "core.solicitar_devoluciones_sin_factura"
GRUPO_VENTAS_DEVOLUCIONES = "Ventas — Devoluciones"

# Estados en los que una devolución todavía puede asignarse (por primera vez)
# a una Carga.
ESTADOS_ASIGNABLES = ("SOLICITADA",)
# Estados en los que el repartidor la ve en su lista / puede capturarla.
ESTADOS_PARA_REPARTIDOR = ("ASIGNADA", "RECOLECTADA")
# Estados en los que ya tiene algo que bodega pueda recibir.
ESTADOS_CON_CAPTURA = ("RECOLECTADA", "CERRADA")
ESTADOS_DESTINO_CARGA = ("PLANEADA", "EN_RUTA")


class SolicitudError(Exception):
    pass


class AsignarDevolucionError(Exception):
    pass


class RecepcionSinFacturaError(Exception):
    pass


def puede_solicitar(usuario):
    return bool(
        usuario
        and usuario.is_authenticated
        and usuario.is_active
        and usuario.has_perm(PERMISO_SOLICITAR)
    )


# --- Solicitud (ventas) ------------------------------------------------


def crear_solicitud(
    *, fecha_ingreso, sucursal, total_unidades_estimado, motivo, contacto=None,
    contacto_sucursal="", cliente=None, observaciones="", usuario=None,
):
    """
    Cambio #29: guarda también el cliente (el de la sucursal si no se pasa)
    y el contacto elegido de la lista; su nombre queda además como texto.
    """
    if cliente is not None and sucursal.cliente_id != cliente.id:
        raise SolicitudError("Esa sucursal no es de este cliente.")
    return DevolucionSinFactura.objects.create(
        fecha_ingreso=fecha_ingreso,
        cliente=cliente or sucursal.cliente,
        sucursal=sucursal,
        total_unidades_estimado=total_unidades_estimado,
        contacto=contacto,
        contacto_sucursal=contacto.nombre if contacto else contacto_sucursal,
        motivo=motivo,
        observaciones=observaciones,
        creada_por=usuario if usuario and usuario.is_authenticated else None,
    )


def solicitudes_recientes(limite=50):
    return (
        DevolucionSinFactura.objects.select_related(
            "cliente", "sucursal__cliente", "motivo", "carga", "creada_por"
        )
        .order_by("-creada_en")[:limite]
    )


# --- Asignación a Carga (oficina, mismo planeador que agrupa facturas) -


def devoluciones_asignables():
    """Solicitudes todavía sin Carga, listas para que oficina las asigne."""
    return (
        DevolucionSinFactura.objects.filter(estado__in=ESTADOS_ASIGNABLES)
        .select_related("sucursal__cliente", "motivo")
        .order_by("fecha_ingreso", "id")
    )


# Cambio #40: una devolución ya asignada se puede pasar a otra carga (o
# volver a "Solicitada") mientras el repartidor no la haya recolectado.
ESTADOS_REASIGNABLES = ("SOLICITADA", "ASIGNADA")


def revisar_devoluciones(devoluciones, destino=None):
    """
    Anota en cada devolución .puede_asignar (bool), .por_que_no y .aviso_carga
    (lo que va a pasar, p. ej. que sale de otra carga). Devuelve
    (aptas, no_aptas). Mismo criterio que core.asignar.revisar para facturas.
    """
    aptas, no_aptas = [], []
    for d in devoluciones:
        d.puede_asignar, d.por_que_no, d.aviso_carga = True, "", ""
        actual = d.carga
        if d.estado not in ESTADOS_REASIGNABLES:
            d.puede_asignar, d.por_que_no = False, "Ya fue recolectada por el repartidor"
        elif destino is not None and d.carga_id == destino.id:
            d.puede_asignar, d.por_que_no = False, "Ya está en esta carga"
        elif actual is not None:
            d.aviso_carga = f"Sale de {actual.codigo_reporte} ({actual.get_estado_display().lower()})"
        (aptas if d.puede_asignar else no_aptas).append(d)
    return aptas, no_aptas


@transaction.atomic
def asignar_a_carga_existente(destino, devoluciones):
    """
    Asigna las devoluciones a una Carga que ya existe. Las que estaban en
    otra carga y todavía no se recolectan se mueven (Cambio #40).
    Devuelve la lista de las que de verdad se asignaron.
    """
    destino = Carga.objects.select_for_update(of=("self",)).get(pk=destino.pk)
    if destino.estado not in ESTADOS_DESTINO_CARGA:
        raise AsignarDevolucionError(
            f"{destino.codigo_reporte} está {destino.get_estado_display().lower()}: "
            "solo se asignan devoluciones a cargas Planeadas o En ruta."
        )
    aptas, _ = revisar_devoluciones(devoluciones, destino)
    if not aptas:
        raise AsignarDevolucionError(
            "Ninguna de las devoluciones seleccionadas se puede asignar a esta carga "
            "(ya están en ella o el repartidor ya las recolectó)."
        )
    ids = [d.id for d in aptas]
    DevolucionSinFactura.objects.filter(id__in=ids, estado__in=ESTADOS_REASIGNABLES).update(
        carga=destino, estado="ASIGNADA"
    )
    for d in aptas:
        d.carga = destino
        d.estado = "ASIGNADA"
    return aptas


@transaction.atomic
def crear_carga_con_devoluciones(
    *, fecha_planeada, ruta, conductor, vehiculo, km_inicial, observaciones, devoluciones,
):
    """Crea una Carga nueva y le asigna las devoluciones seleccionadas."""
    aptas, _ = revisar_devoluciones(devoluciones)
    if not aptas:
        raise AsignarDevolucionError(
            "Ninguna de las devoluciones seleccionadas se puede asignar "
            "(el repartidor ya las recolectó)."
        )
    carga = Carga.objects.create(
        fecha_planeada=fecha_planeada,
        ruta=ruta,
        conductor=conductor,
        vehiculo=vehiculo,
        km_inicial=km_inicial,
        observaciones=observaciones,
    )
    ids = [d.id for d in aptas]
    DevolucionSinFactura.objects.filter(id__in=ids, estado__in=ESTADOS_REASIGNABLES).update(
        carga=carga, estado="ASIGNADA"
    )
    return carga, aptas


@transaction.atomic
def quitar_de_carga(devoluciones):
    """
    Cambio #40: saca de su carga las devoluciones asignadas que todavía no
    se recolectan; vuelven a "Solicitada" para asignarlas de nuevo.
    """
    aptas = [d for d in devoluciones if d.estado == "ASIGNADA"]
    if not aptas:
        raise AsignarDevolucionError(
            "Ninguna de las devoluciones seleccionadas está asignada sin recolectar."
        )
    DevolucionSinFactura.objects.filter(id__in=[d.id for d in aptas], estado="ASIGNADA").update(
        carga=None, estado="SOLICITADA"
    )
    return aptas


# --- Captura del repartidor ---------------------------------------------


def devoluciones_de_la_carga_para_repartidor(carga):
    """
    Devoluciones de esta Carga que el repartidor debe ver/capturar. Trae
    también sus líneas ya capturadas (con el motivo por producto, Cambio
    #27.1) para que el reporte de despacho pueda mostrarlas si se reimprime
    después de la recolección.
    """
    return list(
        DevolucionSinFactura.objects.filter(carga=carga, estado__in=ESTADOS_PARA_REPARTIDOR)
        .select_related("sucursal__cliente", "motivo")
        .prefetch_related(
            Prefetch(
                "detalles",
                queryset=DevolucionSinFacturaDetalle.objects.select_related(
                    "producto", "motivo"
                ),
            )
        )
        .order_by("sucursal__cliente__nombre", "id")
    )


def devolucion_para_repartidor(carga, devolucion_id):
    return (
        DevolucionSinFactura.objects.filter(
            pk=devolucion_id, carga=carga, estado__in=ESTADOS_PARA_REPARTIDOR
        )
        .select_related("sucursal__cliente", "motivo")
        .prefetch_related("detalles__producto")
        .first()
    )


@transaction.atomic
def registrar_captura(
    devolucion, *, cantidades=None, motivos=None, lineas_capturadas=None, observaciones=""
):
    """
    Guarda lo que el repartidor recolectó.

    lineas_capturadas (Cambio #28): lista de dicts
    {"producto_id", "cantidad", "motivo_id", "lote", "fecha_vencimiento"}.
    Un mismo producto puede venir en varias líneas (otro motivo u otro
    lote); cada una se guarda por separado. Lote y vencimiento son
    opcionales.

    cantidades / motivos: forma anterior ({producto_id: cantidad} y
    {producto_id: motivo_id}, una línea por producto); se sigue aceptando.

    El motivo es obligatorio en cada línea con cantidad > 0 (Cambio #27.1).
    Si la devolución ya estaba en RECOLECTADA (el repartidor vuelve a
    corregir antes de que bodega la reciba), se reemplazan las líneas
    anteriores.
    """
    from core.entregas import leer_fecha_vencimiento, limpiar_lote

    if devolucion.estado not in ESTADOS_PARA_REPARTIDOR:
        raise SolicitudError("Esta devolución ya no se puede capturar.")
    if devolucion.carga_id is None:
        raise SolicitudError("Esta devolución no tiene carga asignada.")

    if lineas_capturadas is None:
        motivos = motivos or {}
        lineas_capturadas = [
            {"producto_id": pid, "cantidad": cant, "motivo_id": motivos.get(pid)}
            for pid, cant in (cantidades or {}).items()
        ]

    if devolucion.estado == "RECOLECTADA":
        devolucion.detalles.all().delete()

    lineas = []
    for capturada in lineas_capturadas:
        cantidad = capturada.get("cantidad")
        if not cantidad or cantidad <= 0:
            continue
        motivo_id = capturada.get("motivo_id")
        if not motivo_id:
            raise SolicitudError(
                "Falta elegir el motivo de devolución de uno o más productos."
            )
        vence = capturada.get("fecha_vencimiento")
        if isinstance(vence, str):
            vence = leer_fecha_vencimiento(vence)
        lineas.append(
            DevolucionSinFacturaDetalle(
                devolucion=devolucion,
                producto_id=capturada["producto_id"],
                cantidad_recolectada=cantidad,
                motivo_id=motivo_id,
                lote=limpiar_lote(capturada.get("lote")),
                fecha_vencimiento=vence,
            )
        )
    if not lineas:
        raise SolicitudError("Agrega al menos un producto con cantidad mayor que cero.")
    DevolucionSinFacturaDetalle.objects.bulk_create(lineas)

    devolucion.estado = "RECOLECTADA"
    devolucion.recolectada_en = timezone.now()
    if observaciones:
        devolucion.observaciones = observaciones
    devolucion.save(update_fields=["estado", "recolectada_en", "observaciones"])
    return devolucion


# --- Recepción en bodega de producto terminado --------------------------


def detalles_sin_factura_de_la_carga(carga):
    """Líneas de devoluciones sin factura de esta Carga, listas para bodega."""
    return list(
        DevolucionSinFacturaDetalle.objects.filter(
            devolucion__carga=carga, devolucion__estado__in=ESTADOS_CON_CAPTURA
        )
        .select_related(
            "devolucion__sucursal__cliente", "devolucion__cliente", "devolucion__motivo", "producto", "motivo",
            "devolucion__revisada_cartera_por",
        )
        .order_by("devolucion__id", "id")
    )


def resumen_recepcion_sin_factura(carga, detalles=None):
    """Estado de la recepción en bodega de las devoluciones sin factura de esta Carga."""
    if detalles is None:
        detalles = detalles_sin_factura_de_la_carga(carga)

    totales = {"reportado": CERO, "inventario": CERO, "merma": CERO, "recibido": CERO}
    faltan = 0
    hay_diferencia = False
    for d in detalles:
        totales["reportado"] += d.cantidad_recolectada
        if not d.recepcion_registrada:
            faltan += 1
            continue
        totales["inventario"] += d.recibido_inventario or CERO
        totales["merma"] += d.recibido_merma or CERO
        totales["recibido"] += d.recibido_total
        if d.diferencia_recepcion != 0:
            hay_diferencia = True

    confirmada_ya = bool(detalles) and all(d.devolucion.estado == "CERRADA" for d in detalles)
    if not detalles:
        estado = "SIN_DEVOLUCIONES"
    elif faltan or not confirmada_ya:
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
        "productos": len(detalles),
        "editable": not (carga.esta_cerrada and confirmada),
    }


@transaction.atomic
def registrar_recepcion_sin_factura(carga, *, usuario, cantidades, observaciones="", lotes=None):
    """
    Bodega confirma lo recibido de las devoluciones sin factura de esta Carga.

    cantidades: {id de DevolucionSinFacturaDetalle: (a_inventario, merma)}.
    Misma regla que core.bodega.registrar_recepcion: al menos una de las dos
    cantidades por producto, y observación obligatoria si hay diferencia.
    """
    if carga.estado not in ESTADOS_VISIBLES_BODEGA:
        raise RecepcionSinFacturaError(
            "Esta carga todavía está en ruta: el repartidor no ha terminado."
        )
    detalles = detalles_sin_factura_de_la_carga(carga)
    if not detalles:
        raise RecepcionSinFacturaError("Esta carga no tiene devoluciones sin factura que recibir.")
    if not resumen_recepcion_sin_factura(carga, detalles)["editable"]:
        raise RecepcionSinFacturaError(
            "La carga ya está cerrada y esta recepción quedó confirmada. "
            "Para corregirla, oficina debe reabrir la carga."
        )

    observaciones = (observaciones or "").strip()
    nuevos = []
    hay_diferencia = False
    for d in detalles:
        inventario, merma = cantidades.get(d.id, (None, None))
        if inventario is None and merma is None:
            raise RecepcionSinFacturaError(
                "Falta escribir las cantidades de todos los productos "
                "(si de alguno no llegó nada, escribe 0)."
            )
        inventario = inventario or CERO
        merma = merma or CERO
        if inventario < 0 or merma < 0:
            raise RecepcionSinFacturaError("Las cantidades recibidas no pueden ser negativas.")
        if inventario + merma != d.cantidad_recolectada:
            hay_diferencia = True
        nuevos.append((d, inventario, merma))

    if hay_diferencia and not observaciones:
        raise RecepcionSinFacturaError(
            "Hay diferencia entre lo recolectado y lo recibido: escribe el motivo "
            "en las observaciones para poder confirmar."
        )

    # Cambio #28: bodega puede completar o corregir lote y vencimiento
    # (opcionales). lotes: {id de la línea: (lote, fecha_vencimiento)}; una
    # línea que no viene en el dict conserva lo que tenía.
    lotes = lotes or {}
    for d, inventario, merma in nuevos:
        d.recibido_inventario = inventario
        d.recibido_merma = merma
        campos = ["recibido_inventario", "recibido_merma"]
        if d.id in lotes:
            d.lote, d.fecha_vencimiento = lotes[d.id]
            campos += ["lote", "fecha_vencimiento"]
        d.save(update_fields=campos)

    ids_devolucion = {d.devolucion_id for d, _, _ in nuevos}
    DevolucionSinFactura.objects.filter(id__in=ids_devolucion).update(
        estado="CERRADA",
        recibido_en=timezone.now(),
        recibido_por=usuario,
        observaciones_bodega=observaciones,
    )
    return detalles


def resumen_recepcion_combinado(carga):
    """
    Junta el estado de recepción de las devoluciones CON factura (core.bodega,
    sin modificar) y SIN factura (este módulo) para mostrarse en un solo pill
    en la lista de bodega. No se usa en core.cierre (ese reporte solo mira
    las devoluciones con factura, que son las que se reconcilian con dinero).
    """
    from core.bodega import resumen_recepcion as _resumen_con_factura

    r_cf = _resumen_con_factura(carga)
    r_sf = resumen_recepcion_sin_factura(carga)
    estados = {r_cf["estado"], r_sf["estado"]}
    if estados <= {"SIN_DEVOLUCIONES"}:
        estado = "SIN_DEVOLUCIONES"
    elif "PENDIENTE" in estados:
        estado = "PENDIENTE"
    elif "CON_DIFERENCIA" in estados:
        estado = "CON_DIFERENCIA"
    else:
        estado = "COMPLETA"
    nombre, css = ESTADOS_RECEPCION[estado]

    recibido_sf = carga.devoluciones_sin_factura.filter(recibido_en__isnull=False).order_by(
        "-recibido_en"
    ).first()
    fechas = [f for f in (carga.devoluciones_recibidas_en, recibido_sf.recibido_en if recibido_sf else None) if f]
    return {
        "estado": estado,
        "nombre": nombre,
        "css": css,
        "productos": r_cf["productos"] + r_sf["productos"],
        "recibido_en": max(fechas) if fechas else None,
        "recibido_por": carga.devoluciones_recibidas_por or (recibido_sf.recibido_por if recibido_sf else None),
    }


def cargas_para_bodega_combinado(dias_recibidas=7):
    """
    Igual que core.bodega.cargas_para_bodega, pero también incluye las
    Cargas que solo tienen devoluciones SIN factura (y ninguna con factura).
    """
    from datetime import timedelta

    from django.db.models import Q

    con_devolucion = (
        Carga.objects.filter(
            Q(entregas__lineas__cantidad_devuelta__gt=0)
            | Q(devoluciones_sin_factura__estado__in=ESTADOS_CON_CAPTURA),
            estado__in=ESTADOS_VISIBLES_BODEGA,
        )
        .select_related("ruta", "conductor", "vehiculo", "devoluciones_recibidas_por")
        .distinct()
    )
    limite = timezone.now() - timedelta(days=dias_recibidas)
    pendientes, recibidas = [], []
    for carga in con_devolucion.order_by("fecha_planeada", "id"):
        carga.recepcion = resumen_recepcion_combinado(carga)
        if carga.recepcion["estado"] == "PENDIENTE":
            pendientes.append(carga)
        elif carga.recepcion["estado"] in ("COMPLETA", "CON_DIFERENCIA"):
            recibido_en = carga.recepcion["recibido_en"]
            if recibido_en and recibido_en >= limite:
                recibidas.append(carga)
    recibidas.sort(key=lambda c: c.recepcion["recibido_en"], reverse=True)
    return pendientes, recibidas
