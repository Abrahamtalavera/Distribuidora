from django.db import models


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


class Entrega(models.Model):
    TIPO_CHOICES = [("PARCIAL", "Parcial"), ("TOTAL", "Total")]
    ESTADO_CHOICES = [("CONFIRMADA", "Confirmada"), ("ANULADA", "Anulada")]

    factura = models.ForeignKey(Factura, on_delete=models.PROTECT, related_name="entregas")
    fecha_entrega = models.DateTimeField(auto_now_add=True)
    tipo_entrega = models.CharField(max_length=10, choices=TIPO_CHOICES)
    repartidor = models.ForeignKey(Vendedor, null=True, blank=True, on_delete=models.SET_NULL)
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
