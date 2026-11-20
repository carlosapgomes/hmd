"""Adaptador LLM determinístico exclusivo de verify (slice 002, R5/D9).

O fake vive em ``config/llm_verify.py`` com factory
``create_verify_llm_client`` e override SOMENTE em
``config.settings.verify``: respostas válidas por estágio/perfil da chamada
(sem contador global, sem ORM, sem rede), ``LlmError`` com kind existente em
falha programada e entrada desconhecida, e recusa fail-closed fora da
identidade positiva de verify (inclusive settings programáticas com nome de
banco verify).

Positivos e guardas de subprocesso rodam com ``DJANGO_SETTINGS_MODULE``
explícito e porta de banco inalcançável quando nenhum acesso é esperado —
prova de zero ORM/rede por construção.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

ROOT = Path(__file__).resolve().parents[1]

_VERIFY_ENV = {
    "DJANGO_SETTINGS_MODULE": "config.settings.verify",
    "VERIFY_DB_HOST": "127.0.0.1",
    "VERIFY_DB_PORT": "59999",
    "VERIFY_DB_NAME": "hmd_verify_guard",
    "PYTHONPATH": str(ROOT),
}


def _run_python(
    script: str, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("OPENROUTER_API_KEY_FILE", None)
    env.update(_VERIFY_ENV)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


class TestVerifyAdapterImportGuards:
    """O módulo recusa carregar fora de config.settings.verify (D9)."""

    def test_import_refused_under_test_settings(self) -> None:
        with pytest.raises(ImproperlyConfigured):
            import config.llm_verify  # noqa: F401

    def test_import_refused_under_dev_settings(self) -> None:
        result = _run_python(
            "import django; django.setup(); import config.llm_verify; print('IMPORTED')",
            {"DJANGO_SETTINGS_MODULE": "config.settings.dev"},
        )
        assert result.returncode != 0
        assert "IMPORTED" not in result.stdout

    def test_import_refused_under_programmatic_settings_with_verify_db_name(self) -> None:
        script = (
            "from django.conf import settings\n"
            "settings.configure(\n"
            "    DATABASES={'default': {'ENGINE': 'django.db.backends.postgresql',"
            " 'NAME': 'hmd_verify_x', 'USER': 'h', 'PASSWORD': 'h',"
            " 'HOST': '127.0.0.1', 'PORT': '55435'}},\n"
            "    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth'],\n"
            "    SECRET_KEY='x', USE_TZ=True,\n"
            ")\n"
            "import django\n"
            "django.setup()\n"
            "import config.llm_verify\n"
            "print('IMPORTED')\n"
        )
        result = _run_python(script, {"DJANGO_SETTINGS_MODULE": ""})
        assert result.returncode != 0
        assert "IMPORTED" not in result.stdout

    def test_import_refused_under_verify_settings_with_prod_like_db_name(self) -> None:
        result = _run_python(
            "import django; django.setup(); import config.llm_verify; print('IMPORTED')",
            {"VERIFY_DB_NAME": "hmd_verify_prod"},
        )
        assert result.returncode != 0
        assert "IMPORTED" not in result.stdout

    def test_import_ok_under_verify_settings(self) -> None:
        result = _run_python(
            "import django; django.setup(); import config.llm_verify; print('IMPORTED')"
        )
        assert result.returncode == 0, _output(result)
        assert "IMPORTED" in result.stdout


class TestVerifyAdapterBehavior:
    """Respostas determinísticas por estágio/perfil; LlmError tipado; zero rede."""

    _DRIVER = (
        "import django; django.setup()\n"
        "import json, socket\n"
        "socket.socket = None\n"
        "socket.create_connection = None\n"
        "from config.llm_verify import create_verify_llm_client\n"
        "from apps.pipeline.llm import LlmError\n"
        "client = create_verify_llm_client()\n"
        "{body}\n"
    )

    def test_llm1_divergent_for_declared_art_perif(self) -> None:
        from apps.llm.models import PromptTemplate  # noqa: F401 (doc hint)
        from apps.llm.prompts_seed import user_prompt_name

        _ = user_prompt_name
        script = self._DRIVER.format(
            body=(
                "from apps.llm.prompts_seed import PROMPT_SEED_CONTENTS, user_prompt_name\n"
                "block = PROMPT_SEED_CONTENTS[user_prompt_name('art_perif', 'llm1')]\n"
                "messages = [{'role': 'user', 'content': block + '\\ntexto sintetico'}]\n"
                "schema = {'name': 'Llm1CaseArtifact', 'schema': {'type': 'object'}}\n"
                "first = client.complete('verify-llm1', messages, json_schema=schema)\n"
                "second = create_verify_llm_client().complete('verify-llm1', messages, json_schema=schema)\n"
                "assert first == second, 'resposta deve ser deterministica entre instancias'\n"
                "artifact = json.loads(first)\n"
                "assert artifact['pedido']['procedimentos_solicitados'] == ['cat_cardiaco']\n"
                "print('LLM1-DIVERGENT-OK')\n"
            )
        )
        result = _run_python(script)
        assert result.returncode == 0, _output(result)
        assert "LLM1-DIVERGENT-OK" in result.stdout

    def test_llm1_empty_detection_for_nefrostomia(self) -> None:
        script = self._DRIVER.format(
            body=(
                "from apps.llm.prompts_seed import PROMPT_SEED_CONTENTS, user_prompt_name\n"
                "block = PROMPT_SEED_CONTENTS[user_prompt_name('nefrostomia', 'llm1')]\n"
                "messages = [{'role': 'user', 'content': block + '\\ntexto sintetico'}]\n"
                "schema = {'name': 'Llm1CaseArtifact', 'schema': {'type': 'object'}}\n"
                "artifact = json.loads(client.complete('verify-llm1', messages, json_schema=schema))\n"
                "assert artifact['pedido']['procedimentos_solicitados'] == []\n"
                "print('LLM1-EMPTY-OK')\n"
            )
        )
        result = _run_python(script)
        assert result.returncode == 0, _output(result)
        assert "LLM1-EMPTY-OK" in result.stdout

    def test_llm1_unknown_profile_raises_llm_error(self) -> None:
        script = self._DRIVER.format(
            body=(
                "from apps.pipeline.llm import LlmError\n"
                "messages = [{'role': 'user', 'content': 'relatorio sintetico sem cenario reconhecido'}]\n"
                "schema = {'name': 'Llm1CaseArtifact', 'schema': {'type': 'object'}}\n"
                "try:\n"
                "    client.complete('verify-llm1', messages, json_schema=schema)\n"
                "except LlmError as exc:\n"
                "    assert exc.kind in ('auth', 'rate_limit', 'network', 'timeout', 'invalid_response', 'other', 'config')\n"
                "    print('LLM1-UNKNOWN-LLMERROR-OK')\n"
                "else:\n"
                "    raise AssertionError('entrada desconhecida deveria levantar LlmError')\n"
            )
        )
        result = _run_python(script)
        assert result.returncode == 0, _output(result)
        assert "LLM1-UNKNOWN-LLMERROR-OK" in result.stdout

    def test_llm2_failure_scenario_raises_llm_error(self) -> None:
        script = self._DRIVER.format(
            body=(
                "from apps.llm.prompts_seed import PROMPT_SEED_CONTENTS, user_prompt_name\n"
                "from apps.pipeline.llm import LlmError\n"
                "block = PROMPT_SEED_CONTENTS[user_prompt_name('art_cerebral', 'llm2')]\n"
                "messages = [{'role': 'user', 'content': block + '\\ntexto sintetico'}]\n"
                "schema = {'name': 'Llm2Response', 'schema': {'type': 'object'}}\n"
                "try:\n"
                "    client.complete('verify-llm2', messages, json_schema=schema)\n"
                "except LlmError as exc:\n"
                "    assert exc.kind in ('auth', 'rate_limit', 'network', 'timeout', 'invalid_response', 'other', 'config')\n"
                "    print('LLM2-FAILURE-LLMERROR-OK')\n"
                "else:\n"
                "    raise AssertionError('cenario de falha deveria levantar LlmError')\n"
            )
        )
        result = _run_python(script)
        assert result.returncode == 0, _output(result)
        assert "LLM2-FAILURE-LLMERROR-OK" in result.stdout

    def test_llm2_valid_for_confirmed_profile(self) -> None:
        script = self._DRIVER.format(
            body=(
                "from apps.llm.prompts_seed import PROMPT_SEED_CONTENTS, user_prompt_name\n"
                "block = PROMPT_SEED_CONTENTS[user_prompt_name('angio_art_perif', 'llm2')]\n"
                "messages = [{'role': 'user', 'content': block + '\\ntexto sintetico'}]\n"
                "schema = {'name': 'Llm2Response', 'schema': {'type': 'object'}}\n"
                "first = client.complete('verify-llm2', messages, json_schema=schema)\n"
                "second = create_verify_llm_client().complete('verify-llm2', messages, json_schema=schema)\n"
                "assert first == second\n"
                "response = json.loads(first)\n"
                "assert response['summary_text'].strip()\n"
                "assert [item['procedure_type'] for item in response['procedures']] == ['angio_art_perif']\n"
                "print('LLM2-VALID-OK')\n"
            )
        )
        result = _run_python(script)
        assert result.returncode == 0, _output(result)
        assert "LLM2-VALID-OK" in result.stdout

    def test_complete_refused_under_dev_settings_in_subprocess(self) -> None:
        result = _run_python(
            "import django; django.setup(); import config.llm_verify; print('IMPORTED')",
            {"DJANGO_SETTINGS_MODULE": "config.settings.dev"},
        )
        assert result.returncode != 0
        assert "IMPORTED" not in result.stdout
