"""Terminal pendings must not be applied by normal entry (Unit C repair).

Design D2: terminal reasons keep ``state=pending`` only for audit/support
and are NOT authorization for automatic application or rescheduling.
``handle_reappearance_retry`` already guards them, but ``_apply_locked`` (shared
by ``resolve_medical_replacement`` and ``finalize_summarization``) reused the
same pair/revision/kind PENDING regardless of ``last_reason_code``. These
regressions pin the bypass closed in both entry paths, plus positive controls
proving nonterminal pending still applies the SAME link.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone
from django_q.models import Schedule

from apps.accounts.models import User
from apps.cases import reappearance as reappearance_module
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock, release_case_lock
from apps.cases.models import (
    Case,
    CaseEvent,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    ReappearanceState,
)

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
_NUMBER = "unit-c-repair-8842"
_TERMINAL_REASONS = (
    reappearance_module.REASON_RETRY_EXHAUSTED,
    reappearance_module.REASON_WINDOW_EXCEEDED,
    reappearance_module.REASON_STATE_INELIGIBLE,
)


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


def _identity(case: Case, *, number: str = _NUMBER) -> None:
    case.patient_name = "Maria da Silva"
    case.agency_record_number = number
    case.patient_cns = ""
    case.origin_unit = ""
    case.patient_age = None
    case.save(
        update_fields=[
            "patient_name",
            "agency_record_number",
            "patient_cns",
            "origin_unit",
            "patient_age",
        ]
    )


def _make_pair(owner: User) -> tuple[Case, Case]:
    prior = Case.objects.create(created_by=owner)
    _declared(prior, ANGIO_TYPE)
    _identity(prior)
    _awaiting(prior)
    Case.objects.filter(pk=prior.pk).update(created_at=timezone.now() - timedelta(days=5))
    prior.refresh_from_db()
    incoming = Case.objects.create(created_by=owner)
    _declared(incoming, ANGIO_TYPE)
    _identity(incoming)
    _awaiting(incoming)
    return prior, incoming


def _make_terminal_pending(owner: User, *, reason: str, username: str) -> CaseReappearance:
    """PENDING via real deferral (lease viva), then marked terminal, lease released."""
    prior, incoming = _make_pair(owner)
    doctor = User.objects.create_user(username=username, password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.PENDING
    CaseReappearance.objects.filter(pk=link.pk).update(last_reason_code=reason)
    link.refresh_from_db()
    prior.refresh_from_db()
    release_case_lock(prior, prior.lock_token)  # type: ignore[arg-type]
    return link


@NEW_MODE
@pytest.mark.django_db
@pytest.mark.parametrize("reason", _TERMINAL_REASONS)
def test_resolve_terminal_pending_does_not_apply_or_close(nir_user: User, reason: str) -> None:
    """Resolve direto com terminal sem lease viva: não aplica/não fecha/não cria link/agenda/evento."""
    link = _make_terminal_pending(nir_user, reason=reason, username=f"med-repair-{reason[:6]}")
    prior = Case.objects.get(pk=link.prior_case_id)
    incoming = Case.objects.get(pk=link.incoming_case_id)
    attempts_before = link.attempt_count
    first_before = link.first_attempt_at
    next_before = link.next_attempt_at
    revision_before = link.revision_digest
    wait_before = incoming.reappearance_wait_started_at
    events_before = CaseEvent.objects.count()
    schedules_before = Schedule.objects.count()

    result = reappearance_module.resolve_medical_replacement(incoming)

    # Retorno honesto: o vínculo ainda pendente (nunca um aplicado falso).
    assert result is not None
    assert result.pk == link.pk
    assert result.state == ReappearanceState.PENDING
    link.refresh_from_db()
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.last_reason_code == reason
    assert link.attempt_count == attempts_before
    assert link.first_attempt_at == first_before
    assert link.next_attempt_at == next_before
    assert link.revision_digest == revision_before
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.reappearance_wait_started_at == wait_before
    assert CaseReappearance.objects.count() == 1
    assert Schedule.objects.count() == schedules_before
    assert CaseEvent.objects.count() == events_before
    assert not incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()


@NEW_MODE
@pytest.mark.django_db
@pytest.mark.parametrize("reason", _TERMINAL_REASONS)
def test_finalize_terminal_pending_does_not_apply_or_close(nir_user: User, reason: str) -> None:
    """Final real com terminal prévio: completa o incoming, sem aplicar/fechar/link novo."""
    link = _make_terminal_pending(nir_user, reason=reason, username=f"med-repair-fin-{reason[:6]}")
    prior = Case.objects.get(pk=link.prior_case_id)
    incoming = Case.objects.get(pk=link.incoming_case_id)
    Case.objects.filter(pk=incoming.pk).update(status=CaseStatus.LLM_SUMMARIZING)
    incoming.refresh_from_db()
    attempts_before = link.attempt_count
    first_before = link.first_attempt_at
    revision_before = link.revision_digest
    wait_before = incoming.reappearance_wait_started_at
    schedules_before = Schedule.objects.count()

    outcome = reappearance_module.finalize_summarization(
        case_pk=incoming.pk,
        lock_token=uuid.uuid4(),
        marked_execution=False,
        execution_digests=None,
        execution_authority=(None, None, None),
        execution_declared=(),
        user=None,
        role=SYSTEM_ROLE,
    )

    # Sem FAILED falso causado pela guarda; o incoming completa normalmente.
    assert outcome.status == "completed"
    assert outcome.failure_reason is None
    incoming.refresh_from_db()
    prior.refresh_from_db()
    link.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert link.state == ReappearanceState.PENDING
    assert link.last_reason_code == reason
    assert link.attempt_count == attempts_before
    assert link.first_attempt_at == first_before
    assert link.revision_digest == revision_before
    assert incoming.reappearance_wait_started_at == wait_before
    assert CaseReappearance.objects.count() == 1
    assert Schedule.objects.count() == schedules_before
    # O final emite o evento proprio da sumarizacao; nunca os correlacionados
    # de aplicacao (applied/superseded) do vinculo terminal.
    assert not incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()


@NEW_MODE
@pytest.mark.django_db
def test_resolve_terminal_pending_with_live_lease_does_not_reschedule(
    nir_user: User,
) -> None:
    """_defer com terminal + lease viva: mesmo PENDING, sem agenda/orçamento novo."""
    prior, incoming = _make_pair(nir_user)
    doctor = User.objects.create_user(username="med-repair-live", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None and link.state == ReappearanceState.PENDING
    CaseReappearance.objects.filter(pk=link.pk).update(
        last_reason_code=reappearance_module.REASON_RETRY_EXHAUSTED
    )
    link.refresh_from_db()
    attempts_before = link.attempt_count
    next_before = link.next_attempt_at
    schedules_before = Schedule.objects.count()
    events_before = CaseEvent.objects.count()

    again = reappearance_module.resolve_medical_replacement(incoming)

    assert again is not None
    assert again.pk == link.pk
    assert again.state == ReappearanceState.PENDING
    link.refresh_from_db()
    prior.refresh_from_db()
    assert link.last_reason_code == reappearance_module.REASON_RETRY_EXHAUSTED
    assert link.attempt_count == attempts_before
    assert link.next_attempt_at == next_before
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert CaseReappearance.objects.count() == 1
    assert Schedule.objects.count() == schedules_before
    assert CaseEvent.objects.count() == events_before


@NEW_MODE
@pytest.mark.django_db
def test_nonterminal_pending_after_release_applies_same_link_via_resolve(
    nir_user: User,
) -> None:
    """Controle positivo: não-terminal após release aplica o MESMO vínculo via resolve."""
    prior, incoming = _make_pair(nir_user)
    doctor = User.objects.create_user(username="med-repair-pos-resolve", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None and link.state == ReappearanceState.PENDING
    prior.refresh_from_db()
    release_case_lock(prior, prior.lock_token)  # type: ignore[arg-type]

    applied = reappearance_module.resolve_medical_replacement(incoming)

    assert applied is not None
    assert applied.pk == link.pk
    assert applied.state == ReappearanceState.APPLIED
    assert CaseReappearance.objects.count() == 1
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).count() == 1


@NEW_MODE
@pytest.mark.django_db
def test_nonterminal_pending_after_release_applies_same_link_via_finalize(
    nir_user: User,
) -> None:
    """Controle positivo: não-terminal após release aplica o MESMO vínculo via finalize."""
    prior, incoming = _make_pair(nir_user)
    doctor = User.objects.create_user(username="med-repair-pos-fin", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None and link.state == ReappearanceState.PENDING
    prior.refresh_from_db()
    release_case_lock(prior, prior.lock_token)  # type: ignore[arg-type]
    Case.objects.filter(pk=incoming.pk).update(status=CaseStatus.LLM_SUMMARIZING)
    incoming.refresh_from_db()

    outcome = reappearance_module.finalize_summarization(
        case_pk=incoming.pk,
        lock_token=uuid.uuid4(),
        marked_execution=False,
        execution_digests=None,
        execution_authority=(None, None, None),
        execution_declared=(),
        user=None,
        role=SYSTEM_ROLE,
    )

    assert outcome.status == "completed"
    link.refresh_from_db()
    prior.refresh_from_db()
    assert link.state == ReappearanceState.APPLIED
    assert link.pk == link.pk
    assert CaseReappearance.objects.count() == 1
    assert prior.status == CaseStatus.CLEANED


@NEW_MODE
@pytest.mark.django_db
def test_applied_link_remains_idempotent_no_duplicate_effects(nir_user: User) -> None:
    """Controle positivo: aplicado permanece idempotente (sem efeito/evento duplicado)."""
    prior, incoming = _make_pair(nir_user)
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None and link.state == ReappearanceState.APPLIED
    events_before = CaseEvent.objects.count()

    again = reappearance_module.resolve_medical_replacement(incoming)

    assert again is not None
    assert again.pk == link.pk
    assert again.state == ReappearanceState.APPLIED
    assert CaseEvent.objects.count() == events_before
    assert CaseReappearance.objects.count() == 1
