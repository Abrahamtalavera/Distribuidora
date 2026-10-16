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


# --- Cambio #22: corrección de horas por oficina -----------------------------


def _hora_local(valor):
    """datetime → texto para un <input type="datetime-local">."""
    from django.utils import timezone

    return timezone.localtime(valor).strftime("%Y-%m-%dT%H:%M") if valor else ""


def _leer_hora_local(texto):
    from datetime import datetime

    from django.utils import timezone

    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        return timezone.make_aware(datetime.strptime(texto[:16], "%Y-%m-%dT%H:%M"))
    except ValueError:
        return None


@require_http_methods(["GET", "POST"])
def corregir_horas_view(request, pk):
    from core.models import ParadaCliente
    from core.tiempos import CorreccionError, corregir_horas

    carga = get_object_or_404(Carga.objects.select_related("ruta", "conductor"), pk=pk)
    paradas = list(ParadaCliente.objects.filter(carga=carga).select_related("cliente"))
    entregas = list(
        carga.entregas.filter(estado="CONFIRMADA")
        .select_related("factura__cliente")
        .order_by("fecha_entrega", "id")
    )
    for p in paradas:
        p.valor = _hora_local(p.llegada_en)
    for e in entregas:
        e.valor = _hora_local(e.fecha_entrega)

    error = None
    motivo = ""
    if request.method == "POST":
        motivo = request.POST.get("motivo", "").strip()

        def cambio(nombre, original):
            texto = request.POST.get(nombre, "")
            if texto.strip() == original:
                return None
            return _leer_hora_local(texto)

        try:
            cambiadas = corregir_horas(
                carga,
                usuario=request.user,
                motivo=motivo,
                salida=cambio("salida", _hora_local(carga.salio_a_ruta_en)),
                fin=cambio("fin", _hora_local(carga.ruta_terminada_en)),
                llegadas={
                    p.id: v for p in paradas if (v := cambio(f"llegada_{p.id}", p.valor)) is not None
                },
                entregas={
                    e.id: v for e in entregas if (v := cambio(f"entrega_{e.id}", e.valor)) is not None
                },
            )
        except CorreccionError as exc:
            error = str(exc)
        else:
            if cambiadas:
                messages.success(
                    request, f"Se corrigi{'eron' if cambiadas > 1 else 'ó'} {cambiadas} hora{'s' if cambiadas > 1 else ''}."
                )
            else:
                messages.info(request, "No cambiaste ninguna hora.")
            return redirect("admin:core_carga_cierre", carga.pk)

    return render(
        request,
        "core/corregir_horas.html",
        {
            "carga": carga,
            "salida": request.POST.get("salida") if error else _hora_local(carga.salio_a_ruta_en),
            "fin": request.POST.get("fin") if error else _hora_local(carga.ruta_terminada_en),
            "paradas": paradas,
            "entregas": entregas,
            "error": error,
            "motivo": motivo,
        },
    )
