"""Development settings do HMD.

Uso:
    uv run python manage.py check --settings=config.settings.dev
    uv run python manage.py runserver --settings=config.settings.dev

Default explícito de desenvolvimento para ``SECRET_KEY``; em produção use
``config.settings.prod`` (que falha fechado sem ``DJANGO_SECRET_KEY``).
"""

import os

from config.settings.db import database_config

from .base import *  # noqa: F401,F403

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in ("true", "1", "yes")

# Default explícito de desenvolvimento — nunca use em produção.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-secret-key-not-for-production")

ALLOWED_HOSTS = ["*"]

# Resolução pura (config.settings.db): DATABASE_URL tem precedência; sem ela,
# defaults locais do compose dev. POSTGRES_HOST_PORT alinha o default à porta
# publicada pelo docker-compose quando diferir de 5432 (colisão com outro PG).
postgres_host_port = os.environ.get("POSTGRES_HOST_PORT", "5432")

DATABASES = {
    "default": database_config(
        os.environ,
        default_db_host="localhost",
        default_db_port=postgres_host_port,
        default_db_name="hmd_dev",
        default_db_user="hmd",
        default_db_password="hmd_dev",
        default_application_name="hmd",
        conn_max_age=0,
        conn_health_checks=False,
    )
}
