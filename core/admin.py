from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.urls import path
from django.shortcuts import redirect, render

from core import models
from core.forms import AgruparEnCargaForm
from core.views import importar_facturas_view, imprimir_carga_view


class FacturaDetalleInline(admin.TabularInline):
    model = models.FacturaDetalle
    extra = 0
    readonly_fields = ("subtotal", "iva")


@admin.register(models.Factura)
class FacturaAdmin(admin.ModelAdmin):
    list_display = (
        "numero_factura",
        "cliente",
        "fecha_emision",
        "fecha_sugerida_entrega",
        "modalidad_pago",
        "total",
        "estado_entrega",
        "estado_factura",
        "carga",
    )
    list_editable = ("fecha_sugerida_entrega",)
    list_filter = (
        "estado_entrega",
        "estado_factura",
        "modalidad_pago",
        "fecha_emision",
        "carga",
        "cliente__ruta",
    )
    search_fields = ("numero_factura", "cliente__nombre", "cliente__codigo")
    inlines = [FacturaDetalleInline]
    change_list_template = "admin/core/factura/change_list.html"
    actions = ["agrupar_en_carga"]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "importar-facturas/",
                self.admin_site.admin_view(importar_facturas_view),
                name="core_factura_importar",
            ),
        ]
        return custom + urls

    def agrupar_en_carga(self, request, queryset):
        """
        Acción de admin (Cambio #7): agrupa las facturas seleccionadas en una
        Carga nueva. Muestra primero una pantalla intermedia para capturar los
        datos de la salida (ruta, conductor, unidad, km inicial, etc.).
        """
        if "apply" in request.POST:
            form = AgruparEnCargaForm(request.POST)
            if form.is_valid():
                carga = models.Carga.objects.create(
                    fecha_planeada=form.cleaned_data["fecha_planeada"],
                    ruta=form.cleaned_data["ruta"],
                    conductor=form.cleaned_data["conductor"],
                    vehiculo=form.cleaned_data["vehiculo"],
                    km_inicial=form.cleaned_data["km_inicial"],
                    observaciones=form.cleaned_data["observaciones"],
                )
                actualizadas = queryset.update(carga=carga)
                self.message_user(
                    request,
                    f"Carga #{carga.id} creada con {actualizadas} factura(s) asignada(s).",
                    messages.SUCCESS,
                )
                return None
        else:
            form = AgruparEnCargaForm()

        return render(
            request,
            "admin/core/factura/agrupar_en_carga.html",
            {
                "facturas": queryset,
                "form": form,
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
                "opts": self.model._meta,
                "title": "Agrupar facturas en una nueva Carga",
            },
        )

    agrupar_en_carga.short_description = "Agrupar seleccionadas en una nueva Carga"


@admin.register(models.Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "municipio", "canal", "territorio", "ruta", "activo")
    list_filter = ("municipio", "canal", "territorio", "activo")
    search_fields = ("codigo", "nombre")


@admin.register(models.Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "familia", "precio_unitario", "costo", "activo")
    list_filter = ("familia", "activo")
    search_fields = ("codigo", "nombre")


@admin.register(models.Vendedor)
class VendedorAdmin(admin.ModelAdmin):
    list_display = ("nombre", "tipo", "telefono", "activo")
    list_filter = ("tipo", "activo")
    search_fields = ("nombre",)


@admin.register(models.Ruta)
class RutaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "zona", "activo")


@admin.register(models.ModalidadPago)
class ModalidadPagoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "activo")


@admin.register(models.Vehiculo)
class VehiculoAdmin(admin.ModelAdmin):
    list_display = ("placa", "descripcion", "activo")
    search_fields = ("placa",)


class FacturaEnCargaInline(admin.TabularInline):
    model = models.Factura
    fk_name = "carga"
    extra = 0
    fields = ("numero_factura", "cliente", "total", "estado_entrega")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(models.Carga)
class CargaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "fecha_planeada",
        "ruta",
        "conductor",
        "vehiculo",
        "estado",
        "total_facturas",
    )
    list_filter = ("estado", "fecha_planeada", "ruta", "conductor")
    inlines = [FacturaEnCargaInline]
    change_form_template = "admin/core/carga/change_form.html"

    @admin.display(description="Facturas")
    def total_facturas(self, obj):
        return obj.facturas.count()

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:pk>/imprimir/",
                self.admin_site.admin_view(imprimir_carga_view),
                name="core_carga_imprimir",
            ),
        ]
        return custom + urls


class EntregaDetalleInline(admin.TabularInline):
    model = models.EntregaDetalle
    extra = 0


@admin.register(models.Entrega)
class EntregaAdmin(admin.ModelAdmin):
    list_display = ("id", "factura", "fecha_entrega", "tipo_entrega", "repartidor", "estado")
    list_filter = ("tipo_entrega", "estado")
    inlines = [EntregaDetalleInline]


class PlanPagoInline(admin.TabularInline):
    model = models.PlanPago
    extra = 0


@admin.register(models.CarteraCobro)
class CarteraCobroAdmin(admin.ModelAdmin):
    list_display = ("factura", "modalidad_pago", "monto_total", "saldo_pendiente", "estado")
    list_filter = ("estado", "modalidad_pago")
    search_fields = ("factura__numero_factura",)
    inlines = [PlanPagoInline]


@admin.register(models.Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ("id", "cartera", "fecha_pago", "monto", "forma_pago", "cobrador")
    list_filter = ("forma_pago",)


class ConsignacionDetalleInline(admin.TabularInline):
    model = models.ConsignacionDetalle
    extra = 0


@admin.register(models.Consignacion)
class ConsignacionAdmin(admin.ModelAdmin):
    list_display = ("factura", "cliente", "fecha_entrega", "fecha_liquidacion", "estado")
    list_filter = ("estado",)
    inlines = [ConsignacionDetalleInline]
