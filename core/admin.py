from django.contrib import admin
from django.urls import path
from django.shortcuts import redirect

from core import models
from core.views import importar_facturas_view


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
        "modalidad_pago",
        "total",
        "estado_entrega",
        "estado_factura",
    )
    list_filter = ("estado_entrega", "estado_factura", "modalidad_pago", "fecha_emision")
    search_fields = ("numero_factura", "cliente__nombre", "cliente__codigo")
    inlines = [FacturaDetalleInline]
    change_list_template = "admin/core/factura/change_list.html"

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
