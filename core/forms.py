from django import forms
from django.utils import timezone

from core import models


class ImportarFacturasForm(forms.Form):
    archivo = forms.FileField(label="Archivo Excel (.xlsx)")


class ImportarSucursalesForm(forms.Form):
    """Cambio #25: subir el maestro de clientes/sucursales de SAP."""

    archivo = forms.FileField(label="Archivo Excel (.xlsx)")


class AgruparEnCargaForm(forms.Form):
    """Datos de la nueva Carga al agrupar facturas seleccionadas (Cambio #7)."""

    fecha_planeada = forms.DateField(
        label="Fecha planeada de entrega",
        # El campo de fecha del navegador solo acepta AAAA-MM-DD; sin este
        # formato, con el idioma en español la fecha sugerida salía vacía.
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    ruta = forms.ModelChoiceField(
        queryset=models.Ruta.objects.filter(activo=True),
        required=False,
        label="Ruta / zona",
    )
    conductor = forms.ModelChoiceField(
        queryset=models.Vendedor.objects.filter(tipo="REPARTIDOR", activo=True),
        required=False,
        label="Conductor",
    )
    vehiculo = forms.ModelChoiceField(
        queryset=models.Vehiculo.objects.filter(activo=True),
        required=False,
        label="Unidad",
    )
    km_inicial = forms.DecimalField(required=False, label="Kilometraje inicial")
    observaciones = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Observaciones"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha_planeada"].initial = timezone.localdate()


class SucursalConClienteSelect(forms.Select):
    """Cambio #29: cada sucursal lleva su cliente, para filtrarlas en pantalla."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opcion = super().create_option(name, value, label, selected, index, subindex, attrs)
        instancia = getattr(value, "instance", None)
        if instancia is not None:
            opcion["attrs"]["data-cliente"] = str(instancia.cliente_id)
        return opcion


class SucursalChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.nombre


class SolicitarDevolucionSinFacturaForm(forms.Form):
    """Cambio #27: datos generales de una devolución sin factura (ventas)."""

    fecha_ingreso = forms.DateField(
        label="Fecha de ingreso",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    # Cambio #29: primero el cliente; la sucursal es una de las suyas.
    cliente = forms.ModelChoiceField(
        queryset=models.Cliente.objects.filter(sucursales__activo=True)
        .distinct()
        .order_by("nombre"),
        label="Cliente",
    )
    sucursal = SucursalChoiceField(
        queryset=models.Sucursal.objects.filter(activo=True)
        .select_related("cliente")
        .order_by("nombre"),
        label="Sucursal a retirar",
        widget=SucursalConClienteSelect,
    )
    total_unidades_estimado = forms.IntegerField(
        label="Total de unidades (estimado)", min_value=1
    )
    # Cambio #29: el contacto se elige de la lista de contactos en sucursales.
    contacto = forms.ModelChoiceField(
        queryset=models.ContactoSucursal.objects.filter(activo=True).order_by("nombre"),
        label="Nombre de contacto en la sucursal",
        error_messages={"required": "Elige el contacto de la sucursal."},
        empty_label="Elige el contacto...",
        help_text="Obligatorio: quien entrega la devolución en la sucursal. Si no está en la lista, pide en oficina que lo agreguen.",
    )
    motivo = forms.ModelChoiceField(
        queryset=models.MotivoDevolucion.objects.filter(activo=True).order_by("nombre"),
        label="Motivo general del retiro",
    )
    observaciones = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Observaciones"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha_ingreso"].initial = timezone.localdate()

    def clean(self):
        datos = super().clean()
        cliente, sucursal = datos.get("cliente"), datos.get("sucursal")
        if cliente and sucursal and sucursal.cliente_id != cliente.id:
            self.add_error("sucursal", "Esa sucursal no es de este cliente.")
        return datos
