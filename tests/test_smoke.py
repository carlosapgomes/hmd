"""Smoke tests do scaffold Django (slice 001).

Provam, sem banco de dados:
- R1: settings carregam, ``APP_DISPLAY_NAME`` = "HMD — Hemodinâmica",
  ``ROOT_URLCONF`` resolvível e a home (autenticada desde o slice 004)
  redireciona anônimos ao login;
- R6: ``config.settings.prod`` falha fechado (``ImproperlyConfigured``) quando
  ``DJANGO_SECRET_KEY`` não está definida.
"""

import importlib
import sys

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import Client
from django.urls import reverse

EXPECTED_APP_DISPLAY_NAME = "HMD — Hemodinâmica"


def test_settings_carregam() -> None:
    assert settings.ROOT_URLCONF == "config.urls"


def test_app_display_name_default() -> None:
    assert settings.APP_DISPLAY_NAME == EXPECTED_APP_DISPLAY_NAME


def test_root_urlconf_is_resolvable() -> None:
    assert reverse("home") == "/"


def test_home_redirects_anonymous_to_login() -> None:
    """R4 (slice 004): a home passou a exigir login; anônimos vão ao login."""
    response = Client().get("/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login/")


def test_prod_requires_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
    sys.modules.pop("config.settings.prod", None)
    with pytest.raises(ImproperlyConfigured):
        importlib.import_module("config.settings.prod")
