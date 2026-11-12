"""
Cambio #27: pantallas de ventas para solicitar devoluciones sin factura.

Entran los usuarios con el permiso "Puede solicitar devoluciones sin
factura" (grupo "Ventas — Devoluciones"; también Administración,
Supervisores y superusuarios). No necesitan ser "staff".
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from core.devoluciones_sf import crear_solicitud, puede_solicitar, solicitudes_recientes
from core.forms import SolicitarDevolucionSinFacturaForm


def devoluciones_required(vista):
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(
                f"{reverse('devoluciones_sf_acceso')}?next={request.get_full_path()}"
            )
        if not puede_solicitar(request.user):
            return render(request, "core/ventas_devoluciones/sin_permiso.html", status=403)
        return vista(request, *args, **kwargs)

    return envoltura


class DevolucionesSinFacturaLoginView(LoginView):
    template_name = "core/ventas_devoluciones/acceso.html"
    redirect_authenticated_user = False

    def get_success_url(self):
        destino = self.request.POST.get("next") or self.request.GET.get("next") or ""
        if destino and url_has_allowed_host_and_scheme(
            destino, allowed_hosts={self.request.get_host()}
        ):
            return destino
        return reverse("devoluciones_sf_solicitar")


@require_POST
def devoluciones_sf_salir_view(request):
    logout(request)
    return redirect("devoluciones_sf_acceso")


@devoluciones_required
@require_http_methods(["GET", "POST"])
def devoluciones_sf_solicitar_view(request):
    creada = None
    if request.method == "POST":
        form = SolicitarDevolucionSinFacturaForm(request.POST)
        if form.is_valid():
            creada = crear_solicitud(
                fecha_ingreso=form.cleaned_data["fecha_ingreso"],
                cliente=form.cleaned_data["cliente"],
                sucursal=form.cleaned_data["sucursal"],
                total_unidades_estimado=form.cleaned_data["total_unidades_estimado"],
                contacto=form.cleaned_data["contacto"],
                motivo=form.cleaned_data["motivo"],
                observaciones=form.cleaned_data["observaciones"],
                usuario=request.user,
            )
            messages.success(
                request,
                f"Solicitud guardada con el folio {creada.codigo_reporte}.",
            )
            return redirect("devoluciones_sf_solicitar")
    else:
        form = SolicitarDevolucionSinFacturaForm()

    return render(
        request,
        "core/ventas_devoluciones/solicitar.html",
        {"form": form, "recientes": solicitudes_recientes(15), "pestana": "solicitar"},
    )


@devoluciones_required
def devoluciones_sf_lista_view(request):
    return render(
        request,
        "core/ventas_devoluciones/lista.html",
        {"solicitudes": solicitudes_recientes(200), "pestana": "lista"},
    )
