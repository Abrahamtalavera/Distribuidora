"""
Cambio #39: flota — documentos con vencimiento (vehículos y conductores),
tarjetas de flota y combustible.

- Documentos: cada vehículo y cada conductor (repartidor) tiene documentos
  con fecha de vencimiento. Al renovar se crea uno nuevo y el anterior queda
  en el historial (vigente=False).
- Avisos: 2 meses antes, 1 mes antes y cada semana durante el último mes; ya
  vencido, cada semana hasta renovarlo (ver proximo_aviso). El correo lo arma
  core/avisos_flota.py; en la app se ven en el tablero y en logística.
- Combustible: el conductor registra cada carga desde su celular; logística
  revisa y corrige. Se guarda lo que escribió el conductor.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

from core.models import (
    AbastecimientoCombustible,
    ArchivoFlota,
    DocumentoFlota,
    TarjetaFlota,
    TipoDocumentoFlota,
    Vehiculo,
    Vendedor,
)

PERMISO = "core.gestionar_flota"
CERO = Decimal("0")

DIAS_PRIMER_AVISO = 60
DIAS_SEGUNDO_AVISO = 30
CADA_SEMANA = 7

# Rendimiento por debajo de este porcentaje del normal del vehículo = alerta.
RENDIMIENTO_BAJO = Decimal("0.75")
DIAS_RENDIMIENTO_NORMAL = 120

TAMANO_MAXIMO_ARCHIVO = 6 * 1024 * 1024
TIPOS_IMAGEN = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")
TIPOS_ARCHIVO = TIPOS_IMAGEN + ("application/pdf",)


class FlotaError(Exception):
    pass


def puede_gestionar(usuario):
    return bool(
        usuario and usuario.is_authenticated and usuario.is_active and usuario.has_perm(PERMISO)
    )


# --- Lectura de números escritos en pantalla ---------------------------------


def leer_decimal(texto, nombre, minimo=None):
    texto = (texto or "").replace(",", "").replace("C$", "").strip()
    if not texto:
        raise FlotaError(f"Falta {nombre}.")
    try:
        valor = Decimal(texto)
    except InvalidOperation:
        raise FlotaError(f"{nombre[:1].upper()}{nombre[1:]} no es un número válido.")
    if not valor.is_finite() or valor < 0 or valor >= Decimal("1e9"):
        raise FlotaError(f"{nombre[:1].upper()}{nombre[1:]} no es válido.")
    if minimo is not None and valor <= minimo:
        raise FlotaError(f"{nombre[:1].upper()}{nombre[1:]} debe ser mayor que {minimo}.")
    return valor


def leer_entero(texto, nombre):
    valor = leer_decimal(texto, nombre, minimo=Decimal("0"))
    if valor != valor.to_integral_value():
        raise FlotaError(f"{nombre[:1].upper()}{nombre[1:]} debe ser un número entero.")
    return int(valor)


def leer_fecha(texto):
    from core.entregas import leer_fecha_vencimiento

    return leer_fecha_vencimiento(texto)


# --- Archivos (foto o PDF) ---------------------------------------------------


def guardar_archivo(subido, usuario=None, solo_imagen=False):
    """Guarda en la base de datos un archivo subido. None si no se subió nada."""
    if not subido:
        return None
    tipo = (getattr(subido, "content_type", "") or "").lower()
    permitidos = TIPOS_IMAGEN if solo_imagen else TIPOS_ARCHIVO
    if tipo not in permitidos:
        raise FlotaError("El archivo debe ser una foto" + ("" if solo_imagen else " o un PDF") + ".")
    if subido.size > TAMANO_MAXIMO_ARCHIVO:
        raise FlotaError("El archivo es muy grande (máximo 6 MB).")
    contenido = subido.read()
    return ArchivoFlota.objects.create(
        nombre=(subido.name or "archivo")[:120],
        tipo_contenido=tipo,
        contenido=contenido,
        tamano=len(contenido),
        subido_por=usuario if usuario and usuario.is_authenticated else None,
    )


# --- Documentos y vencimientos ----------------------------------------------

SIN_FECHA, VENCIDO, SEMANA, MES, DOS_MESES, VIGENTE, FALTA = (
    "SIN_FECHA", "VENCIDO", "SEMANA", "MES", "DOS_MESES", "VIGENTE", "FALTA",
)
CSS_ESTADO = {
    VENCIDO: "venc", SEMANA: "semana", MES: "mes", DOS_MESES: "dosmeses",
    VIGENTE: "ok", SIN_FECHA: "gris", FALTA: "gris",
}


def estado_documento(doc, hoy=None):
    hoy = hoy or timezone.localdate()
    if not doc.fecha_vencimiento:
        return {"clave": SIN_FECHA, "texto": "Sin fecha", "css": "gris", "dias": None}
    dias = (doc.fecha_vencimiento - hoy).days
    if dias < 0:
        clave = VENCIDO
        texto = "Vencido hace 1 día" if dias == -1 else f"Vencido hace {-dias} días"
    elif dias <= CADA_SEMANA:
        clave = SEMANA
        texto = "Vence hoy" if dias == 0 else ("1 día" if dias == 1 else f"{dias} días")
    elif dias <= DIAS_SEGUNDO_AVISO:
        clave, texto = MES, f"{dias} días"
    elif dias <= DIAS_PRIMER_AVISO:
        clave, texto = DOS_MESES, f"{dias} días"
    else:
        clave, texto = VIGENTE, "Vigente"
    return {"clave": clave, "texto": texto, "css": CSS_ESTADO[clave], "dias": dias}


def proximo_aviso(doc, hoy=None):
    """
    Fecha del próximo aviso por correo (o None si no habrá). Avisa 2 meses
    antes, 1 mes antes y luego cada semana, también ya vencido, hasta que se
    renueve. Si un aviso se atrasó (no se envió ese día), toca hoy.
    """
    hoy = hoy or timezone.localdate()
    if not doc.vigente or not doc.fecha_vencimiento:
        return None
    vence = doc.fecha_vencimiento
    dias = (vence - hoy).days
    if dias > DIAS_PRIMER_AVISO:
        return vence - timedelta(days=DIAS_PRIMER_AVISO)
    if doc.ultimo_aviso_en is None:
        return hoy
    if dias > DIAS_SEGUNDO_AVISO:
        return vence - timedelta(days=DIAS_SEGUNDO_AVISO)
    if doc.ultimo_aviso_dias is None or doc.ultimo_aviso_dias > DIAS_SEGUNDO_AVISO:
        return hoy
    return max(hoy, doc.ultimo_aviso_en + timedelta(days=CADA_SEMANA))


def texto_proximo_aviso(doc, hoy=None):
    hoy = hoy or timezone.localdate()
    fecha = proximo_aviso(doc, hoy)
    if fecha is None:
        return "Sin fecha, sin aviso" if not doc.fecha_vencimiento else "—"
    dias = (doc.fecha_vencimiento - fecha).days
    if doc.ultimo_aviso_en is None and dias < DIAS_PRIMER_AVISO:
        etapa = "Primer aviso"
    elif dias > DIAS_SEGUNDO_AVISO:
        etapa = "2 meses antes"
    elif doc.ultimo_aviso_dias is None or doc.ultimo_aviso_dias > DIAS_SEGUNDO_AVISO:
        etapa = "1 mes antes"
    elif dias < 0:
        etapa = "Cada semana hasta renovar"
    else:
        etapa = "Semanal"
    return f"{etapa} · {'hoy' if fecha == hoy else fecha.strftime('%d/%m')}"


def conductores_activos():
    return Vendedor.objects.filter(tipo="REPARTIDOR", activo=True).order_by("nombre")


def documentos_vigentes():
    return DocumentoFlota.objects.filter(vigente=True).select_related(
        "tipo", "vehiculo", "conductor", "archivo"
    )


def faltantes():
    """Documentos que deberían existir y no se han registrado (tipos activos)."""
    tipos = list(TipoDocumentoFlota.objects.filter(activo=True))
    tienen = set(
        DocumentoFlota.objects.filter(vigente=True).values_list("tipo_id", "vehiculo_id", "conductor_id")
    )
    filas = []
    vehiculos = list(Vehiculo.objects.filter(activo=True).order_by("placa"))
    conductores = list(conductores_activos())
    for t in tipos:
        if t.aplica_a == "VEHICULO":
            for v in vehiculos:
                if (t.id, v.id, None) not in tienen:
                    filas.append({"tipo": t, "vehiculo": v, "conductor": None})
        else:
            for c in conductores:
                if (t.id, None, c.id) not in tienen:
                    filas.append({"tipo": t, "vehiculo": None, "conductor": c})
    return filas


def _de_activo(doc):
    if doc.vehiculo_id:
        return doc.vehiculo.activo
    return bool(doc.conductor_id and doc.conductor.activo and doc.conductor.tipo == "REPARTIDOR")


FILTROS_VENCIMIENTO = {
    "todos": "Todos",
    "vencidos": "Vencidos",
    "mes": "Este mes",
    "dos_meses": "En 2 meses",
    "faltan": "Faltan",
}
DE_OPCIONES = {"todos": "Vehículos y conductores", "vehiculos": "Solo vehículos", "conductores": "Solo conductores"}


def vencimientos(filtro="todos", de="todos", hoy=None):
    """Filas para la pantalla de vencimientos y conteos por filtro."""
    hoy = hoy or timezone.localdate()
    docs = [d for d in documentos_vigentes() if _de_activo(d)]
    filas = []
    for d in docs:
        est = estado_documento(d, hoy)
        filas.append({
            "doc": d, "tipo": d.tipo, "vehiculo": d.vehiculo, "conductor": d.conductor,
            "estado": est, "aviso": texto_proximo_aviso(d, hoy),
        })
    for f in faltantes():
        filas.append(dict(f, doc=None, estado={"clave": FALTA, "texto": "Falta", "css": "gris", "dias": None}, aviso="Sin fecha, sin aviso"))

    if de == "vehiculos":
        filas = [f for f in filas if f["vehiculo"]]
    elif de == "conductores":
        filas = [f for f in filas if f["conductor"]]

    grupos = {
        "todos": filas,
        "vencidos": [f for f in filas if f["estado"]["clave"] == VENCIDO],
        "mes": [f for f in filas if f["estado"]["clave"] in (SEMANA, MES)],
        "dos_meses": [f for f in filas if f["estado"]["clave"] == DOS_MESES],
        "faltan": [f for f in filas if f["estado"]["clave"] in (FALTA, SIN_FECHA)],
    }
    conteos = {k: len(v) for k, v in grupos.items()}
    orden = {VENCIDO: 0, SEMANA: 1, MES: 2, DOS_MESES: 3, VIGENTE: 4, SIN_FECHA: 5, FALTA: 6}
    elegidas = sorted(
        grupos.get(filtro, filas),
        key=lambda f: (orden[f["estado"]["clave"]], f["estado"]["dias"] if f["estado"]["dias"] is not None else 99999, f["tipo"].nombre),
    )
    return elegidas, conteos


def resumen_atencion(hoy=None):
    """Lo que requiere atención: vencidos y los que vencen en menos de 2 meses."""
    hoy = hoy or timezone.localdate()
    filas = []
    for d in documentos_vigentes():
        if not _de_activo(d):
            continue
        est = estado_documento(d, hoy)
        if est["clave"] in (VENCIDO, SEMANA, MES, DOS_MESES):
            filas.append({"doc": d, "estado": est})
    filas.sort(key=lambda f: f["estado"]["dias"])
    cuenta = defaultdict(int)
    for f in filas:
        cuenta[f["estado"]["clave"]] += 1
    return {
        "filas": filas,
        "total": len(filas),
        "vencidos": cuenta[VENCIDO],
        "semana": cuenta[SEMANA],
        "mes": cuenta[MES],
        "dos_meses": cuenta[DOS_MESES],
    }


def aviso_licencia(conductor, hoy=None):
    """Mensaje para el conductor si su licencia vence en menos de 2 meses o ya venció."""
    if conductor is None:
        return None
    hoy = hoy or timezone.localdate()
    doc = (
        DocumentoFlota.objects.filter(
            conductor=conductor, vigente=True, tipo__aplica_a="CONDUCTOR",
            fecha_vencimiento__isnull=False, tipo__activo=True,
        )
        .select_related("tipo")
        .order_by("fecha_vencimiento")
        .first()
    )
    if doc is None:
        return None
    est = estado_documento(doc, hoy)
    if est["clave"] not in (VENCIDO, SEMANA, MES, DOS_MESES):
        return None
    nombre = doc.tipo.nombre.lower()
    if est["clave"] == VENCIDO:
        titulo = f"Tu {nombre} está vencida desde el {doc.fecha_vencimiento:%d/%m/%Y}"
    elif est["dias"] == 0:
        titulo = f"Tu {nombre} vence hoy"
    else:
        titulo = f"Tu {nombre} vence en {est['dias']} día{'s' if est['dias'] != 1 else ''} ({doc.fecha_vencimiento:%d/%m/%Y})"
    return {"titulo": titulo, "css": est["css"], "estado": est}


def registrar_documento(*, tipo, vehiculo=None, conductor=None, numero="", fecha_vencimiento=None,
                        archivo=None, notas="", usuario=None):
    """
    Agrega un documento. Si ya había uno vigente del mismo tipo para el mismo
    vehículo o conductor, este lo renueva: el anterior pasa al historial (y si
    no se sube archivo nuevo, se conserva el anterior).
    """
    if (vehiculo is None) == (conductor is None):
        raise FlotaError("El documento debe ser de un vehículo o de un conductor.")
    if tipo.aplica_a == "VEHICULO" and vehiculo is None:
        raise FlotaError(f"{tipo.nombre} es un documento de vehículo.")
    if tipo.aplica_a == "CONDUCTOR" and conductor is None:
        raise FlotaError(f"{tipo.nombre} es un documento de conductor.")
    anteriores = DocumentoFlota.objects.filter(
        tipo=tipo, vehiculo=vehiculo, conductor=conductor, vigente=True
    )
    anterior = anteriores.order_by("-id").first()
    if archivo is None and anterior is not None and anterior.fecha_vencimiento == fecha_vencimiento:
        archivo = anterior.archivo
    anteriores.update(vigente=False)
    return DocumentoFlota.objects.create(
        tipo=tipo, vehiculo=vehiculo, conductor=conductor,
        numero=(numero or "").strip()[:60], fecha_vencimiento=fecha_vencimiento,
        archivo=archivo, notas=(notas or "").strip()[:200],
        registrado_por=usuario if usuario and usuario.is_authenticated else None,
    )


def documentos_de(vehiculo=None, conductor=None, hoy=None):
    """Documentos vigentes del vehículo o conductor, con los tipos que faltan."""
    hoy = hoy or timezone.localdate()
    aplica = "VEHICULO" if vehiculo else "CONDUCTOR"
    docs = {
        d.tipo_id: d
        for d in DocumentoFlota.objects.filter(vehiculo=vehiculo, conductor=conductor, vigente=True)
        .select_related("tipo", "archivo")
    }
    filas = []
    for t in TipoDocumentoFlota.objects.filter(aplica_a=aplica, activo=True):
        d = docs.pop(t.id, None)
        filas.append({
            "tipo": t, "doc": d,
            "estado": estado_documento(d, hoy) if d else {"clave": FALTA, "texto": "Falta", "css": "gris", "dias": None},
        })
    for d in docs.values():  # tipos desactivados que aún tienen documento
        filas.append({"tipo": d.tipo, "doc": d, "estado": estado_documento(d, hoy)})
    historial = list(
        DocumentoFlota.objects.filter(vehiculo=vehiculo, conductor=conductor, vigente=False)
        .select_related("tipo", "archivo").order_by("-registrado_en")[:20]
    )
    return filas, historial


def peor_estado(filas):
    """La alerta más urgente de una lista de documentos (para la lista de vehículos)."""
    orden = [VENCIDO, SEMANA, MES, DOS_MESES]
    for clave in orden:
        for f in filas:
            if f["estado"]["clave"] == clave:
                return f
    return None


# --- Combustible -------------------------------------------------------------


def ultima_lectura(vehiculo, antes_de=None, excluir_id=None):
    qs = AbastecimientoCombustible.objects.filter(vehiculo=vehiculo)
    if antes_de is not None:
        qs = qs.filter(fecha__lte=antes_de)
    if excluir_id:
        qs = qs.exclude(pk=excluir_id)
    return qs.order_by("-fecha", "-id").first()


def tarjetas_activas():
    return TarjetaFlota.objects.filter(activa=True).select_related("vehiculo").order_by("ultimos4")


def _datos_capturados(post, vehiculos):
    try:
        vehiculo = vehiculos.get(pk=int(post.get("vehiculo") or 0))
    except (Vehiculo.DoesNotExist, ValueError):
        raise FlotaError("Elige el vehículo.")
    odometro = leer_entero(post.get("odometro"), "el odómetro")
    litros = leer_decimal(post.get("litros"), "los litros", minimo=CERO)
    monto = leer_decimal(post.get("monto"), "el monto", minimo=CERO)
    tarjeta = None
    if post.get("tarjeta"):
        try:
            tarjeta = TarjetaFlota.objects.get(pk=int(post.get("tarjeta")))
        except (TarjetaFlota.DoesNotExist, ValueError):
            raise FlotaError("La tarjeta de flota no es válida.")
    return {
        "vehiculo": vehiculo,
        "odometro": odometro,
        "litros": litros.quantize(Decimal("0.01")),
        "monto": monto.quantize(Decimal("0.01")),
        "estacion": (post.get("estacion") or "").strip()[:80],
        "comprobante": (post.get("comprobante") or "").strip()[:40],
        "tarjeta": tarjeta,
    }


def _a_json(datos):
    return {
        "vehiculo": datos["vehiculo"].placa,
        "odometro": datos["odometro"],
        "litros": str(datos["litros"]),
        "monto": str(datos["monto"]),
        "estacion": datos["estacion"],
        "comprobante": datos["comprobante"],
        "tarjeta": datos["tarjeta"].ultimos4 if datos["tarjeta"] else "",
    }


def _leer_fecha_hora(texto):
    """Fecha y hora de un <input type=datetime-local> (hora de Managua)."""
    from datetime import datetime

    texto = (texto or "").strip()
    if not texto:
        return None
    for formato in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            valor = timezone.make_aware(datetime.strptime(texto, formato))
        except ValueError:
            continue
        if valor > timezone.now() + timedelta(minutes=5):
            raise FlotaError("La fecha de la carga no puede ser futura.")
        return valor
    raise FlotaError("La fecha de la carga no es válida.")


def registrar_abastecimiento(post, archivos=None, *, conductor=None, carga=None, usuario=None, origen="CARGA"):
    """
    Carga de combustible. La registra el conductor (desde su carga o desde el
    acceso de combustible, origen CARGA / CONDUCTOR) o logística (LOGISTICA:
    puede poner la fecha y queda revisada de una vez).
    """
    de_logistica = origen == "LOGISTICA"
    datos = _datos_capturados(post, Vehiculo.objects.all() if de_logistica else Vehiculo.objects.filter(activo=True))
    fecha = _leer_fecha_hora(post.get("fecha")) if de_logistica else None
    foto = guardar_archivo((archivos or {}).get("foto"), usuario, solo_imagen=not de_logistica)
    usuario = usuario if usuario and usuario.is_authenticated else None
    extra = {}
    if de_logistica:
        extra = {
            "estado": "REVISADA", "revisado_por": usuario, "revisado_en": timezone.now(),
            "nota_revision": (post.get("nota") or "").strip()[:200],
        }
    if fecha:
        extra["fecha"] = fecha
    return AbastecimientoCombustible.objects.create(
        conductor=conductor, carga=carga, original=_a_json(datos), foto=foto,
        registrado_por=usuario, origen=origen, **datos, **extra,
    )


INTENTOS_PIN = 5
MINUTOS_BLOQUEO = 15


def verificar_pin(conductor, pin):
    """
    Acceso de combustible: nombre + PIN. Tras 5 intentos fallidos el acceso
    de ese conductor se bloquea 15 minutos. Devuelve (ok, mensaje).
    """
    import secrets

    ahora = timezone.now()
    if conductor.pin_bloqueado_hasta and conductor.pin_bloqueado_hasta > ahora:
        minutos = max(1, int((conductor.pin_bloqueado_hasta - ahora).total_seconds() // 60) + 1)
        return False, f"Demasiados intentos. Espera {minutos} minuto{'s' if minutos != 1 else ''} o pide ayuda a logística."
    correcto = bool(conductor.pin_acceso) and secrets.compare_digest(conductor.pin_acceso, (pin or "").strip())
    if correcto:
        if conductor.pin_intentos_fallidos or conductor.pin_bloqueado_hasta:
            conductor.pin_intentos_fallidos, conductor.pin_bloqueado_hasta = 0, None
            conductor.save(update_fields=["pin_intentos_fallidos", "pin_bloqueado_hasta"])
        return True, ""
    conductor.pin_intentos_fallidos += 1
    if conductor.pin_intentos_fallidos >= INTENTOS_PIN:
        conductor.pin_intentos_fallidos = 0
        conductor.pin_bloqueado_hasta = ahora + timedelta(minutes=MINUTOS_BLOQUEO)
        conductor.save(update_fields=["pin_intentos_fallidos", "pin_bloqueado_hasta"])
        return False, f"Demasiados intentos. Espera {MINUTOS_BLOQUEO} minutos o pide ayuda a logística."
    conductor.save(update_fields=["pin_intentos_fallidos"])
    return False, "PIN incorrecto."


def revisar_abastecimiento(ab, post, usuario):
    """Logística corrige lo que haga falta y la marca revisada (o corregida)."""
    datos = _datos_capturados(post, Vehiculo.objects.all())
    cambio = any(getattr(ab, k) != v for k, v in datos.items())
    for k, v in datos.items():
        setattr(ab, k, v)
    if cambio or ab.estado == "CORREGIDA":
        ab.estado = "CORREGIDA"
    else:
        ab.estado = "REVISADA"
    ab.nota_revision = (post.get("nota") or "").strip()[:200]
    ab.revisado_por = usuario
    ab.revisado_en = timezone.now()
    ab.save()
    return ab


def anotar_abastecimientos(filas):
    """
    Agrega a cada abastecimiento: lectura anterior, km recorridos, km/l,
    rendimiento normal del vehículo y alertas.
    """
    if not filas:
        return filas
    vehiculos = {f.vehiculo_id for f in filas}
    desde = min(f.fecha for f in filas) - timedelta(days=DIAS_RENDIMIENTO_NORMAL)
    historia = defaultdict(list)
    for a in (
        AbastecimientoCombustible.objects.filter(vehiculo_id__in=vehiculos, fecha__gte=desde)
        .only("id", "vehiculo_id", "fecha", "odometro", "litros", "comprobante")
        .order_by("fecha", "id")
    ):
        historia[a.vehiculo_id].append(a)
    # Lectura anterior de los que no tienen otra dentro de la ventana.
    anterior_de = {}
    km_de = {}
    for vid, lista in historia.items():
        previo = (
            AbastecimientoCombustible.objects.filter(vehiculo_id=vid, fecha__lt=desde)
            .order_by("-fecha", "-id").only("odometro").first()
        )
        for a in lista:
            anterior_de[a.id] = previo
            if previo is not None:
                km_de[a.id] = a.odometro - previo.odometro
            previo = a
    repetidos = set()
    comprobantes = [f.comprobante for f in filas if f.comprobante]
    if comprobantes:
        vistos = defaultdict(set)
        for aid, comp in AbastecimientoCombustible.objects.filter(comprobante__in=comprobantes).values_list("id", "comprobante"):
            vistos[comp].add(aid)
        repetidos = {c for c, ids in vistos.items() if len(ids) > 1}

    for f in filas:
        anterior = anterior_de.get(f.id)
        f.anterior = anterior
        f.km = km_de.get(f.id)
        f.rendimiento = (Decimal(f.km) / f.litros).quantize(Decimal("0.1")) if f.km and f.km > 0 and f.litros else None
        km_n = litros_n = CERO
        for a in historia.get(f.vehiculo_id, []):
            k = km_de.get(a.id)
            if a.id != f.id and k and k > 0:
                km_n += k
                litros_n += a.litros
        f.rendimiento_normal = (km_n / litros_n).quantize(Decimal("0.1")) if litros_n else None
        alertas = []
        if anterior is not None and f.km is not None and f.km <= 0:
            alertas.append(("venc", "Odómetro menor al anterior" if f.km < 0 else "Odómetro igual al anterior"))
        tanque = f.vehiculo.capacidad_tanque
        if tanque and f.litros > tanque:
            alertas.append(("venc", f"Más litros que el tanque ({tanque.normalize():f} l)"))
        if f.rendimiento and f.rendimiento_normal and f.rendimiento < f.rendimiento_normal * RENDIMIENTO_BAJO:
            alertas.append(("mes", f"Rendimiento bajo (normal {f.rendimiento_normal})"))
        if f.comprobante and f.comprobante in repetidos:
            alertas.append(("venc", "Comprobante repetido"))
        f.alertas = alertas
    return filas


ESTADOS_FILTRO = {
    "PENDIENTE": "Por revisar",
    "REVISADA": "Revisadas",
    "CORREGIDA": "Corregidas",
    "todas": "Todas",
}


def consulta_combustible(desde, hasta, vehiculo_id=None, conductor_id=None, estado="todas", limite=500):
    qs = (
        AbastecimientoCombustible.objects.filter(fecha__date__gte=desde, fecha__date__lte=hasta)
        .select_related("vehiculo", "conductor", "tarjeta", "revisado_por")
    )
    if vehiculo_id:
        qs = qs.filter(vehiculo_id=vehiculo_id)
    if conductor_id:
        qs = qs.filter(conductor_id=conductor_id)
    todas = list(qs.order_by("-fecha", "-id")[: limite + 1])
    recortado = len(todas) > limite
    todas = anotar_abastecimientos(todas[:limite])
    filas = todas if estado == "todas" else [f for f in todas if f.estado == estado]

    gasto = sum((f.monto for f in todas), CERO)
    litros = sum((f.litros for f in todas), CERO)
    con_km = [f for f in todas if f.km and f.km > 0]
    km = sum(f.km for f in con_km)
    litros_km = sum((f.litros for f in con_km), CERO)
    monto_km = sum((f.monto for f in con_km), CERO)
    resumen = {
        "n": len(todas),
        "gasto": gasto,
        "litros": litros,
        "km": km,
        "rendimiento": (Decimal(km) / litros_km).quantize(Decimal("0.1")) if litros_km else None,
        "costo_km": (monto_km / km).quantize(Decimal("0.01")) if km else None,
        "vehiculos": len({f.vehiculo_id for f in todas}),
        "por_revisar": sum(1 for f in todas if f.estado == "PENDIENTE"),
        "con_alerta": sum(1 for f in todas if f.estado == "PENDIENTE" and f.alertas),
    }
    return filas, resumen, recortado


def por_revisar_total():
    return AbastecimientoCombustible.objects.filter(estado="PENDIENTE").count()


def excel_combustible(filas):
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Combustible"
    titulos = [
        "Fecha", "Vehículo", "Conductor", "Odómetro", "Km recorridos", "Litros", "Monto C$",
        "Km/l", "Estación", "Comprobante", "Tarjeta", "Estado", "Alertas",
        "Registrada por", "Revisado por", "Nota de la corrección", "Original del conductor",
    ]
    ws.append(titulos)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2C318C")
    for f in filas:
        original = ""
        if f.estado == "CORREGIDA" and f.original and f.origen != "LOGISTICA":
            o = f.original
            original = f"Odómetro {o.get('odometro')}, {o.get('litros')} l, C$ {o.get('monto')}, comp. {o.get('comprobante') or '—'}"
        ws.append([
            timezone.localtime(f.fecha).replace(tzinfo=None), f.vehiculo.placa,
            f.conductor.nombre if f.conductor else "", f.odometro, f.km, float(f.litros), float(f.monto),
            float(f.rendimiento) if f.rendimiento else None, f.estacion, f.comprobante,
            f.tarjeta.ultimos4 if f.tarjeta else "", f.get_estado_display(),
            "; ".join(t for _, t in f.alertas),
            f.get_origen_display(),
            f.revisado_por.get_username() if f.revisado_por else "", f.nota_revision, original,
        ])
    for fila in ws.iter_rows(min_row=2):
        fila[0].number_format = "DD/MM/YYYY HH:MM"
        fila[6].number_format = "#,##0.00"
    for col, ancho in zip("ABCDEFGHIJKLMNOPQ", (17, 12, 22, 11, 13, 9, 12, 7, 22, 14, 9, 12, 34, 30, 14, 30, 40)):
        ws.column_dimensions[col].width = ancho
    ws.freeze_panes = "A2"
    salida = BytesIO()
    wb.save(salida)
    return salida.getvalue()


def buscar_vehiculos(texto=""):
    qs = Vehiculo.objects.all().order_by("-activo", "placa")
    texto = (texto or "").strip()
    if texto:
        qs = qs.filter(Q(placa__icontains=texto) | Q(descripcion__icontains=texto) | Q(marca__icontains=texto))
    return qs
