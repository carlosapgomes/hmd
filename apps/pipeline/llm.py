"""Cliente OpenRouter (change llm-pipeline-per-type, slice 001, design D1).

``OpenRouterClient`` é um cliente OpenAI-compatível (SDK ``openai`` pinada)
apontando para ``OPENROUTER_BASE_URL`` (default ``https://openrouter.ai/api/v1``)
com ``OPENROUTER_API_KEY`` e ``timeout=LLM_TIMEOUT_SECONDS`` (default 120). O
método ``complete(model, messages, *, json_schema=None)`` devolve o conteúdo da
resposta; com ``json_schema`` (envelope ``{"name": ..., "schema": ...}``) o
envolve no formato strict do endpoint:

    {"type": "json_schema", "json_schema": {"name", "schema", "strict": true}}

(padrão ats-web). Erros da SDK são mapeados para ``LlmError(kind)`` com kind
estável — 401/403 → ``auth``; 429 → ``rate_limit``; ``APITimeoutError`` →
``timeout``; ``APIConnectionError`` → ``network``; resposta sem conteúdo →
``invalid_response``; demais → ``other``.

Injetabilidade (padrão ``KERBEROS_CLIENT_FACTORY`` do change 02): a factory
default ``LLM_CLIENT_FACTORY`` aponta ``create_openrouter_client``; a suíte
injeta fakes via ``override_settings`` e **nunca** toca a rede. O cliente é
seguro para reuso entre chamadas no mesmo processo (uma instância da SDK por
``OpenRouterClient``); cada chamada a ``get_llm_client()`` cria um cliente
novo. Sem chave configurada o cliente falha fechado com ``LlmError("auth")``.
A mensagem de erro é interna e genérica — nunca conteúdo de payload/PII.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol, cast, runtime_checkable

import openai
from django.conf import settings
from django.utils.module_loading import import_string
from openai import OpenAI

# Kinds estáveis de erro do pipeline LLM (R2/D1) — semântica do slice; a
# mensagem nunca carrega payload/PII.
LlmErrorKind = Literal[
    "auth",
    "rate_limit",
    "network",
    "timeout",
    "invalid_response",
    "other",
]


class LlmError(Exception):
    """Erro tipado do pipeline LLM com ``kind`` estável (R2).

    ``message`` é interna e genérica: descreve a classe do problema para
    diagnóstico/reporte — nunca conteúdo de resposta, payload ou PII.
    """

    kind: LlmErrorKind

    def __init__(self, kind: LlmErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@runtime_checkable
class LlmClient(Protocol):
    """Contrato do cliente LLM consumido pelo pipeline (D1).

    ``complete`` devolve o conteúdo da resposta e levanta ``LlmError`` com
    kind correto; ``json_schema`` (quando presente) é o envelope strict
    ``{"name": ..., "schema": ...}``.
    """

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        """Devolve o texto da resposta do modelo; levanta ``LlmError`` tipado."""
        ...


class OpenRouterClient:
    """Cliente OpenAI-compatível da OpenRouter (R3).

    Uma instância da SDK por cliente; o reuso do mesmo ``OpenRouterClient``
    entre chamadas é seguro (o transporte HTTP da SDK é stateless). Sem
    ``OPENROUTER_API_KEY`` a construção falha fechado com ``LlmError("auth")``.
    """

    def __init__(self) -> None:
        api_key: str = settings.OPENROUTER_API_KEY
        if not api_key:
            raise LlmError(
                "auth",
                "OPENROUTER_API_KEY não configurada (ambiente) — configure a "
                "chave da OpenRouter antes de usar o cliente.",
            )
        base_url: str = settings.OPENROUTER_BASE_URL
        timeout: float = settings.LLM_TIMEOUT_SECONDS
        self._sdk = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        request: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
        }
        if json_schema is not None:
            request["response_format"] = self._build_response_format(json_schema)
        try:
            response: Any = self._sdk.chat.completions.create(**request)
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise LlmError(
                "auth",
                "Falha de autenticação na OpenRouter (401/403) — confira a OPENROUTER_API_KEY.",
            ) from exc
        except openai.RateLimitError as exc:
            raise LlmError(
                "rate_limit",
                "Limite de requisições da OpenRouter atingido (429).",
            ) from exc
        except openai.APITimeoutError as exc:
            raise LlmError(
                "timeout",
                "Tempo limite da chamada à OpenRouter excedido.",
            ) from exc
        except openai.APIConnectionError as exc:
            raise LlmError(
                "network",
                "Falha de conexão com a OpenRouter.",
            ) from exc
        except openai.APIError as exc:
            raise LlmError(
                "other",
                f"Erro inesperado da SDK openai ({type(exc).__name__}).",
            ) from exc

        content: Any
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError):
            content = None
        if not isinstance(content, str) or not content.strip():
            raise LlmError(
                "invalid_response",
                "Resposta do modelo vazia ou sem conteúdo (texto ausente).",
            )
        return content

    @staticmethod
    def _build_response_format(json_schema: dict[str, Any]) -> dict[str, Any]:
        """Envolve o envelope strict do endpoint (padrão ats-web, R3).

        ``json_schema`` carrega ``name`` (nome do schema) e ``schema`` (JSON
        Schema já normalizado para o modo strict). O envelope enviado à SDK é
        ``{"type": "json_schema", "json_schema": {name, schema, strict}}``.
        """
        name = json_schema.get("name")
        schema = json_schema.get("schema")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(schema, dict)
            or not schema
        ):
            raise ValueError(
                "json_schema precisa de 'name' (str não vazio) e 'schema' (dict não vazio)."
            )
        return {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "schema": schema,
                "strict": True,
            },
        }


# Factory injetável: recebe zero argumentos e devolve um cliente pronto.
ClientFactory = Callable[[], LlmClient]


def create_openrouter_client() -> LlmClient:
    """Cria um ``OpenRouterClient`` a partir das envs (default da factory).

    Cliente novo por chamada da factory; para reuso seguro entre chamadas
    consecutivas, mantenha a instância retornada (D1).
    """
    return OpenRouterClient()


def get_llm_client() -> LlmClient:
    """Resolve a factory de ``settings.LLM_CLIENT_FACTORY`` e devolve o cliente.

    O default é o dotted-path do cliente real
    (``apps.pipeline.llm.create_openrouter_client``); testes injetam fakes
    (callable) via ``override_settings`` — zero rede na suíte (padrão
    ``KERBEROS_CLIENT_FACTORY`` do change 02).
    """
    configured: Any = settings.LLM_CLIENT_FACTORY
    if isinstance(configured, str):
        factory = cast(ClientFactory, import_string(configured))
    else:
        factory = cast(ClientFactory, configured)
    return factory()
