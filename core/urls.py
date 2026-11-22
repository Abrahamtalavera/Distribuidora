# Cambio #12: URLs del módulo de entregas (acceso del repartidor por código
# de carga + PIN, lista de facturas de la carga, captura de la entrega).
# Viven fuera de /admin/ porque el repartidor no inicia sesión con una
# cuenta de usuario de Django.

from django.urls import path

from core import (
    views_bodega,
    views_cartera,
    views_cierre,
    views_entregas,
    views_entregas_devolucion_sf,
    views_flota,
    views_tablero,
    views_ventas,
    views_ventas_devoluciones,
)

urlpatterns = [
    path("entregas/acceso/", views_entregas.entregas_acceso_view, name="entregas_acceso"),
    path(
        "entregas/carga/<int:carga_id>/",
        views_entregas.entregas_lista_view,
        name="entregas_lista",
    ),
    path(
        "entregas/carga/<int:carga_id>/factura/<int:factura_id>/",
        views_entregas.entregas_captura_view,
        name="entregas_captura",
    ),
    # Cambio #22: llegada al cliente.
    path(
        "entregas/carga/<int:carga_id>/factura/<int:factura_id>/llegue/",
        views_entregas.entregas_llegue_view,
        name="entregas_llegue",
    ),
    # Cambio #17: marcar facturas como cargadas y salir a ruta.
    path(
        "entregas/carga/<int:carga_id>/factura/<int:factura_id>/cargar/",
        views_entregas.entregas_marcar_cargada_view,
        name="entregas_marcar_cargada",
    ),
    path(
        "entregas/carga/<int:carga_id>/salir-a-ruta/",
        views_entregas.entregas_salir_a_ruta_view,
        name="entregas_salir_a_ruta",
    ),
    path(
        "entregas/carga/<int:carga_id>/terminar/",
        views_cierre.entregas_terminar_view,
        name="entregas_terminar",
    ),
    path(
        "entregas/carga/<int:carga_id>/salir/",
        views_entregas.entregas_salir_view,
        name="entregas_salir",
    ),
    # Cambio #16: recepción de devoluciones en bodega de producto terminado.
    path("bodega/", views_bodega.bodega_lista_view, name="bodega_lista"),
    path("bodega/acceso/", views_bodega.BodegaLoginView.as_view(), name="bodega_acceso"),
    path("bodega/salir/", views_bodega.bodega_salir_view, name="bodega_salir"),
    path(
        "bodega/carga/<int:carga_id>/",
        views_bodega.bodega_recepcion_view,
        name="bodega_recepcion",
    ),
    # Cambio #19: tablero de operación (Administración y Supervisores).
    path("tablero/", views_tablero.tablero_view, name="tablero"),
    # Cambio #20: análisis de un período.
    path("tablero/analisis/", views_tablero.tablero_analisis_view, name="tablero_analisis"),
    # Cambio #24: consulta de Cargas (filtros, resumen y detalle de facturas).
    path("tablero/cargas/", views_tablero.tablero_cargas_view, name="tablero_cargas"),
    path("tablero/acceso/", views_tablero.TableroLoginView.as_view(), name="tablero_acceso"),
    path("tablero/salir/", views_tablero.tablero_salir_view, name="tablero_salir"),
    # Cambio #28: revisión de devoluciones por cartera.
    path("cartera/", views_cartera.cartera_lista_view, name="cartera_lista"),
    path("cartera/acceso/", views_cartera.CarteraLoginView.as_view(), name="cartera_acceso"),
    # Cambio #36: consulta de cargas (reemplaza la recaudación por ruta del #33;
    # la dirección anterior lleva a la nueva).
    path("cartera/cargas/", views_cartera.cartera_cargas_view, name="cartera_cargas"),
    path("cartera/cargas/excel/", views_cartera.cartera_cargas_excel_view, name="cartera_cargas_excel"),
    path("cartera/recaudacion/", views_cartera.cartera_recaudacion_view, name="cartera_recaudacion"),
    # Cambio #37: hoja imprimible de una devolución (cartera y bodega).
    path(
        "devoluciones/imprimir/<str:tipo>/<int:objeto_id>/",
        views_cartera.devolucion_imprimir_view,
        name="devolucion_imprimir",
    ),
    path("cartera/salir/", views_cartera.cartera_salir_view, name="cartera_salir"),
    path(
        "cartera/enterado/<str:tipo>/<int:objeto_id>/",
        views_cartera.cartera_enterado_view,
        name="cartera_enterado",
    ),
    # Cambio #21: consulta del equipo de ventas.
    path("ventas/", views_ventas.ventas_buscar_view, name="ventas_buscar"),
    path("ventas/factura/<int:factura_id>/", views_ventas.ventas_factura_view, name="ventas_factura"),
    path("ventas/ruta/", views_ventas.ventas_ruta_view, name="ventas_ruta"),
    path("ventas/ruta/excel/", views_ventas.ventas_ruta_excel_view, name="ventas_ruta_excel"),
    path("ventas/acceso/", views_ventas.VentasLoginView.as_view(), name="ventas_acceso"),
    path("ventas/salir/", views_ventas.ventas_salir_view, name="ventas_salir"),
    # Cambio #27: solicitud de devoluciones sin factura (ventas).
    path(
        "devoluciones/acceso/",
        views_ventas_devoluciones.DevolucionesSinFacturaLoginView.as_view(),
        name="devoluciones_sf_acceso",
    ),
    path(
        "devoluciones/salir/",
        views_ventas_devoluciones.devoluciones_sf_salir_view,
        name="devoluciones_sf_salir",
    ),
    path(
        "devoluciones/solicitar/",
        views_ventas_devoluciones.devoluciones_sf_solicitar_view,
        name="devoluciones_sf_solicitar",
    ),
    path(
        "devoluciones/lista/",
        views_ventas_devoluciones.devoluciones_sf_lista_view,
        name="devoluciones_sf_lista",
    ),
    # Cambio #27: captura del repartidor de una devolución sin factura.
    path(
        "entregas/carga/<int:carga_id>/devolucion/<int:devolucion_id>/",
        views_entregas_devolucion_sf.entregas_devolucion_sf_view,
        name="entregas_devolucion_sf",
    ),
    path(
        "entregas/carga/<int:carga_id>/devolucion/buscar-producto/",
        views_entregas_devolucion_sf.entregas_devolucion_sf_buscar_producto_view,
        name="entregas_devolucion_sf_buscar_producto",
    ),
    # Cambio #39: logística (flota) y combustible del conductor.
    path("logistica/", views_flota.logistica_flota_view, name="logistica_flota"),
    path("logistica/acceso/", views_flota.LogisticaLoginView.as_view(), name="logistica_acceso"),
    path("logistica/salir/", views_flota.logistica_salir_view, name="logistica_salir"),
    path("logistica/vehiculo/<int:vehiculo_id>/", views_flota.logistica_vehiculo_guardar_view, name="logistica_vehiculo_guardar"),
    path("logistica/documento/", views_flota.logistica_documento_view, name="logistica_documento"),
    path("logistica/vencimientos/", views_flota.logistica_vencimientos_view, name="logistica_vencimientos"),
    path("logistica/conductores/", views_flota.logistica_conductores_view, name="logistica_conductores"),
    path("logistica/combustible/", views_flota.logistica_combustible_view, name="logistica_combustible"),
    path("logistica/combustible/excel/", views_flota.logistica_combustible_excel_view, name="logistica_combustible_excel"),
    path("logistica/combustible/<int:abastecimiento_id>/revisar/", views_flota.logistica_combustible_revisar_view, name="logistica_combustible_revisar"),
    path("logistica/archivo/<int:archivo_id>/", views_flota.logistica_archivo_view, name="logistica_archivo"),
    path("entregas/carga/<int:carga_id>/combustible/", views_flota.entregas_combustible_view, name="entregas_combustible"),
    path("logistica/combustible/nuevo/", views_flota.logistica_combustible_nuevo_view, name="logistica_combustible_nuevo"),
    # Cambio #39.1: acceso del conductor solo para combustible.
    path("combustible/", views_flota.combustible_acceso_view, name="combustible_acceso"),
    path("combustible/registrar/", views_flota.combustible_registrar_view, name="combustible_registrar"),
    path("combustible/salir/", views_flota.combustible_salir_view, name="combustible_salir"),
]
