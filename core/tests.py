from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Carga,
    CargaCuadre,
    CarteraCobro,
    Cliente,
    ContactoSucursal,
    DevolucionSinFactura,
    DevolucionSinFacturaDetalle,
    Entrega,
    EntregaDetalle,
    Factura,
    FacturaDetalle,
    ModalidadPago,
    MotivoDevolucion,
    Pago,
    Producto,
    Ruta,
    Sucursal,
    Vehiculo,
    Vendedor,
)
from core.tablero import resumen_tablero


class TableroBase(TestCase):
    """Datos mínimos para el tablero del Cambio #19."""

    def setUp(self):
        self.hoy = timezone.localdate()
        self.contado = ModalidadPago.objects.get(codigo="CONTADO")
        self.credito = ModalidadPago.objects.get(codigo="CREDITO")
        self.cliente = Cliente.objects.create(codigo="C1", nombre="Pulpería Uno", sucursal="Managua")
        self.producto = Producto.objects.create(codigo="P1", nombre="Producto 1")

    def factura(self, numero, total="115.00", **campos):
        campos.setdefault("fecha_emision", self.hoy)
        campos.setdefault("modalidad_pago", self.contado)
        f = Factura.objects.create(
            numero_factura=numero, cliente=self.cliente, total=Decimal(total), **campos
        )
        FacturaDetalle.objects.create(
            factura=f,
            producto=self.producto,
            cantidad_facturada=Decimal("10"),
            precio_unitario=Decimal("10"),
            subtotal=Decimal("100"),
            iva=Decimal("15"),
        )
        return f


class ResumenTableroTests(TableroBase):
    def test_tablero_vacio_no_falla(self):
        r = resumen_tablero()
        self.assertEqual(r["por_entregar"]["total"]["n"], 0)
        self.assertEqual(r["alertas_activas"], 0)

    def test_por_entregar_separa_por_donde_esta(self):
        planeada = Carga.objects.create(estado="PLANEADA")
        en_ruta = Carga.objects.create(estado="EN_RUTA")
        self.factura("F1")
        self.factura("F2", carga=planeada)
        self.factura("F3", carga=en_ruta)
        self.factura("F4", estado_entrega="COMPLETA")
        self.factura("F5", estado_factura="ANULADA")
        p = resumen_tablero()["por_entregar"]
        self.assertEqual(p["total"]["n"], 3)
        self.assertEqual(p["total"]["monto"], Decimal("345.00"))
        self.assertEqual(p["sin_carga"]["n"], 1)
        self.assertEqual(p["en_carga_planeada"]["n"], 1)
        self.assertEqual(p["en_ruta"]["n"], 1)

    def test_entregado_y_efectividad_del_dia(self):
        carga = Carga.objects.create(estado="EN_RUTA")
        f = self.factura("F1", carga=carga)
        entrega = Entrega.objects.create(factura=f, carga=carga, tipo_entrega="TOTAL")
        EntregaDetalle.objects.create(
            entrega=entrega,
            factura_detalle=f.lineas.first(),
            cantidad_entregada=Decimal("8"),
            cantidad_devuelta=Decimal("2"),
        )
        e = resumen_tablero()["entregado"]
        self.assertEqual(e["valor_entregado"], Decimal("92.00"))  # 8 x 11.50
        self.assertEqual(e["valor_devuelto"], Decimal("23.00"))
        self.assertEqual(e["facturas_visitadas"], 1)
        self.assertEqual(e["pct_efectividad"], Decimal("80.0"))

    def test_cobrado_no_suma_notas_de_credito_como_dinero(self):
        f = self.factura("F1", modalidad_pago=self.credito)
        cartera = CarteraCobro.objects.create(
            factura=f, modalidad_pago=self.credito, monto_total=f.total, saldo_pendiente=0
        )
        Pago.objects.create(cartera=cartera, monto=Decimal("100"), forma_pago="EFECTIVO")
        Pago.objects.create(cartera=cartera, monto=Decimal("15"), forma_pago="NOTA_CREDITO")
        c = resumen_tablero()["cobrado"]
        self.assertEqual(c["total_dinero"], Decimal("100.00"))
        self.assertEqual(len(c["formas"]), 2)

    def test_alertas(self):
        ayer = self.hoy - timedelta(days=1)
        self.factura("ATR", fecha_sugerida_entrega=self.hoy - timedelta(days=3))
        self.factura("A_TIEMPO", fecha_sugerida_entrega=self.hoy)
        Carga.objects.create(estado="RUTA_TERMINADA", ruta_terminada_en=timezone.now())
        Carga.objects.create(estado="EN_RUTA", fecha_planeada=ayer)
        Carga.objects.create(estado="EN_RUTA", fecha_planeada=self.hoy)  # no es alerta
        Carga.objects.create(estado="PLANEADA", fecha_planeada=ayer)
        cerrada = Carga.objects.create(estado="CERRADA", cerrada_en=timezone.now())
        CargaCuadre.objects.create(
            carga=cerrada, forma_pago="EFECTIVO", segun_sistema=Decimal("500"), recibido=Decimal("480")
        )
        vencida = self.factura(
            "CRED", modalidad_pago=self.credito, estado_entrega="COMPLETA",
            fecha_vencimiento=self.hoy - timedelta(days=10),
        )
        CarteraCobro.objects.create(
            factura=vencida, modalidad_pago=self.credito,
            monto_total=vencida.total, saldo_pendiente=Decimal("115"),
        )

        r = resumen_tablero()
        alertas = {a["clave"]: a for a in r["alertas"]}
        self.assertEqual(alertas["facturas_atrasadas"]["total"], 1)
        self.assertEqual(alertas["facturas_atrasadas"]["filas"][0].dias_atraso, 3)
        self.assertEqual(alertas["cargas_sin_cierre"]["total"], 1)
        self.assertEqual(alertas["cargas_en_ruta_atrasadas"]["total"], 1)
        self.assertEqual(alertas["cargas_planeadas_atrasadas"]["total"], 1)
        self.assertEqual(alertas["diferencias_cuadre"]["total"], 1)
        self.assertEqual(alertas["diferencias_cuadre"]["monto"], Decimal("-20.00"))
        self.assertEqual(alertas["cartera_vencida"]["total"], 1)
        self.assertEqual(alertas["cartera_vencida"]["monto"], Decimal("115.00"))
        self.assertEqual(r["cuadre"]["cargas_con_diferencia"], 1)


class TableroAccesoTests(TableroBase):
    def usuario(self, nombre, grupo=None, staff=False):
        u = User.objects.create_user(nombre, password="clave-123", is_staff=staff)
        if grupo:
            u.groups.add(Group.objects.get(name=grupo))
        return u

    def test_sin_sesion_va_al_acceso(self):
        r = self.client.get(reverse("tablero"))
        self.assertRedirects(r, f"{reverse('tablero_acceso')}?next={reverse('tablero')}")

    def test_usuario_sin_grupo_no_entra(self):
        self.client.force_login(self.usuario("vendedor"))
        r = self.client.get(reverse("tablero"))
        self.assertEqual(r.status_code, 403)

    def test_supervisor_entra_sin_ser_staff(self):
        self.client.force_login(self.usuario("supervisor", "Supervisores"))
        self.factura("F1", fecha_sugerida_entrega=self.hoy - timedelta(days=2))
        r = self.client.get(reverse("tablero"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Facturas atrasadas sin carga")
        # No es staff: no ve enlaces al admin.
        self.assertNotContains(r, reverse("admin:index"))

    def test_administracion_ve_enlaces_al_admin(self):
        self.client.force_login(self.usuario("admin2", "Administración", staff=True))
        r = self.client.get(reverse("tablero"), {"dia": "2026-01-05"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, reverse("admin:index"))
        self.assertEqual(r.context["dia"].isoformat(), "2026-01-05")

    def test_dia_mal_escrito_usa_hoy(self):
        self.client.force_login(self.usuario("supervisor", "Supervisores"))
        r = self.client.get(reverse("tablero"), {"dia": "ayer"})
        self.assertEqual(r.context["dia"], self.hoy)

    def test_admin_muestra_acceso_al_tablero(self):
        self.client.force_login(self.usuario("admin2", "Administración", staff=True))
        r = self.client.get(reverse("admin:index"))
        self.assertContains(r, "Abrir tablero")


class AnalisisTests(TableroBase):
    """Cambio #20: análisis de un período."""

    def setUp(self):
        super().setUp()
        from core.models import AreaResponsable, MotivoDevolucion, Ruta, Vehiculo, Vendedor

        self.area = AreaResponsable.objects.create(nombre="Área prueba")
        self.motivo = MotivoDevolucion.objects.create(nombre="Motivo prueba", area_responsable=self.area)
        self.ruta_a = Ruta.objects.create(nombre="Ruta A")
        self.ruta_b = Ruta.objects.create(nombre="Ruta B")
        self.juan = Vendedor.objects.create(nombre="Juan", tipo="REPARTIDOR")
        self.luis = Vendedor.objects.create(nombre="Luis", tipo="REPARTIDOR")
        self.camion = Vehiculo.objects.create(placa="M-PRUEBA")

    def visita(self, numero, carga, entregada, devuelta, repartidor=None, motivo=None):
        f = self.factura(numero, carga=carga)
        e = Entrega.objects.create(factura=f, carga=carga, tipo_entrega="TOTAL", repartidor=repartidor)
        EntregaDetalle.objects.create(
            entrega=e, factura_detalle=f.lineas.first(),
            cantidad_entregada=Decimal(entregada), cantidad_devuelta=Decimal(devuelta),
            motivo_devolucion=motivo,
        )
        return f

    def test_devoluciones_desempeno_y_filtros(self):
        from core.analisis import resumen_analisis

        ahora = timezone.now()
        ca = Carga.objects.create(
            ruta=self.ruta_a, conductor=self.juan, vehiculo=self.camion, estado="CERRADA",
            km_inicial=Decimal("1000"), km_final=Decimal("1080"),
            salio_a_ruta_en=ahora - timedelta(hours=6), ruta_terminada_en=ahora,
        )
        cb = Carga.objects.create(ruta=self.ruta_b, conductor=self.luis, estado="EN_RUTA")
        fa = self.visita("A1", ca, "8", "2", motivo=self.motivo)  # repartidor sale del conductor
        fa.paquetes, fa.costo_flete = 4, Decimal("200")
        fa.save()
        self.visita("B1", cb, "10", "0", repartidor=self.luis)

        r = resumen_analisis(self.hoy, self.hoy)
        e = r["entregas"]
        self.assertEqual(e["totales"]["facturas"], 2)
        self.assertEqual(e["totales"]["entregado"], Decimal("207.00"))  # 92 + 115
        self.assertEqual(e["totales"]["devuelto"], Decimal("23.00"))
        self.assertEqual(e["por_motivo"][0]["nombre"], "Motivo prueba")
        self.assertEqual(e["por_area"][0]["nombre"], "Área prueba")
        repartidores = {f["nombre"]: f for f in e["por_repartidor"]}
        self.assertEqual(repartidores["Juan"]["efectividad"], Decimal("80.0"))
        self.assertEqual(repartidores["Luis"]["efectividad"], Decimal("100.0"))
        self.assertEqual(e["top_clientes"][0]["valor"], Decimal("23.00"))

        unidades = {u["nombre"]: u for u in r["unidades"]}
        self.assertEqual(unidades["M-PRUEBA"]["km"], Decimal("80"))
        self.assertEqual(unidades["M-PRUEBA"]["horas_promedio"], 6.0)
        self.assertEqual(unidades["M-PRUEBA"]["costo_paquete"], Decimal("50.00"))

        solo_b = resumen_analisis(self.hoy, self.hoy, ruta_id=self.ruta_b.id)["entregas"]
        self.assertEqual(solo_b["totales"]["facturas"], 1)
        solo_juan = resumen_analisis(self.hoy, self.hoy, repartidor_id=self.juan.id)["entregas"]
        self.assertEqual(solo_juan["totales"]["facturas"], 1)

        fuera = resumen_analisis(self.hoy - timedelta(days=10), self.hoy - timedelta(days=5))
        self.assertEqual(fuera["entregas"]["totales"]["facturas"], 0)

    def test_pantalla_analisis(self):
        u = User.objects.create_user("sup", password="x")
        u.groups.add(Group.objects.get(name="Supervisores"))
        self.client.force_login(u)
        r = self.client.get(reverse("tablero_analisis"), {"desde": "2026-02-01", "hasta": "2026-01-01", "ruta": "abc"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["desde"].isoformat(), "2026-01-01")  # se invierten
        self.assertIsNone(r.context["ruta_id"])
        self.assertContains(r, "Devoluciones por motivo")

    def test_vendedor_sin_permiso_no_ve_analisis(self):
        self.client.force_login(User.objects.create_user("vend", password="x"))
        self.assertEqual(self.client.get(reverse("tablero_analisis")).status_code, 403)


class ConsultaVentasTests(TableroBase):
    """Cambio #21: consulta del equipo de ventas."""

    def setUp(self):
        super().setUp()
        from core.models import AreaResponsable, MotivoDevolucion, Ruta, Vendedor

        self.motivo = MotivoDevolucion.objects.create(
            nombre="Vencido", area_responsable=AreaResponsable.objects.create(nombre="Bodega X")
        )
        self.ruta = Ruta.objects.create(nombre="Norte")
        self.otra_ruta = Ruta.objects.create(nombre="Sur")
        self.juan = Vendedor.objects.create(nombre="Juan", tipo="REPARTIDOR")
        self.vendedora = Vendedor.objects.create(nombre="María", tipo="VENDEDOR")
        self.vendedor_user = User.objects.create_user("maria", password="x")
        self.vendedor_user.groups.add(Group.objects.get(name="Ventas"))

    def entregada_con_devolucion(self):
        from core.entregas import registrar_entrega

        carga = Carga.objects.create(ruta=self.ruta, conductor=self.juan, estado="EN_RUTA",
                                     salio_a_ruta_en=timezone.now())
        f = self.factura("A-1042", carga=carga, vendedor=self.vendedora)
        linea = f.lineas.first()
        registrar_entrega(
            factura=f, repartidor=self.juan, carga=carga,
            cantidades_entregadas={linea.id: "8"},
            motivo_por_linea={linea.id: self.motivo.id},
            pagos=[{"monto": "92", "forma_pago": "EFECTIVO", "numero_recibo": "R-1"}],
            documento_devolucion="DV-1",
        )
        f.refresh_from_db()
        return f

    def test_estado_y_saldo(self):
        from core.consulta import anotar, obtener_factura

        f = self.entregada_con_devolucion()
        f = obtener_factura(f.pk)
        self.assertEqual(f.estado, "CON_DEVOLUCION")
        self.assertEqual(f.valor_devuelto, Decimal("23.00"))
        self.assertEqual(f.saldo, Decimal("0.00"))  # 115 - 23 devuelto - 92 pagado

        sin_carga = obtener_factura(self.factura("A-2000").pk)
        self.assertEqual(sin_carga.estado, "POR_ENTREGAR")
        self.assertEqual(sin_carga.saldo, Decimal("115.00"))
        anulada = obtener_factura(self.factura("A-2001", estado_factura="ANULADA").pk)
        self.assertEqual(anulada.estado, "ANULADA")
        self.assertIsNone(anulada.saldo)

    def test_detalle_de_factura(self):
        from core.consulta import detalle_factura, obtener_factura

        f = obtener_factura(self.entregada_con_devolucion().pk)
        d = detalle_factura(f)
        self.assertEqual(len(d["entregas"]), 1)
        self.assertEqual(d["productos"][0]["devuelto"], Decimal("2"))
        self.assertIn("Vencido (Bodega X)", d["productos"][0]["motivo"])
        self.assertEqual(d["productos"][0]["bodega"], "pendiente de recibir")
        self.assertEqual(d["total_pagado"], Decimal("92.00"))
        titulos = [p["titulo"] for p in d["pasos"] if p["hecho"]]
        self.assertIn("Visitada", titulos)
        self.assertIn("Salió a ruta", titulos)

    def test_busqueda(self):
        from core.consulta import buscar_facturas

        self.factura("A-1042")
        self.factura("A-1043")
        exacta, _ = buscar_facturas("a-1042")
        self.assertEqual(exacta.numero_factura, "A-1042")
        exacta, resultados = buscar_facturas("Pulpería")
        self.assertIsNone(exacta)
        self.assertEqual(len(resultados), 2)
        self.assertEqual(buscar_facturas("nada-que-ver")[1], [])

    def test_listado_por_ruta_y_estado(self):
        from core.consulta import listado

        self.entregada_con_devolucion()
        self.factura("A-3000")  # sin carga
        Carga.objects.create(ruta=self.otra_ruta)
        facturas, resumen, _ = listado(self.hoy, self.hoy)
        self.assertEqual(resumen["facturas"], 2)
        self.assertEqual(resumen["con_devolucion"], 1)
        self.assertEqual(resumen["sin_carga"], 1)
        solo_norte, _, _ = listado(self.hoy, self.hoy, ruta_id=self.ruta.id)
        self.assertEqual([f.numero_factura for f in solo_norte], ["A-1042"])
        pendientes, _, _ = listado(self.hoy, self.hoy, estado="PENDIENTES")
        self.assertEqual([f.numero_factura for f in pendientes], ["A-3000"])
        de_maria, _, _ = listado(self.hoy, self.hoy, vendedor_id=self.vendedora.id)
        self.assertEqual(len(de_maria), 1)

    def test_pantallas_para_vendedor(self):
        f = self.entregada_con_devolucion()
        self.client.force_login(self.vendedor_user)
        # Número exacto: abre el detalle directo.
        r = self.client.get(reverse("ventas_buscar"), {"q": "A-1042"})
        self.assertRedirects(r, reverse("ventas_factura", args=[f.pk]))
        r = self.client.get(reverse("ventas_factura", args=[f.pk]))
        self.assertContains(r, "Con devolución")
        self.assertContains(r, "DV-1")
        r = self.client.get(reverse("ventas_ruta"), {"ruta": self.ruta.id, "desde": self.hoy.isoformat(), "hasta": self.hoy.isoformat()})
        self.assertContains(r, "A-1042")
        r = self.client.get(reverse("ventas_ruta_excel"), {"desde": self.hoy.isoformat()})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["Content-Disposition"].startswith("attachment"))
        self.assertEqual(r.content[:2], b"PK")  # es un .xlsx
        # El vendedor no entra al tablero ni ve enlaces al admin.
        self.assertEqual(self.client.get(reverse("tablero")).status_code, 403)
        self.assertEqual(self.client.get(reverse("ventas_factura", args=[99999])).status_code, 404)

    def test_sin_permiso_y_sin_sesion(self):
        r = self.client.get(reverse("ventas_ruta"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("ventas_acceso"), r["Location"])
        self.client.force_login(User.objects.create_user("bodeguero", password="x"))
        self.assertEqual(self.client.get(reverse("ventas_buscar")).status_code, 403)

    def test_supervisor_tambien_consulta(self):
        u = User.objects.create_user("sup", password="x")
        u.groups.add(Group.objects.get(name="Supervisores"))
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("ventas_buscar")).status_code, 200)
        self.assertContains(self.client.get(reverse("tablero")), reverse("ventas_buscar"))


class HorasYGpsTests(TableroBase):
    """Cambio #22: salida, llegada, entrega y fin de ruta; GPS; correcciones."""

    def setUp(self):
        super().setUp()
        from core.models import Vendedor

        self.juan = Vendedor.objects.create(nombre="Juan", tipo="REPARTIDOR", pin_acceso="1234")
        self.salida = timezone.now() - timedelta(hours=5)
        self.carga = Carga.objects.create(conductor=self.juan, estado="EN_RUTA", salio_a_ruta_en=self.salida)
        self.f1 = self.factura("A-1", carga=self.carga, cargada_en=self.salida)
        self.otro = Cliente.objects.create(codigo="C2", nombre="Farmacia Dos", latitud=Decimal("12.100000"), longitud=Decimal("-86.250000"))
        self.f2 = Factura.objects.create(numero_factura="A-2", cliente=self.otro, total=Decimal("115"),
                                         fecha_emision=self.hoy, modalidad_pago=self.contado,
                                         carga=self.carga, cargada_en=self.salida)
        FacturaDetalle.objects.create(factura=self.f2, producto=self.producto, cantidad_facturada=Decimal("10"),
                                      precio_unitario=Decimal("10"), subtotal=Decimal("100"), iva=Decimal("15"))

    def entregar(self, factura, gps=None, cuando=None):
        from core.entregas import registrar_entrega
        from core.tiempos import guardar_gps, leer_gps

        linea = factura.lineas.first()
        e = registrar_entrega(factura=factura, repartidor=self.juan, carga=self.carga,
                              cantidades_entregadas={linea.id: "10"})
        if cuando:
            Entrega.objects.filter(pk=e.pk).update(fecha_entrega=cuando)
            e.refresh_from_db()
        guardar_gps(e, leer_gps(gps or {}))
        return e

    def test_gps_aprende_y_compara_ubicacion(self):
        from core.tiempos import indicador_gps

        # Cliente sin ubicación: la primera entrega con buena señal la guarda.
        e1 = self.entregar(self.f1, {"gps_estado": "OK", "gps_latitud": "12.136389", "gps_longitud": "-86.251389", "gps_precision": "15"})
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.latitud, Decimal("12.136389"))
        self.assertEqual(indicador_gps(e1)["codigo"], "PRIMERA")
        # Cliente con ubicación: a ~4.4 km queda "lejos".
        e2 = self.entregar(self.f2, {"gps_estado": "OK", "gps_latitud": "12.140000", "gps_longitud": "-86.250000", "gps_precision": "20"})
        ind = indicador_gps(e2)
        self.assertEqual(ind["codigo"], "LEJOS")
        self.assertGreater(ind["distancia"], 4000)
        self.assertIn("km", ind["texto"])

    def test_gps_cerca_denegado_y_datos_malos(self):
        from core.tiempos import indicador_gps, leer_gps

        e = self.entregar(self.f2, {"gps_estado": "OK", "gps_latitud": "12.101000", "gps_longitud": "-86.250000", "gps_precision": "30"})
        self.assertEqual(indicador_gps(e)["codigo"], "EN_CLIENTE")  # ~110 m
        self.assertEqual(leer_gps({"gps_estado": "DENEGADO"})["estado"], "DENEGADO")
        self.assertEqual(leer_gps({"gps_estado": "OK", "gps_latitud": "999", "gps_longitud": "1"})["estado"], "NO_DISPONIBLE")
        self.assertEqual(leer_gps({"gps_estado": "cualquier cosa"})["estado"], "NO_DISPONIBLE")
        self.assertEqual(leer_gps({}, desde_oficina=True)["estado"], "OFICINA")

    def test_tiempos_de_la_carga(self):
        from core.tiempos import registrar_llegada, tiempos_de_la_carga

        t0 = self.salida
        registrar_llegada(self.carga, self.cliente, cuando=t0 + timedelta(minutes=30))
        self.entregar(self.f1, cuando=t0 + timedelta(minutes=50))
        registrar_llegada(self.carga, self.otro, cuando=t0 + timedelta(minutes=80))
        self.entregar(self.f2, cuando=t0 + timedelta(minutes=95))
        # Un segundo "Llegué" al mismo cliente no cambia la primera hora.
        registrar_llegada(self.carga, self.otro, cuando=t0 + timedelta(minutes=99))
        self.carga.ruta_terminada_en = t0 + timedelta(hours=3)
        self.carga.save()

        t = tiempos_de_la_carga(self.carga)
        self.assertEqual(t["total"], timedelta(hours=3))
        self.assertEqual(t["hasta_primer_cliente"], timedelta(minutes=30))
        self.assertEqual([p["cliente"].codigo for p in t["paradas"]], ["C1", "C2"])
        self.assertEqual(t["paradas"][0]["en_cliente"], timedelta(minutes=20))
        self.assertEqual(t["paradas"][1]["en_cliente"], timedelta(minutes=15))
        self.assertEqual(t["paradas"][1]["viaje"], timedelta(minutes=30))  # 50 → 80
        self.assertEqual(t["promedio_en_cliente"], timedelta(minutes=17, seconds=30))
        self.assertEqual(t["ultimo_a_fin"], timedelta(minutes=85))
        self.assertEqual(t["sin_llegada"], 0)

    def test_corregir_horas_deja_registro(self):
        from core.models import CorreccionHora
        from core.tiempos import CorreccionError, corregir_horas

        e = self.entregar(self.f1, cuando=self.salida + timedelta(hours=1))
        admin = User.objects.create_user("ofi", password="x", is_staff=True)
        with self.assertRaises(CorreccionError):
            corregir_horas(self.carga, usuario=admin, motivo="", salida=self.salida)
        with self.assertRaises(CorreccionError):
            corregir_horas(self.carga, usuario=admin, motivo="x", entregas={e.id: self.salida - timedelta(hours=1)})
        nueva = self.salida - timedelta(minutes=10)
        n = corregir_horas(self.carga, usuario=admin, motivo="Olvidó marcar la salida", salida=nueva,
                           entregas={e.id: e.fecha_entrega})  # sin cambio: no cuenta
        self.assertEqual(n, 1)
        self.carga.refresh_from_db()
        self.assertEqual(self.carga.salio_a_ruta_en, nueva)
        c = CorreccionHora.objects.get()
        self.assertEqual((c.campo, c.usuario, c.valor_anterior), ("SALIDA", admin, self.salida))

    def test_flujo_del_repartidor_en_el_celular(self):
        from core.models import ParadaCliente

        s = self.client.session
        s["entrega_carga_id"] = self.carga.id
        s.save()
        url = reverse("entregas_captura", args=[self.carga.id, self.f1.id])
        r = self.client.get(url)
        self.assertContains(r, "Llegué al cliente")
        r = self.client.post(reverse("entregas_llegue", args=[self.carga.id, self.f1.id]))
        self.assertTrue(r.json()["ok"])
        self.assertContains(self.client.get(url), "Llegaste a este cliente")
        linea = self.f1.lineas.first()
        self.client.post(url, {f"ent_{linea.id}": "10", "gps_estado": "OK", "gps_latitud": "12.1",
                               "gps_longitud": "-86.2", "gps_precision": "12", "documento_cliente": "ORIGINAL"})
        e = Entrega.objects.get(factura=self.f1)
        self.assertEqual(e.gps_estado, "OK")
        # Segunda factura: "Llegué" sin señal → la hora llega con la entrega.
        hora_cel = self.salida + timedelta(hours=2)
        linea2 = self.f2.lineas.first()
        self.client.post(reverse("entregas_captura", args=[self.carga.id, self.f2.id]),
                         {f"ent_{linea2.id}": "10", "gps_estado": "DENEGADO",
                          "llegada_ts": str(int(hora_cel.timestamp() * 1000))})
        p = ParadaCliente.objects.get(carga=self.carga, cliente=self.otro)
        self.assertTrue(p.llegada_sin_senal)
        self.assertEqual(int(p.llegada_en.timestamp()), int(hora_cel.timestamp()))
        r = self.client.get(reverse("entregas_lista", args=[self.carga.id]))
        self.assertContains(r, "Salida ")
        self.assertContains(r, "Entregó ")

    def test_cierre_y_reporte_muestran_horas(self):
        from core.tiempos import registrar_llegada

        registrar_llegada(self.carga, self.cliente)
        self.entregar(self.f1, {"gps_estado": "OK", "gps_latitud": "12.1", "gps_longitud": "-86.2", "gps_precision": "10"})
        admin = User.objects.create_superuser("jefe", password="x")
        self.client.force_login(admin)
        r = self.client.get(reverse("admin:core_carga_cierre", args=[self.carga.id]))
        self.assertContains(r, "Horas y tiempos de la ruta")
        self.assertContains(r, "Corregir horas")
        r = self.client.get(reverse("admin:core_carga_horas", args=[self.carga.id]))
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse("admin:core_carga_horas", args=[self.carga.id]), {
            "salida": timezone.localtime(self.salida - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M"),
            "motivo": "Prueba",
        })
        self.assertRedirects(r, reverse("admin:core_carga_cierre", args=[self.carga.id]))
        self.assertContains(self.client.get(reverse("admin:core_carga_cierre", args=[self.carga.id])), "Horas corregidas por oficina")
        r = self.client.get(reverse("tablero_analisis"), {"desde": (self.hoy - timedelta(days=1)).isoformat()})
        self.assertContains(r, "GPS en el cliente")


class AgregarACargaExistenteTests(TableroBase):
    """Cambio #23: agregar facturas seleccionadas a una Carga existente."""

    def setUp(self):
        super().setUp()
        from core.models import Vehiculo

        self.planeada = Carga.objects.create(estado="PLANEADA")
        self.en_ruta = Carga.objects.create(estado="EN_RUTA", salio_a_ruta_en=timezone.now())
        self.cerrada = Carga.objects.create(estado="CERRADA")
        self.camion_paq = Vehiculo.objects.create(placa="M000003", pide_paquetes_costo=True)
        self.con_paq = Carga.objects.create(estado="PLANEADA", vehiculo=self.camion_paq)
        self.admin = User.objects.create_superuser("jefe", password="x")

    def test_reglas(self):
        from core.asignar import AsignarError, agregar_a_carga

        libre = self.factura("F-LIBRE")
        de_otra = self.factura("F-OTRA", carga=self.planeada, cargada_en=timezone.now(), paquetes=3)
        en_camion = self.factura("F-CAMION", carga=self.en_ruta, cargada_en=timezone.now())
        anulada = self.factura("F-ANUL", estado_factura="ANULADA")
        entregada = self.factura("F-ENT", estado_entrega="COMPLETA")
        destino = Carga.objects.create(estado="EN_RUTA")
        agregadas, no = agregar_a_carga(destino, [libre, de_otra, en_camion, anulada, entregada])
        self.assertEqual({f.numero_factura for f in agregadas}, {"F-LIBRE", "F-OTRA"})
        self.assertEqual({f.numero_factura for f in no}, {"F-CAMION", "F-ANUL", "F-ENT"})
        de_otra.refresh_from_db()
        self.assertEqual(de_otra.carga, destino)
        self.assertIsNone(de_otra.cargada_en)  # hay que volver a cargarla
        self.assertIsNone(de_otra.paquetes)
        with self.assertRaises(AsignarError):
            agregar_a_carga(self.cerrada, [self.factura("F-X")])
        with self.assertRaises(AsignarError):  # la unidad pide paquetes y costo
            agregar_a_carga(self.con_paq, [self.factura("F-Y")])

    def test_accion_desde_la_lista_de_facturas(self):
        from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME

        f1, f2 = self.factura("F-1"), self.factura("F-2")
        self.client.force_login(self.admin)
        url = reverse("admin:core_factura_changelist")
        datos = {"action": "agregar_a_carga_existente", ACTION_CHECKBOX_NAME: [f1.pk, f2.pk]}
        r = self.client.post(url, datos)
        self.assertContains(r, "Agregar facturas a una Carga existente")
        self.assertContains(r, self.planeada.codigo_reporte)
        self.assertNotContains(r, self.cerrada.codigo_reporte)
        # Unidad con paquetes: sin datos no deja; con datos sí.
        r = self.client.post(url, {**datos, "apply": "1", "carga_destino": self.con_paq.pk})
        self.assertContains(r, "pide paquetes y costo")
        f1.refresh_from_db()
        self.assertIsNone(f1.carga)
        r = self.client.post(url, {**datos, "apply": "1", "carga_destino": self.con_paq.pk,
                                   f"paq_{f1.pk}": "2", f"cos_{f1.pk}": "50", f"paq_{f2.pk}": "1", f"cos_{f2.pk}": "25"})
        self.assertEqual(r.status_code, 302)
        f1.refresh_from_db()
        self.assertEqual((f1.carga, f1.paquetes, f1.costo_flete), (self.con_paq, 2, Decimal("50.00")))


class ConsultaCargasTests(TableroBase):
    """Cambio #24: consulta de Cargas para Administración y Supervisores."""

    def setUp(self):
        super().setUp()
        from core.models import Ruta, Vendedor

        self.ruta = Ruta.objects.create(nombre="Norte")
        self.otra_ruta = Ruta.objects.create(nombre="Sur")
        self.juan = Vendedor.objects.create(nombre="Juan", tipo="REPARTIDOR")
        self.luis = Vendedor.objects.create(nombre="Luis", tipo="REPARTIDOR")

    def factura_con_resultado(self, numero, carga, entregada="0", devuelta="0", **campos):
        f = self.factura(numero, carga=carga, **campos)
        linea = f.lineas.first()
        linea.cantidad_entregada = Decimal(entregada)
        linea.cantidad_devuelta = Decimal(devuelta)
        linea.save()
        return f

    def test_clasificacion_resumen_y_avance(self):
        from core.consulta_cargas import listado_cargas

        carga = Carga.objects.create(ruta=self.ruta, conductor=self.juan, fecha_planeada=self.hoy)
        self.factura_con_resultado("C1", carga, entregada="10")  # Completa
        self.factura_con_resultado("C2", carga, entregada="8", devuelta="2")  # Con devolución
        self.factura_con_resultado("C3", carga, entregada="0", devuelta="10")  # No entregada
        self.factura_con_resultado("C4", carga)  # Sin visitar
        self.factura_con_resultado("C5", carga, entregada="5")  # Parcial -> cuenta como sin visitar
        self.factura("C6", carga=carga, estado_factura="ANULADA")  # no cuenta

        cargas, resumen, recortado = listado_cargas(self.hoy, self.hoy)
        self.assertFalse(recortado)
        self.assertEqual(len(cargas), 1)
        c = cargas[0]
        self.assertEqual(c.total_facturas, 5)
        self.assertEqual(c.completa, 1)
        self.assertEqual(c.devolucion, 1)
        self.assertEqual(c.no_entregada, 1)
        self.assertEqual(c.sin_visitar, 2)  # sin visitar + parcial
        self.assertEqual(c.monto, Decimal("575.00"))  # 5 x 115
        self.assertEqual(c.avance, 40)  # (1 + 1) / 5 = 40%
        self.assertEqual(resumen["cargas"], 1)
        self.assertEqual(resumen["facturas"], 5)
        self.assertEqual(resumen["sin_visitar"], 2)

    def test_carga_sin_facturas_activas(self):
        from core.consulta_cargas import listado_cargas

        Carga.objects.create(fecha_planeada=self.hoy)
        cargas, _, _ = listado_cargas(self.hoy, self.hoy)
        c = cargas[0]
        self.assertEqual(c.total_facturas, 0)
        self.assertIsNone(c.avance)
        self.assertEqual(c.monto, Decimal("0.00"))

    def test_filtros(self):
        from core.consulta_cargas import listado_cargas

        c1 = Carga.objects.create(ruta=self.ruta, conductor=self.juan, estado="EN_RUTA", fecha_planeada=self.hoy)
        c2 = Carga.objects.create(ruta=self.otra_ruta, conductor=self.luis, estado="PLANEADA", fecha_planeada=self.hoy)
        self.factura_con_resultado("F1", c1, entregada="10")
        self.factura_con_resultado("F2", c2)

        solo_ruta, _, _ = listado_cargas(self.hoy, self.hoy, ruta_id=self.ruta.id)
        self.assertEqual([c.pk for c in solo_ruta], [c1.pk])
        solo_conductor, _, _ = listado_cargas(self.hoy, self.hoy, conductor_id=self.luis.id)
        self.assertEqual([c.pk for c in solo_conductor], [c2.pk])
        solo_estado, _, _ = listado_cargas(self.hoy, self.hoy, estado="PLANEADA")
        self.assertEqual([c.pk for c in solo_estado], [c2.pk])
        por_id, _, _ = listado_cargas(self.hoy, self.hoy, carga_id=c1.id)
        self.assertEqual([c.pk for c in por_id], [c1.pk])
        fuera, _, _ = listado_cargas(self.hoy - timedelta(days=10), self.hoy - timedelta(days=5))
        self.assertEqual(fuera, [])

    def test_pantalla_filtros_permisos_y_detalle(self):
        carga = Carga.objects.create(
            ruta=self.ruta, conductor=self.juan, estado="EN_RUTA",
            salio_a_ruta_en=timezone.now(), fecha_planeada=self.hoy,
        )
        self.factura_con_resultado("D-1", carga, entregada="8", devuelta="2")

        # Sin sesión: redirige al acceso.
        self.assertEqual(self.client.get(reverse("tablero_cargas")).status_code, 302)
        # Con sesión pero sin el permiso del tablero.
        self.client.force_login(User.objects.create_user("vend", password="x"))
        self.assertEqual(self.client.get(reverse("tablero_cargas")).status_code, 403)

        u = User.objects.create_user("sup", password="x")
        u.groups.add(Group.objects.get(name="Supervisores"))
        self.client.force_login(u)
        r = self.client.get(reverse("tablero_cargas"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Consulta de cargas")
        self.assertContains(r, carga.codigo_reporte)
        self.assertContains(r, "D-1")
        self.assertContains(r, "Con devolución")
        # El enlace a esta pantalla aparece en la pestaña del resto del tablero.
        self.assertContains(self.client.get(reverse("tablero")), reverse("tablero_cargas"))
        self.assertContains(self.client.get(reverse("tablero_analisis")), reverse("tablero_cargas"))

    def test_fechas_invertidas_y_filtro_de_carga_no_numerico(self):
        u = User.objects.create_user("sup2", password="x")
        u.groups.add(Group.objects.get(name="Supervisores"))
        self.client.force_login(u)
        r = self.client.get(
            reverse("tablero_cargas"),
            {"desde": "2026-02-01", "hasta": "2026-01-01", "carga": "abc"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["desde"].isoformat(), "2026-01-01")  # se invierten
        self.assertEqual(r.context["hasta"].isoformat(), "2026-02-01")
        self.assertEqual(r.context["carga_id"], "abc")  # se conserva en el campo, pero no filtra
        self.assertEqual(r.context["cargas"], [])


def _excel_sucursales(filas):
    """
    Construye en memoria un Excel con las columnas del maestro de
    clientes/sucursales (Cambio #25), para usarlo en pruebas sin depender de
    un archivo real en disco. `filas` es una lista de dicts con las claves
    de core.importador_sucursales.COLUMNAS_ESPERADAS (las que falten quedan
    en blanco).
    """
    from io import BytesIO

    import openpyxl

    from core.importador_sucursales import COLUMNAS_ESPERADAS

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(COLUMNAS_ESPERADAS)
    for fila in filas:
        ws.append([fila.get(col, "") for col in COLUMNAS_ESPERADAS])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class SucursalModelTests(TableroBase):
    """Cambio #25: catálogo de Sucursales (direcciones de entrega) de un Cliente."""

    def test_un_cliente_puede_tener_varias_sucursales(self):
        s1 = Sucursal.objects.create(cliente=self.cliente, nombre="Centro", direccion="Calle 1")
        s2 = Sucursal.objects.create(cliente=self.cliente, nombre="Bodega Norte", direccion="Calle 2")
        self.assertEqual(set(self.cliente.sucursales.all()), {s1, s2})
        self.assertEqual(str(s1), f"Centro ({self.cliente.codigo})")

    def test_no_se_duplica_el_mismo_nombre_para_el_mismo_cliente(self):
        from django.db import IntegrityError, transaction

        Sucursal.objects.create(cliente=self.cliente, nombre="Centro")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Sucursal.objects.create(cliente=self.cliente, nombre="Centro")

    def test_factura_sin_sucursal_queda_en_null(self):
        f = self.factura("SN-1")
        self.assertIsNone(f.sucursal)


class ImportadorSucursalesTests(TableroBase):
    """Cambio #25: importar y re-importar el maestro de clientes/sucursales."""

    def test_crea_clientes_y_sucursales_nuevas(self):
        from core.importador_sucursales import importar_excel

        archivo = _excel_sucursales(
            [
                {
                    "Código SN": "C100",
                    "Nombre SN": "Supermercado Uno",
                    "Cod Grupo": 5,
                    "Grupo": "Supermercados",
                    "Nombre de dirección": "Tienda Centro",
                    "Departamento": "Managua",
                    "Territorio": "T1",
                    "Municipio": "Managua",
                    "Direccion": "Calle Central 123",
                    "Canal": "Moderno",
                    "EstadoCliente": "Activo",
                },
                {
                    "Código SN": "C100",
                    "Nombre SN": "Supermercado Uno",
                    "Nombre de dirección": "Tienda Sur",
                    "Direccion": "Carretera Sur km 5",
                    "EstadoCliente": "Activo",
                },
            ]
        )
        resumen = importar_excel(archivo, nombre_archivo="maestro.xlsx")
        self.assertEqual(resumen["sucursales_creadas"], 2)
        self.assertEqual(resumen["sucursales_actualizadas"], 0)
        self.assertEqual(resumen["clientes_nuevos"], 1)
        self.assertEqual(resumen["filas_omitidas"], 0)

        cliente = Cliente.objects.get(codigo="C100")
        self.assertEqual(cliente.sucursales.count(), 2)
        centro = cliente.sucursales.get(nombre="Tienda Centro")
        self.assertEqual(centro.direccion, "Calle Central 123")
        self.assertEqual(centro.grupo, "Supermercados")
        self.assertEqual(centro.cod_grupo, 5)
        self.assertTrue(centro.activo)

    def test_reimportar_actualiza_en_vez_de_duplicar(self):
        from core.importador_sucursales import importar_excel

        primero = _excel_sucursales(
            [
                {
                    "Código SN": "C100",
                    "Nombre SN": "Supermercado Uno",
                    "Nombre de dirección": "Tienda Centro",
                    "Direccion": "Dirección vieja",
                    "EstadoCliente": "Activo",
                }
            ]
        )
        importar_excel(primero, nombre_archivo="v1.xlsx")

        segundo = _excel_sucursales(
            [
                {
                    "Código SN": "C100",
                    "Nombre SN": "Supermercado Uno",
                    "Nombre de dirección": "Tienda Centro",
                    "Direccion": "Dirección corregida",
                    "EstadoCliente": "Inactivo",
                }
            ]
        )
        resumen = importar_excel(segundo, nombre_archivo="v2.xlsx")

        self.assertEqual(resumen["sucursales_creadas"], 0)
        self.assertEqual(resumen["sucursales_actualizadas"], 1)
        self.assertEqual(Sucursal.objects.filter(cliente__codigo="C100").count(), 1)
        sucursal = Sucursal.objects.get(cliente__codigo="C100", nombre="Tienda Centro")
        self.assertEqual(sucursal.direccion, "Dirección corregida")
        self.assertFalse(sucursal.activo)

    def test_filas_sin_codigo_o_sin_nombre_se_omiten(self):
        from core.importador_sucursales import importar_excel

        archivo = _excel_sucursales(
            [
                # Código en blanco (solo espacios): se cuenta como omitida.
                {"Código SN": "  ", "Nombre de dirección": "Tienda X", "EstadoCliente": "Activo"},
                # Nombre de sucursal vacío (None): también se cuenta como omitida.
                {"Código SN": "C200", "Nombre de dirección": "", "EstadoCliente": "Activo"},
            ]
        )
        resumen = importar_excel(archivo, nombre_archivo="malo.xlsx")
        self.assertEqual(resumen["filas_omitidas"], 2)
        self.assertEqual(resumen["sucursales_creadas"], 0)

    def test_columnas_faltantes_lanza_error(self):
        from io import BytesIO

        import openpyxl

        from core.importador_sucursales import ImportacionError, importar_excel

        wb = openpyxl.Workbook()
        wb.active.append(["Código SN", "Nombre SN"])  # faltan casi todas las columnas
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        with self.assertRaises(ImportacionError):
            importar_excel(buf, nombre_archivo="incompleto.xlsx")


class ObtenerSucursalTests(TableroBase):
    """
    Cambio #25: el importador de facturas (core.importador) enlaza cada
    factura nueva con su Sucursal por nombre, sin crear sucursales nuevas.
    """

    def test_encuentra_la_sucursal_ignorando_mayusculas_y_espacios(self):
        from core.importador import _obtener_sucursal

        Sucursal.objects.create(cliente=self.cliente, nombre="Tienda Centro", direccion="Calle 1")
        encontrada = _obtener_sucursal({}, self.cliente, "  tienda centro  ")
        self.assertIsNotNone(encontrada)
        self.assertEqual(encontrada.nombre, "Tienda Centro")

    def test_sin_coincidencia_devuelve_none_sin_crear_nada(self):
        from core.importador import _obtener_sucursal

        resultado = _obtener_sucursal({}, self.cliente, "Sucursal que no existe")
        self.assertIsNone(resultado)
        self.assertEqual(Sucursal.objects.count(), 0)

    def test_nombre_vacio_devuelve_none(self):
        from core.importador import _obtener_sucursal

        self.assertIsNone(_obtener_sucursal({}, self.cliente, ""))
        self.assertIsNone(_obtener_sucursal({}, self.cliente, None))

    def test_usa_la_cache_en_vez_de_volver_a_consultar(self):
        from core.importador import _obtener_sucursal

        sucursal = Sucursal.objects.create(cliente=self.cliente, nombre="Tienda Centro")
        sucursal_id = sucursal.id
        cache = {}
        primera = _obtener_sucursal(cache, self.cliente, "Tienda Centro")
        Sucursal.objects.filter(pk=sucursal_id).delete()  # si usara la BD otra vez, daría None
        segunda = _obtener_sucursal(cache, self.cliente, "Tienda Centro")
        self.assertEqual(primera.id, sucursal_id)
        self.assertEqual(segunda.id, sucursal_id)


class ImportarSucursalesVistaTests(TableroBase):
    """Cambio #25: pantalla de admin "Importar sucursales desde Excel"."""

    def _archivo_valido(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        contenido = _excel_sucursales(
            [
                {
                    "Código SN": "C300",
                    "Nombre SN": "Ferretería Dos",
                    "Nombre de dirección": "Sucursal Única",
                    "Direccion": "Barrio Central",
                    "EstadoCliente": "Activo",
                }
            ]
        ).read()
        return SimpleUploadedFile(
            "maestro.xlsx", contenido, content_type="application/vnd.openxmlformats-officedocument.spreadsheetsheet.sheet"
        )

    def test_requiere_permiso_de_staff(self):
        url = reverse("admin:core_cliente_importar_sucursales")
        # Sin iniciar sesión, el admin redirige al login.
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)
        # Usuario normal (no staff): también fuera.
        self.client.force_login(User.objects.create_user("vendedor", password="x"))
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)

    def test_staff_ve_el_formulario_y_el_boton_en_el_listado_de_clientes(self):
        admin = User.objects.create_superuser("jefe", password="x")
        self.client.force_login(admin)
        r = self.client.get(reverse("admin:core_cliente_importar_sucursales"))
        self.assertEqual(r.status_code, 200)
        listado = self.client.get(reverse("admin:core_cliente_changelist"))
        self.assertContains(listado, "Importar sucursales desde Excel")

    def test_importacion_exitosa_muestra_resumen(self):
        admin = User.objects.create_superuser("jefe2", password="x")
        self.client.force_login(admin)
        r = self.client.post(
            reverse("admin:core_cliente_importar_sucursales"),
            {"archivo": self._archivo_valido()},
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "1 sucursales nuevas")
        self.assertTrue(
            Sucursal.objects.filter(cliente__codigo="C300", nombre="Sucursal Única").exists()
        )

    def test_archivo_con_columnas_faltantes_muestra_error(self):
        from io import BytesIO

        import openpyxl
        from django.core.files.uploadedfile import SimpleUploadedFile

        wb = openpyxl.Workbook()
        wb.active.append(["Código SN"])
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        archivo = SimpleUploadedFile(
            "malo.xlsx", buf.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetsheet.sheet"
        )

        admin = User.objects.create_superuser("jefe3", password="x")
        self.client.force_login(admin)
        r = self.client.post(
            reverse("admin:core_cliente_importar_sucursales"), {"archivo": archivo}
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "no tiene las columnas esperadas")


class DevolucionSinFacturaBase(TableroBase):
    """Cambio #27: datos mínimos para las devoluciones sin factura."""

    def setUp(self):
        super().setUp()
        self.ruta = Ruta.objects.create(nombre="Ruta 1")
        self.repartidor = Vendedor.objects.create(
            nombre="Juan Repartidor", tipo="REPARTIDOR", pin_acceso="4321"
        )
        self.sucursal = Sucursal.objects.create(cliente=self.cliente, nombre="Sucursal Centro")
        self.motivo = MotivoDevolucion.objects.filter(activo=True).first()
        self.ventas = User.objects.create_user("ventas1", password="x")
        grupo, _ = Group.objects.get_or_create(name="Ventas — Devoluciones")
        self.ventas.groups.add(grupo)
        self.bodega_user = User.objects.create_user("bodega1", password="x")
        grupo_bodega, _ = Group.objects.get_or_create(name="Bodega PT")
        self.bodega_user.groups.add(grupo_bodega)
        self.admin = User.objects.create_superuser("jefe_devol", password="x")

    def crear_devolucion(self, **extra):
        campos = {
            "fecha_ingreso": self.hoy,
            "sucursal": self.sucursal,
            "total_unidades_estimado": 10,
            "contacto_sucursal": "María en bodega del cliente",
            "motivo": self.motivo,
        }
        campos.update(extra)
        return DevolucionSinFactura.objects.create(**campos)


class DevolucionSinFacturaModeloTests(DevolucionSinFacturaBase):
    def test_codigo_reporte(self):
        d = self.crear_devolucion()
        self.assertEqual(d.codigo_reporte, f"DEVOL-{d.id:04d}")

    def test_propiedades_detalle(self):
        d = self.crear_devolucion(estado="RECOLECTADA")
        linea = DevolucionSinFacturaDetalle.objects.create(
            devolucion=d, producto=self.producto, cantidad_recolectada=Decimal("5")
        )
        self.assertFalse(linea.recepcion_registrada)
        self.assertIsNone(linea.recibido_total)
        self.assertIsNone(linea.diferencia_recepcion)
        linea.recibido_inventario = Decimal("4")
        linea.recibido_merma = Decimal("1")
        linea.save()
        self.assertTrue(linea.recepcion_registrada)
        self.assertEqual(linea.recibido_total, Decimal("5"))
        self.assertEqual(linea.diferencia_recepcion, Decimal("0"))


class SolicitarDevolucionTests(DevolucionSinFacturaBase):
    def test_puede_solicitar(self):
        from core.devoluciones_sf import puede_solicitar

        self.assertTrue(puede_solicitar(self.ventas))
        otro = User.objects.create_user("sinpermiso", password="x")
        self.assertFalse(puede_solicitar(otro))

    def test_crear_solicitud(self):
        from core.devoluciones_sf import crear_solicitud

        d = crear_solicitud(
            fecha_ingreso=self.hoy,
            sucursal=self.sucursal,
            total_unidades_estimado=8,
            contacto_sucursal="Pedro",
            motivo=self.motivo,
            observaciones="",
            usuario=self.ventas,
        )
        self.assertEqual(d.estado, "SOLICITADA")
        self.assertEqual(d.creada_por, self.ventas)

    def test_vista_solicitar_requiere_permiso(self):
        r = self.client.get(reverse("devoluciones_sf_solicitar"))
        self.assertEqual(r.status_code, 302)
        sin_permiso = User.objects.create_user("x1", password="x")
        self.client.force_login(sin_permiso)
        r = self.client.get(reverse("devoluciones_sf_solicitar"))
        self.assertEqual(r.status_code, 403)

    def test_vista_solicitar_crea_devolucion(self):
        self.client.force_login(self.ventas)
        r = self.client.post(
            reverse("devoluciones_sf_solicitar"),
            {
                "fecha_ingreso": self.hoy.isoformat(),
                "cliente": self.sucursal.cliente_id,
                "sucursal": self.sucursal.pk,
                "total_unidades_estimado": "12",
                "contacto": ContactoSucursal.objects.first().pk,
                "motivo": self.motivo.pk,
                "observaciones": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(DevolucionSinFactura.objects.count(), 1)
        creada = DevolucionSinFactura.objects.first()
        self.assertEqual(creada.estado, "SOLICITADA")
        self.assertEqual(creada.creada_por, self.ventas)

    def test_vista_lista(self):
        self.crear_devolucion()
        self.client.force_login(self.ventas)
        r = self.client.get(reverse("devoluciones_sf_lista"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "DEVOL-")


class AsignarDevolucionACargaTests(DevolucionSinFacturaBase):
    def test_asignar_a_carga_existente(self):
        from core.devoluciones_sf import asignar_a_carga_existente

        destino = Carga.objects.create(estado="PLANEADA")
        d1 = self.crear_devolucion()
        d2 = self.crear_devolucion()
        cerrada_antes = Carga.objects.create(estado="CERRADA")

        asignadas = asignar_a_carga_existente(destino, [d1, d2])
        self.assertEqual(len(asignadas), 2)
        d1.refresh_from_db()
        self.assertEqual(d1.carga, destino)
        self.assertEqual(d1.estado, "ASIGNADA")

        from core.devoluciones_sf import AsignarDevolucionError

        with self.assertRaises(AsignarDevolucionError):
            asignar_a_carga_existente(cerrada_antes, [self.crear_devolucion()])

    def test_crear_carga_con_devoluciones(self):
        from core.devoluciones_sf import crear_carga_con_devoluciones

        d1 = self.crear_devolucion()
        carga, asignadas = crear_carga_con_devoluciones(
            fecha_planeada=self.hoy,
            ruta=self.ruta,
            conductor=self.repartidor,
            vehiculo=None,
            km_inicial=None,
            observaciones="",
            devoluciones=[d1],
        )
        self.assertEqual(carga.devoluciones_sin_factura.count(), 1)
        d1.refresh_from_db()
        self.assertEqual(d1.estado, "ASIGNADA")
        self.assertEqual(d1.carga, carga)

    def test_accion_admin_agrupar_en_carga(self):
        from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME

        d1 = self.crear_devolucion()
        self.client.force_login(self.admin)
        url = reverse("admin:core_devolucionsinfactura_changelist")
        datos = {
            "action": "agrupar_en_carga_devolucion",
            ACTION_CHECKBOX_NAME: [d1.pk],
        }
        r = self.client.post(url, datos)
        self.assertContains(r, "Agrupar devoluciones en una nueva Carga")
        r = self.client.post(
            url,
            {
                **datos,
                "apply": "1",
                "fecha_planeada": self.hoy.isoformat(),
                "ruta": "",
                "conductor": "",
                "vehiculo": "",
                "km_inicial": "",
                "observaciones": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        d1.refresh_from_db()
        self.assertEqual(d1.estado, "ASIGNADA")
        self.assertIsNotNone(d1.carga)

    def test_accion_admin_agregar_a_carga_existente(self):
        from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME

        destino = Carga.objects.create(estado="PLANEADA")
        d1 = self.crear_devolucion()
        self.client.force_login(self.admin)
        url = reverse("admin:core_devolucionsinfactura_changelist")
        datos = {
            "action": "agregar_a_carga_existente_devolucion",
            ACTION_CHECKBOX_NAME: [d1.pk],
        }
        r = self.client.post(url, datos)
        self.assertContains(r, "Agregar devoluciones a una Carga existente")
        self.assertContains(r, destino.codigo_reporte)
        r = self.client.post(url, {**datos, "apply": "1", "carga_destino": destino.pk})
        self.assertEqual(r.status_code, 302)
        d1.refresh_from_db()
        self.assertEqual(d1.carga, destino)
        self.assertEqual(d1.estado, "ASIGNADA")


class CapturaRepartidorDevolucionTests(DevolucionSinFacturaBase):
    def setUp(self):
        super().setUp()
        self.carga = Carga.objects.create(
            estado="EN_RUTA", ruta=self.ruta, conductor=self.repartidor,
            salio_a_ruta_en=timezone.now(),
        )
        self.devolucion = self.crear_devolucion(carga=self.carga, estado="ASIGNADA")

    def _entrar(self):
        self.client.post(
            reverse("entregas_acceso"),
            {"codigo_carga": self.carga.codigo_acceso, "pin_repartidor": "4321"},
        )

    def test_registrar_captura(self):
        from core.devoluciones_sf import registrar_captura

        registrar_captura(
            self.devolucion,
            cantidades={self.producto.id: Decimal("3")},
            motivos={self.producto.id: self.motivo.id},
            observaciones="",
        )
        self.devolucion.refresh_from_db()
        self.assertEqual(self.devolucion.estado, "RECOLECTADA")
        self.assertEqual(self.devolucion.detalles.count(), 1)
        linea = self.devolucion.detalles.first()
        self.assertEqual(linea.cantidad_recolectada, Decimal("3"))
        self.assertEqual(linea.motivo, self.motivo)

    def test_registrar_captura_exige_motivo_por_producto(self):
        """Cambio #27.1: cada producto debe traer su motivo de devolución."""
        from core.devoluciones_sf import SolicitudError, registrar_captura

        with self.assertRaises(SolicitudError):
            registrar_captura(
                self.devolucion, cantidades={self.producto.id: Decimal("3")}, motivos={}
            )
        self.assertEqual(self.devolucion.detalles.count(), 0)

    def test_registrar_captura_corrige_lineas_anteriores(self):
        from core.devoluciones_sf import registrar_captura

        registrar_captura(
            self.devolucion,
            cantidades={self.producto.id: Decimal("3")},
            motivos={self.producto.id: self.motivo.id},
        )
        self.devolucion.refresh_from_db()
        otro_producto = Producto.objects.create(codigo="P2", nombre="Producto 2")
        otro_motivo = MotivoDevolucion.objects.create(nombre="Otro motivo", activo=True)
        registrar_captura(
            self.devolucion,
            cantidades={otro_producto.id: Decimal("2")},
            motivos={otro_producto.id: otro_motivo.id},
        )
        self.assertEqual(self.devolucion.detalles.count(), 1)
        linea = self.devolucion.detalles.first()
        self.assertEqual(linea.producto, otro_producto)
        self.assertEqual(linea.motivo, otro_motivo)

    def test_vista_captura_requiere_sesion(self):
        url = reverse("entregas_devolucion_sf", args=[self.carga.id, self.devolucion.id])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)

    def test_vista_captura_guarda(self):
        self._entrar()
        url = reverse("entregas_devolucion_sf", args=[self.carga.id, self.devolucion.id])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Motivo de devolución")
        r = self.client.post(
            url,
            {
                "producto_id": [str(self.producto.id)],
                "cantidad": ["4"],
                "motivo_id": [str(self.motivo.id)],
                "observaciones": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.devolucion.refresh_from_db()
        self.assertEqual(self.devolucion.estado, "RECOLECTADA")
        self.assertEqual(self.devolucion.detalles.first().motivo, self.motivo)

    def test_vista_captura_exige_motivo(self):
        """Cambio #27.1: sin motivo por producto, no se guarda la recolección."""
        self._entrar()
        url = reverse("entregas_devolucion_sf", args=[self.carga.id, self.devolucion.id])
        r = self.client.post(
            url,
            {
                "producto_id": [str(self.producto.id)],
                "cantidad": ["4"],
                "motivo_id": [""],
                "observaciones": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.devolucion.refresh_from_db()
        self.assertEqual(self.devolucion.estado, "ASIGNADA")
        self.assertEqual(self.devolucion.detalles.count(), 0)

    def test_buscar_producto(self):
        self._entrar()
        url = reverse("entregas_devolucion_sf_buscar_producto", args=[self.carga.id])
        r = self.client.get(url, {"q": self.producto.codigo})
        self.assertEqual(r.status_code, 200)
        datos = r.json()
        self.assertEqual(len(datos["resultados"]), 1)
        self.assertEqual(datos["resultados"][0]["id"], self.producto.id)

    def test_lista_entregas_muestra_devolucion(self):
        self._entrar()
        r = self.client.get(reverse("entregas_lista", args=[self.carga.id]))
        self.assertContains(r, self.devolucion.codigo_reporte)


class RecepcionBodegaSinFacturaTests(DevolucionSinFacturaBase):
    def setUp(self):
        super().setUp()
        self.carga = Carga.objects.create(
            estado="RUTA_TERMINADA", ruta=self.ruta, conductor=self.repartidor,
        )
        self.devolucion = self.crear_devolucion(
            carga=self.carga, estado="RECOLECTADA", recolectada_en=timezone.now()
        )
        self.linea = DevolucionSinFacturaDetalle.objects.create(
            devolucion=self.devolucion,
            producto=self.producto,
            cantidad_recolectada=Decimal("5"),
            motivo=self.motivo,
        )

    def test_resumen_pendiente(self):
        from core.devoluciones_sf import resumen_recepcion_sin_factura

        r = resumen_recepcion_sin_factura(self.carga)
        self.assertEqual(r["estado"], "PENDIENTE")
        self.assertEqual(r["productos"], 1)

    def test_registrar_recepcion_completa(self):
        from core.devoluciones_sf import registrar_recepcion_sin_factura, resumen_recepcion_sin_factura

        registrar_recepcion_sin_factura(
            self.carga,
            usuario=self.bodega_user,
            cantidades={self.linea.id: (Decimal("5"), Decimal("0"))},
        )
        self.devolucion.refresh_from_db()
        self.assertEqual(self.devolucion.estado, "CERRADA")
        self.assertEqual(self.devolucion.recibido_por, self.bodega_user)
        r = resumen_recepcion_sin_factura(self.carga)
        self.assertEqual(r["estado"], "COMPLETA")

    def test_registrar_recepcion_con_diferencia_exige_observacion(self):
        from core.devoluciones_sf import RecepcionSinFacturaError, registrar_recepcion_sin_factura

        with self.assertRaises(RecepcionSinFacturaError):
            registrar_recepcion_sin_factura(
                self.carga,
                usuario=self.bodega_user,
                cantidades={self.linea.id: (Decimal("3"), Decimal("0"))},
            )
        registrar_recepcion_sin_factura(
            self.carga,
            usuario=self.bodega_user,
            cantidades={self.linea.id: (Decimal("3"), Decimal("0"))},
            observaciones="Faltó una unidad",
        )
        self.devolucion.refresh_from_db()
        self.assertEqual(self.devolucion.estado, "CERRADA")

    def test_resumen_combinado_incluye_carga_sin_entregas_con_factura(self):
        from core.devoluciones_sf import cargas_para_bodega_combinado

        pendientes, recibidas = cargas_para_bodega_combinado()
        self.assertIn(self.carga, pendientes)

    def test_vista_bodega_recepcion_confirma_sin_factura(self):
        self.client.force_login(self.bodega_user)
        url = reverse("bodega_recepcion", args=[self.carga.id])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.devolucion.codigo_reporte)
        self.assertContains(r, self.motivo.nombre)
        r = self.client.post(
            url,
            {
                "seccion": "sf",
                f"invsf_{self.linea.id}": "5",
                f"mersf_{self.linea.id}": "0",
                "observaciones_bodega_sf": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.devolucion.refresh_from_db()
        self.assertEqual(self.devolucion.estado, "CERRADA")

    def test_vista_bodega_lista_muestra_carga_con_devolucion_sin_factura(self):
        self.client.force_login(self.bodega_user)
        r = self.client.get(reverse("bodega_lista"))
        self.assertContains(r, self.carga.codigo_reporte)


class ImprimirCargaConDevolucionSinFacturaTests(DevolucionSinFacturaBase):
    def test_reporte_muestra_devolucion_asignada(self):
        carga = Carga.objects.create(estado="PLANEADA", ruta=self.ruta)
        self.crear_devolucion(carga=carga, estado="ASIGNADA")
        self.client.force_login(self.admin)
        r = self.client.get(reverse("admin:core_carga_imprimir", args=[carga.id]))
        self.assertContains(r, "Devoluciones sin factura a recolectar en esta ruta")

    def test_reporte_despacho_muestra_motivo_por_producto_ya_recolectado(self):
        """Cambio #27.1: si ya se recolectó, el despacho muestra el motivo de cada producto."""
        carga = Carga.objects.create(estado="EN_RUTA", ruta=self.ruta)
        devolucion = self.crear_devolucion(
            carga=carga, estado="RECOLECTADA", recolectada_en=timezone.now()
        )
        DevolucionSinFacturaDetalle.objects.create(
            devolucion=devolucion,
            producto=self.producto,
            cantidad_recolectada=Decimal("3"),
            motivo=self.motivo,
        )
        self.client.force_login(self.admin)
        r = self.client.get(reverse("admin:core_carga_imprimir", args=[carga.id]))
        self.assertContains(r, "Motivo del producto")
        self.assertContains(r, self.motivo.nombre)

    def test_reporte_cierre_muestra_devoluciones_sin_factura(self):
        """Cambio #27.1: el reporte de cierre también lista el motivo por producto."""
        carga = Carga.objects.create(estado="CERRADA", ruta=self.ruta)
        devolucion = self.crear_devolucion(
            carga=carga,
            estado="CERRADA",
            recolectada_en=timezone.now(),
            recibido_en=timezone.now(),
            recibido_por=self.bodega_user,
        )
        DevolucionSinFacturaDetalle.objects.create(
            devolucion=devolucion,
            producto=self.producto,
            cantidad_recolectada=Decimal("3"),
            recibido_inventario=Decimal("3"),
            recibido_merma=Decimal("0"),
            motivo=self.motivo,
        )
        self.client.force_login(self.admin)
        r = self.client.get(reverse("admin:core_carga_imprimir", args=[carga.id]))
        self.assertContains(r, "Devoluciones sin factura")
        self.assertContains(r, self.motivo.nombre)


class PantallasVentasDevolucionesTests(DevolucionSinFacturaBase):
    def test_acceso_sin_login(self):
        r = self.client.get(reverse("devoluciones_sf_acceso"))
        self.assertEqual(r.status_code, 200)

    def test_solicitar_get_con_permiso(self):
        self.client.force_login(self.ventas)
        r = self.client.get(reverse("devoluciones_sf_solicitar"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Nueva solicitud")

    def test_sin_permiso_muestra_aviso(self):
        sin_permiso = User.objects.create_user("x2", password="x")
        self.client.force_login(sin_permiso)
        r = self.client.get(reverse("devoluciones_sf_solicitar"))
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "no tiene permiso", status_code=403)


class Cambio28Base(DevolucionSinFacturaBase):
    """Cambio #28: lote, vencimiento, varias líneas por producto y cartera."""

    def setUp(self):
        super().setUp()
        self.otro_motivo = (
            MotivoDevolucion.objects.filter(activo=True).exclude(pk=self.motivo.pk).first()
            or MotivoDevolucion.objects.create(nombre="Empaque dañado (prueba)")
        )
        self.cartera_user = User.objects.create_user("cartera1", password="x")
        grupo_cartera, _ = Group.objects.get_or_create(name="Cartera")
        self.cartera_user.groups.add(grupo_cartera)
        self.vence = self.hoy + timedelta(days=40)

    def entrega_con_devolucion(self, partes=None, entregada="4"):
        """Factura de 10 unidades; se entregan 4 y se devuelven 6."""
        from core.entregas import registrar_entrega

        self.carga = Carga.objects.create(
            estado="EN_RUTA", ruta=self.ruta, conductor=self.repartidor,
            salio_a_ruta_en=timezone.now(),
        )
        factura = self.factura("F-28", carga=self.carga, cargada_en=timezone.now())
        linea = factura.lineas.first()
        entrega = registrar_entrega(
            factura=factura,
            cantidades_entregadas={linea.id: Decimal(entregada)},
            devolucion_por_linea={linea.id: partes} if partes is not None else None,
            carga=self.carga,
        )
        return factura, linea, entrega


class EntregaVariosMotivosTests(Cambio28Base):
    def test_mismo_producto_con_dos_motivos_lote_y_vencimiento(self):
        factura, linea, entrega = self.entrega_con_devolucion(
            [
                {"cantidad": "4", "motivo_id": self.motivo.id, "lote": " L-1 ", "fecha_vencimiento": "2026-10-30"},
                {"cantidad": "2", "motivo_id": self.otro_motivo.id, "lote": "L-2", "fecha_vencimiento": self.vence.isoformat()},
            ]
        )
        detalles = list(entrega.lineas.order_by("id"))
        self.assertEqual(len(detalles), 2)
        self.assertEqual(detalles[0].cantidad_entregada, Decimal("4"))
        self.assertEqual(detalles[0].cantidad_devuelta, Decimal("4"))
        self.assertEqual(detalles[0].lote, "L-1")
        self.assertEqual(str(detalles[0].fecha_vencimiento), "2026-10-30")
        self.assertEqual(detalles[1].cantidad_entregada, Decimal("0"))
        self.assertEqual(detalles[1].cantidad_devuelta, Decimal("2"))
        self.assertEqual(detalles[1].motivo_devolucion, self.otro_motivo)
        linea.refresh_from_db()
        self.assertEqual(linea.cantidad_entregada, Decimal("4"))
        self.assertEqual(linea.cantidad_devuelta, Decimal("6"))
        self.assertEqual(linea.pendiente_entrega, Decimal("0"))

    def test_una_sola_parte_toma_todo_lo_devuelto_y_lote_opcional(self):
        _, _, entrega = self.entrega_con_devolucion(
            [{"cantidad": "", "motivo_id": self.motivo.id, "lote": "", "fecha_vencimiento": ""}]
        )
        detalle = entrega.lineas.get()
        self.assertEqual(detalle.cantidad_devuelta, Decimal("6"))
        self.assertEqual(detalle.lote, "")
        self.assertIsNone(detalle.fecha_vencimiento)

    def test_partes_que_no_suman_lo_devuelto_no_guardan_nada(self):
        from core.entregas import RegistroEntregaError

        with self.assertRaises(RegistroEntregaError):
            self.entrega_con_devolucion(
                [
                    {"cantidad": "4", "motivo_id": self.motivo.id},
                    {"cantidad": "1", "motivo_id": self.otro_motivo.id},
                ]
            )
        self.assertEqual(Entrega.objects.count(), 0)
        self.assertEqual(FacturaDetalle.objects.get().cantidad_devuelta, Decimal("0"))

    def test_sin_detalle_funciona_como_antes(self):
        _, _, entrega = self.entrega_con_devolucion(None)
        detalle = entrega.lineas.get()
        self.assertEqual(detalle.cantidad_devuelta, Decimal("6"))

    def test_pantalla_de_captura_envia_varios_motivos(self):
        self.carga = Carga.objects.create(
            estado="EN_RUTA", ruta=self.ruta, conductor=self.repartidor,
            salio_a_ruta_en=timezone.now(),
        )
        factura = self.factura("F-28B", carga=self.carga, cargada_en=timezone.now(),
                               modalidad_pago=self.credito)
        linea = factura.lineas.first()
        self.client.force_login(self.admin)  # oficina: no pide llegada ni GPS
        url = reverse("entregas_captura", args=[self.carga.id, factura.id])
        r = self.client.get(url)
        self.assertContains(r, "Otro motivo para este producto")
        r = self.client.post(url, {
            f"ent_{linea.id}": "7",
            f"dev_cant_{linea.id}": ["2", "1"],
            f"dev_motivo_{linea.id}": [str(self.motivo.id), str(self.otro_motivo.id)],
            f"dev_lote_{linea.id}": ["LA", "LB"],
            f"dev_vence_{linea.id}": [self.vence.isoformat(), ""],
            "documento_cliente": "ORIGINAL",
        })
        self.assertEqual(r.status_code, 302)
        detalles = list(EntregaDetalle.objects.filter(factura_detalle=linea).order_by("id"))
        self.assertEqual([d.cantidad_devuelta for d in detalles], [Decimal("2"), Decimal("1")])
        self.assertEqual([d.lote for d in detalles], ["LA", "LB"])


class RecoleccionVariasLineasTests(Cambio28Base):
    def setUp(self):
        super().setUp()
        self.carga = Carga.objects.create(
            estado="EN_RUTA", ruta=self.ruta, conductor=self.repartidor,
            salio_a_ruta_en=timezone.now(),
        )
        self.devolucion = self.crear_devolucion(carga=self.carga, estado="ASIGNADA")

    def test_mismo_producto_en_dos_lineas(self):
        from core.devoluciones_sf import registrar_captura

        registrar_captura(
            self.devolucion,
            lineas_capturadas=[
                {"producto_id": self.producto.id, "cantidad": Decimal("10"), "motivo_id": self.motivo.id,
                 "lote": "L-2509", "fecha_vencimiento": "30/10/2026"},
                {"producto_id": self.producto.id, "cantidad": Decimal("3"), "motivo_id": self.otro_motivo.id,
                 "lote": "", "fecha_vencimiento": ""},
            ],
        )
        lineas = list(self.devolucion.detalles.order_by("id"))
        self.assertEqual(len(lineas), 2)
        self.assertEqual(lineas[0].lote, "L-2509")
        self.assertEqual(str(lineas[0].fecha_vencimiento), "2026-10-30")
        self.assertEqual(lineas[1].motivo, self.otro_motivo)
        self.assertIsNone(lineas[1].fecha_vencimiento)

    def test_pantalla_del_repartidor_no_junta_lineas_del_mismo_producto(self):
        self.client.post(
            reverse("entregas_acceso"),
            {"codigo_carga": self.carga.codigo_acceso, "pin_repartidor": "4321"},
        )
        url = reverse("entregas_devolucion_sf", args=[self.carga.id, self.devolucion.id])
        r = self.client.post(url, {
            "producto_id": [str(self.producto.id), str(self.producto.id)],
            "cantidad": ["10", "3"],
            "motivo_id": [str(self.motivo.id), str(self.otro_motivo.id)],
            "lote": ["L-1", "</script><b>"],
            "fecha_vencimiento": [self.vence.isoformat(), ""],
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.devolucion.detalles.count(), 2)
        # Al volver a abrir, el lote escrito no rompe el script de la página.
        r = self.client.get(url)
        self.assertContains(r, "Otra línea de este producto")
        self.assertNotContains(r, "</script><b>")


class BodegaLoteTests(Cambio28Base):
    def test_bodega_completa_lote_y_vencimiento(self):
        carga = Carga.objects.create(estado="RUTA_TERMINADA", ruta=self.ruta, conductor=self.repartidor)
        devolucion = self.crear_devolucion(carga=carga, estado="RECOLECTADA", recolectada_en=timezone.now())
        linea = DevolucionSinFacturaDetalle.objects.create(
            devolucion=devolucion, producto=self.producto, cantidad_recolectada=Decimal("5"), motivo=self.motivo,
        )
        self.client.force_login(self.bodega_user)
        url = reverse("bodega_recepcion", args=[carga.id])
        self.assertContains(self.client.get(url), f'name="lotesf_{linea.id}"')
        r = self.client.post(url, {
            "seccion": "sf",
            f"invsf_{linea.id}": "5", f"mersf_{linea.id}": "0",
            f"lotesf_{linea.id}": "L-77", f"vencesf_{linea.id}": "2026-01-15",
        })
        self.assertEqual(r.status_code, 302)
        linea.refresh_from_db()
        self.assertEqual(linea.lote, "L-77")
        self.assertEqual(str(linea.fecha_vencimiento), "2026-01-15")
        r = self.client.get(url)
        self.assertContains(r, "L-77")
        self.assertContains(r, "Vencido")


class CarteraTests(Cambio28Base):
    def _devolucion_sf(self, estado="CERRADA"):
        carga = Carga.objects.create(estado="RUTA_TERMINADA", ruta=self.ruta, conductor=self.repartidor)
        devolucion = self.crear_devolucion(
            carga=carga, estado=estado, recolectada_en=timezone.now(),
            recibido_en=timezone.now() if estado == "CERRADA" else None,
        )
        DevolucionSinFacturaDetalle.objects.create(
            devolucion=devolucion, producto=self.producto, cantidad_recolectada=Decimal("5"),
            motivo=self.motivo, lote="L-9", fecha_vencimiento=self.vence,
            recibido_inventario=Decimal("5") if estado == "CERRADA" else None,
            recibido_merma=Decimal("0") if estado == "CERRADA" else None,
        )
        return devolucion

    def test_no_se_puede_antes_de_bodega(self):
        from core.cartera import CarteraError, dar_por_enterado

        devolucion = self._devolucion_sf(estado="RECOLECTADA")
        with self.assertRaises(CarteraError):
            dar_por_enterado("sf", devolucion.id, usuario=self.cartera_user)

    def test_sin_factura_recibida_por_cartera(self):
        from core.cartera import CarteraError, dar_por_enterado, devoluciones_para_cartera

        devolucion = self._devolucion_sf()
        pendientes, _ = devoluciones_para_cartera("pendientes")
        self.assertEqual([i["clave"] for i in pendientes], [f"sf-{devolucion.id}"])
        dar_por_enterado("sf", devolucion.id, usuario=self.cartera_user, comentario="ok")
        devolucion.refresh_from_db()
        self.assertEqual(devolucion.revisada_cartera_por, self.cartera_user)
        self.assertEqual(devolucion.estado_mostrar, "Recibido por cartera")
        self.assertEqual(devoluciones_para_cartera("pendientes")[0], [])
        with self.assertRaises(CarteraError):
            dar_por_enterado("sf", devolucion.id, usuario=self.cartera_user)

    def test_con_factura_despues_de_recepcion_en_bodega(self):
        from core.bodega import registrar_recepcion
        from core.cartera import CarteraError, dar_por_enterado

        _, _, entrega = self.entrega_con_devolucion(
            [{"cantidad": "4", "motivo_id": self.motivo.id}, {"cantidad": "2", "motivo_id": self.otro_motivo.id}]
        )
        with self.assertRaises(CarteraError):
            dar_por_enterado("e", entrega.id, usuario=self.cartera_user)
        self.carga.estado = "RUTA_TERMINADA"
        self.carga.save()
        registrar_recepcion(
            self.carga, usuario=self.bodega_user,
            cantidades={d.id: (d.cantidad_devuelta, Decimal("0")) for d in entrega.lineas.all()},
        )
        dar_por_enterado("e", entrega.id, usuario=self.cartera_user)
        entrega.refresh_from_db()
        self.assertIsNotNone(entrega.revisada_cartera_en)

    def test_pantallas_de_cartera(self):
        devolucion = self._devolucion_sf()
        r = self.client.get(reverse("cartera_lista"))
        self.assertEqual(r.status_code, 302)
        self.client.force_login(self.bodega_user)
        self.assertEqual(self.client.get(reverse("cartera_lista")).status_code, 403)
        self.client.force_login(self.cartera_user)
        r = self.client.get(reverse("cartera_lista") + f"?sel=sf-{devolucion.id}")
        self.assertContains(r, devolucion.codigo_reporte)
        self.assertContains(r, "Dar por enterado")
        self.assertContains(r, "L-9")
        r = self.client.post(
            reverse("cartera_enterado", args=["sf", devolucion.id]),
            {"comentario": "Revisado", "volver": "ver=pendientes"},
        )
        self.assertEqual(r.status_code, 302)
        devolucion.refresh_from_db()
        self.assertTrue(devolucion.recibida_por_cartera)
        # Ventas ve el nuevo estatus en "Mis solicitudes".
        self.client.force_login(self.ventas)
        self.assertContains(self.client.get(reverse("devoluciones_sf_lista")), "Recibido por cartera")

    def test_reportes_de_la_carga_muestran_lote_y_cartera(self):
        from core.cartera import dar_por_enterado

        devolucion = self._devolucion_sf()
        dar_por_enterado("sf", devolucion.id, usuario=self.cartera_user)
        carga = devolucion.carga
        carga.estado = "CERRADA"
        carga.save()
        self.client.force_login(self.admin)
        r = self.client.get(reverse("admin:core_carga_imprimir", args=[carga.id]))
        self.assertContains(r, "L-9")
        self.assertContains(r, self.vence.strftime("%d/%m/%Y"))
        self.assertContains(r, "Recibido por cartera")
        r = self.client.get(reverse("admin:core_carga_cierre", args=[carga.id]))
        self.assertContains(r, "Devoluciones sin factura")
        self.assertContains(r, "L-9")


class ConsultaRutaDevolucionesTests(Cambio28Base):
    """Cambio #28: detalle de devoluciones en la consulta por ruta (pantalla y Excel)."""

    def setUp(self):
        super().setUp()
        self.ventas_consulta = User.objects.create_user("consulta1", password="x")
        self.ventas_consulta.groups.add(Group.objects.get(name="Ventas"))

    def test_pantalla_y_excel_traen_el_detalle(self):
        from io import BytesIO

        from openpyxl import load_workbook

        self.entrega_con_devolucion(
            [
                {"cantidad": "4", "motivo_id": self.motivo.id, "lote": "L-CF1", "fecha_vencimiento": self.vence.isoformat()},
                {"cantidad": "2", "motivo_id": self.otro_motivo.id, "lote": "L-CF2"},
            ]
        )
        dev = self.crear_devolucion(carga=self.carga, estado="RECOLECTADA", recolectada_en=timezone.now())
        DevolucionSinFacturaDetalle.objects.create(
            devolucion=dev, producto=self.producto, cantidad_recolectada=Decimal("3"),
            motivo=self.motivo, lote="L-SF1",
        )
        self.client.force_login(self.ventas_consulta)
        filtros = {"ruta": self.ruta.id, "desde": self.hoy.isoformat(), "hasta": self.hoy.isoformat()}
        r = self.client.get(reverse("ventas_ruta"), filtros)
        self.assertContains(r, "Detalle de devoluciones (3 productos)")
        for texto in ("L-CF1", "L-CF2", "L-SF1", dev.codigo_reporte, self.otro_motivo.nombre):
            self.assertContains(r, texto)

        r = self.client.get(reverse("ventas_ruta_excel"), filtros)
        libro = load_workbook(BytesIO(r.content))
        self.assertEqual(libro.sheetnames, ["Facturas", "Devoluciones"])
        hoja = libro["Devoluciones"]
        lotes = sorted(fila[14] for fila in hoja.iter_rows(min_row=4, values_only=True))
        self.assertEqual(lotes, ["L-CF1", "L-CF2", "L-SF1"])

        # Con filtro de estado no salen las devoluciones sin factura.
        r = self.client.get(reverse("ventas_ruta"), dict(filtros, estado="CON_DEVOLUCION"))
        self.assertNotContains(r, "L-SF1")


class ClienteYContactosDevolucionSFTests(DevolucionSinFacturaBase):
    """Cambio #29: cliente guardado en la devolución y lista de contactos."""

    def test_lista_inicial_de_contactos(self):
        from core.models import ContactoSucursal

        self.assertEqual(ContactoSucursal.objects.count(), 30)
        self.assertTrue(ContactoSucursal.objects.filter(nombre="Jazmina Hernández").exists())

    def test_solicitud_con_cliente_y_contacto(self):
        from core.models import ContactoSucursal

        contacto = ContactoSucursal.objects.get(nombre="Santos Ocampo")
        self.client.force_login(self.ventas)
        r = self.client.get(reverse("devoluciones_sf_solicitar"))
        self.assertContains(r, "Santos Ocampo")
        self.assertContains(r, f'data-cliente="{self.sucursal.cliente_id}"')
        r = self.client.post(reverse("devoluciones_sf_solicitar"), {
            "fecha_ingreso": self.hoy.isoformat(),
            "cliente": self.sucursal.cliente_id,
            "sucursal": self.sucursal.pk,
            "total_unidades_estimado": "5",
            "contacto": contacto.pk,
            "motivo": self.motivo.pk,
        })
        self.assertEqual(r.status_code, 302)
        d = DevolucionSinFactura.objects.get()
        self.assertEqual(d.cliente, self.sucursal.cliente)
        self.assertEqual(d.contacto, contacto)
        self.assertEqual(d.contacto_sucursal, "Santos Ocampo")
        r = self.client.get(reverse("devoluciones_sf_lista"))
        self.assertContains(r, self.sucursal.cliente.nombre)
        self.assertContains(r, "Santos Ocampo")

        # Eliminar el contacto de la lista no borra el nombre de la devolución.
        contacto.delete()
        d.refresh_from_db()
        self.assertIsNone(d.contacto)
        self.assertEqual(d.contacto_sucursal, "Santos Ocampo")

    def test_sucursal_de_otro_cliente_se_rechaza(self):
        otro = Cliente.objects.create(codigo="C-OTRO", nombre="Otro cliente")
        Sucursal.objects.create(cliente=otro, nombre="Otra sucursal")
        self.client.force_login(self.ventas)
        r = self.client.post(reverse("devoluciones_sf_solicitar"), {
            "fecha_ingreso": self.hoy.isoformat(),
            "cliente": otro.pk,
            "sucursal": self.sucursal.pk,
            "total_unidades_estimado": "5",
            "motivo": self.motivo.pk,
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Esa sucursal no es de este cliente.")
        self.assertEqual(DevolucionSinFactura.objects.count(), 0)

    def test_contacto_obligatorio(self):
        self.client.force_login(self.ventas)
        r = self.client.post(reverse("devoluciones_sf_solicitar"), {
            "fecha_ingreso": self.hoy.isoformat(),
            "cliente": self.sucursal.cliente_id,
            "sucursal": self.sucursal.pk,
            "total_unidades_estimado": "5",
            "motivo": self.motivo.pk,
        })
        self.assertContains(r, "Elige el contacto de la sucursal.")
        self.assertEqual(DevolucionSinFactura.objects.count(), 0)

    def test_cliente_se_toma_de_la_sucursal(self):
        d = self.crear_devolucion()
        self.assertEqual(d.cliente, self.sucursal.cliente)

    def test_admin_de_contactos(self):
        from core.models import ContactoSucursal

        self.client.force_login(self.admin)
        r = self.client.post(reverse("admin:core_contactosucursal_add"), {"nombre": "Nuevo Contacto", "activo": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(ContactoSucursal.objects.filter(nombre="Nuevo Contacto").exists())
        r = self.client.get(reverse("admin:core_contactosucursal_changelist"))
        self.assertContains(r, "Nuevo Contacto")


class ConsultaRutaSucursalTests(Cambio28Base):
    """Cambio #31: columna Sucursal en la consulta por ruta y fechas."""

    def test_columna_sucursal(self):
        self.factura("F-31", sucursal=self.sucursal)
        u = User.objects.create_user("consulta31", password="x")
        u.groups.add(Group.objects.get(name="Ventas"))
        self.client.force_login(u)
        r = self.client.get(reverse("ventas_ruta"), {"desde": self.hoy.isoformat(), "hasta": self.hoy.isoformat()})
        self.assertContains(r, "<th>Sucursal</th>", count=1)
        self.assertContains(r, self.sucursal.nombre)
        self.assertContains(r, "tabla-compacta")


class AccesoCarteraAdminTests(Cambio28Base):
    """Cambio #34: acceso a la consulta de cartera desde el inicio del admin."""

    def test_tarjeta_en_el_admin_y_regreso(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("admin:index"))
        self.assertContains(r, "Consulta de cartera")
        self.assertContains(r, reverse("cartera_cargas"))
        self.assertContains(r, reverse("cartera_lista"))
        r = self.client.get(reverse("cartera_cargas"))
        self.assertContains(r, "Ir al admin")

    def test_sin_permiso_no_ve_la_tarjeta(self):
        staff = User.objects.create_user("staff34", password="x", is_staff=True)
        self.client.force_login(staff)
        self.assertNotContains(self.client.get(reverse("admin:index")), "Consulta de cartera")


class CarteraCargasTests(Cambio28Base):
    """Cambio #36: consulta de cargas para cartera (reemplaza la recaudación por ruta)."""

    def setUp(self):
        super().setUp()
        from core.cierre import calcular_resultado  # noqa: F401  (misma lógica que el tablero)

        self.ruta2 = Ruta.objects.create(nombre="Ruta Sur 36")
        self.c1 = Carga.objects.create(estado="EN_RUTA", ruta=self.ruta, conductor=self.repartidor,
                                       fecha_planeada=self.hoy)
        self.c2 = Carga.objects.create(estado="CERRADA", ruta=self.ruta2, conductor=self.repartidor,
                                       fecha_planeada=self.hoy)
        self.f1 = self.factura("C36-1", total="100.00", carga=self.c1, cargada_en=timezone.now())
        self.f2 = self.factura("C36-2", total="250.00", carga=self.c1, cargada_en=timezone.now(),
                               modalidad_pago=self.credito, fecha_vencimiento=self.hoy + timedelta(days=30))
        self.f3 = self.factura("C36-3", total="40.00", carga=self.c2, cargada_en=timezone.now())
        cartera = CarteraCobro.objects.create(
            factura=self.f1, modalidad_pago=self.contado, monto_total=Decimal("100"),
            saldo_pendiente=Decimal("0"),
        )
        Pago.objects.create(cartera=cartera, monto=Decimal("60"), forma_pago="EFECTIVO", numero_recibo="R-601")
        Pago.objects.create(cartera=cartera, monto=Decimal("40"), forma_pago="TRANSFERENCIA", referencia="BAC-88")

    def test_sumas_cargas_y_detalle(self):
        from core.cartera_cargas import consulta

        d = consulta(self.hoy, self.hoy)
        t = d["totales"]
        self.assertEqual(t["cargas"], 2)
        self.assertEqual((t["total_n"], t["contado_n"], t["credito_n"]), (3, 2, 1))
        self.assertEqual(t["total_monto"], Decimal("390.00"))
        self.assertEqual(t["contado_cobrado"], Decimal("100.00"))
        self.assertEqual(t["credito_sin"], Decimal("250.00"))
        self.assertEqual(t["sin_visitar"], 3)
        c1 = next(c for c in d["cargas"] if c.pk == self.c1.pk)
        self.assertEqual((c1.total_facturas, c1.total_efectivo, c1.total_credito, c1.total_pagado),
                         (2, Decimal("100.00"), Decimal("250.00"), Decimal("100.00")))
        f1 = next(f for f in c1.detalle_facturas if f["numero_factura"] == "C36-1")
        self.assertEqual(f1["documento"], "Recibo R-601 (efectivo) · Ref. BAC-88 (transferencia)")
        f2 = next(f for f in c1.detalle_facturas if f["numero_factura"] == "C36-2")
        self.assertTrue(f2["documento"].startswith("Vence "))
        rutas = {g["nombre"]: g for g in d["por_ruta"]}
        self.assertEqual(rutas["Ruta Sur 36"]["contado_monto"], Decimal("40.00"))

    def test_filtros_varias_rutas_y_estados(self):
        from core.cartera_cargas import consulta

        d = consulta(self.hoy, self.hoy, [self.ruta2.id], ["CERRADA"])
        self.assertEqual([c.pk for c in d["cargas"]], [self.c2.pk])
        d = consulta(self.hoy, self.hoy, [self.ruta.id, self.ruta2.id], ["PLANEADA"])
        self.assertEqual(d["cargas"], [])

    def test_pantalla_excel_y_direccion_anterior(self):
        from io import BytesIO

        from openpyxl import load_workbook

        self.client.force_login(self.bodega_user)
        self.assertEqual(self.client.get(reverse("cartera_cargas")).status_code, 403)
        self.client.force_login(self.cartera_user)
        r = self.client.get(reverse("cartera_cargas"), {"ruta": [self.ruta.id]})
        self.assertContains(r, "Resumen por ruta")
        self.assertContains(r, "Total pagado recibido")
        self.assertContains(r, "Total pagado recibido C$")
        self.assertContains(r, "C36-2")
        self.assertNotContains(r, "C36-3")
        self.assertContains(r, "Recibo R-601")
        self.assertContains(r, "Consulta de cargas")
        self.assertNotContains(r, "Recaudación por ruta")
        r = self.client.get(reverse("cartera_cargas_excel"), {"ruta": [self.ruta.id]})
        libro = load_workbook(BytesIO(r.content))
        self.assertEqual(libro.sheetnames, ["Cargas", "Facturas", "Resumen por ruta"])
        facturas = sorted(f[2] for f in libro["Facturas"].iter_rows(min_row=4, values_only=True))
        self.assertEqual(facturas, ["C36-1", "C36-2"])
        resumen = list(libro["Resumen por ruta"].iter_rows(min_row=3, values_only=True))
        self.assertEqual(resumen[0][7], "Total pagado recibido C$")
        self.assertEqual(resumen[-1][7], 100.0)
        self.assertRedirects(self.client.get("/cartera/recaudacion/"), reverse("cartera_cargas"),
                             fetch_redirect_response=False)


class ImprimirDevolucionTests(CarteraTests):
    """Cambio #37: hoja imprimible de devoluciones (cartera y bodega)."""

    def test_sin_factura_dos_filas_y_bodegas(self):
        from core.impresion_devolucion import hoja

        dev = self._devolucion_sf()
        linea = dev.detalles.get()
        linea.recibido_inventario, linea.recibido_merma = Decimal("3"), Decimal("2")
        linea.save()
        dev.contacto_sucursal = "Karen Vargas"
        dev.save()
        h = hoja("sf", dev.id)
        self.assertEqual(h["numero"], dev.codigo_reporte)
        self.assertEqual(h["persona"], "Karen Vargas")
        self.assertEqual([(f["estado"], f["bodega"], f["cantidad"]) for f in h["filas"]],
                         [("A inventario", "Bodega 4", Decimal("3")), ("Merma", "Bodega 7", Decimal("2"))])
        self.assertEqual(h["filas"][0]["causa"], self.motivo.nombre)
        self.assertEqual(h["filas"][0]["lote"], "L-9")

    def test_no_se_imprime_antes_de_bodega(self):
        from core.impresion_devolucion import ImpresionError, hoja

        dev = self._devolucion_sf(estado="RECOLECTADA")
        with self.assertRaises(ImpresionError):
            hoja("sf", dev.id)
        self.client.force_login(self.cartera_user)
        r = self.client.get(reverse("cartera_lista") + f"?ver=todas&sel=sf-{dev.id}")
        self.assertContains(r, "Disponible cuando bodega la reciba")
        r = self.client.get(reverse("devolucion_imprimir", args=["sf", dev.id]))
        self.assertEqual(r.status_code, 404)

    def test_cartera_y_bodega_pueden_imprimir(self):
        dev = self._devolucion_sf()
        url = reverse("devolucion_imprimir", args=["sf", dev.id])
        self.client.force_login(self.cartera_user)
        r = self.client.get(reverse("cartera_lista") + f"?sel=sf-{dev.id}")
        self.assertContains(r, url)
        r = self.client.get(url + "?fisico=0040975")
        self.assertContains(r, "Bodega de Producto Terminado")
        self.assertContains(r, "0040975")
        self.assertContains(r, "Causa de la devolución")
        self.client.force_login(self.bodega_user)
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.get(reverse("bodega_recepcion", args=[dev.carga_id]))
        self.assertContains(r, "Imprimir devoluciones recibidas")
        self.assertContains(r, url)
        otro = User.objects.create_user("nadie37", password="x")
        self.client.force_login(otro)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_con_factura_precio_e_iva_de_la_factura(self):
        from core.bodega import registrar_recepcion
        from core.impresion_devolucion import hoja

        _, linea, entrega = self.entrega_con_devolucion(
            [{"cantidad": "4", "motivo_id": self.motivo.id, "lote": "L-A"},
             {"cantidad": "2", "motivo_id": self.otro_motivo.id}]
        )
        self.carga.estado = "RUTA_TERMINADA"
        self.carga.save()
        registrar_recepcion(self.carga, usuario=self.bodega_user,
                            cantidades={d.id: (d.cantidad_devuelta, Decimal("0")) for d in entrega.lineas.all()})
        h = hoja("e", entrega.id)
        self.assertEqual(len(h["filas"]), 2)
        self.assertEqual(h["filas"][0]["precio"], linea.precio_unitario)
        self.assertEqual(h["filas"][0]["factura"], linea.factura.numero_factura)
        self.assertEqual({f["bodega"] for f in h["filas"]}, {"Bodega 4"})
