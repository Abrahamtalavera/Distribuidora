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
    Entrega,
    EntregaDetalle,
    Factura,
    FacturaDetalle,
    ModalidadPago,
    Pago,
    Producto,
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
