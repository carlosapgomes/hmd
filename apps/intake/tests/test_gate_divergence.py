"""Testes do gate de divergência no intake (slice 004, R7/D5).

Cobre o despacho de ``release_retained_case`` por (estado, razão): retenção
de FORMATO (``PDF_EXTRACTING``) mantém o comportamento atual (→ ANONYMIZING)
e a nova retenção por divergência de procedimentos (``LLM_EXTRACTING`` +
``procedure_divergence``) libera via ``bypass_pipeline_divergence`` (→
LLM_SUMMARIZING, flag zerada, ``CASE_GATE_BYPASSED`` no mesmo atomic). O
reenvio de documentos continua exclusivo da retenção de formato; release de
caso fora das duas retenções → 400 sem efeito. Templates de lista/detalhe
exibem as duas retenções com badge distinto e a ação de reenvio só no formato.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseStatus
from apps.intake.services import (
    CaseNotRetainedError,
    release_retained_case,
    resubmit_case_documents,
)

NIR_ROLE = "nir"
SYSTEM_ROLE = "system"
DIVERGENCE_REASON = "procedure_divergence"
# Motivo canônico da retenção de formato (mesmo shape do gate, D4).
FORMAT_REASON = "documento fora do padrão SESAB do relatório (motivo: missing_header)"


@pytest.fixture(autouse=True)
def _no_inline_processing() -> Iterator[None]:
    """Criação sem processamento automático: o teste controla os estados."""

    with override_settings(
        INTAKE_RUN_TASKS_INLINE=False,
        ANONYMIZATION_RUN_TASKS_INLINE=False,
    ):
        yield


@pytest.fixture
def nir_user() -> User:
    role, _ = Role.objects.get_or_create(name=NIR_ROLE)
    user = User.objects.create_user(username="nir-divergencia", password="senha-teste")
    user.roles.add(role)
    return user


def _valid_pdf(name: str = "relatorio.pdf") -> SimpleUploadedFile:
    """Upload fake aceito pela validação PDF-only (metadados; sem extração)."""
    return SimpleUploadedFile(name, b"%PDF-1.4 relatorio fake", content_type="application/pdf")


def _advance_to_llm_extracting(case: Case) -> Case:
    """Caminho FSM real até LLM_EXTRACTING (sem anonimização de conteúdo)."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.LLM_EXTRACTING
    return case


def _divergence_retained_case(user: User) -> Case:
    """Caso retido por divergência de procedimentos em LLM_EXTRACTING."""
    case = Case.objects.create(created_by=user)
    _advance_to_llm_extracting(case)
    case.manual_review_required = True
    case.manual_review_reason = DIVERGENCE_REASON
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    return case


def _event_types(case: Case) -> list[str]:
    return list(case.events.order_by("id").values_list("event_type", flat=True))


# ── R7: release despacha pela retenção ─────────────────────────────────────


@pytest.mark.django_db
def test_release_dispatches_by_retention(nir_user: User) -> None:
    """R7/R8: LLM_EXTRACTING + procedure_divergence → liberar avança a
    LLM_SUMMARIZING via ``bypass_pipeline_divergence`` com flag zerada e os
    eventos de transição + ``CASE_GATE_BYPASSED`` no mesmo atomic (ator NIR)."""
    case = _divergence_retained_case(nir_user)
    events_before = case.events.count()

    target = release_retained_case(case=case, user=nir_user, role=NIR_ROLE)
    case.refresh_from_db()

    assert target == CaseStatus.LLM_SUMMARIZING
    assert case.status == CaseStatus.LLM_SUMMARIZING
    assert case.manual_review_required is False
    assert case.manual_review_reason == ""
    assert case.events.count() == events_before + 2
    event_types = _event_types(case)
    assert event_types[-2:] == [
        CaseEventType.CASE_STATUS_LLM_SUMMARIZING.value,
        CaseEventType.CASE_GATE_BYPASSED.value,
    ]
    bypass = case.events.get(event_type=CaseEventType.CASE_GATE_BYPASSED)
    assert bypass.actor == nir_user
    assert bypass.actor_type == "user"
    assert bypass.actor_role == NIR_ROLE
    assert bypass.payload == {"reason": DIVERGENCE_REASON}


@pytest.mark.django_db
def test_release_format_unchanged(nir_user: User) -> None:
    """R7/R8: retenção de FORMATO (PDF_EXTRACTING) mantém o comportamento
    atual — release → ANONYMIZING com o motivo original no bypass."""
    case = Case.objects.create(created_by=nir_user)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.manual_review_required = True
    case.manual_review_reason = FORMAT_REASON
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    events_before = case.events.count()

    target = release_retained_case(case=case, user=nir_user, role=NIR_ROLE)
    case.refresh_from_db()

    assert target == CaseStatus.ANONYMIZING
    assert case.status == CaseStatus.ANONYMIZING
    assert case.manual_review_required is False
    assert case.manual_review_reason == ""
    assert case.events.count() == events_before + 2
    bypass = case.events.get(event_type=CaseEventType.CASE_GATE_BYPASSED)
    assert bypass.payload == {"reason": FORMAT_REASON}
    assert CaseEventType.CASE_STATUS_ANONYMIZING.value in _event_types(case)


@pytest.mark.django_db
def test_release_not_retained_rejected(nir_user: User) -> None:
    """R7/R8: release de caso fora das duas retenções → erro sem efeito
    (LLM_EXTRACTING sem flag; LLM_EXTRACTING com motivo não-divergência)."""
    free = _advance_to_llm_extracting(Case.objects.create(created_by=nir_user))
    wrong_reason = _advance_to_llm_extracting(Case.objects.create(created_by=nir_user))
    wrong_reason.manual_review_required = True
    wrong_reason.manual_review_reason = "outro_motivo"
    wrong_reason.save(update_fields=["manual_review_required", "manual_review_reason"])

    for case in (free, wrong_reason):
        events_before = case.events.count()
        with pytest.raises(CaseNotRetainedError):
            release_retained_case(case=case, user=nir_user, role=NIR_ROLE)
        case.refresh_from_db()
        assert case.status == CaseStatus.LLM_EXTRACTING
        assert case.events.count() == events_before


@pytest.mark.django_db
def test_release_view_400_for_unretained_llm_case(
    client: Client,
    nir_user: User,
) -> None:
    """R7: POST de liberar em caso LLM_EXTRACTING não-retido → 400 na view
    (sem efeito), como acontece hoje para o caso fora da retenção de formato."""
    case = _advance_to_llm_extracting(Case.objects.create(created_by=nir_user))
    client.force_login(nir_user)

    response = client.post(reverse("intake:gate_release", args=[case.case_id]))

    assert response.status_code == 400
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is False


# ── R7: resubmit continua só para retenção de formato ──────────────────────


@pytest.mark.django_db
def test_resubmit_denied_for_divergence_retention(nir_user: User) -> None:
    """R7: o reenvio de documentos é só da retenção de FORMATO — divergência
    em LLM_EXTRACTING responde erro (a liberação é o bypass, não o reenvio)."""
    case = _divergence_retained_case(nir_user)
    events_before = case.events.count()

    with pytest.raises(CaseNotRetainedError):
        resubmit_case_documents(
            case=case,
            user=nir_user,
            role=NIR_ROLE,
            files=[_valid_pdf()],
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == DIVERGENCE_REASON
    assert case.events.count() == events_before


# ── R7: lista/detalhe com badge distinto e ações por retenção ──────────────


@pytest.mark.django_db
def test_my_cases_lists_llm_retention_with_distinct_badge(
    client: Client,
    nir_user: User,
) -> None:
    """R7: a lista mostra o caso retido por divergência com badge distinto."""
    case = _divergence_retained_case(nir_user)
    client.force_login(nir_user)

    body = client.get(reverse("intake:my_cases")).content.decode()

    assert str(case.case_id) in body
    assert "Extraindo via LLM" in body
    assert "Divergência" in body


@pytest.mark.django_db
def test_detail_shows_bypass_only_for_divergence(
    client: Client,
    nir_user: User,
) -> None:
    """R7: no detalhe de retenção por divergência só aparece a ação de
    liberar (bypass) — sem o formulário de reenvio de documentos."""
    case = _divergence_retained_case(nir_user)
    client.force_login(nir_user)

    body = client.get(reverse("intake:case_detail", args=[case.case_id])).content.decode()

    assert reverse("intake:gate_release", args=[case.case_id]) in body
    assert "Liberar caso" in body
    assert reverse("intake:gate_resubmit", args=[case.case_id]) not in body
    assert 'name="documents"' not in body
