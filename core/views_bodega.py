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
    devoluciones_de_la_carga,
    puede_recibir,
    registrar_recepcion,
    resumen_recepcion,
)
# Cambio #27: devoluciones sin factura, recibidas en la misma pantalla.
from core.devoluciones_sf import (
    RecepcionSinFacturaError,
    cargas_para_bodega_combinado,
    detalles_sin_factura_de_la_carga,
    registrar_recepcion_sin_factura,
    resumen_recepcion_sin_factura,
)
from core.entregas import leer_fecha_vencimiento, limpiar_lote
from core.models import Carga


def _decimal_o_none(texto):
    texto = (texto or "").strip().replace(",", "")
    if texto == "":
        return None
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def _lotes_escritos(post, devoluciones, sufijo=""):
    """
    Cambio #28: lote y vencimiento que bodega escribió en cada línea. Solo
    se toman las líneas que traen las casillas (si no vienen, no se tocan).
    Devuelve (para guardar, tal como se escribió).
    """
    para_guardar, escrito = {}, {}
    for d in devoluciones:
        clave_lote, clave_vence = f"lote{sufijo}_{d.id}", f"vence{sufijo}_{d.id}"
        if clave_lote not in post and clave_vence not in post:
            continue
        lote, vence = post.get(clave_lote, ""), post.get(clave_vence, "").strip()
        escrito[d.id] = (lote, vence)
        para_guardar[d.id] = (limpiar_lote(lote), leer_fecha_vencimiento(vence))
    return para_guardar, escrito


def _poner_lote_en_pantalla(devoluciones, escrito):
    for d in devoluciones:
        if escrito is not None and d.id in escrito.get("lotes", {}):
            d.valor_lote, d.valor_vence = escrito["lotes"][d.id]
        else:
            d.valor_lote = d.lote
            d.valor_vence = d.fecha_vencimiento.isoformat() if d.fecha_vencimiento else ""


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
    # Cambio #27: incluye también las cargas que solo tienen devoluciones
    # sin factura (y ninguna con factura).
    pendientes, recibidas = cargas_para_bodega_combinado()
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
    # Cambio #27: devoluciones sin factura, su propio formulario en la misma
    # página (no se mezclan: cada una se confirma por separado).
    escrito_sf = None
    corrigiendo_sf = request.GET.get("corregir_sf") == "1"

    if request.method == "POST" and request.POST.get("seccion") == "sf":
        detalles_sf = detalles_sin_factura_de_la_carga(carga)
        escrito_sf = {
            "cantidades": {
                d.id: (
                    request.POST.get(f"invsf_{d.id}", "").strip(),
                    request.POST.get(f"mersf_{d.id}", "").strip(),
                )
                for d in detalles_sf
            },
            "observaciones": request.POST.get("observaciones_bodega_sf", ""),
        }
        lotes_sf, escrito_sf["lotes"] = _lotes_escritos(request.POST, detalles_sf, "sf")
        try:
            registrar_recepcion_sin_factura(
                carga,
                usuario=request.user,
                cantidades={
                    i: (_decimal_o_none(inv), _decimal_o_none(mer))
                    for i, (inv, mer) in escrito_sf["cantidades"].items()
                },
                observaciones=escrito_sf["observaciones"],
                lotes=lotes_sf,
            )
        except RecepcionSinFacturaError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request, "Recepción de devoluciones sin factura confirmada."
            )
            return redirect("bodega_recepcion", carga_id=carga.id)
    elif request.method == "POST":
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
        lotes_cf, escrito["lotes"] = _lotes_escritos(request.POST, devoluciones)
        try:
            registrar_recepcion(
                carga,
                usuario=request.user,
                cantidades={
                    i: (_decimal_o_none(inv), _decimal_o_none(mer))
                    for i, (inv, mer) in escrito["cantidades"].items()
                },
                observaciones=escrito["observaciones"],
                lotes=lotes_cf,
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

    _poner_lote_en_pantalla(devoluciones, escrito)

    detalles_sf = detalles_sin_factura_de_la_carga(carga)
    recepcion_sf = resumen_recepcion_sin_factura(carga, detalles_sf)
    _poner_lote_en_pantalla(detalles_sf, escrito_sf)
    editando_sf = recepcion_sf["editable"] and (
        not recepcion_sf["confirmada"] or corrigiendo_sf or escrito_sf is not None
    )
    for d in detalles_sf:
        if escrito_sf is not None:
            d.valor_inv, d.valor_mer = escrito_sf["cantidades"].get(d.id, ("", ""))
        else:
            d.valor_inv = "" if d.recibido_inventario is None else _plano(d.recibido_inventario)
            d.valor_mer = "" if d.recibido_merma is None else _plano(d.recibido_merma)
    obs_escrita_sf = (
        escrito_sf["observaciones"]
        if escrito_sf is not None
        else (detalles_sf[0].devolucion.observaciones_bodega if detalles_sf else "")
    )

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
            "detalles_sf": detalles_sf,
            "recepcion_sf": recepcion_sf,
            "editando_sf": editando_sf,
            "obs_escrita_sf": obs_escrita_sf,
        },
    )


def _plano(valor):
    """Decimal → texto con punto decimal y sin ceros de más, para un <input>."""
    texto = f"{valor:.3f}".rstrip("0").rstrip(".")
    return texto or "0"
