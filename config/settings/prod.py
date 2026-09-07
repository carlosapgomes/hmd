"""Production settings do HMD.

Falha fechado: sem ``DJANGO_SECRET_KEY`` a inicialização aborta com
``ImproperlyConfigured`` — nunca há fallback para valor de build (R6). Cache
do anti-lockout (slice 004/R3b): enquanto ``CACHES`` for o default
``LocMemCache`` (por-processo) a inicialização também aborta — produção exige
cache compartilhado entre workers (Redis/Memcached) configurado no deploy.
"""

import os

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403,F405

DEBUG = False

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY é obrigatório em produção.")

# Cache do anti-lockout local (change ad-kerberos, slice 004, R3b/D7):
# ``LocMemCache`` é por-processo — com múltiplos workers o limiar efetivo do
# rate-limit de login seria multiplicado. Produção falha fechado enquanto
# ``CACHES`` permanecer LocMem: o deploy precisa configurar um cache
# compartilhado (Redis/Memcached) para o limiar valer entre workers. Dev/teste
# continuam em LocMem (limiar por processo é suficiente lá).
if CACHES["default"]["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache":  # noqa: F405
    raise ImproperlyConfigured(
        "CACHES não pode ser LocMemCache em produção: o rate-limit de login "
        "(LOGIN_ATTEMPTS_LIMIT) exige cache compartilhado entre workers "
        "(Redis/Memcached) — configure CACHES no deploy."
    )

ALLOWED_HOSTS = [
    host.strip() for host in os.environ.get("ALLOWED_HOSTS", "").split(",") if host.strip()
]

DATABASES = {"default": dj_database_url.config(conn_max_age=600, conn_health_checks=True)}

# Processamento do intake (change intake-nir-upload, slice 003, R4): em
# produção o default é enfileirar no cluster pdf — nunca processar inline sem
# intenção explícita (falha fechado). Só roda inline se a variável estiver
# explicitamente ligada.
INTAKE_RUN_TASKS_INLINE = os.environ.get("INTAKE_RUN_TASKS_INLINE", "false").lower() in (
    "true",
    "1",
    "yes",
)

# Processamento da anonimização (change presidio-anonymization, slice 004,
# R2): em produção o default é enfileirar no cluster anonymization — nunca
# rodar inline sem intenção explícita (o processo web nunca carrega o modelo
# spaCy; só o worker anonymization). Só roda inline se a variável estiver
# explicitamente ligada.
ANONYMIZATION_RUN_TASKS_INLINE = os.environ.get(
    "ANONYMIZATION_RUN_TASKS_INLINE", "false"
).lower() in (
    "true",
    "1",
    "yes",
)

# Pipeline LLM (change llm-pipeline-per-type, slice 006, R4): em produção o
# default é enfileirar no cluster llm — nunca rodar inline sem intenção
# explícita (o processo web nunca chama a OpenRouter; só o worker-llm). Só
# roda inline se a variável estiver explicitamente ligada.
LLM_RUN_TASKS_INLINE = os.environ.get("LLM_RUN_TASKS_INLINE", "false").lower() in (
    "true",
    "1",
    "yes",
)

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
