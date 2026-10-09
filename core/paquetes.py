"""
Cambio #18: paquetes y costo por factura en las unidades que los piden.

Cuando la unidad (Vehículo) de una Carga tiene marcada la casilla "Pide
paquetes y costo" (por ejemplo M000003), el planeador debe escribir, al
planear, cuántos paquetes lleva cada factura y cuánto cuesta llevarla.
"""

from decimal import Decimal, InvalidOperation


def leer_paquetes_costo(datos, facturas):
    """
    Lee del POST los campos paq_<id> y cos_<id> de cada factura.

    Devuelve (valores, escritos, errores):
      - valores: {factura_id: (paquetes:int, costo:Decimal)} si todo es válido.
      - escritos: {factura_id: (texto_paquetes, texto_costo)} para volver a
        mostrar lo que se escribió.
      - errores: {factura_id: set("paq" / "cos")} con los campos vacíos o
        inválidos.
    """
    valores, escritos, errores = {}, {}, {}
    for f in facturas:
        paq_txt = (datos.get(f"paq_{f.id}") or "").strip()
        cos_txt = (datos.get(f"cos_{f.id}") or "").strip().replace(",", "")
        escritos[f.id] = (paq_txt, cos_txt)
        malos = set()
        paq = cos = None
        try:
            paq = int(paq_txt)
            if paq < 0:
                malos.add("paq")
        except ValueError:
            malos.add("paq")
        try:
            cos = Decimal(cos_txt).quantize(Decimal("0.01"))
            if cos < 0:
                malos.add("cos")
        except (InvalidOperation, ValueError):
            malos.add("cos")
        if malos:
            errores[f.id] = malos
        else:
            valores[f.id] = (paq, cos)
    return valores, escritos, errores


def facturas_sin_paquetes_costo(carga, solo_cargadas=False):
    """Facturas activas de una carga que pide paquetes y costo y les falta alguno."""
    if not carga.pide_paquetes_costo:
        return []
    qs = carga.facturas.filter(estado_factura="ACTIVA")
    if solo_cargadas:
        qs = qs.filter(cargada_en__isnull=False)
    return [f for f in qs.order_by("numero_factura") if f.falta_paquetes_costo]
