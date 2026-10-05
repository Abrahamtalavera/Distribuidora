"""
Cambio #16: pantallas de bodega de producto terminado para recibir las
devoluciones de cada Carga.

El personal de bodega entra con su propio usuario y contraseña (se crean en
el admin y se agregan al grupo "Bodega PT"). No necesitan ser "staff": no
entran al panel de administración ni ven dinero; solo estas pantallas.
"""

from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from core.bodega import (
    ESTADOS_VISIBLES_BODEGA,
    RecepcionError,
    cargas_para_bodega,
    devoluciones_de_la_carga,
    puede_recibir,
    registrar_recepcion,
    resumen_recepcion,
)
from core.models import Carga


def _decimal_o_none(texto):
    texto = (texto or "").strip().replace(",", "")
    if texto == "":
        return None
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def bodega_required(vista):
    """Solo deja pasar a usuarios con permiso de recibir devoluciones."""

    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('bodega_acceso')}?next={request.path}")
        if not puede_recibir(request.user):
            return render(request, "core/bodega/sin_permiso.html", status=403)
        return vista(request, *args, **kwargs)

    return envoltura


class BodegaLoginView(LoginView):
    template_name = "core/bodega/acceso.html"
    redirect_authenticated_user = False

    def get_success_url(self):
        destino = self.request.POST.get("next") or self.request.GET.get("next") or ""
        if destino and url_has_allowed_host_and_scheme(
            destino, allowed_hosts={self.request.get_host()}
        ):
            return destino
        return reverse("bodega_lista")


@require_POST
def bodega_salir_view(request):
    logout(request)
    return redirect("bodega_acceso")


@bodega_required
def bodega_lista_view(request):
    pendientes, recibidas = cargas_para_bodega()
    return render(
        request,
        "core/bodega/lista.html",
        {"pendientes": pendientes, "recibidas": recibidas},
    )


@bodega_required
@require_http_methods(["GET", "POST"])
def bodega_recepcion_view(request, carga_id):
    carga = get_object_or_404(
        Carga.objects.select_related(
            "ruta", "conductor", "vehiculo", "devoluciones_recibidas_por"
        ),
        pk=carga_id,
        estado__in=ESTADOS_VISIBLES_BODEGA,
    )
    escrito = None  # lo escrito en un intento de confirmar que falló
    corrigiendo = request.GET.get("corregir") == "1"

    if request.method == "POST":
        devoluciones = devoluciones_de_la_carga(carga)
        escrito = {
            "cantidades": {
                d.id: (
                    request.POST.get(f"inv_{d.id}", "").strip(),
                    request.POST.get(f"mer_{d.id}", "").strip(),
                )
                for d in devoluciones
            },
            "observaciones": request.POST.get("observaciones_bodega", ""),
        }
        try:
            registrar_recepcion(
                carga,
                usuario=request.user,
                cantidades={
                    i: (_decimal_o_none(inv), _decimal_o_none(mer))
                    for i, (inv, mer) in escrito["cantidades"].items()
                },
                observaciones=escrito["observaciones"],
            )
        except RecepcionError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Recepción de {carga.codigo_reporte} confirmada.")
            return redirect("bodega_recepcion", carga_id=carga.id)

    devoluciones = devoluciones_de_la_carga(carga)
    recepcion = resumen_recepcion(carga, devoluciones)
    # Se muestra el formulario si falta recibir, si se pidió corregir (y se
    # puede), o si se acaba de intentar confirmar y falló.
    editando = recepcion["editable"] and (
        not recepcion["confirmada"] or corrigiendo or escrito is not None
    )
    for d in devoluciones:
        if escrito is not None:
            d.valor_inv, d.valor_mer = escrito["cantidades"].get(d.id, ("", ""))
        else:
            d.valor_inv = "" if d.recibido_inventario is None else _plano(d.recibido_inventario)
            d.valor_mer = "" if d.recibido_merma is None else _plano(d.recibido_merma)

    return render(
        request,
        "core/bodega/recepcion.html",
        {
            "carga": carga,
            "devoluciones": devoluciones,
            "recepcion": recepcion,
            "editando": editando,
            "obs_escrita": (
                escrito["observaciones"] if escrito is not None else carga.observaciones_bodega
            ),
        },
    )


def _plano(valor):
    """Decimal → texto con punto decimal y sin ceros de más, para un <input>."""
    texto = f"{valor:.3f}".rstrip("0").rstrip(".")
    return texto or "0"
