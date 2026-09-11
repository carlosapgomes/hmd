"""Test settings do HMD.

Isolamento da suíte: banco de teste dedicado via ``TEST_DATABASE_URL`` (default
local na porta 5433 / ``TEST_DB_PORT``) resolvido pela função pura de
``config.settings.db`` com prefixo ``TEST_`` — ``DATABASE_URL``/``DB_*`` de
desenvolvimento são ignorados mesmo com ``.env`` carregado (slice 002).

Uso:
    uv run pytest   # com o compose de teste no ar
"""

import os

from config.settings.db import database_config

from .base import *  # noqa: F401,F403

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
