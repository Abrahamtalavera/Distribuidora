"""
Cambio #39: correo diario a logística con los documentos de flota que toca
avisar (2 meses antes, 1 mes antes y semanal en el último mes; ya vencidos,
semanal hasta renovarlos). A los conductores no se les manda correo: ven el
aviso de su licencia en la app.

Se ejecuta una vez al día: con el comando `python manage.py avisos_flota`
(tarea programada en Railway) o, si nadie lo programa, la primera vez del
día que alguien abre el tablero, el admin o la pantalla de logística. Si el
correo no está configurado (EMAIL_HOST vacío) no hace nada y no marca nada,
para que al configurarlo salgan los avisos pendientes.
"""

import logging
import threading

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone

from core.flota import _de_activo, documentos_vigentes, estado_documento, proximo_aviso
from core.models import EjecucionAvisosFlota

log = logging.getLogger(__name__)


def correo_configurado():
    return bool(getattr(settings, "EMAIL_HOST", "") and getattr(settings, "AVISOS_FLOTA_CORREOS", []))


def documentos_para_avisar(hoy=None):
    hoy = hoy or timezone.localdate()
    docs = []
    for d in documentos_vigentes():
        if not _de_activo(d):
            continue
        fecha = proximo_aviso(d, hoy)
        if fecha is not None and fecha <= hoy:
            docs.append(d)
    docs.sort(key=lambda d: d.fecha_vencimiento)
    return docs


def armar_correo(docs, hoy=None):
    hoy = hoy or timezone.localdate()
    vencidos = [d for d in docs if d.fecha_vencimiento < hoy]
    asunto = f"Sol Maya · Flota: {len(docs)} documento{'s' if len(docs) != 1 else ''} por vencer"
    if vencidos:
        asunto += f" ({len(vencidos)} vencido{'s' if len(vencidos) != 1 else ''})"
    lineas = [
        f"Avisos de vencimiento de documentos de flota — {hoy:%d/%m/%Y}",
        "",
    ]
    for d in docs:
        est = estado_documento(d, hoy)
        de = f"{d.vehiculo.placa} ({d.vehiculo.descripcion})" if d.vehiculo_id else f"{d.conductor.nombre} (conductor)"
        numero = f" N° {d.numero}" if d.numero else ""
        lineas.append(f"- {d.tipo.nombre}{numero} · {de} · vence {d.fecha_vencimiento:%d/%m/%Y} · {est['texto']}")
    lineas += [
        "",
        "Para renovar un documento, entra a la app: Logística › Vencimientos.",
        "Se vuelve a avisar 1 mes antes y cada semana durante el último mes, hasta renovarlo.",
    ]
    return asunto, "\n".join(lineas)


def enviar_avisos(hoy=None, forzar=False):
    """Envía el correo del día. Devuelve (cantidad de documentos, mensaje)."""
    hoy = hoy or timezone.localdate()
    if not correo_configurado():
        return 0, "El correo no está configurado (EMAIL_HOST); no se envió nada."
    try:
        with transaction.atomic():
            ejecucion, creada = EjecucionAvisosFlota.objects.get_or_create(fecha=hoy)
    except IntegrityError:
        return 0, "Otro proceso ya está enviando los avisos de hoy."
    if not creada and not forzar:
        return 0, "Los avisos de hoy ya se habían revisado."

    docs = documentos_para_avisar(hoy)
    if not docs:
        ejecucion.documentos, ejecucion.enviado, ejecucion.detalle = 0, False, "Nada que avisar hoy."
        ejecucion.save()
        return 0, ejecucion.detalle
    asunto, cuerpo = armar_correo(docs, hoy)
    try:
        send_mail(asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, settings.AVISOS_FLOTA_CORREOS)
    except Exception as exc:  # noqa: BLE001 — se reintenta en la próxima visita
        log.exception("No se pudo enviar el correo de avisos de flota")
        ejecucion.delete()
        return 0, f"No se pudo enviar el correo: {exc}"
    for d in docs:
        d.ultimo_aviso_en = hoy
        d.ultimo_aviso_dias = (d.fecha_vencimiento - hoy).days
        d.save(update_fields=["ultimo_aviso_en", "ultimo_aviso_dias"])
    ejecucion.documentos, ejecucion.enviado = len(docs), True
    ejecucion.detalle = f"Enviado a {', '.join(settings.AVISOS_FLOTA_CORREOS)}"[:300]
    ejecucion.save()
    return len(docs), ejecucion.detalle


def _en_segundo_plano():
    try:
        enviar_avisos()
    finally:
        close_old_connections()


def disparar_si_toca():
    """Desde una vista: si hoy no se han enviado los avisos, los envía aparte."""
    if not correo_configurado():
        return
    if EjecucionAvisosFlota.objects.filter(fecha=timezone.localdate()).exists():
        return
    if getattr(settings, "AVISOS_FLOTA_EN_SEGUNDO_PLANO", True):
        threading.Thread(target=_en_segundo_plano, daemon=True).start()
    else:
        enviar_avisos()
