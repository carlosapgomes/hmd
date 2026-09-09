"""Testes do worker ``process_case_attachments`` (R3/R5) e das settings (R5).

A task é chamada DIRETO (sem qcluster — a flag inline e o contrato do worker
dispensam broker): anexos de fixture com PDFs reais (PyMuPDF) ou bytes de
imagem fake; OCR externo sempre fake (monkeypatch de
``apps.attachments.vision.transcribe_image``) — suíte sem rede. Desde o slice
003 o pipeline por anexo é completo (extração → anonimização → verificação), e
a verificação LLM usa cliente fake via ``LLM_CLIENT_FACTORY`` + seed dos
prompts + analyzer fake da anonimização (sem modelo spaCy). Cobre:
processamento de anexo PDF-local e imagem→vision com evento de auditoria
ANTES do envio; PDF-imagem rasterizado; teto de páginas → failed; falha de
vision → failed + evento SEM afetar caso nem outros anexos; VISION_MODEL vazio
→ fail-closed; idempotência por etapa (retry não re-envia OCR nem duplica
evento; texto já extraído pula direto para anonimização/verificação); anexo
removido (antes e durante) → no-op silencioso sem evento; caso sem anexos →
retorno imediato.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.accounts.models import User
from apps.anonymization.engine import AnonymizationEngine
from apps.attachments.models import (
    AttachmentStatus,
    CaseAttachment,
    ExtractionMethod,
    PatientMatch,
)
from apps.attachments.tasks import process_case_attachments
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseEvent
from apps.pipeline.llm import LlmError

# Conteúdo fake de imagens (nenhum parser lê o payload — só os bytes viajam).
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"conteudo-fake-jpeg-" * 4

_PDF_TEXT_PAGES = [
    "RELATÓRIO DE EXAME COMPLEMENTAR",
    "Paciente: Maria da Silva — ECG de esforço supervisionado",
    "Conclusão: sem alterações isquêmicas induzidas pelo exercício.",
]

FAKE_MODEL = "modelo-tasks-anexo-teste"

DISPATCHED = CaseEventType.CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED.value
PROCESSED = CaseEventType.CASE_ATTACHMENT_PROCESSED.value
FAILED = CaseEventType.CASE_ATTACHMENT_FAILED.value


class _EmptyAnalyzer:
    """Analyzer fake sem detecções (determinístico decide o que tokenizar)."""

    def analyze(
        self,
        *,
        text: str,
        language: str,
        score_threshold: float,
    ) -> list[Any]:
        del text, language, score_threshold
        return []


class _MatchLlmClient:
    """Cliente fake da verificação: responde match e registra as chamadas."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, str]], dict[str, Any] | None]] = []

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append((model, list(messages), json_schema))
        return json.dumps(
            {
                "patient_match": PatientMatch.MATCH,
                "summary": "Documento clínico do paciente do caso.",
                "evidence": "Identificação consistente com o token informado.",
            },
            ensure_ascii=False,
        )


@pytest.fixture(autouse=True)
def _seeded_prompts() -> None:
    """Seed idempotente dos 29 prompts (a verificação exige o ATTACHMENT_VERIFICATION)."""
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture(autouse=True)
def _stub_anonymization_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Engine fake na anonimização dos anexos (sem modelo spaCy na suíte)."""
    fake = AnonymizationEngine(analyzer=_EmptyAnalyzer(), anonymizer=None)
    monkeypatch.setattr("apps.anonymization.services.get_anonymization_engine", lambda: fake)


def _install_vision_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    delete_attachment_pk: int | None = None,
    raise_error: Exception | None = None,
) -> Callable[[], list[tuple[bytes, str]]]:
    """Fake de ``vision.transcribe_image`` com registros e asserts de ordem.

    No momento de cada chamada externa o evento de auditoria
    ``CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED`` JÁ está persistido (R3: o
    evento antecede qualquer envio). Retorna um accessor das chamadas.
    """
    calls: list[tuple[bytes, str]] = []

    def _transcribe(image_bytes: bytes, content_type: str) -> str:
        if delete_attachment_pk is not None:
            CaseAttachment.objects.filter(pk=delete_attachment_pk).delete()
        if raise_error is not None:
            raise raise_error
        calls.append((image_bytes, content_type))
        return f"transcrição fake ({len(calls)})"

    monkeypatch.setattr("apps.attachments.vision.transcribe_image", _transcribe)
    monkeypatch.setattr("apps.attachments.vision.ensure_vision_ready", lambda: None)
    return lambda: calls


def _run_pipeline(case: Case, client: _MatchLlmClient | None = None) -> _MatchLlmClient:
    """Executa a task com o cliente fake da verificação injetado."""
    fake = client or _MatchLlmClient()
    with override_settings(LLM_CLIENT_FACTORY=lambda: fake, LLM1_MODEL=FAKE_MODEL):
        process_case_attachments(case.case_id)
    return fake


def _events_of_type(case: Case, event_type: str) -> list[CaseEvent]:
    return list(case.events.filter(event_type=event_type).order_by("id"))


# ── R3/R5: processamento feliz (pipeline completo) ─────────────────────────


@pytest.mark.django_db
def test_task_processes_pending_pdf_local(
    owner_user: User,
    case_factory: Callable[[User], Case],
    pdf_bytes_factory: Callable[[list[str]], bytes],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3/R5: anexo pendente PDF com texto → extração local + anonimização +
    verificação; status final ``processed`` com texto/método/artefatos; SEM
    evento externo (nem envio ao OCR)."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=pdf_bytes_factory(_PDF_TEXT_PAGES),
        content_type="application/pdf",
        user=owner_user,
        name="relatorio-anexo.pdf",
    )
    vision_calls: list[tuple[bytes, str]] = []

    def _fake_vision(image_bytes: bytes, content_type: str) -> str:
        vision_calls.append((image_bytes, content_type))
        raise AssertionError("PDF com camada de texto NÃO pode ir ao OCR externo")

    monkeypatch.setattr("apps.attachments.vision.transcribe_image", _fake_vision)
    monkeypatch.setattr("apps.attachments.vision.ensure_vision_ready", lambda: None)

    _run_pipeline(case)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.PROCESSED
    assert attachment.extraction_method == ExtractionMethod.LOCAL_PDF
    assert attachment.extracted_text
    assert "Paciente: Maria da Silva" in attachment.extracted_text
    assert attachment.anonymized_text
    assert vision_calls == []
    assert _events_of_type(case, DISPATCHED) == []
    assert _events_of_type(case, FAILED) == []
    assert len(_events_of_type(case, PROCESSED)) == 1


@pytest.mark.django_db
def test_task_image_vision_dispatch_event_before_send(
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3/R5: imagem → vision; o evento de auditoria (actor system, payload
    filename+método) é gravado ANTES do envio externo; o pipeline completo
    fecha como ``processed``."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="foto-exame.jpg",
    )

    def _transcribe(image_bytes: bytes, content_type: str) -> str:
        # Ordem exigida: quando o OCR externo roda, o evento JÁ está na trilha.
        assert _events_of_type(case, DISPATCHED) != []
        return "exame de imagem: sem alterações"

    monkeypatch.setattr("apps.attachments.vision.transcribe_image", _transcribe)
    monkeypatch.setattr("apps.attachments.vision.ensure_vision_ready", lambda: None)

    _run_pipeline(case)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.PROCESSED
    assert attachment.extraction_method == ExtractionMethod.VISION
    assert attachment.extracted_text == "exame de imagem: sem alterações"
    assert attachment.anonymized_text

    dispatched = _events_of_type(case, DISPATCHED)
    assert len(dispatched) == 1
    assert dispatched[0].actor_type == "system"
    assert dispatched[0].actor is None
    assert dispatched[0].actor_role == "system"
    assert dispatched[0].payload["filename"] == "foto-exame.jpg"
    assert dispatched[0].payload["method"] == ExtractionMethod.VISION
    assert len(_events_of_type(case, PROCESSED)) == 1


@pytest.mark.django_db
def test_task_pdf_image_rasterizes_and_dispatches_once(
    owner_user: User,
    case_factory: Callable[[User], Case],
    pdf_bytes_factory: Callable[[list[str]], bytes],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3/R5: PDF-imagem → páginas rasterizadas em PNG; transcrições concatadas
    com ``\\n\\n`` e UM ÚNICO evento de auditoria (antes da primeira página)."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=pdf_bytes_factory(["", ""]),
        content_type="application/pdf",
        user=owner_user,
        name="scan-documento.pdf",
    )
    vision_calls = _install_vision_fake(monkeypatch)

    _run_pipeline(case)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.PROCESSED
    assert attachment.extraction_method == ExtractionMethod.VISION
    assert attachment.extracted_text == "transcrição fake (1)\n\ntranscrição fake (2)"
    calls = vision_calls()
    assert len(calls) == 2
    for image_bytes, content_type in calls:
        assert content_type == "image/png"
        assert image_bytes.startswith(b"\x89PNG")
    assert len(_events_of_type(case, DISPATCHED)) == 1
    assert len(_events_of_type(case, PROCESSED)) == 1


@pytest.mark.django_db
def test_task_no_attachments_returns_immediately(
    owner_user: User,
    case_factory: Callable[[User], Case],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3: caso sem anexos → a task retorna sem erro e sem eventos."""
    case = case_factory(owner_user)
    vision_calls = _install_vision_fake(monkeypatch)

    _run_pipeline(case)

    assert vision_calls() == []
    assert case.events.count() == 0


# ── R3: falha por anexo (fail-closed, caso e demais anexos intactos) ──────


@pytest.mark.django_db
def test_task_vision_failure_marks_failed_other_attachments_intact(
    owner_user: User,
    case_factory: Callable[[User], Case],
    pdf_bytes_factory: Callable[[list[str]], bytes],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3/R5: falha de vision num anexo → ``failed`` + evento de falha; o caso e
    os demais anexos seguem o pipeline completo até ``processed`` (fail-closed
    por anexo)."""
    case = case_factory(owner_user)
    failing = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="foto-ruim.jpg",
    )
    healthy = attachment_record_factory(
        case,
        content=pdf_bytes_factory(_PDF_TEXT_PAGES),
        content_type="application/pdf",
        user=owner_user,
        name="relatorio-anexo.pdf",
    )
    _install_vision_fake(
        monkeypatch, raise_error=LlmError("network", "Falha de conexão com a OpenRouter.")
    )

    _run_pipeline(case)

    failing.refresh_from_db()
    assert failing.status == AttachmentStatus.FAILED
    assert failing.extracted_text == ""
    assert "Falha de conexão" in failing.failed_reason
    healthy.refresh_from_db()
    assert healthy.status == AttachmentStatus.PROCESSED
    assert healthy.extraction_method == ExtractionMethod.LOCAL_PDF
    assert healthy.extracted_text
    assert healthy.anonymized_text

    failed_events = _events_of_type(case, FAILED)
    assert len(failed_events) == 1
    assert failed_events[0].payload["filename"] == "foto-ruim.jpg"
    assert "Falha de conexão" in str(failed_events[0].payload["reason"])
    # O caso segue íntegro (sem transição/evento de estado do caso).
    assert len(_events_of_type(case, DISPATCHED)) == 1  # auditoria do envio tentado
    assert len(_events_of_type(case, PROCESSED)) == 1


@pytest.mark.django_db
def test_task_page_cap_marks_failed_with_clear_reason(
    owner_user: User,
    case_factory: Callable[[User], Case],
    pdf_bytes_factory: Callable[[list[str]], bytes],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3: PDF-imagem acima do teto → ``failed`` com motivo claro; sem envio
    externo (nem evento de auditoria, nem chamada de vision)."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=pdf_bytes_factory([""] * 11),
        content_type="application/pdf",
        user=owner_user,
        name="scan-gigante.pdf",
    )
    vision_calls = _install_vision_fake(monkeypatch)

    with override_settings(ATTACHMENTS_VISION_MAX_PAGES=10):
        process_case_attachments(case.case_id)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.FAILED
    assert "10" in attachment.failed_reason
    assert "página" in attachment.failed_reason
    assert vision_calls() == []
    assert _events_of_type(case, DISPATCHED) == []
    assert len(_events_of_type(case, FAILED)) == 1
    assert _events_of_type(case, PROCESSED) == []


@pytest.mark.django_db
def test_task_vision_no_model_fails_closed(
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
) -> None:
    """R3/R2: ``VISION_MODEL`` vazio com vision real (sem fake) → anexo
    ``failed`` com motivo de configuração; o fail-closed da visão garante que
    nada foi enviado (coberto na unidade — SDK fake conta instâncias)."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="foto.jpg",
    )

    with override_settings(VISION_MODEL="", OPENROUTER_API_KEY="chave-fake"):
        process_case_attachments(case.case_id)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.FAILED
    assert attachment.extracted_text == ""
    assert "VISION_MODEL" in attachment.failed_reason
    assert len(_events_of_type(case, FAILED)) == 1
    # P2 review: sem envio possível → sem evento fantasma de auditoria na
    # trilha (a pré-checagem de configuração roda ANTES do DISPATCHED).
    assert _events_of_type(case, DISPATCHED) == []


# ── R3/R5: idempotência por etapa e corrida com a ciência do NIR ──────────


@pytest.mark.django_db
def test_task_retry_skips_extraction(
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3/R5: reexecução com ``extracted_text``/``extraction_method`` já
    persistidos → SEM nova chamada de vision nem evento de auditoria (retry do
    q2 nunca re-envia ao OCR externo); o pipeline retoma da anonimização e
    fecha ``processed``."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="foto.jpg",
        status=AttachmentStatus.PROCESSING,
        extracted_text="texto já extraído na tentativa anterior",
        extraction_method=ExtractionMethod.VISION,
    )
    vision_calls = _install_vision_fake(monkeypatch)

    _run_pipeline(case)

    attachment.refresh_from_db()
    assert vision_calls() == []
    assert attachment.extracted_text == "texto já extraído na tentativa anterior"
    assert attachment.anonymized_text == "texto já extraído na tentativa anterior"
    assert attachment.status == AttachmentStatus.PROCESSED
    assert _events_of_type(case, DISPATCHED) == []
    assert _events_of_type(case, FAILED) == []
    assert len(_events_of_type(case, PROCESSED)) == 1


@pytest.mark.django_db
def test_task_attachment_deleted_noop(
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3: anexo removido antes da task → no-op silencioso, sem evento nem
    exceção (corrida com a ciência do NIR)."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="foto-removida.jpg",
    )
    attachment.delete()
    vision_calls = _install_vision_fake(monkeypatch)

    _run_pipeline(case)

    assert vision_calls() == []
    assert case.events.count() == 0


@pytest.mark.django_db
def test_task_sibling_deleted_mid_run_is_silent_noop(
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3/R5: anexo removido DURANTE o processamento (ciência do NIR) → o passo
    do anexo re-lê a row e vira no-op silencioso — sem ``failed`` nem evento
    para o removido; o anexo em processamento segue o pipeline até ``processed``."""
    case = case_factory(owner_user)
    # O "deleter" é processado primeiro (ordem created_at/pk) e remove o irmão.
    deleting = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="a-processado.jpg",
    )
    sibling = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="b-removido.jpg",
    )
    vision_calls = _install_vision_fake(monkeypatch, delete_attachment_pk=sibling.pk)

    _run_pipeline(case)

    deleting.refresh_from_db()
    assert deleting.status == AttachmentStatus.PROCESSED
    assert deleting.extraction_method == ExtractionMethod.VISION
    assert deleting.extracted_text
    assert deleting.anonymized_text
    assert not CaseAttachment.objects.filter(pk=sibling.pk).exists()
    # Nenhum evento de falha para o anexo removido no meio.
    assert _events_of_type(case, FAILED) == []
    assert len(_events_of_type(case, DISPATCHED)) == 1
    assert len(_events_of_type(case, PROCESSED)) == 1
    assert vision_calls() == [(_JPEG_BYTES, "image/jpeg")]


@pytest.mark.django_db
def test_task_self_deleted_mid_extraction_is_silent_noop(
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3 (P2 review): o PRÓPRIO anexo é removido durante sua transcrição
    (ciência do NIR no meio do OCR) → a re-leitura pós-extração vira no-op
    silencioso: nada é persistido, sem ``failed`` nem evento de falha. O
    ``DISPATCHED`` permanece legítimo (o envio externo de fato aconteceu
    antes da remoção)."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="self-removido.jpg",
    )
    _install_vision_fake(monkeypatch, delete_attachment_pk=attachment.pk)

    _run_pipeline(case)

    assert not CaseAttachment.objects.filter(pk=attachment.pk).exists()
    assert _events_of_type(case, FAILED) == []
    assert len(_events_of_type(case, DISPATCHED)) == 1
    assert _events_of_type(case, PROCESSED) == []
