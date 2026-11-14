"""Guards fail-closed do harness de verificação (ui-parity-foundation slice 002, fix-1).

P0/P1 cobertos aqui (negativos em subprocesso, sem tocar banco algum):
- ``seed_verify`` recusa execucao sob settings dev e prod-like ANTES de
  qualquer ORM (a porta inalcançável prova que nem a conexao e tentada:
  sem ``OperationalError``/``Connection refused`` na saida);
- ``backends_verify`` recusa carregar sem identidade positiva de
  ``config.settings.verify``: ambiente vazio, settings dev e settings
  programaticas (``settings.configure()``) com banco ``hmd_verify_*``.

Os testes rodam o interpretador do ambiente (``sys.executable``) com
settings explicitas; nenhum deles abre conexao com banco de dev/prod.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

_PROGRAMMATIC_SETTINGS = (
    "from django.conf import settings\n"
    "settings.configure(\n"
    "    DATABASES={'default': {'ENGINE': 'django.db.backends.postgresql',"
    " 'NAME': 'hmd_verify_x', 'USER': 'h', 'PASSWORD': 'h',"
    " 'HOST': '127.0.0.1', 'PORT': '55435'}},\n"
    "    INSTALLED_APPS=['config.admin.HmdAdminConfig', 'django.contrib.auth',"
    " 'django.contrib.contenttypes', 'django.contrib.sessions',"
    " 'django.contrib.messages', 'django.contrib.staticfiles', 'django_q',"
    " 'apps.accounts', 'apps.cases', 'apps.intake', 'apps.doctor',"
    " 'apps.anonymization', 'apps.pipeline', 'apps.llm', 'apps.scheduler',"
    " 'apps.attachments', 'apps.dashboard'],\n"
    "    SECRET_KEY='x', USE_TZ=True,\n"
    ")\n"
    "import django\n"
    "django.setup()\n"
    "import apps.accounts.backends_verify\n"
    "print('IMPORTED')\n"
)


def _run_python(
    args: list[str],
    env_extra: dict[str, str] | None = None,
    remove: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Executa o interpretador do ambiente a partir da raiz do checkout."""
    env = dict(os.environ)
    for key in remove:
        env.pop(key, None)
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY_FILE"):
        env.pop(key, None)
    if env_extra:
        env.update(env_extra)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


class TestSeedVerifyGuards:
    """P0: o seed sintético nunca executa fora de config.settings.verify."""

    def test_seed_refused_under_dev_settings_before_any_orm(self) -> None:
        result = _run_python(
            ["manage.py", "seed_verify", "--settings=config.settings.dev"],
            {"DATABASE_URL": "postgres://hmd:hmd_dev@127.0.0.1:59999/hmd_dev"},
        )
        output = _output(result)
        assert result.returncode != 0
        assert "ImproperlyConfigured" in output
        assert "seed_verify só executa sob config.settings.verify" in output
        # Guard antes do ORM: nenhuma conexao foi tentada (sem OperationalError).
        assert "OperationalError" not in output
        assert "Connection refused" not in output

    def test_seed_refused_under_prod_like_settings_before_any_orm(self) -> None:
        result = _run_python(
            ["manage.py", "seed_verify", "--settings=config.settings.prod"],
            {
                "DJANGO_SECRET_KEY": "dummy-prod-like-secret",
                "DB_HOST": "127.0.0.1",
                "DB_PORT": "59999",
                "DB_NAME": "hmd",
                "DB_USER": "hmd",
                "DB_PASSWORD": "hmd_dev",
            },
        )
        output = _output(result)
        assert result.returncode != 0
        assert "ImproperlyConfigured" in output
        assert "seed_verify só executa sob config.settings.verify" in output
        assert "OperationalError" not in output
        assert "Connection refused" not in output


class TestBackendsVerifyGuards:
    """P1: o backend sintético exige identidade positiva de verify settings."""

    def test_backend_refused_with_empty_settings_module(self) -> None:
        result = _run_python(
            ["-c", "import apps.accounts.backends_verify; print('IMPORTED')"],
            remove=("DJANGO_SETTINGS_MODULE",),
        )
        output = _output(result)
        assert result.returncode != 0
        assert "ImproperlyConfigured" in output
        assert result.stdout.strip() != "IMPORTED"

    def test_backend_refused_under_dev_settings(self) -> None:
        result = _run_python(
            [
                "-c",
                "import django; django.setup();"
                " import apps.accounts.backends_verify; print('IMPORTED')",
            ],
            {"DJANGO_SETTINGS_MODULE": "config.settings.dev"},
        )
        output = _output(result)
        assert result.returncode != 0
        assert "ImproperlyConfigured" in output
        assert result.stdout.strip() != "IMPORTED"

    def test_backend_refused_programmatic_settings_with_verify_db_name(self) -> None:
        """settings.configure() + banco hmd_verify_* ainda recusa (fail-closed)."""
        result = _run_python(
            ["-c", _PROGRAMMATIC_SETTINGS],
            remove=("DJANGO_SETTINGS_MODULE",),
        )
        output = _output(result)
        assert result.returncode != 0
        assert "ImproperlyConfigured" in output
        assert result.stdout.strip() != "IMPORTED"
