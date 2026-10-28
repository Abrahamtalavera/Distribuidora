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
    contexto["pestana"] = "dia"
    return render(request, "core/tablero/tablero.html", contexto)


# ---------------------------------------------------------------------------
# Cambio #20: análisis de un período (gráficas de devoluciones y desempeño)
# ---------------------------------------------------------------------------

MAX_DIAS_ANALISIS = 366


def _leer_fecha(texto, por_defecto):
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError:
        return por_defecto


def _leer_id(texto):
    texto = (texto or "").strip()
    return int(texto) if texto.isdigit() else None


def _atajos_periodo():
    hoy = timezone.localdate()
    inicio_mes = hoy.replace(day=1)
    fin_mes_ant = inicio_mes.fromordinal(inicio_mes.toordinal() - 1)
    return [
        ("Últimos 7 días", hoy.fromordinal(hoy.toordinal() - 6), hoy),
        ("Últimos 30 días", hoy.fromordinal(hoy.toordinal() - 29), hoy),
        ("Este mes", inicio_mes, hoy),
        ("Mes anterior", fin_mes_ant.replace(day=1), fin_mes_ant),
    ]


@tablero_required
def tablero_analisis_view(request):
    from core.analisis import periodo_por_defecto, resumen_analisis
    from core.models import Ruta, Vendedor

    desde_def, hasta_def = periodo_por_defecto()
    desde = _leer_fecha(request.GET.get("desde"), desde_def)
    hasta = _leer_fecha(request.GET.get("hasta"), hasta_def)
    if desde > hasta:
        desde, hasta = hasta, desde
    aviso = ""
    if (hasta - desde).days >= MAX_DIAS_ANALISIS:
        desde = hasta.fromordinal(hasta.toordinal() - MAX_DIAS_ANALISIS + 1)
        aviso = f"El período se limitó a {MAX_DIAS_ANALISIS} días."
    ruta_id = _leer_id(request.GET.get("ruta"))
    repartidor_id = _leer_id(request.GET.get("repartidor"))

    contexto = resumen_analisis(desde, hasta, ruta_id, repartidor_id)
    contexto.update(
        {
            "desde": desde,
            "hasta": hasta,
            "ruta_id": ruta_id,
            "repartidor_id": repartidor_id,
            "rutas": Ruta.objects.order_by("nombre"),
            "repartidores": Vendedor.objects.filter(tipo="REPARTIDOR").order_by("nombre"),
            "aviso": aviso,
            "enlaces_admin": request.user.is_staff,
            "pestana": "analisis",
            "atajos": _atajos_periodo(),
        }
    )
    return render(request, "core/tablero/analisis.html", contexto)


# ---------------------------------------------------------------------------
# Cambio #24: consulta de Cargas (filtros, resumen y detalle de facturas)
# ---------------------------------------------------------------------------


@tablero_required
def tablero_cargas_view(request):
    from core.consulta_cargas import (
        MAX_CARGAS_LISTADO,
        MAX_DIAS_LISTADO,
        listado_cargas,
        periodo_por_defecto,
    )
    from core.models import Carga, Ruta, Vendedor

    desde_def, hasta_def = periodo_por_defecto()
    desde = _leer_fecha(request.GET.get("desde"), desde_def)
    hasta = _leer_fecha(request.GET.get("hasta"), hasta_def)
    if desde > hasta:
        desde, hasta = hasta, desde
    aviso = ""
    if (hasta - desde).days >= MAX_DIAS_LISTADO:
        desde = hasta.fromordinal(hasta.toordinal() - MAX_DIAS_LISTADO + 1)
        aviso = f"El período se limitó a {MAX_DIAS_LISTADO} días."

    carga_texto = (request.GET.get("carga") or "").strip()
    carga_id = _leer_id(carga_texto)
    ruta_id = _leer_id(request.GET.get("ruta"))
    conductor_id = _leer_id(request.GET.get("conductor"))
    estado = request.GET.get("estado", "")
    if estado not in dict(Carga.ESTADO_CHOICES):
        estado = ""

    cargas, resumen, recortado = listado_cargas(
        desde, hasta, carga_id, ruta_id, conductor_id, estado
    )

    contexto = {
        "desde": desde,
        "hasta": hasta,
        "carga_id": carga_texto,
        "ruta_id": ruta_id,
        "conductor_id": conductor_id,
        "estado": estado,
        "cargas": cargas,
        "resumen": resumen,
        "recortado": recortado,
        "max_cargas": MAX_CARGAS_LISTADO,
        "aviso": aviso,
        "rutas": Ruta.objects.order_by("nombre"),
        "conductores": Vendedor.objects.filter(tipo="REPARTIDOR").order_by("nombre"),
        "estados_carga": Carga.ESTADO_CHOICES,
        "enlaces_admin": request.user.is_staff,
        "pestana": "cargas",
    }
    return render(request, "core/tablero/cargas.html", contexto)
