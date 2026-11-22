from django.core.management.base import BaseCommand

from core.avisos_flota import enviar_avisos


class Command(BaseCommand):
    help = "Cambio #39: envía el correo diario de documentos de flota por vencer."

    def add_arguments(self, parser):
        parser.add_argument("--forzar", action="store_true", help="Volver a revisar aunque ya se hizo hoy.")

    def handle(self, *args, **opciones):
        n, mensaje = enviar_avisos(forzar=opciones["forzar"])
        self.stdout.write(f"{n} documento(s). {mensaje}")
