"""Retry idempotente e limitado (S2 unidade C — R4.4–R4.9, AUD-04–06).

Reem uso do vínculo PENDING do mesmo par/revisão/kind, imutabilidade da
revisão (drift → ``state_ineligible`` sem reset), nomes/dedupe por
vínculo+revisão+tentativa, orçamento 24 tentativas ou 24h com terminais
que impedem retomada tardia, e falhas de agenda sem sucesso falso.
Preserva fix1 (par registrado) e fix2 (savepoint de agenda).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError
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
    ResolutionKind,
)

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
FLAG_OFF = override_settings(CASE_REAPPEARANCE_ENABLED=False)
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
_NUMBER = "unit-c-4471"


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


def _make_pending(owner: User, *, username: str = "med-unit-c") -> CaseReappearance:
    prior, incoming = _make_pair(owner)
    doctor = User.objects.create_user(username=username, password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.PENDING
    return link


def _due(link: CaseReappearance) -> CaseReappearance:
    CaseReappearance.objects.filter(pk=link.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    link.refresh_from_db()
    return link


@NEW_MODE
@pytest.mark.django_db
def test_pending_reentry_with_live_lease_reuses_same_link(nir_user: User) -> None:
    """R4.4: segunda resolução com lease viva reusa o PENDING, sem duplicar agenda."""
    link = _make_pending(nir_user)
    incoming = Case.objects.get(pk=link.incoming_case_id)
    attempts_before = link.attempt_count
    schedules_before = Schedule.objects.count()
    again = reappearance_module.resolve_medical_replacement(incoming)
    assert again is not None
    assert again.pk == link.pk
    assert CaseReappearance.objects.count() == 1
    assert Schedule.objects.count() == schedules_before
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.attempt_count == attempts_before
    prior = Case.objects.get(pk=link.prior_case_id)
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()


@NEW_MODE
@pytest.mark.django_db
def test_release_then_resolve_applies_same_link(nir_user: User) -> None:
    """R4.4: liberada a lease, resolve aplica o MESMO vínculo (sem INSERT novo)."""
    link = _make_pending(nir_user)
    prior = Case.objects.get(pk=link.prior_case_id)
    incoming = Case.objects.get(pk=link.incoming_case_id)
    release_case_lock(prior, prior.lock_token)  # type: ignore[arg-type]
    applied = reappearance_module.resolve_medical_replacement(incoming)
    assert applied is not None
    assert applied.pk == link.pk
    assert applied.state == ReappearanceState.APPLIED
    assert CaseReappearance.objects.count() == 1
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).count() == 1
    assert prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).count() == 1


@NEW_MODE
@pytest.mark.django_db
def test_applied_link_reentry_has_no_duplicate_effects(nir_user: User) -> None:
    """R4.4: vínculo aplicado retorna sem novo efeito/evento (guarda)."""
    prior, incoming = _make_pair(nir_user)
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None and link.state == ReappearanceState.APPLIED
    events_before = CaseEvent.objects.count()
    again = reappearance_module.resolve_medical_replacement(incoming)
    assert again is not None and again.pk == link.pk
    assert CaseEvent.objects.count() == events_before
    assert CaseReappearance.objects.count() == 1


@NEW_MODE
@pytest.mark.django_db
def test_final_reentry_with_preexisting_pending_reuses_link(nir_user: User) -> None:
    """R4.4/R4.9: final real com PENDING prévio não levanta nem marca FAILED."""
    prior, incoming = _make_pair(nir_user)
    # Incoming volta a LLM_SUMMARIZING para o seam final (setup do final).
    Case.objects.filter(pk=incoming.pk).update(status=CaseStatus.LLM_SUMMARIZING)
    incoming.refresh_from_db()
    revision = reappearance_module.revision_digest_for_pair(incoming, prior)
    doctor = User.objects.create_user(username="med-unit-c-final", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = CaseReappearance.objects.create(
        incoming_case=incoming,
        prior_case=prior,
        canonical_case=incoming,
        revision_digest=revision,
        evidence_code="occurrence_number",
        state=ReappearanceState.PENDING,
        resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
        attempt_count=1,
        first_attempt_at=timezone.now(),
        next_attempt_at=timezone.now() + timedelta(seconds=60),
        last_reason_code=reappearance_module.REASON_LEASE_BUSY,
    )
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
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert CaseReappearance.objects.count() == 1
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.attempt_count == 1


@NEW_MODE
@pytest.mark.django_db
def test_final_reentry_applies_same_link(nir_user: User) -> None:
    """R4.4: final real sem lease aplica o PENDING prévio (mesmo pk)."""
    prior, incoming = _make_pair(nir_user)
    Case.objects.filter(pk=incoming.pk).update(status=CaseStatus.LLM_SUMMARIZING)
    incoming.refresh_from_db()
    revision = reappearance_module.revision_digest_for_pair(incoming, prior)
    link = CaseReappearance.objects.create(
        incoming_case=incoming,
        prior_case=prior,
        canonical_case=incoming,
        revision_digest=revision,
        evidence_code="occurrence_number",
        state=ReappearanceState.PENDING,
        resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
        attempt_count=1,
        first_attempt_at=timezone.now(),
        next_attempt_at=timezone.now() - timedelta(seconds=1),
    )
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
    assert CaseReappearance.objects.count() == 1
    link.refresh_from_db()
    assert link.state == ReappearanceState.APPLIED
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED


@NEW_MODE
@pytest.mark.django_db
def test_revision_drift_marks_state_ineligible_without_reset(nir_user: User) -> None:
    """R4.5/AUD-05: identidade mudou → state_ineligible, sem retarget/reset/aplicar."""
    link = _make_pending(nir_user)
    prior = Case.objects.get(pk=link.prior_case_id)
    incoming = Case.objects.get(pk=link.incoming_case_id)
    release_case_lock(prior, prior.lock_token)  # type: ignore[arg-type]
    revision_before = link.revision_digest
    attempts_before = link.attempt_count
    first_before = link.first_attempt_at
    # Idade entra no digest mas mantém o par forte (prior sem idade é
    # compatível com qualquer idade informada) — drift de revisão puro.
    incoming.patient_age = 30
    incoming.save(update_fields=["patient_age"])
    _due(link)
    reappearance_module.handle_reappearance_retry(str(link.pk), revision_before, attempts_before)
    link.refresh_from_db()
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.revision_digest == revision_before
    assert link.attempt_count == attempts_before
    assert link.first_attempt_at == first_before
    assert link.last_reason_code == reappearance_module.REASON_STATE_INELIGIBLE
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert not incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()


@NEW_MODE
@pytest.mark.django_db
def test_schedule_name_contains_revision_hash_and_distinguishes_revisions(
    nir_user: User,
) -> None:
    """R4.6: nome da agenda inclui hash da revisão; revisões não colidem."""
    link = _make_pending(nir_user)
    revision_hash = hashlib.sha256(link.revision_digest.encode()).hexdigest()[:16]
    # Agenda real criada na deferral usa nome com vínculo+revisão+tentativa.
    schedule = Schedule.objects.get()
    assert revision_hash in schedule.name
    assert str(link.pk) in schedule.name
    assert schedule.func == reappearance_module.RETRY_FUNC
    assert schedule.cluster == reappearance_module.RETRY_CLUSTER
    assert schedule.schedule_type == Schedule.ONCE
    # Revisões distintas não compartilham nome (mesmo vínculo+tentativa).
    other_name = reappearance_module.retry_schedule_name(link.pk, 1, "v1:outra-revisao")
    assert schedule.name != other_name


@NEW_MODE
@pytest.mark.django_db
def test_schedule_for_other_revision_is_not_false_dedupe(nir_user: User) -> None:
    """R4.6: agenda da revisão antiga (mesma tentativa) não é dedupe da nova."""
    link = _make_pending(nir_user)
    # Simula outra revisão para o mesmo vínculo+tentativa: agenda antiga
    # existe com nome da revisão antiga; nova revisão agenda a sua própria.
    old_revision = link.revision_digest
    old_name = Schedule.objects.get().name
    old_revision_hash = hashlib.sha256(old_revision.encode()).hexdigest()[:16]
    assert old_revision_hash in old_name
    CaseReappearance.objects.filter(pk=link.pk).update(
        revision_digest="v1:" + "a" * 64, attempt_count=0
    )
    link.refresh_from_db()
    assert link.revision_digest != old_revision
    schedules_before = Schedule.objects.count()
    result = reappearance_module.schedule_next_reappearance_retry(link)
    assert result is True
    assert Schedule.objects.count() == schedules_before + 1
    link.refresh_from_db()
    new_names = set(Schedule.objects.values_list("name", flat=True))
    assert len(new_names) == 2
    assert old_name in new_names
    new_revision_hash = hashlib.sha256(link.revision_digest.encode()).hexdigest()[:16]
    assert any(new_revision_hash in name for name in new_names if name != old_name)


@NEW_MODE
@pytest.mark.django_db
def test_exhausted_then_release_then_duplicate_job_is_noop(nir_user: User) -> None:
    """R4.7: esgotado → libera lease → job duplicado NÃO aplica/reagenda/duplica evento."""
    link = _make_pending(nir_user)
    prior = Case.objects.get(pk=link.prior_case_id)
    incoming = Case.objects.get(pk=link.incoming_case_id)
    CaseReappearance.objects.filter(pk=link.pk).update(
        attempt_count=reappearance_module.RETRY_MAX_ATTEMPTS,
        first_attempt_at=timezone.now() - timedelta(hours=1),
        next_attempt_at=timezone.now() - timedelta(minutes=1),
    )
    link.refresh_from_db()
    revision = link.revision_digest
    attempt = link.attempt_count
    reappearance_module.handle_reappearance_retry(str(link.pk), revision, attempt)
    link.refresh_from_db()
    assert link.last_reason_code == reappearance_module.REASON_RETRY_EXHAUSTED
    release_case_lock(prior, prior.lock_token)  # type: ignore[arg-type]
    events_before = CaseEvent.objects.count()
    schedules_before = Schedule.objects.count()
    reappearance_module.handle_reappearance_retry(str(link.pk), revision, attempt)
    link.refresh_from_db()
    prior.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.last_reason_code == reappearance_module.REASON_RETRY_EXHAUSTED
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert CaseEvent.objects.count() == events_before
    assert Schedule.objects.count() == schedules_before
    assert not incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()


@NEW_MODE
@pytest.mark.django_db
def test_window_exactly_24h_is_exceeded(nir_user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """R4.7: >=24h desde first_attempt_at esgota (limite atingido primeiro)."""
    link = _make_pending(nir_user)
    prior = Case.objects.get(pk=link.prior_case_id)
    release_case_lock(prior, prior.lock_token)  # type: ignore[arg-type]
    fixed_now = timezone.now()
    monkeypatch.setattr("django.utils.timezone.now", lambda: fixed_now)
    CaseReappearance.objects.filter(pk=link.pk).update(
        first_attempt_at=fixed_now - timedelta(hours=24),
        next_attempt_at=fixed_now - timedelta(seconds=1),
    )
    link.refresh_from_db()
    reappearance_module.handle_reappearance_retry(
        str(link.pk), link.revision_digest, link.attempt_count
    )
    link.refresh_from_db()
    prior.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.last_reason_code == reappearance_module.REASON_WINDOW_EXCEEDED
    assert prior.status == CaseStatus.AWAITING_DOCTOR


@NEW_MODE
@pytest.mark.django_db
def test_window_just_under_24h_still_applies(nir_user: User) -> None:
    """R4.7 guarda: 23h59m ainda elegível — aplica após release."""
    link = _make_pending(nir_user)
    prior = Case.objects.get(pk=link.prior_case_id)
    release_case_lock(prior, prior.lock_token)  # type: ignore[arg-type]
    CaseReappearance.objects.filter(pk=link.pk).update(
        first_attempt_at=timezone.now() - timedelta(hours=23, minutes=59),
        next_attempt_at=timezone.now() - timedelta(seconds=1),
    )
    link.refresh_from_db()
    reappearance_module.handle_reappearance_retry(
        str(link.pk), link.revision_digest, link.attempt_count
    )
    link.refresh_from_db()
    assert link.state == ReappearanceState.APPLIED


@NEW_MODE
@pytest.mark.django_db
def test_early_job_is_noop_without_budget_consumption(nir_user: User) -> None:
    """R4.8: job adiantado (next_attempt futuro) não consome orçamento."""
    link = _make_pending(nir_user)
    attempts_before = link.attempt_count
    events_before = CaseEvent.objects.count()
    schedules_before = Schedule.objects.count()
    # next_attempt_at futuro (1h) — job chega antes da hora.
    assert link.next_attempt_at is not None and link.next_attempt_at > timezone.now()
    reappearance_module.handle_reappearance_retry(
        str(link.pk), link.revision_digest, link.attempt_count
    )
    link.refresh_from_db()
    assert link.attempt_count == attempts_before
    assert link.state == ReappearanceState.PENDING
    assert CaseEvent.objects.count() == events_before
    assert Schedule.objects.count() == schedules_before


@NEW_MODE
@pytest.mark.django_db
def test_schedule_integrity_error_without_durable_schedule_is_not_dedupe(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.9: IntegrityError sem agenda durável NÃO é dedupe — falha observável."""
    link = _make_pending(nir_user)
    incoming = Case.objects.get(pk=link.incoming_case_id)

    def _integrity(*args: object, **kwargs: object) -> object:
        raise IntegrityError("duplicate key value violates unique constraint (same name)")

    monkeypatch.setattr("django_q.tasks.schedule", _integrity)
    CaseReappearance.objects.filter(pk=link.pk).update(attempt_count=1)
    link.refresh_from_db()
    with pytest.raises(IntegrityError):
        reappearance_module.schedule_next_reappearance_retry(link)
    link.refresh_from_db()
    # Sem Schedule falso para a tentativa nova (só a agenda da tentativa 1).
    assert Schedule.objects.count() == 1
    incoming.refresh_from_db()


@NEW_MODE
@pytest.mark.django_db
def test_integrity_error_with_other_revision_durable_is_not_dedupe(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.6/R4.9: durável de OUTRA revisão não prova dedupe da tentativa atual."""
    link = _make_pending(nir_user)

    def _integrity(*args: object, **kwargs: object) -> object:
        raise IntegrityError("duplicate key value violates unique constraint (same name)")

    monkeypatch.setattr("django_q.tasks.schedule", _integrity)
    # Agenda durável antiga (formato sem hash de revisão) da tentativa 2.
    Schedule.objects.create(
        name=reappearance_module.retry_schedule_name(link.pk, 2),
        func=reappearance_module.RETRY_FUNC,
        schedule_type=Schedule.ONCE,
        next_run=timezone.now() + timedelta(seconds=60),
        cluster=reappearance_module.RETRY_CLUSTER,
        args=repr((str(link.pk), "v1:outra-revisao", 2)),
        kwargs="{}",
    )
    CaseReappearance.objects.filter(pk=link.pk).update(attempt_count=1)
    link.refresh_from_db()
    with pytest.raises(IntegrityError):
        reappearance_module.schedule_next_reappearance_retry(link)


@FLAG_OFF
@pytest.mark.django_db
def test_feature_off_retry_consumes_no_budget(nir_user: User) -> None:
    """R4.8/R5.3: flag off — retry não consome orçamento nem aplica."""
    with NEW_MODE:
        link = _make_pending(nir_user)
        link_pk, revision, attempt = link.pk, link.revision_digest, link.attempt_count
        _due(link)
    events_before = CaseEvent.objects.count()
    reappearance_module.handle_reappearance_retry(str(link_pk), revision, attempt)
    link.refresh_from_db()
    assert link.attempt_count == attempt
    assert link.state == ReappearanceState.PENDING
    assert CaseEvent.objects.count() == events_before
