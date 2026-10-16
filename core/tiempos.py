"""
Cambio #22: horas de la ruta y GPS.

- Las horas las pone el sistema: salida a ruta (Cambio #17), llegada a cada
  cliente (botón "Llegué"), entrega de cada factura y fin de ruta (Cambio
  #14). Oficina puede corregir una hora desde el admin y queda registrado
  quién, cuándo y por qué (CorreccionHora).
- Al registrar una entrega el celular manda su ubicación GPS. Si el cliente
  tiene ubicación guardada se compara la distancia; si no la tiene, la
  primera entrega con buena señal se la deja guardada.

Igual que el resto de módulos, la lógica vive aquí para poder probarla.
"""

import math
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import CorreccionHora, Entrega, ParadaCliente

# Distancia máxima (metros) entre la entrega y el cliente para considerarla
# "en el cliente". Se le suma la precisión que reporta el celular, hasta un
# máximo, porque un GPS de ±80 m no puede asegurar menos que eso.
DISTANCIA_CLIENTE_M = 300
PRECISION_MAX_TOLERADA_M = 200
# Precisión mínima para que una entrega deje guardada la ubicación del cliente.
PRECISION_PARA_APRENDER_M = 100
# Hora que manda el celular cuando marcó "Llegué" sin señal: se acepta si cae
# entre la salida a ruta (con este margen) y ahora (con este margen).
MARGEN_HORA_CELULAR = timedelta(minutes=5)

ESTADOS_GPS_CELULAR = {"OK", "DENEGADO", "NO_DISPONIBLE"}


class CorreccionError(Exception):
    pass


# ---------------------------------------------------------------------------
# GPS
# ---------------------------------------------------------------------------


def _decimal(texto):
    try:
        return Decimal(str(texto).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def leer_gps(datos, desde_oficina=False):
    """
    Lee los campos gps_* que manda el formulario de captura. Devuelve un
    dict {estado, latitud, longitud, precision}. Nunca falla: si algo viene
    mal, el estado queda como NO_DISPONIBLE y la entrega se guarda igual.
    """
    if desde_oficina:
        return {"estado": "OFICINA", "latitud": None, "longitud": None, "precision": None}
    estado = (datos.get("gps_estado") or "").strip().upper()
    lat = _decimal(datos.get("gps_latitud"))
    lng = _decimal(datos.get("gps_longitud"))
    precision = _decimal(datos.get("gps_precision"))
    if estado == "OK":
        if lat is None or lng is None or not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            estado = "NO_DISPONIBLE"
    if estado != "OK":
        return {
            "estado": estado if estado in ESTADOS_GPS_CELULAR else "NO_DISPONIBLE",
            "latitud": None,
            "longitud": None,
            "precision": None,
        }
    return {
        "estado": "OK",
        "latitud": lat.quantize(Decimal("0.000001")),
        "longitud": lng.quantize(Decimal("0.000001")),
        "precision": int(precision) if precision is not None and precision >= 0 else None,
    }


def guardar_gps(entrega, gps):
    entrega.gps_estado = gps["estado"]
    entrega.gps_latitud = gps["latitud"]
    entrega.gps_longitud = gps["longitud"]
    entrega.gps_precision = gps["precision"]
    entrega.save(update_fields=["gps_estado", "gps_latitud", "gps_longitud", "gps_precision"])

    cliente = entrega.factura.cliente
    if (
        gps["estado"] == "OK"
        and cliente.latitud is None
        and gps["precision"] is not None
        and gps["precision"] <= PRECISION_PARA_APRENDER_M
    ):
        cliente.latitud = gps["latitud"]
        cliente.longitud = gps["longitud"]
        fecha = timezone.localtime(entrega.fecha_entrega).strftime("%d/%m/%Y")
        cliente.ubicacion_origen = f"Primera entrega con GPS ({entrega.factura.numero_factura}, {fecha})"
        cliente.save(update_fields=["latitud", "longitud", "ubicacion_origen"])
        entrega.ubicacion_aprendida = True


def distancia_m(lat1, lng1, lat2, lng2):
    """Distancia en metros entre dos puntos (fórmula de haversine)."""
    r = 6371000.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lng2) - float(lng1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def url_mapa(lat, lng):
    return f"https://www.google.com/maps?q={lat},{lng}"


INDICADORES = {
    "EN_CLIENTE": ("En el cliente", "completa"),
    "LEJOS": ("Lejos del cliente", "noentregada"),
    "SIN_REFERENCIA": ("Ubicación registrada", "contado"),
    "PRIMERA": ("Ubicación guardada", "contado"),
    "DENEGADO": ("GPS no permitido", "parcial"),
    "NO_DISPONIBLE": ("Sin señal GPS", "parcial"),
    "OFICINA": ("Registrada en oficina", "pendiente"),
    "SIN_DATO": ("Sin GPS", "pendiente"),
}


def indicador_gps(entrega, cliente=None):
    """
    Indicador de GPS de una entrega: dict con codigo, texto, css, distancia
    (metros, o None) y mapa (URL, o None).
    """
    cliente = cliente or entrega.factura.cliente
    estado = entrega.gps_estado
    distancia = None
    mapa = None
    if estado == "OK":
        mapa = url_mapa(entrega.gps_latitud, entrega.gps_longitud)
        if cliente.latitud is None or cliente.longitud is None:
            codigo = "SIN_REFERENCIA"
        else:
            distancia = round(
                distancia_m(entrega.gps_latitud, entrega.gps_longitud, cliente.latitud, cliente.longitud)
            )
            if distancia <= 1 and cliente.ubicacion_origen.startswith("Primera entrega") and entrega.factura.numero_factura in cliente.ubicacion_origen:
                codigo = "PRIMERA"
            else:
                tolerancia = DISTANCIA_CLIENTE_M + min(
                    entrega.gps_precision or 0, PRECISION_MAX_TOLERADA_M
                )
                codigo = "EN_CLIENTE" if distancia <= tolerancia else "LEJOS"
    elif estado in INDICADORES:
        codigo = estado
    else:
        codigo = "SIN_DATO"
    texto, css = INDICADORES[codigo]
    if codigo == "LEJOS":
        texto = f"A {_texto_distancia(distancia)} del cliente"
    return {"codigo": codigo, "texto": texto, "css": css, "distancia": distancia, "mapa": mapa}


def _texto_distancia(metros):
    if metros is None:
        return ""
    if metros >= 1000:
        return f"{metros / 1000:.1f} km"
    return f"{metros} m"


# ---------------------------------------------------------------------------
# Llegada a cada cliente
# ---------------------------------------------------------------------------


def leer_hora_celular(texto, carga):
    """
    Hora (milisegundos desde 1970) que guardó el celular al tocar "Llegué"
    sin señal. Devuelve un datetime o None si no es creíble.
    """
    try:
        ms = int(str(texto).strip())
    except (TypeError, ValueError):
        return None
    cuando = datetime.fromtimestamp(ms / 1000, tz=dt_timezone.utc)
    ahora = timezone.now()
    desde = (carga.salio_a_ruta_en or ahora - timedelta(hours=24)) - MARGEN_HORA_CELULAR
    if desde <= cuando <= ahora + MARGEN_HORA_CELULAR:
        return min(cuando, ahora)
    return None


def registrar_llegada(carga, cliente, cuando=None, sin_senal=False):
    """
    Marca la llegada del repartidor al cliente en esta carga. Si ya estaba
    marcada, la deja como estaba (cuenta la primera vez). Devuelve la parada.
    """
    existente = ParadaCliente.objects.filter(carga=carga, cliente=cliente).first()
    if existente:
        return existente
    try:
        with transaction.atomic():
            return ParadaCliente.objects.create(
                carga=carga,
                cliente=cliente,
                llegada_en=cuando or timezone.now(),
                llegada_sin_senal=sin_senal,
            )
    except IntegrityError:  # dos toques casi al mismo tiempo
        return ParadaCliente.objects.get(carga=carga, cliente=cliente)


# ---------------------------------------------------------------------------
# Tiempos de una carga
# ---------------------------------------------------------------------------


def duracion(desde, hasta):
    if not desde or not hasta or hasta < desde:
        return None
    return hasta - desde


def texto_duracion(valor):
    """timedelta → '2 h 05 min' / '35 min'."""
    if valor is None:
        return "—"
    minutos = int(round(valor.total_seconds() / 60))
    horas, minutos = divmod(minutos, 60)
    if horas:
        return f"{horas} h {minutos:02d} min"
    return f"{minutos} min"


def _promedio(valores):
    valores = [v for v in valores if v is not None]
    if not valores:
        return None
    return sum(valores, timedelta()) / len(valores)


def tiempos_de_la_carga(carga):
    """
    Horas y tiempos de la ruta. Devuelve:
      salida, fin, total, hasta_primer_cliente, paradas (en orden de visita:
      cliente, facturas, llegada, salida, en_cliente, viaje desde la parada
      anterior), promedios, mayor viaje, y por_factura {factura_id: {...}}
      con la llegada, la hora de entrega y el indicador de GPS.
    """
    entregas = list(
        carga.entregas.filter(estado="CONFIRMADA")
        .select_related("factura__cliente")
        .order_by("fecha_entrega", "id")
    ) if carga.pk else []
    llegadas = {p.cliente_id: p for p in carga.paradas.all()} if carga.pk else {}

    por_cliente = {}
    por_factura = {}
    for e in entregas:
        cliente = e.factura.cliente
        datos = por_cliente.setdefault(
            cliente.id,
            {"cliente": cliente, "facturas": [], "primera_entrega": e.fecha_entrega, "salida": e.fecha_entrega},
        )
        if e.factura.numero_factura not in datos["facturas"]:
            datos["facturas"].append(e.factura.numero_factura)
        datos["salida"] = max(datos["salida"], e.fecha_entrega)
        por_factura[e.factura_id] = {
            "hora": e.fecha_entrega,
            "visitas": por_factura.get(e.factura_id, {}).get("visitas", 0) + 1,
            "gps": indicador_gps(e, cliente),
            "entrega": e,
        }
    # Clientes con "Llegué" pero sin entrega registrada todavía.
    for cliente_id, parada in llegadas.items():
        if cliente_id not in por_cliente:
            por_cliente[cliente_id] = {
                "cliente": parada.cliente, "facturas": [], "primera_entrega": None, "salida": None,
            }

    paradas = []
    for cliente_id, d in por_cliente.items():
        parada = llegadas.get(cliente_id)
        llegada = parada.llegada_en if parada else None
        paradas.append(
            {
                "cliente": d["cliente"],
                "facturas": d["facturas"],
                "llegada": llegada,
                "llegada_sin_senal": bool(parada and parada.llegada_sin_senal),
                "salida": d["salida"],
                "en_cliente": duracion(llegada, d["salida"]),
                "orden_hora": llegada or d["primera_entrega"],
            }
        )
    paradas.sort(key=lambda p: (p["orden_hora"] is None, p["orden_hora"] or timezone.now()))

    anterior = carga.salio_a_ruta_en
    for i, p in enumerate(paradas, 1):
        p["orden"] = i
        inicio = p["llegada"] or p["salida"]
        p["viaje"] = duracion(anterior, inicio)
        if p["salida"]:
            anterior = p["salida"]
    for d in por_factura.values():
        cliente_id = d["entrega"].factura.cliente_id
        parada = next((p for p in paradas if p["cliente"].id == cliente_id), None)
        d["llegada"] = parada["llegada"] if parada else None
        d["orden"] = parada["orden"] if parada else None
        d["en_cliente"] = parada["en_cliente"] if parada else None

    salida, fin = carga.salio_a_ruta_en, carga.ruta_terminada_en
    primera = paradas[0] if paradas else None
    viajes = [p["viaje"] for p in paradas[1:]]
    ultima_salida = max((p["salida"] for p in paradas if p["salida"]), default=None)
    gps = [d["gps"]["codigo"] for d in por_factura.values()]
    return {
        "salida": salida,
        "fin": fin,
        "total": duracion(salida, fin),
        "hasta_primer_cliente": duracion(salida, (primera["llegada"] or primera["salida"]) if primera else None),
        "ultimo_a_fin": duracion(ultima_salida, fin),
        "paradas": paradas,
        "clientes_visitados": sum(1 for p in paradas if p["salida"]),
        "promedio_en_cliente": _promedio([p["en_cliente"] for p in paradas]),
        "promedio_viaje": _promedio(viajes),
        "mayor_viaje": max((v for v in viajes if v is not None), default=None),
        "sin_llegada": sum(1 for p in paradas if p["salida"] and not p["llegada"]),
        "por_factura": por_factura,
        "gps_en_cliente": gps.count("EN_CLIENTE") + gps.count("PRIMERA"),
        "gps_lejos": gps.count("LEJOS"),
        "gps_sin": sum(1 for c in gps if c in ("DENEGADO", "NO_DISPONIBLE", "SIN_DATO")),
        "gps_total": len(gps),
        "correcciones": list(
            carga.correcciones_hora.select_related("usuario", "entrega__factura", "parada__cliente")
        ) if carga.pk else [],
    }


# ---------------------------------------------------------------------------
# Corrección de horas (oficina)
# ---------------------------------------------------------------------------


@transaction.atomic
def corregir_horas(carga, *, usuario, motivo, salida=None, fin=None, llegadas=None, entregas=None):
    """
    Cambia las horas indicadas y deja una CorreccionHora por cada una que
    cambió de verdad. `llegadas` = {parada_id: datetime}; `entregas` =
    {entrega_id: datetime}. Devuelve cuántas horas cambiaron.
    """
    motivo = (motivo or "").strip()
    if not motivo:
        raise CorreccionError("Escribe el motivo de la corrección.")
    llegadas = llegadas or {}
    entregas = entregas or {}

    nueva_salida = salida if salida is not None else carga.salio_a_ruta_en
    nuevo_fin = fin if fin is not None else carga.ruta_terminada_en
    if nueva_salida and nuevo_fin and nuevo_fin < nueva_salida:
        raise CorreccionError("El fin de ruta no puede ser antes de la salida.")
    ahora = timezone.now() + MARGEN_HORA_CELULAR
    for valor in [salida, fin, *llegadas.values(), *entregas.values()]:
        if valor is not None and valor > ahora:
            raise CorreccionError("No se puede poner una hora que todavía no ha pasado.")
    for valor in [*llegadas.values(), *entregas.values()]:
        if valor is None:
            continue
        if nueva_salida and valor < nueva_salida:
            raise CorreccionError("Una llegada o entrega no puede ser antes de la salida a ruta.")
        if nuevo_fin and valor > nuevo_fin:
            raise CorreccionError("Una llegada o entrega no puede ser después del fin de ruta.")

    cambios = []

    def anotar(campo, anterior, nuevo, **rel):
        if nuevo is None or anterior == nuevo:
            return False
        cambios.append(
            CorreccionHora(
                carga=carga, campo=campo, valor_anterior=anterior, valor_nuevo=nuevo,
                motivo=motivo[:200], usuario=usuario, **rel,
            )
        )
        return True

    campos_carga = []
    if anotar("SALIDA", carga.salio_a_ruta_en, salida):
        carga.salio_a_ruta_en = salida
        campos_carga.append("salio_a_ruta_en")
    if anotar("FIN", carga.ruta_terminada_en, fin):
        carga.ruta_terminada_en = fin
        campos_carga.append("ruta_terminada_en")
    if campos_carga:
        carga.save(update_fields=campos_carga)

    for parada in carga.paradas.filter(id__in=list(llegadas)):
        nuevo = llegadas[parada.id]
        if anotar("LLEGADA", parada.llegada_en, nuevo, parada=parada):
            parada.llegada_en = nuevo
            parada.save(update_fields=["llegada_en"])
    for entrega in Entrega.objects.filter(carga=carga, id__in=list(entregas)):
        nuevo = entregas[entrega.id]
        if anotar("ENTREGA", entrega.fecha_entrega, nuevo, entrega=entrega):
            Entrega.objects.filter(pk=entrega.pk).update(fecha_entrega=nuevo)

    CorreccionHora.objects.bulk_create(cambios)
    return len(cambios)
