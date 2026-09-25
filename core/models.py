from django.db import models
from django.utils import timezone


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

    class Meta:
        verbose_name_plural = "Vendedores"

    def __str__(self):
        return self.nombre


class Vehiculo(models.Model):
    placa = models.CharField(max_length=20, unique=True)
    descripcion = models.CharField(max_length=100, blank=True)
    activo = models.BooleanField(default=True)

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
        ("CERRADA", "Cerrada"),
    ]

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
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Carga"
        verbose_name_plural = "Cargas"
        ordering = ["-fecha_planeada", "-id"]

    def __str__(self):
        return f"Carga #{self.id} - {self.fecha_planeada}"


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
    limite_credito = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    dias_credito = models.IntegerField(default=0)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Clientes"

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


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

    class Meta:
        verbose_name_plural = "Facturas"
        ordering = ["-fecha_emision", "-numero_factura"]

    def __str__(self):
        return self.numero_factura


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

    class Meta:
        verbose_name = "Línea de factura"
        verbose_name_plural = "Líneas de factura"

    def __str__(self):
        return f"{self.factura.numero_factura} / {self.producto.codigo}"

    @property
    def pendiente_entrega(self):
        return self.cantidad_facturada - self.cantidad_entregada


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

    factura = models.ForeignKey(Factura, on_delete=models.PROTECT, related_name="entregas")
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
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="CONFIRMADA")

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

    class Meta:
        verbose_name = "Línea de entrega"
        verbose_name_plural = "Líneas de entrega"


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
    ]
    cartera = models.ForeignKey(CarteraCobro, on_delete=models.PROTECT, related_name="pagos")
    plan_pago = models.ForeignKey(PlanPago, null=True, blank=True, on_delete=models.SET_NULL)
    fecha_pago = models.DateTimeField(auto_now_add=True)
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    forma_pago = models.CharField(max_length=20, choices=FORMA_CHOICES)
    referencia = models.CharField(max_length=50, blank=True)
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
