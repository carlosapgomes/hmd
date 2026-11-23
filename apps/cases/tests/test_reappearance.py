"""Substituição médica por reapresentação (slice 002, R1–R5 — RED).

Cobre o contrato S2 ponta a ponta no nível de serviço/modelo/FSM/eventos:
vínculo exclusivo de fila compatível com contexto histórico (R1), jamais sem
novo pronto (R2 — integração real via orquestrador em
``apps/pipeline/tests/test_reappearance_orchestrator.py``), espera herdada e
cadeia imutável (R3 — fila/HTML em ``apps/doctor/tests/test_reappearance_wait.py``),
atomic final mutex→grupo ordenado→revalidação com corridas PG reais e retry
limitado no cluster llm (R4) e disposição/métricas com flag off preservando
exclusões (R5 — métricas em ``apps/dashboard/tests/test_reappearance_metrics.py``).
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django_q.models import Schedule

from apps.accounts.models import User
from apps.cases import reappearance as reappearance_module
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock
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
OTHER_TYPE = "cat_cardiaco"
_CNS_A = "207139200176053"

_AGO_5D = timedelta(days=5)


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
    name: str = "Maria da Silva",
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


def _make_pair(
    owner: User,
    *,
    prior_days_ago: int = 5,
    procedures: tuple[str, ...] = (ANGIO_TYPE,),
    number: str = "123",
    cns: str = "",
) -> tuple[Case, Case]:
    """Original aguardando + novo aguardando, identidade forte, original envelhecido."""
    prior = Case.objects.create(created_by=owner)
    _declared(prior, *procedures)
    _identity(prior, number=number, cns=cns)
    _awaiting(prior)
    Case.objects.filter(pk=prior.pk).update(
        created_at=timezone.now() - timedelta(days=prior_days_ago)
    )
    prior.refresh_from_db()
    incoming = Case.objects.create(created_by=owner)
    _declared(incoming, *procedures)
    _identity(incoming, number=number, cns=cns)
    _awaiting(incoming)
    return prior, incoming


def _event_types(case: Case) -> list[str]:
    return list(case.events.order_by("id").values_list("event_type", flat=True))


# ── R1: vínculo/marco aditivos, FSM, eventos, trilha ───────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_close_as_reappearance_from_awaiting_doctor(nir_user: User) -> None:
    """R1: operação FSM nomeada encerra original AWAITING_DOCTOR em CLEANED."""
    case = Case.objects.create(created_by=nir_user)
    _declared(case, ANGIO_TYPE)
    _awaiting(case)
    case.close_as_reappearance(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.CLEANED
    assert CaseEventType.CASE_STATUS_CLEANED in _event_types(case)


@NEW_MODE
@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [
        CaseStatus.NEW,
        CaseStatus.LLM_EXTRACTING,
        CaseStatus.LLM_SUMMARIZING,
        CaseStatus.DOCTOR_DENIED,
        CaseStatus.FINAL_REPLY_POSTED,
        CaseStatus.CLEANED,
    ],
)
def test_close_as_reappearance_refuses_other_sources(nir_user: User, status: CaseStatus) -> None:
    """R1: transição sistêmica só parte de AWAITING_DOCTOR (sem estados novos)."""
    from django_fsm import TransitionNotAllowed

    case = Case.objects.create(created_by=nir_user)
    Case.objects.filter(pk=case.pk).update(status=status)
    case.refresh_from_db()
    with pytest.raises(TransitionNotAllowed):
        case.close_as_reappearance(user=None, role=SYSTEM_ROLE)


@NEW_MODE
@pytest.mark.django_db
def test_resolve_creates_applied_link_and_two_correlated_events(nir_user: User) -> None:
    """R1: vínculo aplicado + dois eventos correlacionados (UUIDs/código, sem PII)."""
    prior, incoming = _make_pair(nir_user)
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.APPLIED
    assert link.resolution_kind == ResolutionKind.MEDICAL_REPLACEMENT
    assert link.canonical_case_id == incoming.pk
    assert link.attempt_count == 0
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    applied = incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).first()
    superseded = prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).first()
    assert applied is not None
    assert superseded is not None
    assert applied.payload["reappearance_id"] == str(link.pk)
    assert superseded.payload["reappearance_id"] == str(link.pk)
    assert applied.payload["prior_case_id"] == str(prior.pk)
    assert superseded.payload["incoming_case_id"] == str(incoming.pk)
    assert applied.payload["resolution_kind"] == ResolutionKind.MEDICAL_REPLACEMENT.value
    assert applied.payload["evidence_code"] == "occurrence_number"
    for event in (applied, superseded):
        blob = str(event.payload)
        assert "Maria" not in blob
        assert _CNS_A not in blob
        assert set(event.payload) <= {
            "reappearance_id",
            "prior_case_id",
            "incoming_case_id",
            "evidence_code",
            "resolution_kind",
            "revision_digest",
        }


@NEW_MODE
@pytest.mark.django_db
def test_pair_unique_per_revision_and_kind(nir_user: User) -> None:
    """R1: unique(incoming, prior, revision_digest, resolution_kind)."""
    prior, incoming = _make_pair(nir_user)
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CaseReappearance.objects.create(
                incoming_case=incoming,
                prior_case=prior,
                revision_digest=link.revision_digest,
                evidence_code=link.evidence_code,
                state=ReappearanceState.PENDING,
                resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
            )


@NEW_MODE
@pytest.mark.django_db
def test_single_applied_queue_resolution_per_incoming_coexists_with_context(
    nir_user: User,
) -> None:
    """R1: UMA resolução de FILA aplicada por incoming; prior_context coexiste."""
    prior, incoming = _make_pair(nir_user)
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    context_link = CaseReappearance.objects.create(
        incoming_case=incoming,
        prior_case=prior,
        revision_digest=link.revision_digest,
        evidence_code=link.evidence_code,
        state=ReappearanceState.APPLIED,
        resolution_kind=ResolutionKind.PRIOR_CONTEXT,
    )
    assert context_link.pk is not None
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CaseReappearance.objects.create(
                incoming_case=incoming,
                prior_case=prior,
                revision_digest="v1:other",
                evidence_code=link.evidence_code,
                state=ReappearanceState.APPLIED,
                resolution_kind=ResolutionKind.SCHEDULING_REPEAT,
            )


@NEW_MODE
@pytest.mark.django_db
def test_incoming_cannot_be_prior(nir_user: User) -> None:
    """R1: incoming nunca é o próprio prior (check)."""
    case = Case.objects.create(created_by=nir_user)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CaseReappearance.objects.create(
                incoming_case=case,
                prior_case=case,
                revision_digest="v1:x",
                evidence_code="occurrence_number",
                state=ReappearanceState.PENDING,
                resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
            )


@NEW_MODE
@pytest.mark.django_db
def test_chain_links_are_immutable_with_current_representative(
    nir_user: User,
) -> None:
    """R1: cadeia A→B→C preserva elos; representante atual resolve com guard de ciclo."""
    prior_a, incoming_b = _make_pair(nir_user, number="777")
    link_ab = reappearance_module.resolve_medical_replacement(incoming_b)
    assert link_ab is not None
    incoming_c = Case.objects.create(created_by=nir_user)
    _declared(incoming_c, ANGIO_TYPE)
    _identity(incoming_c, number="777")
    _awaiting(incoming_c)
    link_bc = reappearance_module.resolve_medical_replacement(incoming_c)
    assert link_bc is not None
    assert link_bc.prior_case_id == incoming_b.pk
    link_ab.refresh_from_db()
    assert link_ab.prior_case_id == prior_a.pk
    assert link_ab.incoming_case_id == incoming_b.pk
    assert reappearance_module.current_representative(prior_a).pk == incoming_c.pk
    assert reappearance_module.current_representative(incoming_b).pk == incoming_c.pk
    assert reappearance_module.current_representative(incoming_c).pk == incoming_c.pk


@NEW_MODE
@pytest.mark.django_db
def test_legacy_cases_fall_back_to_created_at(nir_user: User) -> None:
    """R1: marco nulo (legado) → espera efetiva é o created_at."""
    case = Case.objects.create(created_by=nir_user)
    assert case.reappearance_wait_started_at is None
    assert reappearance_module.effective_wait_started_at(case) == case.created_at


# ── R2: só com novo pronto ─────────────────────────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_weak_pair_never_closes_original(nir_user: User) -> None:
    """R2: par fraco (nome+origem) não encerra — sem confirmação (S5) não há efeito."""
    prior = Case.objects.create(created_by=nir_user)
    _declared(prior, ANGIO_TYPE)
    _identity(prior, number="", origin="Hospital Geral")
    _awaiting(prior)
    incoming = Case.objects.create(created_by=nir_user)
    _declared(incoming, ANGIO_TYPE)
    _identity(incoming, number="", origin="Hospital Geral")
    _awaiting(incoming)
    assert reappearance_module.resolve_medical_replacement(incoming) is None
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()


@NEW_MODE
@pytest.mark.django_db
def test_type_change_never_closes_original(nir_user: User) -> None:
    """R2: conjunto efetivo distinto nunca se funde, nem por CNS."""
    prior, _ = _make_pair(nir_user, procedures=(ANGIO_TYPE,), cns=_CNS_A)
    incoming = Case.objects.create(created_by=nir_user)
    _declared(incoming, OTHER_TYPE)
    _identity(incoming, number="123", cns=_CNS_A)
    _awaiting(incoming)
    assert reappearance_module.resolve_medical_replacement(incoming) is None
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR


@NEW_MODE
@pytest.mark.django_db
def test_ambiguous_candidates_never_close(nir_user: User) -> None:
    """R2: dois originais fortes plausíveis impedem a automação."""
    prior1 = Case.objects.create(created_by=nir_user)
    _declared(prior1, ANGIO_TYPE)
    _identity(prior1, number="555")
    _awaiting(prior1)
    Case.objects.filter(pk=prior1.pk).update(created_at=timezone.now() - timedelta(days=2))
    prior1.refresh_from_db()
    prior2 = Case.objects.create(created_by=nir_user)
    _declared(prior2, ANGIO_TYPE)
    _identity(prior2, number="555")
    _awaiting(prior2)
    Case.objects.filter(pk=prior2.pk).update(created_at=timezone.now() - timedelta(days=1))
    prior2.refresh_from_db()
    incoming = Case.objects.create(created_by=nir_user)
    _declared(incoming, ANGIO_TYPE)
    _identity(incoming, number="555")
    _awaiting(incoming)
    assert reappearance_module.resolve_medical_replacement(incoming) is None
    prior1.refresh_from_db()
    prior2.refresh_from_db()
    assert prior1.status == CaseStatus.AWAITING_DOCTOR
    assert prior2.status == CaseStatus.AWAITING_DOCTOR


@NEW_MODE
@pytest.mark.django_db
def test_decided_original_is_not_overwritten(nir_user: User) -> None:
    """R2/R4: original decidido antes da conclusão do novo — sem encerramento."""
    prior, incoming_new = _make_pair(nir_user)
    prior.record_doctor_decision(accepted=False, user=None, role=SYSTEM_ROLE)
    assert prior.status == CaseStatus.DOCTOR_DENIED
    assert reappearance_module.resolve_medical_replacement(incoming_new) is None
    prior.refresh_from_db()
    assert prior.status == CaseStatus.DOCTOR_DENIED
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()


# ── R3: espera herdada ─────────────────────────────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_new_inherits_oldest_chain_marker_without_touching_created_at(
    nir_user: User,
) -> None:
    """R3: novo herda menor marco da cadeia; created_at intacto."""
    prior, incoming = _make_pair(nir_user, prior_days_ago=5)
    prior_created = prior.created_at
    incoming_created = incoming.created_at
    reappearance_module.resolve_medical_replacement(incoming)
    incoming.refresh_from_db()
    assert incoming.reappearance_wait_started_at == prior_created
    assert incoming.created_at == incoming_created
    assert reappearance_module.effective_wait_started_at(incoming) == prior_created


@NEW_MODE
@pytest.mark.django_db
def test_three_link_chain_inherits_first_marker(nir_user: User) -> None:
    """R3: cadeia de três — C exibe espera desde A."""
    prior_a, incoming_b = _make_pair(nir_user, number="999", prior_days_ago=9)
    reappearance_module.resolve_medical_replacement(incoming_b)
    incoming_b.refresh_from_db()
    first_marker = reappearance_module.effective_wait_started_at(incoming_b)
    assert first_marker == prior_a.created_at
    incoming_c = Case.objects.create(created_by=nir_user)
    _declared(incoming_c, ANGIO_TYPE)
    _identity(incoming_c, number="999")
    _awaiting(incoming_c)
    reappearance_module.resolve_medical_replacement(incoming_c)
    incoming_c.refresh_from_db()
    assert incoming_c.reappearance_wait_started_at == first_marker
    assert incoming_c.created_at != first_marker


# ── R4: atomic, leases, retry ──────────────────────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_live_human_lease_defers_without_forcing(nir_user: User) -> None:
    """R4: reserva viva no original — sem tomada; pendência + retry agendado."""
    prior, incoming = _make_pair(nir_user)
    doctor = User.objects.create_user(username="med-reserva", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.PENDING
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()
    link.refresh_from_db()
    assert link.attempt_count == 1
    assert link.first_attempt_at is not None
    assert link.next_attempt_at is not None
    assert link.last_reason_code == "lease_busy"
    assert Schedule.objects.filter(
        name=reappearance_module.retry_schedule_name(link.pk, 1, link.revision_digest),
        func=reappearance_module.RETRY_FUNC,
        cluster=reappearance_module.RETRY_CLUSTER,
    ).exists()


@NEW_MODE
@pytest.mark.django_db
def test_expired_lease_applies_immediately(
    nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    """R4: lease expirada não bloqueia — aplica e limpa o resíduo."""
    prior, incoming = _make_pair(nir_user)
    doctor = user_factory("med-expirado", "doctor")
    lock = claim_case_lock(
        prior, user=doctor, context="doctor_decision", role="doctor", lease_seconds=-1
    )
    assert lock.locked_until < timezone.now()
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.APPLIED
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert prior.lock_token is None


@NEW_MODE
@pytest.mark.django_db
def test_retry_applies_after_lease_release(nir_user: User) -> None:
    """R4: retomada — liberada a reserva, próxima tentativa aplica."""
    prior, incoming = _make_pair(nir_user)
    doctor = User.objects.create_user(username="med-retoma", password="x")
    lock = claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None and link.state == ReappearanceState.PENDING
    from apps.cases.locks import release_case_lock

    release_case_lock(prior, lock.token)
    CaseReappearance.objects.filter(pk=link.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    link.refresh_from_db()
    reappearance_module.handle_reappearance_retry(
        str(link.pk), link.revision_digest, link.attempt_count
    )
    link.refresh_from_db()
    assert link.state == ReappearanceState.APPLIED
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED


@NEW_MODE
@pytest.mark.django_db
def test_duplicate_retry_job_is_noop(nir_user: User) -> None:
    """R4: job duplicado/atrasado (tentativa divergente) não consome orçamento."""
    prior, incoming = _make_pair(nir_user)
    doctor = User.objects.create_user(username="med-duplo", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None and link.state == ReappearanceState.PENDING
    attempts_before = link.attempt_count
    events_before = CaseEvent.objects.count()
    schedules_before = Schedule.objects.count()
    reappearance_module.handle_reappearance_retry(
        str(link.pk), link.revision_digest, link.attempt_count + 99
    )
    link.refresh_from_db()
    assert link.attempt_count == attempts_before
    assert link.state == ReappearanceState.PENDING
    assert CaseEvent.objects.count() == events_before
    assert Schedule.objects.count() == schedules_before


@NEW_MODE
@pytest.mark.django_db
def test_stale_revision_job_is_noop(nir_user: User) -> None:
    """R4: job com revisão divergente não aplica nem consome orçamento."""
    prior, incoming = _make_pair(nir_user)
    doctor = User.objects.create_user(username="med-rev", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None and link.state == ReappearanceState.PENDING
    attempts_before = link.attempt_count
    reappearance_module.handle_reappearance_retry(
        str(link.pk), "v1:revisao-antiga", link.attempt_count
    )
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.attempt_count == attempts_before


@NEW_MODE
@pytest.mark.django_db
def test_retry_budget_exhaustion_emits_single_technical_event(
    nir_user: User,
) -> None:
    """R4: 24 tentativas/24h — esgota com evento técnico único, sem encerrar."""
    prior, incoming = _make_pair(nir_user)
    doctor = User.objects.create_user(username="med-esgota", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None and link.state == ReappearanceState.PENDING
    CaseReappearance.objects.filter(pk=link.pk).update(
        attempt_count=reappearance_module.RETRY_MAX_ATTEMPTS,
        first_attempt_at=timezone.now() - timedelta(hours=1),
        next_attempt_at=timezone.now() - timedelta(minutes=1),
    )
    link.refresh_from_db()
    reappearance_module.handle_reappearance_retry(
        str(link.pk), link.revision_digest, link.attempt_count
    )
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.last_reason_code == "retry_exhausted"
    exhausted = incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_RETRY_EXHAUSTED)
    assert exhausted.count() == 1
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    second_count = exhausted.count()
    reappearance_module.handle_reappearance_retry(
        str(link.pk), link.revision_digest, link.attempt_count
    )
    assert (
        incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_RETRY_EXHAUSTED).count()
        == second_count
    )


@NEW_MODE
@pytest.mark.django_db
def test_retry_backoff_is_capped_exponential() -> None:
    """R4/D6: 60s duplicado por tentativa, teto 3600s."""
    delays = [reappearance_module.compute_retry_delay_seconds(attempt) for attempt in (1, 2, 3, 7)]
    assert delays == [60, 120, 240, 3600]
    assert reappearance_module.compute_retry_delay_seconds(24) == 3600


@NEW_MODE
@pytest.mark.django_db
def test_schedule_dispatch_failure_keeps_pending_without_false_success(
    nir_user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """R4/D6: falha de agenda — pendência observável, sem sucesso falso, sem 500."""
    import apps.cases.reappearance as module

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("broker indisponível")

    monkeypatch.setattr("django_q.tasks.schedule", _boom)
    prior, incoming = _make_pair(nir_user)
    doctor = User.objects.create_user(username="med-agenda", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    with caplog.at_level("WARNING"):
        link = module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.PENDING
    assert Schedule.objects.count() == 0
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()
    assert any("reappearance" in record.getMessage().lower() for record in caplog.records)


@NEW_MODE
@pytest.mark.django_db
def test_release_case_lock_never_resolves(
    nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    """R4: D6 proíbe resolução a partir do release — pin direto."""
    from apps.cases.locks import release_case_lock

    prior, incoming = _make_pair(nir_user)
    doctor = user_factory("med-release", "doctor")
    lock = claim_case_lock(incoming, user=doctor, context="doctor_decision", role="doctor")
    release_case_lock(incoming, lock.token)
    assert not CaseReappearance.objects.exists()
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR


# ── R4: corridas PG reais ──────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_apply_has_single_winner(nir_user: User) -> None:
    """R4: dois resolvedores concorrentes — um aplicado, sem duplicar contagem."""
    prior = Case.objects.create(created_by=nir_user)
    _declared(prior, ANGIO_TYPE)
    _identity(prior, number="conc-1")
    _awaiting(prior)
    incoming = Case.objects.create(created_by=nir_user)
    _declared(incoming, ANGIO_TYPE)
    _identity(incoming, number="conc-1")
    _awaiting(incoming)
    incoming_pk = incoming.pk
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def _worker() -> None:
        from django.db import connections
        from django.test import override_settings as _override_settings

        try:
            barrier.wait(timeout=30)
            with _override_settings(
                CASE_REAPPEARANCE_ENABLED=True,
                CASE_REAPPEARANCE_WINDOW_DAYS=15,
            ):
                case = Case.objects.get(pk=incoming_pk)
                link = reappearance_module.resolve_medical_replacement(case)
                outcomes.append(link.state if link is not None else "none")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads), "deadlock na resolução"
    assert not errors
    assert (
        CaseReappearance.objects.filter(
            incoming_case_id=incoming_pk,
            state=ReappearanceState.APPLIED,
            resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
        ).count()
        == 1
    )
    assert (
        CaseEvent.objects.filter(
            case_id=prior.pk, event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED
        ).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_two_incomings_sharing_prior_without_deadlock(
    nir_user: User,
) -> None:
    """R4: dois novos × mesmo original — locks ordenados, sem deadlock.

    Duas intercalações são válidas (a coleta vê o commit ou não): ou só um
    vínculo (original→novo) ou cadeia (original→A→B). Em ambas: sem deadlock,
    sem erro, original CLEANED, representante final AWAITING_DOCTOR, cada
    incoming com no máximo UMA resolução de fila aplicada e eventos
    correlacionados íntegros.
    """
    prior = Case.objects.create(created_by=nir_user)
    _declared(prior, ANGIO_TYPE)
    _identity(prior, number="conc-2")
    _awaiting(prior)
    incomings: list[Case] = []
    for _ in range(2):
        incoming = Case.objects.create(created_by=nir_user)
        _declared(incoming, ANGIO_TYPE)
        _identity(incoming, number="conc-2")
        _awaiting(incoming)
        incomings.append(incoming)
    pks = [case.pk for case in incomings]
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _worker(case_pk: uuid.UUID) -> None:
        from django.db import connections
        from django.test import override_settings as _override_settings

        try:
            barrier.wait(timeout=30)
            with _override_settings(
                CASE_REAPPEARANCE_ENABLED=True,
                CASE_REAPPEARANCE_WINDOW_DAYS=15,
            ):
                case = Case.objects.get(pk=case_pk)
                reappearance_module.resolve_medical_replacement(case)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=_worker, args=(pk,)) for pk in pks]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads), "deadlock na resolução"
    assert not errors
    own_ids = {prior.pk, *[case.pk for case in incomings]}
    applied = list(
        CaseReappearance.objects.filter(
            Q(incoming_case_id__in=own_ids, prior_case_id__in=own_ids),
            state=ReappearanceState.APPLIED,
            resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
        )
    )
    assert 1 <= len(applied) <= 2
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    representative = reappearance_module.current_representative(prior)
    representative.refresh_from_db()
    assert representative.status == CaseStatus.AWAITING_DOCTOR
    for incoming in incomings:
        incoming.refresh_from_db()
        assert incoming.status in (CaseStatus.AWAITING_DOCTOR, CaseStatus.CLEANED)
    for link in applied:
        assert (
            link.incoming_case.events.filter(
                event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED,
                payload__reappearance_id=str(link.pk),
            ).count()
            == 1
        )
        assert (
            link.prior_case.events.filter(
                event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED,
                payload__reappearance_id=str(link.pk),
            ).count()
            == 1
        )


# ── R5: disposição ─────────────────────────────────────────────────────────


@NEW_MODE
@pytest.mark.django_db
@override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=True)
def test_substituted_original_follows_minimization_with_marker(
    nir_user: User,
) -> None:
    """R5: minimização ativa — original disposto com marcador, sem ressuscitar."""
    prior, incoming = _make_pair(nir_user)
    prior.extracted_text = "texto clinico"
    prior.anonymized_text = "texto anonimizado"
    prior.save(update_fields=["extracted_text", "anonymized_text"])
    reappearance_module.resolve_medical_replacement(incoming)
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert prior.extracted_text == ""
    assert prior.anonymized_text == ""
    assert prior.clinical_data_disposed_at is not None


@NEW_MODE
@pytest.mark.django_db
@override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False)
def test_substituted_original_retained_without_marker(nir_user: User) -> None:
    """R5: retenção — original preserva dados e marcador nulo."""
    prior, incoming = _make_pair(nir_user)
    prior.extracted_text = "texto clinico"
    prior.save(update_fields=["extracted_text"])
    reappearance_module.resolve_medical_replacement(incoming)
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert prior.extracted_text == "texto clinico"
    assert prior.clinical_data_disposed_at is None


@NEW_MODE
@pytest.mark.django_db
def test_exactly_one_queue_resolution_survives_chain(nir_user: User) -> None:
    """R5/contrato: cadeia não duplica resolução de fila por incoming."""
    _, incoming_b = _make_pair(nir_user, number="chain-1", prior_days_ago=4)
    link_ab = reappearance_module.resolve_medical_replacement(incoming_b)
    assert link_ab is not None
    assert (
        CaseReappearance.objects.filter(
            incoming_case=incoming_b,
            state=ReappearanceState.APPLIED,
            resolution_kind__in=[
                ResolutionKind.MEDICAL_REPLACEMENT,
                ResolutionKind.SCHEDULING_REPEAT,
            ],
        ).count()
        == 1
    )


# ── Flag off ───────────────────────────────────────────────────────────────


@FLAG_OFF
@pytest.mark.django_db
def test_flag_off_creates_no_candidates_resolutions_or_retries(
    nir_user: User,
) -> None:
    """R5/Migration Plan: flag off — sem candidatos, resoluções ou retries novos."""
    prior, incoming = _make_pair(nir_user)
    assert reappearance_module.find_medical_replacement_candidate(incoming) is None
    assert reappearance_module.resolve_medical_replacement(incoming) is None
    assert not CaseReappearance.objects.exists()
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR


@FLAG_OFF
@pytest.mark.django_db
def test_flag_off_retry_is_noop_without_consuming_budget(nir_user: User) -> None:
    """R5/D6: jobs pendentes ao desligar viram no-op sem consumir orçamento."""
    with NEW_MODE:
        prior, incoming = _make_pair(nir_user)
        doctor = User.objects.create_user(username="med-off", password="x")
        claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
        link = reappearance_module.resolve_medical_replacement(incoming)
        assert link is not None and link.state == ReappearanceState.PENDING
        link_pk = link.pk
        revision = link.revision_digest
        attempt = link.attempt_count
    events_before = CaseEvent.objects.count()
    reappearance_module.handle_reappearance_retry(str(link_pk), revision, attempt)
    link.refresh_from_db()
    assert link.attempt_count == attempt
    assert link.state == ReappearanceState.PENDING
    assert CaseEvent.objects.count() == events_before


# ── HTTP histórico ─────────────────────────────────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_history_shows_immediate_link_and_current_representative(
    client: Client, user_factory: Callable[[str, str], User]
) -> None:
    """R1: HTTP histórico A→B→C — elo B e representante C com links autorizados."""
    nir = user_factory("nir-hist", "nir")
    client.force_login(nir)
    session = client.session
    session["active_role"] = "nir"
    session.save()
    prior_a, incoming_b = _make_pair(nir, number="hist-1", prior_days_ago=6)
    reappearance_module.resolve_medical_replacement(incoming_b)
    incoming_c = Case.objects.create(created_by=nir)
    _declared(incoming_c, ANGIO_TYPE)
    _identity(incoming_c, number="hist-1")
    _awaiting(incoming_c)
    reappearance_module.resolve_medical_replacement(incoming_c)
    response = client.get(reverse("intake:closed_case_detail", args=[prior_a.case_id]))
    assert response.status_code == 200
    content = response.content.decode()
    assert str(incoming_b.case_id) in content
    assert str(incoming_c.case_id) in content
