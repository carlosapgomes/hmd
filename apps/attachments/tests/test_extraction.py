"""Testes de extração híbrida (R1/R2) e das settings do slice (R5).

Cobre o contrato do slice **sem rede**: o OCR externo é sempre fake
(monkeypatch de ``apps.attachments.vision.transcribe_image`` na extração; a
SDK OpenAI fake na visão). PDFs de fixture são gerados pelo PyMuPDF em memória
(zero binários no repositório). Cenários: PDF com camada de texto → local (sem
chamada externa nem evento); imagem e PDF-imagem → vision com data-URL e
concatenação; teto de páginas → erro nomeado antes de qualquer envio; visão
sem ``VISION_MODEL``/chave → fail-closed sem instanciar a SDK; ``R5``: as
settings do slice existem com os defaults contratados.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
from django.conf import settings
from django.test import override_settings

from apps.accounts.models import User
from apps.attachments import vision as vision_module
from apps.attachments.extraction import (
    AttachmentPageLimitExceededError,
    extract_attachment_text,
)
from apps.attachments.models import CaseAttachment, ExtractionMethod
from apps.attachments.vision import transcribe_image
from apps.cases.models import Case
from apps.pipeline.llm import LlmError

# Conteúdo fake de imagens (nenhum parser lê o payload — só os bytes viajam).
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"conteudo-fake-jpeg-" * 4

# Páginas com camada de texto real (soma > 30 chars do limiar da R1).
_PDF_TEXT_PAGES = [
    "RELATÓRIO DE EXAME COMPLEMENTAR",
    "Paciente: Maria da Silva — ECG de esforço supervisionado",
    "Conclusão: sem alterações isquêmicas induzidas pelo exercício.",
]


class _FakeChatMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChatChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeChatMessage(content)


def _chat_response(content: str | None) -> Any:
    """Resposta de chat fake com o conteúdo desejado (shape da SDK)."""
    return SimpleNamespace(choices=[_FakeChatChoice(content)] if content is not None else [])


class FakeVisionOpenAI:
    """Substituto de ``openai.OpenAI`` no módulo de visão, sem rede.

    ``script`` é o roteiro de resultados de cada chamada de
    ``chat.completions.create`` (exceção a levantar ou resposta fake); as
    instâncias registram ``(api_key, base_url, timeout)`` e os kwargs da
    chamada (padrão ``FakeOpenAI`` dos testes do pipeline LLM).
    """

    instances: ClassVar[list[FakeVisionOpenAI]] = []
    script: ClassVar[list[Any]] = []

    def __init__(self, *, api_key: str, base_url: str, timeout: float) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.calls: list[dict[str, Any]] = []
        FakeVisionOpenAI.instances.append(self)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = FakeVisionOpenAI.script.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _clean_vision_fake() -> None:
    """Estado das fakes de classe é isolado entre testes."""
    FakeVisionOpenAI.instances = []
    FakeVisionOpenAI.script = []


# ── R1: extração local (PDF com texto; SEM chamada externa) ───────────────


@pytest.mark.django_db
def test_extract_pdf_text_local(
    monkeypatch: pytest.MonkeyPatch,
    owner_user: User,
    case_factory: Callable[[User], Case],
    pdf_bytes_factory: Callable[[list[str]], bytes],
    attachment_record_factory: Callable[..., CaseAttachment],
) -> None:
    """R1: PDF com camada de texto (> 30 chars) → (texto, local_pdf) via
    PyMuPDF — nenhuma chamada externa, nenhum dispatch."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=pdf_bytes_factory(_PDF_TEXT_PAGES),
        content_type="application/pdf",
        user=owner_user,
        name="relatorio-anexo.pdf",
    )
    external_calls: list[tuple[bytes, str]] = []

    def _fake_vision(image_bytes: bytes, content_type: str) -> str:
        external_calls.append((image_bytes, content_type))
        raise AssertionError("PDF com camada de texto NÃO pode ir ao OCR externo")

    monkeypatch.setattr("apps.attachments.vision.transcribe_image", _fake_vision)
    # Pré-check de configuração real faria fail-closed nos testes (sem env);
    # o fake cobre o comportamento de transcrição.
    monkeypatch.setattr("apps.attachments.vision.ensure_vision_ready", lambda: None)
    dispatched: list[str] = []

    text, method = extract_attachment_text(
        attachment, on_external_dispatch=lambda: dispatched.append("dispatch")
    )

    assert method == ExtractionMethod.LOCAL_PDF
    assert "Paciente: Maria da Silva" in text
    assert external_calls == []
    assert dispatched == []


@pytest.mark.django_db
def test_extract_pdf_short_text_goes_to_vision(
    monkeypatch: pytest.MonkeyPatch,
    owner_user: User,
    case_factory: Callable[[User], Case],
    pdf_bytes_factory: Callable[[list[str]], bytes],
    attachment_record_factory: Callable[..., CaseAttachment],
) -> None:
    """R1: PDF com camada de texto ≤ 30 chars (ruído/espelho) → vision."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=pdf_bytes_factory(["Assinatura digital", ""]),
        content_type="application/pdf",
        user=owner_user,
        name="ruido.pdf",
    )
    calls: list[tuple[bytes, str]] = []

    def _fake_vision(image_bytes: bytes, content_type: str) -> str:
        calls.append((image_bytes, content_type))
        return "texto da página"

    monkeypatch.setattr("apps.attachments.vision.transcribe_image", _fake_vision)
    # Pré-check de configuração real faria fail-closed nos testes (sem env);
    # o fake cobre o comportamento de transcrição.
    monkeypatch.setattr("apps.attachments.vision.ensure_vision_ready", lambda: None)

    text, method = extract_attachment_text(attachment)

    assert method == ExtractionMethod.VISION
    assert text == "texto da página\n\ntexto da página"
    assert len(calls) == 2
    assert {content_type for _, content_type in calls} == {"image/png"}


# ── R1: imagem e PDF-imagem via vision ────────────────────────────────────


@pytest.mark.django_db
def test_extract_image_via_vision(
    monkeypatch: pytest.MonkeyPatch,
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
) -> None:
    """R1: imagem vai DIRETO ao vision com os bytes originais + content-type."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="foto-exame.jpg",
    )
    calls: list[tuple[bytes, str]] = []

    def _fake_vision(image_bytes: bytes, content_type: str) -> str:
        calls.append((image_bytes, content_type))
        return "transcrição do laudo do exame"

    monkeypatch.setattr("apps.attachments.vision.transcribe_image", _fake_vision)
    # Pré-check de configuração real faria fail-closed nos testes (sem env);
    # o fake cobre o comportamento de transcrição.
    monkeypatch.setattr("apps.attachments.vision.ensure_vision_ready", lambda: None)
    dispatched: list[str] = []

    text, method = extract_attachment_text(
        attachment, on_external_dispatch=lambda: dispatched.append("dispatch")
    )

    assert method == ExtractionMethod.VISION
    assert text == "transcrição do laudo do exame"
    # Imagem não é rasterizada: os bytes originais e o MIME original viajam.
    assert calls == [(_JPEG_BYTES, "image/jpeg")]
    assert dispatched == ["dispatch"]


@pytest.mark.django_db
def test_extract_pdf_image_via_vision(
    monkeypatch: pytest.MonkeyPatch,
    owner_user: User,
    case_factory: Callable[[User], Case],
    pdf_bytes_factory: Callable[[list[str]], bytes],
    attachment_record_factory: Callable[..., CaseAttachment],
) -> None:
    """R1: PDF-imagem → páginas rasterizadas em PNG; transcrições concatadas
    com ``\\n\\n`` na ordem das páginas."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=pdf_bytes_factory(["", ""]),
        content_type="application/pdf",
        user=owner_user,
        name="scan-documento.pdf",
    )
    calls: list[tuple[bytes, str]] = []
    page_number = iter(range(1, 100))

    def _fake_vision(image_bytes: bytes, content_type: str) -> str:
        calls.append((image_bytes, content_type))
        return f"transcrição da página {next(page_number)}"

    monkeypatch.setattr("apps.attachments.vision.transcribe_image", _fake_vision)
    # Pré-check de configuração real faria fail-closed nos testes (sem env);
    # o fake cobre o comportamento de transcrição.
    monkeypatch.setattr("apps.attachments.vision.ensure_vision_ready", lambda: None)
    dispatched: list[str] = []

    text, method = extract_attachment_text(
        attachment, on_external_dispatch=lambda: dispatched.append("dispatch")
    )

    assert method == ExtractionMethod.VISION
    assert text == "transcrição da página 1\n\ntranscrição da página 2"
    assert len(calls) == 2
    # Páginas rasterizadas chegam como PNG (header mágico) por página.
    for image_bytes, content_type in calls:
        assert content_type == "image/png"
        assert image_bytes.startswith(b"\x89PNG")
    assert dispatched == ["dispatch"]


# ── R1: teto de páginas por anexo ─────────────────────────────────────────


@pytest.mark.django_db
def test_extract_page_cap_failed(
    monkeypatch: pytest.MonkeyPatch,
    owner_user: User,
    case_factory: Callable[[User], Case],
    pdf_bytes_factory: Callable[[list[str]], bytes],
    attachment_record_factory: Callable[..., CaseAttachment],
) -> None:
    """R1: PDF-imagem acima de ATTACHMENTS_VISION_MAX_PAGES → erro nomeado
    ANTES de qualquer envio externo (nem dispatch, nem chamada de vision)."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=pdf_bytes_factory([""] * 11),
        content_type="application/pdf",
        user=owner_user,
        name="scan-gigante.pdf",
    )
    calls: list[tuple[bytes, str]] = []

    def _never_vision(image_bytes: bytes, content_type: str) -> str:
        calls.append((image_bytes, content_type))
        return "inaceitável"

    monkeypatch.setattr("apps.attachments.vision.transcribe_image", _never_vision)
    monkeypatch.setattr("apps.attachments.vision.ensure_vision_ready", lambda: None)
    dispatched: list[str] = []

    with override_settings(ATTACHMENTS_VISION_MAX_PAGES=10):
        with pytest.raises(AttachmentPageLimitExceededError, match="10"):
            extract_attachment_text(
                attachment, on_external_dispatch=lambda: dispatched.append("dispatch")
            )

    assert calls == []
    assert dispatched == []


# ── R2: vision — fail-closed e shape da chamada ───────────────────────────


def test_vision_no_model_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2: sem ``VISION_MODEL`` → ``LlmError`` de configuração ANTES de
    qualquer envio (a SDK fake nem chega a ser instanciada)."""
    monkeypatch.setattr(vision_module, "OpenAI", FakeVisionOpenAI)

    with override_settings(VISION_MODEL="", OPENROUTER_API_KEY="chave-fake"):
        with pytest.raises(LlmError) as excinfo:
            transcribe_image(b"bytes-de-imagem", "image/png")

    assert excinfo.value.kind == "config"
    assert FakeVisionOpenAI.instances == []


def test_vision_missing_api_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2: modelo presente mas ``OPENROUTER_API_KEY`` vazia → ``auth``
    (padrão do ``OpenRouterClient``), sem instanciar a SDK."""
    monkeypatch.setattr(vision_module, "OpenAI", FakeVisionOpenAI)

    with override_settings(VISION_MODEL="modelo-vision", OPENROUTER_API_KEY=""):
        with pytest.raises(LlmError) as excinfo:
            transcribe_image(b"bytes-de-imagem", "image/png")

    assert excinfo.value.kind == "auth"
    assert FakeVisionOpenAI.instances == []


def test_vision_transcribes_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2: ``transcribe_image`` monta a mensagem multimodal com o data-URL do
    PNG (base64 do conteúdo + content-type) e devolve o texto da resposta."""
    monkeypatch.setattr(vision_module, "OpenAI", FakeVisionOpenAI)
    FakeVisionOpenAI.script = [
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="texto do exame"))]
        )
    ]
    content = b"\x89PNG\r\n\x1a\nconteudo-rasterizado"

    with override_settings(
        VISION_MODEL="modelo-vision-teste",
        OPENROUTER_API_KEY="chave-fake",
        OPENROUTER_BASE_URL="https://openrouter.example/api/v1",
        LLM_TIMEOUT_SECONDS=7,
    ):
        text = transcribe_image(content, "image/png")

    assert text == "texto do exame"
    instance = FakeVisionOpenAI.instances[0]
    assert instance.api_key == "chave-fake"
    assert instance.base_url == "https://openrouter.example/api/v1"
    assert instance.timeout == 7
    request = instance.calls[0]
    assert request["model"] == "modelo-vision-teste"
    messages = cast(Any, request["messages"])
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content_parts = messages[0]["content"]
    expected_url = f"data:image/png;base64,{base64.b64encode(content).decode('ascii')}"
    assert content_parts[1]["image_url"]["url"] == expected_url
    assert "text" in content_parts[0]["type"] and content_parts[0]["text"]  # texto instrutivo


def test_vision_empty_response_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2: resposta sem conteúdo → ``LlmError`` de resposta inválida."""
    monkeypatch.setattr(vision_module, "OpenAI", FakeVisionOpenAI)
    FakeVisionOpenAI.script = [SimpleNamespace(choices=[])]

    with override_settings(VISION_MODEL="modelo-vision", OPENROUTER_API_KEY="chave-fake"):
        with pytest.raises(LlmError) as excinfo:
            transcribe_image(b"bytes-de-imagem", "image/png")

    assert excinfo.value.kind == "invalid_response"


# ── R5: settings do slice ─────────────────────────────────────────────────


def test_slice_settings_defined() -> None:
    """R5: VISION_MODEL (env, default vazio), ATTACHMENTS_RUN_TASKS_INLINE
    (default true), ATTACHMENTS_VISION_MAX_PAGES (10) e o cluster attachments
    em ``Q_CLUSTER["ALT_CLUSTERS"]`` com a estrutura dos demais."""
    assert isinstance(settings.VISION_MODEL, str)
    assert isinstance(settings.ATTACHMENTS_RUN_TASKS_INLINE, bool)
    assert settings.ATTACHMENTS_VISION_MAX_PAGES == 10
    q_cluster: Any = settings.Q_CLUSTER
    alt_clusters: Any = q_cluster["ALT_CLUSTERS"]
    assert "attachments" in alt_clusters
    attachments_cluster: Any = alt_clusters["attachments"]
    for key in ("workers", "timeout", "retry"):
        assert key in attachments_cluster
