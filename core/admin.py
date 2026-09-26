from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.views.main import ChangeList
from django.db.models import Q
from django.urls import path
from django.shortcuts import redirect, render

from core import models
from core.forms import AgruparEnCargaForm
from core.views import importar_facturas_view, imprimir_carga_view


class FacturaDetalleInline(admin.TabularInline):
    model = models.FacturaDetalle
    extra = 0
    readonly_fields = ("subtotal", "iva")


# Cambio #11: filtros como desplegable de selección múltiple en los
# encabezados de Sucursal, Municipio, Canal, Ruta y Carga (reemplazan el
# panel lateral para esos cinco campos). Cada uno se controla con un
# parámetro GET propio (por ejemplo "f_sucursal=Managua,León"), leído en
# FacturaAdmin.get_queryset. Los demás filtros (estado de entrega, estado de
# factura, modalidad de pago, fecha de emisión) siguen en el panel lateral,
# sin cambios.
FACTURA_FILTROS_TEXTO = {
    "sucursal": {"lookup": "cliente__sucursal", "label": "Sucursal"},
    "municipio": {"lookup": "cliente__municipio", "label": "Municipio"},
    "canal": {"lookup": "cliente__canal", "label": "Canal"},
}

FACTURA_FILTRO_PARAMS_GET = ("f_sucursal", "f_municipio", "f_canal", "f_ruta", "f_carga")

CARGA_SIN_ASIGNAR = "__none__"


class FacturaChangeList(ChangeList):
    """
    El admin de Django, por defecto, toma cualquier parámetro de la URL que
    no reconoce (que no sea de orden, búsqueda, paginación, etc.) e intenta
    usarlo directamente como un filtro de campo del modelo — y si el nombre
    no corresponde a ningún campo real, corta la petición con un redirect
    "silencioso" que borra esos parámetros. Nuestros parámetros "f_sucursal",
    "f_ruta", etc. no son campos reales (los interpretamos nosotros mismos en
    FacturaAdmin.get_queryset), así que hay que decirle al admin que los
    ignore por completo en vez de intentar validarlos como si fueran campos.
    """

    def get_filters_params(self, params=None):
        lookup_params = super().get_filters_params(params)
        for clave in FACTURA_FILTRO_PARAMS_GET:
            lookup_params.pop(clave, None)
        return lookup_params


@admin.register(models.Factura)
class FacturaAdmin(admin.ModelAdmin):
    list_display = (
        "numero_factura",
        "cliente",
        "sucursal",
        "municipio",
        "canal",
        "ruta",
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
    )
    search_fields = ("numero_factura", "cliente__nombre", "cliente__codigo")
    inlines = [FacturaDetalleInline]
    change_list_template = "admin/core/factura/change_list.html"
    actions = ["agrupar_en_carga"]

    class Media:
        css = {"all": ("core/admin_dropdown_filters.css",)}
        js = ("core/admin_dropdown_filters.js",)

    def get_changelist(self, request, **kwargs):
        return FacturaChangeList

    @admin.display(description="Sucursal", ordering="cliente__sucursal")
    def sucursal(self, obj):
        return obj.cliente.sucursal

    @admin.display(description="Municipio", ordering="cliente__municipio")
    def municipio(self, obj):
        return obj.cliente.municipio

    @admin.display(description="Canal", ordering="cliente__canal")
    def canal(self, obj):
        return obj.cliente.canal

    @admin.display(description="Ruta", ordering="cliente__ruta__nombre")
    def ruta(self, obj):
        return obj.cliente.ruta

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        for clave, cfg in FACTURA_FILTROS_TEXTO.items():
            crudo = request.GET.get(f"f_{clave}")
            if not crudo:
                continue
            valores = [v for v in crudo.split(",") if v]
            if valores:
                qs = qs.filter(**{f"{cfg['lookup']}__in": valores})

        crudo_ruta = request.GET.get("f_ruta")
        if crudo_ruta:
            ids_ruta = [int(v) for v in crudo_ruta.split(",") if v.isdigit()]
            if ids_ruta:
                qs = qs.filter(cliente__ruta_id__in=ids_ruta)

        crudo_carga = request.GET.get("f_carga")
        if crudo_carga:
            valores_carga = [v for v in crudo_carga.split(",") if v]
            condicion = Q()
            hay_condicion = False
            if CARGA_SIN_ASIGNAR in valores_carga:
                condicion |= Q(carga__isnull=True)
                hay_condicion = True
            ids_carga = [int(v) for v in valores_carga if v.isdigit()]
            if ids_carga:
                condicion |= Q(carga_id__in=ids_carga)
                hay_condicion = True
            if hay_condicion:
                qs = qs.filter(condicion)

        return qs

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["filtro_columnas_json"] = self._opciones_filtros_dropdown()
        return super().changelist_view(request, extra_context=extra_context)

    def _opciones_filtros_dropdown(self):
        def valores_distintos(campo):
            return list(
                models.Cliente.objects.exclude(**{campo: ""})
                .order_by(campo)
                .values_list(campo, flat=True)
                .distinct()
            )

        rutas = [
            {"value": str(r.id), "label": r.nombre}
            for r in models.Ruta.objects.order_by("nombre")
        ]
        cargas = [{"value": CARGA_SIN_ASIGNAR, "label": "Sin carga asignada"}] + [
            {"value": str(c.id), "label": f"CARGA-{c.id:04d}"}
            for c in models.Carga.objects.order_by("-id")[:500]
        ]

        return {
            "sucursal": {
                "label": "Sucursal",
                "options": [{"value": v, "label": v} for v in valores_distintos("sucursal")],
            },
            "municipio": {
                "label": "Municipio",
                "options": [{"value": v, "label": v} for v in valores_distintos("municipio")],
            },
            "canal": {
                "label": "Canal",
                "options": [{"value": v, "label": v} for v in valores_distintos("canal")],
            },
            "ruta": {"label": "Ruta", "options": rutas},
            "carga": {"label": "Carga", "options": cargas},
        }

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
        "codigo_acceso",
    )
    list_filter = ("estado", "fecha_planeada", "ruta", "conductor")
    readonly_fields = ("codigo_acceso",)
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


@admin.register(models.AreaResponsable)
class AreaResponsableAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activo")
    search_fields = ("nombre",)


@admin.register(models.MotivoDevolucion)
class MotivoDevolucionAdmin(admin.ModelAdmin):
    list_display = ("nombre", "area_responsable", "activo")
    list_filter = ("area_responsable", "activo")
    search_fields = ("nombre",)


class EntregaDetalleInline(admin.TabularInline):
    model = models.EntregaDetalle
    extra = 0
    fields = (
        "factura_detalle",
        "cantidad_entregada",
        "cantidad_devuelta",
        "motivo_devolucion",
    )
    # "factura_detalle" es un FK a cualquier línea de cualquier factura; sin
    # esto, el admin intenta dibujar un <select> con las ~32,000 líneas que
    # ya existen en producción y la pantalla se queda colgada cargando.
    raw_id_fields = ("factura_detalle",)


@admin.register(models.Entrega)
class EntregaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "factura",
        "fecha_entrega",
        "tipo_entrega",
        "repartidor",
        "motivo_devolucion_general",
        "estado",
    )
    list_filter = ("tipo_entrega", "estado", "motivo_devolucion_general__area_responsable")
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
