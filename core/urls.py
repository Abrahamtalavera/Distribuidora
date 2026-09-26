# Cambio #12: URLs del módulo de entregas (acceso del repartidor por código
# de carga + PIN, lista de facturas de la carga, captura de la entrega).
# Viven fuera de /admin/ porque el repartidor no inicia sesión con una
# cuenta de usuario de Django.

from django.urls import path

from core import views_entregas

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
    path(
        "entregas/carga/<int:carga_id>/salir/",
        views_entregas.entregas_salir_view,
        name="entregas_salir",
    ),
]
