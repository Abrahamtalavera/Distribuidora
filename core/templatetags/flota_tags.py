"""Cambio #39: datos de flota para la tarjeta de Logística en el inicio del admin."""

from django import template

register = template.Library()


@register.simple_tag
def resumen_flota():
    from core.avisos_flota import disparar_si_toca
    from core.flota import por_revisar_total, resumen_atencion

    disparar_si_toca()
    r = resumen_atencion()
    return {"documentos": r["total"], "vencidos": r["vencidos"], "por_revisar": por_revisar_total()}
