"""Production settings do HMD.

Falha fechado: sem ``DJANGO_SECRET_KEY`` a inicialização aborta com
``ImproperlyConfigured`` — nunca há fallback para valor de build (R6). Cache
compartilhado entre workers (change pilot-deployment-v0-1-1, slice 001/R4):
produção usa ``DatabaseCache`` (tabela ``hmd_cache``) e o guard anti-LocMem do
slice 004/R3b segue ativo — a inicialização aborta se o backend voltar a ser
por-processo.
"""

import os

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403,F405

DEBUG = False

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY é obrigatório em produção.")

# Cache compartilhado entre workers (change pilot-deployment-v0-1-1, slice
# 001/R4, design D3): ``DatabaseCache`` na tabela ``hmd_cache`` — criada pelo
# passo de migração ``manage.py createcachetable hmd_cache`` (idempotente; a
# tabela é de infraestrutura, não de domínio). Sobrescreve o LocMem do base.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.database.DatabaseCache",
        "LOCATION": "hmd_cache",
    },
}

# Cache do anti-lockout local (change ad-kerberos, slice 004, R3b/D7):
# ``LocMemCache`` é por-processo — com múltiplos workers o limiar efetivo do
# rate-limit de login seria multiplicado. O guard segue proibindo LocMem em
# produção: qualquer troca de backend que reintroduza cache local aborta a
# inicialização. Dev/teste continuam em LocMem (limiar por processo é
# suficiente lá).
if CACHES["default"]["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache":  # noqa: F405
    raise ImproperlyConfigured(
        "CACHES não pode ser LocMemCache em produção: o rate-limit de login "
        "(LOGIN_ATTEMPTS_LIMIT) exige cache compartilhado entre workers. "
        "O default daqui é DatabaseCache na tabela hmd_cache (criada pelo "
        "passo de migração via createcachetable) — este guard é self-check "
        "contra regressões de configuração."
    )

ALLOWED_HOSTS = [
    host.strip() for host in os.environ.get("ALLOWED_HOSTS", "").split(",") if host.strip()
]

# Origem pública do piloto como default (D3): DNS público, não secret. O env
# aceita várias origens separadas por vírgula.
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "https://hmd.projetoshgrs.com").split(",")
    if origin.strip()
]

# O TLS termina no proxy reverso, que injeta ``X-Forwarded-Proto`` (D3): ligado
# por default no piloto; ``PROXY_SSL_HEADER=false`` desliga quando não houver
# proxy HTTPS à frente.
SECURE_PROXY_SSL_HEADER: tuple[str, str] | None
if os.environ.get("PROXY_SSL_HEADER", "true").lower() in ("true", "1", "yes"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
else:
    SECURE_PROXY_SSL_HEADER = None

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

# Anexos (change attachment-processing-ocr, slice 002, P1 review): em
# produção o default é enfileirar no cluster attachments — nunca rodar
# inline sem intenção explícita (o envio de imagem a OCR externo acontece
# apenas no worker-attachments; o processo que conclui a anonimização não
# transcreve nada inline). Só roda inline se a variável estiver explicitamente
# ligada — mesma convenção fail-closed dos três flags acima.
ATTACHMENTS_RUN_TASKS_INLINE = os.environ.get("ATTACHMENTS_RUN_TASKS_INLINE", "false").lower() in (
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
