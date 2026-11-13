"""Smoke tests do scaffold Django (slice 001).

Provam, sem banco de dados:
- R1: settings carregam, ``APP_DISPLAY_NAME`` = "SAR – HMD",
  ``ROOT_URLCONF`` resolvível e a home (autenticada desde o slice 004)
  redireciona anônimos ao login;
- R6: ``config.settings.prod`` falha fechado (``ImproperlyConfigured``) quando
  ``DJANGO_SECRET_KEY`` não está definida.
"""

import importlib
import re
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import Client
from django.urls import reverse

EXPECTED_APP_DISPLAY_NAME = "SAR – HMD"


def test_settings_carregam() -> None:
    assert settings.ROOT_URLCONF == "config.urls"


def test_app_display_name_default() -> None:
    assert settings.APP_DISPLAY_NAME == EXPECTED_APP_DISPLAY_NAME


def test_compose_prod_app_display_name_default_is_sar_hmd() -> None:
    """P1-compose: sem APP_DISPLAY_NAME no ambiente, o deploy resolve SAR – HMD.

    O fallback do docker-compose.prod.yml (${APP_DISPLAY_NAME:-...}) não pode
    anular o default das settings com o nome antigo; o mecanismo de override
    por ambiente (${APP_DISPLAY_NAME:-...}) deve permanecer intacto.
    """
    compose_path = Path(__file__).resolve().parents[1] / "docker-compose.prod.yml"
    text = compose_path.read_text(encoding="utf-8")
    match = re.search(
        r"^\s*APP_DISPLAY_NAME:\s*\$\{APP_DISPLAY_NAME:-(.+)\}\s*$",
        text,
        flags=re.MULTILINE,
    )
    assert match is not None
    # Com APP_DISPLAY_NAME ausente no ambiente, a substituição do Compose
    # resolve para o fallback capturado — isto é o default de deploy.
    assert match.group(1) == EXPECTED_APP_DISPLAY_NAME


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
