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
  cap_drop ALL (mitigações mantidas como defesa em profundidade) e a env
  ``DJANGO_SETTINGS_MODULE=prod`` do web (P1: o gunicorn não aceita
  --settings; o wsgi.py defaulta p/ prod desde o slice 001 do change
  image-hardening-v0-1-4).
- Checks estáticos do hardening da imagem (change image-hardening-v0-1-4,
  slice 001: entrypoint WSGI sem env cai em prod; slice 002): o Dockerfile
  cria o usuário dedicado não-root 10001 com ``/app/media`` próprio e declara
  ``USER`` depois do ``collectstatic``, e a dívida do adiamento do compose
  foi quitada.

O import de ``config.settings.prod`` segue o padrão de ``test_health.py``:
``importlib`` com envs dummy e ``sys.modules.pop`` a cada teste — nenhum
segredo real entra na suíte e as settings ativas (``config.settings.test``) não
são afetadas.
"""

from __future__ import annotations

import importlib
import os
import re
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
from django.test import override_settings

User = get_user_model()

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = "docker-compose.prod.yml"
DOCKERFILE = "Dockerfile"
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


# ── Slice 001 (image-hardening-v0-1-4): wsgi fail-safe → PROD ─────────────


def _run_wsgi_settings_probe(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Importa ``config.wsgi`` num subprocess e imprime o settings efetivo."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import config.wsgi; import django.conf; print(django.conf.settings.SETTINGS_MODULE)",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_wsgi_defaults_to_prod_settings_without_env(tmp_path: Path) -> None:
    """R1/R2: sem ``DJANGO_SETTINGS_MODULE`` no ambiente, o entrypoint WSGI
    carrega os settings de PRODUÇÃO — esquecer a env nunca mais ergue o site
    com a configuração de desenvolvimento (envs mínimas de prod: secret dummy
    em arquivo + banco por ``DB_*``)."""
    dummies = _create_secret_dummies(tmp_path)
    env = {
        **os.environ,
        "DJANGO_SECRET_KEY_FILE": dummies["secret_key"],
        "DB_PASSWORD_FILE": dummies["app_db_password"],
        "DB_HOST": "localhost",
        "DB_NAME": "hmd",
        "DB_USER": "hmd",
    }
    env.pop("DJANGO_SETTINGS_MODULE", None)
    env.pop("DJANGO_SECRET_KEY", None)
    env.pop("DATABASE_URL", None)

    result = _run_wsgi_settings_probe(env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "config.settings.prod"


def test_wsgi_env_overrides_default() -> None:
    """R2: com ``DJANGO_SETTINGS_MODULE`` setada, a env continua vencendo o
    default do wsgi (compose/CI seguem idênticos)."""
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test"}

    result = _run_wsgi_settings_probe(env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "config.settings.test"


def test_prod_cache_backend_imports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Regressão v0.1.3 (falha real de produção): o backend de cache de prod
    precisa RESOLVER como classe — o path ``...backends.database.`` (inválido;
    o módulo certo é ``...backends.db``) passava nos asserts de string porque
    settings não importam o backend no load, e explodia só no
    ``createcachetable``/runtime com ``InvalidCacheBackendError``."""
    from django.utils.module_loading import import_string

    _set_minimal_prod_env(monkeypatch)
    monkeypatch.setenv(
        "DJANGO_SECRET_KEY_FILE",
        str(
            _create_secret_dummies(__import__("pathlib").Path(__import__("tempfile").mkdtemp()))[
                "secret_key"
            ]
        ),
    )
    prod = _import_prod()

    backend_cls = import_string(prod.CACHES["default"]["BACKEND"])
    assert backend_cls.__module__ == "django.core.cache.backends.db"


def test_prod_settings_pass_django_check(tmp_path: Path) -> None:
    """Regressão v0.1.3: ``manage.py check`` com settings de PROD (envs
    dummy + secret em arquivo) precisa sair limpo — carrega o app completo
    com a configuração real do deploy."""
    from apps.accounts.tests.test_prod_secrets import _create_secret_dummies

    dummies = _create_secret_dummies(tmp_path)
    env = {
        **os.environ,
        "DJANGO_SECRET_KEY_FILE": dummies["secret_key"],
        "DB_PASSWORD_FILE": dummies["app_db_password"],
        "DB_HOST": "localhost",
        "DB_NAME": "hmd",
        "DB_USER": "hmd",
    }
    env.pop("DJANGO_SECRET_KEY", None)
    env.pop("DATABASE_URL", None)

    result = subprocess.run(
        [sys.executable, "manage.py", "check", "--settings=config.settings.prod"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.django_db
def test_database_cache_createcachetable_roundtrip() -> None:
    """Regressão v0.1.3 (mecanismo do migrate): valida ``createcachetable`` +
    set/get/delete com o backend ``db.DatabaseCache`` no banco de TESTE — o
    mesmo mecanismo do serviço migrate. A COBERTURA da config de prod em si
    está nos testes (a) import e (b) check; este usa path próprio para isolar
    o mecanismo."""
    from django.core.cache import caches
    from django.core.management import call_command

    table = "hmd_cache_regression"
    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.db.DatabaseCache",
                "LOCATION": table,
            }
        }
    ):
        call_command("createcachetable", table)
        cache = caches["default"]
        cache.set("readyz-probe", "ok", 30)
        assert cache.get("readyz-probe") == "ok"
        cache.delete("readyz-probe")
        assert cache.get("readyz-probe") is None
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")


# ── Slice 002 (image-hardening-v0-1-4): imagem roda como não-root ───────────

_USER_DIRECTIVE = re.compile(r"USER\s+(\S+)\s*$", re.IGNORECASE)
_RUN_OR_COPY_DIRECTIVE = re.compile(r"(RUN|COPY)\s", re.IGNORECASE)


def _deploy_artifact(relative_path: str) -> str:
    """Conteúdo cru de um artefato de deploy (checks estáticos de conteúdo)."""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _dockerfile_instructions() -> list[str]:
    """Instruções do Dockerfile, sem comentários nem linhas em branco."""
    return [
        line.strip()
        for line in _deploy_artifact(DOCKERFILE).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _user_directives(instructions: list[str]) -> list[str]:
    """Valores das diretivas ``USER`` do Dockerfile, na ordem do arquivo."""
    return [
        match.group(1)
        for line in instructions
        if (match := _USER_DIRECTIVE.match(line)) is not None
    ]


def test_dockerfile_runs_as_non_root() -> None:
    """R1/R3: a imagem cria o usuário dedicado 10001 e roda como ele.

    O BUILD segue como root (useradd, mídia, collectstatic) — a ordem exigida
    é que o ``collectstatic`` venha ANTES do ``USER`` (R3), e o processo de
    RUNTIME (gunicorn/one-shots) seja o não-root, sem re-elevação posterior.
    """
    instructions = _dockerfile_instructions()

    assert any("groupadd -g 10001 hmd" in line for line in instructions)
    assert any(
        "useradd -u 10001 -g hmd -M -s /usr/sbin/nologin hmd" in line for line in instructions
    )
    # /app/media nasce na imagem com o ownership do usuário: o volume nomeado
    # vazio (web e workers) o herda na primeira montagem (design D1).
    assert any("mkdir -p /app/media" in line for line in instructions)
    assert any("chown hmd:hmd /app/media" in line for line in instructions)

    # Única diretiva USER, com uid:gid fixos — nada de USER root/USER 0 no
    # arquivo nem re-elevação depois dele.
    assert _user_directives(instructions) == ["10001:10001"]
    assert not any("chown -R" in line for line in instructions)

    user_index = instructions.index("USER 10001:10001")
    expose_index = next(i for i, line in enumerate(instructions) if line.startswith("EXPOSE"))
    assert user_index < expose_index
    collectstatic_index = next(i for i, line in enumerate(instructions) if "collectstatic" in line)
    assert collectstatic_index < user_index


DEV_COMPOSE_FILE = "docker-compose.dev.yml"
DEV_IMAGE = "hmd-dev:app"


def _compose_service_blocks(relative_path: str) -> dict[str, list[str]]:
    """Serviços de um compose (nome → linhas do bloco), por indentação.

    Parsing leve no mesmo estilo dos demais checks estáticos (a suíte não usa
    parser de YAML): cada chave de serviço mora em exatamente 2 espaços.
    """
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in _deploy_artifact(relative_path).splitlines():
        match = re.match(r"^  (\S+):\s*$", line)
        if match is not None:
            current = match.group(1)
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
    return blocks


def test_dev_compose_runs_as_root_deliberately() -> None:
    """P1 do review do slice 002: o overlay de DEV volta a root EXPLICITAMENTE.

    O ``docker-compose.dev.yml`` builda o MESMO Dockerfile endurecido (uid
    10001), mas o fluxo de desenvolvimento (``uv sync --frozen`` escrevendo no
    ``/app/.venv`` do volume root-owned, cache do uv e workers gravando em
    media root-owned) quebra como não-root. Decisão do dono (opção A): todos
    os serviços que usam a imagem ``hmd-dev:app`` declaram ``user: "0:0"`` — o
    endurecimento non-root vale para a imagem de PRODUÇÃO (usuário 10001 do
    Dockerfile), não para o servidor de teste.
    """
    blocks = _compose_service_blocks(DEV_COMPOSE_FILE)
    image_services = {
        name: lines
        for name, lines in blocks.items()
        if any(f"image: {DEV_IMAGE}" in line for line in lines)
    }

    assert len(image_services) == 5, sorted(image_services)
    for name, lines in image_services.items():
        assert any(re.match(r'^\s+user: "0:0"\s*$', line) for line in lines), name


def test_dockerfile_nonroot_regression() -> None:
    """R2: regressões estáticas do hardening não-root.

    (a) o ``USER 10001:10001`` vem APÓS a última instrução de build que
    escreve em ``/app`` (deps, COPY do código, collectstatic) — quem escreve
    na imagem é o root do BUILD; (b) a mídia tem ownership do usuário; (c) a
    dívida do adiamento foi quitada no compose: o comentário stale sumiu e
    ``read_only``/``cap_drop ALL``/``no-new-privileges`` seguem ativos como
    defesa em profundidade.
    """
    # Guard espelhado (review 2, P2): o prod compose NUNCA pode sobrescrever
    # o usuário da imagem — `user:` no overlay dev é válido; no prod
    # reintroduziria root silenciosamente.
    assert not re.search(r"^\s+user:", _deploy_artifact("docker-compose.prod.yml"), re.M)

    instructions = _dockerfile_instructions()
    user_index = instructions.index("USER 10001:10001")

    last_build_write = max(
        i for i, line in enumerate(instructions) if _RUN_OR_COPY_DIRECTIVE.match(line)
    )
    assert user_index > last_build_write
    assert any("chown hmd:hmd /app/media" in line for line in instructions)

    compose = _deploy_artifact(COMPOSE_FILE)
    assert "non-root fica p/ o próximo release" not in compose
    # O comentário stale do wsgi (P2 do review do slice 001) também sai: o
    # default de produção agora mora no config/wsgi.py e o compose não
    # referencia mais settings de DEV.
    assert "config.settings.dev" not in compose
    # A env explícita do settings segue no web (P1) — nada funcional mudou.
    assert "DJANGO_SETTINGS_MODULE: config.settings.prod" in compose
    # Mitigações mantidas (defesa em profundidade, não substituídas).
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose and "- ALL" in compose
