"""
Filtros de formato que NO dependen del idioma activo. Con LANGUAGE_CODE="es"
Django muestra los Decimal con coma decimal ("4850,00"); el negocio usa coma
de miles y punto decimal ("4,850.00"), así que se formatea a mano (mismo
criterio de los Cambios #8 y #12).
"""

from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def moneda(valor):
    if valor is None or valor == "":
        return ""
    return f"{Decimal(valor):,.2f}"


@register.filter
def moneda_signo(valor):
    """Como moneda, pero con signo + cuando es positivo (para diferencias)."""
    if valor is None or valor == "":
        return ""
    valor = Decimal(valor)
    return f"{'+' if valor > 0 else ''}{valor:,.2f}"


@register.filter
def km(valor):
    if valor is None or valor == "":
        return ""
    return f"{Decimal(valor):,.1f}"


@register.filter
def plano(valor):
    """Número con punto decimal y sin separador de miles, para value= de un input."""
    if valor is None or valor == "":
        return ""
    return f"{Decimal(valor):.2f}"


@register.filter
def plano_km(valor):
    if valor is None or valor == "":
        return ""
    return f"{Decimal(valor):.1f}"


@register.filter
def cantidad(valor):
    if valor is None or valor == "":
        return ""
    texto = f"{Decimal(valor):,.3f}".rstrip("0").rstrip(".")
    return texto or "0"
