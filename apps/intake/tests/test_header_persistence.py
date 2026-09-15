"""Testes da persistência dos metadados do cabeçalho SESAB no caso (change
sesab-header-extraction, slice 001, R3/R4/D4/D6).

Cobre o worker pdf (``process_case_documents``): os 4 metadados são extraídos
do texto e gravados no MESMO write do ``extracted_text`` (nenhum evento
carrega os valores); cabeçalho ausente não é erro (campos vazios). Cobre
também o reenvio de documentos (``resubmit_case_documents``): os 4 metadados
são zerados na MESMA transação do reenvio — novo PDF que falhe na extração
não deixa metadados órfãos do documento anterior.

PDFs de fixture são gerados pelo PyMuPDF dentro dos testes (zero binários no
repositório); storage in-memory (conftest do app).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator

import pymupdf
import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.accounts.models import User
from apps.cases.models import Case, CaseDocument, CaseStatus
from apps.intake.services import create_case_with_documents, resubmit_case_documents
from apps.intake.tasks import process_case_documents

NIR_ROLE = "nir"
SYSTEM_ROLE = "system"
RETENTION_REASON = "documento fora do padrão SESAB do relatório (motivo: missing_header)"

_REPORT_HEADER = "RELATÓRIO DE OCORRÊNCIAS"
_INSTITUTIONAL_SIGNAL = "Central Estadual de Regulação"
_DEMOGRAPHICS_LINE = "FULANO DE TAL SILVA - Idade: 79a. - Sexo: F - Raça/Cor: Parda"
_SECTIONS = [
    "Código: 33345",
    "Abertura: 01/02/2026",
    "Unid. Origem: Hospital Geral do Estado",
    "Motivo da Solicitação: cateterismo cardíaco diagnóstico",
    "Complemento da Solicitação: paciente encaminhado para avaliação hemodinâmica",
    "Resumo Clínico: paciente com dor torácica em investigação",
    "Data Adm. Unid.: 01/02/2026",
]
_PADDING = "Linha de preenchimento para atingir o tamanho mínimo do relatório de regulação."
_CORRUPT_PDF = b"%PDF-1.4 conteudo invalido, nao e um pdf de verdade." * 6


@pytest.fixture(autouse=True)
def _no_inline_processing() -> Iterator[None]:
    """Módulo sem processamento automático: o teste decide quando processar."""
    with override_settings(
        INTAKE_RUN_TASKS_INLINE=False,
        ANONYMIZATION_RUN_TASKS_INLINE=False,
    ):
        yield


def _pdf_bytes(pages: list[list[str]]) -> bytes:
    """Gera um PDF real com uma página por lista de linhas (PyMuPDF)."""
    document = pymupdf.open()  # type: ignore[no-untyped-call]
    try:
        for lines in pages:
            document.new_page().insert_text((72, 72), "\n".join(lines))
        data = document.tobytes()  # type: ignore[no-untyped-call]
        return bytes(data)
    finally:
        document.close()  # type: ignore[no-untyped-call]


def _header_page(*, days_value: str = "5", with_demographics: bool = True) -> list[str]:
    """Página de relatório padrão com (ou sem) o cabeçalho de metadados."""
    lines = [_REPORT_HEADER, _INSTITUTIONAL_SIGNAL, *_SECTIONS]
    if with_demographics:
        lines.append(_DEMOGRAPHICS_LINE)
        lines.extend(["Paciente:", "Dias em tela:", days_value])
    while len("\n".join(lines)) < 600:
        lines.append(_PADDING)
    return lines


def _add_document(case: Case, *, content: bytes, uploaded_by: User) -> CaseDocument:
    """Grava um CaseDocument com o conteúdo dado no storage in-memory."""
    document = CaseDocument(
        case=case,
        position=1,
        original_filename="relatorio.pdf",
        content_type="application/pdf",
        size_bytes=len(content),
        uploaded_by=uploaded_by,
    )
    document.file.save(f"case_documents/{case.case_id}/1.pdf", ContentFile(content), save=False)
    document.save()
    return document


def _create_case_with_pdf(user: User, content: bytes) -> Case:
    """Cria um caso NEW direto (a task é chamada à mão)."""
    case = Case.objects.create(created_by=user)
    _add_document(case, content=content, uploaded_by=user)
    return case


def _retain_for_review(case: Case) -> Case:
    """Simula o resultado do gate: retido em PDF_EXTRACTING com a flag."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.manual_review_required = True
    case.manual_review_reason = RETENTION_REASON
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    return case


def _valid_pdf(name: str, content: bytes | None = None) -> SimpleUploadedFile:
    """Upload de PDF do padrão SESAB (conteúdo extraível ou o dado)."""
    payload = content if content is not None else _pdf_bytes([_header_page()])
    return SimpleUploadedFile(name, payload, content_type="application/pdf")


# ── R3/R4: worker pdf persiste os metadados ────────────────────────────────


@pytest.mark.django_db
def test_worker_persists_header_metadata(nir_user: User) -> None:
    """R3/R4: worker grava idade/sexo/raça/dias no caso (maior dias entre páginas)."""
    case = _create_case_with_pdf(
        nir_user, _pdf_bytes([_header_page(days_value="5"), _header_page(days_value="6")])
    )

    process_case_documents(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.ANONYMIZING
    assert case.patient_age == 79
    assert case.patient_gender == "F"
    assert case.patient_race == "Parda"
    assert case.days_on_screen == 6


@pytest.mark.django_db
def test_worker_without_header_leaves_metadata_empty(nir_user: User) -> None:
    """R3/R4: cabeçalho ausente não é erro — caso processado com campos vazios."""
    case = _create_case_with_pdf(nir_user, _pdf_bytes([_header_page(with_demographics=False)]))

    process_case_documents(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.ANONYMIZING
    assert case.patient_age is None
    assert case.patient_gender == ""
    assert case.patient_race == ""
    assert case.days_on_screen is None


@pytest.mark.django_db
def test_worker_events_carry_no_metadata_values(nir_user: User) -> None:
    """R3/D4: eventos do worker não carregam os valores dos metadados (PHI-free)."""
    case = _create_case_with_pdf(nir_user, _pdf_bytes([_header_page()]))

    process_case_documents(case.case_id)
    case.refresh_from_db()

    # A extração rodou (os metadados estão no caso) e, ainda assim, os
    # payloads dos eventos não carregam os valores (não-vacuoso).
    assert case.patient_age == 79
    assert case.patient_race == "Parda"
    serialized = json.dumps([event.payload for event in case.events.all()])
    for key in ("patient_age", "patient_gender", "patient_race", "days_on_screen"):
        assert key not in serialized
    assert "Parda" not in serialized
    assert not re.search(r"\b79\b", serialized)


# ── R3/R4: reenvio de documentos zera os metadados ─────────────────────────


@pytest.mark.django_db
def test_resubmit_zeroes_header_metadata(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3/R4: reenvio zera os 4 metadados na MESMA transação (None/"")."""
    case = _retain_for_review(
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(name="antigo.pdf"),
            procedure_type="art_perif",
        )
    )
    case.patient_age = 79
    case.patient_gender = "F"
    case.patient_race = "Parda"
    case.days_on_screen = 5
    case.save(update_fields=["patient_age", "patient_gender", "patient_race", "days_on_screen"])

    resubmit_case_documents(
        case=case,
        user=nir_user,
        role=NIR_ROLE,
        files=[_valid_pdf("corrigido.pdf")],
    )

    case.refresh_from_db()
    assert case.patient_age is None
    assert case.patient_gender == ""
    assert case.patient_race == ""
    assert case.days_on_screen is None


@pytest.mark.django_db
def test_resubmit_corrupt_pdf_keeps_metadata_zeroed(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3/R4: novo PDF que falha na extração não deixa metadados órfãos."""
    case = _retain_for_review(
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(name="antigo.pdf"),
            procedure_type="art_perif",
        )
    )
    case.patient_age = 79
    case.patient_gender = "F"
    case.patient_race = "Parda"
    case.days_on_screen = 5
    case.save(update_fields=["patient_age", "patient_gender", "patient_race", "days_on_screen"])

    resubmit_case_documents(
        case=case,
        user=nir_user,
        role=NIR_ROLE,
        files=[_valid_pdf("corrompido.pdf", content=_CORRUPT_PDF)],
    )
    process_case_documents(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert case.patient_age is None
    assert case.patient_gender == ""
    assert case.patient_race == ""
    assert case.days_on_screen is None
