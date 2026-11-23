"""Unit F — métricas honestas e flag off (R5.2/R5.3/AUD-10d).

Técnicos (evento SUPERSEDED real) fora de Em andamento e de TODAS as
contagens por procedimento; decisões reais do mantido seguem contadas
(approved/denied); tempo médio usa só decisões reais; flag off impede novos
efeitos/retries/comando sem apagar histórico, métricas ou leitura.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.cases import reappearance as reappearance_module
from apps.cases.locks import claim_case_lock
from apps.cases.models import Case, CaseProcedure, CaseStatus, DoctorDisposition
from apps.dashboard.metrics import (
    compute_avg_time_to_decision,
    compute_by_procedure_type,
    compute_summary,
)

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
FLAG_OFF = override_settings(CASE_REAPPEARANCE_ENABLED=False)
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
OTHER_TYPE = "cat_cardiaco"


@pytest.fixture
def nir_user() -> User:
    role, _ = Role.objects.get_or_create(name="nir")
    user = User.objects.create_user(username="nir-reap-met-f", password="senha-teste")
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


def _make_pair(owner: User, *, number: str) -> tuple[Case, Case]:
    prior = Case.objects.create(created_by=owner)
    _declared(prior, ANGIO_TYPE)
    _identify(prior, number=number)
    _awaiting(prior)
    incoming = Case.objects.create(created_by=owner)
    _declared(incoming, ANGIO_TYPE)
    _identify(incoming, number=number)
    _awaiting(incoming)
    return prior, incoming


@NEW_MODE
@pytest.mark.django_db
def test_maintained_real_decisions_counted_technical_excluded(nir_user: User) -> None:
    """R5.2: approved/denied reais do mantido contam; técnico fora de tudo."""
    prior, incoming = _make_pair(nir_user, number="met-f-real-1")
    assert reappearance_module.resolve_medical_replacement(incoming) is not None
    kept = Case.objects.create(created_by=nir_user)
    _declared(kept, ANGIO_TYPE, OTHER_TYPE)
    rows = {row.procedure_type: row for row in kept.procedures.all()}
    rows[ANGIO_TYPE].doctor_disposition = DoctorDisposition.APPROVED
    rows[ANGIO_TYPE].doctor_decided_at = timezone.now()
    rows[ANGIO_TYPE].save(update_fields=["doctor_disposition", "doctor_decided_at"])
    rows[OTHER_TYPE].doctor_disposition = DoctorDisposition.DENIED
    rows[OTHER_TYPE].doctor_decided_at = timezone.now()
    rows[OTHER_TYPE].save(update_fields=["doctor_disposition", "doctor_decided_at"])
    by_type = {row["procedure_type"]: row for row in compute_by_procedure_type("tudo")}
    # Angio: incoming mantido (pending) + kept (approved); prior técnico fora.
    assert by_type[ANGIO_TYPE]["total"] == 2
    assert by_type[ANGIO_TYPE]["approved"] == 1
    assert by_type[ANGIO_TYPE]["denied"] == 0
    assert by_type[ANGIO_TYPE]["pending"] == 1
    assert by_type[OTHER_TYPE]["total"] == 1
    assert by_type[OTHER_TYPE]["denied"] == 1
    summary = compute_summary("tudo")
    assert summary["total"] == 3
    assert summary["encerrados"] == 1
    assert summary["em_andamento"] == 2


@NEW_MODE
@pytest.mark.django_db
def test_avg_time_uses_only_real_kept_decisions(nir_user: User) -> None:
    """R5.2: tempo médio ignora técnico sem decisão e usa só decisões reais."""
    prior, incoming = _make_pair(nir_user, number="met-f-avg-1")
    assert reappearance_module.resolve_medical_replacement(incoming) is not None
    kept = Case.objects.create(created_by=nir_user)
    decision = timezone.now()
    Case.objects.filter(pk=kept.pk).update(created_at=decision - timedelta(hours=4))
    CaseProcedure.objects.create(
        case=kept,
        procedure_type=ANGIO_TYPE,
        declared_by_nir=True,
        doctor_disposition=DoctorDisposition.APPROVED,
        doctor_decided_at=decision,
    )
    assert compute_avg_time_to_decision("tudo") == timedelta(hours=4)


@NEW_MODE
@pytest.mark.django_db
def test_avg_time_none_with_only_technical_pending(nir_user: User) -> None:
    """R5.2: só técnico pendente + mantido sem decisão → média None (sem invenção)."""
    prior, incoming = _make_pair(nir_user, number="met-f-avg-2")
    assert reappearance_module.resolve_medical_replacement(incoming) is not None
    assert compute_avg_time_to_decision("tudo") is None


@FLAG_OFF
@pytest.mark.django_db
def test_flag_off_blocks_new_effects_retry_budget_and_command(nir_user: User) -> None:
    """R5.3: flag off — sem resolução/retry/orçamento/comando; histórico mantido."""
    with NEW_MODE:
        prior, incoming = _make_pair(nir_user, number="met-f-off-1")
        doctor = User.objects.create_user(username="med-f-off", password="x")
        claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
        link = reappearance_module.resolve_medical_replacement(incoming)
        assert link is not None
        link_pk, revision, attempt = link.pk, link.revision_digest, link.attempt_count
        applied_prior, applied_incoming = _make_pair(nir_user, number="met-f-off-2")
        assert reappearance_module.resolve_medical_replacement(applied_incoming) is not None
    # Revisão obsoleta do pending acima é inelegível em qualquer modo; aqui o
    # ponto é a flag: retry não consome orçamento nem agenda.
    reappearance_module.handle_reappearance_retry(str(link_pk), revision, attempt)
    link.refresh_from_db()
    assert link.attempt_count == attempt
    # Comando --apply recusa com flag off (exit real 1 via CommandError).
    with pytest.raises(CommandError):
        call_command("reenqueue_pending_reappearances", str(link_pk), "--apply")
    link.refresh_from_db()
    assert link.attempt_count == attempt
    # Histórico/métricas/leitura preservados com flag off.
    assert reappearance_module.current_representative(applied_prior).pk == applied_incoming.pk
    links = reappearance_module.historical_reappearance_links(applied_prior)
    assert links.get("immediate") is not None
    summary = compute_summary("tudo")
    assert summary["total"] >= 4
    assert summary["encerrados"] >= 1
    by_type = {row["procedure_type"]: row for row in compute_by_procedure_type("tudo")}
    # Técnicos aplicados seguem excluídos mesmo com a flag desligada:
    # 3 rows angio contam (pending1, incoming1, incoming2); o prior aplicado fora.
    assert by_type[ANGIO_TYPE]["total"] == 3
