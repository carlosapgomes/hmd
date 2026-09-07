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
    # Fila de tasks assíncronas (django-q2, design D2) — broker ORM, sem Redis.
    "django_q",
    # Apps de domínio (apps/).
    "apps.accounts",
    # Núcleo de casos (change 03): slice 001 entrega o catálogo code-first
    # (procedure_catalog + seed/verificação); models chegam no slice 002.
    "apps.cases",
    # Intake do NIR (change intake-nir-upload, slice 001).
    "apps.intake",
    # Anonimização (change presidio-anonymization, slice 001): módulo puro de
    # pré-extração determinística; sem models/recognizers neste slice.
    "apps.anonymization",
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

# Locks/lease de casos (change 03, slice 004, design D6): duração default da
# lease de exclusividade de mutação por caso, em segundos (default 300 = 5min,
# alinhado ao ats-web). Sem variantes por papel/contexto no HMD.
CASE_LOCK_LEASE_SECONDS = int(os.environ.get("CASE_LOCK_LEASE_SECONDS", "300"))

# Intake do relatório NIR (change intake-nir-upload, slice 001, design D1/R3):
# limites de upload por caso — quantidade máxima de PDFs do relatório e tamanho
# máximo por arquivo em MB (defaults 10/20; mesmo padrão de defaults por env).
INTAKE_MAX_DOCUMENTS = int(os.environ.get("INTAKE_MAX_DOCUMENTS", "10"))
INTAKE_MAX_FILE_MB = int(os.environ.get("INTAKE_MAX_FILE_MB", "20"))

# Gate de regulação (change intake-nir-upload, slice 002, design D4/R5):
# thresholds do padrão SESAB do relatório — tamanho mínimo do texto extraído
# (chars) e nº mínimo de seções operacionais reconhecidas (defaults do
# ats-web: 500/3).
INTAKE_REGULATION_MIN_TEXT_CHARS = int(os.environ.get("INTAKE_REGULATION_MIN_TEXT_CHARS", "500"))
INTAKE_REGULATION_MIN_OPERATIONAL_SECTIONS = int(
    os.environ.get("INTAKE_REGULATION_MIN_OPERATIONAL_SECTIONS", "3")
)

# Modo de execução do processamento pós-criação do intake (change
# intake-nir-upload, slice 003, design D2/R4): ``True`` (default em dev/teste)
# executa a task sincronamente na criação — UX imediata e testes
# determinísticos, sem worker; ``False`` enfileira no cluster ``pdf`` do
# django-q2 (produção/compose com o serviço worker-pdf).
INTAKE_RUN_TASKS_INLINE = os.environ.get("INTAKE_RUN_TASKS_INLINE", "true").lower() in (
    "true",
    "1",
    "yes",
)

# Anonimização Presidio (change presidio-anonymization, slice 002, design
# D3/D4): modelo spaCy pt-BR do engine singleton e limiar de score do
# analyzer. O modelo default (pt_core_news_lg, ~541 MB) é trocável por um
# menor (ex.: pt_core_news_md) em ambientes enxutos; o engine só carrega o
# modelo no processo que o importa (worker/testes — nunca no processo web).
ANONYMIZATION_SPACY_MODEL = os.environ.get("ANONYMIZATION_SPACY_MODEL", "pt_core_news_lg")
ANONYMIZATION_SCORE_THRESHOLD = float(os.environ.get("ANONYMIZATION_SCORE_THRESHOLD", "0.45"))

# Modo de execução da task de anonimização (change presidio-anonymization,
# slice 004, design D7/R2): ``True`` (default em dev/teste) executa a task
# sincronamente quando o signal de entrada em ANONYMIZING dispara — testes
# determinísticos, sem worker; ``False`` enfileira no cluster ``anonymization``
# do django-q2 (produção/compose com o serviço worker-anonymization).
ANONYMIZATION_RUN_TASKS_INLINE = os.environ.get(
    "ANONYMIZATION_RUN_TASKS_INLINE", "true"
).lower() in (
    "true",
    "1",
    "yes",
)

# django-q2 (change intake-nir-upload, slice 003, design D2): fila de tasks
# assíncronas com broker ORM (``orm: "default"`` — sem Redis), no formato do
# ats-web, com ``ALT_CLUSTERS`` DENTRO de ``Q_CLUSTER``. O cluster ``pdf``
# (extração de PDF, 2 workers/timeout 180s) é consumido por um processo
# ``manage.py qcluster`` com ``Q_CLUSTER_NAME=pdf``; o cluster ``anonymization``
# (slice 004 — engine Presidio/spaCy no worker, 2 workers/timeout 300s/
# retry 360s, separado do ``pdf`` para o modelo não competir com a extração)
# por ``Q_CLUSTER_NAME=anonymization``; o cluster ``llm`` chega no change 06.
Q_CLUSTER = {
    "name": "hmd",
    "orm": "default",
    # Base (cluster default, não executado no compose dev) com timeout/retry
    # coerentes para o Conf do django-q2 não reclamar de configuração.
    "timeout": 900,
    "retry": 1200,
    "ALT_CLUSTERS": {
        "pdf": {
            "workers": 2,
            "timeout": 180,
            "retry": 300,
        },
        "anonymization": {
            "workers": 2,
            "timeout": 300,
            "retry": 360,
        },
    },
}
