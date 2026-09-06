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
