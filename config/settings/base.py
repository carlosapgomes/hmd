"""Base Django settings do HMD — Hemodinâmica.

Settings independentes de ambiente, compartilhadas por dev/prod/test.
Valores sensíveis ou variáveis por ambiente são lidos do ambiente nos módulos
por ambiente (``config.settings.{dev,prod,test}``); veja ``.env.example``.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Carrega variáveis locais de .env sem sobrescrever o ambiente do processo.
load_dotenv(BASE_DIR / ".env")

# Nome do app exibido no cabeçalho, título da página e meta tags.
APP_DISPLAY_NAME = os.environ.get("APP_DISPLAY_NAME", "HMD — Hemodinâmica")

INSTALLED_APPS = [
    # Admin (change admin-local-identity, slice 001/D3): app config custom com
    # ``default_site`` = ``config.admin.HmdAdminSite`` — o ``admin.site``
    # global resolve para a subclass (login coberto pelo anti-lockout local).
    "config.admin.HmdAdminConfig",
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
    # Fila médica (change doctor-queue-decision, slice 002): fila por estado
    # com filtro de subtipo sob access control fechado (matriz D2).
    "apps.doctor",
    # Anonimização (change presidio-anonymization, slice 001): módulo puro de
    # pré-extração determinística; sem models/recognizers neste slice.
    "apps.anonymization",
    # Pipeline LLM (change llm-pipeline-per-type, slice 001): cliente OpenRouter
    # + llm_check; sem models neste slice (chegam no slice 004).
    "apps.pipeline",
    # Prompts LLM versionados (change llm-pipeline-per-type, slice 003):
    # PromptTemplate (1 ativo por nome via constraint parcial) + seeds
    # idempotentes (28 templates) + montagem dos prompts do caso.
    "apps.llm",
    # Fila do agendador (change scheduler-multi-unit, slice 001): serviços
    # transacionais de confirmar/negar o agendamento sobre os campos de D1;
    # a fila/detalhe/UI do papel scheduler chegam no slice 003.
    "apps.scheduler",
    # Anexos clínicos (change attachment-processing-ocr, slice 001): model
    # CaseAttachment + validação própria (apps/attachments/services.py); o
    # worker/extração/verificação chegam nos slices 002/003.
    "apps.attachments",
    # Painel gerencial (change dashboard-notifications-pwa, slice 003):
    # serviços puros de métricas + view dashboard:home (login-required,
    # transversal, zero-PHI). Sem models próprios.
    "apps.dashboard",
]

# Modelo de usuário customizado (D8) — estendido uma única vez.
AUTH_USER_MODEL = "accounts.User"

# Autenticação (change admin-local-identity, slice 001, design D1): dois
# backends em ordem fixa — Kerberos primeiro (usuários com `ad_upn` autenticam
# via AD); local por design (ADR-0009) para o perfil administrativo
# (superusuário sem `ad_upn`) — sem flag de habilitação (D2).
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.KerberosBackend",
    "apps.accounts.backends.LocalAccountBackend",
]

# Validação Kerberos no Active Directory (change ad-kerberos, D1/D5/D10).
# ``AD_DCS`` são os KDCs conhecidos do domínio raiz; o realm NÃO é
# configurável: é derivado do sufixo do ``ad_upn`` no momento da validação
# (D3 — floresta multi-domínio). ``AD_KDC_TIMEOUT`` é o timeout por tentativa
# (default 3s). Failover entre DCs só em erro de transporte (D5).
# Sem ``AD_DCS`` configurado o login AD fica indisponível por config
# (fail-closed nomeado — nenhum endereço interno fica embutido no repo;
# provisionar via env no ambiente de cada deploy).
AD_DCS = [dc.strip() for dc in os.environ.get("AD_DCS", "").split(",") if dc.strip()]
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

# Rótulos das unidades de agendamento (change unit-labels-env, slice 001/D1):
# exibição configurável por ambiente, sem outra validação além do ``strip``
# (rótulo operacional, exibido como fornecido). A resolução fica na fonte única
# ``apps.cases.units``; ``SchedulingUnit`` segue o canônico/default do model.
_UNIT_LABEL_DEFAULTS: dict[int, str] = {1: "Unidade 1", 2: "Unidade 2"}


def _parse_unit_labels(environ: Mapping[str, str]) -> dict[int, str]:
    """Rótulos das unidades vindos das envs; vazio → default canônico."""
    return {
        unit: (environ.get(f"HMD_UNIT_{unit}_LABEL") or "").strip() or default
        for unit, default in _UNIT_LABEL_DEFAULTS.items()
    }


UNIT_LABELS = _parse_unit_labels(os.environ)

# Notificações in-app (change dashboard-notifications-pwa, slice 002, design
# D2): janela de retenção, em horas, das notificações JÁ LIDAS na lista. As não
# lidas nunca somem e nada é apagado — a leitura fora da janela apenas sai do
# resultado de ``UserNotification.objects.visible_for_list()``.
NOTIFICATION_READ_RETENTION_HOURS = int(os.environ.get("NOTIFICATION_READ_RETENTION_HOURS", "48"))

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
                "apps.accounts.context_processors.notification_unread_count",
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

# Anexos do intake (change attachment-processing-ocr, slice 001, design D2/D7):
# limites por caso — quantidade máxima de anexos e tamanho máximo por arquivo
# em MB, além dos content-types aceitos (jpg/png/pdf; defaults 5/10).
# Consumidos pela fonte única de validação (apps/attachments/services.py)
# ANTES de qualquer gravação na criação do caso.
ATTACHMENTS_MAX_COUNT = int(os.environ.get("ATTACHMENTS_MAX_COUNT", "5"))
ATTACHMENTS_MAX_SIZE_MB = int(os.environ.get("ATTACHMENTS_MAX_SIZE_MB", "10"))
ATTACHMENTS_ACCEPTED_MIME_TYPES = [
    mime.strip()
    for mime in os.environ.get(
        "ATTACHMENTS_ACCEPTED_MIME_TYPES",
        "image/jpeg,image/png,application/pdf",
    ).split(",")
    if mime.strip()
]
# Modo de execução do worker de anexos (change attachment-processing-ocr,
# slice 002, design D3/D7): ``True`` (default em dev/teste) executa a task
# sincronamente quando o signal de anonimização dispara — testes
# determinísticos, sem worker; ``False`` enfileira no cluster ``attachments``
# do django-q2 (produção/compose com o serviço worker-attachments).
ATTACHMENTS_RUN_TASKS_INLINE = os.environ.get("ATTACHMENTS_RUN_TASKS_INLINE", "true").lower() in (
    "true",
    "1",
    "yes",
)
# Teto de páginas rasterizadas por anexo PDF-imagem para o OCR externo
# (design D7, default 10): excedente → anexo ``failed`` com motivo claro — o
# envio externo em lote nunca é cego.
ATTACHMENTS_VISION_MAX_PAGES = int(os.environ.get("ATTACHMENTS_VISION_MAX_PAGES", "10"))

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

# Chave geral do intake do NIR (change pilot-deployment-v0-1-1, slice 002,
# design D7): ``True`` (default em dev/teste) permite criar casos/reenviar
# documentos; ``False`` bloqueia fail-closed no boundary do serviço (guard
# ``_assert_intake_enabled``) e informa o usuário nas rotas de POST. Em
# produção o default é ``False`` (mesma convenção dos flags
# ``*_RUN_TASKS_INLINE``) — fase 1 do piloto sem upload.
INTAKE_ENABLED = os.environ.get("INTAKE_ENABLED", "true").lower() in (
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

# Harness de benchmark de anonimização (change presidio-anonymization, slice
# 005, design D9/R2): recall mínimo por tipo de entidade para o comando
# ``anonymization_benchmark --corpus <arquivo>`` passar (default 0.90; o corpus
# sintético versionado roda na suíte; o corpus real é aceite operacional) e
# limite duro opcional do pico de RSS do processo em MB (ausente/0 = desligado
# — o RSS é sempre reportado; o limite é critério operacional pré-produção).
ANONYMIZATION_BENCHMARK_MIN_RECALL = float(
    os.environ.get("ANONYMIZATION_BENCHMARK_MIN_RECALL", "0.90")
)
ANONYMIZATION_BENCHMARK_MAX_RSS_MB = (
    int(os.environ.get("ANONYMIZATION_BENCHMARK_MAX_RSS_MB", "0")) or None
)

# Cliente LLM OpenRouter (change llm-pipeline-per-type, slice 001, design
# D1): SDK OpenAI apontando para a OpenRouter — base URL default
# ``https://openrouter.ai/api/v1``. ``OPENROUTER_API_KEY`` e os modelos por
# estágio (``LLM1_MODEL``/``LLM2_MODEL``) têm default vazio — fail-fast no uso:
# ``llm_check`` reporta a ausência; o pipeline (slice 004+) exige as envs.
# ``LLM_TIMEOUT_SECONDS`` é o timeout por chamada (default 120).
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
LLM1_MODEL = os.environ.get("LLM1_MODEL", "")
LLM2_MODEL = os.environ.get("LLM2_MODEL", "")
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "120"))
# Modelo OpenAI-compatível da OpenRouter para OCR de imagem/PDF-imagem dos
# anexos (change attachment-processing-ocr, slice 002, design D3/D7): env SEM
# default ("" — fail-closed no uso: sem modelo configurado a transcrição
# falha com LlmError antes de qualquer envio). Escolha por benchmark
# operacional, como LLM1_MODEL/LLM2_MODEL.
VISION_MODEL = os.environ.get("VISION_MODEL", "")
# Factory injetável do cliente real (D1, padrão KERBEROS_CLIENT_FACTORY do
# change 02): dotted-path resolvido em tempo de chamada por
# ``apps.pipeline.llm``. Testes sobrescrevem com fakes via
# ``override_settings`` — nenhuma chamada de rede na suíte.
LLM_CLIENT_FACTORY = "apps.pipeline.llm.create_openrouter_client"

# Prior-case (change llm-pipeline-per-type, slice 005, design D7): janelas do
# lookup de casos prévios em dias — match primário por número de ocorrência
# (default 7) e fallback por nome normalizado + nascimento (default 15). O
# intervalo é fechado: ``decisão_prévia <= case.created_at <= decisão_prévia +
# janela`` (decisão "futura" nunca casa — correção do review).
PRIOR_CASE_WINDOW_DAYS = int(os.environ.get("PRIOR_CASE_WINDOW_DAYS", "7"))
PRIOR_CASE_FALLBACK_WINDOW_DAYS = int(os.environ.get("PRIOR_CASE_FALLBACK_WINDOW_DAYS", "15"))

# Modo de execução da task do pipeline LLM (change llm-pipeline-per-type,
# slice 006, design D9/R4): ``True`` (default em dev/teste) executa o pipeline
# sincronamente quando o signal de entrada em LLM_EXTRACTING dispara — testes
# determinísticos e cadeia inline de dev single-process; ``False`` enfileira no
# cluster ``llm`` do django-q2 (produção/compose com o serviço worker-llm — o
# processo web nunca chama a OpenRouter).
LLM_RUN_TASKS_INLINE = os.environ.get("LLM_RUN_TASKS_INLINE", "true").lower() in (
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
# por ``Q_CLUSTER_NAME=anonymization``; o cluster ``llm`` (slice 006 — pipeline
# LLM1/LLM2, 1 worker/timeout 900s/retry 960s) por ``Q_CLUSTER_NAME=llm``; o
# cluster ``attachments`` (slice 002 do change 10 — OCR externo por página com
# teto de páginas, 2 workers/timeout 900s/retry 960s) por
# ``Q_CLUSTER_NAME=attachments`` (produção: mesmo worker do worker-llm).
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
        "llm": {
            "workers": 1,
            "timeout": 900,
            "retry": 960,
        },
        # Anexos (slice 002 — OCR externo com a SDK da OpenRouter): chamadas
        # por página com timeout LLM_TIMEOUT_SECONDS e teto de páginas por
        # anexo; tempo/retry generosos espelhando o cluster llm (produção =
        # mesmo worker do worker-llm com Q_CLUSTER_NAME=attachments).
        "attachments": {
            "workers": 2,
            "timeout": 900,
            "retry": 960,
        },
    },
}
