"""Cliente de visão (OCR externo) dos anexos — change attachment-processing-ocr.

``transcribe_image(image_bytes, content_type)`` chama ``settings.VISION_MODEL``
na OpenRouter (SDK OpenAI) com uma mensagem multimodal: texto instrutivo +
imagem como data-URL (``data:<content_type>;base64,…``); devolve a transcrição
(texto livre — SEM ``json_schema``). Reutiliza ``LlmError`` do pipeline
(``apps/pipeline/llm.py``) — NÃO estende o protocolo ``LlmClient`` (domínio
diferente, zero toque no contrato do change 06): mesma env/base/timeout
(``OPENROUTER_*``/``LLM_TIMEOUT_SECONDS``) e o MESMO mapeamento de erros da
SDK do ``OpenRouterClient`` (401/403 → ``auth``; 429 → ``rate_limit``;
timeout/conexão → ``timeout``/``network``; resposta vazia →
``invalid_response``; demais → ``other``).

Fail-closed de configuração ANTES de qualquer envio: ``VISION_MODEL`` ausente/
vazio → ``LlmError("config", …)`` e ``OPENROUTER_API_KEY`` ausente →
``LlmError("auth", …)``. A mensagem de erro é interna e genérica — nunca
conteúdo de payload/PII. O kind ``config`` foi adicionado à ``LlmErrorKind``
compartilhada (apps/pipeline/llm.py) para consumidores tipados do ``.kind``.
"""

from __future__ import annotations

import base64
from typing import Any

import openai
from django.conf import settings
from openai import OpenAI

from apps.pipeline.llm import LlmError

# Instrução da transcrição: fidelidade literal, sem resumo/interpretação.
_TRANSCRIPTION_INSTRUCTION = (
    "Transcreva fielmente todo o texto legível na imagem, preservando a "
    "estrutura em linhas e parágrafos. Não resuma, não interprete e não "
    "acrescente conteúdo que não esteja na imagem."
)


def ensure_vision_ready() -> None:
    """Pré-checa a configuração do OCR externo SEM instanciar a SDK (P2 review).

    Fail-closed adiantado: usado pelo caminho de extração ANTES de gravar o
    evento de auditoria ``DISPATCHED`` — se nada pode ser enviado (modelo ou
    chave ausentes), a trilha registra apenas ``FAILED``, sem evento fantasma
    de envio externo.

    Raises:
        LlmError: ``config`` sem ``VISION_MODEL``; ``auth`` sem chave.
    """
    if not settings.VISION_MODEL:
        raise LlmError(
            "config",
            "VISION_MODEL não configurada (ambiente) — defina o modelo de OCR "
            "antes de transcrever anexos.",
        )
    if not settings.OPENROUTER_API_KEY:
        raise LlmError(
            "auth",
            "OPENROUTER_API_KEY não configurada (ambiente) — configure a chave "
            "da OpenRouter antes de usar o OCR externo.",
        )


def transcribe_image(image_bytes: bytes, content_type: str) -> str:
    """Transcreve uma imagem via ``VISION_MODEL`` (data-URL multimodal).

    Fail-closed ANTES de qualquer envio: sem ``VISION_MODEL`` →
    ``LlmError("config", …)``; sem ``OPENROUTER_API_KEY`` →
    ``LlmError("auth", …)``. Erros da SDK mapeados para ``LlmError`` tipado
    (mesmo mapeamento do ``OpenRouterClient`` do pipeline).

    Raises:
        LlmError: configuração ausente (config/auth) ou falha da API
            (rate_limit/network/timeout/invalid_response/other).
    """
    ensure_vision_ready()
    api_key: str = settings.OPENROUTER_API_KEY
    sdk = OpenAI(
        api_key=api_key,
        base_url=settings.OPENROUTER_BASE_URL,
        timeout=settings.LLM_TIMEOUT_SECONDS,
    )

    encoded = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{content_type};base64,{encoded}"
    request: dict[str, Any] = {
        "model": settings.VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _TRANSCRIPTION_INSTRUCTION},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }
    try:
        response: Any = sdk.chat.completions.create(**request)
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
            "Resposta do modelo vazia ou sem conteúdo (transcrição ausente).",
        )
    return content
