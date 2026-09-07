"""Testes do Case + FSM de 17 estados + CaseEvent (change 03, slice 002, R1–R7).

Cobre a tabela completa de transições do design D4 (cada (operação, source)
individualmente: source válido progride + grava evento; source inválido
rejeita sem efeito), os 5 cenários da spec "FSM de 17 estados com transições
protegidas", o cenário de evento com ator/papel da spec ("Trilha de auditoria
append-only"), a gravação direta (R5) e a proteção do status contra atribuição
direta (R1).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from django.db import transaction
from django_fsm import TransitionNotAllowed

from apps.accounts.models import User
from apps.cases.events import CaseEventType, case_status_event_type
from apps.cases.models import Case, CaseStatus

DOCTOR_ROLE = "doctor"
NIR_ROLE = "nir"
SCHEDULER_ROLE = "scheduler"
SYSTEM_ROLE = "system"

# Caminho (operação, kwargs extras) de NEW até cada estado usado como source.
# Estados de processamento são percorridos pelas operações de conclusão; as
# self-transitions de início de worker são opcionais e cobertas individualmente.
_STEPS_TO_STATE: dict[str, list[tuple[str, dict[str, object]]]] = {
    "NEW": [],
    "PDF_EXTRACTING": [("start_pdf_extraction", {})],
    "ANONYMIZING": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
    ],
    "LLM_EXTRACTING": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
    ],
    "LLM_SUMMARIZING": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
        ("complete_llm_extraction", {}),
    ],
    "AWAITING_DOCTOR": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
        ("complete_llm_extraction", {}),
        ("complete_llm_summarization", {}),
    ],
    "DOCTOR_DENIED": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
        ("complete_llm_extraction", {}),
        ("complete_llm_summarization", {}),
        ("record_doctor_decision", {"accepted": False}),
    ],
    "DOCTOR_ACCEPTED": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
        ("complete_llm_extraction", {}),
        ("complete_llm_summarization", {}),
        ("record_doctor_decision", {"accepted": True}),
    ],
    "SCHEDULER_REQUESTED": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
        ("complete_llm_extraction", {}),
        ("complete_llm_summarization", {}),
        ("record_doctor_decision", {"accepted": True}),
        ("request_scheduling", {}),
    ],
    "AWAITING_SCHEDULING": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
        ("complete_llm_extraction", {}),
        ("complete_llm_summarization", {}),
        ("record_doctor_decision", {"accepted": True}),
        ("request_scheduling", {}),
        ("await_scheduling_confirmation", {}),
    ],
    "SCHEDULING_CONFIRMED": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
        ("complete_llm_extraction", {}),
        ("complete_llm_summarization", {}),
        ("record_doctor_decision", {"accepted": True}),
        ("request_scheduling", {}),
        ("await_scheduling_confirmation", {}),
        ("confirm_scheduling", {}),
    ],
    "SCHEDULING_DENIED": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
        ("complete_llm_extraction", {}),
        ("complete_llm_summarization", {}),
        ("record_doctor_decision", {"accepted": True}),
        ("request_scheduling", {}),
        ("await_scheduling_confirmation", {}),
        ("deny_scheduling", {}),
    ],
    "FAILED": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("fail_processing", {"reason": "motivo de falha do setup"}),
    ],
    "FINAL_REPLY_POSTED": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
        ("complete_llm_extraction", {}),
        ("complete_llm_summarization", {}),
        ("record_doctor_decision", {"accepted": False}),
        ("post_final_reply", {}),
    ],
    "AWAITING_NIR_ACK": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
        ("complete_llm_extraction", {}),
        ("complete_llm_summarization", {}),
        ("record_doctor_decision", {"accepted": False}),
        ("post_final_reply", {}),
        ("nir_acknowledge", {}),
    ],
    "CLEANING": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
        ("complete_llm_extraction", {}),
        ("complete_llm_summarization", {}),
        ("record_doctor_decision", {"accepted": False}),
        ("post_final_reply", {}),
        ("nir_acknowledge", {}),
        ("start_cleaning", {}),
    ],
    "CLEANED": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("complete_anonymization", {}),
        ("complete_llm_extraction", {}),
        ("complete_llm_summarization", {}),
        ("record_doctor_decision", {"accepted": False}),
        ("post_final_reply", {}),
        ("nir_acknowledge", {}),
        ("start_cleaning", {}),
        ("complete_cleaning", {}),
    ],
}

# Tabela D4 por (operação, source): cada linha é um teste individual.
# (operação, source, target, source-inválido p/ teste de rejeição, kwargs)
_TRANSITION_ROWS: tuple[tuple[str, str, str, str, dict[str, object]], ...] = (
    ("start_pdf_extraction", "NEW", "PDF_EXTRACTING", "AWAITING_DOCTOR", {}),
    ("complete_pdf_extraction", "PDF_EXTRACTING", "ANONYMIZING", "NEW", {}),
    ("start_anonymization", "ANONYMIZING", "ANONYMIZING", "NEW", {}),
    ("complete_anonymization", "ANONYMIZING", "LLM_EXTRACTING", "NEW", {}),
    ("start_llm_extraction", "LLM_EXTRACTING", "LLM_EXTRACTING", "NEW", {}),
    ("complete_llm_extraction", "LLM_EXTRACTING", "LLM_SUMMARIZING", "NEW", {}),
    ("start_llm_summarization", "LLM_SUMMARIZING", "LLM_SUMMARIZING", "NEW", {}),
    ("complete_llm_summarization", "LLM_SUMMARIZING", "AWAITING_DOCTOR", "NEW", {}),
    (
        "fail_processing",
        "PDF_EXTRACTING",
        "FAILED",
        "AWAITING_DOCTOR",
        {"reason": "pdf corrompido"},
    ),
    (
        "fail_processing",
        "ANONYMIZING",
        "FAILED",
        "AWAITING_DOCTOR",
        {"reason": "recognizer falhou"},
    ),
    (
        "fail_processing",
        "LLM_EXTRACTING",
        "FAILED",
        "AWAITING_DOCTOR",
        {"reason": "schema inválido"},
    ),
    (
        "fail_processing",
        "LLM_SUMMARIZING",
        "FAILED",
        "AWAITING_DOCTOR",
        {"reason": "prompt estourou"},
    ),
    ("record_doctor_decision", "AWAITING_DOCTOR", "DOCTOR_DENIED", "NEW", {"accepted": False}),
    ("record_doctor_decision", "AWAITING_DOCTOR", "DOCTOR_ACCEPTED", "NEW", {"accepted": True}),
    ("request_scheduling", "DOCTOR_ACCEPTED", "SCHEDULER_REQUESTED", "DOCTOR_DENIED", {}),
    (
        "await_scheduling_confirmation",
        "SCHEDULER_REQUESTED",
        "AWAITING_SCHEDULING",
        "DOCTOR_ACCEPTED",
        {},
    ),
    (
        "confirm_scheduling",
        "AWAITING_SCHEDULING",
        "SCHEDULING_CONFIRMED",
        "SCHEDULER_REQUESTED",
        {},
    ),
    ("deny_scheduling", "AWAITING_SCHEDULING", "SCHEDULING_DENIED", "SCHEDULER_REQUESTED", {}),
    ("post_final_reply", "DOCTOR_DENIED", "FINAL_REPLY_POSTED", "AWAITING_DOCTOR", {}),
    ("post_final_reply", "SCHEDULING_CONFIRMED", "FINAL_REPLY_POSTED", "AWAITING_DOCTOR", {}),
    ("post_final_reply", "SCHEDULING_DENIED", "FINAL_REPLY_POSTED", "AWAITING_DOCTOR", {}),
    ("nir_acknowledge", "FINAL_REPLY_POSTED", "AWAITING_NIR_ACK", "DOCTOR_DENIED", {}),
    ("start_cleaning", "AWAITING_NIR_ACK", "CLEANING", "FINAL_REPLY_POSTED", {}),
    ("complete_cleaning", "CLEANING", "CLEANED", "AWAITING_NIR_ACK", {}),
)


def _create_case(created_by: User) -> Case:
    return Case.objects.create(created_by=created_by)


def _case_in_state(state: str, *, created_by: User) -> Case:
    """Cria um caso e o dirige pelas transições válidas até ``state``."""
    case = _create_case(created_by)
    for operation, extra in _STEPS_TO_STATE[state]:
        getattr(case, operation)(user=None, role=SYSTEM_ROLE, **extra)
    assert case.status == state
    return case


def _event_types(case: Case) -> list[str]:
    return [event.event_type for event in case.events.order_by("id")]


# ── R1: Case enxuto e status protegido ─────────────────────────────────────


@pytest.mark.django_db
def test_new_case_defaults_to_new(nir_user: User) -> None:
    case = _create_case(nir_user)
    case.refresh_from_db()
    assert case.status == CaseStatus.NEW


@pytest.mark.django_db
def test_status_protected_against_direct_assignment(nir_user: User) -> None:
    case = _case_in_state("NEW", created_by=nir_user)
    with pytest.raises(AttributeError, match="Direct status modification is not allowed"):
        case.status = CaseStatus.CLEANED
    case.refresh_from_db()
    assert case.status == CaseStatus.NEW


# ── R2/R6: cada transição da tabela D4, individualmente ────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(("operation", "source", "target", "_invalid", "extra"), _TRANSITION_ROWS)
def test_transition_valid_source_progresses_and_records_event(
    operation: str,
    source: str,
    target: str,
    _invalid: str,
    extra: dict[str, object],
    nir_user: User,
) -> None:
    case = _case_in_state(source, created_by=nir_user)
    events_before = case.events.count()

    getattr(case, operation)(user=nir_user, role=NIR_ROLE, **extra)

    case.refresh_from_db()
    assert case.status == target
    assert case.events.count() == events_before + 1
    event = case.events.order_by("id").last()
    assert event is not None
    assert event.event_type == case_status_event_type(target)
    assert event.event_type in CaseEventType.values
    # Payload canônico {source, target}; ``reason`` do fail_processing também
    # entra no payload (R5) — demais kwargs são argumentos da operação.
    expected_payload: dict[str, object] = {"source": source, "target": target}
    if isinstance(extra.get("reason"), str):
        expected_payload["reason"] = extra["reason"]
    assert event.payload == expected_payload


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("operation", "_source", "_target", "invalid_source", "extra"), _TRANSITION_ROWS
)
def test_transition_invalid_source_rejected_without_effect(
    operation: str,
    _source: str,
    _target: str,
    invalid_source: str,
    extra: dict[str, object],
    nir_user: User,
) -> None:
    case = _case_in_state(invalid_source, created_by=nir_user)
    events_before = case.events.count()

    with pytest.raises(TransitionNotAllowed):
        getattr(case, operation)(user=nir_user, role=NIR_ROLE, **extra)

    case.refresh_from_db()
    assert case.status == invalid_source
    assert case.events.count() == events_before


# ── Spec FSM: 5 cenários ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_happy_path_to_awaiting_doctor(nir_user: User) -> None:
    """Cenário: caminho feliz até a fila médica."""
    case = _create_case(nir_user)
    case.start_pdf_extraction(user=nir_user, role=NIR_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()

    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert _event_types(case) == [
        case_status_event_type("PDF_EXTRACTING"),
        case_status_event_type("ANONYMIZING"),
        case_status_event_type("LLM_EXTRACTING"),
        case_status_event_type("LLM_SUMMARIZING"),
        case_status_event_type("AWAITING_DOCTOR"),
    ]


@pytest.mark.django_db
def test_doctor_denial_goes_directly_to_final_reply(doctor_user: User) -> None:
    """Cenário: negação médica chega a FINAL_REPLY_POSTED sem fila de agendamento."""
    case = _case_in_state("AWAITING_DOCTOR", created_by=doctor_user)
    case.record_doctor_decision(accepted=False, user=doctor_user, role=DOCTOR_ROLE)
    assert case.status == CaseStatus.DOCTOR_DENIED
    case.post_final_reply(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()

    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    event_types = _event_types(case)
    assert event_types[-2:] == [
        case_status_event_type("DOCTOR_DENIED"),
        case_status_event_type("FINAL_REPLY_POSTED"),
    ]
    assert case_status_event_type("DOCTOR_ACCEPTED") not in event_types
    assert case_status_event_type("SCHEDULER_REQUESTED") not in event_types


@pytest.mark.django_db
def test_doctor_acceptance_goes_to_scheduling(doctor_user: User) -> None:
    """Cenário: aceitação médica segue DOCTOR_ACCEPTED → SCHEDULER_REQUESTED → AWAITING_SCHEDULING."""
    case = _case_in_state("AWAITING_DOCTOR", created_by=doctor_user)
    case.record_doctor_decision(accepted=True, user=doctor_user, role=DOCTOR_ROLE)
    case.request_scheduling(user=None, role=SYSTEM_ROLE)
    case.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()

    assert case.status == CaseStatus.AWAITING_SCHEDULING
    event_types = _event_types(case)
    assert event_types[-3:] == [
        case_status_event_type("DOCTOR_ACCEPTED"),
        case_status_event_type("SCHEDULER_REQUESTED"),
        case_status_event_type("AWAITING_SCHEDULING"),
    ]


@pytest.mark.django_db
def test_invalid_transition_rejected(doctor_user: User) -> None:
    """Cenário: operação que não parte do estado atual é rejeitada sem efeito."""
    case = _case_in_state("AWAITING_DOCTOR", created_by=doctor_user)
    events_before = case.events.count()

    with pytest.raises(TransitionNotAllowed):
        case.confirm_scheduling(user=doctor_user, role=DOCTOR_ROLE)

    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert case.events.count() == events_before


@pytest.mark.django_db
def test_fail_processing_records_reason(nir_user: User) -> None:
    """Cenário: falha de processamento leva a FAILED com motivo na trilha."""
    case = _case_in_state("ANONYMIZING", created_by=nir_user)
    reason = "presidio indisponível"
    case.fail_processing(reason=reason, user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()

    assert case.status == CaseStatus.FAILED
    event = case.events.order_by("id").last()
    assert event is not None
    assert event.event_type == case_status_event_type("FAILED")
    assert event.payload == {
        "source": "ANONYMIZING",
        "target": "FAILED",
        "reason": reason,
    }


# ── R5: aceitação médica encadeada atomicamente ────────────────────────────


@pytest.mark.django_db
def test_acceptance_chain_events(doctor_user: User) -> None:
    """Aceitação + request_scheduling encadeadas: eventos em DOCTOR_ACCEPTED e
    SCHEDULER_REQUESTED (gravação direta no mesmo atomic)."""
    case = _case_in_state("AWAITING_DOCTOR", created_by=doctor_user)
    events_before = case.events.count()

    with transaction.atomic():
        case.record_doctor_decision(accepted=True, user=doctor_user, role=DOCTOR_ROLE)
        case.request_scheduling(user=None, role=SYSTEM_ROLE)

    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.events.count() == events_before + 2
    assert _event_types(case)[-2:] == [
        case_status_event_type("DOCTOR_ACCEPTED"),
        case_status_event_type("SCHEDULER_REQUESTED"),
    ]


@pytest.mark.django_db
def test_acceptance_chain_is_atomic(doctor_user: User) -> None:
    """Falha no meio do encadeamento desfaz estado e eventos juntos."""
    case = _case_in_state("AWAITING_DOCTOR", created_by=doctor_user)
    events_before = case.events.count()

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            case.record_doctor_decision(accepted=True, user=doctor_user, role=DOCTOR_ROLE)
            case.request_scheduling(user=None, role=SYSTEM_ROLE)
            msg = "falha posterior ao encadeamento"
            raise RuntimeError(msg)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.AWAITING_DOCTOR
    assert fresh.events.count() == events_before


# ── Spec "Trilha de auditoria": cenário de transição com ator e papel ─────


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("role", "username", "operation", "source", "extra"),
    [
        (NIR_ROLE, "nir-ator", "start_pdf_extraction", "NEW", {}),
        (
            DOCTOR_ROLE,
            "doctor-ator",
            "record_doctor_decision",
            "AWAITING_DOCTOR",
            {"accepted": True},
        ),
        (SCHEDULER_ROLE, "scheduler-ator", "confirm_scheduling", "AWAITING_SCHEDULING", {}),
    ],
)
def test_event_recorded_with_actor_and_role(
    role: str,
    username: str,
    operation: str,
    source: str,
    extra: dict[str, object],
    user_factory: Callable[[str, str], User],
) -> None:
    user = user_factory(username, role)
    case = _case_in_state(source, created_by=user)

    getattr(case, operation)(user=user, role=role, **extra)

    event = case.events.order_by("id").last()
    assert event is not None
    assert event.actor == user
    assert event.actor_type == "user"
    assert event.actor_role == role
    assert event.payload["source"] == source


@pytest.mark.django_db
def test_worker_transition_records_system_actor(nir_user: User) -> None:
    """Worker passa role='system' sem usuário: ator do evento é o sistema."""
    case = _case_in_state("PDF_EXTRACTING", created_by=nir_user)

    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)

    event = case.events.order_by("id").last()
    assert event is not None
    assert event.actor is None
    assert event.actor_type == "system"
    assert event.actor_role == SYSTEM_ROLE


# ── R6: caminho de aceitação completo até CLEANED ─────────────────────────


@pytest.mark.django_db
def test_acceptance_full_path_to_cleaned(nir_user: User, doctor_user: User) -> None:
    case = _case_in_state("AWAITING_DOCTOR", created_by=nir_user)
    case.record_doctor_decision(accepted=True, user=doctor_user, role=DOCTOR_ROLE)
    case.request_scheduling(user=None, role=SYSTEM_ROLE)
    case.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    case.confirm_scheduling(user=doctor_user, role=DOCTOR_ROLE)
    case.post_final_reply(user=None, role=SYSTEM_ROLE)
    case.nir_acknowledge(user=nir_user, role=NIR_ROLE)
    case.start_cleaning(user=None, role=SYSTEM_ROLE)
    case.complete_cleaning(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()

    assert case.status == CaseStatus.CLEANED
    assert _event_types(case)[-8:] == [
        case_status_event_type("DOCTOR_ACCEPTED"),
        case_status_event_type("SCHEDULER_REQUESTED"),
        case_status_event_type("AWAITING_SCHEDULING"),
        case_status_event_type("SCHEDULING_CONFIRMED"),
        case_status_event_type("FINAL_REPLY_POSTED"),
        case_status_event_type("AWAITING_NIR_ACK"),
        case_status_event_type("CLEANING"),
        case_status_event_type("CLEANED"),
    ]


# ── Guardas de coerência do contrato ───────────────────────────────────────


def test_canonical_event_types_cover_all_transition_targets() -> None:
    """Cada estado-alvo da tabela D4 tem tipo canônico CASE_STATUS_<ESTADO>."""
    targets = {target for (_operation, _source, target, _invalid, _extra) in _TRANSITION_ROWS}
    assert targets == set(CaseStatus.values) - {CaseStatus.NEW}
    for target in targets:
        assert case_status_event_type(target) == f"CASE_STATUS_{target}"
