import random

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

# Cambio #12: alfabeto usado para generar códigos de acceso cortos (carga) y
# PINs (repartidor). Se excluyen caracteres que se confunden fácilmente al
# leerlos en un celular: 0/O, 1/I/L.
ALFABETO_CODIGO_ACCESO = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


class ModalidadPago(models.Model):
    codigo = models.CharField(max_length=20, unique=True)  # CONTADO, CREDITO, CONSIGNACION
    nombre = models.CharField(max_length=50)
    descripcion = models.TextField(blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Modalidad de pago"
        verbose_name_plural = "Modalidades de pago"

    def __str__(self):
        return self.nombre


class Ruta(models.Model):
    nombre = models.CharField(max_length=100)
    zona = models.CharField(max_length=100, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Rutas"

    def __str__(self):
        return self.nombre


class Vendedor(models.Model):
    TIPO_CHOICES = [
        ("VENDEDOR", "Vendedor"),
        ("COBRADOR", "Cobrador"),
        ("REPARTIDOR", "Repartidor"),
    ]
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default="VENDEDOR")
    telefono = models.CharField(max_length=30, blank=True)
    activo = models.BooleanField(default=True)
    pin_acceso = models.CharField(
        max_length=10,
        blank=True,
        verbose_name="PIN de acceso (repartidor)",
        help_text=(
            "Clave corta que usa el repartidor, junto con el código de la "
            "carga, para entrar a registrar entregas desde su celular. "
            "Solo aplica a repartidores."
        ),
    )

    class Meta:
        verbose_name_plural = "Vendedores"

    def __str__(self):
        return self.nombre


class Vehiculo(models.Model):
    placa = models.CharField(max_length=20, unique=True)
    descripcion = models.CharField(max_length=100, blank=True)
    activo = models.BooleanField(default=True)
    pide_paquetes_costo = models.BooleanField(
        default=False,
        verbose_name="Pide paquetes y costo",
        help_text=(
            "Al planear una Carga con esta unidad, cada factura debe llevar "
            "paquetes y costo (Cambio #18)."
        ),
    )

    class Meta:
        verbose_name_plural = "Vehículos"

    def __str__(self):
        return self.placa


class Carga(models.Model):
    """
    Agrupación de facturas que arma el planeador de rutas para una salida de
    reparto (Cambio #7). El número consecutivo de este registro (su id) es el
    "identificador de carga" al que se le asocian los datos de la salida:
    conductor, ruta, unidad de transporte, kilometraje, etc.
    """

    ESTADO_CHOICES = [
        ("PLANEADA", "Planeada"),
        ("EN_RUTA", "En ruta"),
        ("RUTA_TERMINADA", "Ruta terminada"),
        ("CERRADA", "Cerrada"),
    ]
    # Cambio #14: mientras la carga está en uno de estos estados el
    # repartidor ya no puede registrar entregas ni cobros.
    ESTADOS_BLOQUEADOS_REPARTIDOR = ("RUTA_TERMINADA", "CERRADA")

    fecha_planeada = models.DateField(default=timezone.localdate)
    ruta = models.ForeignKey(
        "Ruta", null=True, blank=True, on_delete=models.SET_NULL, related_name="cargas"
    )
    conductor = models.ForeignKey(
        "Vendedor",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        limit_choices_to={"tipo": "REPARTIDOR"},
        related_name="cargas_como_conductor",
    )
    vehiculo = models.ForeignKey(Vehiculo, null=True, blank=True, on_delete=models.SET_NULL)
    km_inicial = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    km_final = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="PLANEADA")
    observaciones = models.TextField(blank=True)
    codigo_acceso = models.CharField(
        max_length=10,
        unique=True,
        blank=True,
        editable=False,
        help_text=(
            "Código corto y no consecutivo (distinto del número de carga) "
            "que, junto con el PIN del repartidor, permite entrar desde el "
            "celular a registrar las entregas de esta carga. Se genera "
            "automáticamente al crear la carga."
        ),
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    # --- Cambio #14: cierre de la carga en dos pasos -----------------------
    ruta_terminada_en = models.DateTimeField(
        null=True, blank=True, editable=False, verbose_name="Ruta terminada el"
    )
    observaciones_repartidor = models.TextField(
        blank=True,
        verbose_name="Observaciones del repartidor",
        help_text="Lo que escribe el repartidor al marcar 'Terminé mi ruta'.",
    )
    cerrada_en = models.DateTimeField(
        null=True, blank=True, editable=False, verbose_name="Cerrada el"
    )
    cerrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="cargas_cerradas",
        verbose_name="Cerrada por",
    )
    observaciones_cierre = models.TextField(
        blank=True,
        verbose_name="Observaciones del cierre (oficina)",
        help_text="Obligatoria cuando el dinero recibido no cuadra con el sistema.",
    )
    facturas_liberadas = models.ManyToManyField(
        "Factura",
        blank=True,
        editable=False,
        related_name="cargas_que_la_liberaron",
        help_text=(
            "Facturas que seguían sin visitar al cerrar la carga y que por "
            "eso salieron de ella (quedaron 'Sin carga asignada'). Se guardan "
            "aquí para que el reporte de cierre las siga mostrando."
        ),
    )

    # --- Cambio #17: salida a ruta ------------------------------------------
    salio_a_ruta_en = models.DateTimeField(
        null=True, blank=True, editable=False, verbose_name="Salió a ruta el"
    )
    facturas_no_cargadas = models.ManyToManyField(
        "Factura",
        blank=True,
        editable=False,
        related_name="cargas_que_no_la_cargaron",
        help_text=(
            "Facturas que no se marcaron como cargadas y que por eso salieron "
            "de la carga al tocar 'Salir a ruta' (quedaron 'Sin carga "
            "asignada'). Se guardan para que el cierre las siga mostrando."
        ),
    )

    # --- Cambio #16: recepción de devoluciones en bodega de producto terminado
    devoluciones_recibidas_en = models.DateTimeField(
        null=True, blank=True, editable=False, verbose_name="Devoluciones recibidas el"
    )
    devoluciones_recibidas_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="cargas_recibidas_en_bodega",
        verbose_name="Devoluciones recibidas por",
    )
    observaciones_bodega = models.TextField(
        blank=True,
        verbose_name="Observaciones de bodega",
        help_text="Obligatoria cuando lo recibido en bodega no coincide con lo reportado.",
    )

    class Meta:
        verbose_name = "Carga"
        verbose_name_plural = "Cargas"
        ordering = ["-fecha_planeada", "-id"]
        permissions = [
            ("recibir_devoluciones", "Puede recibir devoluciones en bodega"),
            # Cambio #19: tablero de operación (Administración y Supervisores).
            ("ver_tablero", "Puede ver el tablero de operación"),
        ]

    def __str__(self):
        return f"Carga #{self.id} - {self.fecha_planeada}"

    @staticmethod
    def generar_codigo_acceso_unico():
        """
        Cambio #12: genera un código de acceso corto que no se repita con
        ninguna carga existente. No usa el id consecutivo de la carga para
        que no sea adivinable (CARGA-0042 sí es público/visible en reportes,
        este código no).
        """
        while True:
            candidato = "".join(random.choice(ALFABETO_CODIGO_ACCESO) for _ in range(5))
            if not Carga.objects.filter(codigo_acceso=candidato).exists():
                return candidato

    def save(self, *args, **kwargs):
        if not self.codigo_acceso:
            self.codigo_acceso = self.generar_codigo_acceso_unico()
        super().save(*args, **kwargs)

    @property
    def pide_paquetes_costo(self):
        """Cambio #18: la unidad de esta carga pide paquetes y costo por factura."""
        return bool(self.vehiculo_id and self.vehiculo.pide_paquetes_costo)

    @property
    def en_fase_de_carga(self):
        """Cambio #17: antes de 'Salir a ruta' el repartidor carga el camión."""
        return self.estado == "PLANEADA"

    @property
    def bloqueada_para_repartidor(self):
        return self.estado in self.ESTADOS_BLOQUEADOS_REPARTIDOR

    @property
    def esta_cerrada(self):
        return self.estado == "CERRADA"

    @property
    def codigo_reporte(self):
        return f"CARGA-{self.id:04d}"


class CargaCuadre(models.Model):
    """
    Cambio #14: cuadre de dinero del cierre de una carga, una fila por forma
    de pago en dinero (efectivo, cheque, transferencia, tarjeta). Guarda lo
    que decía el sistema en el momento del cierre y lo que oficina recibió,
    para que el reporte de cierre no cambie si después se registran más
    pagos de esas facturas.
    """

    carga = models.ForeignKey(Carga, on_delete=models.CASCADE, related_name="cuadre")
    forma_pago = models.CharField(max_length=20)
    segun_sistema = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    recibido = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Cuadre de dinero"
        verbose_name_plural = "Cuadre de dinero"
        unique_together = ("carga", "forma_pago")

    def __str__(self):
        return f"{self.carga_id} / {self.forma_pago}"

    @property
    def diferencia(self):
        return self.recibido - self.segun_sistema


class Cliente(models.Model):
    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=150)
    municipio = models.CharField(max_length=100, blank=True)
    sucursal = models.CharField(max_length=150, blank=True)
    canal = models.CharField(max_length=100, blank=True)
    territorio = models.CharField(max_length=100, blank=True)
    direccion = models.CharField(max_length=200, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    ruta = models.ForeignKey(Ruta, null=True, blank=True, on_delete=models.SET_NULL)
    modalidad_pago_default = models.ForeignKey(
        ModalidadPago, null=True, blank=True, on_delete=models.SET_NULL
    )
    # Cambio #22: ubicación del cliente para el indicador de GPS de las
    # entregas. Se toma sola de la primera entrega con buena señal, o se
    # escribe a mano en el admin.
    latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    ubicacion_origen = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Origen de la ubicación",
        help_text="De dónde salió la ubicación (primera entrega con GPS, o escrita a mano).",
    )
    limite_credito = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    dias_credito = models.IntegerField(default=0)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Clientes"

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


class Sucursal(models.Model):
    """
    Cambio #25: sucursal (punto de entrega) de un Cliente, cada una con su
    propia dirección. Un mismo Cliente (código SAP) puede facturar a varias
    sucursales -- por ejemplo, un supermercado reparte a decenas de tiendas,
    cada una con su propia dirección de entrega. Antes de este cambio el
    sistema solo guardaba un texto suelto de "sucursal" y "dirección" en el
    propio Cliente (Cliente.sucursal / Cliente.direccion), tomado de la
    PRIMERA factura que llegaba de ese cliente y nunca actualizado después.

    Se carga y actualiza desde el maestro de clientes de SAP (ver
    core.importador_sucursales), re-importable: no se duplica si se vuelve a
    subir el mismo archivo o uno más reciente (clave: cliente + nombre).
    """

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="sucursales")
    nombre = models.CharField(max_length=150, verbose_name="Nombre de la sucursal")
    direccion = models.CharField(max_length=255, blank=True)
    departamento = models.CharField(max_length=100, blank=True)
    municipio = models.CharField(max_length=100, blank=True)
    territorio = models.CharField(max_length=100, blank=True)
    canal = models.CharField(max_length=100, blank=True)
    cod_grupo = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Código de grupo"
    )
    grupo = models.CharField(max_length=100, blank=True, verbose_name="Grupo")
    activo = models.BooleanField(default=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sucursal"
        verbose_name_plural = "Sucursales"
        ordering = ["cliente__codigo", "nombre"]
        unique_together = ("cliente", "nombre")

    def __str__(self):
        return f"{self.nombre} ({self.cliente.codigo})"


class Producto(models.Model):
    codigo = models.CharField(max_length=30, unique=True)
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    familia = models.CharField(max_length=100, blank=True)
    unidad_medida = models.CharField(max_length=20, default="UND")
    precio_unitario = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    costo = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Productos"

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


class Factura(models.Model):
    ESTADO_ENTREGA_CHOICES = [
        ("PENDIENTE", "Pendiente"),
        ("PARCIAL", "Parcial"),
        ("COMPLETA", "Completa"),
    ]
    ESTADO_FACTURA_CHOICES = [
        ("ACTIVA", "Activa"),
        ("ANULADA", "Anulada"),
    ]

    numero_factura = models.CharField(max_length=30, unique=True)
    serie = models.CharField(max_length=30, blank=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="facturas")
    # Cambio #25: sucursal/dirección de entrega de esta factura. Se resuelve
    # al importar el Excel de facturas, buscando en el catálogo de
    # Sucursales por cliente + nombre (columna "Sucursal" del Excel). Si no
    # hay coincidencia, o es una factura de antes de este cambio, queda sin
    # sucursal.
    sucursal = models.ForeignKey(
        "Sucursal", null=True, blank=True, on_delete=models.SET_NULL, related_name="facturas"
    )
    vendedor = models.ForeignKey(Vendedor, null=True, blank=True, on_delete=models.SET_NULL)
    modalidad_pago = models.ForeignKey(ModalidadPago, on_delete=models.PROTECT)
    fecha_emision = models.DateField()
    fecha_vencimiento = models.DateField(null=True, blank=True)
    fecha_sugerida_entrega = models.DateField(
        null=True,
        blank=True,
        help_text="El planeador la define factura por factura; por defecto es un día después de la fecha de emisión.",
    )
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    impuestos = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    estado_entrega = models.CharField(
        max_length=20, choices=ESTADO_ENTREGA_CHOICES, default="PENDIENTE"
    )
    estado_factura = models.CharField(
        max_length=20, choices=ESTADO_FACTURA_CHOICES, default="ACTIVA"
    )
    origen_importacion = models.CharField(
        max_length=100, blank=True, help_text="Nombre del archivo Excel del que se importó"
    )
    carga = models.ForeignKey(
        Carga,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="facturas",
        help_text="Carga (salida de reparto) a la que fue asignada esta factura",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    # Cambio #17: el repartidor marca la factura como "Cargada" cuando sube
    # su mercadería al camión, antes de salir a ruta. Solo una factura
    # cargada se puede entregar. Se borra cuando la factura sale de su carga.
    cargada_en = models.DateTimeField(
        null=True, blank=True, editable=False, verbose_name="Cargada el"
    )
    cargada_por = models.CharField(
        max_length=150, blank=True, editable=False, verbose_name="Cargada por"
    )
    # Cambio #18: datos que captura el planeador cuando la unidad de la carga
    # los pide (por ejemplo M000003). Son de la factura dentro de su carga.
    paquetes = models.PositiveIntegerField(null=True, blank=True, verbose_name="Paquetes")
    costo_flete = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name="Costo",
        help_text="Costo de llevar esta factura en la unidad (C$), lo escribe el planeador.",
    )

    class Meta:
        verbose_name_plural = "Facturas"
        ordering = ["-fecha_emision", "-numero_factura"]
        permissions = [
            # Cambio #21: pantalla de consulta del equipo de ventas.
            ("consultar_ventas", "Puede usar la consulta de ventas"),
        ]

    def __str__(self):
        return self.numero_factura

    @property
    def esta_cargada(self):
        return self.cargada_en is not None

    @property
    def falta_paquetes_costo(self):
        return self.paquetes is None or self.costo_flete is None


class FacturaDetalle(models.Model):
    factura = models.ForeignKey(Factura, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad_facturada = models.DecimalField(max_digits=12, decimal_places=3)
    precio_lista = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    descuento = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    precio_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    iva = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cantidad_entregada = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    cantidad_devuelta = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
        help_text=(
            "Cantidad de esta línea que se marcó como devuelta/rechazada en "
            "alguna entrega (Cambio #12). Se resta del pendiente igual que "
            "lo entregado, para no dejarla como 'pendiente' para siempre."
        ),
    )

    class Meta:
        verbose_name = "Línea de factura"
        verbose_name_plural = "Líneas de factura"

    def __str__(self):
        return f"{self.factura.numero_factura} / {self.producto.codigo}"

    @property
    def pendiente_entrega(self):
        return self.cantidad_facturada - self.cantidad_entregada - self.cantidad_devuelta


class AreaResponsable(models.Model):
    """
    Catálogo de áreas del negocio que pueden ser responsables de una
    devolución (Cambio #9): Bodega PT, Producción, Ventas, Logística, etc.
    Se deja como catálogo editable desde el admin para que se puedan agregar
    o renombrar áreas sin necesidad de un cambio de código.
    """

    nombre = models.CharField(max_length=100, unique=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Área responsable"
        verbose_name_plural = "Áreas responsables"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class MotivoDevolucion(models.Model):
    """
    Catálogo de motivos de devolución de productos en las entregas
    (Cambio #9), cada uno asociado al área responsable de esa devolución.
    Cargado inicialmente a partir de la lista de motivos que ya maneja el
    negocio (archivo "Motivos de devoluciones.xlsx").
    """

    nombre = models.CharField(max_length=150, unique=True)
    area_responsable = models.ForeignKey(
        AreaResponsable,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="motivos_devolucion",
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Motivo de devolución"
        verbose_name_plural = "Motivos de devolución"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Entrega(models.Model):
    TIPO_CHOICES = [("PARCIAL", "Parcial"), ("TOTAL", "Total")]
    ESTADO_CHOICES = [("CONFIRMADA", "Confirmada"), ("ANULADA", "Anulada")]
    DOCUMENTO_CLIENTE_CHOICES = [("ORIGINAL", "Original"), ("COPIA", "Copia")]

    factura = models.ForeignKey(Factura, on_delete=models.PROTECT, related_name="entregas")
    carga = models.ForeignKey(
        Carga,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="entregas",
        help_text=(
            "Carga en la que se hizo esta visita (Cambio #14). Se guarda "
            "aparte de la carga de la factura para que el cierre de una carga "
            "no cambie si la factura se mueve después a otra carga."
        ),
    )
    fecha_entrega = models.DateTimeField(auto_now_add=True)
    tipo_entrega = models.CharField(max_length=10, choices=TIPO_CHOICES)
    repartidor = models.ForeignKey(Vendedor, null=True, blank=True, on_delete=models.SET_NULL)
    motivo_devolucion_general = models.ForeignKey(
        MotivoDevolucion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="entregas_con_motivo_general",
        help_text=(
            "Motivo que aplica a toda la visita (por ejemplo, local cerrado o "
            "cliente sin dinero). Déjalo vacío si lo que se devolvió fue solo "
            "algunos productos con su propio motivo."
        ),
    )
    observaciones = models.TextField(blank=True)
    documento_devolucion = models.CharField(
        max_length=30,
        blank=True,
        verbose_name="N.º de documento de devolución",
        help_text=(
            "Número del documento de devolución que llena el repartidor "
            "cuando en esta visita se devolvió algo (Cambio #15)."
        ),
    )
    documento_cliente = models.CharField(
        max_length=10,
        choices=DOCUMENTO_CLIENTE_CHOICES,
        blank=True,
        verbose_name="Factura dejada al cliente",
        help_text=(
            "Si al cliente se le dejó la factura original o una copia "
            "(Cambio #15). Vacío cuando en la visita no se entregó nada."
        ),
    )
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="CONFIRMADA")

    # --- Cambio #22: ubicación GPS del celular al registrar la entrega ------
    GPS_CHOICES = [
        ("OK", "Ubicación registrada"),
        ("DENEGADO", "El repartidor no permitió la ubicación"),
        ("NO_DISPONIBLE", "El celular no dio la ubicación"),
        ("OFICINA", "Registrada desde oficina"),
    ]
    gps_estado = models.CharField(
        max_length=15, choices=GPS_CHOICES, blank=True, verbose_name="GPS",
        help_text="Vacío en las entregas anteriores al Cambio #22.",
    )
    gps_latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    gps_longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    gps_precision = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Precisión GPS (m)"
    )

    # --- Cambio #28: revisión de cartera de la devolución de esta visita ----
    # Un analista de cartera "da por enterado" que revisó lo devuelto, y la
    # devolución pasa a "Recibido por cartera". Solo se puede después de que
    # bodega recibió la devolución.
    revisada_cartera_en = models.DateTimeField(
        null=True, blank=True, editable=False, verbose_name="Recibido por cartera el"
    )
    revisada_cartera_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Recibido por cartera (usuario)",
    )
    comentario_cartera = models.TextField(blank=True, verbose_name="Comentario de cartera")

    class Meta:
        verbose_name_plural = "Entregas"

    def __str__(self):
        return f"Entrega {self.id} - {self.factura.numero_factura}"


class EntregaDetalle(models.Model):
    entrega = models.ForeignKey(Entrega, on_delete=models.CASCADE, related_name="lineas")
    factura_detalle = models.ForeignKey(FacturaDetalle, on_delete=models.PROTECT)
    cantidad_entregada = models.DecimalField(max_digits=12, decimal_places=3)
    cantidad_devuelta = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    motivo_devolucion = models.ForeignKey(
        MotivoDevolucion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lineas_entrega",
        help_text="Motivo de devolución específico de este producto, si aplica.",
    )
    # Cambio #28: lote y vencimiento del producto devuelto. Son opcionales:
    # el repartidor los llena si puede, y bodega los completa o corrige al
    # recibir. Desde este cambio, un mismo producto de la factura puede
    # tener varias líneas en la misma visita (una por motivo/lote); las
    # líneas extra llevan cantidad_entregada = 0 y solo su parte devuelta.
    lote = models.CharField(max_length=40, blank=True, verbose_name="Lote")
    fecha_vencimiento = models.DateField(
        null=True, blank=True, verbose_name="Fecha de vencimiento"
    )
    # Cambio #16: lo que bodega de producto terminado recibió de esta
    # devolución, separado por destino. Vacío (None) = todavía no se ha
    # recibido. Lo recibido es la suma de los dos.
    recibido_inventario = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name="Recibido a inventario",
    )
    recibido_merma = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name="Recibido como merma",
    )

    class Meta:
        verbose_name = "Línea de entrega"
        verbose_name_plural = "Líneas de entrega"

    @property
    def esta_vencido(self):
        """Cambio #28: True si la fecha de vencimiento ya pasó."""
        from django.utils import timezone

        return bool(self.fecha_vencimiento and self.fecha_vencimiento < timezone.localdate())

    @property
    def recepcion_registrada(self):
        return self.recibido_inventario is not None or self.recibido_merma is not None

    @property
    def recibido_total(self):
        """Lo recibido en bodega (inventario + merma), o None si aún no se recibe."""
        if not self.recepcion_registrada:
            return None
        return (self.recibido_inventario or 0) + (self.recibido_merma or 0)

    @property
    def diferencia_recepcion(self):
        total = self.recibido_total
        return None if total is None else total - self.cantidad_devuelta


class CarteraCobro(models.Model):
    ESTADO_CHOICES = [
        ("VIGENTE", "Vigente"),
        ("VENCIDA", "Vencida"),
        ("PAGADA", "Pagada"),
        ("EN_MORA", "En mora"),
        ("ANULADA", "Anulada"),
    ]
    factura = models.OneToOneField(Factura, on_delete=models.PROTECT, related_name="cartera")
    modalidad_pago = models.ForeignKey(ModalidadPago, on_delete=models.PROTECT)
    monto_total = models.DecimalField(max_digits=14, decimal_places=2)
    saldo_pendiente = models.DecimalField(max_digits=14, decimal_places=2)
    fecha_creacion = models.DateField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="VIGENTE")
    cobrador = models.ForeignKey(Vendedor, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = "Cartera de cobro"
        verbose_name_plural = "Cartera de cobro"

    def __str__(self):
        return f"Cartera {self.factura.numero_factura}"


class PlanPago(models.Model):
    ESTADO_CHOICES = [
        ("PENDIENTE", "Pendiente"),
        ("PAGADA", "Pagada"),
        ("VENCIDA", "Vencida"),
    ]
    cartera = models.ForeignKey(CarteraCobro, on_delete=models.CASCADE, related_name="cuotas")
    numero_cuota = models.IntegerField()
    fecha_vencimiento = models.DateField()
    monto_cuota = models.DecimalField(max_digits=14, decimal_places=2)
    saldo_cuota = models.DecimalField(max_digits=14, decimal_places=2)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="PENDIENTE")

    class Meta:
        verbose_name = "Cuota de pago"
        verbose_name_plural = "Plan de pagos"
        unique_together = ("cartera", "numero_cuota")


class Pago(models.Model):
    FORMA_CHOICES = [
        ("EFECTIVO", "Efectivo"),
        ("TRANSFERENCIA", "Transferencia"),
        ("CHEQUE", "Cheque"),
        ("TARJETA", "Tarjeta"),
        ("NOTA_CREDITO", "Nota de crédito"),
    ]
    cartera = models.ForeignKey(CarteraCobro, on_delete=models.PROTECT, related_name="pagos")
    plan_pago = models.ForeignKey(PlanPago, null=True, blank=True, on_delete=models.SET_NULL)
    entrega = models.ForeignKey(
        Entrega,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pagos",
        help_text=(
            "Visita de entrega en la que el repartidor recibió este pago "
            "(Cambio #14). Vacío si el pago se registró fuera de una entrega."
        ),
    )
    fecha_pago = models.DateTimeField(auto_now_add=True)
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    forma_pago = models.CharField(max_length=20, choices=FORMA_CHOICES)
    referencia = models.CharField(
        max_length=50,
        blank=True,
        help_text=(
            "Número de transferencia, cheque o voucher; si la forma de pago es "
            "nota de crédito, aquí va el número de la nota de crédito."
        ),
    )
    numero_recibo = models.CharField(
        max_length=30,
        blank=True,
        verbose_name="N.º de recibo",
        help_text=(
            "Número del recibo con el que el repartidor recibe el efectivo "
            "del cliente (Cambio #15). Solo aplica a pagos en efectivo."
        ),
    )
    cobrador = models.ForeignKey(Vendedor, null=True, blank=True, on_delete=models.SET_NULL)
    observaciones = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "Pagos"


class Consignacion(models.Model):
    ESTADO_CHOICES = [
        ("ABIERTA", "Abierta"),
        ("LIQUIDADA", "Liquidada"),
        ("CERRADA", "Cerrada"),
    ]
    factura = models.ForeignKey(Factura, on_delete=models.PROTECT, related_name="consignaciones")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT)
    fecha_entrega = models.DateField()
    fecha_liquidacion = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="ABIERTA")

    class Meta:
        verbose_name_plural = "Consignaciones"


class ConsignacionDetalle(models.Model):
    consignacion = models.ForeignKey(
        Consignacion, on_delete=models.CASCADE, related_name="lineas"
    )
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad_entregada = models.DecimalField(max_digits=12, decimal_places=3)
    cantidad_vendida = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    cantidad_devuelta = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    precio_unitario = models.DecimalField(max_digits=14, decimal_places=4)

    class Meta:
        verbose_name = "Línea de consignación"
        verbose_name_plural = "Líneas de consignación"


class ParadaCliente(models.Model):
    """
    Cambio #22: llegada del repartidor a un cliente dentro de una carga. Se
    marca con el botón "Llegué" (una vez por cliente, aunque tenga varias
    facturas en la carga). La salida no se guarda aparte: es la hora de la
    última entrega registrada a ese cliente en la carga.
    """

    carga = models.ForeignKey(Carga, on_delete=models.CASCADE, related_name="paradas")
    cliente = models.ForeignKey("Cliente", on_delete=models.CASCADE, related_name="paradas")
    llegada_en = models.DateTimeField(verbose_name="Llegada")
    llegada_sin_senal = models.BooleanField(
        default=False,
        verbose_name="Marcada sin señal",
        help_text="El celular guardó la hora y la envió después, junto con la entrega.",
    )

    class Meta:
        verbose_name = "Llegada a cliente"
        verbose_name_plural = "Llegadas a clientes"
        unique_together = ("carga", "cliente")
        ordering = ["llegada_en"]

    def __str__(self):
        return f"{self.cliente} · {self.carga.codigo_reporte}"


class ContactoSucursal(models.Model):
    """
    Cambio #29: lista de contactos en las sucursales (la persona del cliente
    que entrega la devolución). Ventas la elige al solicitar una devolución
    sin factura. Se agregan y eliminan desde el admin; eliminar un contacto
    no borra su nombre de las devoluciones que ya lo usaron.
    """

    nombre = models.CharField(max_length=150, unique=True, verbose_name="Nombre del contacto")
    activo = models.BooleanField(
        default=True, help_text="Desmárcalo para que ya no salga en la lista sin borrarlo."
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Contacto en sucursal"
        verbose_name_plural = "Contactos en sucursales"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class DevolucionSinFactura(models.Model):
    """
    Cambio #27: devoluciones de producto que el cliente regresa sin que
    estén asociadas a una factura puntual (por ejemplo, un cambio de
    mercadería vieja o dañada que no corresponde a una entrega reciente).

    Una persona de ventas registra la solicitud con los datos generales
    (fecha, sucursal, motivo, contacto). Oficina la asigna a una Carga desde
    el mismo planeador que agrupa facturas (Cambio #7/#23). El repartidor,
    al pasar por la sucursal, captura el detalle de productos y cantidades
    recibidas (DevolucionSinFacturaDetalle). Esas cantidades se entregan en
    bodega de producto terminado y se reciben por detalle, igual que las
    devoluciones que sí vienen de una factura (Cambio #16).
    """

    ESTADO_CHOICES = [
        ("SOLICITADA", "Solicitada"),
        ("ASIGNADA", "Asignada a ruta"),
        ("RECOLECTADA", "Recolectada"),
        ("CERRADA", "Cerrada"),
    ]

    fecha_ingreso = models.DateField(verbose_name="Fecha de ingreso")
    # Cambio #29: el cliente queda guardado en la devolución (antes solo se
    # sabía por la sucursal). Se elige primero y la sucursal debe ser una de
    # las suyas.
    cliente = models.ForeignKey(
        "Cliente",
        null=True,
        on_delete=models.PROTECT,
        related_name="devoluciones_sin_factura",
        verbose_name="Cliente",
    )
    sucursal = models.ForeignKey(
        "Sucursal",
        on_delete=models.PROTECT,
        related_name="devoluciones_sin_factura",
        verbose_name="Sucursal a retirar",
    )
    total_unidades_estimado = models.PositiveIntegerField(
        verbose_name="Total de unidades (estimado)",
        help_text="Lo que dice la persona de ventas al registrar la solicitud; el repartidor captura la cantidad real por producto.",
    )
    # Cambio #29: el contacto se elige de la lista ContactoSucursal. El
    # nombre también se guarda como texto (contacto_sucursal), así queda
    # aunque luego se elimine el contacto de la lista.
    contacto = models.ForeignKey(
        "ContactoSucursal",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="devoluciones_sin_factura",
        verbose_name="Contacto en la sucursal",
    )
    contacto_sucursal = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="Nombre de contacto en la sucursal",
        help_text="Nombre de la persona de la sucursal que entrega la devolución.",
    )
    motivo = models.ForeignKey(
        "MotivoDevolucion",
        on_delete=models.PROTECT,
        related_name="devoluciones_sin_factura",
        verbose_name="Motivo general del retiro",
    )
    observaciones = models.TextField(blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="SOLICITADA")
    carga = models.ForeignKey(
        "Carga",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="devoluciones_sin_factura",
        verbose_name="Carga asignada",
    )
    creada_en = models.DateTimeField(auto_now_add=True)
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Solicitada por",
    )

    # --- Captura del repartidor ---------------------------------------------
    recolectada_en = models.DateTimeField(null=True, blank=True, editable=False)

    # --- Recepción en bodega de producto terminado --------------------------
    recibido_en = models.DateTimeField(
        null=True, blank=True, editable=False, verbose_name="Recibida en bodega el"
    )
    recibido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="devoluciones_sin_factura_recibidas",
        verbose_name="Recibida por",
    )
    observaciones_bodega = models.TextField(
        blank=True,
        verbose_name="Observaciones de bodega",
        help_text="Obligatoria cuando lo recibido en bodega no coincide con lo recolectado.",
    )

    # --- Cambio #28: revisión de cartera (después de la recepción en bodega)
    revisada_cartera_en = models.DateTimeField(
        null=True, blank=True, editable=False, verbose_name="Recibido por cartera el"
    )
    revisada_cartera_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Recibido por cartera (usuario)",
    )
    comentario_cartera = models.TextField(blank=True, verbose_name="Comentario de cartera")

    class Meta:
        verbose_name = "Devolución sin factura"
        verbose_name_plural = "Devoluciones sin factura"
        ordering = ["-creada_en"]
        permissions = [
            ("solicitar_devoluciones_sin_factura", "Puede solicitar devoluciones sin factura"),
            ("revisar_devoluciones_cartera", "Puede dar por enterada una devolución (cartera)"),
        ]

    def __str__(self):
        return self.codigo_reporte

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.cliente_id and self.sucursal_id and self.sucursal.cliente_id != self.cliente_id:
            raise ValidationError({"sucursal": "Esa sucursal no es de este cliente."})

    def save(self, *args, **kwargs):
        # Cambio #29: si no se eligió cliente, se toma el de la sucursal.
        if self.sucursal_id and not self.cliente_id:
            self.cliente_id = self.sucursal.cliente_id
        if self.contacto_id and not self.contacto_sucursal:
            self.contacto_sucursal = self.contacto.nombre
        super().save(*args, **kwargs)

    @property
    def recibida_por_cartera(self):
        return self.revisada_cartera_en is not None

    @property
    def estado_mostrar(self):
        """Estado para pantallas y reportes: incluye "Recibido por cartera" (Cambio #28)."""
        if self.recibida_por_cartera:
            return "Recibido por cartera"
        return self.get_estado_display()

    @property
    def estado_css(self):
        return "CARTERA" if self.recibida_por_cartera else self.estado

    @property
    def codigo_reporte(self):
        return f"DEVOL-{self.id:04d}"


class DevolucionSinFacturaDetalle(models.Model):
    """Línea de producto y cantidad que captura el repartidor al recolectar."""

    devolucion = models.ForeignKey(
        DevolucionSinFactura, on_delete=models.CASCADE, related_name="detalles"
    )
    producto = models.ForeignKey("Producto", on_delete=models.PROTECT, related_name="+")
    cantidad_recolectada = models.DecimalField(max_digits=12, decimal_places=3)
    # Cambio #27.1: motivo de devolución de este producto en particular,
    # elegido por el entregador al recibirlo (del mismo catálogo que usa
    # EntregaDetalle.motivo_devolucion, Cambio #9). Se deja null=True a nivel
    # de base de datos solo por si existieran líneas capturadas antes de
    # este ajuste; la pantalla del repartidor lo exige siempre para líneas
    # nuevas.
    motivo = models.ForeignKey(
        "MotivoDevolucion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lineas_devolucion_sf",
        verbose_name="Motivo de devolución",
        help_text="Motivo de este producto en particular, agregado por el entregador al recibirlo.",
    )
    # Cambio #28: lote y vencimiento (opcionales; bodega puede completarlos).
    # Un mismo producto puede venir en varias líneas, una por motivo o lote.
    lote = models.CharField(max_length=40, blank=True, verbose_name="Lote")
    fecha_vencimiento = models.DateField(
        null=True, blank=True, verbose_name="Fecha de vencimiento"
    )
    # Cambio #27: lo que bodega de producto terminado recibió, separado por
    # destino, igual que en EntregaDetalle (Cambio #16).
    recibido_inventario = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name="Recibido a inventario",
    )
    recibido_merma = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name="Recibido como merma",
    )

    class Meta:
        verbose_name = "Línea de devolución sin factura"
        verbose_name_plural = "Líneas de devolución sin factura"

    def __str__(self):
        return f"{self.devolucion.codigo_reporte} / {self.producto.codigo}"

    @property
    def esta_vencido(self):
        """Cambio #28: True si la fecha de vencimiento ya pasó."""
        from django.utils import timezone

        return bool(self.fecha_vencimiento and self.fecha_vencimiento < timezone.localdate())

    @property
    def recepcion_registrada(self):
        return self.recibido_inventario is not None or self.recibido_merma is not None

    @property
    def recibido_total(self):
        if not self.recepcion_registrada:
            return None
        return (self.recibido_inventario or 0) + (self.recibido_merma or 0)

    @property
    def diferencia_recepcion(self):
        total = self.recibido_total
        return None if total is None else total - self.cantidad_recolectada


class CorreccionHora(models.Model):
    """
    Cambio #22: registro de cada hora corregida por oficina (salida a ruta,
    fin de ruta o entrega de una factura). Las horas las pone el sistema; si
    alguna vez hay que cambiar una, queda aquí quién, cuándo y por qué.
    """

    CAMPO_CHOICES = [
        ("SALIDA", "Salida a ruta"),
        ("FIN", "Fin de ruta"),
        ("LLEGADA", "Llegada a cliente"),
        ("ENTREGA", "Entrega de factura"),
    ]
    carga = models.ForeignKey(Carga, on_delete=models.CASCADE, related_name="correcciones_hora")
    entrega = models.ForeignKey(
        "Entrega", null=True, blank=True, on_delete=models.CASCADE, related_name="correcciones_hora"
    )
    parada = models.ForeignKey(
        ParadaCliente, null=True, blank=True, on_delete=models.CASCADE, related_name="correcciones_hora"
    )
    campo = models.CharField(max_length=10, choices=CAMPO_CHOICES)
    valor_anterior = models.DateTimeField(null=True, blank=True)
    valor_nuevo = models.DateTimeField()
    motivo = models.CharField(max_length=200)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    corregido_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Corrección de hora"
        verbose_name_plural = "Correcciones de hora"
        ordering = ["-corregido_en"]

    def __str__(self):
        return f"{self.get_campo_display()} · {self.carga.codigo_reporte}"
