"""
Cambio #19: pantalla del tablero de operación.

Entran los usuarios con el permiso "Puede ver el tablero de operación"
(grupos "Administración" y "Supervisores", y los superusuarios). No
necesitan ser "staff": un supervisor puede ver el tablero sin entrar al
panel de administración. Los enlaces al admin solo se muestran a quien sí
puede entrar ahí.
"""

from datetime import date
from functools import wraps

from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.tablero import puede_ver_tablero, resumen_tablero


def tablero_required(vista):
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('tablero_acceso')}?next={request.path}")
        if not puede_ver_tablero(request.user):
            return render(request, "core/tablero/sin_permiso.html", status=403)
        return vista(request, *args, **kwargs)

    return envoltura


class TableroLoginView(LoginView):
    template_name = "core/tablero/acceso.html"

    def get_success_url(self):
        destino = self.request.POST.get("next") or self.request.GET.get("next") or ""
        if destino and url_has_allowed_host_and_scheme(
            destino, allowed_hosts={self.request.get_host()}
        ):
            return destino
        return reverse("tablero")


@require_POST
def tablero_salir_view(request):
    logout(request)
    return redirect("tablero_acceso")


def _leer_dia(texto):
    """Fecha del parámetro ?dia=AAAA-MM-DD; hoy si viene vacía o mal escrita."""
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError:
        return timezone.localdate()


@tablero_required
def tablero_view(request):
    dia = _leer_dia(request.GET.get("dia"))
    contexto = resumen_tablero(dia)
    contexto["dia_anterior"] = dia.fromordinal(dia.toordinal() - 1)
    contexto["dia_siguiente"] = dia.fromordinal(dia.toordinal() + 1)
    contexto["enlaces_admin"] = request.user.is_staff
    return render(request, "core/tablero/tablero.html", contexto)
