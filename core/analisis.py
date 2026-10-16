"""
Cambio #20: análisis de entregas, devoluciones y desempeño para un período.

Complementa el tablero del Cambio #19 (que mira un día). Aquí se elige un
rango de fechas y, opcionalmente, una ruta o un repartidor, y se responde:
  - ¿Por qué se devuelve? (motivo y área responsable)
  - ¿Cómo rinde cada repartidor y cada ruta? (entregado, devuelto, efectividad)
  - ¿Qué clientes y productos devuelven más?
  - ¿Cuánto recorren y cuestan las unidades? (km, tiempo de ruta, flete)

Las visitas se ubican en el período por la fecha de la entrega; las cargas,
por su fecha planeada. Valores en C$ con IVA, calculados igual que en el
cierre de la carga. Solo lee: no cambia nada.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q
from django.utils import timezone

from core.entregas import valor_unitario
from core.tiempos import _promedio, indicador_gps, tiempos_de_la_carga
from core.models import Carga, EntregaDetalle, Factura

CERO = Decimal("0")
CENTAVO = Decimal("0.01")
DIAS_POR_DEFECTO = 30
TOP = 10
SIN_DATO = "Sin especificar"


def _r(valor):
    return (valor or CERO).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _pct(parte, total):
    if not total:
        return None
    return (Decimal(parte) * 100 / Decimal(total)).quantize(Decimal("0.1"))


def periodo_por_defecto():
    hasta = timezone.localdate()
    return hasta - timedelta(days=DIAS_POR_DEFECTO - 1), hasta


def _con_barras(filas, campo):
    """Agrega a cada fila .barra = % respecto a la fila mayor (para dibujarla)."""
    mayor = max((f[campo] for f in filas), default=CERO)
    for f in filas:
        f["barra"] = float(f[campo] * 100 / mayor) if mayor else 0
    return filas


def _agrupar_resto(filas, total, maximo=TOP):
    """Deja las `maximo` filas mayores y junta el resto en una fila 'Otros'."""
    if len(filas) <= maximo:
        return filas
    resto = filas[maximo - 1:]
    valor = sum((f["valor"] for f in resto), CERO)
    otros = {
        "nombre": f"Otros ({len(resto)} motivos)",
        "area": "Varias áreas",
        "valor": valor,
        "lineas": sum(f["lineas"] for f in resto),
        "pct": _pct(valor, total),
    }
    return _con_barras(filas[: maximo - 1] + [otros], "valor")


def _lineas_del_periodo(desde, hasta, ruta_id=None, repartidor_id=None):
    qs = EntregaDetalle.objects.filter(
        entrega__estado="CONFIRMADA",
        entrega__fecha_entrega__date__gte=desde,
        entrega__fecha_entrega__date__lte=hasta,
    ).select_related(
        "factura_detalle__producto",
        "entrega__factura__cliente",
        "entrega__repartidor",
        "entrega__carga__ruta",
        "entrega__carga__conductor",
        "entrega__motivo_devolucion_general__area_responsable",
        "motivo_devolucion__area_responsable",
    )
    if ruta_id:
        qs = qs.filter(entrega__carga__ruta_id=ruta_id)
    if repartidor_id:
        qs = qs.filter(
            Q(entrega__repartidor_id=repartidor_id)
            | Q(entrega__repartidor__isnull=True, entrega__carga__conductor_id=repartidor_id)
        )
    return qs


def _repartidor(entrega):
    persona = entrega.repartidor or (entrega.carga.conductor if entrega.carga_id else None)
    return persona.nombre if persona else SIN_DATO


def _ruta(entrega):
    if entrega.carga_id and entrega.carga.ruta_id:
        return entrega.carga.ruta.nombre
    return SIN_DATO


def _fila_desempeno(nombre):
    return {
        "nombre": nombre, "facturas": set(), "con_dev": set(), "entregado": CERO, "devuelto": CERO,
        # Cambio #22: indicador de GPS por visita (entrega).
        "gps": {},
    }


def _cerrar_desempeno(grupos):
    filas = []
    for g in grupos.values():
        entregado, devuelto = _r(g["entregado"]), _r(g["devuelto"])
        filas.append(
            {
                "nombre": g["nombre"],
                "facturas": len(g["facturas"]),
                "con_devolucion": len(g["con_dev"]),
                "entregado": entregado,
                "devuelto": devuelto,
                "efectividad": _pct(entregado, entregado + devuelto),
                "gps_total": len(g["gps"]),
                "gps_en_cliente": sum(1 for c in g["gps"].values() if c in ("EN_CLIENTE", "PRIMERA")),
                "gps_lejos": sum(1 for c in g["gps"].values() if c == "LEJOS"),
                "gps_pct": _pct(
                    sum(1 for c in g["gps"].values() if c in ("EN_CLIENTE", "PRIMERA")),
                    sum(1 for c in g["gps"].values() if c in ("EN_CLIENTE", "PRIMERA", "LEJOS")),
                ),
            }
        )
    filas.sort(key=lambda f: f["entregado"], reverse=True)
    return _con_barras(filas, "entregado")


def analisis_entregas(desde, hasta, ruta_id=None, repartidor_id=None):
    """Todo lo que sale de las visitas (líneas de entrega) del período."""
    por_motivo = defaultdict(lambda: {"valor": CERO, "lineas": 0, "area": ""})
    por_area = defaultdict(lambda: {"valor": CERO, "lineas": 0})
    por_repartidor = {}
    por_ruta = {}
    por_cliente = defaultdict(lambda: {"valor": CERO, "facturas": set()})
    por_producto = defaultdict(lambda: {"valor": CERO, "cantidad": CERO, "unidad": ""})
    total_ent = total_dev = CERO
    facturas = set()
    gps_por_entrega = {}

    for linea in _lineas_del_periodo(desde, hasta, ruta_id, repartidor_id):
        e = linea.entrega
        unitario = valor_unitario(linea.factura_detalle)
        ent = linea.cantidad_entregada * unitario
        dev = linea.cantidad_devuelta * unitario
        total_ent += ent
        total_dev += dev
        facturas.add(e.factura_id)

        if e.id not in gps_por_entrega:
            gps_por_entrega[e.id] = indicador_gps(e, e.factura.cliente)["codigo"] if e.gps_estado else None
        for grupos, nombre in ((por_repartidor, _repartidor(e)), (por_ruta, _ruta(e))):
            g = grupos.setdefault(nombre, _fila_desempeno(nombre))
            if gps_por_entrega[e.id]:
                g["gps"][e.id] = gps_por_entrega[e.id]
            g["facturas"].add(e.factura_id)
            g["entregado"] += ent
            g["devuelto"] += dev
            if dev:
                g["con_dev"].add(e.factura_id)

        if linea.cantidad_devuelta <= 0:
            continue
        motivo = linea.motivo_devolucion or e.motivo_devolucion_general
        nombre_motivo = motivo.nombre if motivo else SIN_DATO
        area = motivo.area_responsable.nombre if motivo and motivo.area_responsable else SIN_DATO
        por_motivo[nombre_motivo]["valor"] += dev
        por_motivo[nombre_motivo]["lineas"] += 1
        por_motivo[nombre_motivo]["area"] = area
        por_area[area]["valor"] += dev
        por_area[area]["lineas"] += 1

        cliente = e.factura.cliente
        por_cliente[cliente.id]["nombre"] = cliente.nombre
        por_cliente[cliente.id]["sucursal"] = cliente.sucursal
        por_cliente[cliente.id]["valor"] += dev
        por_cliente[cliente.id]["facturas"].add(e.factura_id)

        producto = linea.factura_detalle.producto
        por_producto[producto.id]["nombre"] = f"{producto.codigo} · {producto.nombre}"
        por_producto[producto.id]["unidad"] = producto.unidad_medida
        por_producto[producto.id]["valor"] += dev
        por_producto[producto.id]["cantidad"] += linea.cantidad_devuelta

    total_ent, total_dev = _r(total_ent), _r(total_dev)

    def ordenar(dic, **extra):
        filas = []
        for clave, d in dic.items():
            fila = {"nombre": d.get("nombre", clave), **{k: v for k, v in d.items() if k != "facturas"}}
            fila["valor"] = _r(d["valor"])
            fila["pct"] = _pct(fila["valor"], total_dev)
            if "facturas" in d:
                fila["facturas"] = len(d["facturas"])
            filas.append(fila)
        filas.sort(key=lambda f: f["valor"], reverse=True)
        return _con_barras(filas, "valor")

    return {
        "totales": {
            "facturas": len(facturas),
            "entregado": total_ent,
            "devuelto": total_dev,
            "efectividad": _pct(total_ent, total_ent + total_dev),
        },
        "por_motivo": _agrupar_resto(ordenar(por_motivo), total_dev),
        "por_area": ordenar(por_area),
        "por_repartidor": _cerrar_desempeno(por_repartidor),
        "por_ruta": _cerrar_desempeno(por_ruta),
        "top_clientes": ordenar(por_cliente)[:TOP],
        "top_productos": ordenar(por_producto)[:TOP],
    }


def analisis_unidades(desde, hasta, ruta_id=None, repartidor_id=None):
    """
    Por vehículo: cargas, km recorridos, horas en ruta y flete (paquetes y
    costo que captura el planeador, Cambio #18). Cuenta solo cargas que ya
    salieron a ruta.
    """
    cargas = Carga.objects.filter(
        fecha_planeada__gte=desde,
        fecha_planeada__lte=hasta,
        estado__in=("EN_RUTA", "RUTA_TERMINADA", "CERRADA"),
    ).select_related("vehiculo", "conductor")
    if ruta_id:
        cargas = cargas.filter(ruta_id=ruta_id)
    if repartidor_id:
        cargas = cargas.filter(conductor_id=repartidor_id)
    cargas = list(cargas)

    flete = defaultdict(lambda: {"paquetes": 0, "costo": CERO})
    for f in Factura.objects.filter(carga__in=cargas).exclude(estado_factura="ANULADA"):
        flete[f.carga_id]["paquetes"] += f.paquetes or 0
        flete[f.carga_id]["costo"] += f.costo_flete or CERO

    grupos = {}
    for c in cargas:
        nombre = c.vehiculo.placa if c.vehiculo_id else SIN_DATO
        g = grupos.setdefault(
            nombre,
            {"nombre": nombre, "cargas": 0, "km": CERO, "cargas_km": 0,
             "horas": 0.0, "cargas_horas": 0, "paquetes": 0, "costo": CERO},
        )
        g["cargas"] += 1
        if c.km_inicial is not None and c.km_final is not None and c.km_final >= c.km_inicial:
            g["km"] += c.km_final - c.km_inicial
            g["cargas_km"] += 1
        if c.salio_a_ruta_en and c.ruta_terminada_en and c.ruta_terminada_en > c.salio_a_ruta_en:
            g["horas"] += (c.ruta_terminada_en - c.salio_a_ruta_en).total_seconds() / 3600
            g["cargas_horas"] += 1
        g["paquetes"] += flete[c.id]["paquetes"]
        g["costo"] += flete[c.id]["costo"]

    filas = []
    for g in grupos.values():
        filas.append(
            {
                "nombre": g["nombre"],
                "cargas": g["cargas"],
                "km": g["km"],
                "km_promedio": (g["km"] / g["cargas_km"]).quantize(Decimal("0.1")) if g["cargas_km"] else None,
                "horas_promedio": round(g["horas"] / g["cargas_horas"], 1) if g["cargas_horas"] else None,
                "paquetes": g["paquetes"],
                "costo": _r(g["costo"]),
                "costo_paquete": _r(g["costo"] / g["paquetes"]) if g["paquetes"] else None,
            }
        )
    filas.sort(key=lambda f: f["km"], reverse=True)
    return _con_barras(filas, "km")


def tiempos_por_grupo(desde, hasta, ruta_id=None, repartidor_id=None):
    """
    Cambio #22: tiempos promedio de las cargas del período que ya terminaron
    ruta, por repartidor y por ruta: tiempo en ruta, tiempo en cada cliente
    y tiempo entre clientes.
    """
    cargas = Carga.objects.filter(
        fecha_planeada__gte=desde,
        fecha_planeada__lte=hasta,
        estado__in=("RUTA_TERMINADA", "CERRADA"),
        salio_a_ruta_en__isnull=False,
    ).select_related("ruta", "conductor")
    if ruta_id:
        cargas = cargas.filter(ruta_id=ruta_id)
    if repartidor_id:
        cargas = cargas.filter(conductor_id=repartidor_id)
    acumulado = {"repartidor": defaultdict(lambda: defaultdict(list)), "ruta": defaultdict(lambda: defaultdict(list))}
    for carga in cargas:
        t = tiempos_de_la_carga(carga)
        claves = (
            ("repartidor", carga.conductor.nombre if carga.conductor_id else SIN_DATO),
            ("ruta", carga.ruta.nombre if carga.ruta_id else SIN_DATO),
        )
        for tipo, nombre in claves:
            a = acumulado[tipo][nombre]
            a["total"].append(t["total"])
            a["en_cliente"].extend(p["en_cliente"] for p in t["paradas"])
            a["viaje"].extend(p["viaje"] for p in t["paradas"][1:])
            if t["total"] and t["clientes_visitados"]:
                a["por_cliente"].append(t["total"] / t["clientes_visitados"])
    return {
        tipo: {
            nombre: {clave: _promedio(valores) for clave, valores in a.items()}
            for nombre, a in grupos.items()
        }
        for tipo, grupos in acumulado.items()
    }


def resumen_analisis(desde, hasta, ruta_id=None, repartidor_id=None):
    entregas = analisis_entregas(desde, hasta, ruta_id, repartidor_id)
    tiempos = tiempos_por_grupo(desde, hasta, ruta_id, repartidor_id)
    for tipo, filas in (("repartidor", entregas["por_repartidor"]), ("ruta", entregas["por_ruta"])):
        for fila in filas:
            fila["tiempos"] = tiempos[tipo].get(fila["nombre"], {})
    return {
        "entregas": entregas,
        "unidades": analisis_unidades(desde, hasta, ruta_id, repartidor_id),
    }
