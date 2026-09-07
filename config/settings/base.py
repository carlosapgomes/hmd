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

# Autenticação (change ad-kerberos-authentication, slice 003, design D4): dois
# backends em ordem fixa — Kerberos primeiro (usuários com `ad_upn` autenticam
# via AD); local só para break-glass (superusuário sem `ad_upn`) e apenas com
# AD_ALLOW_LOCAL_AUTH=True (default False; dev/test setam True).
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.KerberosBackend",
    "apps.accounts.backends.LocalAccountBackend",
]
# Break-glass local (ADR-0003): autenticação local permitida apenas para
# superusuários sem `ad_upn`, e somente quando esta flag está ligada.
AD_ALLOW_LOCAL_AUTH = os.environ.get("AD_ALLOW_LOCAL_AUTH", "false").lower() in (
    "true",
    "1",
    "yes",
)

# Validação Kerberos no Active Directory (change ad-kerberos, D1/D5/D10).
# ``AD_DCS`` são os KDCs conhecidos do domínio raiz; o realm NÃO é
# configurável: é derivado do sufixo do ``ad_upn`` no momento da validação
# (D3 — floresta multi-domínio). ``AD_KDC_TIMEOUT`` é o timeout por tentativa
# (default 3s). Failover entre DCs só em erro de transporte (D5).
AD_DCS = [
    dc.strip()
    for dc in os.environ.get("AD_DCS", "<DC1-IP>,<DC2-IP>").split(",")
    if dc.strip()
]
AD_KDC_TIMEOUT = int(os.environ.get("AD_KDC_TIMEOUT", "3"))
# Factory injetável do wrapper real (D6): dotted-path resolvido em tempo de
# chamada por ``apps.accounts.kerberos``. Testes sobrescrevem com fakes via
# ``override_settings`` — nenhuma chamada de rede na suíte.
KERBEROS_CLIENT_FACTORY = "apps.accounts.kerberos.validate_password"

# Anti-lockout local via cache (change ad-kerberos, slice 004, R3/D7): limiar
# de tentativas malsucedidas por CPF/IP+CPF, janela de contagem e duração do
# bloqueio temporário. Defaults abaixo da política típica de lockout do AD.
# O cache default é LocMem (por processo) — suficiente para dev/teste; em
# produção o cache compartilhado é obrigatório (falha fechado em prod, R3b).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    },
}
LOGIN_ATTEMPTS_LIMIT = int(os.environ.get("LOGIN_ATTEMPTS_LIMIT", "5"))
LOGIN_ATTEMPTS_WINDOW_SECONDS = int(os.environ.get("LOGIN_ATTEMPTS_WINDOW_SECONDS", "900"))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", "900"))

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
