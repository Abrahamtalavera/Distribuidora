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
from django.utils import timezone
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


# --- Cambio #36: consulta de cargas (reemplaza la recaudación por ruta, #33) --


def _filtros_cargas(request):
    from datetime import date

    from core.consulta_cargas import MAX_DIAS_LISTADO
    from core.consulta_cargas import periodo_por_defecto as periodo_cargas

    desde_def, hasta_def = periodo_cargas()

    def fecha(texto, defecto):
        try:
            return date.fromisoformat((texto or "").strip())
        except ValueError:
            return defecto

    desde = fecha(request.GET.get("desde"), desde_def)
    hasta = fecha(request.GET.get("hasta"), hasta_def)
    if desde > hasta:
        desde, hasta = hasta, desde
    aviso = ""
    if (hasta - desde).days >= MAX_DIAS_LISTADO:
        desde = hasta.fromordinal(hasta.toordinal() - MAX_DIAS_LISTADO + 1)
        aviso = f"El período se limitó a {MAX_DIAS_LISTADO} días."
    rutas = [int(r) for r in request.GET.getlist("ruta") if r.strip().isdigit()]
    estados = request.GET.getlist("estado")
    return desde, hasta, rutas, estados, aviso


@cartera_required
def cartera_cargas_view(request):
    from core.cartera_cargas import consulta
    from core.consulta_cargas import MAX_CARGAS_LISTADO
    from core.models import Carga, Ruta

    desde, hasta, rutas, estados, aviso = _filtros_cargas(request)
    datos = consulta(desde, hasta, rutas, estados)
    return render(
        request,
        "core/cartera/cargas.html",
        {
            "pestana": "cargas",
            "desde": desde,
            "hasta": hasta,
            "aviso": aviso,
            "rutas": Ruta.objects.order_by("nombre"),
            "estados_opciones": Carga.ESTADO_CHOICES,
            "d": datos,
            "max_cargas": MAX_CARGAS_LISTADO,
            "consulta": request.GET.urlencode(),
        },
    )


@cartera_required
def cartera_cargas_excel_view(request):
    from django.http import HttpResponse

    from core.cartera_cargas import consulta, excel
    from core.models import Ruta

    desde, hasta, rutas, estados, _ = _filtros_cargas(request)
    datos = consulta(desde, hasta, rutas, estados)
    nombres = list(Ruta.objects.filter(pk__in=rutas).order_by("nombre").values_list("nombre", flat=True))
    titulo = (
        f"Cargas {', '.join(nombres) if nombres else 'todas las rutas'} · "
        f"fecha planeada {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}"
    )
    respuesta = HttpResponse(
        excel(datos, titulo),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    respuesta["Content-Disposition"] = (
        f'attachment; filename="cargas_cartera_{desde:%Y%m%d}_{hasta:%Y%m%d}.xlsx"'
    )
    return respuesta


def cartera_recaudacion_view(request):
    """La "Recaudación por ruta" (Cambio #33) se reemplazó por la consulta de cargas."""
    return redirect("cartera_cargas")


# --- Cambio #37: hoja imprimible de una devolución ------------------------------


def devolucion_imprimir_view(request, tipo, objeto_id):
    """
    Hoja "DEVOLUCIONES – Bodega de Producto Terminado". La pueden abrir
    cartera (desde la ficha de revisión) y bodega (desde la recepción), solo
    cuando bodega ya recibió la devolución.
    """
    from core.bodega import puede_recibir
    from core.impresion_devolucion import ImpresionError, hoja

    if not request.user.is_authenticated:
        return redirect(f"{reverse('cartera_acceso')}?next={request.get_full_path()}")
    if not (puede_revisar(request.user) or puede_recibir(request.user)):
        return render(request, "core/cartera/sin_permiso.html", status=403)
    try:
        datos = hoja(tipo, objeto_id)
    except ImpresionError as exc:
        return render(request, "core/devoluciones/no_imprimible.html", {"mensaje": str(exc)}, status=404)
    return render(
        request,
        "core/devoluciones/imprimir.html",
        {"h": datos, "ahora": timezone.now(), "fisico": (request.GET.get("fisico") or "").strip()[:30]},
    )
