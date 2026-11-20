"""
Cambio #33: consulta de cartera "Recaudación por ruta".

Sirve a cartera para estimar la recaudación y los pagos de un período, en
total o por ruta. Filtros: una o varias rutas, rango de fechas (de emisión)
y uno o varios estados de entrega. Arriba muestra los totales (cantidad de
facturas y C$) generales, por estado de entrega y por ruta, siempre
separados entre contado y crédito. Abajo, el detalle de facturas con las
columnas de la consulta de ventas más: contado/crédito, tipo de pago
recibido y documento asociado.

- "Crédito" agrupa las modalidades que no son de contado (crédito y
  consignación).
- Las facturas anuladas se listan si se filtran, pero no suman en ningún
  total.
- Por cobrar = el saldo completo de cada factura (el de la cartera si ya
  existe; si no, el total menos lo devuelto).
"""

from collections import OrderedDict
from decimal import Decimal
from io import BytesIO

from django.db.models import Prefetch, Q
from django.utils import timezone

from core.consulta import ESTADOS, MAX_FILAS_LISTADO, _base, _r, anotar
from core.models import Pago

CERO = Decimal("0")
CONTADO = "CONTADO"
CREDITO = "CREDITO"
NOMBRE_MODALIDAD = {CONTADO: "Contado", CREDITO: "Crédito"}
ESTADOS_POR_DEFECTO = [e for e in ESTADOS if e != "ANULADA"]
SIN_RUTA = "Sin ruta"


def modalidad_de(factura):
    codigo = (factura.modalidad_pago.codigo or "").upper()
    return CONTADO if codigo == CONTADO else CREDITO


def ruta_de(factura):
    """La ruta de la carga en la que va la factura; si ya no tiene carga, la de su última visita."""
    if factura.carga_id and factura.carga.ruta_id:
        return factura.carga.ruta
    for e in reversed(getattr(factura, "entregas_confirmadas", [])):
        if e.carga_id and e.carga.ruta_id:
            return e.carga.ruta
    return None


def _documento(pago):
    ref = (pago.referencia or "").strip()
    recibo = (pago.numero_recibo or "").strip()
    if pago.forma_pago == "EFECTIVO":
        return f"Recibo {recibo}" if recibo else "Sin recibo"
    if pago.forma_pago == "NOTA_CREDITO":
        return f"NC {ref}" if ref else "Nota de crédito"
    if pago.forma_pago == "CHEQUE":
        return f"Cheque {ref}" if ref else "Cheque"
    return f"Ref. {ref}" if ref else pago.get_forma_pago_display()


def _anotar_cobro(f):
    """Tipo de pago recibido, documento asociado y lo cobrado."""
    pagos = []
    cartera = getattr(f, "cartera", None) if _tiene_cartera(f) else None
    if cartera is not None:
        pagos = list(cartera.pagos.all())
    f.modalidad_cr = modalidad_de(f)
    f.modalidad_cr_nombre = NOMBRE_MODALIDAD[f.modalidad_cr]
    f.cobrado = _r(sum((p.monto for p in pagos), CERO))
    formas = []
    for p in pagos:
        nombre = p.get_forma_pago_display()
        if nombre not in formas:
            formas.append(nombre)
    f.tipos_pago = " + ".join(formas)
    f.documentos = " · ".join(_documento(p) for p in pagos)
    f.pagos_lista = pagos
    if not pagos:
        if f.modalidad_cr == CREDITO and f.fecha_vencimiento:
            f.documento_nota = f"Vence {f.fecha_vencimiento:%d/%m/%Y}"
        elif f.estado == "ANULADA":
            f.documento_nota = ""
        else:
            f.documento_nota = "Sin pago aún" if f.modalidad_cr == CREDITO else "Sin cobrar"
    else:
        f.documento_nota = ""


def _tiene_cartera(f):
    try:
        f.cartera
    except type(f).cartera.RelatedObjectDoesNotExist:
        return False
    return True


def _bolsa():
    return {
        "contado_n": 0, "contado_monto": CERO, "contado_cobrado": CERO, "contado_saldo": CERO,
        "credito_n": 0, "credito_monto": CERO, "credito_cobrado": CERO, "credito_saldo": CERO,
    }


def _sumar(bolsa, f):
    pre = "contado" if f.modalidad_cr == CONTADO else "credito"
    bolsa[f"{pre}_n"] += 1
    bolsa[f"{pre}_monto"] += f.total
    bolsa[f"{pre}_cobrado"] += f.cobrado
    bolsa[f"{pre}_saldo"] += f.saldo or CERO


def _cerrar(bolsa, **extra):
    for k in list(bolsa):
        if not k.endswith("_n"):
            bolsa[k] = _r(bolsa[k])
    bolsa["total_n"] = bolsa["contado_n"] + bolsa["credito_n"]
    bolsa["total_monto"] = _r(bolsa["contado_monto"] + bolsa["credito_monto"])
    bolsa["total_cobrado"] = _r(bolsa["contado_cobrado"] + bolsa["credito_cobrado"])
    bolsa["total_saldo"] = _r(bolsa["contado_saldo"] + bolsa["credito_saldo"])
    bolsa.update(extra)
    return bolsa


def consulta(desde, hasta, rutas_ids=None, estados=None):
    """
    Devuelve un dict con: facturas (detalle), totales (generales),
    por_estado, por_ruta, anuladas (cuántas se listan sin sumar) y recortado.
    """
    from core.models import Entrega

    rutas_ids = [r for r in (rutas_ids or []) if r]
    estados = [e for e in (estados or []) if e in ESTADOS] or list(ESTADOS_POR_DEFECTO)

    qs = (
        _base()
        .filter(fecha_emision__gte=desde, fecha_emision__lte=hasta)
        .prefetch_related(
            Prefetch("cartera__pagos", queryset=Pago.objects.order_by("fecha_pago", "id")),
            Prefetch(
                "entregas",
                queryset=Entrega.objects.filter(estado="CONFIRMADA")
                .select_related("carga__ruta")
                .order_by("fecha_entrega", "id"),
                to_attr="entregas_confirmadas",
            ),
        )
    )
    if rutas_ids:
        qs = qs.filter(Q(carga__ruta_id__in=rutas_ids) | Q(entregas__carga__ruta_id__in=rutas_ids)).distinct()
    qs = qs.order_by("-fecha_emision", "-numero_factura")

    filas = list(qs[: MAX_FILAS_LISTADO + 1])
    recortado = len(filas) > MAX_FILAS_LISTADO
    filas = filas[:MAX_FILAS_LISTADO]
    for f in filas:
        anotar(f)
    filas = [f for f in filas if f.estado in estados]
    for f in filas:
        _anotar_cobro(f)
        ruta = ruta_de(f)
        f.ruta_obj = ruta
        f.ruta_nombre = ruta.nombre if ruta else SIN_RUTA

    activas = [f for f in filas if f.estado != "ANULADA"]
    totales = _bolsa()
    por_estado = OrderedDict((e, _bolsa()) for e in ESTADOS if e in estados and e != "ANULADA")
    por_ruta = {}
    for f in activas:
        _sumar(totales, f)
        _sumar(por_estado[f.estado], f)
        _sumar(por_ruta.setdefault(f.ruta_nombre, _bolsa()), f)

    return {
        "facturas": filas,
        "totales": _cerrar(totales),
        "por_estado": [
            _cerrar(b, codigo=e, nombre=ESTADOS[e][0], css=ESTADOS[e][1])
            for e, b in por_estado.items()
        ],
        "por_ruta": [
            _cerrar(por_ruta[n], nombre=n)
            for n in sorted(por_ruta, key=lambda n: (n == SIN_RUTA, n.lower()))
        ],
        "anuladas": len(filas) - len(activas),
        "recortado": recortado,
        "estados_elegidos": estados,
        "rutas_elegidas": rutas_ids,
    }


def excel(datos, titulo):
    """Archivo .xlsx (bytes): hoja "Facturas" con el detalle y hoja "Totales"."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    azul = PatternFill("solid", fgColor="2C318C")
    blanca = Font(bold=True, color="FFFFFF")

    def encabezar(hoja, fila):
        for celda in hoja[fila]:
            if celda.value is not None:
                celda.font = blanca
                celda.fill = azul
                celda.alignment = Alignment(vertical="center")

    libro = Workbook()
    hoja = libro.active
    hoja.title = "Facturas"
    hoja.append([titulo])
    hoja["A1"].font = Font(bold=True, size=13)
    hoja.append([])
    cols = [
        "Factura", "Emitida", "Cliente", "Vendedor", "Sucursal", "Ruta", "Carga", "Repartidor",
        "Contado / crédito", "Modalidad", "Estado", "Total", "Devuelto", "Cobrado", "Saldo",
        "Tipo de pago recibido", "Documento asociado",
    ]
    hoja.append(cols)
    encabezar(hoja, 3)
    for f in datos["facturas"]:
        carga = f.carga
        hoja.append(
            [
                f.numero_factura,
                f.fecha_emision,
                f.cliente.nombre,
                f.vendedor.nombre if f.vendedor_id else "",
                f.sucursal.nombre if f.sucursal_id else f.cliente.sucursal,
                f.ruta_nombre,
                carga.codigo_reporte if carga else "",
                carga.conductor.nombre if carga and carga.conductor_id else "",
                f.modalidad_cr_nombre,
                f.modalidad_pago.nombre,
                f.estado_nombre,
                float(f.total),
                float(f.valor_devuelto or 0),
                float(f.cobrado),
                float(f.saldo) if f.saldo is not None else None,
                f.tipos_pago,
                f.documentos or f.documento_nota,
            ]
        )
    for fila in hoja.iter_rows(min_row=4):
        fila[1].number_format = "DD/MM/YYYY"
        for i in (11, 12, 13, 14):
            fila[i].number_format = "#,##0.00"
    anchos = [12, 11, 32, 22, 22, 12, 12, 22, 12, 14, 16, 13, 12, 12, 13, 26, 30]
    for i, ancho in enumerate(anchos, start=1):
        hoja.column_dimensions[get_column_letter(i)].width = ancho
    hoja.freeze_panes = "A4"
    hoja.auto_filter.ref = f"A3:{get_column_letter(len(cols))}{max(hoja.max_row, 3)}"

    t = libro.create_sheet("Totales")
    t.append([titulo])
    t["A1"].font = Font(bold=True, size=13)
    cab = ["", "Contado #", "Contado C$", "Crédito #", "Crédito C$", "Total #", "Total C$", "Cobrado C$", "Por cobrar C$"]

    def bloque(nombre, filas):
        t.append([])
        t.append([nombre])
        t.cell(row=t.max_row, column=1).font = Font(bold=True, size=12, color="2C318C")
        t.append(cab)
        encabezar(t, t.max_row)
        for etiqueta, b in filas:
            t.append(
                [
                    etiqueta, b["contado_n"], float(b["contado_monto"]), b["credito_n"],
                    float(b["credito_monto"]), b["total_n"], float(b["total_monto"]),
                    float(b["total_cobrado"]), float(b["total_saldo"]),
                ]
            )
            for i in (3, 5, 7, 8, 9):
                t.cell(row=t.max_row, column=i).number_format = "#,##0.00"

    tot = datos["totales"]
    bloque("Totales generales", [("Total", tot)])
    bloque("Por estado de entrega", [(b["nombre"], b) for b in datos["por_estado"]] + [("Total", tot)])
    bloque("Por ruta", [(b["nombre"], b) for b in datos["por_ruta"]] + [("Total", tot)])
    t.column_dimensions["A"].width = 22
    for letra in "BCDEFGHI":
        t.column_dimensions[letra].width = 14

    salida = BytesIO()
    libro.save(salida)
    return salida.getvalue()
