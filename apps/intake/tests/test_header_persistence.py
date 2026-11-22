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
from apps.cases.closure import (
    _CLEANED_EMPTY_VALUES,
    _clean_acknowledged_clinical_data,
    acknowledge_case_receipt,
    post_doctor_denial_reply,
)
from apps.cases.models import Case, CaseDocument, CaseProcedure, CaseStatus
from apps.cases.procedures import record_doctor_procedure_decisions
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


# ── R2/R3 (change painel-ats-parity, slice 001): unidade de origem ─────────
#
# ``origin_unit`` segue o MESMO ciclo dos metadados do cabeçalho: o worker pdf
# persiste no write único do ``extracted_text``; o reenvio de documentos zera
# na mesma transação; a minimização do CLEANED NÃO o toca (dado
# administrativo — paridade com ``agency_record_number``).

_ORIGIN_UNIT = "HELN - HOSPITAL ESTADUAL DO LESTE NORTE"
_ORIGIN_UNIT_INLINE = "Hospital Geral do Estado"


def _header_page_with_multiline_origin(value: str) -> list[str]:
    """Página padrão com ``Unid. Origem:`` sozinho e o valor na linha seguinte.

    O rótulo inline do ``_SECTIONS`` é removido para o valor vir do layout
    multilinha (caso do corpus real).
    """
    sections = [line for line in _SECTIONS if not line.startswith("Unid. Origem:")]
    lines = [_REPORT_HEADER, _INSTITUTIONAL_SIGNAL, *sections, "Unid. Origem:", value]
    while len("\n".join(lines)) < 600:
        lines.append(_PADDING)
    return lines


@pytest.mark.django_db
def test_worker_persists_origin_unit_multiline(nir_user: User) -> None:
    """R2/R3: worker grava a unidade de origem do rótulo multilinha."""
    case = _create_case_with_pdf(
        nir_user, _pdf_bytes([_header_page_with_multiline_origin(_ORIGIN_UNIT)])
    )

    process_case_documents(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.ANONYMIZING
    assert case.origin_unit == _ORIGIN_UNIT


@pytest.mark.django_db
def test_worker_persists_origin_unit_inline(nir_user: User) -> None:
    """R2/R3: worker grava a unidade da forma mesma-linha do cabeçalho."""
    case = _create_case_with_pdf(nir_user, _pdf_bytes([_header_page()]))

    process_case_documents(case.case_id)
    case.refresh_from_db()

    assert case.origin_unit == _ORIGIN_UNIT_INLINE


@pytest.mark.django_db
def test_worker_events_carry_no_origin_unit(nir_user: User) -> None:
    """R2/D4: eventos do worker não carregam o valor da unidade (PHI-free)."""
    case = _create_case_with_pdf(
        nir_user, _pdf_bytes([_header_page_with_multiline_origin(_ORIGIN_UNIT)])
    )

    process_case_documents(case.case_id)
    case.refresh_from_db()

    # Não-vacuoso: a unidade foi extraída e, ainda assim, não aparece nos eventos.
    assert case.origin_unit == _ORIGIN_UNIT
    serialized = json.dumps([event.payload for event in case.events.all()])
    assert "origin_unit" not in serialized
    assert "HELN" not in serialized


@pytest.mark.django_db
def test_resubmit_zeroes_origin_unit(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/R3: reenvio zera ``origin_unit`` na MESMA transação ("")."""
    case = _retain_for_review(
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(name="antigo.pdf"),
            procedure_type="art_perif",
        )
    )
    case.origin_unit = _ORIGIN_UNIT
    case.save(update_fields=["origin_unit"])

    resubmit_case_documents(
        case=case,
        user=nir_user,
        role=NIR_ROLE,
        files=[_valid_pdf("corrigido.pdf")],
    )

    case.refresh_from_db()
    assert case.origin_unit == ""


@pytest.mark.django_db
def test_resubmit_corrupt_pdf_keeps_origin_unit_zeroed(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/R3: novo PDF que falha na extração não deixa unidade órfã."""
    case = _retain_for_review(
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(name="antigo.pdf"),
            procedure_type="art_perif",
        )
    )
    case.origin_unit = _ORIGIN_UNIT
    case.save(update_fields=["origin_unit"])

    resubmit_case_documents(
        case=case,
        user=nir_user,
        role=NIR_ROLE,
        files=[_valid_pdf("corrompido.pdf", content=_CORRUPT_PDF)],
    )
    process_case_documents(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert case.origin_unit == ""


@pytest.mark.django_db
def test_cleaned_minimization_preserves_origin_unit(nir_user: User) -> None:
    """R3: a minimização do CLEANED preserva a unidade de origem (paridade
    administrativa com ``agency_record_number``; não-vacuoso: os campos
    clínicos SÃO zerados pela MESMA passada)."""
    case = _create_case_with_pdf(
        nir_user, _pdf_bytes([_header_page_with_multiline_origin(_ORIGIN_UNIT)])
    )
    process_case_documents(case.case_id)
    case.refresh_from_db()
    assert case.origin_unit == _ORIGIN_UNIT

    _clean_acknowledged_clinical_data(case)
    case.refresh_from_db()

    assert case.extracted_text == ""
    assert case.anonymized_text == ""
    assert case.origin_unit == _ORIGIN_UNIT


def test_cleaned_empty_values_does_not_touch_origin_unit() -> None:
    """R3 (gate 2): ``_CLEANED_EMPTY_VALUES`` permanece IDÊNTICO — a unidade
    de origem não entra na minimização."""
    assert "origin_unit" not in _CLEANED_EMPTY_VALUES


# ── R2–R6 (change transport-badge, slice 001): tipo de transporte ─────────
#
# ``transport_type`` segue o MESMO ciclo dos metadados do cabeçalho: o worker
# pdf persiste no write único do ``extracted_text``; o reenvio de documentos
# zera na mesma transação; o evento de extração NÃO carrega o valor; a
# minimização do CLEANED NÃO o toca (metadado administrativo não-
# identificante — paridade com ``origin_unit``). ``apps/cases/closure.py``
# permanece intocado: a AUSÊNCIA do campo no dict É a preservação.

_TRANSPORT_BASIC = "TRANSPORTE: Ambulância Básica"
_TRANSPORT_MOBILE_ICU = "TRANSPORTE: UTI Móvel"


def _header_page_with_transport(value: str) -> list[str]:
    """Página padrão com o label estrutural do transporte (valor mesma-linha)."""
    return [*_header_page(), value]


@pytest.mark.django_db
def test_transport_type_defaults_to_none_with_canonical_labels(nir_user: User) -> None:
    """R2: migration 0015 aplica — campo nasce nulo, labels canônicos."""
    case = Case.objects.create(created_by=nir_user)

    assert case.transport_type is None
    case.transport_type = "basic"
    assert case.get_transport_type_display() == "Ambulância Básica"
    case.transport_type = "mobile_icu"
    assert case.get_transport_type_display() == "UTI Móvel"


@pytest.mark.django_db
def test_worker_persists_transport_type_basic(nir_user: User) -> None:
    """R3: worker grava ``basic`` no mesmo write dos metadados."""
    case = _create_case_with_pdf(
        nir_user, _pdf_bytes([_header_page_with_transport(_TRANSPORT_BASIC)])
    )

    process_case_documents(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.ANONYMIZING
    assert case.transport_type == "basic"


@pytest.mark.django_db
def test_worker_persists_transport_type_mobile_icu(nir_user: User) -> None:
    """R3: worker grava ``mobile_icu`` no mesmo write dos metadados."""
    case = _create_case_with_pdf(
        nir_user, _pdf_bytes([_header_page_with_transport(_TRANSPORT_MOBILE_ICU)])
    )

    process_case_documents(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.ANONYMIZING
    assert case.transport_type == "mobile_icu"


@pytest.mark.django_db
def test_worker_without_transport_label_leaves_field_empty(nir_user: User) -> None:
    """R3: relatório sem label processa normalmente com o campo vazio."""
    case = _create_case_with_pdf(nir_user, _pdf_bytes([_header_page()]))

    process_case_documents(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.ANONYMIZING
    assert case.transport_type is None


@pytest.mark.django_db
def test_worker_events_carry_no_transport_value(nir_user: User) -> None:
    """R4: evento de extração NÃO carrega o valor do transporte."""
    case = _create_case_with_pdf(
        nir_user, _pdf_bytes([_header_page_with_transport(_TRANSPORT_MOBILE_ICU)])
    )

    process_case_documents(case.case_id)
    case.refresh_from_db()

    # Não-vacuoso: o transporte foi extraído e, ainda assim, não aparece nos eventos.
    assert case.transport_type == "mobile_icu"
    serialized = json.dumps([event.payload for event in case.events.all()])
    assert "transport_type" not in serialized
    assert "mobile_icu" not in serialized


@pytest.mark.django_db
def test_resubmit_zeroes_transport_type(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R5: reenvio sem label zera o campo na MESMA transação (sem stale)."""
    case = _retain_for_review(
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(name="antigo.pdf"),
            procedure_type="art_perif",
        )
    )
    case.transport_type = "mobile_icu"
    case.save(update_fields=["transport_type"])

    resubmit_case_documents(
        case=case,
        user=nir_user,
        role=NIR_ROLE,
        files=[_valid_pdf("corrigido.pdf")],
    )

    case.refresh_from_db()
    assert case.transport_type is None


@pytest.mark.django_db
def test_resubmit_corrupt_pdf_keeps_transport_zeroed(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R5: novo PDF que falha na extração não deixa transporte órfão."""
    case = _retain_for_review(
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(name="antigo.pdf"),
            procedure_type="art_perif",
        )
    )
    case.transport_type = "mobile_icu"
    case.save(update_fields=["transport_type"])

    resubmit_case_documents(
        case=case,
        user=nir_user,
        role=NIR_ROLE,
        files=[_valid_pdf("corrompido.pdf", content=_CORRUPT_PDF)],
    )
    process_case_documents(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert case.transport_type is None


@pytest.mark.django_db
def test_cleaned_minimization_preserves_transport_type(nir_user: User) -> None:
    """R6: a minimização do CLEANED preserva o transporte (paridade
    administrativa com ``agency_record_number``; não-vacuoso: os campos
    clínicos SÃO zerados pela MESMA passada)."""
    case = _create_case_with_pdf(
        nir_user, _pdf_bytes([_header_page_with_transport(_TRANSPORT_MOBILE_ICU)])
    )
    process_case_documents(case.case_id)
    case.refresh_from_db()
    assert case.transport_type == "mobile_icu"

    _clean_acknowledged_clinical_data(case)
    case.refresh_from_db()

    assert case.extracted_text == ""
    assert case.anonymized_text == ""
    assert case.transport_type == "mobile_icu"


def test_cleaned_empty_values_does_not_touch_transport_type() -> None:
    """R6 (gate 2): ``_CLEANED_EMPTY_VALUES`` permanece IDÊNTICO — o tipo de
    transporte não entra na minimização; ``closure.py`` intocado."""
    assert "transport_type" not in _CLEANED_EMPTY_VALUES


def _retained_page_with_transport(value: str) -> list[str]:
    """Página COM label que falha no gate (sem sinal institucional).

    Retida em ``PDF_EXTRACTING`` com ``manual_review_required`` — permite
    semear o valor inicial do R5 pelo processamento REAL (sem setattr) e
    ainda reenviar pelo caminho do serviço."""
    lines = [
        _REPORT_HEADER,
        *_SECTIONS,
        _DEMOGRAPHICS_LINE,
        "Paciente:",
        "Dias em tela:",
        "5",
        value,
    ]
    while len("\n".join(lines)) < 600:
        lines.append(_PADDING)
    return lines


@pytest.mark.django_db
def test_resubmit_without_label_reprocesses_successfully(nir_user: User) -> None:
    """R5: reenvio sem label reprocessa com sucesso — avanço normal e campo vazio.

    Seed REAL: o valor inicial vem do processamento de documento COM label
    (retido no gate, sem setattr); após o reenvio SEM label, o reprocessamento
    avança a ``ANONYMIZING`` com ``transport_type is None``."""
    case = _create_case_with_pdf(
        nir_user, _pdf_bytes([_retained_page_with_transport(_TRANSPORT_MOBILE_ICU)])
    )

    process_case_documents(case.case_id)
    case.refresh_from_db()
    assert case.status == CaseStatus.PDF_EXTRACTING
    assert case.manual_review_required is True
    assert case.transport_type == "mobile_icu"

    resubmit_case_documents(
        case=case,
        user=nir_user,
        role=NIR_ROLE,
        files=[_valid_pdf("corrigido.pdf")],
    )
    case.refresh_from_db()
    assert case.transport_type is None

    process_case_documents(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.ANONYMIZING
    assert case.transport_type is None


@pytest.mark.django_db
@pytest.mark.parametrize("minimization", [True, False])
def test_ack_preserves_transport_type_in_both_modes(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    minimization: bool,
) -> None:
    """R6: a ciência REAL preserva o transporte em AMBOS os modos até CLEANED.

    Seed REAL: o caso é processado de documento COM label (sem setattr) e
    dirigido pelo fluxo de negativa até ``FINAL_REPLY_POSTED``; a ciência vai
    pelo serviço ``acknowledge_case_receipt``. No modo ativo, a minimização
    dos clínicos (``extracted_text`` vazio) prova o teste não-vácuo."""
    doctor_user = user_factory("doctor-transport-ack", "doctor")
    case = _create_case_with_pdf(
        nir_user, _pdf_bytes([_header_page_with_transport(_TRANSPORT_MOBILE_ICU)])
    )
    for procedure_type in ("art_perif", "nefrostomia"):
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)

    process_case_documents(case.case_id)
    case.refresh_from_db()
    assert case.status == CaseStatus.ANONYMIZING
    assert case.transport_type == "mobile_icu"
    extracted_before = case.extracted_text
    assert extracted_before != ""

    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    record_doctor_procedure_decisions(
        case,
        {
            "art_perif": ("denied", "sem indicação clínica"),
            "nefrostomia": ("denied", "risco cirúrgico elevado"),
        },
        user=doctor_user,
        role="doctor",
    )
    case.refresh_from_db()
    post_doctor_denial_reply(case, user=doctor_user, role="doctor")
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.transport_type == "mobile_icu"

    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=minimization):
        acknowledge_case_receipt(case, user=nir_user, role=NIR_ROLE)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert case.transport_type == "mobile_icu"
    if minimization:
        assert case.extracted_text == ""
    else:
        assert case.extracted_text == extracted_before
