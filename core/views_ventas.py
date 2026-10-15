"""
Cambio #21: pantallas de consulta del equipo de ventas.

Entran los usuarios con el permiso "Puede usar la consulta de ventas"
(grupo "Ventas"; también Administración, Supervisores y superusuarios). No
necesitan ser "staff". Todo es de solo lectura.
"""

from datetime import date
from functools import wraps

from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.consulta import (
    FILTROS_ESTADO,
    MAX_DIAS_LISTADO,
    MAX_FILAS_LISTADO,
    buscar_facturas,
    detalle_factura,
    excel_listado,
    listado,
    obtener_factura,
    periodo_por_defecto,
    puede_consultar,
)
from core.models import Ruta, Vendedor
from core.tablero import puede_ver_tablero


def ventas_required(vista):
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('ventas_acceso')}?next={request.get_full_path()}")
        if not puede_consultar(request.user):
            return render(request, "core/ventas/sin_permiso.html", status=403)
        return vista(request, *args, **kwargs)

    return envoltura


class VentasLoginView(LoginView):
    template_name = "core/ventas/acceso.html"

    def get_success_url(self):
        destino = self.request.POST.get("next") or self.request.GET.get("next") or ""
        if destino and url_has_allowed_host_and_scheme(
            destino, allowed_hosts={self.request.get_host()}
        ):
            return destino
        return reverse("ventas_buscar")


@require_POST
def ventas_salir_view(request):
    logout(request)
    return redirect("ventas_acceso")


def _contexto_base(request, pestana):
    return {
        "pestana": pestana,
        "enlaces_admin": request.user.is_staff,
        "ve_tablero": puede_ver_tablero(request.user),
    }


@ventas_required
def ventas_buscar_view(request):
    texto = (request.GET.get("q") or "").strip()
    exacta, resultados = buscar_facturas(texto) if texto else (None, [])
    if exacta:
        return redirect("ventas_factura", factura_id=exacta.pk)
    contexto = _contexto_base(request, "buscar")
    contexto.update({"q": texto, "resultados": resultados, "buscado": bool(texto)})
    return render(request, "core/ventas/buscar.html", contexto)


@ventas_required
def ventas_factura_view(request, factura_id):
    factura = obtener_factura(factura_id)
    if factura is None:
        raise Http404("Factura no encontrada")
    contexto = _contexto_base(request, "buscar")
    contexto.update(detalle_factura(factura))
    contexto["q"] = factura.numero_factura
    contexto["volver"] = request.GET.get("volver", "")
    return render(request, "core/ventas/factura.html", contexto)


def _leer_fecha(texto, por_defecto):
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError:
        return por_defecto


def _leer_id(texto):
    texto = (texto or "").strip()
    return int(texto) if texto.isdigit() else None


def _filtros_listado(request):
    desde_def, hasta_def = periodo_por_defecto()
    desde = _leer_fecha(request.GET.get("desde"), desde_def)
    hasta = _leer_fecha(request.GET.get("hasta"), hasta_def)
    if desde > hasta:
        desde, hasta = hasta, desde
    aviso = ""
    if (hasta - desde).days >= MAX_DIAS_LISTADO:
        desde = hasta.fromordinal(hasta.toordinal() - MAX_DIAS_LISTADO + 1)
        aviso = f"El período se limitó a {MAX_DIAS_LISTADO} días."
    estado = request.GET.get("estado", "")
    if estado not in FILTROS_ESTADO:
        estado = ""
    return {
        "desde": desde,
        "hasta": hasta,
        "ruta_id": _leer_id(request.GET.get("ruta")),
        "vendedor_id": _leer_id(request.GET.get("vendedor")),
        "estado": estado,
        "aviso": aviso,
    }


@ventas_required
def ventas_ruta_view(request):
    filtros = _filtros_listado(request)
    facturas, resumen, recortado = listado(
        filtros["desde"], filtros["hasta"], filtros["ruta_id"],
        filtros["vendedor_id"], filtros["estado"],
    )
    contexto = _contexto_base(request, "ruta")
    contexto.update(filtros)
    contexto.update(
        {
            "facturas": facturas,
            "resumen": resumen,
            "recortado": recortado,
            "max_filas": MAX_FILAS_LISTADO,
            "rutas": Ruta.objects.order_by("nombre"),
            "vendedores": Vendedor.objects.filter(tipo="VENDEDOR").order_by("nombre"),
            "estados": [(clave, nombre) for clave, (nombre, _) in FILTROS_ESTADO.items()],
            "consulta": request.GET.urlencode(),
        }
    )
    return render(request, "core/ventas/ruta.html", contexto)


@ventas_required
def ventas_ruta_excel_view(request):
    filtros = _filtros_listado(request)
    facturas, _, _ = listado(
        filtros["desde"], filtros["hasta"], filtros["ruta_id"],
        filtros["vendedor_id"], filtros["estado"],
    )
    ruta = Ruta.objects.filter(pk=filtros["ruta_id"]).first() if filtros["ruta_id"] else None
    titulo = (
        f"Facturas {ruta.nombre if ruta else 'todas las rutas'} · "
        f"{filtros['desde']:%d/%m/%Y} al {filtros['hasta']:%d/%m/%Y}"
    )
    contenido = excel_listado(facturas, titulo)
    nombre = f"facturas_{filtros['desde']:%Y%m%d}_{filtros['hasta']:%Y%m%d}.xlsx"
    respuesta = HttpResponse(
        contenido,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre}"'
    return respuesta
