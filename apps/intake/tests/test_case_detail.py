"""Testes do detalhe do NIR — procedimentos declarados × detectados.

Cobre R1–R3 do slice 001 do change ``detected-procedures-visible``: a seção
«Procedimentos do caso» lista TODAS as rows de ``CaseProcedure`` com origem
(declarado pelo NIR × detectado na extração) e o status de detecção após a
reconciliação, e o card de revisão da divergência resume declarados ×
detectados junto à ação de liberação.

Caso real que originou o change (dev): divergência ``angio_art_perif``
declarada não-detectada + ``art_perif`` detectada não-declarada — o detalhe
listava apenas os declarados e o NIR decidia às cegas.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseStatus
from apps.cases.procedures import record_detected_procedures
from apps.intake.services import create_case_with_documents

NIR_ROLE = "nir"
SYSTEM_ROLE = "system"
DIVERGENCE_REASON = "procedure_divergence"

# Labels do catálogo (fonte única: PROCEDURE_PROFILES).
ANGIO_LABEL = "Angioplastia arterial periférica (membros)"
ART_PERIF_LABEL = "Arteriografia periférica"


@pytest.fixture(autouse=True)
def _no_inline_processing() -> Iterator[None]:
    """Criação sem processamento automático: o teste controla os estados."""

    with override_settings(
        INTAKE_RUN_TASKS_INLINE=False,
        ANONYMIZATION_RUN_TASKS_INLINE=False,
    ):
        yield


def _create_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    procedure_type: str,
) -> Case:
    """Cria um caso NEW com 1 PDF fake e o tipo declarado informado."""
    return create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(),
        procedure_type=procedure_type,
    )


def _advance_to_llm_extracting(case: Case) -> Case:
    """Caminho FSM real até LLM_EXTRACTING (sem conteúdo de PDF)."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.LLM_EXTRACTING
    return case


def _detail_body(client: Client, case: Case) -> str:
    response = client.get(reverse("intake:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    return response.content.decode()


def _visible_text(body: str) -> str:
    """HTML sem os atributos de rota (``href``/``action``/``data-src``).

    O uid permanece como chave técnica das URLs (D5 do design + PVP-002 R2:
    o embed lazy carrega a rota de serve em ``data-src``); remover os
    atributos permite pinar que ele NÃO aparece em texto visível.
    """
    return re.sub(r'(?:href|action|data-src)="[^"]*"', "", body)


def _identification_card(body: str) -> str:
    """Fatia o card «Identificação do paciente» (wrapper até o próximo card).

    A âncora é o ``h1`` do card (o comentário do template cita o mesmo texto).
    """
    heading = body.index(">Identificação do paciente</h1>")
    start = body.rindex('<div class="card shadow-sm', 0, heading)
    return body[start : body.index('<div class="card shadow-sm', heading)]


def _drive_to_status(case: Case, status: str) -> Case:
    """Leva o caso ao estado informado pelas operações reais da FSM."""
    if status == CaseStatus.PDF_EXTRACTING.value:
        case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    elif status == CaseStatus.LLM_EXTRACTING.value:
        _advance_to_llm_extracting(case)
    elif status == CaseStatus.FAILED.value:
        case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
        case.fail_processing(reason="falha na extração do PDF", user=None, role=SYSTEM_ROLE)
    else:
        assert case.status == status
    return case


def _identified_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    *,
    record_number: str,
    patient_name: str = "",
    patient_age: int | None = None,
) -> Case:
    """Caso NEW com o nº de ocorrência/identificação do cabeçalho SESAB."""
    case = _create_case(user, pdf_factory, "art_perif")
    case.agency_record_number = record_number
    case.patient_name = patient_name
    case.patient_age = patient_age
    case.save(update_fields=["agency_record_number", "patient_name", "patient_age"])
    return case


# ── Slice 002 do change ``case-detail-human-reference``: card de identificação
#    do paciente + referência humana em title/breadcrumb/link de correção ────


@pytest.mark.django_db
def test_detail_uses_human_reference_without_full_uid(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: title/breadcrumb exibem «Caso <nº>»; uid completo só nos hrefs."""
    case = _identified_case(nir_user, pdf_factory, record_number="5040778")

    client.force_login(nir_user)
    body = _detail_body(client, case)

    assert "— Caso 5040778" in body
    assert 'aria-current="page">Caso 5040778</li>' in body
    assert str(case.case_id) not in _visible_text(body)
    assert str(case.case_id) in body  # chave técnica das URLs (D5)


@pytest.mark.django_db
def test_detail_identification_card_shows_patient_and_timestamps(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: card «Identificação do paciente» com nome, idade, nº e timestamps."""
    case = _identified_case(
        nir_user,
        pdf_factory,
        record_number="5040778",
        patient_name="Paciente do Detalhe",
        patient_age=84,
    )

    client.force_login(nir_user)
    card = _identification_card(_detail_body(client, case))

    assert '<h1 class="h5 mb-0">Identificação do paciente</h1>' in card
    assert "Paciente do Detalhe" in card
    assert "84 a" in card
    assert "5040778" in card
    assert "Criado em" in card
    assert "atualizado em" in card


@pytest.mark.django_db
def test_detail_without_record_number_uses_short_uid_fallback(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: sem nº → `uid <8 chars>`; sem identificação → «—» e idade omitida."""
    case = _identified_case(nir_user, pdf_factory, record_number="")
    short_id = str(case.case_id)[:8]

    client.force_login(nir_user)
    body = _detail_body(client, case)
    card = _identification_card(body)

    assert f"Caso uid {short_id}" in body
    assert f"uid {short_id}" in card
    assert str(case.case_id) not in _visible_text(body)
    assert "—" in card
    assert "Idade" not in card


@pytest.mark.django_db
def test_detail_correction_link_uses_disambiguated_reference(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: link de «Reenvios corrigidos» exibe `nº · sufixo` com href por uid."""
    original = _identified_case(nir_user, pdf_factory, record_number="5040778")
    correction = _identified_case(nir_user, pdf_factory, record_number="5040778")
    correction.corrects_case = original
    correction.correction_reason = "Documento ilegível reenviado"
    correction.save(update_fields=["corrects_case", "correction_reason"])
    short_id = str(correction.case_id)[:8]

    client.force_login(nir_user)
    body = _detail_body(client, original)

    assert "Reenvios corrigidos deste caso" in body
    assert f"5040778 · {short_id}" in body
    assert reverse("intake:case_detail", args=[correction.case_id]) in body
    assert str(correction.case_id) not in _visible_text(body)


@pytest.mark.parametrize(
    ("status", "manual_review", "review_reason", "badge_class", "badge_text"),
    [
        (CaseStatus.FAILED.value, False, "", "text-bg-danger", "Falha no processamento"),
        (
            CaseStatus.LLM_EXTRACTING.value,
            True,
            DIVERGENCE_REASON,
            "text-bg-info",
            "Extraindo via LLM",
        ),
        (
            CaseStatus.PDF_EXTRACTING.value,
            True,
            "missing_header",
            "text-bg-warning",
            "Extraindo PDF",
        ),
        (CaseStatus.NEW.value, False, "", "text-bg-secondary", "Novo"),
    ],
)
@pytest.mark.django_db
def test_detail_identification_card_preserves_status_badges(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    status: str,
    manual_review: bool,
    review_reason: str,
    badge_class: str,
    badge_text: str,
) -> None:
    """R2: as classes condicionais do badge de status migram para o header."""
    case = _drive_to_status(_create_case(nir_user, pdf_factory, "art_perif"), status)
    case.manual_review_required = manual_review
    case.manual_review_reason = review_reason
    case.save(update_fields=["manual_review_required", "manual_review_reason"])

    client.force_login(nir_user)
    card = _identification_card(_detail_body(client, case))

    assert f"badge rounded-pill {badge_class}" in card
    assert badge_text in card


@pytest.mark.django_db
def test_detail_divergence_lists_declared_and_detected(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/R3: divergência exibe ambos os conjuntos + resumo no card de revisão."""
    case = _create_case(nir_user, pdf_factory, "angio_art_perif")
    _advance_to_llm_extracting(case)
    record_detected_procedures(
        case,
        {"angio_art_perif": "not_detected", "art_perif": "detected"},
        user=None,
        role=SYSTEM_ROLE,
    )
    case.manual_review_required = True
    case.manual_review_reason = DIVERGENCE_REASON
    case.save(update_fields=["manual_review_required", "manual_review_reason"])

    client.force_login(nir_user)
    body = _detail_body(client, case)

    # Seção com as DUAS rows (declarada não-detectada + detectada extra).
    assert "Procedimentos do caso" in body
    assert ANGIO_LABEL in body
    assert ART_PERIF_LABEL in body
    # Origem e detecção por row.
    assert "Declarado" in body
    assert "Não detectado" in body
    assert "Detectado na extração" in body
    # Resumo declarados × detectados no card da divergência, junto à ação.
    assert "Declarados:" in body
    assert "— não detectado" in body
    assert "Detectados na extração:" in body
    assert "Liberar caso" in body


@pytest.mark.django_db
def test_detail_coincident_row_shows_declared_and_detected(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: coincidência = UMA row com os dois badges (Declarado + Detectado)."""
    case = _create_case(nir_user, pdf_factory, "art_perif")
    _advance_to_llm_extracting(case)
    record_detected_procedures(
        case,
        {"art_perif": "detected"},
        user=None,
        role=SYSTEM_ROLE,
    )

    client.force_login(nir_user)
    body = _detail_body(client, case)

    assert body.count(ART_PERIF_LABEL) == 1
    assert "Declarado" in body
    assert "Detectado" in body
    assert "Não detectado" not in body
    assert "Detectado na extração" not in body


@pytest.mark.django_db
def test_detail_without_reconciliation_shows_only_origin(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: sem reconciliação (pending) → só a origem, sem badge de detecção."""
    case = _create_case(nir_user, pdf_factory, "art_perif")

    client.force_login(nir_user)
    body = _detail_body(client, case)

    assert "Procedimentos do caso" in body
    assert ART_PERIF_LABEL in body
    assert "Declarado" in body
    assert "Detectado" not in body
    assert "Não detectado" not in body
    assert "Detectado na extração" not in body
