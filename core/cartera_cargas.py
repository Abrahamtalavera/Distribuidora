"""
Cambio #36: consulta de cartera "Consulta de cargas" (reemplaza la
"Recaudación por ruta" del Cambio #33).

Cartera filtra Cargas por fecha planeada, una o varias rutas y uno o varios
estados de la ruta, y ve:

- Sumas: facturas (cantidad, monto, cobrado y sin cobrar), lo mismo para
  efectivo (= contado) y para crédito, y cuántas facturas van con
  devolución, no entregadas y sin visitar.
- Resumen por ruta (se despliega si se quiere ver): contado y crédito en
  cantidad y C$, total y por cobrar.
- Cada carga: facturas, completas, con devolución, no entregadas, monto,
  total en efectivo, total en crédito, total pagado recibido y % de avance.
- Al desplegar una carga, sus facturas: código y nombre del cliente,
  estado, contado o crédito, monto, monto pagado y documento asociado al
  pago.

"Crédito" agrupa las modalidades que no son de contado (crédito y
consignación). Solo lee: no cambia nada. El estado de cada factura sale de
core.cierre.calcular_resultado, igual que en la consulta de cargas del
tablero, para que los números coincidan.
"""

from collections import OrderedDict, defaultdict
from io import BytesIO

from django.db.models import Prefetch

from core.cierre import calcular_resultado
from core.consulta_cargas import (
    _BUCKET,
    _CSS_BUCKET,
    _NOMBRE_BUCKET,
    CERO,
    MAX_CARGAS_LISTADO,
    _r,
)
from core.models import Factura, Pago

CONTADO = "CONTADO"
CREDITO = "CREDITO"
NOMBRE_MODALIDAD = {CONTADO: "Contado", CREDITO: "Crédito"}
SIN_RUTA = "Sin ruta"


def modalidad_de(factura):
    codigo = (factura.modalidad_pago.codigo or "").upper()
    return CONTADO if codigo == CONTADO else CREDITO


def documento_de_pago(pago):
    ref = (pago.referencia or "").strip()
    recibo = (pago.numero_recibo or "").strip()
    if pago.forma_pago == "EFECTIVO":
        texto = f"Recibo {recibo}" if recibo else "Sin recibo"
    elif pago.forma_pago == "NOTA_CREDITO":
        texto = f"NC {ref}" if ref else "Nota de crédito"
    elif pago.forma_pago == "CHEQUE":
        texto = f"Cheque {ref}" if ref else "Cheque"
    else:
        texto = f"Ref. {ref}" if ref else ""
    forma = pago.get_forma_pago_display().lower()
    return f"{texto} ({forma})" if texto else pago.get_forma_pago_display()


def _cartera_de(factura):
    try:
        return factura.cartera
    except type(factura).cartera.RelatedObjectDoesNotExist:
        return None


def _bolsa():
    return {
        "contado_n": 0, "contado_monto": CERO, "contado_cobrado": CERO, "contado_sin": CERO,
        "credito_n": 0, "credito_monto": CERO, "credito_cobrado": CERO, "credito_sin": CERO,
        "devolucion": 0, "no_entregada": 0, "sin_visitar": 0,
    }


def _sumar(bolsa, f):
    pre = "contado" if f["modalidad"] == CONTADO else "credito"
    bolsa[f"{pre}_n"] += 1
    bolsa[f"{pre}_monto"] += f["monto"]
    bolsa[f"{pre}_cobrado"] += f["pagado"]
    bolsa[f"{pre}_sin"] += f["sin_cobrar"]
    if f["bucket"] in ("devolucion", "no_entregada", "sin_visitar"):
        bolsa[f["bucket"]] += 1


def _cerrar(bolsa, **extra):
    for k in list(bolsa):
        if isinstance(bolsa[k], type(CERO)):
            bolsa[k] = _r(bolsa[k])
    bolsa["total_n"] = bolsa["contado_n"] + bolsa["credito_n"]
    bolsa["total_monto"] = _r(bolsa["contado_monto"] + bolsa["credito_monto"])
    bolsa["total_cobrado"] = _r(bolsa["contado_cobrado"] + bolsa["credito_cobrado"])
    bolsa["total_sin"] = _r(bolsa["contado_sin"] + bolsa["credito_sin"])
    bolsa.update(extra)
    return bolsa


def _detalle_factura(f):
    resultado, _valor_ent, valor_dev = calcular_resultado(list(f.lineas.all()))
    bucket = _BUCKET[resultado]
    cartera = _cartera_de(f)
    pagos = list(cartera.pagos.all()) if cartera is not None else []
    pagado = _r(sum((p.monto for p in pagos), CERO))
    if cartera is not None:
        sin_cobrar = _r(cartera.saldo_pendiente)
    else:
        sin_cobrar = _r(max(f.total - (valor_dev or CERO) - pagado, CERO))
    modalidad = modalidad_de(f)
    documentos = " · ".join(documento_de_pago(p) for p in pagos)
    if not documentos:
        if modalidad == CREDITO and f.fecha_vencimiento:
            documentos = f"Vence {f.fecha_vencimiento:%d/%m/%Y}"
        else:
            documentos = "—"
    return {
        "pk": f.pk,
        "numero_factura": f.numero_factura,
        "cliente_codigo": f.cliente.codigo,
        "cliente_nombre": f.cliente.nombre,
        "bucket": bucket,
        "estado_nombre": _NOMBRE_BUCKET[bucket],
        "css": _CSS_BUCKET[bucket],
        "modalidad": modalidad,
        "modalidad_nombre": NOMBRE_MODALIDAD[modalidad],
        "monto": _r(f.total),
        "pagado": pagado,
        "sin_cobrar": sin_cobrar,
        "documento": documentos,
        "con_pago": bool(pagos),
    }


def consulta(desde, hasta, rutas_ids=None, estados=None):
    """
    Devuelve un dict con: cargas (cada una con sus totales y
    .detalle_facturas), totales (las sumas del encabezado), por_ruta,
    recortado, rutas_elegidas y estados_elegidos.
    """
    from core.models import Carga

    validos = dict(Carga.ESTADO_CHOICES)
    rutas_ids = [r for r in (rutas_ids or []) if r]
    estados = [e for e in (estados or []) if e in validos]

    qs = Carga.objects.filter(fecha_planeada__gte=desde, fecha_planeada__lte=hasta)
    if rutas_ids:
        qs = qs.filter(ruta_id__in=rutas_ids)
    if estados:
        qs = qs.filter(estado__in=estados)
    qs = qs.select_related("ruta", "conductor").order_by("-fecha_planeada", "-id")
    recortado = qs.count() > MAX_CARGAS_LISTADO
    cargas = list(qs[:MAX_CARGAS_LISTADO])

    facturas = (
        Factura.objects.filter(carga_id__in=[c.id for c in cargas], estado_factura="ACTIVA")
        .select_related("cliente", "modalidad_pago", "cartera")
        .prefetch_related(
            "lineas",
            Prefetch("cartera__pagos", queryset=Pago.objects.order_by("fecha_pago", "id")),
        )
        .order_by("numero_factura")
    )
    por_carga = defaultdict(list)
    for f in facturas:
        por_carga[f.carga_id].append(_detalle_factura(f))

    totales = _bolsa()
    por_ruta = OrderedDict()
    for c in cargas:
        detalle = por_carga.get(c.id, [])
        b = _bolsa()
        for f in detalle:
            _sumar(b, f)
            _sumar(totales, f)
            _sumar(por_ruta.setdefault(c.ruta.nombre if c.ruta_id else SIN_RUTA, _bolsa()), f)
        _cerrar(b)
        c.detalle_facturas = detalle
        c.total_facturas = len(detalle)
        c.completa = sum(1 for f in detalle if f["bucket"] == "completa")
        c.devolucion = b["devolucion"]
        c.no_entregada = b["no_entregada"]
        c.sin_visitar = b["sin_visitar"]
        c.monto = b["total_monto"]
        c.total_efectivo = b["contado_monto"]
        c.total_credito = b["credito_monto"]
        c.total_pagado = b["total_cobrado"]
        c.avance = (
            round((c.completa + c.devolucion) * 100 / c.total_facturas)
            if c.total_facturas
            else None
        )

    return {
        "cargas": cargas,
        "totales": _cerrar(totales, cargas=len(cargas)),
        "por_ruta": [
            _cerrar(por_ruta[n], nombre=n)
            for n in sorted(por_ruta, key=lambda n: (n == SIN_RUTA, n.lower()))
        ],
        "recortado": recortado,
        "rutas_elegidas": rutas_ids,
        "estados_elegidos": estados,
    }


def excel(datos, titulo):
    """Archivo .xlsx (bytes): hojas "Cargas", "Facturas" y "Resumen por ruta"."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    azul = PatternFill("solid", fgColor="2C318C")
    blanca = Font(bold=True, color="FFFFFF")

    def hoja_con(libro, nombre, columnas, filas, monedas, anchos, primera=False):
        hoja = libro.active if primera else libro.create_sheet(nombre)
        hoja.title = nombre
        hoja.append([titulo])
        hoja["A1"].font = Font(bold=True, size=13)
        hoja.append([])
        hoja.append(columnas)
        for celda in hoja[3]:
            celda.font = blanca
            celda.fill = azul
            celda.alignment = Alignment(vertical="center", wrap_text=True)
        for fila in filas:
            hoja.append(fila)
        for fila in hoja.iter_rows(min_row=4):
            for i in monedas:
                fila[i].number_format = "#,##0.00"
        for i, ancho in enumerate(anchos, start=1):
            hoja.column_dimensions[get_column_letter(i)].width = ancho
        hoja.freeze_panes = "A4"
        hoja.auto_filter.ref = f"A3:{get_column_letter(len(columnas))}{max(hoja.max_row, 3)}"
        return hoja

    libro = Workbook()
    cargas = datos["cargas"]
    hoja_con(
        libro, "Cargas",
        ["ID de carga", "Fecha planeada", "Ruta", "Conductor", "Estado de la ruta", "Total facturas",
         "Completa", "Con devolución", "No entregada", "Sin visitar", "Monto total", "Total en efectivo",
         "Total en crédito", "Total pagado recibido", "% avance"],
        [
            [c.codigo_reporte, c.fecha_planeada, c.ruta.nombre if c.ruta_id else "",
             c.conductor.nombre if c.conductor_id else "", c.get_estado_display(), c.total_facturas,
             c.completa, c.devolucion, c.no_entregada, c.sin_visitar, float(c.monto),
             float(c.total_efectivo), float(c.total_credito), float(c.total_pagado),
             (c.avance / 100) if c.avance is not None else None]
            for c in cargas
        ],
        (10, 11, 12, 13), [12, 12, 12, 26, 15, 10, 10, 12, 12, 10, 14, 14, 14, 16, 10], primera=True,
    )
    for fila in libro["Cargas"].iter_rows(min_row=4):
        fila[1].number_format = "DD/MM/YYYY"
        fila[14].number_format = "0%"
    hoja_con(
        libro, "Facturas",
        ["ID de carga", "Ruta", "No. factura", "Código cliente", "Nombre cliente", "Estado de factura",
         "Contado o crédito", "Monto de factura", "Monto pagado", "Sin cobrar", "Documento asociado al pago"],
        [
            [c.codigo_reporte, c.ruta.nombre if c.ruta_id else "", f["numero_factura"], f["cliente_codigo"],
             f["cliente_nombre"], f["estado_nombre"], f["modalidad_nombre"], float(f["monto"]),
             float(f["pagado"]), float(f["sin_cobrar"]), f["documento"]]
            for c in cargas for f in c.detalle_facturas
        ],
        (7, 8, 9), [12, 10, 12, 12, 34, 15, 14, 14, 13, 13, 36],
    )
    tot = datos["totales"]
    hoja_con(
        libro, "Resumen por ruta",
        ["Ruta", "Contado (cantidad)", "Contado C$", "Crédito (cantidad)", "Crédito C$",
         "Total cantidad", "Total C$", "Por cobrar C$"],
        [
            [g["nombre"], g["contado_n"], float(g["contado_monto"]), g["credito_n"],
             float(g["credito_monto"]), g["total_n"], float(g["total_monto"]), float(g["total_sin"])]
            for g in datos["por_ruta"]
        ]
        + [["Total", tot["contado_n"], float(tot["contado_monto"]), tot["credito_n"],
            float(tot["credito_monto"]), tot["total_n"], float(tot["total_monto"]), float(tot["total_sin"])]],
        (2, 4, 6, 7), [16, 12, 14, 12, 14, 12, 14, 14],
    )
    salida = BytesIO()
    libro.save(salida)
    return salida.getvalue()
