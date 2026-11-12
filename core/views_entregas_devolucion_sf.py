"""
Cambio #27: pantalla del repartidor para capturar lo que recolectó de una
devolución sin factura asignada a su Carga.

Vive fuera del admin, igual que core.views_entregas: el repartidor entra
por código de carga + PIN, no con una cuenta de usuario de Django. Reusa
_carga_autorizada() de ese módulo para no duplicar la lógica de acceso.
"""

import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.devoluciones_sf import SolicitudError, devolucion_para_repartidor, registrar_captura
from core.models import MotivoDevolucion, Producto
from core.views_entregas import _carga_autorizada


def _cantidad_plana(valor):
    texto = f"{valor:.3f}".rstrip("0").rstrip(".")
    return texto if texto else "0"


@require_http_methods(["GET", "POST"])
def entregas_devolucion_sf_view(request, carga_id, devolucion_id):
    carga = _carga_autorizada(request, carga_id)
    if carga is None:
        return redirect("entregas_acceso")

    devolucion = devolucion_para_repartidor(carga, devolucion_id)
    if devolucion is None:
        messages.error(request, "Esta devolución no está disponible (ya fue recibida o no existe).")
        return redirect("entregas_lista", carga_id=carga.id)

    es_staff = request.user.is_authenticated and request.user.is_staff
    if carga.esta_cerrada:
        messages.error(
            request, "Esta carga ya está cerrada. Oficina debe reabrirla para corregir algo."
        )
        return redirect("entregas_lista", carga_id=carga.id)
    if carga.bloqueada_para_repartidor and not es_staff:
        messages.error(
            request,
            "Ya marcaste la ruta como terminada. Si falta corregir algo, pídelo en oficina.",
        )
        return redirect("entregas_lista", carga_id=carga.id)

    motivos = MotivoDevolucion.objects.filter(activo=True).order_by("nombre")

    if request.method == "POST":
        productos_ids = request.POST.getlist("producto_id")
        cantidades_crudas = request.POST.getlist("cantidad")
        motivos_crudos = request.POST.getlist("motivo_id")
        # Cambio #28: lote y vencimiento por línea; el mismo producto puede
        # venir en varias líneas (otro motivo u otro lote).
        lotes = request.POST.getlist("lote")
        vencimientos = request.POST.getlist("fecha_vencimiento")
        lotes += [""] * (len(productos_ids) - len(lotes))
        vencimientos += [""] * (len(productos_ids) - len(vencimientos))
        lineas_capturadas = []
        for pid, crudo, motivo_crudo, lote, vence in zip(
            productos_ids, cantidades_crudas, motivos_crudos, lotes, vencimientos
        ):
            if not (pid or "").strip().isdigit():
                continue
            crudo = (crudo or "").strip()
            try:
                cantidad = Decimal(crudo) if crudo else Decimal("0")
            except InvalidOperation:
                cantidad = Decimal("0")
            if cantidad > 0:
                motivo_crudo = (motivo_crudo or "").strip()
                lineas_capturadas.append(
                    {
                        "producto_id": int(pid),
                        "cantidad": cantidad,
                        "motivo_id": int(motivo_crudo) if motivo_crudo.isdigit() else None,
                        "lote": lote,
                        "fecha_vencimiento": vence,
                    }
                )

        try:
            registrar_captura(
                devolucion,
                lineas_capturadas=lineas_capturadas,
                observaciones=request.POST.get("observaciones", "").strip(),
            )
        except SolicitudError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request, f"Recolección de {devolucion.codigo_reporte} guardada."
            )
        return redirect("entregas_lista", carga_id=carga.id)

    lineas_previas_json = "[]"
    if devolucion.estado == "RECOLECTADA":
        lineas_previas_json = json.dumps(
            [
                {
                    "producto": {
                        "id": linea.producto_id,
                        "codigo": linea.producto.codigo,
                        "nombre": linea.producto.nombre,
                    },
                    "cantidad": _cantidad_plana(linea.cantidad_recolectada),
                    "motivo_id": linea.motivo_id,
                    "lote": linea.lote,
                    "fecha_vencimiento": (
                        linea.fecha_vencimiento.isoformat() if linea.fecha_vencimiento else ""
                    ),
                }
                for linea in devolucion.detalles.select_related("producto").order_by("id")
            ]
        )
        # Va dentro de un <script>: que un texto escrito (como el lote) no
        # pueda cerrar la etiqueta.
        lineas_previas_json = (
            lineas_previas_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        )

    return render(
        request,
        "core/entregas/devolucion_sf.html",
        {
            "carga": carga,
            "devolucion": devolucion,
            "lineas_previas_json": lineas_previas_json,
            "motivos": motivos,
        },
    )


def entregas_devolucion_sf_buscar_producto_view(request, carga_id):
    """Pequeño endpoint JSON: busca productos por código o descripción."""
    carga = _carga_autorizada(request, carga_id)
    if carga is None:
        return JsonResponse({"resultados": []}, status=403)
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"resultados": []})
    productos = Producto.objects.filter(activo=True).filter(
        Q(codigo__icontains=q) | Q(nombre__icontains=q)
    ).order_by("nombre")[:15]
    return JsonResponse(
        {
            "resultados": [
                {"id": p.id, "codigo": p.codigo, "nombre": p.nombre} for p in productos
            ]
        }
    )
