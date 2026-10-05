"""
Cambio #14: vistas del cierre de Carga.

- entregas_terminar_view: paso 1, el repartidor (o oficina) marca "Terminé
  mi ruta" desde la pantalla de entregas. Usa el mismo acceso por código de
  carga + PIN que el resto del módulo de entregas.
- cierre_carga_view: paso 2, solo personal de oficina (sesión de
  administrador): revisar, recibir dinero y devoluciones, cerrar, devolver a
  ruta o reabrir.
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.cierre import (
    FORMAS_DINERO,
    CierreError,
    cerrar_carga,
    devolver_a_ruta,
    reabrir_carga,
    resumen_cierre,
    terminar_ruta,
)
from core.models import Carga
from core.views_entregas import _carga_autorizada


def _decimal_o_none(texto):
    texto = (texto or "").strip().replace(",", "")
    if texto == "":
        return None
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


@require_http_methods(["GET", "POST"])
def entregas_terminar_view(request, carga_id):
    carga = _carga_autorizada(request, carga_id)
    if carga is None:
        return redirect("entregas_acceso")
    if carga.bloqueada_para_repartidor:
        return redirect("entregas_lista", carga_id=carga.id)

    error = None
    km_escrito = ""
    observaciones = ""
    if request.method == "POST":
        km_escrito = request.POST.get("km_final", "").strip()
        observaciones = request.POST.get("observaciones", "").strip()
        try:
            terminar_ruta(
                carga, km_final=_decimal_o_none(km_escrito), observaciones=observaciones
            )
        except CierreError as exc:
            error = str(exc)
        else:
            # La lista ya muestra el aviso de "Ruta terminada"; no hace falta
            # además un mensaje suelto.
            return redirect("entregas_lista", carga_id=carga.id)

    return render(
        request,
        "core/entregas/terminar.html",
        {
            "carga": carga,
            "r": resumen_cierre(carga),
            "error": error,
            "km_escrito": km_escrito,
            "observaciones": observaciones,
        },
    )


@staff_member_required
@require_http_methods(["GET", "POST"])
def cierre_carga_view(request, pk):
    carga = get_object_or_404(
        Carga.objects.select_related("ruta", "conductor", "vehiculo", "cerrada_por"), pk=pk
    )
    escrito = {}  # lo que se escribió en un intento de cierre que falló

    if request.method == "POST":
        accion = request.POST.get("accion")
        try:
            if accion == "cerrar":
                escrito = {
                    "recibido": {
                        codigo: request.POST.get(f"recibido_{codigo}", "").strip()
                        for codigo, _ in FORMAS_DINERO
                    },
                    "observaciones": request.POST.get("observaciones_cierre", ""),
                    "km_final": request.POST.get("km_final", "").strip(),
                }
                cerrar_carga(
                    carga,
                    usuario=request.user,
                    recibido_por_forma={
                        codigo: _decimal_o_none(valor)
                        for codigo, valor in escrito["recibido"].items()
                    },
                    observaciones=escrito["observaciones"],
                    km_final=_decimal_o_none(escrito["km_final"]),
                )
                messages.success(request, f"{carga.codigo_reporte} cerrada.")
            elif accion == "devolver":
                devolver_a_ruta(carga)
                messages.success(
                    request,
                    f"{carga.codigo_reporte} devuelta a ruta: el repartidor puede "
                    "volver a registrar entregas.",
                )
            elif accion == "reabrir":
                reabrir_carga(carga)
                messages.success(request, f"{carga.codigo_reporte} reabierta.")
            else:
                raise CierreError("Acción no reconocida.")
        except CierreError as exc:
            messages.error(request, str(exc))
        else:
            return redirect("admin:core_carga_cierre", pk=carga.pk)

    r = resumen_cierre(carga)
    # Valor que se muestra en cada casilla "Recibido": lo escrito en un
    # intento fallido, o lo guardado, o (si nunca se ha cerrado) lo que dice
    # el sistema para todo menos el efectivo, que se deja vacío a propósito
    # para que oficina cuente el dinero y escriba la cifra.
    for fila in r["cuadre"]:
        if "recibido" in escrito:
            fila["valor_input"] = escrito["recibido"].get(fila["codigo"], "")
        elif fila["recibido"] is not None:
            fila["valor_input"] = f"{fila['recibido']:.2f}"
        elif fila["codigo"] == "EFECTIVO" and fila["segun_sistema"] > 0:
            fila["valor_input"] = ""
        else:
            fila["valor_input"] = f"{fila['segun_sistema']:.2f}"
    return render(
        request,
        "core/cierre_carga.html",
        {
            "carga": carga,
            "r": r,
            "obs_escrita": escrito.get("observaciones", carga.observaciones_cierre),
            "km_escrito": escrito.get("km_final"),
        },
    )
