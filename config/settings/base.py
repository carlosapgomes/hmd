"""Base Django settings do HMD — Hemodinâmica.

Settings independentes de ambiente, compartilhadas por dev/prod/test.
Valores sensíveis ou variáveis por ambiente são lidos do ambiente nos módulos
por ambiente (``config.settings.{dev,prod,test}``); veja ``.env.example``.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Carrega variáveis locais de .env sem sobrescrever o ambiente do processo.
load_dotenv(BASE_DIR / ".env")

# Nome do app exibido no cabeçalho, título da página e meta tags.
APP_DISPLAY_NAME = os.environ.get("APP_DISPLAY_NAME", "HMD — Hemodinâmica")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Apps de domínio (apps/).
    "apps.accounts",
]

# Modelo de usuário customizado (D8) — estendido uma única vez.
AUTH_USER_MODEL = "accounts.User"

# Autenticação local transitória (ADR-0003, slice 004 R1): backend custom que
# delega a checagem de senha ao ModelBackend e recusa account_status != active.
# O change ad-kerberos-authentication substitui o login local comum.
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.LocalAccountBackend",
]

# Intranet guard (ADR-0002, slice 006 R1/R6): papéis restritos à rede interna
# e faixas de intranet aceitas. ``INTRANET_RESTRICTED_ROLES`` aceita vários
# papéis separados por vírgula (default: só ``nir``). ``INTRANET_IP_RANGE``
# aceita CIDRs separadas por vírgula; vazio = restrição desligada (default
# seguro de desenvolvimento — o papel restrito nunca é bloqueado).
INTRANET_RESTRICTED_ROLES = [
    role.strip()
    for role in os.environ.get("INTRANET_RESTRICTED_ROLES", "nir").split(",")
    if role.strip()
]
INTRANET_IP_RANGE = os.environ.get("INTRANET_IP_RANGE", "")
# Header de proxy reverso com o IP real do cliente (túnel Cloudflare).
TRUSTED_PROXY_HEADER = os.environ.get("TRUSTED_PROXY_HEADER", "HTTP_CF_CONNECTING_IP")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # Papel ativo em sessão (slice 005, R1/R6): registrado após o
    # AuthenticationMiddleware (precisa de request.user) e após o
    # MessageMiddleware (precisa do storage de mensagens para deslogar com
    # mensagem o usuário sem papéis).
    "apps.accounts.middleware.ActiveRoleMiddleware",
    # Guard de intranet (slice 006, R1/R5): roda APÓS o ActiveRoleMiddleware —
    # precisa de ``session["active_role"]`` já resolvido para decidir pelo
    # papel ativo. Paths isentos (login/logout/switch-role/static/media) e
    # faixa vazia (dev) nunca bloqueiam.
    "apps.accounts.middleware.IntranetGuardMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context_processors.app_display_name",
                "apps.accounts.context_processors.role_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Bahia"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Validação de senha (padrão Django, explícito para paridade de configuração).
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
