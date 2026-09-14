"""Testes da task de processamento de PDF do worker (slice 003, design D2/D4/D5).

A task é chamada DIRETO (sem qcluster — o R6 e a flag inline dispensam worker):
PDFs de fixture são gerados pelo PyMuPDF em memória (zero binários no repo) e
armazenados via storage de teste in-memory. Cobre os cenários da spec
"Extração assíncrona no cluster pdf" (feliz → ANONYMIZING, corrompido →
FAILED, marca d'água não vaza) e do gate (fora do padrão retido), mais o
contrato R2: branch por estado (reenvio em PDF_EXTRACTING processa SEM start),
idempotência por estado (reexecução não duplica eventos) e respeito ao lock do
caso (``CaseLockConflictError`` propagada). Criação com ``INLINE=True`` já
chega processada (R3/R6).
"""

from __future__ import annotations

from collections.abc import Callable

import pymupdf
import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.locks import CaseLockConflictError, claim_case_lock
from apps.cases.models import Case, CaseDocument, CaseStatus
from apps.intake.services import create_case_with_documents

# Teste RED do slice: o módulo de tasks não existe ainda — a coleta falha com
# ModuleNotFoundError; GREEN o implementa (apps/intake/tasks.py).
from apps.intake.tasks import process_case_documents

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"

_WATERMARK_BAND = "33345 33345 33345 33345"
_RECORD_NUMBER = "33345"

_REPORT_HEADER = "RELATÓRIO DE OCORRÊNCIAS"
_INSTITUTIONAL_SIGNALS = [
    "Central Estadual de Regulação",
    "Secretaria da Saúde do Estado",
    "Governo do Estado da Bahia",
]
_SECTIONS = [
    f"Código: {_RECORD_NUMBER}",
    "Abertura: 01/02/2025",
    "Unid. Origem: Hospital Geral do Estado",
    "Motivo da Solicitação: cateterismo cardíaco diagnóstico",
    "Complemento da Solicitação: paciente encaminhado para avaliação hemodinâmica",
    "Resumo Clínico: paciente de 58 anos com dor torácica em investigação",
    "Dias em tela: 3",
    "Data Adm. Unid.: 01/02/2025",
]
_PADDING = "Linha de preenchimento para atingir o tamanho mínimo do relatório de regulação."
_OFF_PATTERN_LINES = [
    "Eletrocardiograma de repouso",
    "Paciente: João Maria, 58 anos",
    "Laudo fora do padrão do relatório de regulação para o teste do gate.",
    "Não há cabeçalho, sinais institucionais nem seções operacionais aqui.",
]
_CORRUPT_PDF = b"%PDF-1.4 conteudo invalido, nao e um pdf de verdade." * 6


# ── Helpers de fixture (PDFs reais gerados por PyMuPDF, storage in-memory) ─


def _pdf_bytes(lines: list[str]) -> bytes:
    """Gera um PDF real de 1 página com o texto dado (linha por linha)."""
    document = pymupdf.open()  # type: ignore[no-untyped-call]
    try:
        document.new_page().insert_text((72, 72), "\n".join(lines))
        data = document.tobytes()  # type: ignore[no-untyped-call]
        return bytes(data)
    finally:
        document.close()  # type: ignore[no-untyped-call]


def _standard_report_lines(*, with_watermark: bool = True) -> list[str]:
    """Linhas de um relatório SESAB padrão (> threshold de chars, com margem)."""
    lines = [_REPORT_HEADER, *_INSTITUTIONAL_SIGNALS, *_SECTIONS]
    if with_watermark:
        lines.append(_WATERMARK_BAND)
    while len("\n".join(lines)) < 560:
        lines.append(_PADDING)
    return lines


def _add_document(
    case: Case,
    *,
    content: bytes,
    position: int,
    uploaded_by: User,
) -> CaseDocument:
    """Grava um CaseDocument com conteúdo dado no storage in-memory dos testes."""
    document = CaseDocument(
        case=case,
        position=position,
        original_filename=f"relatorio-{position}.pdf",
        content_type="application/pdf",
        size_bytes=len(content),
        uploaded_by=uploaded_by,
    )
    document.file.save(
        f"case_documents/{case.case_id}/{position}.pdf", ContentFile(content), save=False
    )
    document.save()
    return document


def _create_case_with_pdf(user: User, content: bytes) -> Case:
    """Cria um caso NEW direto (sem o serviço — a task é chamada à mão)."""
    case = Case.objects.create(created_by=user)
    _add_document(case, content=content, position=1, uploaded_by=user)
    return case


def _event_types(case: Case) -> list[str]:
    return list(case.events.values_list("event_type", flat=True))


# ── Cenários da spec "Extração assíncrona no cluster pdf" ──────────────────


@pytest.mark.django_db
def test_happy_path_anonymizing(nir_user: User) -> None:
    """R2/R6/cenário feliz: relatório padrão → ANONYMIZING com texto, nº e eventos.

    O nº aparece como marca d'água repetida E como padrão (``Código:``): é
    extraído do TEXTO BRUTO (antes do strip) e a sequência não vaza para o
    ``extracted_text`` armazenado (cenário "Marca d'água não vaza").
    """
    case = _create_case_with_pdf(nir_user, _pdf_bytes(_standard_report_lines()))

    process_case_documents(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.ANONYMIZING
    assert case.agency_record_number == _RECORD_NUMBER
    assert case.agency_record_extracted_at is not None
    assert case.extracted_text
    assert "Motivo da Solicitação" in case.extracted_text
    assert _RECORD_NUMBER not in case.extracted_text
    assert case.manual_review_required is False
    assert case.manual_review_reason == ""

    # Eventos na ordem exigida: PDF_EXTRACTING (start) antes de EXTRACTION_COMPLETED.
    event_types = _event_types(case)
    assert event_types.index(CaseEventType.CASE_STATUS_PDF_EXTRACTING.value) < event_types.index(
        CaseEventType.CASE_EXTRACTION_COMPLETED.value
    )
    assert CaseEventType.CASE_STATUS_ANONYMIZING.value in event_types

    completion = case.events.get(event_type=CaseEventType.CASE_EXTRACTION_COMPLETED)
    assert completion.payload["record_number"] == _RECORD_NUMBER
    assert completion.payload["text_length"] == len(case.extracted_text)


@pytest.mark.django_db
def test_corrupt_pdf_failed(nir_user: User) -> None:
    """R2/cenário spec: PDF ilegível → FAILED com o motivo no payload do evento."""
    case = _create_case_with_pdf(nir_user, _CORRUPT_PDF)

    process_case_documents(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.FAILED
    failed_event = case.events.get(event_type=CaseEventType.CASE_STATUS_FAILED.value)
    assert failed_event.payload["target"] == CaseStatus.FAILED
    assert str(failed_event.payload["reason"]).strip() != ""


# ── Cenários da spec "Gate de regulação retém documento fora do padrão" ────


@pytest.mark.django_db
def test_gate_retains_out_of_pattern(nir_user: User) -> None:
    """R2/R6: documento fora do padrão fica retido em PDF_EXTRACTING com motivo."""
    case = _create_case_with_pdf(nir_user, _pdf_bytes(_OFF_PATTERN_LINES))

    process_case_documents(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.PDF_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason != ""
    assert case.events.filter(event_type=CaseEventType.CASE_GATE_MANUAL_REVIEW.value).exists()
    # Retenção nunca avança sozinho: nem ANONYMIZING, nem evento de conclusão.
    assert CaseEventType.CASE_STATUS_ANONYMIZING.value not in _event_types(case)
    assert not case.events.filter(event_type=CaseEventType.CASE_EXTRACTION_COMPLETED.value).exists()


@pytest.mark.django_db
def test_resubmitted_case_reprocesses_without_start(nir_user: User) -> None:
    """R2/R6: caso retido reenviado (PDF_EXTRACTING) → task roda SEM start e chega a ANONYMIZING.

    Simula a substituição de documentos do reenvio (slice 005): documentos
    novos no lugar dos antigos e campos de retenção/texto zerados — a task só
    vê o estado PDF_EXTRACTING e não repete ``start_pdf_extraction``.
    """
    retained = _create_case_with_pdf(nir_user, _pdf_bytes(_OFF_PATTERN_LINES))
    process_case_documents(retained.case_id)
    retained.refresh_from_db()
    assert retained.status == CaseStatus.PDF_EXTRACTING
    assert retained.manual_review_required is True
    starts_before = _event_types(retained).count(CaseEventType.CASE_STATUS_PDF_EXTRACTING.value)
    assert starts_before == 1

    # Reenvio: substitui documentos e zera flag/texto/nº (comportamento do 005).
    retained.documents.all().delete()
    _add_document(
        retained, content=_pdf_bytes(_standard_report_lines()), position=1, uploaded_by=nir_user
    )
    retained.manual_review_required = False
    retained.manual_review_reason = ""
    retained.extracted_text = ""
    retained.agency_record_number = ""
    retained.agency_record_extracted_at = None
    retained.save()

    process_case_documents(retained.case_id)
    retained.refresh_from_db()

    assert retained.status == CaseStatus.ANONYMIZING
    assert retained.manual_review_required is False
    assert retained.extracted_text
    # Nenhum start novo: o estado PDF_EXTRACTING pulou a transição (source NEW).
    event_types = _event_types(retained)
    assert event_types.count(CaseEventType.CASE_STATUS_PDF_EXTRACTING.value) == starts_before
    assert event_types.count(CaseEventType.CASE_STATUS_ANONYMIZING.value) == 1
    assert CaseEventType.CASE_EXTRACTION_COMPLETED.value in event_types


# ── Contratos R2/R3: idempotência, lock e inline ───────────────────────────


@pytest.mark.django_db
def test_idempotent_noop_on_already_processed(nir_user: User) -> None:
    """R2/R6: reexecução em caso já em ANONYMIZING → no-op sem novos eventos."""
    case = _create_case_with_pdf(nir_user, _pdf_bytes(_standard_report_lines()))
    process_case_documents(case.case_id)
    case.refresh_from_db()
    assert case.status == CaseStatus.ANONYMIZING
    events_before = case.events.count()

    process_case_documents(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.ANONYMIZING
    assert case.events.count() == events_before


@pytest.mark.django_db
def test_respects_lock(
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R2/R6: caso sob lock ativo de outro ator → CaseLockConflictError propagada.

    A tarefa não começa nada (sem eventos de extração) quando o claim conflita.
    """
    doctor = user_factory("doctor-tasks", DOCTOR_ROLE)
    case = _create_case_with_pdf(nir_user, _pdf_bytes(_standard_report_lines()))
    claim_case_lock(case, user=doctor, context="doctor_decision", role=DOCTOR_ROLE)

    with pytest.raises(CaseLockConflictError):
        process_case_documents(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.NEW
    assert CaseEventType.CASE_STATUS_PDF_EXTRACTING.value not in _event_types(case)


@pytest.mark.django_db
def test_inline_creation_processes(nir_user: User) -> None:
    """R3/R6: com INLINE=True a criação já enfileira inline e deixa processado."""
    content = _pdf_bytes(_standard_report_lines())
    uploaded = SimpleUploadedFile("relatorio.pdf", content, content_type="application/pdf")

    with override_settings(INTAKE_RUN_TASKS_INLINE=True):
        case = create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=uploaded,
            procedure_type="cat_cardiaco",
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.ANONYMIZING
    assert case.extracted_text
    assert case.documents.count() == 1
    assert case.events.filter(event_type=CaseEventType.CASE_STATUS_ANONYMIZING.value).exists()
