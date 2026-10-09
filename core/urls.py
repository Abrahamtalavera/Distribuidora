# Cambio #12: URLs del módulo de entregas (acceso del repartidor por código
# de carga + PIN, lista de facturas de la carga, captura de la entrega).
# Viven fuera de /admin/ porque el repartidor no inicia sesión con una
# cuenta de usuario de Django.

from django.urls import path

from core import views_bodega, views_cierre, views_entregas

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
]
