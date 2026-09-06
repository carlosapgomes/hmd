"""Testes do slice 002 — resolução de banco e isolamento da suíte.

Cobre:
- R1: precedência de ``DATABASE_URL`` sobre ``DB_*``; montagem por ``DB_*``
  sem URL; ``DB_PASSWORD_FILE`` como fonte da senha; configuração
  insuficiente → ``ImproperlyConfigured`` (falha fechada, divergência
  deliberada vs ats-web);
- R1b: a mesma função pura resolve o banco de teste com prefixo ``TEST_``;
- R5: settings de teste isoladas do banco de dev mesmo com ``DATABASE_URL``
  de desenvolvimento definida no ambiente, e migrações aplicadas no banco
  de teste dedicado.
"""

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.db import database_config

# ── R1: função pura de resolução ────────────────────────────────────────────


def test_database_url_tem_precedencia_sobre_db_vars() -> None:
    cfg = database_config(
        {
            "DATABASE_URL": "postgres://hmd:hmd_dev@db:5432/hmd_dev",
            "DB_HOST": "outro-host",
            "DB_PORT": "9999",
            "DB_NAME": "outro-nome",
            "DB_USER": "outro-user",
            "DB_PASSWORD": "outra-senha",
        }
    )
    assert cfg["HOST"] == "db"
    assert cfg["NAME"] == "hmd_dev"
    assert cfg["PORT"] == 5432


def test_sem_url_monta_configuracao_por_db_vars() -> None:
    cfg = database_config(
        {
            "DB_HOST": "localhost",
            "DB_PORT": "5432",
            "DB_NAME": "hmd_dev",
            "DB_USER": "hmd",
            "DB_PASSWORD": "hmd_dev",
        }
    )
    assert cfg["ENGINE"] == "django.db.backends.postgresql"
    assert cfg["HOST"] == "localhost"
    assert cfg["NAME"] == "hmd_dev"
    assert cfg["USER"] == "hmd"
    assert cfg["PASSWORD"] == "hmd_dev"


def test_db_password_file_fornece_a_senha(tmp_path: Path) -> None:
    secret_file = tmp_path / "db_password"
    secret_file.write_text("senha-secreta\n")

    cfg = database_config(
        {
            "DB_HOST": "localhost",
            "DB_PORT": "5432",
            "DB_NAME": "hmd_dev",
            "DB_USER": "hmd",
            "DB_PASSWORD_FILE": str(secret_file),
        }
    )
    assert cfg["PASSWORD"] == "senha-secreta"


def test_arquivo_de_senha_vazio_levanta_improperly_configured(tmp_path: Path) -> None:
    secret_file = tmp_path / "db_password_vazio"
    secret_file.write_text("")

    with pytest.raises(ImproperlyConfigured):
        database_config(
            {
                "DB_HOST": "localhost",
                "DB_PORT": "5432",
                "DB_NAME": "hmd_dev",
                "DB_USER": "hmd",
                "DB_PASSWORD_FILE": str(secret_file),
            }
        )


def test_configuracao_insuficiente_levanta_improperly_configured() -> None:
    # Sem DATABASE_URL e sem senha disponível (nem default) → falha fechada.
    with pytest.raises(ImproperlyConfigured):
        database_config({})


# ── R1b: resolução do banco de teste com prefixo TEST_ ─────────────────────


def test_prefixo_test_resolve_test_database_url() -> None:
    cfg = database_config(
        {
            "TEST_DATABASE_URL": "postgres://hmd:hmd_test@localhost:5433/hmd_test",
            # DATABASE_URL de desenvolvimento presente não pode interferir.
            "DATABASE_URL": "postgres://hmd:hmd_dev@db:5432/hmd_dev",
        },
        prefix="TEST_",
    )
    assert cfg["NAME"] == "hmd_test"
    assert cfg["PORT"] == 5433


def test_prefixo_test_monta_configuracao_por_test_db_vars() -> None:
    cfg = database_config(
        {
            "TEST_DB_HOST": "localhost",
            "TEST_DB_PORT": "5433",
            "TEST_DB_NAME": "hmd_test",
            "TEST_DB_USER": "hmd",
            "TEST_DB_PASSWORD": "hmd_dev",
        },
        prefix="TEST_",
    )
    assert cfg["NAME"] == "hmd_test"
    assert cfg["USER"] == "hmd"
    assert cfg["PORT"] == "5433"


# ── R5: settings de teste isoladas do banco de dev ─────────────────────────


def _reload_module(name: str) -> ModuleType:
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def test_settings_de_teste_ignoram_banco_de_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ambiente de dev configurado (URL do banco dev), sem URL de teste:
    # config.settings.test precisa continuar apontando para o banco de teste.
    monkeypatch.setenv("DATABASE_URL", "postgres://hmd:hmd_dev@db:5432/hmd_dev")
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    dev_module = _reload_module("config.settings.dev")
    test_module = _reload_module("config.settings.test")

    dev_db = dev_module.DATABASES["default"]
    test_db = test_module.DATABASES["default"]

    assert dev_db["NAME"] == "hmd_dev"
    assert test_db["NAME"] == "hmd_test"
    assert test_db["NAME"] != dev_db["NAME"]
    assert str(test_db["PORT"]) != str(dev_db["PORT"])


@pytest.mark.django_db
def test_migracoes_aplicadas_no_banco_de_teste() -> None:
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.django_migrations')")
        assert cursor.fetchone()[0] == "django_migrations"
