from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render

from core.forms import ImportarFacturasForm
from core.importador import ImportacionError, importar_excel


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
