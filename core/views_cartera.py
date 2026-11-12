"""
Cambio #28: pantallas de cartera para revisar devoluciones y "dar por
enterado" (estatus "Recibido por cartera").

Entran los usuarios con el permiso "Puede dar por enterada una devolución
(cartera)" (grupo "Cartera"; también Administración, Supervisores y
superusuarios). No necesitan ser "staff". Usan el mismo diseño que las
pantallas de bodega.
"""

from datetime import datetime
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.cartera import (
    FILTROS,
    PENDIENTE,
    CarteraError,
    dar_por_enterado,
    devoluciones_para_cartera,
    item_cartera,
    periodo_por_defecto,
    puede_revisar,
)


def cartera_required(vista):
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('cartera_acceso')}?next={request.get_full_path()}")
        if not puede_revisar(request.user):
            return render(request, "core/cartera/sin_permiso.html", status=403)
        return vista(request, *args, **kwargs)

    return envoltura


class CarteraLoginView(LoginView):
    template_name = "core/cartera/acceso.html"
    redirect_authenticated_user = False

    def get_success_url(self):
        destino = self.request.POST.get("next") or self.request.GET.get("next") or ""
        if destino and url_has_allowed_host_and_scheme(
            destino, allowed_hosts={self.request.get_host()}
        ):
            return destino
        return reverse("cartera_lista")


@require_POST
def cartera_salir_view(request):
    logout(request)
    return redirect("cartera_acceso")


def _leer_fecha(texto, por_defecto):
    try:
        return datetime.strptime((texto or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return por_defecto


def _leer_seleccion(texto):
    tipo, _, numero = (texto or "").partition("-")
    if tipo in ("e", "sf") and numero.isdigit():
        return tipo, int(numero)
    return None, None


@cartera_required
def cartera_lista_view(request):
    desde_def, hasta_def = periodo_por_defecto()
    filtro = request.GET.get("ver", "pendientes")
    if filtro not in FILTROS:
        filtro = "pendientes"
    desde = _leer_fecha(request.GET.get("desde"), desde_def)
    hasta = _leer_fecha(request.GET.get("hasta"), hasta_def)
    if desde > hasta:
        desde, hasta = hasta, desde

    items, recortado = devoluciones_para_cartera(filtro, desde, hasta)
    pendientes, _ = (
        (items, False) if filtro == "pendientes" else devoluciones_para_cartera("pendientes")
    )

    tipo, numero = _leer_seleccion(request.GET.get("sel"))
    seleccion = item_cartera(tipo, numero) if tipo else None

    consulta = {"ver": filtro, "desde": desde.isoformat(), "hasta": hasta.isoformat()}
    return render(
        request,
        "core/cartera/lista.html",
        {
            "items": items,
            "recortado": recortado,
            "filtro": filtro,
            "desde": desde,
            "hasta": hasta,
            "consulta": urlencode(consulta),
            "total_pendientes": len(pendientes),
            "seleccion": seleccion,
            "puede_enterar": seleccion is not None and seleccion["estado"] == PENDIENTE,
        },
    )


@cartera_required
@require_POST
def cartera_enterado_view(request, tipo, objeto_id):
    volver = request.POST.get("volver", "")
    destino = reverse("cartera_lista") + (f"?{volver}" if volver else "")
    if not url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        destino = reverse("cartera_lista")
    try:
        objeto = dar_por_enterado(
            tipo, objeto_id, usuario=request.user, comentario=request.POST.get("comentario", "")
        )
    except CarteraError as exc:
        messages.error(request, str(exc))
        separador = "&" if "?" in destino else "?"
        return redirect(f"{destino}{separador}sel={tipo}-{objeto_id}")
    folio = objeto.factura.numero_factura if tipo == "e" else objeto.codigo_reporte
    messages.success(request, f"Devolución de {folio}: recibida por cartera.")
    return redirect(destino)
