"""Settings de verificação do HMD (ui-parity-foundation, slice 002/D7).

Uso exclusivo do harness ``verify-hmd`` (``.pi/skills/verify-hmd``): instância
isolada em loopback, banco próprio ``hmd_verify_*`` e backend sintético
exclusivo (``apps.accounts.backends_verify``). NUNCA dev/prod.

Fail-closed, sem exceções:
- só aceita host de loopback (``localhost``/``127.0.0.1``/``::1``) — qualquer
  outro host levanta ``ImproperlyConfigured`` no import;
- só aceita nome ``hmd_verify_<sufixo>`` (minúsculas/dígitos/underscore) e
  recusa qualquer nome com ``prod`` — banco de dev/prod nunca é alvo;
- ``DATABASE_URL``/``DB_*`` de dev são ignorados por construção: a resolução
  lê apenas ``VERIFY_DB_*`` (nunca ``database_config`` com prefixo vazio).
"""

import os
import re
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

_VERIFY_DB_NAME_RE = re.compile(r"hmd_verify_[a-z0-9_]+")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

VERIFY_DB_HOST = os.environ.get("VERIFY_DB_HOST", "127.0.0.1")
VERIFY_DB_PORT = os.environ.get("VERIFY_DB_PORT", os.environ.get("TEST_DB_PORT", "55435"))
VERIFY_DB_NAME = os.environ.get("VERIFY_DB_NAME", "hmd_verify_local")
VERIFY_DB_USER = os.environ.get("VERIFY_DB_USER", "hmd")
VERIFY_DB_PASSWORD = os.environ.get("VERIFY_DB_PASSWORD", "hmd_dev")

if VERIFY_DB_HOST not in _LOOPBACK_HOSTS:
    raise ImproperlyConfigured(
        f"verify settings recusam host não-loopback (VERIFY_DB_HOST={VERIFY_DB_HOST!r})."
    )
if not _VERIFY_DB_NAME_RE.fullmatch(VERIFY_DB_NAME) or "prod" in VERIFY_DB_NAME:
    raise ImproperlyConfigured(
        "verify settings recusam nome de banco fora do padrão "
        f"hmd_verify_* (VERIFY_DB_NAME={VERIFY_DB_NAME!r})."
    )

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": VERIFY_DB_NAME,
        "USER": VERIFY_DB_USER,
        "PASSWORD": VERIFY_DB_PASSWORD,
        "HOST": VERIFY_DB_HOST,
        "PORT": VERIFY_DB_PORT,
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
        "OPTIONS": {"application_name": "hmd-verify"},
    }
}

DEBUG = False
# STATIC_ROOT por-run, nunca o staticfiles/ compartilhado do checkout
# (base.py): o harness exporta HMD_VERIFY_STATIC_ROOT=<run>/staticfiles
# antes de collectstatic e runserver, então cada instância coleta e serve
# do seu próprio root sob tmp/verification/<run>/ e runs concorrentes não
# compartilham arquivos coletados. Sem launch (manage.py check, imports de
# teste), o fallback é um diretório por-processo em temp — nunca o checkout.
_VERIFY_STATIC_ROOT = os.environ.get("HMD_VERIFY_STATIC_ROOT", "")
if _VERIFY_STATIC_ROOT:
    STATIC_ROOT = Path(_VERIFY_STATIC_ROOT)
else:
    STATIC_ROOT = Path(tempfile.gettempdir()) / f"hmd-verify-static-{os.getpid()}"
# MEDIA_ROOT por-run (slice PVP-002, R5): o harness exporta
# HMD_VERIFY_MEDIA_ROOT=<run>/media no launch/servidor, então os PDFs
# criados na jornada vivem na run e o cleanup remove só a mídia da run.
# Sem launch, o fallback é um diretório por-processo em temp — NUNCA o
# media/ do checkout (BASE_DIR/media é o default de dev de base.py).
_VERIFY_MEDIA_ROOT = os.environ.get("HMD_VERIFY_MEDIA_ROOT", "")
if _VERIFY_MEDIA_ROOT:
    MEDIA_ROOT = Path(_VERIFY_MEDIA_ROOT)
else:
    MEDIA_ROOT = Path(tempfile.gettempdir()) / f"hmd-verify-media-{os.getpid()}"
# Estáticos reais do app: o harness roda collectstatic para STATIC_ROOT e o
# WhiteNoise (middleware do base) serve esses arquivos com DEBUG=False — os
# mesmos assets de static/ que a aplicação serve em produção, sem depender
# do atalho de debug do runserver. Sem isso, as telas dirigidas ficavam sem
# CSS/JS (404) e as capturas não serviam como evidência visual.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
SECRET_KEY = "verify-only-secret-key-not-for-production"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]

# Backend sintético exclusivo da verificação (D7b): o único caminho de
# autenticação nesta instância. Recusa carregar sob qualquer outro settings
# (guard no próprio módulo).
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends_verify.VerifySyntheticBackend",
]

# Determinístico e sem workers externos: processamento inline, intake ligado.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
INTAKE_RUN_TASKS_INLINE = True
ANONYMIZATION_RUN_TASKS_INLINE = True
LLM_RUN_TASKS_INLINE = True
ATTACHMENTS_RUN_TASKS_INLINE = True
INTAKE_ENABLED = True
OPENROUTER_API_KEY_FILE = ""
OPENROUTER_API_KEY = ""
APP_DISPLAY_NAME = "SAR – HMD"
UNIT_LABELS = {1: "Unidade 1", 2: "Unidade 2"}

# Sem faixa de intranet: o guard nunca bloqueia a instância de loopback.
INTRANET_IP_RANGE = ""
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
