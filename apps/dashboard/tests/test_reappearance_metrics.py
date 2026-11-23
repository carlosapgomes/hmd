"""Métricas sem demanda técnica de reapresentação (slice 002, R5 — RED).

Encerramentos técnicos saem de Em andamento e de TODAS as contagens por
procedimento; Total do resumo continua contando envios; Encerrados inclui
técnicos; flag off mantém as exclusões dos vínculos já aplicados.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from apps.accounts.models import Role, User
from apps.cases import reappearance as reappearance_module
from apps.cases.models import Case, CaseProcedure, CaseStatus
from apps.dashboard.metrics import compute_by_procedure_type, compute_summary

_NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
FLAG_OFF = override_settings(CASE_REAPPEARANCE_ENABLED=False)
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"


@pytest.fixture
def nir_user() -> User:
    role, _ = Role.objects.get_or_create(name="nir")
    user = User.objects.create_user(username="nir-reap-met", password="senha-teste")
    user.roles.add(role)
    return user


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


def _identify(case: Case, *, number: str) -> None:
    case.patient_name = "Maria da Silva"
    case.agency_record_number = number
    case.save(update_fields=["patient_name", "agency_record_number"])


@_NEW_MODE
@pytest.mark.django_db
def test_technical_closure_out_of_in_progress_but_kept_in_total_and_closed(
    nir_user: User,
) -> None:
    """R5: original substituído (evento real) — fora de Em andamento; Total/Encerrados intactos."""
    prior = Case.objects.create(created_by=nir_user)
    _declared(prior, ANGIO_TYPE)
    _identify(prior, number="met-1")
    _awaiting(prior)
    incoming = Case.objects.create(created_by=nir_user)
    _declared(incoming, ANGIO_TYPE)
    _identify(incoming, number="met-1")
    _awaiting(incoming)
    reappearance_module.resolve_medical_replacement(incoming)
    summary = compute_summary("tudo")
    assert summary["total"] == 2
    assert summary["encerrados"] == 1
    assert summary["em_andamento"] == 1
    assert summary["agendados"] == 0
    assert summary["negados"] == 0


@_NEW_MODE
@pytest.mark.django_db
def test_technical_closures_excluded_from_all_procedure_counts(nir_user: User) -> None:
    """R5: três encerrados técnicos + mantido — tabela conta só o mantido."""
    for index in range(3):
        prior = Case.objects.create(created_by=nir_user)
        _declared(prior, ANGIO_TYPE)
        _identify(prior, number=f"met-t-{index}")
        _awaiting(prior)
        incoming = Case.objects.create(created_by=nir_user)
        _declared(incoming, ANGIO_TYPE)
        _identify(incoming, number=f"met-t-{index}")
        _awaiting(incoming)
        reappearance_module.resolve_medical_replacement(incoming)
    rows = {row["procedure_type"]: row for row in compute_by_procedure_type("tudo")}
    angio = rows[ANGIO_TYPE]
    assert angio["total"] == 3
    assert angio["approved"] == 0
    assert angio["denied"] == 0
    assert angio["pending"] == 3


@FLAG_OFF
@pytest.mark.django_db
def test_flag_off_keeps_exclusions_of_applied_links(nir_user: User) -> None:
    """R5: flag off — vínculos aplicados seguem excluídos das métricas."""
    with _NEW_MODE:
        prior = Case.objects.create(created_by=nir_user)
        _declared(prior, ANGIO_TYPE)
        _identify(prior, number="met-off-1")
        _awaiting(prior)
        incoming = Case.objects.create(created_by=nir_user)
        _declared(incoming, ANGIO_TYPE)
        _identify(incoming, number="met-off-1")
        _awaiting(incoming)
        assert reappearance_module.resolve_medical_replacement(incoming) is not None
    summary = compute_summary("tudo")
    assert summary["total"] == 2
    assert summary["encerrados"] == 1
    assert summary["em_andamento"] == 1
    rows = {row["procedure_type"]: row for row in compute_by_procedure_type("tudo")}
    assert rows[ANGIO_TYPE]["total"] == 1


@_NEW_MODE
@pytest.mark.django_db
def test_technical_case_invents_no_medical_outcome(nir_user: User) -> None:
    """R5: encerrado técnico não cria decisão/agendamento fictícios no resumo."""
    prior = Case.objects.create(created_by=nir_user)
    _declared(prior, ANGIO_TYPE)
    _identify(prior, number="met-f-1")
    _awaiting(prior)
    incoming = Case.objects.create(created_by=nir_user)
    _declared(incoming, ANGIO_TYPE)
    _identify(incoming, number="met-f-1")
    _awaiting(incoming)
    reappearance_module.resolve_medical_replacement(incoming)
    summary = compute_summary("tudo")
    assert summary["agendados"] == 0
    assert summary["negados"] == 0
    assert summary["administratively_closed"] == 0
