"""
Cambio #12: vistas del módulo de entregas. A propósito NO viven dentro del
admin de Django (no usan admin_site.admin_view), porque el repartidor entra
por código de carga + PIN, no con una cuenta de usuario de Django. El
personal de oficina, que sí tiene sesión de administrador, usa las mismas
pantallas: _carga_autorizada() deja pasar a cualquier usuario is_staff sin
pedirle código ni PIN.
"""

import secrets
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.entregas import RegistroEntregaError, registrar_entrega
from core.models import Carga, Factura, MotivoDevolucion

SESSION_KEY_CARGA = "entrega_carga_id"


def _moneda(valor):
    return f"{valor:,.2f}"


def _cantidad_plana(valor):
    """
    Convierte un Decimal a un string "plano" (punto decimal, sin separador de
    miles) apto para meterlo en un atributo value= de un <input type=number>
    o para que JS lo lea con parseFloat. Necesario porque LANGUAGE_CODE="es"
    hace que Django renderice un Decimal directamente en una plantilla con
    coma decimal (por ejemplo "18,000"), lo cual el navegador considera un
    valor inválido para type=number y termina mostrando el campo vacío
    (mismo tipo de problema ya visto con montos en dinero en el Cambio #8).
    """
    texto = f"{valor:.3f}".rstrip("0").rstrip(".")
    return texto if texto else "0"


def _carga_autorizada(request, carga_id):
    """
    Devuelve la Carga si el visitante tiene derecho a verla: o es personal de
    oficina con sesión de administrador (puede ver cualquier carga), o es un
    repartidor que ya pasó por entregas_acceso_view con el código y PIN
    correctos para justo esta carga (guardado en la sesión).
    """
    carga = get_object_or_404(Carga, pk=carga_id)
    if request.user.is_authenticated and request.user.is_staff:
        return carga
    if request.session.get(SESSION_KEY_CARGA) == carga.id:
        return carga
    return None


@require_http_methods(["GET", "POST"])
def entregas_acceso_view(request):
    error = None
    if request.method == "POST":
        codigo = request.POST.get("codigo_carga", "").strip().upper()
        pin = request.POST.get("pin_repartidor", "").strip()
        carga = None
        if codigo and pin:
            carga = (
                Carga.objects.filter(codigo_acceso=codigo)
                .select_related("conductor")
                .first()
            )
        pin_repartidor = carga.conductor.pin_acceso if carga and carga.conductor else ""
        if carga and pin_repartidor and secrets.compare_digest(pin_repartidor, pin):
            request.session[SESSION_KEY_CARGA] = carga.id
            return redirect("entregas_lista", carga_id=carga.id)
        error = "Código o PIN incorrectos. Verifica con tu planeador de rutas."

    return render(request, "core/entregas/acceso.html", {"error": error})


def entregas_salir_view(request, carga_id):
    if request.session.get(SESSION_KEY_CARGA) == carga_id:
        del request.session[SESSION_KEY_CARGA]
    return redirect("entregas_acceso")


def entregas_lista_view(request, carga_id):
    carga = _carga_autorizada(request, carga_id)
    if carga is None:
        return redirect("entregas_acceso")

    facturas = list(
        carga.facturas.filter(estado_factura="ACTIVA")
        .select_related("cliente", "modalidad_pago")
        .order_by("cliente__nombre", "numero_factura")
    )
    for factura in facturas:
        factura.total_fmt = _moneda(factura.total)

    total_facturas = len(facturas)
    entregadas = sum(1 for f in facturas if f.estado_entrega == "COMPLETA")
    porcentaje = round((entregadas / total_facturas) * 100) if total_facturas else 0

    return render(
        request,
        "core/entregas/lista.html",
        {
            "carga": carga,
            "facturas": facturas,
            "total_facturas": total_facturas,
            "entregadas": entregadas,
            "porcentaje": porcentaje,
            "es_staff": request.user.is_authenticated and request.user.is_staff,
        },
    )


@require_http_methods(["GET", "POST"])
def entregas_captura_view(request, carga_id, factura_id):
    carga = _carga_autorizada(request, carga_id)
    if carga is None:
        return redirect("entregas_acceso")

    factura = get_object_or_404(
        Factura.objects.select_related("cliente", "modalidad_pago"),
        pk=factura_id,
        carga=carga,
    )
    es_contado = factura.modalidad_pago.codigo == "CONTADO"
    motivos = MotivoDevolucion.objects.filter(activo=True).order_by("nombre")

    if request.method == "POST":
        lineas_pendientes = list(factura.lineas.all())
        cantidades = {}
        motivo_por_linea = {}
        for linea in lineas_pendientes:
            if linea.pendiente_entrega <= 0:
                continue
            crudo = request.POST.get(f"ent_{linea.id}", "").strip()
            try:
                cantidades[linea.id] = Decimal(crudo) if crudo else Decimal("0")
            except InvalidOperation:
                cantidades[linea.id] = Decimal("0")
            motivo_id = request.POST.get(f"motivo_{linea.id}")
            if motivo_id:
                motivo_por_linea[linea.id] = motivo_id

        motivo_general_id = request.POST.get("motivo_general") or None
        observaciones = request.POST.get("observaciones", "").strip()

        cobro = None
        if es_contado and request.POST.get("registrar_cobro") == "1":
            monto_crudo = request.POST.get("monto_cobrado", "").strip()
            try:
                monto = Decimal(monto_crudo) if monto_crudo else Decimal("0")
            except InvalidOperation:
                monto = Decimal("0")
            if monto > 0:
                cobro = {
                    "monto": monto,
                    "forma_pago": request.POST.get("forma_pago", "EFECTIVO"),
                    "referencia": request.POST.get("referencia", "").strip(),
                }

        try:
            registrar_entrega(
                factura=factura,
                repartidor=carga.conductor,
                cantidades_entregadas=cantidades,
                motivo_general_id=motivo_general_id,
                motivo_por_linea=motivo_por_linea,
                observaciones=observaciones,
                cobro=cobro,
            )
        except RegistroEntregaError as exc:
            messages.error(request, str(exc))
        else:
            texto = f"Entrega guardada para la factura {factura.numero_factura}."
            if cobro:
                texto += " Cobro registrado."
            messages.success(request, texto)
        return redirect("entregas_lista", carga_id=carga.id)

    lineas = [l for l in factura.lineas.select_related("producto").all() if l.pendiente_entrega > 0]
    for linea in lineas:
        linea.pendiente_str = _cantidad_plana(linea.pendiente_entrega)
        linea.facturada_str = _cantidad_plana(linea.cantidad_facturada)
    factura.total_fmt = _moneda(factura.total)
    factura.total_raw = _cantidad_plana(factura.total)

    return render(
        request,
        "core/entregas/captura.html",
        {
            "carga": carga,
            "factura": factura,
            "lineas": lineas,
            "motivos": motivos,
            "es_contado": es_contado,
        },
    )
