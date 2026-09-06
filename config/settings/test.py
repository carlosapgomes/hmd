"""Test settings do HMD.

Isolamento da suíte: banco de teste dedicado via ``TEST_DATABASE_URL`` (com
default local na porta 5433) — ``DATABASE_URL``/``DB_*`` de desenvolvimento
são ignorados. Nenhum teste do slice 001 acessa o banco; o detalhamento de
``db.py`` e do compose de teste chega no slice 002.

Uso:
    uv run pytest
"""

import dj_database_url

from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "test-secret-key-not-for-production"
ALLOWED_HOSTS = ["testserver"]

DATABASES = {
    "default": dj_database_url.config(
        default="postgres://hmd:hmd_dev@localhost:5433/hmd_test",
        conn_max_age=0,
        conn_health_checks=False,
        env="TEST_DATABASE_URL",
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
