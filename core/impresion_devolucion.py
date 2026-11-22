"""
Cambio #37: hoja imprimible de una devolución ("DEVOLUCIONES – Bodega de
Producto Terminado"), según el formato físico que ya usa el negocio.

Se imprime desde la ficha de Cartera › Revisión de devoluciones y desde la
recepción de bodega, siempre DESPUÉS de que bodega recibió la devolución
(antes faltarían la fecha de revisión, el estado y la bodega de destino).

Una devolución es:
- con factura: lo devuelto en una visita (una Entrega con líneas devueltas);
- sin factura: una DevolucionSinFactura (Cambio #27).

Cada línea sale con su causa (el motivo que registró el repartidor). Si una
línea tuvo parte a inventario y parte a merma, sale en dos filas: a
inventario va a Bodega 4 y a merma a Bodega 7. El precio sale de la factura
(con factura) o del catálogo de productos (sin factura) y se puede cambiar
en la hoja antes de imprimir; el IVA sale de la factura o, sin factura, al
15%.
"""

from decimal import Decimal, InvalidOperation

from django.db.models import Prefetch
from django.utils import timezone

from core.cartera import devolucion_sf_recibida_en_bodega, entrega_recibida_en_bodega
from core.models import (
    DatosImpresionDevolucion,
    DevolucionSinFactura,
    DevolucionSinFacturaDetalle,
    Entrega,
    EntregaDetalle,
)

CERO = Decimal("0")
TASA_IVA_SIN_FACTURA = Decimal("0.15")
BODEGA_INVENTARIO = "Bodega 4"
BODEGA_MERMA = "Bodega 7"
FILAS_MINIMAS = 14


class ImpresionError(Exception):
    pass


def _plano(valor):
    """Decimal → texto con punto decimal y sin ceros de más."""
    return f"{valor:.4f}".rstrip("0").rstrip(".") if valor is not None else ""


def _filas_de_linea(linea_id, base, inventario, merma, total):
    """Una fila por destino (inventario / merma) con lo que bodega registró."""
    filas = []
    if inventario:
        filas.append(dict(base, clave=f"{linea_id}-inv", cantidad=inventario, estado="A inventario", inventario=True, bodega=BODEGA_INVENTARIO))
    if merma:
        filas.append(dict(base, clave=f"{linea_id}-mer", cantidad=merma, estado="Merma", inventario=False, bodega=BODEGA_MERMA))
    if not filas:  # bodega no recibió nada de esta línea
        filas.append(dict(base, clave=f"{linea_id}-nr", cantidad=total, estado="No recibido", inventario=None, bodega="—"))
    return filas


def _hoja_entrega(entrega_id):
    e = (
        Entrega.objects.select_related(
            "factura__cliente", "factura__sucursal", "carga__ruta", "carga__conductor",
            "repartidor", "revisada_cartera_por", "motivo_devolucion_general",
        )
        .prefetch_related(
            Prefetch(
                "lineas",
                queryset=EntregaDetalle.objects.filter(cantidad_devuelta__gt=0)
                .select_related("factura_detalle__producto", "motivo_devolucion")
                .order_by("id"),
            )
        )
        .filter(pk=entrega_id, estado="CONFIRMADA")
        .first()
    )
    lineas = list(e.lineas.all()) if e else []
    if e is None or not lineas:
        raise ImpresionError("Esa devolución no existe.")
    if not entrega_recibida_en_bodega(e, lineas):
        raise ImpresionError("Bodega todavía no ha recibido esta devolución; se podrá imprimir después.")

    factura = e.factura
    filas = []
    for d in lineas:
        fd = d.factura_detalle
        tasa = (fd.iva / fd.subtotal) if fd.subtotal else CERO
        motivo = d.motivo_devolucion or e.motivo_devolucion_general
        base = {
            "codigo": fd.producto.codigo,
            "nombre": fd.producto.nombre,
            "causa": motivo.nombre if motivo else "",
            "precio": fd.precio_unitario,
            "tasa_iva": tasa,
            "lote": d.lote,
            "vence": d.fecha_vencimiento,
            "factura": factura.numero_factura,
        }
        filas += _filas_de_linea(d.id, base, d.recibido_inventario, d.recibido_merma, d.cantidad_devuelta)

    carga = e.carga
    sucursal = factura.sucursal
    persona = e.repartidor or (carga.conductor if carga else None)
    return {
        "tipo": "e",
        "objeto_id": e.id,
        "numero": e.documento_devolucion or f"Fact. {factura.numero_factura}",
        "tipo_nombre": f"Devolución con factura {factura.numero_factura}",
        "fecha_devolucion": timezone.localtime(e.fecha_entrega).date(),
        "fecha_revision": timezone.localtime(carga.devoluciones_recibidas_en).date() if carga and carga.devoluciones_recibidas_en else None,
        "cliente": factura.cliente.nombre,
        "persona": persona.nombre if persona else "",
        "sucursal": sucursal.nombre if sucursal else factura.cliente.sucursal,
        "departamento": sucursal.departamento if sucursal else "",
        "filas": filas,
        "revisada_en": e.revisada_cartera_en,
        "revisada_por": e.revisada_cartera_por,
        "carga": carga,
    }


def _hoja_sin_factura(devolucion_id):
    dev = (
        DevolucionSinFactura.objects.select_related(
            "cliente", "sucursal__cliente", "carga__ruta", "revisada_cartera_por",
        )
        .prefetch_related(
            Prefetch(
                "detalles",
                queryset=DevolucionSinFacturaDetalle.objects.select_related("producto", "motivo").order_by("id"),
            )
        )
        .filter(pk=devolucion_id)
        .first()
    )
    if dev is None:
        raise ImpresionError("Esa devolución no existe.")
    if not devolucion_sf_recibida_en_bodega(dev):
        raise ImpresionError("Bodega todavía no ha recibido esta devolución; se podrá imprimir después.")

    filas = []
    for d in dev.detalles.all():
        base = {
            "codigo": d.producto.codigo,
            "nombre": d.producto.nombre,
            "causa": d.motivo.nombre if d.motivo_id else "",
            "precio": d.producto.precio_unitario,
            "tasa_iva": TASA_IVA_SIN_FACTURA,
            "lote": d.lote,
            "vence": d.fecha_vencimiento,
            "factura": "—",
        }
        filas += _filas_de_linea(d.id, base, d.recibido_inventario, d.recibido_merma, d.cantidad_recolectada)

    return {
        "tipo": "sf",
        "objeto_id": dev.id,
        "numero": dev.codigo_reporte,
        "tipo_nombre": "Devolución sin factura",
        "fecha_devolucion": timezone.localtime(dev.recolectada_en).date() if dev.recolectada_en else dev.fecha_ingreso,
        "fecha_revision": timezone.localtime(dev.recibido_en).date() if dev.recibido_en else None,
        "cliente": (dev.cliente or dev.sucursal.cliente).nombre,
        "persona": dev.contacto_sucursal,
        "sucursal": dev.sucursal.nombre,
        "departamento": dev.sucursal.departamento,
        "filas": filas,
        "revisada_en": dev.revisada_cartera_en,
        "revisada_por": dev.revisada_cartera_por,
        "carga": dev.carga,
    }


def puede_editar(usuario, guardado):
    """
    Cambio #37.1: la primera vez solo cartera digita y guarda; una vez
    guardado, solo un administrador (superusuario) puede cambiarlo.
    """
    from core.cartera import puede_revisar

    if guardado is None:
        return puede_revisar(usuario)
    return bool(usuario and usuario.is_active and usuario.is_superuser)


def _totales(filas):
    subtotal = iva = CERO
    for f in filas:
        f["precio_texto"] = _plano(f["precio"])
        f["total"] = (f["cantidad"] * f["precio"]).quantize(Decimal("0.01"))
        subtotal += f["total"]
        iva += (f["total"] * f["tasa_iva"]).quantize(Decimal("0.01"))
    return subtotal, iva


def hoja(tipo, objeto_id):
    """Datos de la hoja imprimible. Lanza ImpresionError si no se puede imprimir."""
    if tipo == "e":
        datos = _hoja_entrega(objeto_id)
    elif tipo == "sf":
        datos = _hoja_sin_factura(objeto_id)
    else:
        raise ImpresionError("Esa devolución no existe.")

    guardado = (
        DatosImpresionDevolucion.objects.select_related("guardado_por", "modificado_por")
        .filter(tipo=tipo, objeto_id=objeto_id)
        .first()
    )
    for i, f in enumerate(datos["filas"]):
        f["n"] = i
        f["cant_texto"] = _plano(f["cantidad"])
        f["tasa_texto"] = _plano(f["tasa_iva"])
        if guardado and f["clave"] in guardado.precios:
            try:
                f["precio"] = Decimal(str(guardado.precios[f["clave"]]))
            except InvalidOperation:
                pass
    datos["subtotal"], datos["iva"] = _totales(datos["filas"])
    datos["total"] = datos["subtotal"] + datos["iva"]
    datos["guardado"] = guardado
    datos["numero_fisico"] = guardado.numero_fisico if guardado else ""
    datos["vacias"] = range(max(FILAS_MINIMAS - len(datos["filas"]), 0))
    return datos


def guardar(datos, usuario, numero_fisico, precios_texto):
    """
    Guarda el N° físico y los precios digitados. `precios_texto` es
    {clave_de_fila: texto}. Lanza ImpresionError si algo no es válido.
    Los totales se calculan aquí, no se confía en los del navegador.
    """
    guardado = datos["guardado"]
    if not puede_editar(usuario, guardado):
        raise ImpresionError(
            "Solo un administrador puede cambiar estos datos una vez guardados."
            if guardado else "Solo el personal de cartera puede guardar estos datos."
        )
    precios = {}
    for f in datos["filas"]:
        texto = (precios_texto.get(f["clave"]) or "").replace(",", "").strip()
        try:
            precio = Decimal(texto) if texto else CERO
        except InvalidOperation:
            raise ImpresionError(f"El precio de {f['nombre']} no es un número válido.")
        if precio < 0 or precio >= Decimal("1e9"):
            raise ImpresionError(f"El precio de {f['nombre']} no es válido.")
        f["precio"] = precio.quantize(Decimal("0.0001"))
        precios[f["clave"]] = _plano(f["precio"])
    subtotal, iva = _totales(datos["filas"])

    if guardado is None:
        guardado = DatosImpresionDevolucion(tipo=datos["tipo"], objeto_id=datos["objeto_id"], guardado_por=usuario)
    else:
        guardado.modificado_por = usuario
        guardado.modificado_en = timezone.now()
    guardado.numero_fisico = (numero_fisico or "").strip()[:30]
    guardado.precios = precios
    guardado.subtotal, guardado.iva, guardado.total = subtotal, iva, subtotal + iva
    guardado.save()
    return guardado


def imprimibles_de_la_carga(carga):
    """
    Devoluciones de esta carga que ya se pueden imprimir (bodega las
    recibió), para los botones de la recepción de bodega.
    """
    items = []
    entregas = (
        Entrega.objects.filter(carga=carga, estado="CONFIRMADA", lineas__cantidad_devuelta__gt=0)
        .distinct()
        .select_related("factura__cliente", "carga")
        .prefetch_related(
            Prefetch("lineas", queryset=EntregaDetalle.objects.filter(cantidad_devuelta__gt=0))
        )
        .order_by("factura__numero_factura")
    )
    for e in entregas:
        if entrega_recibida_en_bodega(e, list(e.lineas.all())):
            items.append({
                "tipo": "e", "id": e.id,
                "titulo": e.documento_devolucion or f"Fact. {e.factura.numero_factura}",
                "detalle": e.factura.cliente.nombre,
            })
    for d in (
        DevolucionSinFactura.objects.filter(carga=carga, estado="CERRADA")
        .select_related("sucursal__cliente", "cliente")
        .order_by("id")
    ):
        items.append({
            "tipo": "sf", "id": d.id, "titulo": d.codigo_reporte,
            "detalle": f"{(d.cliente or d.sucursal.cliente).nombre} — {d.sucursal.nombre}",
        })
    return items
