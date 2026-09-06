"""Resolução da configuração do banco de dados (PostgreSQL).

Função pura ``database_config`` que resolve o dicionário de ``DATABASES``:

1. ``DATABASE_URL`` definida → configuração derivada da URL (precedência
   máxima; parâmetros de query como ``?sslmode=require`` são preservados);
2. ausente → montagem por variáveis individuais ``DB_*`` (sem round-trip de
   URL), com ``DB_PASSWORD_FILE`` (Docker secret/K8s) como fonte da senha.

A mesma função resolve o banco de teste quando chamada com ``prefix="TEST_"``:
``TEST_DATABASE_URL`` com precedência sobre ``TEST_DB_*`` — a suíte nunca lê
``DATABASE_URL``/``DB_*`` de desenvolvimento.

Divergência deliberada vs ats-web: configuração insuficiente (sem URL e sem
senha disponível) levanta ``ImproperlyConfigured`` — o HMD falha fechado em
vez de retornar ``{}`` silenciosamente.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import dj_database_url
from django.core.exceptions import ImproperlyConfigured


def _read_secret(env: Mapping[str, str], secret_file_key: str) -> str | None:
    """Lê o segredo do arquivo indicado por ``{nome}_FILE``, se apontado.

    O arquivo tem precedência sobre a variável de ambiente correspondente.
    Arquivo inexistente/ilegível ou vazio levanta ``ImproperlyConfigured``
    (falha fechada — nunca cair silenciosamente para outro valor).
    """
    secret_file = env.get(secret_file_key)
    if secret_file:
        try:
            secret = Path(secret_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ImproperlyConfigured(f"Não foi possível ler o segredo em {secret_file}.") from exc
        if not secret:
            raise ImproperlyConfigured(f"O arquivo indicado por {secret_file_key} está vazio.")
        return secret
    return None


def database_config(
    env: Mapping[str, str],
    *,
    prefix: str = "",
    default_db_host: str = "localhost",
    default_db_port: str = "5432",
    default_db_name: str = "hmd",
    default_db_user: str = "hmd",
    default_db_password: str | None = None,
    default_application_name: str = "hmd",
    conn_max_age: int = 0,
    conn_health_checks: bool = False,
) -> dict[str, Any]:
    """Monta a configuração de ``DATABASES["default"]`` a partir de *env*.

    Chaves consultadas são ``{prefix}DATABASE_URL`` e ``{prefix}DB_*``
    (com ``prefix=""`` para dev/prod e ``prefix="TEST_"`` para testes).
    """

    def key(name: str) -> str:
        return f"{prefix}{name}"

    # ── Caminho A: DATABASE_URL (precedência máxima) ────────────────────
    database_url = env.get(key("DATABASE_URL"))
    if database_url:
        return dict(
            dj_database_url.parse(
                database_url,
                conn_max_age=conn_max_age,
                conn_health_checks=conn_health_checks,
            )
        )

    # ── Caminho B: variáveis individuais DB_* ───────────────────────────
    db_password = _read_secret(env, key("DB_PASSWORD_FILE"))
    if db_password is None:
        db_password = env.get(key("DB_PASSWORD"))
    if db_password is None:
        db_password = default_db_password
    if not db_password:
        raise ImproperlyConfigured(
            f"{key('DATABASE_URL')} ausente e senha não configurada — defina "
            f"{key('DB_PASSWORD')} ou {key('DB_PASSWORD_FILE')}."
        )

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env.get(key("DB_NAME"), default_db_name),
        "USER": env.get(key("DB_USER"), default_db_user),
        "PASSWORD": db_password,
        "HOST": env.get(key("DB_HOST"), default_db_host),
        "PORT": env.get(key("DB_PORT"), default_db_port),
        "CONN_MAX_AGE": conn_max_age,
        "CONN_HEALTH_CHECKS": conn_health_checks,
        "OPTIONS": {
            "application_name": env.get(key("DB_APPLICATION_NAME"), default_application_name),
        },
    }
