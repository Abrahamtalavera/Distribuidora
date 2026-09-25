from collections import OrderedDict
from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render

from core.forms import ImportarFacturasForm
from core.importador import ImportacionError, importar_excel
from core.models import Carga


def _moneda(valor):
    """
    Formatea un Decimal como moneda con separador de miles (coma) y punto
    decimal, sin importar el LANGUAGE_CODE activo (Cambio #8: el filtro
    |floatformat es sensible al idioma y con LANGUAGE_CODE="es" mostraba
    "4850,00" en vez de "4,850.00").
    """
    return f"{valor:,.2f}"


@staff_member_required
def importar_facturas_view(request):
    resumen = None
    if request.method == "POST":
        form = ImportarFacturasForm(request.POST, request.FILES)
        if form.is_valid():
            archivo = form.cleaned_data["archivo"]
            try:
                resumen = importar_excel(archivo, nombre_archivo=archivo.name)
                messages.success(
                    request,
                    f"Importación completa: {resumen['facturas_creadas']} facturas nuevas, "
                    f"{resumen['facturas_omitidas_existentes']} ya existían y se omitieron.",
                )
            except ImportacionError as exc:
                messages.error(request, str(exc))
    else:
        form = ImportarFacturasForm()

    return render(
        request,
        "core/importar_facturas.html",
        {"form": form, "resumen": resumen},
    )


@staff_member_required
def imprimir_carga_view(request, pk):
    """
    Reporte imprimible de una Carga (Cambio #8): encabezado de la carga,
    lista de facturas incluidas (sin detalle de productos) y un resumen de
    totales con desglose por modalidad de pago (contado / crédito / etc.).
    """
    carga = get_object_or_404(Carga, pk=pk)
    facturas = list(
        carga.facturas.select_related("cliente", "modalidad_pago").order_by("numero_factura")
    )

    monto_total = Decimal("0")
    resumen_modalidades = OrderedDict()
    for factura in facturas:
        monto_total += factura.total
        factura.total_fmt = _moneda(factura.total)
        nombre_modalidad = (
            factura.modalidad_pago.nombre if factura.modalidad_pago else "Sin modalidad"
        )
        datos = resumen_modalidades.setdefault(
            nombre_modalidad, {"cantidad": 0, "monto": Decimal("0")}
        )
        datos["cantidad"] += 1
        datos["monto"] += factura.total

    for datos in resumen_modalidades.values():
        datos["monto_fmt"] = _moneda(datos["monto"])

    return render(
        request,
        "core/imprimir_carga.html",
        {
            "carga": carga,
            "facturas": facturas,
            "total_facturas": len(facturas),
            "monto_total": monto_total,
            "monto_total_fmt": _moneda(monto_total),
            "resumen_modalidades": resumen_modalidades,
        },
    )
