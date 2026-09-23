from django import forms
from django.utils import timezone

from core import models


class ImportarFacturasForm(forms.Form):
    archivo = forms.FileField(label="Archivo Excel (.xlsx)")


class AgruparEnCargaForm(forms.Form):
    """Datos de la nueva Carga al agrupar facturas seleccionadas (Cambio #7)."""

    fecha_planeada = forms.DateField(
        label="Fecha planeada de entrega",
        widget=forms.DateInput(attrs={"type": "date"}),
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
