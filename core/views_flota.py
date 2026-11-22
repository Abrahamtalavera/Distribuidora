"""
Cambio #39: pantallas de logística (flota, vencimientos, conductores y
combustible) y la pantalla del conductor para registrar combustible.

Logística entra con usuario y contraseña (permiso "Puede gestionar la flota
(logística)", grupo "Logística"). El conductor registra el combustible desde
su celular, dentro de su carga (código + PIN), como las entregas.
"""

from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from core import flota
from core.avisos_flota import correo_configurado, disparar_si_toca
from core.flota import FlotaError
from core.models import (
    AbastecimientoCombustible,
    ArchivoFlota,
    TipoDocumentoFlota,
    Vehiculo,
    Vendedor,
)


def logistica_required(vista):
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('logistica_acceso')}?next={request.get_full_path()}")
        if not flota.puede_gestionar(request.user):
            return render(request, "core/flota/sin_permiso.html", status=403)
        return vista(request, *args, **kwargs)

    return envoltura


class LogisticaLoginView(LoginView):
    template_name = "core/flota/acceso.html"
    redirect_authenticated_user = False

    def get_success_url(self):
        destino = self.request.POST.get("next") or self.request.GET.get("next") or ""
        if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={self.request.get_host()}):
            return destino
        return reverse("logistica_flota")


@require_POST
def logistica_salir_view(request):
    logout(request)
    return redirect("logistica_acceso")


def _contexto_base(request, pestana):
    disparar_si_toca()
    atencion = flota.resumen_atencion()
    return {
        "pestana": pestana,
        "n_atencion": atencion["total"],
        "n_por_revisar": flota.por_revisar_total(),
    }


def _volver(request, por_defecto):
    destino = request.POST.get("volver") or ""
    if destino.startswith("/") and url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        return redirect(destino)
    return redirect(por_defecto)


# --- Flota (vehículos) -------------------------------------------------------


@logistica_required
def logistica_flota_view(request):
    texto = (request.GET.get("q") or "").strip()[:60]
    vehiculos = list(flota.buscar_vehiculos(texto))
    ultimas = {}
    for a in AbastecimientoCombustible.objects.filter(vehiculo__in=vehiculos).order_by("vehiculo_id", "-fecha", "-id"):
        ultimas.setdefault(a.vehiculo_id, a)
    desde = timezone.localdate() - timedelta(days=30)
    for v in vehiculos:
        docs, _ = flota.documentos_de(vehiculo=v)
        v.peor = flota.peor_estado(docs)
        v.n_faltan = sum(1 for d in docs if d["estado"]["clave"] == flota.FALTA)
        v.ultima = ultimas.get(v.id)
        _, resumen, _ = flota.consulta_combustible(desde, timezone.localdate(), vehiculo_id=v.id)
        v.rendimiento = resumen["rendimiento"]

    seleccion = None
    docs = historial = []
    try:
        vid = int(request.GET.get("v") or 0)
    except ValueError:
        vid = 0
    if vid:
        seleccion = next((v for v in vehiculos if v.id == vid), None) or Vehiculo.objects.filter(pk=vid).first()
    if seleccion is None and vehiculos and not texto:
        seleccion = vehiculos[0]
    if seleccion is not None:
        docs, historial = flota.documentos_de(vehiculo=seleccion)

    contexto = _contexto_base(request, "flota")
    contexto.update({
        "vehiculos": vehiculos,
        "texto": texto,
        "sel": seleccion,
        "docs": docs,
        "historial": historial,
        "tipos": TipoDocumentoFlota.objects.filter(aplica_a="VEHICULO", activo=True),
        "tarjetas": flota.tarjetas_activas().filter(vehiculo=seleccion) if seleccion else [],
        "opciones_tipo": Vehiculo.TIPO_CHOICES,
        "opciones_combustible": Vehiculo.COMBUSTIBLE_CHOICES,
        "opciones_propiedad": Vehiculo.PROPIEDAD_CHOICES,
        "renovar": request.GET.get("renovar") or "",
        "volver": f"{reverse('logistica_flota')}?v={seleccion.id}" if seleccion else "",
    })
    return render(request, "core/flota/flota.html", contexto)


@logistica_required
@require_POST
def logistica_vehiculo_guardar_view(request, vehiculo_id):
    v = get_object_or_404(Vehiculo, pk=vehiculo_id)
    destino = f"{reverse('logistica_flota')}?v={v.id}"
    try:
        anio = (request.POST.get("anio") or "").strip()
        v.anio = int(anio) if anio else None
        if v.anio is not None and not 1950 <= v.anio <= timezone.localdate().year + 1:
            raise FlotaError("El año no es válido.")
        tanque = (request.POST.get("capacidad_tanque") or "").strip()
        v.capacidad_tanque = flota.leer_decimal(tanque, "la capacidad del tanque", minimo=flota.CERO) if tanque else None
    except (ValueError, FlotaError) as exc:
        messages.error(request, str(exc) if isinstance(exc, FlotaError) else "El año no es válido.")
        return redirect(destino)
    v.marca = (request.POST.get("marca") or "").strip()[:60]
    v.modelo = (request.POST.get("modelo") or "").strip()[:60]
    v.capacidad_carga = (request.POST.get("capacidad_carga") or "").strip()[:60]
    for campo, opciones in (("tipo", Vehiculo.TIPO_CHOICES), ("combustible", Vehiculo.COMBUSTIBLE_CHOICES), ("propiedad", Vehiculo.PROPIEDAD_CHOICES)):
        valor = request.POST.get(campo) or ""
        setattr(v, campo, valor if valor in dict(opciones) else "")
    v.save()
    messages.success(request, f"Datos de {v.placa} guardados.")
    return redirect(destino)


@logistica_required
@require_POST
def logistica_documento_view(request):
    """Agrega o renueva un documento de un vehículo o de un conductor."""
    tipo = get_object_or_404(TipoDocumentoFlota, pk=request.POST.get("tipo") or 0)
    vehiculo = conductor = None
    if request.POST.get("vehiculo"):
        vehiculo = get_object_or_404(Vehiculo, pk=request.POST.get("vehiculo"))
        por_defecto = f"{reverse('logistica_flota')}?v={vehiculo.id}"
    else:
        conductor = get_object_or_404(Vendedor, pk=request.POST.get("conductor") or 0, tipo="REPARTIDOR")
        por_defecto = f"{reverse('logistica_conductores')}?c={conductor.id}"
    try:
        texto_fecha = (request.POST.get("vence") or "").strip()
        vence = flota.leer_fecha(texto_fecha)
        if texto_fecha and vence is None:
            raise FlotaError("La fecha de vencimiento no es válida.")
        archivo = flota.guardar_archivo(request.FILES.get("archivo"), request.user)
        doc = flota.registrar_documento(
            tipo=tipo, vehiculo=vehiculo, conductor=conductor,
            numero=request.POST.get("numero"), fecha_vencimiento=vence,
            archivo=archivo, notas=request.POST.get("notas"), usuario=request.user,
        )
    except FlotaError as exc:
        messages.error(request, str(exc))
        return _volver(request, por_defecto)
    messages.success(request, f"{doc.tipo.nombre} de {doc.dueno_texto} guardado.")
    return _volver(request, por_defecto)


# --- Vencimientos ------------------------------------------------------------


@logistica_required
def logistica_vencimientos_view(request):
    filtro = request.GET.get("ver", "todos")
    if filtro not in flota.FILTROS_VENCIMIENTO:
        filtro = "todos"
    de = request.GET.get("de", "todos")
    if de not in flota.DE_OPCIONES:
        de = "todos"
    filas, conteos = flota.vencimientos(filtro, de)
    contexto = _contexto_base(request, "vencimientos")
    contexto.update({
        "filas": filas,
        "conteos": conteos,
        "filtro": filtro,
        "de": de,
        "filtros": flota.FILTROS_VENCIMIENTO,
        "de_opciones": flota.DE_OPCIONES,
        "correo_activo": correo_configurado(),
        "correos": ", ".join(settings.AVISOS_FLOTA_CORREOS),
        "tipos": TipoDocumentoFlota.objects.all(),
    })
    return render(request, "core/flota/vencimientos.html", contexto)


# --- Conductores -------------------------------------------------------------


@logistica_required
def logistica_conductores_view(request):
    conductores = list(flota.conductores_activos())
    for c in conductores:
        docs, _ = flota.documentos_de(conductor=c)
        c.docs = docs
        c.peor = flota.peor_estado(docs)
    seleccion = None
    docs = historial = []
    try:
        cid = int(request.GET.get("c") or 0)
    except ValueError:
        cid = 0
    if cid:
        seleccion = next((c for c in conductores if c.id == cid), None) or Vendedor.objects.filter(pk=cid, tipo="REPARTIDOR").first()
    if seleccion is not None:
        docs, historial = flota.documentos_de(conductor=seleccion)
    contexto = _contexto_base(request, "conductores")
    contexto.update({
        "conductores": conductores,
        "sel": seleccion,
        "docs": docs,
        "historial": historial,
        "tipos": TipoDocumentoFlota.objects.filter(aplica_a="CONDUCTOR", activo=True),
        "renovar": request.GET.get("renovar") or "",
        "volver": f"{reverse('logistica_conductores')}?c={seleccion.id}" if seleccion else "",
    })
    return render(request, "core/flota/conductores.html", contexto)


# --- Combustible -------------------------------------------------------------


def _leer_fecha_iso(texto, por_defecto):
    try:
        return datetime.strptime((texto or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return por_defecto


def _filtros_combustible(request):
    hoy = timezone.localdate()
    desde = _leer_fecha_iso(request.GET.get("desde"), hoy.replace(day=1))
    hasta = _leer_fecha_iso(request.GET.get("hasta"), hoy)
    if desde > hasta:
        desde, hasta = hasta, desde
    try:
        vehiculo_id = int(request.GET.get("vehiculo") or 0) or None
    except ValueError:
        vehiculo_id = None
    try:
        conductor_id = int(request.GET.get("conductor") or 0) or None
    except ValueError:
        conductor_id = None
    estado = request.GET.get("estado", "PENDIENTE")
    if estado not in flota.ESTADOS_FILTRO:
        estado = "PENDIENTE"
    return {"desde": desde, "hasta": hasta, "vehiculo_id": vehiculo_id, "conductor_id": conductor_id, "estado": estado}


def _consulta_texto(f):
    datos = {"desde": f["desde"].isoformat(), "hasta": f["hasta"].isoformat(), "estado": f["estado"]}
    if f["vehiculo_id"]:
        datos["vehiculo"] = f["vehiculo_id"]
    if f["conductor_id"]:
        datos["conductor"] = f["conductor_id"]
    return urlencode(datos)


@logistica_required
def logistica_combustible_view(request):
    f = _filtros_combustible(request)
    filas, resumen, recortado = flota.consulta_combustible(f["desde"], f["hasta"], f["vehiculo_id"], f["conductor_id"], f["estado"])
    seleccion = None
    try:
        sid = int(request.GET.get("sel") or 0)
    except ValueError:
        sid = 0
    if sid:
        seleccion = next((x for x in filas if x.id == sid), None)
        if seleccion is None:
            a = AbastecimientoCombustible.objects.select_related("vehiculo", "conductor", "tarjeta", "revisado_por").filter(pk=sid).first()
            seleccion = flota.anotar_abastecimientos([a])[0] if a else None
    elif f["estado"] == "PENDIENTE" and filas:
        seleccion = filas[0]
    contexto = _contexto_base(request, "combustible")
    contexto.update({
        "filas": filas,
        "r": resumen,
        "recortado": recortado,
        "f": f,
        "consulta": _consulta_texto(f),
        "sel": seleccion,
        "vehiculos": Vehiculo.objects.order_by("-activo", "placa"),
        "conductores": Vendedor.objects.filter(tipo="REPARTIDOR").order_by("-activo", "nombre"),
        "tarjetas": flota.tarjetas_activas(),
        "estados": flota.ESTADOS_FILTRO,
        "titulo_lista": "Cargas por revisar" if f["estado"] == "PENDIENTE" else f"Cargas · {flota.ESTADOS_FILTRO[f['estado']].lower()}",
    })
    return render(request, "core/flota/combustible.html", contexto)


@logistica_required
@require_POST
def logistica_combustible_revisar_view(request, abastecimiento_id):
    ab = get_object_or_404(AbastecimientoCombustible, pk=abastecimiento_id)
    volver = request.POST.get("consulta") or ""
    destino = f"{reverse('logistica_combustible')}?{volver}" if volver else reverse("logistica_combustible")
    try:
        flota.revisar_abastecimiento(ab, request.POST, request.user)
    except FlotaError as exc:
        messages.error(request, str(exc))
        return redirect(f"{destino}&sel={ab.id}" if volver else f"{destino}?sel={ab.id}")
    messages.success(request, f"Carga de {ab.vehiculo.placa} del {timezone.localtime(ab.fecha):%d/%m} marcada como {ab.get_estado_display().lower()}.")
    return redirect(destino)


@logistica_required
def logistica_combustible_excel_view(request):
    f = _filtros_combustible(request)
    filas, _, _ = flota.consulta_combustible(f["desde"], f["hasta"], f["vehiculo_id"], f["conductor_id"], f["estado"], limite=5000)
    respuesta = HttpResponse(
        flota.excel_combustible(filas),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    respuesta["Content-Disposition"] = f'attachment; filename="combustible_{f["desde"]:%Y%m%d}_{f["hasta"]:%Y%m%d}.xlsx"'
    return respuesta


@logistica_required
def logistica_archivo_view(request, archivo_id):
    archivo = get_object_or_404(ArchivoFlota, pk=archivo_id)
    respuesta = HttpResponse(bytes(archivo.contenido), content_type=archivo.tipo_contenido)
    nombre = archivo.nombre.replace('"', "")
    respuesta["Content-Disposition"] = f'inline; filename="{nombre}"'
    respuesta["X-Content-Type-Options"] = "nosniff"
    return respuesta


# --- Conductor: registrar combustible desde el celular -----------------------


@require_http_methods(["GET", "POST"])
def entregas_combustible_view(request, carga_id):
    from core.views_entregas import _carga_autorizada

    carga = _carga_autorizada(request, carga_id)
    if carga is None:
        return redirect("entregas_acceso")
    conductor = carga.conductor
    vehiculos = Vehiculo.objects.filter(activo=True).order_by("placa")
    error = None
    datos = request.POST if request.method == "POST" else {}
    if request.method == "POST":
        try:
            ab = flota.registrar_abastecimiento(
                request.POST, request.FILES, conductor=conductor, carga=carga,
                usuario=request.user if request.user.is_authenticated else None,
            )
        except FlotaError as exc:
            error = str(exc)
        else:
            messages.success(request, f"Carga de combustible guardada: {ab.litros.normalize():f} litros en {ab.vehiculo.placa}.")
            return redirect("entregas_combustible", carga_id=carga.id)

    try:
        vehiculo_id = int(datos.get("vehiculo") or 0) or carga.vehiculo_id
    except ValueError:
        vehiculo_id = carga.vehiculo_id
    lecturas = {}
    for v in vehiculos:
        ultima = flota.ultima_lectura(v)
        if ultima:
            lecturas[v.id] = {"odometro": ultima.odometro, "fecha": timezone.localtime(ultima.fecha).strftime("%d/%m")}
    tarjetas = list(flota.tarjetas_activas())
    recientes = (
        AbastecimientoCombustible.objects.filter(conductor=conductor).select_related("vehiculo").order_by("-fecha", "-id")[:5]
        if conductor else []
    )
    return render(request, "core/entregas/combustible.html", {
        "carga": carga,
        "conductor": conductor,
        "vehiculos": vehiculos,
        "vehiculo_id": vehiculo_id,
        "lecturas": lecturas,
        "tarjetas": tarjetas,
        "tarjeta_por_vehiculo": {t.vehiculo_id: t.id for t in tarjetas if t.vehiculo_id},
        "recientes": recientes,
        "datos": datos,
        "error": error,
        "aviso_licencia": flota.aviso_licencia(conductor),
    })
