"""
Django settings for the App de Distribución de Productos.
"""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: en producción, definir DJANGO_SECRET_KEY como variable de entorno.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "django-insecure-p94bv$-1v@3edr$_s01$90k8l*^@#$9ha!56h6m1%%$neux^4v"
)

# DEBUG se apaga automáticamente en producción (cuando hay DATABASE_URL, típico de Railway).
DEBUG = os.environ.get("DJANGO_DEBUG", "1" if "DATABASE_URL" not in os.environ else "0") == "1"

ALLOWED_HOSTS = ["*"]  # Se restringe más adelante cuando tengamos el dominio final fijo.

# CSRF: se agrega automáticamente el dominio público que Railway asigna a esta
# aplicación (viene en la variable de entorno RAILWAY_PUBLIC_DOMAIN), más
# cualquier dominio extra que se defina manualmente en DJANGO_CSRF_TRUSTED_ORIGINS.
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]
_railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
if _railway_domain:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_railway_domain}")
# Cubre además cualquier subdominio *.up.railway.app, por si se genera otro dominio.
CSRF_TRUSTED_ORIGINS.append("https://*.up.railway.app")


INSTALLED_APPS = [
    # Cambio #30: "core" va primero para que su admin/base_site.html (con la
    # marca Sol Maya) tenga prioridad sobre la plantilla del admin de Django.
    "core",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Base de datos: usa DATABASE_URL si existe (Railway la provee automáticamente
# al agregar un servicio de PostgreSQL); si no, usa SQLite local para desarrollo.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "es"
TIME_ZONE = "America/Managua"
USE_I18N = True
USE_TZ = True


STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Límite de subida de archivos (el Excel de facturas puede pesar varios MB)
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024


# Cambio #39: correo para los avisos de vencimiento de documentos de flota.
# Mientras EMAIL_HOST no esté configurado en Railway, no se envían correos
# (los avisos siguen apareciendo en el tablero y en la pantalla de logística).
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "0") == "1"
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "avisos@solmaya.com.ni")
AVISOS_FLOTA_CORREOS = [
    c.strip()
    for c in os.environ.get(
        "AVISOS_FLOTA_CORREOS", "logistica@solmaya.com.ni,atalavera@solmaya.com.ni"
    ).split(",")
    if c.strip()
]
