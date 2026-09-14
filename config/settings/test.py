"""Test settings do HMD.

Isolamento da suíte: banco de teste dedicado via ``TEST_DATABASE_URL`` (default
local na porta 5433 / ``TEST_DB_PORT``) resolvido pela função pura de
``config.settings.db`` com prefixo ``TEST_`` — ``DATABASE_URL``/``DB_*`` de
desenvolvimento são ignorados mesmo com ``.env`` carregado (slice 002).

Uso:
    uv run pytest   # com o compose de teste no ar
"""

import os

# Imunidade REAL a env hostil (review F2 do slice phase2-workers-secrets): a
# resolução do segredo acontece NO IMPORT de base — precisamos limpar as
# chaves ANTES do `from .base import *`, senão um .env do host com
# OPENROUTER_API_KEY_FILE inválido aborta a importação da suíte inteira
# (fail-closed alto, mas quebra a coleção de testes).
os.environ.pop("OPENROUTER_API_KEY_FILE", None)
os.environ.pop("OPENROUTER_API_KEY", None)

from config.settings.db import database_config  # noqa: E402

from .base import *  # noqa: F401,F403,E402

DEBUG = False
SECRET_KEY = "test-secret-key-not-for-production"
ALLOWED_HOSTS = ["testserver"]

# Resolução via função pura com prefixo TEST_: a suíte lê apenas
# TEST_DATABASE_URL/TEST_DB_* e ignora DATABASE_URL/DB_* de dev mesmo com
# .env carregado. TEST_DB_PORT alinha o default à porta publicada pelo
# docker-compose.test.yml quando diferir de 5433 (colisão com outro PG).
test_db_port = os.environ.get("TEST_DB_PORT", "5433")

DATABASES = {
    "default": database_config(
        os.environ,
        prefix="TEST_",
        default_db_host="localhost",
        default_db_port=test_db_port,
        default_db_name="hmd_test",
        default_db_user="hmd",
        default_db_password="hmd_dev",
        default_application_name="hmd",
        conn_max_age=0,
        conn_health_checks=False,
    )
}

# Hashers mais rápidos para a suíte.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False

# Determinístico independente de .env local.
APP_DISPLAY_NAME = "HMD — Hemodinâmica"

# Labels de unidade canônicos na suíte (change unit-labels-env, review P2):
# sem o pin, HMD_UNIT_*_LABEL no host/.env tornaria os testes de default
# não-determinísticos. O caminho env→settings continua coberto pelos testes
# diretos de _parse_unit_labels.
UNIT_LABELS = {1: "Unidade 1", 2: "Unidade 2"}

# Chave da OpenRouter (change phase2-workers-secrets, slice 001/D3): sem o pin,
# um .env do host com OPENROUTER_API_KEY/OPENROUTER_API_KEY_FILE tornaria a
# suíte não-determinística (precedente UNIT_LABELS). A chave real entra por
# ARQUIVO só nos workers de produção; a suíte nunca chama a OpenRouter.
OPENROUTER_API_KEY_FILE = ""
OPENROUTER_API_KEY = ""

# Processamento do intake roda inline na suíte (design D2/R4): as tasks são
# chamadas direto, sem cluster real — determinístico independente de um .env
# local com INTAKE_RUN_TASKS_INLINE=false.
INTAKE_RUN_TASKS_INLINE = True

# Intake HABILITADO na suíte (P1 review do slice 002 do pilot-deployment):
# dezenas de testes chamam o serviço de criação sem override — pinar aqui
# torna a suíte determinística mesmo com INTAKE_ENABLED=false no ambiente
# (ex.: .env copiado de um host do piloto).
INTAKE_ENABLED = True

# Processamento da anonimização roda inline na suíte (design D7/R2): o signal
# de entrada em ANONYMIZING executa a task sincronamente — determinístico
# independente de um .env local com ANONYMIZATION_RUN_TASKS_INLINE=false.
ANONYMIZATION_RUN_TASKS_INLINE = True

# Pipeline LLM roda inline na suíte (design D9/R4): o signal de entrada em
# LLM_EXTRACTING executa o orquestrador sincronamente — determinístico
# independente de um .env local com LLM_RUN_TASKS_INLINE=false. Testes de
# cadeia (anonymization transaction=True) que não exercem o pipeline sobrescrevem
# para False (o enqueue vira no-op async).
LLM_RUN_TASKS_INLINE = True

# Worker de anexos roda inline na suíte (change attachment-processing-ocr,
# slice 002, design D3): o signal do CASE_ANONYMIZATION_COMPLETED executa a
# task sincronamente — determinístico independente de um .env local com
# ATTACHMENTS_RUN_TASKS_INLINE=false (mesmo padrão dos três flags acima).
ATTACHMENTS_RUN_TASKS_INLINE = True
