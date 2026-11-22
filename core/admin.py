from decimal import Decimal

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.views.main import ChangeList
from django.db.models import Q
from django.urls import path
from django.shortcuts import redirect, render

from core import models
from core.forms import AgruparEnCargaForm
from core.paquetes import facturas_sin_paquetes_costo, leer_paquetes_costo
from core.devoluciones_sf import (
    AsignarDevolucionError,
    asignar_a_carga_existente as asignar_devolucion_a_carga_existente,
    crear_carga_con_devoluciones,
)
from core.views import (
    importar_facturas_view,
    importar_sucursales_view,
    imprimir_carga_view,
)
from core.views_cierre import cierre_carga_view, corregir_horas_view


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
#
# Cambio #25: "sucursal" ahora filtra por el nombre de la Sucursal real
# (catálogo), no por el texto suelto que quedó guardado en Cliente.sucursal.
FACTURA_FILTROS_TEXTO = {
    "sucursal": {"lookup": "sucursal__nombre", "label": "Sucursal"},
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
        "sucursal_entrega",
        "direccion",
        "municipio",
        "canal",
        "ruta",
        "fecha_emision",
        "fecha_sugerida_entrega",
        "modalidad_pago",
        "total",
        "estado_entrega",
        "cargada",
        "paquetes",
        "costo_flete",
        "estado_factura",
        "carga",
    )
    list_editable = ("fecha_sugerida_entrega",)
    readonly_fields = ("cargada_en", "cargada_por")
    list_filter = (
        "estado_entrega",
        "estado_factura",
        "modalidad_pago",
        "fecha_emision",
    )
    search_fields = ("numero_factura", "cliente__nombre", "cliente__codigo")
    autocomplete_fields = ("sucursal",)
    inlines = [FacturaDetalleInline]
    change_list_template = "admin/core/factura/change_list.html"
    actions = ["agrupar_en_carga", "agregar_a_carga_existente"]

    class Media:
        css = {"all": ("core/admin_dropdown_filters.css",)}
        js = ("core/admin_dropdown_filters.js",)

    def get_changelist(self, request, **kwargs):
        return FacturaChangeList

    @admin.display(description="Cargada", boolean=True, ordering="cargada_en")
    def cargada(self, obj):
        return obj.esta_cargada

    def save_model(self, request, obj, form, change):
        # Cambio #17: si oficina mueve la factura a otra carga (o la saca),
        # su mercadería hay que volver a cargarla.
        if change and "carga" in form.changed_data:
            obj.cargada_en = None
            obj.cargada_por = ""
            # Cambio #18: paquetes y costo son de la factura en su carga
            # anterior, salvo que se hayan escrito en este mismo cambio.
            if "paquetes" not in form.changed_data:
                obj.paquetes = None
            if "costo_flete" not in form.changed_data:
                obj.costo_flete = None
        super().save_model(request, obj, form, change)

    # Cambio #25: si la factura tiene una Sucursal real (catálogo) se usa esa;
    # si no (factura de antes de este cambio, o cliente sin sucursal todavía
    # en el catálogo), se cae al texto suelto que había en Cliente.sucursal.
    @admin.display(description="Sucursal", ordering="sucursal__nombre")
    def sucursal_entrega(self, obj):
        return obj.sucursal.nombre if obj.sucursal_id else obj.cliente.sucursal

    @admin.display(description="Dirección", ordering="sucursal__direccion")
    def direccion(self, obj):
        return obj.sucursal.direccion if obj.sucursal_id else ""

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

        sucursales = list(
            models.Sucursal.objects.order_by("nombre")
            .values_list("nombre", flat=True)
            .distinct()
        )

        return {
            "sucursal": {
                "label": "Sucursal",
                "options": [{"value": v, "label": v} for v in sucursales],
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
        facturas = list(queryset.select_related("cliente").order_by("numero_factura"))
        escritos, errores = {}, {}
        if "apply" in request.POST:
            form = AgruparEnCargaForm(request.POST)
            valores = {}
            vehiculo = form.cleaned_data.get("vehiculo") if form.is_valid() else None
            # Cambio #18: si la unidad lo pide, cada factura lleva paquetes y costo.
            if vehiculo is not None and vehiculo.pide_paquetes_costo:
                valores, escritos, errores = leer_paquetes_costo(request.POST, facturas)
            if form.is_valid() and not errores:
                carga = models.Carga.objects.create(
                    fecha_planeada=form.cleaned_data["fecha_planeada"],
                    ruta=form.cleaned_data["ruta"],
                    conductor=form.cleaned_data["conductor"],
                    vehiculo=form.cleaned_data["vehiculo"],
                    km_inicial=form.cleaned_data["km_inicial"],
                    observaciones=form.cleaned_data["observaciones"],
                )
                # Cambio #17: en la carga nueva hay que volver a cargarlas.
                # Cambio #18: paquetes y costo son de la factura en ESTA carga.
                actualizadas = queryset.update(
                    carga=carga, cargada_en=None, cargada_por="", paquetes=None, costo_flete=None
                )
                for factura_id, (paquetes, costo) in valores.items():
                    models.Factura.objects.filter(pk=factura_id).update(
                        paquetes=paquetes, costo_flete=costo
                    )
                self.message_user(
                    request,
                    f"Carga #{carga.id} creada con {actualizadas} factura(s) asignada(s).",
                    messages.SUCCESS,
                )
                return None
            if errores:
                vehiculo_placa = form.cleaned_data["vehiculo"].placa
                messages.error(
                    request,
                    f"Falta escribir paquetes y costo en todas las facturas (la unidad "
                    f"{vehiculo_placa} los pide). Los campos marcados en rojo están vacíos "
                    "o no son válidos.",
                )
        else:
            form = AgruparEnCargaForm()

        for f in facturas:
            f.paq_escrito, f.cos_escrito = escritos.get(f.id, ("", ""))
            f.errores_paq = errores.get(f.id, set())
        return render(
            request,
            "admin/core/factura/agrupar_en_carga.html",
            {
                "facturas": facturas,
                "unidades_con_paquetes": list(
                    models.Vehiculo.objects.filter(pide_paquetes_costo=True).values_list(
                        "id", flat=True
                    )
                ),
                "total_facturas_fmt": f"{sum((f.total for f in facturas), Decimal('0')):,.2f}",
                "form": form,
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
                "opts": self.model._meta,
                "title": "Agrupar facturas en una nueva Carga",
            },
        )

    agrupar_en_carga.short_description = "Agrupar seleccionadas en una nueva Carga"

    def agregar_a_carga_existente(self, request, queryset):
        """
        Acción de admin (Cambio #23): agrega las facturas seleccionadas a una
        Carga que ya existe (Planeada o En ruta). Pantalla intermedia para
        elegir la carga y, si la unidad lo pide, escribir paquetes y costo.
        """
        from core.asignar import AsignarError, agregar_a_carga, cargas_destino, revisar

        facturas = list(queryset.select_related("cliente", "carga").order_by("numero_factura"))
        cargas = list(cargas_destino()[:200])
        destino = None
        destino_id = request.POST.get("carga_destino", "")
        if destino_id.isdigit():
            destino = next((c for c in cargas if c.id == int(destino_id)), None)
        escritos, errores = {}, {}
        if "apply" in request.POST:
            if destino is None:
                messages.error(request, "Elige la carga a la que quieres agregar las facturas.")
            else:
                aptas, _ = revisar(facturas, destino)
                valores = {}
                if destino.pide_paquetes_costo:
                    valores, escritos, errores = leer_paquetes_costo(request.POST, aptas)
                if errores:
                    messages.error(
                        request,
                        f"La unidad {destino.vehiculo.placa} pide paquetes y costo en cada factura. "
                        "Los campos marcados en rojo están vacíos o no son válidos.",
                    )
                else:
                    try:
                        agregadas, no_aptas = agregar_a_carga(destino, facturas, valores)
                    except AsignarError as exc:
                        messages.error(request, str(exc))
                    else:
                        texto = (
                            f"{len(agregadas)} factura{'s' if len(agregadas) != 1 else ''} "
                            f"agregada{'s' if len(agregadas) != 1 else ''} a {destino.codigo_reporte}."
                        )
                        if no_aptas:
                            texto += " No se agregaron: " + "; ".join(
                                f"{f.numero_factura} ({f.motivo.lower()})" for f in no_aptas
                            ) + "."
                        if destino.estado == "EN_RUTA":
                            texto += (
                                " La carga ya está en ruta: el repartidor debe marcarlas como "
                                "cargadas antes de poder entregarlas."
                            )
                        self.message_user(
                            request, texto, messages.WARNING if no_aptas else messages.SUCCESS
                        )
                        return None

        revisar(facturas, destino)
        for f in facturas:
            f.paq_escrito, f.cos_escrito = escritos.get(f.id, ("", ""))
            f.errores_paq = errores.get(f.id, set())
        for c in cargas:
            c.total_facturas = c.facturas.filter(estado_factura="ACTIVA").count()
        return render(
            request,
            "admin/core/factura/agregar_a_carga.html",
            {
                "facturas": facturas,
                "aptas": sum(1 for f in facturas if f.puede),
                "cargas": cargas,
                "destino": destino,
                "cargas_con_paquetes": [c.id for c in cargas if c.pide_paquetes_costo],
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
                "opts": self.model._meta,
                "title": "Agregar facturas a una Carga existente",
            },
        )

    agregar_a_carga_existente.short_description = "Agregar seleccionadas a una Carga existente"


# Cambio #25: sucursales (puntos de entrega) del cliente, editables ahí
# mismo en la ficha del cliente, además del importador masivo de Excel.
class SucursalInline(admin.TabularInline):
    model = models.Sucursal
    extra = 0
    fields = ("nombre", "direccion", "municipio", "canal", "activo")


@admin.register(models.Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "municipio", "canal", "territorio", "ruta", "activo")
    list_filter = ("municipio", "canal", "territorio", "activo")
    search_fields = ("codigo", "nombre")
    inlines = [SucursalInline]
    change_list_template = "admin/core/cliente/change_list.html"

    # Cambio #22: si oficina escribe la ubicación a mano, queda anotado.
    def save_model(self, request, obj, form, change):
        if {"latitud", "longitud"} & set(form.changed_data) and "ubicacion_origen" not in form.changed_data:
            obj.ubicacion_origen = (
                f"Escrita a mano por {request.user.get_username()}" if obj.latitud is not None else ""
            )
        super().save_model(request, obj, form, change)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "importar-sucursales/",
                self.admin_site.admin_view(importar_sucursales_view),
                name="core_cliente_importar_sucursales",
            ),
        ]
        return custom + urls


# Cambio #25: catálogo de sucursales, también administrable aparte (con
# buscador propio, usado además por autocomplete_fields en FacturaAdmin).
@admin.register(models.Sucursal)
class SucursalAdmin(admin.ModelAdmin):
    list_display = ("nombre", "cliente", "direccion", "municipio", "canal", "grupo", "activo")
    list_filter = ("canal", "activo")
    search_fields = ("nombre", "cliente__nombre", "cliente__codigo")
    autocomplete_fields = ("cliente",)


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
    list_display = ("placa", "descripcion", "marca", "modelo", "tipo", "activo", "pide_paquetes_costo")
    list_filter = ("activo", "tipo", "pide_paquetes_costo")
    search_fields = ("placa", "descripcion", "marca")
    fieldsets = (
        (None, {"fields": ("placa", "descripcion", "activo", "pide_paquetes_costo")}),
        ("Flota (Cambio #39)", {"fields": (
            ("marca", "modelo", "anio"), ("tipo", "combustible", "propiedad"),
            ("capacidad_tanque", "capacidad_carga"),
        )}),
    )


class FacturaEnCargaInline(admin.TabularInline):
    model = models.Factura
    fk_name = "carga"
    extra = 0
    fields = ("numero_factura", "cliente", "total", "estado_entrega", "cargada_en")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    # Cambio #18: si la unidad de la carga pide paquetes y costo, se muestran
    # y se pueden corregir aquí, factura por factura.
    def get_fields(self, request, obj=None):
        campos = list(self.fields)
        if obj is not None and obj.pide_paquetes_costo:
            campos += ["paquetes", "costo_flete"]
        return campos


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
    readonly_fields = (
        "codigo_acceso",
        "ruta_terminada_en",
        "observaciones_repartidor",
        "cerrada_en",
        "cerrada_por",
        "observaciones_cierre",
        "devoluciones_recibidas_en",
        "devoluciones_recibidas_por",
        "observaciones_bodega",
        "salio_a_ruta_en",
    )
    inlines = [FacturaEnCargaInline]
    change_form_template = "admin/core/carga/change_form.html"

    # Cambio #14: "Ruta terminada" y "Cerrada" solo se alcanzan por el flujo
    # de cierre (botón "Cerrar carga"), no eligiéndolas a mano en esta ficha.
    def get_readonly_fields(self, request, obj=None):
        campos = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.bloqueada_para_repartidor:
            campos.append("estado")
        return campos

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == "estado":
            kwargs["choices"] = [
                (codigo, nombre)
                for codigo, nombre in models.Carga.ESTADO_CHOICES
                if codigo in ("PLANEADA", "EN_RUTA")
            ]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    @admin.display(description="Facturas")
    def total_facturas(self, obj):
        return obj.facturas.count()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        # Cambio #18: avisar qué facturas no tienen paquetes y costo.
        if request.method == "GET":
            carga = models.Carga.objects.filter(pk=object_id).select_related("vehiculo").first()
            if carga is not None:
                faltan = facturas_sin_paquetes_costo(carga)
                if faltan:
                    messages.warning(
                        request,
                        f"Esta Carga usa la unidad {carga.vehiculo.placa}: falta escribir "
                        f"paquetes y costo en {len(faltan)} factura"
                        f"{'s' if len(faltan) != 1 else ''} ("
                        + ", ".join(f.numero_factura for f in faltan)
                        + "). El repartidor no podrá salir a ruta mientras falten.",
                    )
        return super().change_view(request, object_id, form_url, extra_context)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:pk>/imprimir/",
                self.admin_site.admin_view(imprimir_carga_view),
                name="core_carga_imprimir",
            ),
            path(
                "<int:pk>/cierre/",
                self.admin_site.admin_view(cierre_carga_view),
                name="core_carga_cierre",
            ),
            # Cambio #22: corregir horas (queda registrado quién y por qué).
            path(
                "<int:pk>/horas/",
                self.admin_site.admin_view(corregir_horas_view),
                name="core_carga_horas",
            ),
        ]
        return custom + urls


# Cambio #27: devoluciones sin factura. Las líneas de detalle las captura el
# repartidor desde su celular (core.views_entregas_devolucion_sf); aquí solo
# se ven, de solo lectura.
class DevolucionSinFacturaDetalleInline(admin.TabularInline):
    model = models.DevolucionSinFacturaDetalle
    extra = 0
    fields = (
        "producto",
        "cantidad_recolectada",
        "motivo",
        "lote",
        "fecha_vencimiento",
        "recibido_inventario",
        "recibido_merma",
    )
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(models.ContactoSucursal)
class ContactoSucursalAdmin(admin.ModelAdmin):
    """
    Cambio #29: lista de contactos en las sucursales que ventas elige al
    solicitar una devolución sin factura. Se agregan con "Añadir" y se
    eliminan con la acción "Eliminar" (o se desactivan para ocultarlos).
    """

    list_display = ("nombre", "activo", "devoluciones")
    list_editable = ("activo",)
    list_filter = ("activo",)
    search_fields = ("nombre",)
    fields = ("nombre", "activo")

    def get_queryset(self, request):
        from django.db.models import Count

        return super().get_queryset(request).annotate(
            num_devoluciones=Count("devoluciones_sin_factura")
        )

    @admin.display(description="Devoluciones", ordering="num_devoluciones")
    def devoluciones(self, obj):
        return obj.num_devoluciones


@admin.register(models.DevolucionSinFactura)
class DevolucionSinFacturaAdmin(admin.ModelAdmin):
    list_display = (
        "codigo_reporte",
        "fecha_ingreso",
        "cliente",
        "sucursal",
        "contacto_sucursal",
        "motivo",
        "estado",
        "total_unidades_estimado",
        "carga",
    )
    list_filter = ("estado", "motivo__area_responsable")
    search_fields = (
        "sucursal__nombre",
        "cliente__nombre",
        "cliente__codigo",
        "sucursal__cliente__nombre",
        "sucursal__cliente__codigo",
        "contacto_sucursal",
    )
    autocomplete_fields = ("cliente", "sucursal", "contacto")
    list_select_related = ("cliente", "sucursal__cliente", "motivo", "carga")
    readonly_fields = (
        "estado",
        "carga",
        "creada_en",
        "creada_por",
        "recolectada_en",
        "recibido_en",
        "recibido_por",
        "observaciones_bodega",
        "revisada_cartera_en",
        "revisada_cartera_por",
    )
    inlines = [DevolucionSinFacturaDetalleInline]
    actions = ["agrupar_en_carga_devolucion", "agregar_a_carga_existente_devolucion"]

    def agrupar_en_carga_devolucion(self, request, queryset):
        """
        Acción de admin (Cambio #27): agrupa las devoluciones seleccionadas
        en una Carga nueva. Mismo planeador (misma pantalla/mecánica) que
        agrupa facturas en core.admin.FacturaAdmin.agrupar_en_carga.
        """
        devoluciones = list(
            queryset.select_related("sucursal__cliente").order_by("fecha_ingreso", "id")
        )
        if "apply" in request.POST:
            form = AgruparEnCargaForm(request.POST)
            if form.is_valid():
                try:
                    carga, asignadas = crear_carga_con_devoluciones(
                        fecha_planeada=form.cleaned_data["fecha_planeada"],
                        ruta=form.cleaned_data["ruta"],
                        conductor=form.cleaned_data["conductor"],
                        vehiculo=form.cleaned_data["vehiculo"],
                        km_inicial=form.cleaned_data["km_inicial"],
                        observaciones=form.cleaned_data["observaciones"],
                        devoluciones=devoluciones,
                    )
                except AsignarDevolucionError as exc:
                    messages.error(request, str(exc))
                else:
                    self.message_user(
                        request,
                        f"Carga {carga.codigo_reporte} creada con {len(asignadas)} "
                        f"devolución{'es' if len(asignadas) != 1 else ''} asignada(s).",
                        messages.SUCCESS,
                    )
                    return None
        else:
            form = AgruparEnCargaForm()

        return render(
            request,
            "admin/core/devolucionsinfactura/agrupar_en_carga.html",
            {
                "devoluciones": devoluciones,
                "form": form,
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
                "opts": self.model._meta,
                "title": "Agrupar devoluciones en una nueva Carga",
            },
        )

    agrupar_en_carga_devolucion.short_description = "Agrupar seleccionadas en una nueva Carga"

    def agregar_a_carga_existente_devolucion(self, request, queryset):
        """
        Acción de admin (Cambio #27): agrega las devoluciones seleccionadas a
        una Carga que ya existe. Mismo planeador que
        core.admin.FacturaAdmin.agregar_a_carga_existente.
        """
        from core.asignar import cargas_destino

        devoluciones = list(
            queryset.select_related("sucursal__cliente", "carga").order_by(
                "fecha_ingreso", "id"
            )
        )
        cargas = list(cargas_destino()[:200])
        destino = None
        destino_id = request.POST.get("carga_destino", "")
        if destino_id.isdigit():
            destino = next((c for c in cargas if c.id == int(destino_id)), None)

        if "apply" in request.POST:
            if destino is None:
                messages.error(request, "Elige la carga a la que quieres agregar las devoluciones.")
            else:
                try:
                    agregadas = asignar_devolucion_a_carga_existente(destino, devoluciones)
                except AsignarDevolucionError as exc:
                    messages.error(request, str(exc))
                else:
                    self.message_user(
                        request,
                        f"{len(agregadas)} devolución{'es' if len(agregadas) != 1 else ''} "
                        f"agregada{'s' if len(agregadas) != 1 else ''} a {destino.codigo_reporte}.",
                        messages.SUCCESS,
                    )
                    return None

        for c in cargas:
            c.total_devoluciones = c.devoluciones_sin_factura.count()
        return render(
            request,
            "admin/core/devolucionsinfactura/agregar_a_carga.html",
            {
                "devoluciones": devoluciones,
                "cargas": cargas,
                "destino": destino,
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
                "opts": self.model._meta,
                "title": "Agregar devoluciones a una Carga existente",
            },
        )

    agregar_a_carga_existente_devolucion.short_description = (
        "Agregar seleccionadas a una Carga existente"
    )


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
        "lote",
        "fecha_vencimiento",
        "recibido_inventario",
        "recibido_merma",
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
        "gps_estado",
        "tipo_entrega",
        "repartidor",
        "motivo_devolucion_general",
        "documento_cliente",
        "documento_devolucion",
        "estado",
    )
    list_filter = (
        "tipo_entrega",
        "estado",
        "documento_cliente",
        "gps_estado",
        "motivo_devolucion_general__area_responsable",
    )
    search_fields = ("factura__numero_factura", "documento_devolucion")
    inlines = [EntregaDetalleInline]
    readonly_fields = (
        "fecha_entrega", "gps_estado", "gps_latitud", "gps_longitud", "gps_precision", "mapa",
        "revisada_cartera_en", "revisada_cartera_por",
    )
    actions = ["usar_ubicacion_para_cliente"]

    @admin.display(description="Mapa")
    def mapa(self, obj):
        from django.utils.html import format_html

        from core.tiempos import url_mapa

        if obj.gps_latitud is None:
            return "—"
        return format_html('<a href="{}" target="_blank" rel="noopener">Ver en el mapa</a>', url_mapa(obj.gps_latitud, obj.gps_longitud))

    @admin.action(description="Usar la ubicación GPS de esta entrega como la del cliente")
    def usar_ubicacion_para_cliente(self, request, queryset):
        hechos = 0
        for entrega in queryset.select_related("factura__cliente"):
            if entrega.gps_latitud is None:
                continue
            cliente = entrega.factura.cliente
            cliente.latitud = entrega.gps_latitud
            cliente.longitud = entrega.gps_longitud
            cliente.ubicacion_origen = (
                f"Entrega {entrega.factura.numero_factura}, elegida por {request.user.get_username()}"
            )
            cliente.save(update_fields=["latitud", "longitud", "ubicacion_origen"])
            hechos += 1
        self.message_user(request, f"Ubicación actualizada en {hechos} cliente{'s' if hechos != 1 else ''}.")


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
    list_display = (
        "id",
        "cartera",
        "fecha_pago",
        "monto",
        "forma_pago",
        "numero_recibo",
        "referencia",
        "cobrador",
    )
    list_filter = ("forma_pago",)
    search_fields = ("cartera__factura__numero_factura", "numero_recibo", "referencia")


class ConsignacionDetalleInline(admin.TabularInline):
    model = models.ConsignacionDetalle
    extra = 0


@admin.register(models.Consignacion)
class ConsignacionAdmin(admin.ModelAdmin):
    list_display = ("factura", "cliente", "fecha_entrega", "fecha_liquidacion", "estado")
    list_filter = ("estado",)
    inlines = [ConsignacionDetalleInline]


# Cambio #19: el inicio del admin muestra el acceso al tablero de operación.
admin.site.index_template = "admin/index_sol_maya.html"
# Cambio #30: nombres del sitio con la marca.
admin.site.site_header = "Sol Maya · Distribución"
admin.site.site_title = "Sol Maya"
admin.site.index_title = "Administración"


# Cambio #22: llegadas a clientes y registro de horas corregidas (solo lectura).
@admin.register(models.ParadaCliente)
class ParadaClienteAdmin(admin.ModelAdmin):
    list_display = ("carga", "cliente", "llegada_en", "llegada_sin_senal")
    list_filter = ("llegada_sin_senal",)
    search_fields = ("cliente__nombre", "cliente__codigo")
    raw_id_fields = ("carga", "cliente")
    readonly_fields = ("carga", "cliente", "llegada_en", "llegada_sin_senal")

    def has_add_permission(self, request):
        return False


@admin.register(models.CorreccionHora)
class CorreccionHoraAdmin(admin.ModelAdmin):
    list_display = ("corregido_en", "carga", "campo", "valor_anterior", "valor_nuevo", "usuario", "motivo")
    list_filter = ("campo",)
    search_fields = ("motivo", "carga__id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.DatosImpresionDevolucion)
class DatosImpresionDevolucionAdmin(admin.ModelAdmin):
    """Cambio #37.1: solo consulta; se corrigen desde la hoja de impresión."""

    list_display = ("tipo", "objeto_id", "numero_fisico", "total", "guardado_por", "guardado_en", "modificado_por", "modificado_en")
    list_filter = ("tipo",)
    search_fields = ("numero_fisico",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False



# --- Cambio #39: flota ------------------------------------------------------


@admin.register(models.Conductor)
class ConductorAdmin(admin.ModelAdmin):
    """Repartidores con su licencia. Se crean y editan en Vendedores."""

    list_display = ("nombre", "telefono", "activo", "licencia", "estado_licencia")
    list_filter = ("activo",)
    search_fields = ("nombre",)
    fields = ("nombre", "telefono", "activo")
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).filter(tipo="REPARTIDOR")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def _licencia(self, obj):
        return (
            models.DocumentoFlota.objects.filter(conductor=obj, vigente=True, tipo__aplica_a="CONDUCTOR")
            .order_by("fecha_vencimiento").first()
        )

    @admin.display(description="Licencia (vence)")
    def licencia(self, obj):
        d = self._licencia(obj)
        if d is None:
            return "Falta"
        return d.fecha_vencimiento.strftime("%d/%m/%Y") if d.fecha_vencimiento else "Sin fecha"

    @admin.display(description="Estado")
    def estado_licencia(self, obj):
        from core.flota import estado_documento

        d = self._licencia(obj)
        return estado_documento(d)["texto"] if d else "—"


@admin.register(models.TipoDocumentoFlota)
class TipoDocumentoFlotaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "aplica_a", "activo", "orden")
    list_filter = ("aplica_a", "activo")
    list_editable = ("activo", "orden")


@admin.register(models.DocumentoFlota)
class DocumentoFlotaAdmin(admin.ModelAdmin):
    list_display = ("tipo", "dueno", "numero", "fecha_vencimiento", "estado", "vigente", "ultimo_aviso_en")
    list_filter = ("vigente", "tipo__aplica_a", "tipo")
    search_fields = ("numero", "vehiculo__placa", "conductor__nombre")
    raw_id_fields = ("archivo",)
    readonly_fields = ("ultimo_aviso_en", "ultimo_aviso_dias", "registrado_por", "registrado_en")
    autocomplete_fields = ()
    date_hierarchy = "fecha_vencimiento"

    @admin.display(description="De")
    def dueno(self, obj):
        return obj.dueno_texto

    @admin.display(description="Estado")
    def estado(self, obj):
        from core.flota import estado_documento

        return estado_documento(obj)["texto"] if obj.vigente else "Historial"


@admin.register(models.TarjetaFlota)
class TarjetaFlotaAdmin(admin.ModelAdmin):
    list_display = ("__str__", "vehiculo", "activa", "notas")
    list_filter = ("activa",)
    search_fields = ("ultimos4", "vehiculo__placa")


@admin.register(models.AbastecimientoCombustible)
class AbastecimientoCombustibleAdmin(admin.ModelAdmin):
    list_display = ("fecha", "vehiculo", "conductor", "odometro", "litros", "monto", "comprobante", "estado")
    list_filter = ("estado", "vehiculo")
    search_fields = ("comprobante", "vehiculo__placa", "conductor__nombre", "estacion")
    date_hierarchy = "fecha"
    raw_id_fields = ("carga", "foto")
    readonly_fields = ("original", "registrado_por", "registrado_en", "revisado_por", "revisado_en")


@admin.register(models.EjecucionAvisosFlota)
class EjecucionAvisosFlotaAdmin(admin.ModelAdmin):
    list_display = ("fecha", "documentos", "enviado", "detalle", "ejecutado_en")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# Cambio #39: el inicio del admin agrupa las entradas en bloques
# (Distribución / Flota / Configuración) para encontrar fácil cada cosa.
GRUPOS_ADMIN = [
    ("DISTRIBUCIÓN", "distribucion", "Clientes y ventas",
     ["Cliente", "Sucursal", "ContactoSucursal", "Producto", "Vendedor", "Factura", "ModalidadPago"]),
    ("DISTRIBUCIÓN", "distribucion", "Rutas y entregas",
     ["Ruta", "Carga", "Entrega", "ParadaCliente", "CorreccionHora"]),
    ("DISTRIBUCIÓN", "distribucion", "Devoluciones",
     ["DevolucionSinFactura", "MotivoDevolucion", "AreaResponsable", "DatosImpresionDevolucion"]),
    ("DISTRIBUCIÓN", "distribucion", "Cartera y cobros", ["CarteraCobro", "Pago", "Consignacion"]),
    ("FLOTA", "flota", "Vehículos y conductores", ["Vehiculo", "Conductor"]),
    ("FLOTA", "flota", "Documentos", ["DocumentoFlota", "TipoDocumentoFlota", "EjecucionAvisosFlota"]),
    ("FLOTA", "flota", "Combustible", ["AbastecimientoCombustible", "TarjetaFlota"]),
    ("CONFIGURACIÓN", "configuracion", "Usuarios y permisos", ["User", "Group"]),
]


def _app_list_agrupada(self, request, app_label=None):
    from django.contrib.admin import AdminSite
    from django.urls import reverse
    from django.utils.text import slugify

    apps = AdminSite.get_app_list(self, request, app_label)
    if app_label:  # página de una app: se deja como Django la arma
        return apps
    modelos = {}
    for app in apps:
        for m in app["models"]:
            modelos[m["object_name"]] = m
    resultado = []
    for bloque, clase, nombre, nombres in GRUPOS_ADMIN:
        lista = [modelos.pop(n) for n in nombres if n in modelos]
        if lista:
            slug = slugify(nombre)
            resultado.append({
                "name": nombre,
                "app_label": f"{clase}-{slug}",
                "app_url": f"{reverse('admin:index')}#{clase}-{slug}",
                "has_module_perms": True,
                "models": lista,
                "bloque": bloque,
                "bloque_clase": clase,
            })
    if modelos:  # lo que no esté en un grupo (nuevo) no se pierde
        resultado.append({
            "name": "Otros", "app_label": "otros", "app_url": reverse("admin:index") + "#otros",
            "has_module_perms": True, "models": sorted(modelos.values(), key=lambda m: m["name"]),
            "bloque": "CONFIGURACIÓN", "bloque_clase": "configuracion",
        })
    return resultado


admin.site.get_app_list = _app_list_agrupada.__get__(admin.site)
