"""Testes de comunicações operacionais por caso (change 03, slice 005, R1–R6).

Cobre o model ``CaseCommunicationMessage`` (R1), o serviço de post manual
(R2 — autor + papel ativo + timestamp, thread ordenada), a projeção sistêmica
por signal ``CaseEvent.post_save`` para o conjunto inicial de tipos canônicos
(R3 — mensagem ``system`` sem autor, vinculada ao evento por ``source_event``
O2O; tipos fora do conjunto não projetam), a idempotência da projeção (R4 —
re-save/chamada repetida não duplicam) e os 2 cenários da spec "Comunicações
operacionais por caso". R5 (mensagens system não geram notificação/badge/estado
de leitura) é check de ausência por inspeção do diff e do app de casos.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from django.db import models

from apps.accounts.models import User
from apps.cases.communications import (
    SYSTEM_COMMUNICATION_TEXTS,
    create_system_communication_notice_for_event,
    post_user_communication,
)
from apps.cases.events import CaseEventType, case_status_event_type
from apps.cases.models import (
    ActorType,
    Case,
    CaseCommunicationMessage,
    CaseEvent,
    CaseStatus,
    MessageType,
)

DOCTOR_ROLE = "doctor"
NIR_ROLE = "nir"
SCHEDULER_ROLE = "scheduler"
SYSTEM_ROLE = "system"


def _create_case(created_by: User) -> Case:
    return Case.objects.create(created_by=created_by)


def _case_in_state(state: str, *, created_by: User) -> Case:
    """Cria um caso e o dirige pelas transições válidas até ``state``."""
    case = _create_case(created_by)
    steps: dict[str, list[tuple[str, dict[str, object]]]] = {
        "PDF_EXTRACTING": [("start_pdf_extraction", {})],
        "AWAITING_DOCTOR": [
            ("start_pdf_extraction", {}),
            ("complete_pdf_extraction", {}),
            ("complete_anonymization", {}),
            ("complete_llm_extraction", {}),
            ("complete_llm_summarization", {}),
        ],
    }
    for operation, extra in steps[state]:
        getattr(case, operation)(user=None, role=SYSTEM_ROLE, **extra)
    assert case.status == state
    return case


# ── R1: CaseCommunicationMessage ───────────────────────────────────────────


def test_message_fields() -> None:
    """R1: shape do espelho do ats-web (message_id UUID pk, message_type
    user|system default user, case CASCADE, author PROTECT null, author_role,
    body, source_event O2O → CaseEvent, system_event_type, created_at e
    ordering por created_at)."""
    message_id = cast(Any, CaseCommunicationMessage._meta.get_field("message_id"))
    assert message_id.primary_key is True

    case_field = cast(Any, CaseCommunicationMessage._meta.get_field("case"))
    assert case_field.remote_field.on_delete == models.CASCADE
    assert case_field.remote_field.related_name == "communication_messages"

    message_type = cast(Any, CaseCommunicationMessage._meta.get_field("message_type"))
    assert dict(message_type.choices) == dict(MessageType.choices)
    assert message_type.default == MessageType.USER

    author = cast(Any, CaseCommunicationMessage._meta.get_field("author"))
    assert author.null is True
    assert author.blank is True
    assert author.remote_field.on_delete == models.PROTECT
    assert author.remote_field.related_name == "case_communication_messages"

    assert cast(Any, CaseCommunicationMessage._meta.get_field("author_role")).blank is True
    assert cast(Any, CaseCommunicationMessage._meta.get_field("system_event_type")).blank is True
    assert CaseCommunicationMessage._meta.get_field("body").__class__ is models.TextField

    source_event = cast(Any, CaseCommunicationMessage._meta.get_field("source_event"))
    assert source_event.one_to_one is True
    assert source_event.remote_field.related_name == "communication_notice"

    assert CaseCommunicationMessage._meta.ordering == ["created_at"]


# ── R2: post manual com autor + papel ──────────────────────────────────────


@pytest.mark.django_db
def test_post_user_message_records_role(
    nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    """Cenário spec: usuário autenticado com papel ativo ``scheduler`` posta no
    caso — a mensagem grava autor, papel ativo e timestamp."""
    scheduler = user_factory("scheduler-teste", SCHEDULER_ROLE)
    case = _create_case(nir_user)

    message = post_user_communication(
        case, user=scheduler, role=SCHEDULER_ROLE, body="Pedido de vaga prioritária."
    )

    assert message.message_type == MessageType.USER
    assert message.author == scheduler
    assert message.author_role == SCHEDULER_ROLE
    assert message.body == "Pedido de vaga prioritária."
    assert message.source_event is None
    assert message.system_event_type == ""
    assert message.created_at is not None

    stored = CaseCommunicationMessage.objects.get(pk=message.pk)
    assert stored.author == scheduler
    assert stored.author_role == SCHEDULER_ROLE


@pytest.mark.django_db
def test_post_user_messages_appear_ordered_in_thread(
    nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    """R6: posts manuais e a projeção sistêmica aparecem na mesma thread, em
    ordem cronológica (user → system → user)."""
    scheduler = user_factory("scheduler-ordem", SCHEDULER_ROLE)
    case = _create_case(nir_user)

    post_user_communication(case, user=scheduler, role=SCHEDULER_ROLE, body="primeira")
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    post_user_communication(case, user=scheduler, role=SCHEDULER_ROLE, body="segunda")

    thread = list(case.communication_messages.all())
    assert [message.message_type for message in thread] == [
        MessageType.USER,
        MessageType.SYSTEM,
        MessageType.USER,
    ]
    assert [message.body for message in thread] == [
        "primeira",
        SYSTEM_COMMUNICATION_TEXTS[case_status_event_type("AWAITING_DOCTOR")],
        "segunda",
    ]


# ── R3: projeção sistêmica de eventos da FSM ───────────────────────────────


@pytest.mark.django_db
def test_fsm_event_projects_system_message(nir_user: User) -> None:
    """Cenário spec: transição do conjunto inicial (chegada à fila médica)
    projeta mensagem ``system`` sem autor, vinculada ao evento por O2O."""
    case = _case_in_state("AWAITING_DOCTOR", created_by=nir_user)
    event_type = case_status_event_type("AWAITING_DOCTOR")
    event = case.events.get(event_type=event_type)

    messages = list(case.communication_messages.all())
    assert len(messages) == 1
    message = messages[0]
    assert message.message_type == MessageType.SYSTEM
    assert message.source_event == event
    assert message.author is None
    assert message.author_role == ""
    assert message.system_event_type == event_type
    assert message.body == SYSTEM_COMMUNICATION_TEXTS[event_type]
    assert message.created_at is not None


@pytest.mark.django_db
def test_non_projected_event_no_message(nir_user: User) -> None:
    """R3: eventos fora do conjunto inicial (transições de processamento e
    operações/locks) não projetam mensagem alguma."""
    case = _case_in_state("PDF_EXTRACTING", created_by=nir_user)
    assert case.communication_messages.count() == 0

    CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_LOCK_CLAIMED,
        actor_type=ActorType.USER,
        actor=nir_user,
        actor_role=NIR_ROLE,
        payload={},
    )
    assert case.communication_messages.count() == 0


# ── R4: idempotência da projeção ───────────────────────────────────────────


@pytest.mark.django_db
def test_projection_idempotent(nir_user: User) -> None:
    """R4: ``source_event`` O2O garante no máximo uma mensagem system por
    evento — re-save do evento (signal de novo) e chamadas repetidas da
    projeção não duplicam."""
    case = _case_in_state("AWAITING_DOCTOR", created_by=nir_user)
    event = case.events.get(event_type=case_status_event_type("AWAITING_DOCTOR"))
    assert case.communication_messages.count() == 1

    event.save()  # signal post_save dispara de novo com created=False
    first = create_system_communication_notice_for_event(event)
    second = create_system_communication_notice_for_event(event)

    assert first is not None
    assert second is not None
    assert first.pk == second.pk
    assert case.communication_messages.count() == 1


# ── R6: projeção das 4 transições do conjunto inicial ──────────────────────


@pytest.mark.django_db
def test_all_initial_projected_event_types_produce_messages(
    nir_user: User, doctor_user: User
) -> None:
    """R3/R6: cada tipo canônico do conjunto inicial projeta uma mensagem
    system com o texto canônico correspondente."""
    # AWAITING_DOCTOR (fim do pipeline) e DOCTOR_DENIED (negação médica).
    doctor_denied = _create_case(nir_user)
    _drive_to_awaiting_doctor(doctor_denied)
    doctor_denied.record_doctor_decision(accepted=False, user=doctor_user, role=DOCTOR_ROLE)

    # SCHEDULING_CONFIRMED e SCHEDULING_DENIED partem de AWAITING_DOCTOR.
    scheduling_confirmed = _create_case(nir_user)
    _drive_to_awaiting_doctor(scheduling_confirmed)
    scheduling_confirmed.record_doctor_decision(accepted=True, user=doctor_user, role=DOCTOR_ROLE)
    scheduling_confirmed.request_scheduling(user=None, role=SYSTEM_ROLE)
    scheduling_confirmed.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    scheduling_confirmed.confirm_scheduling(user=None, role=SYSTEM_ROLE)

    scheduling_denied = _create_case(nir_user)
    _drive_to_awaiting_doctor(scheduling_denied)
    scheduling_denied.record_doctor_decision(accepted=True, user=doctor_user, role=DOCTOR_ROLE)
    scheduling_denied.request_scheduling(user=None, role=SYSTEM_ROLE)
    scheduling_denied.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    scheduling_denied.deny_scheduling(user=None, role=SYSTEM_ROLE)

    expected: dict[Case, tuple[str, ...]] = {
        doctor_denied: (
            case_status_event_type("AWAITING_DOCTOR"),
            case_status_event_type("DOCTOR_DENIED"),
        ),
        scheduling_confirmed: (
            case_status_event_type("AWAITING_DOCTOR"),
            case_status_event_type("SCHEDULING_CONFIRMED"),
        ),
        scheduling_denied: (
            case_status_event_type("AWAITING_DOCTOR"),
            case_status_event_type("SCHEDULING_DENIED"),
        ),
    }
    for case, projected_types in expected.items():
        messages = list(case.communication_messages.all())
        assert [message.system_event_type for message in messages] == list(projected_types)
        for message in messages:
            assert message.message_type == MessageType.SYSTEM
            assert message.author is None
            assert message.system_event_type in SYSTEM_COMMUNICATION_TEXTS
            assert message.body == SYSTEM_COMMUNICATION_TEXTS[message.system_event_type]


def _drive_to_awaiting_doctor(case: Case) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
