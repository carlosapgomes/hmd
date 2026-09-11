"""Testes do slice 005 — segredos por arquivos (change pilot-deployment-v0-1-1).

Cobre:
- R1: ``SECRET_KEY`` de produção lida de ``DJANGO_SECRET_KEY_FILE`` (arquivo
  com PRECEDÊNCIA sobre ``DJANGO_SECRET_KEY``; arquivo vazio/ilegível e
  ausência de fonte falham fechado com ``ImproperlyConfigured``);
- R1: ``DATABASES`` de produção montadas por ``DB_*`` + ``DB_PASSWORD_FILE``
  SEM ``DATABASE_URL`` no ambiente (fail-closed nomeado sem senha/arquivo);
- R2: ``seed_admin`` aceita ``DJANGO_SUPERUSER_PASSWORD_FILE`` (arquivo com
  precedência sobre ``DJANGO_SUPERUSER_PASSWORD``; fail-closed sem fonte);
- R3/R4: ``docker-compose.prod.yml`` resolve com TODOS os profiles e um
  segredo por consumidor, sem ``DATABASE_URL``/``MIGRATOR_DATABASE_URL``;
- Hardening de container (diretiva blueprint/Eon): limites PLANEJADOS de
  fase 1 do web (512m/1.0/200) + rootfs read-only/no-new-privileges/
  cap_drop ALL (risco residual de root aceito na fase 1) e a env
  ``DJANGO_SETTINGS_MODULE=prod`` do web (P1: o wsgi.py defaulta p/ dev e
  o gunicorn não aceita --settings).

O import de ``config.settings.prod`` segue o padrão de ``test_health.py``:
``importlib`` com envs dummy e ``sys.modules.pop`` a cada teste — nenhum
segredo real entra na suíte e as settings ativas (``config.settings.test``) não
são afetadas.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError

User = get_user_model()

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = "docker-compose.prod.yml"
PROD_SECRET_KEY = "chave-de-teste-de-producao"
PROD_DATABASE_URL = "postgres://build:build@localhost/build"
SECRET_FILE_NAMES = (
    "app_db_password",
    "migrator_db_password",
    "secret_key",
    "superuser_password",
)


def _import_prod() -> ModuleType:
    """Importa ``config.settings.prod`` de novo (sem valores reais no ambiente)."""
    sys.modules.pop("config.settings.prod", None)
    return importlib.import_module("config.settings.prod")


def _set_minimal_prod_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfaz a resolução de banco p/ os testes que só olham a SECRET_KEY."""
    monkeypatch.setenv("DATABASE_URL", PROD_DATABASE_URL)
    monkeypatch.delenv("DJANGO_SECRET_KEY_FILE", raising=False)


# ── R1: SECRET_KEY por arquivo ──────────────────────────────────────────────


class TestProdSecretKeyFile:
    """R1: ``DJANGO_SECRET_KEY_FILE`` com precedência e fail-closed."""

    def test_prod_secret_key_file_precedence(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Com arquivo E env presentes, o ARQUIVO vence."""
        secret_file = tmp_path / "secret_key"
        secret_file.write_text("chave-do-arquivo\n")
        _set_minimal_prod_env(monkeypatch)
        monkeypatch.setenv("DJANGO_SECRET_KEY", "chave-da-env")
        monkeypatch.setenv("DJANGO_SECRET_KEY_FILE", str(secret_file))

        prod = _import_prod()

        assert prod.SECRET_KEY == "chave-do-arquivo"

    def test_prod_secret_key_env_sem_arquivo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sem arquivo, a env ``DJANGO_SECRET_KEY`` continua valendo."""
        _set_minimal_prod_env(monkeypatch)
        monkeypatch.setenv("DJANGO_SECRET_KEY", "chave-da-env")

        prod = _import_prod()

        assert prod.SECRET_KEY == "chave-da-env"

    def test_prod_secret_key_fail_closed_sem_fonte(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sem arquivo e sem env, a inicialização aborta (erro existente)."""
        _set_minimal_prod_env(monkeypatch)
        monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)

        with pytest.raises(ImproperlyConfigured):
            _import_prod()

    def test_prod_secret_key_arquivo_vazio_fail_closed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Arquivo apontado mas vazio → ``ImproperlyConfigured`` nomeado."""
        secret_file = tmp_path / "secret_key_vazio"
        secret_file.write_text("")
        _set_minimal_prod_env(monkeypatch)
        monkeypatch.setenv("DJANGO_SECRET_KEY", "chave-da-env")
        monkeypatch.setenv("DJANGO_SECRET_KEY_FILE", str(secret_file))

        with pytest.raises(ImproperlyConfigured) as excinfo:
            _import_prod()

        assert "DJANGO_SECRET_KEY_FILE" in str(excinfo.value)

    def test_prod_secret_key_arquivo_ilegivel_fail_closed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Arquivo apontado mas inexistente → ``ImproperlyConfigured`` nomeado."""
        _set_minimal_prod_env(monkeypatch)
        monkeypatch.setenv("DJANGO_SECRET_KEY", "chave-da-env")
        monkeypatch.setenv("DJANGO_SECRET_KEY_FILE", str(tmp_path / "nao-existe"))

        with pytest.raises(ImproperlyConfigured):
            _import_prod()


# ── R1: DATABASES por DB_* + DB_PASSWORD_FILE (sem DATABASE_URL) ────────────


class TestProdDatabaseComponents:
    """R1: o compose monta o banco por ``DB_*`` (sem URL no ambiente)."""

    def test_prod_db_components_without_url(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``DB_*`` + ``DB_PASSWORD_FILE`` resolvem a conexão sem URL."""
        password_file = tmp_path / "app_db_password"
        password_file.write_text("senha-da-aplicacao\n")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.setenv("DB_HOST", "postgres-app-hmd")
        monkeypatch.setenv("DB_PORT", "5432")
        monkeypatch.setenv("DB_NAME", "app_hmd")
        monkeypatch.setenv("DB_USER", "hmd_app")
        monkeypatch.setenv("DB_PASSWORD_FILE", str(password_file))
        monkeypatch.setenv("DJANGO_SECRET_KEY", PROD_SECRET_KEY)
        monkeypatch.delenv("DJANGO_SECRET_KEY_FILE", raising=False)

        prod = _import_prod()
        database = prod.DATABASES["default"]

        assert database["ENGINE"] == "django.db.backends.postgresql"
        assert database["HOST"] == "postgres-app-hmd"
        assert database["PORT"] == "5432"
        assert database["NAME"] == "app_hmd"
        assert database["USER"] == "hmd_app"
        assert database["PASSWORD"] == "senha-da-aplicacao"
        assert database["CONN_MAX_AGE"] == 600
        assert database["CONN_HEALTH_CHECKS"] is True

    def test_prod_db_sem_senha_nem_arquivo_fail_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sem URL, sem senha e sem arquivo → aborta com erro nomeado."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.delenv("DB_PASSWORD_FILE", raising=False)
        monkeypatch.setenv("DB_HOST", "postgres-app-hmd")
        monkeypatch.setenv("DB_NAME", "app_hmd")
        monkeypatch.setenv("DB_USER", "hmd_app")
        monkeypatch.setenv("DJANGO_SECRET_KEY", PROD_SECRET_KEY)
        monkeypatch.delenv("DJANGO_SECRET_KEY_FILE", raising=False)

        with pytest.raises(ImproperlyConfigured) as excinfo:
            _import_prod()

        assert "senha não configurada" in str(excinfo.value)


# ── R2: seed_admin por arquivo ──────────────────────────────────────────────


@pytest.mark.django_db
class TestSeedAdminPasswordFile:
    """R2: ``seed_admin`` aceita ``DJANGO_SUPERUSER_PASSWORD_FILE``."""

    def test_seed_admin_password_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Senha lida do arquivo (username segue por env não-sensível)."""
        password_file = tmp_path / "superuser_password"
        password_file.write_text("senha-do-arquivo\n")
        monkeypatch.setenv("DJANGO_SUPERUSER_USERNAME", "admin-arquivo")
        monkeypatch.delenv("DJANGO_SUPERUSER_PASSWORD", raising=False)
        monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD_FILE", str(password_file))

        call_command("seed_admin")

        user = User.objects.get(username="admin-arquivo")
        assert user.check_password("senha-do-arquivo")

    def test_seed_admin_password_file_precedence(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Com arquivo E env, o ARQUIVO vence."""
        password_file = tmp_path / "superuser_password"
        password_file.write_text("senha-do-arquivo\n")
        monkeypatch.setenv("DJANGO_SUPERUSER_USERNAME", "admin-arquivo")
        monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "senha-da-env")
        monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD_FILE", str(password_file))

        call_command("seed_admin")

        user = User.objects.get(username="admin-arquivo")
        assert user.check_password("senha-do-arquivo")
        assert not user.check_password("senha-da-env")

    def test_seed_admin_fail_closed_sem_fonte(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sem env e sem arquivo, o comando aborta (erro existente)."""
        monkeypatch.setenv("DJANGO_SUPERUSER_USERNAME", "admin-sem-senha")
        monkeypatch.delenv("DJANGO_SUPERUSER_PASSWORD", raising=False)
        monkeypatch.delenv("DJANGO_SUPERUSER_PASSWORD_FILE", raising=False)

        with pytest.raises(CommandError):
            call_command("seed_admin")

    def test_seed_admin_password_file_vazio_fail_closed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Arquivo apontado mas vazio → aborta fechado (nomeado)."""
        password_file = tmp_path / "superuser_password_vazio"
        password_file.write_text("")
        monkeypatch.setenv("DJANGO_SUPERUSER_USERNAME", "admin-arquivo-vazio")
        monkeypatch.delenv("DJANGO_SUPERUSER_PASSWORD", raising=False)
        monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD_FILE", str(password_file))

        with pytest.raises(ImproperlyConfigured):
            call_command("seed_admin")


# ── R3/R4: compose resolve com todos os profiles e sem URL no ambiente ──────


def _create_secret_dummies(tmp_path: Path) -> dict[str, str]:
    """Cria um arquivo dummy por segredo e devolve o mapa env → caminho."""
    files: dict[str, str] = {}
    for name in SECRET_FILE_NAMES:
        secret_file = tmp_path / f"{name}.txt"
        secret_file.write_text(f"dummy-{name}\n")
        files[name] = str(secret_file)
    return files


def _compose_env(dummies: dict[str, str]) -> dict[str, str]:
    """Ambiente do compose: dummies apontados pelas envs ``*_FILE``."""
    return {
        **os.environ,
        "APP_DB_PASSWORD_FILE": dummies["app_db_password"],
        "MIGRATOR_DB_PASSWORD_FILE": dummies["migrator_db_password"],
        "SECRET_KEY_FILE": dummies["secret_key"],
        "SUPERUSER_PASSWORD_FILE": dummies["superuser_password"],
    }


def _run_compose(config_env: dict[str, str], *, quiet: bool) -> subprocess.CompletedProcess[str]:
    command = [
        "docker",
        "compose",
        "--profile",
        "migrate",
        "--profile",
        "workers",
        "-f",
        COMPOSE_FILE,
        "--env-file",
        ".env.example",
        "config",
    ]
    if quiet:
        command.append("--quiet")
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=config_env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose indisponível")
def test_compose_config_valid_with_all_profiles(tmp_path: Path) -> None:
    """``config --quiet`` resolve o compose com migrate+workers (R3/R4)."""
    result = _run_compose(_compose_env(_create_secret_dummies(tmp_path)), quiet=True)

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose indisponível")
def test_compose_config_uses_secret_files(tmp_path: Path) -> None:
    """O compose renderizado não tem URL de banco e monta /run/secrets (R3)."""
    dummies = _create_secret_dummies(tmp_path)

    result = _run_compose(_compose_env(dummies), quiet=False)

    assert result.returncode == 0, result.stderr
    rendered = result.stdout
    assert "DATABASE_URL" not in rendered
    assert "MIGRATOR_DATABASE_URL" not in rendered
    # As envs ``*_FILE`` (dummies em tmp) são as fontes dos segredos.
    assert f"file: {dummies['secret_key']}" in rendered
    assert f"file: {dummies['app_db_password']}" in rendered
    assert f"file: {dummies['migrator_db_password']}" in rendered
    assert f"file: {dummies['superuser_password']}" in rendered
    assert "/run/secrets/secret_key" in rendered
    assert "/run/secrets/app_db_password" in rendered
    assert "/run/secrets/migrator_db_password" in rendered
    assert "/run/secrets/superuser_password" in rendered


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose indisponível")
def test_compose_trusted_proxy_default_is_cloudflare(tmp_path: Path) -> None:
    """Topologia do piloto (Cloudflared → Caddy → HMD): o default do compose
    para o header de IP confiável é ``HTTP_CF_CONNECTING_IP`` (borda
    Cloudflare fixa com o cliente real; Caddy repassa sem tocar). Um default
    de XFF aqui faria o guard de intranet ler o IP do cloudflared (finding
    de integração do blueprint, 2026-09-11)."""
    env = _compose_env(_create_secret_dummies(tmp_path))
    env.pop("TRUSTED_PROXY_HEADER", None)

    result = _run_compose(env, quiet=False)

    assert result.returncode == 0, result.stderr
    assert "TRUSTED_PROXY_HEADER: HTTP_CF_CONNECTING_IP" in result.stdout


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose indisponível")
def test_compose_web_has_phase1_limits_and_container_hardening(tmp_path: Path) -> None:
    """Fase 1 (blueprint): limites planejados/iniciais do web + mitigação do
    risco residual de rodar como root (rootfs read-only, no-new-privileges,
    cap_drop ALL). Workers exigirão faixas próprias (fase 2)."""
    result = _run_compose(_compose_env(_create_secret_dummies(tmp_path)), quiet=False)

    assert result.returncode == 0, result.stderr
    web = result.stdout.split("  web:")[1].split("\n\n  ")[0]
    # O render do compose normaliza unidades (512m -> bytes; "1.0" -> 1).
    assert 'mem_limit: "536870912"' in web
    assert "cpus: 1" in web
    assert "pids_limit: 200" in web
    assert "read_only: true" in web
    assert "no-new-privileges:true" in web
    assert "cap_drop:" in web and "- ALL" in web
    # P1 (review de hardening): sem esta env o wsgi.py defaulta para settings
    # de DEV no web de produção (gunicorn não aceita --settings).
    assert "DJANGO_SETTINGS_MODULE: config.settings.prod" in web
