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
