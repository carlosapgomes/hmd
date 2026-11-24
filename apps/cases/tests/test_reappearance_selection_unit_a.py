"""Seleção conservadora de reapresentação (S2 unidade A — R2.2/R2.3, AUD-02/08).

O pool de ambiguidade inclui TODOS os plausíveis (``strong`` e
``needs_confirmation``) nas três filas D1/D3 — ``AWAITING_DOCTOR``,
``SCHEDULER_REQUESTED`` e ``AWAITING_SCHEDULING`` — incluindo fracos por
origem/idade e conflitos de CNS. Só há substituição médica quando há
EXATAMENTE UM plausível, forte e ainda médico. Anterioridade estrita pela
tupla ``(created_at, case_id)`` (D3), na descoberta e na revalidação.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.cases import reappearance as reappearance_module
from apps.cases.models import Case, CaseProcedure, CaseReappearance, CaseStatus

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)

SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
_CNS_A = "207139200176053"
_CNS_B = "605932160152243"
_NAME = "Maria da Silva"
_NAME_VARIANT = "MARIA  da silva"  # normalização-equivalente: caixa + espaços


def _declared(case: Case, *procedure_types: str) -> None:
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)


def _awaiting(case: Case) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _identity(
    case: Case,
    *,
    name: str = _NAME,
    number: str = "123",
    cns: str = "",
    origin: str = "",
    age: int | None = None,
) -> Case:
    case.patient_name = name
    case.agency_record_number = number
    case.patient_cns = cns
    case.origin_unit = origin
    case.patient_age = age
    case.save(
        update_fields=[
            "patient_name",
            "agency_record_number",
            "patient_cns",
            "origin_unit",
            "patient_age",
        ]
    )
    return case


def _make_case(
    owner: User,
    *,
    case_id: uuid.UUID | None = None,
    days_ago: int = 5,
    to_scheduling: bool = False,
) -> Case:
    case = Case.objects.create(created_by=owner, **({"case_id": case_id} if case_id else {}))
    _declared(case, ANGIO_TYPE)
    _awaiting(case)
    if to_scheduling:
        case.record_doctor_decision(accepted=True, user=None, role=SYSTEM_ROLE)
        case.request_scheduling(user=None, role=SYSTEM_ROLE)
        case.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
        assert case.status == CaseStatus.AWAITING_SCHEDULING
    Case.objects.filter(pk=case.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    case.refresh_from_db()
    return case


def _make_incoming(owner: User, *, case_id: uuid.UUID | None = None) -> Case:
    incoming = Case.objects.create(created_by=owner, **({"case_id": case_id} if case_id else {}))
    _declared(incoming, ANGIO_TYPE)
    _awaiting(incoming)
    return incoming


def _discovered_ids(incoming: Case) -> set[uuid.UUID]:
    return {case.pk for case in reappearance_module._candidate_queryset(incoming)}


@NEW_MODE
@pytest.mark.django_db
def test_strong_plus_weak_origin_variant_blocks_automation(nir_user: User) -> None:
    """R2.2: forte + fraco (nome+origem, variante de normalização) → sem seleção."""
    strong = _make_case(nir_user, days_ago=5)
    _identity(strong, number="123")
    weak = _make_case(nir_user, days_ago=3)
    _identity(weak, name=_NAME_VARIANT, number="", origin="Hospital Geral")
    incoming = _make_incoming(nir_user)
    _identity(incoming, number="123", origin="Hospital Geral")
    # Não-vácuo: os dois plausíveis são de fato descobertos e classificados.
    assert _discovered_ids(incoming) == {strong.pk, weak.pk}
    incoming_signals = reappearance_module._identity_signals_for(incoming)
    assert (
        reappearance_module.classify_pair(
            incoming_signals, reappearance_module._identity_signals_for(weak)
        ).verdict
        == "needs_confirmation"
    )
    assert reappearance_module.find_medical_replacement_candidate(incoming) is None
    assert reappearance_module.resolve_medical_replacement(incoming) is None
    strong.refresh_from_db()
    weak.refresh_from_db()
    assert strong.status == CaseStatus.AWAITING_DOCTOR
    assert weak.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()


@NEW_MODE
@pytest.mark.django_db
def test_strong_plus_cns_conflict_blocks_automation(nir_user: User) -> None:
    """R2.2: forte + conflito de CNS válido (mesma regulação) → sem seleção."""
    strong = _make_case(nir_user, days_ago=5)
    _identity(strong, number="123", cns=_CNS_A)
    conflict = _make_case(nir_user, days_ago=3)
    _identity(conflict, number="123", cns=_CNS_B)
    incoming = _make_incoming(nir_user)
    _identity(incoming, number="123", cns=_CNS_A)
    assert _discovered_ids(incoming) == {strong.pk, conflict.pk}
    assert reappearance_module.find_medical_replacement_candidate(incoming) is None
    assert reappearance_module.resolve_medical_replacement(incoming) is None
    strong.refresh_from_db()
    conflict.refresh_from_db()
    assert strong.status == CaseStatus.AWAITING_DOCTOR
    assert conflict.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()


@NEW_MODE
@pytest.mark.django_db
def test_strong_plus_age_conflict_blocks_automation(nir_user: User) -> None:
    """R2.2: forte + par forte com diferença de idade >1 (conferência) → sem seleção."""
    strong = _make_case(nir_user, days_ago=5)
    _identity(strong, number="123", age=46)
    age_conflict = _make_case(nir_user, days_ago=3)
    _identity(age_conflict, number="123", age=48)
    incoming = _make_incoming(nir_user)
    _identity(incoming, number="123", age=45)
    assert _discovered_ids(incoming) == {strong.pk, age_conflict.pk}
    assert reappearance_module.find_medical_replacement_candidate(incoming) is None
    assert reappearance_module.resolve_medical_replacement(incoming) is None
    strong.refresh_from_db()
    age_conflict.refresh_from_db()
    assert strong.status == CaseStatus.AWAITING_DOCTOR
    assert age_conflict.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()


@NEW_MODE
@pytest.mark.django_db
def test_strong_medical_plus_scheduling_plausible_blocks_automation(nir_user: User) -> None:
    """R2.2: forte médico + plausível em agendamento → sem seleção nem efeito S2."""
    strong = _make_case(nir_user, days_ago=5)
    _identity(strong, number="123")
    scheduling = _make_case(nir_user, days_ago=3, to_scheduling=True)
    _identity(scheduling, number="123")
    incoming = _make_incoming(nir_user)
    _identity(incoming, number="123")
    assert _discovered_ids(incoming) == {strong.pk, scheduling.pk}
    assert reappearance_module.find_medical_replacement_candidate(incoming) is None
    assert reappearance_module.resolve_medical_replacement(incoming) is None
    strong.refresh_from_db()
    scheduling.refresh_from_db()
    assert strong.status == CaseStatus.AWAITING_DOCTOR
    assert scheduling.status == CaseStatus.AWAITING_SCHEDULING
    assert not CaseReappearance.objects.exists()


@NEW_MODE
@pytest.mark.django_db
def test_weak_alone_is_discovered_but_never_applies(nir_user: User) -> None:
    """R2.2: fraco sozinho é plausível descoberto, mas nunca aplica."""
    weak = _make_case(nir_user, days_ago=5)
    _identity(weak, name=_NAME_VARIANT, number="", origin="Hospital Geral")
    incoming = _make_incoming(nir_user)
    _identity(incoming, number="", origin="Hospital Geral")
    assert _discovered_ids(incoming) == {weak.pk}
    assert reappearance_module.find_medical_replacement_candidate(incoming) is None
    assert reappearance_module.resolve_medical_replacement(incoming) is None
    weak.refresh_from_db()
    assert weak.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()


@NEW_MODE
@pytest.mark.django_db
def test_single_strong_medical_with_name_variant_still_applies(nir_user: User) -> None:
    """R2.2 (controle positivo): único forte médico, nome variante → aplica."""
    strong = _make_case(nir_user, days_ago=5)
    _identity(strong, name=_NAME_VARIANT, number="123")
    incoming = _make_incoming(nir_user)
    _identity(incoming, number="123")
    assert _discovered_ids(incoming) == {strong.pk}
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    strong.refresh_from_db()
    assert strong.status == CaseStatus.CLEANED


@NEW_MODE
@pytest.mark.django_db
def test_creation_tie_with_larger_case_id_is_not_anterior(nir_user: User) -> None:
    """R2.3: empate de criação com case_id maior NÃO é anterior estrito."""
    incoming_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    prior_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    prior = _make_case(nir_user, case_id=prior_id, days_ago=0)
    _identity(prior, number="123")
    incoming = _make_incoming(nir_user, case_id=incoming_id)
    _identity(incoming, number="123")
    Case.objects.filter(pk=prior.pk).update(created_at=incoming.created_at)
    prior.refresh_from_db()
    assert prior.created_at == incoming.created_at
    assert reappearance_module.find_medical_replacement_candidate(incoming) is None
    assert reappearance_module.resolve_medical_replacement(incoming) is None
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()


@NEW_MODE
@pytest.mark.django_db
def test_creation_tie_with_smaller_case_id_is_anterior(nir_user: User) -> None:
    """R2.3: empate de criação com case_id menor É anterior estrito → aplica."""
    prior_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    incoming_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    prior = _make_case(nir_user, case_id=prior_id, days_ago=0)
    _identity(prior, number="123")
    incoming = _make_incoming(nir_user, case_id=incoming_id)
    _identity(incoming, number="123")
    Case.objects.filter(pk=prior.pk).update(created_at=incoming.created_at)
    prior.refresh_from_db()
    assert prior.created_at == incoming.created_at
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
