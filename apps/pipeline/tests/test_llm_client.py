"""Testes do cliente OpenRouter e do comando ``llm_check`` (slice 001, R1–R6).

Cobre o contrato do slice **sem rede**:

- R2/R3: ``LlmError(kind)`` com os kinds estáveis; mapeamento de cada kind pelos
  erros REAIS da SDK openai 3.8.0 (instâncias reais de ``AuthenticationError``/
  ``PermissionDeniedError``/``RateLimitError``/``APITimeoutError``/
  ``APIConnectionError``/``APIStatusError`` com objetos de transporte stub —
  a SDK apenas armazena/lê o status, nenhuma chamada de rede); ``complete``
  devolve o conteúdo e envolve ``json_schema`` no formato strict do endpoint;
  ausência de ``OPENROUTER_API_KEY`` falha fechado com kind ``auth``;
- R4/R6: factory injetável via ``settings.LLM_CLIENT_FACTORY`` (fakes via
  ``override_settings``, padrão ``KERBEROS_CLIENT_FACTORY`` do change 02);
- R5: ``llm_check`` com fake ok (exit 0, modelos/latências reportados), com
  fake auth-fail (exit ≠ 0 e kind reportado) e com modelo não configurado
  (ausência reportada e exit ≠ 0).
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import openai
import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

import apps.pipeline.llm as llm_module
from apps.pipeline.llm import LlmClient, LlmError

MINIMAL_MESSAGES: Sequence[dict[str, str]] = [
    {"role": "user", "content": "Responda: ok"},
]

# Resposta de chat fake (shape ``choices[0].message.content`` da SDK).
OK_RESPONSE = SimpleNamespace(
    choices=[SimpleNamespace(message=SimpleNamespace(content="resposta-ok"))]
)


def _chat_response(content: str | None) -> Any:
    """Resposta de chat fake com o conteúdo desejado (``None`` simula vazio)."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeOpenAI:
    """Substituto de ``openai.OpenAI`` sem rede (padrão FakeKerbrosClient).

    ``script`` é o roteiro de resultados de cada chamada de
    ``chat.completions.create`` (exceção a levantar ou resposta fake).
    Instâncias registram ``(api_key, base_url, timeout)`` e os kwargs de cada
    chamada — o cliente real é exercitado ponta a ponta sobre o transporte
    fake.
    """

    instances: ClassVar[list[FakeOpenAI]] = []
    script: ClassVar[list[Any]] = []

    def __init__(self, *, api_key: str, base_url: str, timeout: float) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.calls: list[dict[str, Any]] = []
        FakeOpenAI.instances.append(self)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = FakeOpenAI.script.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _StubRequest:
    """Objeto mínimo aceito como ``request`` pelos erros de transporte da SDK."""


class _StubResponse:
    """Objeto mínimo aceito como ``response`` pelos erros de status da SDK."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.request = _StubRequest()
        self.headers: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _clean_fake_state() -> None:
    """Estado das fakes de classe é isolado entre testes."""
    FakeOpenAI.instances = []
    FakeOpenAI.script = []


def _patch_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Troca o ``OpenAI`` do módulo ``apps.pipeline.llm`` pelo fake."""
    monkeypatch.setattr(llm_module, "OpenAI", FakeOpenAI)


def _sdk_error(exc_type: Any, status_code: int) -> Exception:
    """Instância REAL de um erro da SDK openai (3.8.0) sem tocar a rede.

    Erros de status (``APIStatusError`` e subclasses: 401/403/429/5xx) recebem
    ``response``; os de transporte (conexão/timeout) recebem ``request``. Os
    objetos HTTP são stub mínimo — a SDK só armazena/lê status e headers; o que
    importa para o mapeamento é a CLASSE real da exceção levantada.
    """
    if exc_type is openai.APITimeoutError:
        return cast(Exception, exc_type(request=cast(Any, _StubRequest())))
    if issubclass(exc_type, openai.APIStatusError):
        return cast(
            Exception,
            exc_type(
                "erro fake da sdk", response=cast(Any, _StubResponse(status_code or 500)), body=None
            ),
        )
    return cast(Exception, exc_type(message="erro fake da sdk", request=cast(Any, _StubRequest())))


class TestLlmError:
    """R2: exceção tipada com ``kind``."""

    def test_exposes_kind_and_message(self) -> None:
        error = LlmError("timeout", "Tempo limite excedido.")

        assert error.kind == "timeout"
        assert "Tempo limite" in str(error)


class TestOpenRouterClientConfig:
    """R3/R4: cliente real usa as envs; ausência de chave falha fechado."""

    def test_default_factory_points_to_real_client(self) -> None:
        from django.conf import settings

        assert settings.LLM_CLIENT_FACTORY == "apps.pipeline.llm.create_openrouter_client"

    def test_construction_reads_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_openai(monkeypatch)
        with override_settings(
            OPENROUTER_API_KEY="chave-fake",
            OPENROUTER_BASE_URL="https://openrouter.example/api/v1",
            LLM_TIMEOUT_SECONDS=7,
        ):
            client = llm_module.OpenRouterClient()

        assert FakeOpenAI.instances[0].api_key == "chave-fake"
        assert FakeOpenAI.instances[0].base_url == "https://openrouter.example/api/v1"
        assert FakeOpenAI.instances[0].timeout == 7
        assert client is not None

    def test_missing_api_key_fails_closed(self) -> None:
        with override_settings(OPENROUTER_API_KEY=""):
            with pytest.raises(LlmError) as excinfo:
                llm_module.OpenRouterClient()

        assert excinfo.value.kind == "auth"

    def test_base_url_default_is_openrouter(self) -> None:
        from django.conf import settings

        assert settings.OPENROUTER_BASE_URL == "https://openrouter.ai/api/v1"


class TestComplete:
    """R3: ``complete`` devolve conteúdo e monta o formato strict."""

    def _client(self, monkeypatch: pytest.MonkeyPatch) -> llm_module.OpenRouterClient:
        _patch_openai(monkeypatch)
        with override_settings(OPENROUTER_API_KEY="chave-fake"):
            return llm_module.OpenRouterClient()

    def test_complete_returns_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        FakeOpenAI.script = [OK_RESPONSE]
        client = self._client(monkeypatch)

        content = client.complete("modelo-teste", MINIMAL_MESSAGES)

        assert content == "resposta-ok"
        request = FakeOpenAI.instances[0].calls[0]
        assert request["model"] == "modelo-teste"
        assert request["messages"] == list(MINIMAL_MESSAGES)
        assert "response_format" not in request

    def test_complete_wraps_json_schema_in_strict_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        FakeOpenAI.script = [OK_RESPONSE]
        client = self._client(monkeypatch)
        schema: dict[str, Any] = {"type": "object", "properties": {}}

        client.complete(
            "modelo-teste",
            MINIMAL_MESSAGES,
            json_schema={"name": "llm1_response", "schema": schema},
        )

        request = FakeOpenAI.instances[0].calls[0]
        assert request["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "llm1_response",
                "schema": schema,
                "strict": True,
            },
        }

    def test_malformed_json_schema_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        FakeOpenAI.script = [OK_RESPONSE]
        client = self._client(monkeypatch)

        with pytest.raises(ValueError):
            client.complete("modelo-teste", MINIMAL_MESSAGES, json_schema={"name": ""})

    def test_empty_content_raises_invalid_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        FakeOpenAI.script = [_chat_response(None)]
        client = self._client(monkeypatch)

        with pytest.raises(LlmError) as excinfo:
            client.complete("modelo-teste", MINIMAL_MESSAGES)

        assert excinfo.value.kind == "invalid_response"


class TestErrorKindsMapping:
    """R2/R3: cada kind mapeado pelo erro REAL da SDK levantado pela SDK fake."""

    @pytest.mark.parametrize(
        ("exc_factory", "expected_kind"),
        [
            pytest.param(
                lambda: _sdk_error(openai.AuthenticationError, 401),
                "auth",
                id="authentication_error_401",
            ),
            pytest.param(
                lambda: _sdk_error(openai.PermissionDeniedError, 403),
                "auth",
                id="permission_denied_403",
            ),
            pytest.param(
                lambda: _sdk_error(openai.RateLimitError, 429),
                "rate_limit",
                id="rate_limit_429",
            ),
            pytest.param(
                lambda: _sdk_error(openai.APITimeoutError, 0),
                "timeout",
                id="api_timeout_error",
            ),
            pytest.param(
                lambda: _sdk_error(openai.APIConnectionError, 0),
                "network",
                id="api_connection_error",
            ),
            pytest.param(
                lambda: _sdk_error(openai.APIStatusError, 500),
                "other",
                id="api_status_error_500",
            ),
        ],
    )
    def test_maps_each_sdk_error_kind(
        self,
        monkeypatch: pytest.MonkeyPatch,
        exc_factory: Any,
        expected_kind: str,
    ) -> None:
        FakeOpenAI.script = [exc_factory()]
        _patch_openai(monkeypatch)
        with override_settings(OPENROUTER_API_KEY="chave-fake"):
            client = llm_module.OpenRouterClient()

        with pytest.raises(LlmError) as excinfo:
            client.complete("modelo-teste", MINIMAL_MESSAGES)

        assert excinfo.value.kind == expected_kind


class FakeLlmClient(LlmClient):
    """Cliente fake da factory (R4/R6): registra chamadas e devolve resposta."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Sequence[dict[str, str]]]] = []
        self.error: LlmError | None = None

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append((model, messages))
        if self.error is not None:
            raise self.error
        return "ok"


class TestFactoryInjection:
    """R4/R6: factory injetável via settings — zero rede na suíte."""

    def test_get_llm_client_returns_fake_from_settings(self) -> None:
        fake = FakeLlmClient()
        with override_settings(LLM_CLIENT_FACTORY=lambda: fake):
            client = llm_module.get_llm_client()

        assert client is fake
        assert client.complete("modelo-teste", MINIMAL_MESSAGES) == "ok"


class TestLlmCheck:
    """R5/R6: comando manual reporta modelo/latência/kind e exit ≠ 0 na falha."""

    MODELS_CONF: dict[str, str] = {
        "LLM1_MODEL": "modelo-1",
        "LLM2_MODEL": "modelo-2",
        "OPENROUTER_API_KEY": "chave-fake",
    }

    def _call(
        self,
        fake: FakeLlmClient,
        stdout: io.StringIO,
        *,
        models_arg: str = "l1,l2",
        model_settings: dict[str, str] | None = None,
    ) -> None:
        settings_override: dict[str, Any] = {"LLM_CLIENT_FACTORY": lambda: fake}
        settings_override.update(model_settings or self.MODELS_CONF)
        with override_settings(**settings_override):
            call_command("llm_check", models=models_arg, stdout=stdout)

    def test_llm_check_ok_reports_models_and_latency(self) -> None:
        fake = FakeLlmClient()
        out = io.StringIO()

        self._call(fake, out)

        text = out.getvalue()
        assert "modelo-1" in text
        assert "modelo-2" in text
        assert "result=ok" in text
        assert "latency_ms=" in text
        assert [call[0] for call in fake.calls] == ["modelo-1", "modelo-2"]

    def test_llm_check_auth_fail_exits_nonzero_and_reports_kind(self) -> None:
        fake = FakeLlmClient()
        fake.error = LlmError("auth", "Falha de autenticação fake.")
        out = io.StringIO()

        with pytest.raises(CommandError):
            self._call(fake, out, models_arg="l1")

        text = out.getvalue()
        assert "modelo-1" in text
        assert "kind=auth" in text
        assert "latency_ms=" in text

    def test_llm_check_missing_model_reports_absence_and_exits_nonzero(self) -> None:
        fake = FakeLlmClient()
        out = io.StringIO()

        with pytest.raises(CommandError):
            self._call(
                fake,
                out,
                models_arg="l1,l2",
                model_settings={
                    "LLM1_MODEL": "",
                    "LLM2_MODEL": "",
                    "OPENROUTER_API_KEY": "chave-fake",
                },
            )

        text = out.getvalue()
        assert "modelo_nao_configurado" in text
        assert "LLM1_MODEL" in text
        assert "LLM2_MODEL" in text
        assert fake.calls == []

    def test_llm_check_unknown_alias_raises_command_error(self) -> None:
        out = io.StringIO()

        with pytest.raises(CommandError):
            self._call(FakeLlmClient(), out, models_arg="l3")

    def test_llm_check_single_model_selection(self) -> None:
        fake = FakeLlmClient()
        out = io.StringIO()

        self._call(fake, out, models_arg="l2")

        assert [call[0] for call in fake.calls] == ["modelo-2"]
