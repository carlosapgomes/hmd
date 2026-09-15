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
