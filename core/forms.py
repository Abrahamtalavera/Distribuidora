from django import forms


class ImportarFacturasForm(forms.Form):
    archivo = forms.FileField(label="Archivo Excel (.xlsx)")
