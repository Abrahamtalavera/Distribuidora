"""Cambio #39: flota — documentos, avisos, combustible y admin agrupado."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import flota
from core.avisos_flota import documentos_para_avisar, enviar_avisos
from core.models import (
    AbastecimientoCombustible,
    Carga,
    DocumentoFlota,
    EjecucionAvisosFlota,
    TarjetaFlota,
    TipoDocumentoFlota,
    Vehiculo,
    Vendedor,
)

CORREO = dict(
    EMAIL_HOST="smtp.prueba", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    AVISOS_FLOTA_CORREOS=["logistica@solmaya.com.ni", "atalavera@solmaya.com.ni"],
    AVISOS_FLOTA_EN_SEGUNDO_PLANO=False,
)


class FlotaBase(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.camion = Vehiculo.objects.create(placa="M 245-118", descripcion="Camión 1", capacidad_tanque=Decimal("80"))
        self.panel = Vehiculo.objects.create(placa="M 118-905", descripcion="Panel 1")
        self.juan = Vendedor.objects.create(nombre="Juan Repartidor", tipo="REPARTIDOR", pin_acceso="4321")
        self.seguro = TipoDocumentoFlota.objects.get(nombre="Seguro")
        self.licencia = TipoDocumentoFlota.objects.get(nombre="Licencia de conducir")
        self.logistica = User.objects.create_user("logi", password="x")
        self.logistica.groups.add(Group.objects.get(name="Logística"))
        self.otro = User.objects.create_user("otro", password="x")
        self.admin = User.objects.create_superuser("jefe", password="x")

    def doc(self, tipo=None, dias=10, **kw):
        kw.setdefault("vehiculo", None if (tipo or self.seguro).aplica_a == "CONDUCTOR" else self.camion)
        if (tipo or self.seguro).aplica_a == "CONDUCTOR":
            kw.setdefault("conductor", self.juan)
        return DocumentoFlota.objects.create(
            tipo=tipo or self.seguro, fecha_vencimiento=self.hoy + timedelta(days=dias), **kw
        )


class DocumentosTests(FlotaBase):
    def test_estados_y_renovacion(self):
        self.assertEqual(flota.estado_documento(self.doc(dias=-3))["clave"], flota.VENCIDO)
        self.assertEqual(flota.estado_documento(self.doc(dias=5))["clave"], flota.SEMANA)
        self.assertEqual(flota.estado_documento(self.doc(dias=21))["texto"], "21 días")
        self.assertEqual(flota.estado_documento(self.doc(dias=45))["clave"], flota.DOS_MESES)
        self.assertEqual(flota.estado_documento(self.doc(dias=200))["clave"], flota.VIGENTE)

        DocumentoFlota.objects.all().delete()
        viejo = self.doc(dias=3, numero="P-1")
        nuevo = flota.registrar_documento(
            tipo=self.seguro, vehiculo=self.camion, numero="P-2",
            fecha_vencimiento=self.hoy + timedelta(days=365), usuario=self.logistica,
        )
        viejo.refresh_from_db()
        self.assertFalse(viejo.vigente)
        self.assertTrue(nuevo.vigente)
        with self.assertRaises(flota.FlotaError):
            flota.registrar_documento(tipo=self.licencia, vehiculo=self.camion)

    def test_faltantes_y_vencimientos(self):
        self.doc(dias=-1)
        filas, conteos = flota.vencimientos()
        self.assertEqual(conteos["vencidos"], 1)
        # 5 tipos de vehículo × 2 vehículos + licencia de Juan − el seguro registrado
        self.assertEqual(conteos["faltan"], 10)
        filas, _ = flota.vencimientos("faltan", "conductores")
        self.assertEqual([(f["tipo"].nombre, f["conductor"]) for f in filas], [("Licencia de conducir", self.juan)])

    def test_calendario_de_avisos(self):
        d = self.doc(dias=90)
        self.assertEqual(flota.proximo_aviso(d, self.hoy), self.hoy + timedelta(days=30))
        d = self.doc(tipo=self.licencia, dias=60)
        self.assertEqual(flota.proximo_aviso(d, self.hoy), self.hoy)
        d.ultimo_aviso_en, d.ultimo_aviso_dias = self.hoy, 60
        self.assertEqual(flota.proximo_aviso(d, self.hoy), self.hoy + timedelta(days=30))
        # Ya en el último mes: aviso del mes, luego semanal, también vencido.
        hoy2 = self.hoy + timedelta(days=31)
        self.assertEqual(flota.proximo_aviso(d, hoy2), hoy2)
        d.ultimo_aviso_en, d.ultimo_aviso_dias = hoy2, 29
        self.assertEqual(flota.proximo_aviso(d, hoy2), hoy2 + timedelta(days=7))
        hoy3 = self.hoy + timedelta(days=70)  # vencido hace 10 días
        d.ultimo_aviso_en, d.ultimo_aviso_dias = hoy3 - timedelta(days=7), -3
        self.assertEqual(flota.proximo_aviso(d, hoy3), hoy3)

    def test_correo_apagado_no_marca_nada(self):
        self.doc(dias=20)
        n, mensaje = enviar_avisos()
        self.assertEqual(n, 0)
        self.assertIn("no está configurado", mensaje)
        self.assertFalse(EjecucionAvisosFlota.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(**CORREO)
    def test_correo_diario_a_logistica(self):
        d = self.doc(dias=20, numero="Póliza 55")
        self.doc(dias=200)  # todavía no toca
        n, _ = enviar_avisos()
        self.assertEqual(n, 1)
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ["logistica@solmaya.com.ni", "atalavera@solmaya.com.ni"])
        self.assertIn("Póliza 55", correo.body)
        self.assertIn("M 245-118", correo.body)
        d.refresh_from_db()
        self.assertEqual((d.ultimo_aviso_en, d.ultimo_aviso_dias), (self.hoy, 20))
        # El mismo día no se repite.
        self.assertEqual(enviar_avisos()[0], 0)
        self.assertEqual(len(mail.outbox), 1)
        # A la semana vuelve a tocar.
        self.assertEqual(documentos_para_avisar(self.hoy + timedelta(days=7)), [d])

    @override_settings(**CORREO)
    def test_al_conductor_no_se_le_manda_correo(self):
        self.doc(tipo=self.licencia, dias=6)
        enviar_avisos()
        self.assertEqual(mail.outbox[0].to, CORREO["AVISOS_FLOTA_CORREOS"])


class PantallasLogisticaTests(FlotaBase):
    def test_permisos(self):
        url = reverse("logistica_flota")
        self.assertRedirects(self.client.get(url), f"{reverse('logistica_acceso')}?next={url}")
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.logistica)
        for nombre in ("logistica_flota", "logistica_vencimientos", "logistica_conductores", "logistica_combustible"):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)

    def test_ficha_vehiculo_y_documento_con_archivo(self):
        self.client.force_login(self.logistica)
        r = self.client.post(reverse("logistica_vehiculo_guardar", args=[self.camion.id]), {
            "marca": "Hino", "modelo": "300", "anio": "2019", "tipo": "CAMION",
            "combustible": "DIESEL", "capacidad_tanque": "100", "propiedad": "PROPIO",
        })
        self.assertRedirects(r, f"{reverse('logistica_flota')}?v={self.camion.id}")
        self.camion.refresh_from_db()
        self.assertEqual((self.camion.marca, self.camion.anio, self.camion.capacidad_tanque), ("Hino", 2019, Decimal("100")))

        pdf = SimpleUploadedFile("poliza.pdf", b"%PDF-1.4 prueba", content_type="application/pdf")
        self.client.post(reverse("logistica_documento"), {
            "tipo": self.seguro.id, "vehiculo": self.camion.id, "numero": "P-9",
            "vence": (self.hoy + timedelta(days=15)).isoformat(), "archivo": pdf,
        })
        d = DocumentoFlota.objects.get(numero="P-9")
        self.assertEqual(d.registrado_por, self.logistica)
        r = self.client.get(reverse("logistica_archivo", args=[d.archivo_id]))
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertEqual(r.content, b"%PDF-1.4 prueba")
        r = self.client.get(reverse("logistica_vencimientos") + "?ver=mes")
        self.assertContains(r, "P-9")
        self.assertContains(r, "Pendiente de configurar la cuenta de correo")
        # Archivo no permitido
        r = self.client.post(reverse("logistica_documento"), {
            "tipo": self.seguro.id, "vehiculo": self.camion.id,
            "archivo": SimpleUploadedFile("x.exe", b"MZ", content_type="application/octet-stream"),
        }, follow=True)
        self.assertContains(r, "debe ser una foto o un PDF")

    def test_tablero_muestra_documentos(self):
        from core.tablero import alerta_documentos_flota

        self.doc(dias=-2)
        a = alerta_documentos_flota(self.hoy)
        self.assertEqual((a["total"], a["nivel"]), (1, "error"))


class CombustibleTests(FlotaBase):
    def setUp(self):
        super().setUp()
        self.carga = Carga.objects.create(conductor=self.juan, vehiculo=self.camion)
        self.tarjeta = TarjetaFlota.objects.create(ultimos4="4471", vehiculo=self.camion)

    def entrar_conductor(self):
        self.client.post(reverse("entregas_acceso"), {"codigo_carga": self.carga.codigo_acceso, "pin_repartidor": "4321"})

    def registrar(self, **datos):
        base = {"vehiculo": self.camion.id, "odometro": "84,210", "litros": "52", "monto": "2,200.40",
                "estacion": "Puma", "comprobante": "A1", "tarjeta": self.tarjeta.id}
        base.update(datos)
        return self.client.post(reverse("entregas_combustible", args=[self.carga.id]), base)

    def test_conductor_registra_y_ve_aviso_de_licencia(self):
        self.doc(tipo=self.licencia, dias=6)
        self.entrar_conductor()
        r = self.client.get(reverse("entregas_lista", args=[self.carga.id]))
        self.assertContains(r, "Tu licencia de conducir vence en 6 días")
        self.assertContains(r, reverse("entregas_combustible", args=[self.carga.id]))
        r = self.registrar()
        self.assertRedirects(r, reverse("entregas_combustible", args=[self.carga.id]))
        a = AbastecimientoCombustible.objects.get()
        self.assertEqual((a.odometro, a.monto, a.conductor, a.carga, a.estado), (84210, Decimal("2200.40"), self.juan, self.carga, "PENDIENTE"))
        self.assertEqual(a.original["odometro"], 84210)
        r = self.client.get(reverse("entregas_combustible", args=[self.carga.id]))
        self.assertContains(r, "Por revisar")
        r = self.registrar(litros="abc")
        self.assertContains(r, "no es un número válido")

    def test_sin_acceso_no_registra(self):
        r = self.registrar()
        self.assertRedirects(r, reverse("entregas_acceso"))
        self.assertFalse(AbastecimientoCombustible.objects.exists())

    def test_alertas_rendimiento_y_revision(self):
        self.entrar_conductor()
        self.registrar(odometro="1000", litros="50", comprobante="1")
        self.registrar(odometro="1400", litros="50", comprobante="2")   # 8 km/l
        self.registrar(odometro="1800", litros="50", comprobante="3")   # 8 km/l
        self.registrar(odometro="1774", litros="90", comprobante="3")   # menor, > tanque, repetido
        filas, r, _ = flota.consulta_combustible(self.hoy, self.hoy)
        ultima = filas[0]
        textos = [t for _, t in ultima.alertas]
        self.assertIn("Odómetro menor al anterior", textos)
        self.assertIn("Más litros que el tanque (80 l)", textos)
        self.assertIn("Comprobante repetido", textos)
        self.assertEqual(r["por_revisar"], 4)
        self.assertEqual(r["km"], 800)
        self.assertEqual(r["rendimiento"], Decimal("8.0"))

        self.client.force_login(self.logistica)
        r = self.client.get(reverse("logistica_combustible") + f"?sel={ultima.id}")
        self.assertContains(r, "Odómetro menor al anterior")
        self.client.post(reverse("logistica_combustible_revisar", args=[ultima.id]), {
            "vehiculo": self.camion.id, "odometro": "2174", "litros": "50", "monto": "2,200.40",
            "comprobante": "4", "estacion": "Puma", "tarjeta": self.tarjeta.id, "nota": "Según foto",
        })
        ultima.refresh_from_db()
        self.assertEqual((ultima.estado, ultima.odometro, ultima.revisado_por), ("CORREGIDA", 2174, self.logistica))
        self.assertEqual(ultima.original["odometro"], 1774)
        sin_cambios = AbastecimientoCombustible.objects.get(comprobante="1")
        self.client.post(reverse("logistica_combustible_revisar", args=[sin_cambios.id]), {
            "vehiculo": self.camion.id, "odometro": "1000", "litros": "50.00", "monto": "2200.40",
            "comprobante": "1", "estacion": "Puma", "tarjeta": self.tarjeta.id,
        })
        sin_cambios.refresh_from_db()
        self.assertEqual(sin_cambios.estado, "REVISADA")
        r = self.client.get(reverse("logistica_combustible_excel") + "?estado=todas")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"PK"))


class AdminAgrupadoTests(FlotaBase):
    def test_bloques_y_tarjeta(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("admin:index"))
        html = r.content.decode()
        for texto in ("DISTRIBUCIÓN", "FLOTA", "CONFIGURACIÓN", "Clientes y ventas", "Combustible",
                      "Abastecimientos de combustible", "Conductores y licencias", "Logística — Flota"):
            self.assertIn(texto, html)
        self.assertLess(html.index("DISTRIBUCIÓN"), html.index(">FLOTA<"))
        self.assertLess(html.index(">FLOTA<"), html.index("CONFIGURACIÓN"))
        nombres = [a["name"] for a in r.context["app_list"]]
        self.assertEqual(nombres[:4], ["Clientes y ventas", "Rutas y entregas", "Devoluciones", "Cartera y cobros"])
        self.assertNotIn("Otros", nombres)
        # La página de una app sigue funcionando.
        self.assertEqual(self.client.get(reverse("admin:app_list", args=["core"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:core_conductor_changelist")).status_code, 200)
