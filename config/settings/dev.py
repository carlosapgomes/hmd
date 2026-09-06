"""Development settings do HMD.

Uso:
    uv run python manage.py check --settings=config.settings.dev
    uv run python manage.py runserver --settings=config.settings.dev

Default explícito de desenvolvimento para ``SECRET_KEY``; em produção use
``config.settings.prod`` (que falha fechado sem ``DJANGO_SECRET_KEY``).
"""

import os

import dj_database_url

from .base import *  # noqa: F401,F403

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in ("true", "1", "yes")

# Default explícito de desenvolvimento — nunca use em produção.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-secret-key-not-for-production")

ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": dj_database_url.config(
        default="postgres://hmd:hmd_dev@localhost:5432/hmd_dev",
        conn_max_age=0,
        conn_health_checks=False,
    )
}
