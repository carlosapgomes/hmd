"""Testes TDD da conferência NIR e integração final (slice 005, unidade 5A).

Cobre os requisitos R1–R4 no núcleo de domínio (``apps/cases/reappearance.py``),
eventos/trilha (``events.py`` / ``event_trail.py`` / ``procedure_review.py``),
prior-case (``apps/pipeline/prior_case.py``) e presenter/card médico tardio
(``apps/doctor/presenters.py`` / ``templates/doctor/_case_cards.html``).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.template.loader import render_to_string
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_q.models import Schedule

from apps.accounts.models import Role, User
from apps.cases import reappearance as reappearance_module
from apps.cases.event_trail import (
    BADGE_NIR,
    EVENT_BADGE_CSS,
    EVENT_LABELS,
    build_event_trail_context,
)
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock
from apps.cases.models import (
    ActorType,
    Case,
    CaseEvent,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    DoctorDisposition,
    ReappearanceState,
    ResolutionKind,
)
from apps.cases.procedure_review import event_detail_lines
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.doctor.presenters import build_case_detail_context
from apps.pipeline.llm2_service import _lookup_prior_context
from apps.pipeline.prior_case import lookup_prior_case_context, record_prior_case_lookups

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
FLAG_OFF = override_settings(CASE_REAPPEARANCE_ENABLED=False, CASE_REAPPEARANCE_WINDOW_DAYS=15)

SYSTEM_ROLE = "system"
NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
ANGIO_TYPE = "art_perif"
CAT_TYPE = "cateterismo"
_CNS_A = "207139200176053"
_CNS_B = "605932160152243"
_NAME = "Maria da Silva"


def _declared(case: Case, *procedure_types: str) -> None:
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)


def _identity(
    case: Case,
    *,
    name: str = _NAME,
    number: str = "",
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


def _advance_to_awaiting_doctor(case: Case) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _advance_to_llm_summarizing(case: Case) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.LLM_SUMMARIZING


def _advance_to_awaiting_scheduling(case: Case, doctor: User) -> None:
    _advance_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case,
        decisions={ANGIO_TYPE: (DoctorDisposition.APPROVED, "Indicação clínica válida.")},
        user=doctor,
        role=DOCTOR_ROLE,
    )
    case.record_doctor_decision(accepted=True, user=doctor, role=DOCTOR_ROLE)
    case.request_scheduling(user=None, role=SYSTEM_ROLE)
    case.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_SCHEDULING


def _make_case(
    owner: User,
    *,
    days_ago: int = 5,
    name: str = _NAME,
    number: str = "",
    cns: str = "",
    origin: str = "",
    age: int | None = None,
    procedure: str = ANGIO_TYPE,
) -> Case:
    case = Case.objects.create(created_by=owner)
    Case.objects.filter(pk=case.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    case.refresh_from_db()
    _declared(case, procedure)
    return _identity(case, name=name, number=number, cns=cns, origin=origin, age=age)


def _assert_no_writes(queries: list[dict[str, str]]) -> None:
    for query in queries:
        sql = query["sql"].lstrip().upper()
        assert not sql.startswith("INSERT"), f"GET/leitura executou INSERT: {sql}"
        assert not sql.startswith("UPDATE"), f"GET/leitura executou UPDATE: {sql}"
        assert not sql.startswith("DELETE"), f"GET/leitura executou DELETE: {sql}"


# ── Eventos e trilha (anti-drift + zero PII) ───────────────────────────────


@pytest.mark.django_db
def test_conference_events_registered_in_trail_without_pii(nir_user: User) -> None:
    assert CaseEventType.CASE_REAPPEARANCE_CONFIRMED in EVENT_LABELS
    assert CaseEventType.CASE_REAPPEARANCE_INDEPENDENT in EVENT_LABELS
    assert EVENT_BADGE_CSS[CaseEventType.CASE_REAPPEARANCE_CONFIRMED] == BADGE_NIR
    assert EVENT_BADGE_CSS[CaseEventType.CASE_REAPPEARANCE_INDEPENDENT] == BADGE_NIR

    case = _make_case(nir_user, number="REG-1", origin="HGE")
    prior_id = uuid.uuid4()
    ev_conf = CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_REAPPEARANCE_CONFIRMED,
        actor_type=ActorType.USER,
        actor=nir_user,
        actor_role=NIR_ROLE,
        payload={
            "reappearance_id": str(uuid.uuid4()),
            "prior_case_id": str(prior_id),
            "evidence_code": reappearance_module.EVIDENCE_ORIGIN_UNIT,
            "revision_digest": "v1:abc",
            "resolution_kind": ResolutionKind.MEDICAL_REPLACEMENT,
        },
    )
    ev_indep = CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_REAPPEARANCE_INDEPENDENT,
        actor_type=ActorType.USER,
        actor=nir_user,
        actor_role=NIR_ROLE,
        payload={
            "reappearance_id": str(uuid.uuid4()),
            "prior_case_id": str(prior_id),
            "evidence_code": reappearance_module.EVIDENCE_CNS_CONFLICT,
            "revision_digest": "v1:abc",
        },
    )
    lines_conf = event_detail_lines(ev_conf)
    lines_indep = event_detail_lines(ev_indep)
    assert lines_conf
    assert lines_indep
    joined = " ".join(lines_conf + lines_indep)
    assert _NAME not in joined
    assert _CNS_A not in joined
    trail = build_event_trail_context(case)
    assert len(trail) == 2


# ── R1: Descoberta read-only de candidatos ─────────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_list_candidates_covers_weak_age_conflict_ambiguity_and_is_readonly(
    nir_user: User,
) -> None:
    prior_weak = _make_case(nir_user, days_ago=6, origin="Hospital Geral", age=64)
    _advance_to_awaiting_doctor(prior_weak)

    prior_age = _make_case(nir_user, days_ago=5, number="REG-AGE", age=50)
    _advance_to_awaiting_doctor(prior_age)

    prior_cns = _make_case(nir_user, days_ago=4, number="REG-CNS", cns=_CNS_B, age=50)
    _advance_to_awaiting_doctor(prior_cns)

    incoming = _make_case(
        nir_user,
        days_ago=1,
        number="REG-AGE",
        cns=_CNS_A,
        origin="hospital geral",
        age=65,
    )
    # Faz prior_cns também bater por regulação para testar conflito de CNS,
    # e cria outro forte para testar ambiguidade strong+strong.
    prior_cns.agency_record_number = "REG-AGE"
    prior_cns.save(update_fields=["agency_record_number"])

    prior_strong_2 = _make_case(nir_user, days_ago=3, number="REG-AGE", cns=_CNS_A, age=65)
    _advance_to_awaiting_doctor(prior_strong_2)
    prior_strong_3 = _make_case(nir_user, days_ago=2, number="REG-AGE", cns=_CNS_A, age=66)
    _advance_to_awaiting_doctor(prior_strong_3)

    with CaptureQueriesContext(connection) as ctx:
        candidates = reappearance_module.list_nir_reappearance_candidates(incoming)
    _assert_no_writes(ctx.captured_queries)

    by_prior = {c.prior.pk: c for c in candidates}
    assert prior_weak.pk in by_prior
    assert by_prior[prior_weak.pk].evidence_code == reappearance_module.EVIDENCE_ORIGIN_UNIT
    assert by_prior[prior_weak.pk].in_active_queue is True

    assert prior_age.pk in by_prior
    assert by_prior[prior_age.pk].evidence_code == reappearance_module.EVIDENCE_AGE_DIVERGENCE

    assert prior_cns.pk in by_prior
    assert by_prior[prior_cns.pk].evidence_code == reappearance_module.EVIDENCE_CNS_CONFLICT
    assert by_prior[prior_cns.pk].conflict is True

    assert prior_strong_2.pk in by_prior
    assert (
        by_prior[prior_strong_2.pk].evidence_code == reappearance_module.EVIDENCE_AMBIGUOUS_STRONG
    )
    assert prior_strong_3.pk in by_prior
    assert (
        by_prior[prior_strong_3.pk].evidence_code == reappearance_module.EVIDENCE_AMBIGUOUS_STRONG
    )


@FLAG_OFF
@pytest.mark.django_db
def test_list_candidates_flag_off_returns_empty(nir_user: User) -> None:
    prior = _make_case(nir_user, days_ago=5, origin="HGE")
    _advance_to_awaiting_doctor(prior)
    incoming = _make_case(nir_user, days_ago=1, origin="HGE")
    assert reappearance_module.list_nir_reappearance_candidates(incoming) == []


# ── R2: Independente por revisão + reabertura + stale ──────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_independent_suppresses_only_exact_pair_and_revision_reopens_on_change(
    nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    other_nir = user_factory("nir-outro", NIR_ROLE)
    prior_1 = _make_case(nir_user, days_ago=5, origin="HGE", age=40)
    _advance_to_awaiting_doctor(prior_1)
    prior_2 = _make_case(nir_user, days_ago=4, origin="HGE", age=40)
    _advance_to_awaiting_doctor(prior_2)
    incoming = _make_case(nir_user, days_ago=1, origin="HGE", age=40)

    candidates = reappearance_module.list_nir_reappearance_candidates(incoming)
    cand_1 = next(c for c in candidates if c.prior.pk == prior_1.pk)

    outcome = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming,
        prior_case_id=prior_1.pk,
        revision_digest=cand_1.revision_digest,
        decision="independent",
        user=other_nir,
        role=NIR_ROLE,
    )
    assert outcome.decision == "independent"
    assert outcome.status == "independent"
    assert CaseEvent.objects.filter(
        case=incoming, event_type=CaseEventType.CASE_REAPPEARANCE_INDEPENDENT
    ).exists()

    # Suprime somente prior_1 nessa revisão; prior_2 continua visível.
    after = reappearance_module.list_nir_reappearance_candidates(incoming)
    assert [c.prior.pk for c in after] == [prior_2.pk]

    # Idempotência na mesma revisão.
    idem = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming,
        prior_case_id=prior_1.pk,
        revision_digest=cand_1.revision_digest,
        decision="independent",
        user=other_nir,
        role=NIR_ROLE,
    )
    assert idem.status == "independent"
    assert (
        CaseEvent.objects.filter(
            case=incoming, event_type=CaseEventType.CASE_REAPPEARANCE_INDEPENDENT
        ).count()
        == 1
    )

    # Mudança de identidade/documento altera o digest → reabre conferência e recusa digest antigo.
    incoming.patient_age = 41
    incoming.save(update_fields=["patient_age"])
    reopened = reappearance_module.list_nir_reappearance_candidates(incoming)
    assert {c.prior.pk for c in reopened} == {prior_1.pk, prior_2.pk}

    with pytest.raises(reappearance_module.ReappearanceStaleError):
        reappearance_module.respond_to_reappearance_conference(
            incoming=incoming,
            prior_case_id=prior_1.pk,
            revision_digest=cand_1.revision_digest,
            decision="same_request",
            user=other_nir,
            role=NIR_ROLE,
        )


# ── R3 Fluxo 1: Substituição médica (pendente antes do pipeline vs imediata) ─


@NEW_MODE
@pytest.mark.django_db
def test_medical_replacement_confirmed_before_pipeline_applies_on_finalize(
    nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    other_nir = user_factory("nir-conf-med", NIR_ROLE)
    prior = _make_case(nir_user, days_ago=6, origin="HGE", age=50)
    _advance_to_awaiting_doctor(prior)

    incoming = _make_case(nir_user, days_ago=1, origin="HGE", age=50)
    _advance_to_llm_summarizing(incoming)
    lock_token = claim_case_lock(
        incoming, user=None, context="worker_llm", role=SYSTEM_ROLE, lease_seconds=120
    ).token

    (cand,) = reappearance_module.list_nir_reappearance_candidates(incoming)
    outcome = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming,
        prior_case_id=prior.pk,
        revision_digest=cand.revision_digest,
        decision="same_request",
        user=other_nir,
        role=NIR_ROLE,
    )
    assert outcome.status == "confirmed_awaiting_pipeline"
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR  # não fecha antes do novo pronto

    # Prepara conclusão do pipeline e finaliza.
    incoming.structured_data = {"pedido": {"procedimento": ANGIO_TYPE}}
    incoming.summary_text = "Resumo clínico sintético."
    incoming.policy_result = {ANGIO_TYPE: {"recommendation": "recomenda_aceitar", "criteria": []}}
    incoming.suggested_action = {
        "procedures": {ANGIO_TYPE: {"suggestion": "aceitar", "motivos": []}},
        "aggregate": {"suggestion": "aceitar", "motivos": []},
    }
    incoming.save(
        update_fields=["structured_data", "summary_text", "policy_result", "suggested_action"]
    )

    fin = reappearance_module.finalize_summarization(
        case_pk=incoming.pk,
        lock_token=lock_token,
        marked_execution=False,
        execution_digests=None,
        execution_authority=(None, None, None),
        execution_declared=(ANGIO_TYPE,),
        user=None,
        role=SYSTEM_ROLE,
    )
    assert fin.status == "completed"
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.reappearance_wait_started_at == prior.effective_wait_started_at
    link = CaseReappearance.objects.get(incoming_case=incoming, prior_case=prior)
    assert link.state == ReappearanceState.APPLIED
    assert link.confirmed_by_id == other_nir.pk


@NEW_MODE
@pytest.mark.django_db
def test_medical_replacement_ready_applies_immediately_with_correlated_events(
    nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    other_nir = user_factory("nir-med-now", NIR_ROLE)
    prior = _make_case(nir_user, days_ago=6, origin="HGE", age=50)
    _advance_to_awaiting_doctor(prior)
    incoming = _make_case(nir_user, days_ago=1, origin="HGE", age=50)
    _advance_to_awaiting_doctor(incoming)

    (cand,) = reappearance_module.list_nir_reappearance_candidates(incoming)
    outcome = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming,
        prior_case_id=prior.pk,
        revision_digest=cand.revision_digest,
        decision="same_request",
        user=other_nir,
        role=NIR_ROLE,
    )
    assert outcome.status == "applied_medical"
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert CaseEvent.objects.filter(
        case=incoming,
        event_type=CaseEventType.CASE_REAPPEARANCE_CONFIRMED,
        actor=other_nir,
        actor_role=NIR_ROLE,
    ).exists()
    assert CaseEvent.objects.filter(
        case=incoming,
        event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED,
        actor_type=ActorType.SYSTEM,
    ).exists()
    assert CaseEvent.objects.filter(
        case=prior,
        event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED,
        actor_type=ActorType.SYSTEM,
    ).exists()


# ── R3 Fluxo 2: Agendamento (pré-LLM1 não arquiva vs pronto preserva lease) ─


@NEW_MODE
@pytest.mark.django_db
def test_scheduling_repeat_confirmed_pre_llm1_awaits_post_llm1_and_ready_preserves_scheduler_lease(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    other_nir = user_factory("nir-sched", NIR_ROLE)
    scheduler_user = user_factory("sched-1", "scheduler")

    prior = _make_case(nir_user, days_ago=6, origin="HGE", age=50)
    _advance_to_awaiting_scheduling(prior, doctor_user)

    # Caso 1: incoming pré-LLM1 (NEW) — NÃO arquiva antes do LLM1.
    incoming_new = _make_case(nir_user, days_ago=1, origin="HGE", age=50)
    (cand_new,) = reappearance_module.list_nir_reappearance_candidates(incoming_new)
    out_new = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming_new,
        prior_case_id=prior.pk,
        revision_digest=cand_new.revision_digest,
        decision="same_request",
        user=other_nir,
        role=NIR_ROLE,
    )
    assert out_new.status == "confirmed_awaiting_pipeline"
    incoming_new.refresh_from_db()
    assert incoming_new.status == CaseStatus.NEW

    # Avança até LLM_EXTRACTING e chama archive_scheduling_repeat_after_llm1.
    incoming_new.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    incoming_new.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    incoming_new.complete_anonymization(user=None, role=SYSTEM_ROLE)
    lock_token = claim_case_lock(
        incoming_new, user=None, context="worker_llm", role=SYSTEM_ROLE, lease_seconds=120
    ).token
    sched_out = reappearance_module.archive_scheduling_repeat_after_llm1(
        case_pk=incoming_new.pk,
        lock_token=lock_token,
        reconciled=True,
    )
    assert sched_out.status == "archived"
    incoming_new.refresh_from_db()
    assert incoming_new.status == CaseStatus.CLEANED
    assert incoming_new.clinical_data_disposed_at is None  # retido p/ scheduler

    # Caso 2: incoming pronto em AWAITING_DOCTOR + lease humana do scheduler no prior.
    claim_case_lock(
        prior, user=scheduler_user, context="scheduler_work", role="scheduler", lease_seconds=120
    )
    incoming_ready = _make_case(nir_user, days_ago=0, origin="HGE", age=50)
    _advance_to_awaiting_doctor(incoming_ready)
    (cand_ready,) = reappearance_module.list_nir_reappearance_candidates(incoming_ready)
    out_ready = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming_ready,
        prior_case_id=prior.pk,
        revision_digest=cand_ready.revision_digest,
        decision="same_request",
        user=other_nir,
        role=NIR_ROLE,
    )
    assert out_ready.status == "applied_scheduling"
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_SCHEDULING
    assert prior.locked_by_id == scheduler_user.pk  # lease humana preservada!


# ── R3 Fluxo 3: Histórico negativo, coexistência, tardio e rollback flag ───


@NEW_MODE
@pytest.mark.django_db
def test_historical_context_coexists_with_queue_and_late_confirmation_honest(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    other_nir = user_factory("nir-hist", NIR_ROLE)

    # Prior 1: histórico negado há 4 dias (mesmo caso também na fila? Não, CLEANED).
    prior_hist = _make_case(nir_user, days_ago=4, origin="HGE", age=50)
    _advance_to_awaiting_doctor(prior_hist)
    record_doctor_procedure_decisions(
        prior_hist,
        decisions={ANGIO_TYPE: (DoctorDisposition.DENIED, "Sem critério clínico.")},
        user=doctor_user,
        role=DOCTOR_ROLE,
    )
    CaseProcedure.objects.filter(case=prior_hist, procedure_type=ANGIO_TYPE).update(
        doctor_decided_at=timezone.now() - timedelta(days=4)
    )
    prior_hist.record_doctor_decision(accepted=False, user=doctor_user, role=DOCTOR_ROLE)
    prior_hist.post_final_reply(user=None, role=SYSTEM_ROLE)
    prior_hist.nir_acknowledge(user=nir_user, role=NIR_ROLE)
    prior_hist.start_cleaning(user=None, role=SYSTEM_ROLE)
    prior_hist.complete_cleaning(user=None, role=SYSTEM_ROLE)

    # Prior 2: ativo na fila médica.
    prior_active = _make_case(nir_user, days_ago=3, origin="HGE", age=50)
    _advance_to_awaiting_doctor(prior_active)

    incoming = _make_case(nir_user, days_ago=1, origin="HGE", age=50)
    _advance_to_awaiting_doctor(incoming)
    # Simula LLM2 já executado ANTES da confirmação do histórico.
    incoming.summary_text = "Sumário já gerado."
    incoming.save(update_fields=["summary_text"])
    CaseEvent.objects.create(
        case=incoming,
        event_type=CaseEventType.CASE_LLM2_COMPLETED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role=SYSTEM_ROLE,
        payload={},
    )

    # Antes da confirmação, lookup_prior_case_context retorna None (fraco não vira fato).
    assert lookup_prior_case_context(incoming, ANGIO_TYPE) is None

    candidates = reappearance_module.list_nir_reappearance_candidates(incoming)
    by_prior = {c.prior.pk: c for c in candidates}
    assert prior_hist.pk in by_prior
    assert by_prior[prior_hist.pk].has_historical_decision is True
    assert prior_active.pk in by_prior
    assert by_prior[prior_active.pk].in_active_queue is True

    # Confirma histórico + ativo sem IntegrityError (coexistência PRIOR_CONTEXT + MEDICAL_REPLACEMENT).
    out_hist = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming,
        prior_case_id=prior_hist.pk,
        revision_digest=by_prior[prior_hist.pk].revision_digest,
        decision="same_request",
        user=other_nir,
        role=NIR_ROLE,
    )
    assert out_hist.status == "confirmed_context_only"

    out_active = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming,
        prior_case_id=prior_active.pk,
        revision_digest=by_prior[prior_active.pk].revision_digest,
        decision="same_request",
        user=other_nir,
        role=NIR_ROLE,
    )
    assert out_active.status == "applied_medical"

    # Lookup agora retorna nir_confirmed com confirmed_after_summarization=True!
    summary = lookup_prior_case_context(incoming, ANGIO_TYPE)
    assert summary is not None
    assert summary.origin == "nir_confirmed"
    assert summary.prior_case_id == str(prior_hist.pk)
    assert summary.confirmed_after_summarization is True

    # Presenter e template exibem aviso tardio honesto.
    ctx = build_case_detail_context(incoming)
    prior_cases = ctx["prior_cases"]
    assert isinstance(prior_cases, list)
    assert prior_cases[0]["confirmed_after_summarization"] is True
    html = render_to_string("doctor/_case_cards.html", ctx)
    assert "Vinculado após a sumarização" in html

    # Com flag desligada (rollback), contexto já aplicado permanece visível!
    with FLAG_OFF:
        rollback_summary = lookup_prior_case_context(incoming, ANGIO_TYPE)
        assert rollback_summary is not None
        assert rollback_summary.origin == "nir_confirmed"

    # Nova execução normal do pipeline (record_prior_case_lookups + novo CASE_LLM2_COMPLETED)
    # registra PRIOR_CASE_LOOKUP com nir_confirmed e zera confirmed_after_summarization.
    record_prior_case_lookups(incoming, user=None, role=SYSTEM_ROLE)
    lookup_ev = CaseEvent.objects.filter(
        case=incoming, event_type=CaseEventType.PRIOR_CASE_LOOKUP
    ).latest("id")
    assert lookup_ev.payload["origin"] == "nir_confirmed"
    CaseEvent.objects.create(
        case=incoming,
        event_type=CaseEventType.CASE_LLM2_COMPLETED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role=SYSTEM_ROLE,
        payload={},
    )
    after_llm2 = lookup_prior_case_context(incoming, ANGIO_TYPE)
    assert after_llm2 is not None
    assert after_llm2.confirmed_after_summarization is False
    llm2_ctx = _lookup_prior_context(incoming, [ANGIO_TYPE])
    assert [item.procedure_type for item in llm2_ctx] == [ANGIO_TYPE]
    assert llm2_ctx[0].origin == "nir_confirmed"


@NEW_MODE
@pytest.mark.django_db
def test_incoming_already_decided_returns_confirmed_evolved_without_closing(
    nir_user: User, doctor_user: User
) -> None:
    prior = _make_case(nir_user, days_ago=5, origin="HGE", age=50)
    _advance_to_awaiting_doctor(prior)
    incoming = _make_case(nir_user, days_ago=1, origin="HGE", age=50)
    _advance_to_awaiting_doctor(incoming)
    record_doctor_procedure_decisions(
        incoming,
        decisions={ANGIO_TYPE: (DoctorDisposition.APPROVED, "Aprovado.")},
        user=doctor_user,
        role=DOCTOR_ROLE,
    )
    incoming.record_doctor_decision(accepted=True, user=doctor_user, role=DOCTOR_ROLE)

    (cand,) = reappearance_module.list_nir_reappearance_candidates(incoming)
    out = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming,
        prior_case_id=prior.pk,
        revision_digest=cand.revision_digest,
        decision="same_request",
        user=nir_user,
        role=NIR_ROLE,
    )
    assert out.status == "confirmed_evolved"
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.status == CaseStatus.DOCTOR_ACCEPTED


# ── R4: Segurança, concorrência, defer/retry, múltiplos candidatos ─────────


@NEW_MODE
@pytest.mark.django_db
def test_security_and_validation_errors(nir_user: User, doctor_user: User) -> None:
    prior = _make_case(nir_user, days_ago=5, origin="HGE", age=50)
    _advance_to_awaiting_doctor(prior)
    unrelated = _make_case(nir_user, days_ago=5, name="Outro Paciente", origin="HGE", age=50)
    _advance_to_awaiting_doctor(unrelated)
    incoming = _make_case(nir_user, days_ago=1, origin="HGE", age=50)
    (cand,) = reappearance_module.list_nir_reappearance_candidates(incoming)

    with pytest.raises(ValueError):
        reappearance_module.respond_to_reappearance_conference(
            incoming=incoming,
            prior_case_id=prior.pk,
            revision_digest=cand.revision_digest,
            decision="talvez",
            user=nir_user,
            role=NIR_ROLE,
        )

    with pytest.raises(ValueError):
        reappearance_module.respond_to_reappearance_conference(
            incoming=incoming,
            prior_case_id=incoming.pk,
            revision_digest=cand.revision_digest,
            decision="same_request",
            user=nir_user,
            role=NIR_ROLE,
        )

    # Papel não-NIR ou papel NIR revogado no banco → PermissionError dentro da tx.
    with pytest.raises(PermissionError):
        reappearance_module.respond_to_reappearance_conference(
            incoming=incoming,
            prior_case_id=prior.pk,
            revision_digest=cand.revision_digest,
            decision="same_request",
            user=doctor_user,
            role=DOCTOR_ROLE,
        )

    nir_role = Role.objects.get(name=NIR_ROLE)
    nir_user.roles.remove(nir_role)
    with pytest.raises(PermissionError):
        reappearance_module.respond_to_reappearance_conference(
            incoming=incoming,
            prior_case_id=prior.pk,
            revision_digest=cand.revision_digest,
            decision="same_request",
            user=nir_user,
            role=NIR_ROLE,
        )
    nir_user.roles.add(nir_role)

    # Prior fora de escopo (nome diferente) → ReappearanceStaleError.
    bad_digest = reappearance_module.revision_digest_for_pair(incoming, unrelated)
    with pytest.raises(reappearance_module.ReappearanceStaleError):
        reappearance_module.respond_to_reappearance_conference(
            incoming=incoming,
            prior_case_id=unrelated.pk,
            revision_digest=bad_digest,
            decision="same_request",
            user=nir_user,
            role=NIR_ROLE,
        )

    # Flag off recusa POST.
    with FLAG_OFF:
        with pytest.raises(reappearance_module.ReappearanceStaleError):
            reappearance_module.respond_to_reappearance_conference(
                incoming=incoming,
                prior_case_id=prior.pk,
                revision_digest=cand.revision_digest,
                decision="same_request",
                user=nir_user,
                role=NIR_ROLE,
            )


@NEW_MODE
@pytest.mark.django_db
def test_live_doctor_lease_defers_and_retry_applies_preserving_confirmed_by(
    nir_user: User, doctor_user: User
) -> None:
    prior = _make_case(nir_user, days_ago=5, origin="HGE", age=50)
    _advance_to_awaiting_doctor(prior)
    lease_token = claim_case_lock(
        prior, user=doctor_user, context="doctor_decide", role=DOCTOR_ROLE, lease_seconds=120
    ).token

    incoming = _make_case(nir_user, days_ago=1, origin="HGE", age=50)
    _advance_to_awaiting_doctor(incoming)
    (cand,) = reappearance_module.list_nir_reappearance_candidates(incoming)

    out = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming,
        prior_case_id=prior.pk,
        revision_digest=cand.revision_digest,
        decision="same_request",
        user=nir_user,
        role=NIR_ROLE,
    )
    assert out.status == "deferred"
    assert out.schedule_failed is False
    assert out.link is not None
    assert out.link.state == ReappearanceState.PENDING
    assert out.link.confirmed_by_id == nir_user.pk

    # Libera a lease e avança next_attempt_at para simular disparo do retry.
    Case.objects.filter(pk=prior.pk).update(locked_until=None, lock_token=None, locked_by=None)
    del lease_token
    CaseReappearance.objects.filter(pk=out.link.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=5)
    )
    reappearance_module.handle_reappearance_retry(
        out.link.pk, out.link.revision_digest, out.link.attempt_count
    )
    out.link.refresh_from_db()
    prior.refresh_from_db()
    assert out.link.state == ReappearanceState.APPLIED
    assert out.link.confirmed_by_id == nir_user.pk
    assert prior.status == CaseStatus.CLEANED


@NEW_MODE
@pytest.mark.django_db
def test_schedule_failed_returns_honest_flag_and_multiple_candidates_forbid_second_apply(
    nir_user: User, doctor_user: User
) -> None:
    prior_1 = _make_case(nir_user, days_ago=6, origin="HGE", age=50)
    _advance_to_awaiting_doctor(prior_1)
    claim_case_lock(
        prior_1, user=doctor_user, context="doctor_decide", role=DOCTOR_ROLE, lease_seconds=120
    )

    incoming_1 = _make_case(nir_user, days_ago=1, origin="HGE", age=50)
    _advance_to_awaiting_doctor(incoming_1)
    (cand_1,) = reappearance_module.list_nir_reappearance_candidates(incoming_1)

    with patch("django_q.tasks.schedule", side_effect=RuntimeError("broker down")):
        out_fail = reappearance_module.respond_to_reappearance_conference(
            incoming=incoming_1,
            prior_case_id=prior_1.pk,
            revision_digest=cand_1.revision_digest,
            decision="same_request",
            user=nir_user,
            role=NIR_ROLE,
        )
    assert out_fail.status == "deferred"
    assert out_fail.schedule_failed is True

    # Múltiplos candidatos: confirmar par 1 fecha apenas par 1; tentar confirmar par 2 → StaleError.
    prior_a = _make_case(nir_user, days_ago=5, name="Ana Maria", origin="HGE", age=30)
    _advance_to_awaiting_doctor(prior_a)
    prior_b = _make_case(nir_user, days_ago=4, name="Ana Maria", origin="HGE", age=30)
    _advance_to_awaiting_doctor(prior_b)
    incoming_2 = _make_case(nir_user, days_ago=1, name="Ana Maria", origin="HGE", age=30)
    _advance_to_awaiting_doctor(incoming_2)

    cands = reappearance_module.list_nir_reappearance_candidates(incoming_2)
    by_prior = {c.prior.pk: c for c in cands}
    assert set(by_prior) == {prior_a.pk, prior_b.pk}

    out_a = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming_2,
        prior_case_id=prior_a.pk,
        revision_digest=by_prior[prior_a.pk].revision_digest,
        decision="same_request",
        user=nir_user,
        role=NIR_ROLE,
    )
    assert out_a.status == "applied_medical"
    prior_a.refresh_from_db()
    prior_b.refresh_from_db()
    assert prior_a.status == CaseStatus.CLEANED
    assert prior_b.status == CaseStatus.AWAITING_DOCTOR

    with pytest.raises(reappearance_module.ReappearanceStaleError):
        reappearance_module.respond_to_reappearance_conference(
            incoming=incoming_2,
            prior_case_id=prior_b.pk,
            revision_digest=by_prior[prior_b.pk].revision_digest,
            decision="same_request",
            user=nir_user,
            role=NIR_ROLE,
        )


@NEW_MODE
@pytest.mark.django_db
def test_forged_independent_on_unambiguous_strong_pair_rejected_without_writes(
    nir_user: User,
) -> None:
    prior = _make_case(nir_user, days_ago=5, number="REG-STRONG", age=50)
    _advance_to_awaiting_doctor(prior)
    incoming = _make_case(nir_user, days_ago=1, number="REG-STRONG", age=50)

    assert reappearance_module.list_nir_reappearance_candidates(incoming) == []
    digest = reappearance_module.revision_digest_for_pair(incoming, prior)
    with pytest.raises(reappearance_module.ReappearanceStaleError):
        reappearance_module.respond_to_reappearance_conference(
            incoming=incoming,
            prior_case_id=prior.pk,
            revision_digest=digest,
            decision="independent",
            user=nir_user,
            role=NIR_ROLE,
        )
    assert not CaseReappearance.objects.filter(incoming_case=incoming, prior_case=prior).exists()
    assert not CaseEvent.objects.filter(
        case=incoming, event_type=CaseEventType.CASE_REAPPEARANCE_INDEPENDENT
    ).exists()


@NEW_MODE
@pytest.mark.django_db
def test_terminal_pending_link_raises_stale_error_instead_of_false_applied(
    nir_user: User,
) -> None:
    prior = _make_case(nir_user, days_ago=5, origin="HGE", age=50)
    _advance_to_awaiting_doctor(prior)
    incoming = _make_case(nir_user, days_ago=1, origin="HGE", age=50)
    _advance_to_awaiting_doctor(incoming)

    digest = reappearance_module.revision_digest_for_pair(incoming, prior)
    CaseReappearance.objects.create(
        incoming_case=incoming,
        prior_case=prior,
        canonical_case=incoming,
        revision_digest=digest,
        evidence_code=reappearance_module.EVIDENCE_ORIGIN_UNIT,
        state=ReappearanceState.PENDING,
        resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
        last_reason_code=reappearance_module.REASON_RETRY_EXHAUSTED,
        confirmed_by=nir_user,
        confirmed_at=timezone.now(),
    )

    with pytest.raises(reappearance_module.ReappearanceStaleError):
        reappearance_module.respond_to_reappearance_conference(
            incoming=incoming,
            prior_case_id=prior.pk,
            revision_digest=digest,
            decision="same_request",
            user=nir_user,
            role=NIR_ROLE,
        )
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR


@NEW_MODE
@pytest.mark.django_db
def test_inflight_llm2_race_after_none_lookup_still_marks_confirmed_after_summarization(
    nir_user: User, doctor_user: User
) -> None:
    prior_hist = _make_case(nir_user, days_ago=4, origin="HGE", age=50)
    _advance_to_awaiting_doctor(prior_hist)
    record_doctor_procedure_decisions(
        prior_hist,
        decisions={ANGIO_TYPE: (DoctorDisposition.DENIED, "Sem critério.")},
        user=doctor_user,
        role=DOCTOR_ROLE,
    )
    CaseProcedure.objects.filter(case=prior_hist, procedure_type=ANGIO_TYPE).update(
        doctor_decided_at=timezone.now() - timedelta(days=4)
    )
    prior_hist.record_doctor_decision(accepted=False, user=doctor_user, role=DOCTOR_ROLE)
    prior_hist.post_final_reply(user=None, role=SYSTEM_ROLE)
    prior_hist.nir_acknowledge(user=nir_user, role=NIR_ROLE)
    prior_hist.start_cleaning(user=None, role=SYSTEM_ROLE)
    prior_hist.complete_cleaning(user=None, role=SYSTEM_ROLE)

    incoming = _make_case(nir_user, days_ago=1, origin="HGE", age=50)
    _advance_to_llm_summarizing(incoming)
    # 1. Pipeline registrou PRIOR_CASE_LOOKUP(origin="none") antes da confirmação.
    record_prior_case_lookups(incoming, user=None, role=SYSTEM_ROLE)
    # 2. NIR confirma enquanto LLM2 ainda está em execução.
    (cand,) = reappearance_module.list_nir_reappearance_candidates(incoming)
    reappearance_module.respond_to_reappearance_conference(
        incoming=incoming,
        prior_case_id=prior_hist.pk,
        revision_digest=cand.revision_digest,
        decision="same_request",
        user=nir_user,
        role=NIR_ROLE,
    )
    # 3. LLM2 termina DEPOIS de confirmed_at, mas nunca consumiu nir_confirmed.
    incoming.summary_text = "Sumário gerado sem contexto confirmado."
    incoming.save(update_fields=["summary_text"])
    CaseEvent.objects.create(
        case=incoming,
        event_type=CaseEventType.CASE_LLM2_COMPLETED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role=SYSTEM_ROLE,
        payload={},
    )

    summary = lookup_prior_case_context(incoming, ANGIO_TYPE)
    assert summary is not None
    assert summary.origin == "nir_confirmed"
    assert summary.confirmed_after_summarization is True


@NEW_MODE
@pytest.mark.django_db
def test_multi_candidate_confirmed_awaiting_pipeline_blocks_second_active_candidate(
    nir_user: User,
) -> None:
    prior_1 = _make_case(nir_user, days_ago=5, origin="HGE", age=40)
    _advance_to_awaiting_doctor(prior_1)
    prior_2 = _make_case(nir_user, days_ago=4, origin="HGE", age=40)
    _advance_to_awaiting_doctor(prior_2)

    incoming = _make_case(nir_user, days_ago=1, origin="HGE", age=40)
    _advance_to_llm_summarizing(incoming)

    cands = reappearance_module.list_nir_reappearance_candidates(incoming)
    by_prior = {c.prior.pk: c for c in cands}
    assert set(by_prior) == {prior_1.pk, prior_2.pk}

    out_1 = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming,
        prior_case_id=prior_1.pk,
        revision_digest=by_prior[prior_1.pk].revision_digest,
        decision="same_request",
        user=nir_user,
        role=NIR_ROLE,
    )
    assert out_1.status == "confirmed_awaiting_pipeline"

    # P2 não deve mais aparecer no Pool A nem ser aceito em POST direto.
    remaining = reappearance_module.list_nir_reappearance_candidates(incoming)
    assert all(c.prior.pk != prior_2.pk for c in remaining)

    with pytest.raises(reappearance_module.ReappearanceStaleError):
        reappearance_module.respond_to_reappearance_conference(
            incoming=incoming,
            prior_case_id=prior_2.pk,
            revision_digest=by_prior[prior_2.pk].revision_digest,
            decision="same_request",
            user=nir_user,
            role=NIR_ROLE,
        )


def _advance_to_scheduler_requested(case: Case, doctor: User) -> None:
    _advance_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case,
        decisions={ANGIO_TYPE: (DoctorDisposition.APPROVED, "Indicação clínica válida.")},
        user=doctor,
        role=DOCTOR_ROLE,
    )
    case.record_doctor_decision(accepted=True, user=doctor, role=DOCTOR_ROLE)
    case.request_scheduling(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.SCHEDULER_REQUESTED


@NEW_MODE
@pytest.mark.django_db
def test_scheduling_repeat_confirmed_during_llm_extracting_with_live_lease_awaits_llm1(
    nir_user: User, doctor_user: User
) -> None:
    prior = _make_case(nir_user, days_ago=5, origin="HGE", age=40)
    _advance_to_scheduler_requested(prior, doctor_user)

    incoming = _make_case(nir_user, days_ago=1, origin="HGE", age=40)
    incoming.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    incoming.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    incoming.complete_anonymization(user=None, role=SYSTEM_ROLE)
    assert incoming.status == CaseStatus.LLM_EXTRACTING

    lock_token = claim_case_lock(
        incoming, user=None, context="worker_llm", role=SYSTEM_ROLE, lease_seconds=120
    ).token

    (cand,) = reappearance_module.list_nir_reappearance_candidates(incoming)
    outcome = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming,
        prior_case_id=prior.pk,
        revision_digest=cand.revision_digest,
        decision="same_request",
        user=nir_user,
        role=NIR_ROLE,
    )
    assert outcome.status == "confirmed_awaiting_pipeline"
    assert (
        CaseReappearance.objects.filter(
            incoming_case=incoming,
            prior_case=prior,
            state=ReappearanceState.CONFIRMED,
            resolution_kind=ResolutionKind.SCHEDULING_REPEAT,
        ).count()
        == 1
    )
    assert (
        CaseReappearance.objects.filter(
            incoming_case=incoming,
            state=ReappearanceState.PENDING,
        ).count()
        == 0
    )
    assert Schedule.objects.count() == 0

    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.LLM_EXTRACTING

    incoming.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    assert incoming.status == CaseStatus.LLM_SUMMARIZING

    archive_out = reappearance_module.archive_scheduling_repeat_after_llm1(
        case_pk=incoming.pk,
        lock_token=lock_token,
        reconciled=True,
        user=None,
        role=SYSTEM_ROLE,
    )
    assert archive_out.status == "archived"
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED


@NEW_MODE
@pytest.mark.django_db
def test_scheduling_repeat_confirmed_during_llm_summarizing_applies_on_finalize(
    nir_user: User, doctor_user: User
) -> None:
    prior = _make_case(nir_user, days_ago=5, origin="HGE", age=40)
    _advance_to_scheduler_requested(prior, doctor_user)

    incoming = _make_case(nir_user, days_ago=1, origin="HGE", age=40)
    _advance_to_llm_summarizing(incoming)

    (cand,) = reappearance_module.list_nir_reappearance_candidates(incoming)
    outcome = reappearance_module.respond_to_reappearance_conference(
        incoming=incoming,
        prior_case_id=prior.pk,
        revision_digest=cand.revision_digest,
        decision="same_request",
        user=nir_user,
        role=NIR_ROLE,
    )
    assert outcome.status == "confirmed_awaiting_pipeline"

    lock_token = claim_case_lock(
        incoming, user=None, context="worker_llm", role=SYSTEM_ROLE, lease_seconds=120
    ).token
    incoming.structured_data = {"pedido": {"procedimento": ANGIO_TYPE}}
    incoming.summary_text = "Resumo clínico sintético."
    incoming.policy_result = {ANGIO_TYPE: {"recommendation": "recomenda_aceitar", "criteria": []}}
    incoming.suggested_action = {
        "procedures": {ANGIO_TYPE: {"suggestion": "aceitar", "motivos": []}},
        "aggregate": {"suggestion": "aceitar", "motivos": []},
    }
    incoming.save(
        update_fields=["structured_data", "summary_text", "policy_result", "suggested_action"]
    )

    fin = reappearance_module.finalize_summarization(
        case_pk=incoming.pk,
        lock_token=lock_token,
        marked_execution=False,
        execution_digests=None,
        execution_authority=(None, None, None),
        execution_declared=(ANGIO_TYPE,),
        user=None,
        role=SYSTEM_ROLE,
    )
    assert fin.status == "completed"
    assert fin.lease_released is True

    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED
    assert prior.status == CaseStatus.SCHEDULER_REQUESTED
    assert CaseReappearance.objects.filter(
        incoming_case=incoming,
        prior_case=prior,
        state=ReappearanceState.APPLIED,
        resolution_kind=ResolutionKind.SCHEDULING_REPEAT,
    ).exists()
