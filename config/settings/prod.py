"""Production settings do HMD.

Falha fechado: sem ``DJANGO_SECRET_KEY`` a inicialização aborta com
``ImproperlyConfigured`` — nunca há fallback para valor de build (R6).
"""

import os

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY é obrigatório em produção.")

ALLOWED_HOSTS = [
    host.strip() for host in os.environ.get("ALLOWED_HOSTS", "").split(",") if host.strip()
]

DATABASES = {"default": dj_database_url.config(conn_max_age=600, conn_health_checks=True)}

# Segurança
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_SSL_REDIRECT = False  # SSL termina no proxy/túnel
X_FRAME_OPTIONS = "DENY"

# Storage
# - default: FileField/ImageField em MEDIA_ROOT (filesystem local).
# - staticfiles: WhiteNoise com manifest e compressão (coletados em deploy).
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
