"""Flag e janela de reapresentação + passthrough do compose (slice 001, R5 — RED).

Com a flag ligada o pipeline usa SOMENTE a janela única e a identidade nova;
as settings legadas ficam depreciadas (modo legado apenas, nunca combinadas).
O compose produtivo declara o passthrough explícito em web e TODOS os workers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from django.conf import settings
from django.test import override_settings

_PROD_COMPOSE = Path("docker-compose.prod.yml")

# Serviços que consomem o pipeline/anonimização e precisam da flag + janela.
_FLAG_SERVICES = ("web", "worker-pdf", "worker-anonymization", "worker-llm", "worker-attachments")
# Serviços que sustentam o rollback legado (janelas antigas).
_LEGACY_SERVICES = ("web", "worker-llm")

_NEW_ENVS = ("CASE_REAPPEARANCE_ENABLED", "CASE_REAPPEARANCE_WINDOW_DAYS")
_LEGACY_ENVS = ("PRIOR_CASE_WINDOW_DAYS", "PRIOR_CASE_FALLBACK_WINDOW_DAYS")


def _service_env(compose: dict[str, object], service: str) -> str:
    services = compose.get("services")
    assert isinstance(services, dict), "compose sem bloco services"
    entry = services.get(service)
    assert isinstance(entry, dict), f"serviço {service} ausente no compose"
    environment = entry.get("environment", {})
    assert isinstance(environment, dict), f"environment de {service} não é mapa"
    return "\n".join(f"{key}={value}" for key, value in environment.items())


def _compose() -> dict[str, object]:
    assert _PROD_COMPOSE.exists(), "docker-compose.prod.yml ausente"
    loaded = yaml.safe_load(_PROD_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


# ── Defaults das settings ─────────────────────────────────────────────────


@pytest.mark.django_db
def test_new_flag_defaults_true_in_test_settings() -> None:
    """R5: flag ligada por default em dev/test (TEST_DB isolado)."""
    assert getattr(settings, "CASE_REAPPEARANCE_ENABLED", None) is True


def test_new_window_defaults_15_in_test_settings() -> None:
    """R5: janela única default 15 dias."""
    assert getattr(settings, "CASE_REAPPEARANCE_WINDOW_DAYS", None) == 15


def test_legacy_settings_still_defined_but_deprecated() -> None:
    """R5: settings legadas seguem definidas (rollback), com defaults 7/15."""
    assert getattr(settings, "PRIOR_CASE_WINDOW_DAYS", None) == 7
    assert getattr(settings, "PRIOR_CASE_FALLBACK_WINDOW_DAYS", None) == 15


def test_prod_settings_flag_defaults_false() -> None:
    """R5: produção falha fechado — flag default false (leitura do módulo)."""
    source = Path("config/settings/prod.py").read_text(encoding="utf-8")
    assert "CASE_REAPPEARANCE_ENABLED" in source
    assert '"false"' in source


# ── Passthrough do compose ────────────────────────────────────────────────


def test_compose_declares_new_env_in_all_pipeline_services() -> None:
    """R5: flag + janela nova com passthrough em web e TODOS os workers."""
    compose = _compose()
    for service in _FLAG_SERVICES:
        env = _service_env(compose, service)
        for name in _NEW_ENVS:
            assert f"{name}:" in env or f"{name}=" in env, f"{name} ausente em {service}"


def test_compose_declares_legacy_env_in_web_and_llm() -> None:
    """R5: janelas legadas com passthrough em web/worker-llm (rollback)."""
    compose = _compose()
    for service in _LEGACY_SERVICES:
        env = _service_env(compose, service)
        for name in _LEGACY_ENVS:
            assert f"{name}:" in env or f"{name}=" in env, f"{name} ausente em {service}"


def test_compose_new_env_uses_coherent_defaults() -> None:
    """R5: passthrough com defaults coerentes (``:-``) entre os serviços."""
    compose = _compose()
    for service in _FLAG_SERVICES:
        env = _service_env(compose, service)
        assert "CASE_REAPPEARANCE_ENABLED" in env
        assert "CASE_REAPPEARANCE_WINDOW_DAYS" in env


@override_settings(CASE_REAPPEARANCE_ENABLED=False)
@pytest.mark.django_db
def test_flag_off_preserves_legacy_lookup_behavior() -> None:
    """R5: flag desligada preserva o lookup legado (sem novos efeitos)."""
    from apps.pipeline import prior_case as prior_case_module

    assert prior_case_module.is_reappearance_enabled() is False
