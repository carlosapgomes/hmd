"""Testes da intercorrência pós-agendamento (scheduler-multi-unit, slice 002, R1–R5).

Cobre a transição FSM ``reopen_scheduling`` (``FINAL_REPLY_POSTED`` →
``AWAITING_SCHEDULING`` — segunda transição nova pós-change-03, sem estado
novo) e o serviço ``reopen_scheduling_after_incident``: reabertura de caso
confirmado na unidade 1 com motivo obrigatório (transição + limpeza dos 5
campos de agendamento — inclusive ``scheduled_by`` — + evento com ``reason`` +
comunicação ao NIR com o motivo), bloqueio da unidade 2 com erro nomeado,
estados fora da janela (``SCHEDULING_CONFIRMED`` e ``AWAITING_NIR_ACK``) e
motivo vazio rejeitados sem efeito, e o ciclo reabrir → reconfirmar com NOVA
resposta final ao NIR (origem ``AWAITING_SCHEDULING`` aceita no confirmar do
slice 001 — branch por estado pula o ``await``). Os cenários espelham os da
spec ``scheduling`` ("Intercorrência na unidade 1 retorna o caso à fila",
"Intercorrência na unidade 2 é bloqueada", "Reconfirmação após intercorrência").
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.utils import timezone
from django_fsm import TransitionNotAllowed

from apps.accounts.models import Role, User
from apps.cases.events import case_status_event_type
from apps.cases.models import Case, CaseCommunicationMessage, CaseStatus, MessageType
from apps.scheduler.services import (
    REPLY_REOPEN_TEMPLATE,
    REPLY_UNIT_1_TEMPLATE,
    confirm_case_scheduling,
    reopen_scheduling_after_incident,
)

SCHEDULER_ROLE = "scheduler"
SYSTEM_ROLE = "system"

# Motivo da intercorrência nos cenários do slice.
REOPEN_REASON = "vaga desmarcada pela unidade de origem — nova data solicitada"

# Formato de exibição das respostas ao NIR (espelho do presenter médico).
_DATETIME_DISPLAY_FORMAT = "%d/%m/%Y %H:%M"

# Passos de processamento comuns a todos os caminhos (NEW → AWAITING_DOCTOR).
_PROCESSING_STEPS: list[tuple[str, dict[str, object]]] = [
    ("start_pdf_extraction", {}),
    ("complete_pdf_extraction", {}),
    ("complete_anonymization", {}),
    ("complete_llm_extraction", {}),
    ("complete_llm_summarization", {}),
]

# Caminho (operação, kwargs) de NEW até cada estado usado como source do slice.
_STEPS_TO_STATE: dict[str, list[tuple[str, dict[str, object]]]] = {
    "SCHEDULER_REQUESTED": [
        *_PROCESSING_STEPS,
        ("record_doctor_decision", {"accepted": True}),
        ("request_scheduling", {}),
    ],
    "SCHEDULING_CONFIRMED": [
        *_PROCESSING_STEPS,
        ("record_doctor_decision", {"accepted": True}),
        ("request_scheduling", {}),
        ("await_scheduling_confirmation", {}),
        ("confirm_scheduling", {}),
    ],
    # Caminho da negação médica (sem dados de agendamento): FINAL_REPLY_POSTED
    # com ``scheduled_unit`` vazio — cobertura do guard de unidade.
    "FINAL_REPLY_POSTED": [
        *_PROCESSING_STEPS,
        ("record_doctor_decision", {"accepted": False}),
        ("post_final_reply", {}),
    ],
    "AWAITING_NIR_ACK": [
        *_PROCESSING_STEPS,
        ("record_doctor_decision", {"accepted": False}),
        ("post_final_reply", {}),
        ("nir_acknowledge", {}),
    ],
}


def _make_user(username: str, role: str) -> User:
    """Cria usuário com um papel (papel criado se não existir)."""
    role_obj, _ = Role.objects.get_or_create(name=role)
    user = User.objects.create_user(username=username, password="senha-teste")
    user.roles.add(role_obj)
    return user


def _case_in_state(state: str, *, created_by: User) -> Case:
    """Cria um caso e o dirige pelas transições válidas até ``state``."""
    case = Case.objects.create(created_by=created_by)
    for operation, extra in _STEPS_TO_STATE[state]:
        getattr(case, operation)(user=None, role=SYSTEM_ROLE, **extra)
    assert case.status == state
    return case


def _future_datetime(**kwargs: int) -> datetime:
    """Data/hora aware futura (UTC) a partir de agora."""
    return timezone.now() + timedelta(**kwargs)


def _display_local(value: datetime) -> str:
    """Data/hora local formatada como entra nas respostas ao NIR."""
    return timezone.localtime(value).strftime(_DATETIME_DISPLAY_FORMAT)


def _confirmed_case(unit: int, *, created_by: User) -> Case:
    """Caso confirmado pelo serviço do slice 001: FINAL_REPLY_POSTED com os 5
    campos de agendamento persistidos e a resposta final ao NIR postada."""
    case = _case_in_state("SCHEDULER_REQUESTED", created_by=created_by)
    confirm_case_scheduling(
        case,
        unit=unit,
        scheduled_datetime=_future_datetime(days=2),
        scheduled_location="Sala de hemodinâmica",
        user=created_by,
        role=SCHEDULER_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


def _user_messages(case: Case) -> list[CaseCommunicationMessage]:
    """Mensagens manuais (user) da thread do caso, em ordem."""
    return list(case.communication_messages.filter(message_type=MessageType.USER))


@pytest.fixture
def scheduler_user() -> User:
    """Agendador ator dos serviços (papel ``scheduler``)."""
    return _make_user("agendador-slice-002", SCHEDULER_ROLE)


# ── R1: transição FSM reopen_scheduling ────────────────────────────────────


@pytest.mark.django_db
def test_reopen_transition_happy(scheduler_user: User) -> None:
    """R1/R5: FINAL_REPLY_POSTED → AWAITING_SCHEDULING com o evento
    ``CASE_STATUS_AWAITING_SCHEDULING`` carregando o source real e o ``reason``
    no payload (``_run_transition`` + extra_payload)."""
    case = _case_in_state("FINAL_REPLY_POSTED", created_by=scheduler_user)
    events_before = case.events.count()

    case.reopen_scheduling(reason=REOPEN_REASON, user=scheduler_user, role=SCHEDULER_ROLE)
    case.refresh_from_db()

    assert case.status == CaseStatus.AWAITING_SCHEDULING
    assert case.events.count() == events_before + 1
    event = case.events.order_by("id").last()
    assert event is not None
    assert event.event_type == case_status_event_type("AWAITING_SCHEDULING")
    assert event.payload == {
        "source": "FINAL_REPLY_POSTED",
        "target": "AWAITING_SCHEDULING",
        "reason": REOPEN_REASON,
    }
    assert event.actor == scheduler_user
    assert event.actor_role == SCHEDULER_ROLE


@pytest.mark.django_db
def test_reopen_wrong_source_blocked(scheduler_user: User) -> None:
    """R1/R5: reopen de estado fora de FINAL_REPLY_POSTED → TransitionNotAllowed
    sem efeito (status e trilha intactos)."""
    case = _case_in_state("SCHEDULING_CONFIRMED", created_by=scheduler_user)
    events_before = case.events.count()

    with pytest.raises(TransitionNotAllowed):
        case.reopen_scheduling(reason=REOPEN_REASON, user=scheduler_user, role=SCHEDULER_ROLE)

    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULING_CONFIRMED
    assert case.events.count() == events_before


# ── R2: reabertura na unidade 1 / bloqueio da unidade 2 ────────────────────


@pytest.mark.django_db
def test_reopen_unit_1_clears_fields_and_notifies(scheduler_user: User) -> None:
    """R2/R5: intercorrência na unidade 1 → AWAITING_SCHEDULING com os 5 campos
    limpos (inclusive ``scheduled_by``), ``scheduling_reopen_reason`` persistido,
    evento com ``reason`` e comunicação ao NIR com o motivo."""
    case = _confirmed_case(1, created_by=scheduler_user)
    events_before = case.events.count()

    reopen_scheduling_after_incident(
        case, reason=REOPEN_REASON, user=scheduler_user, role=SCHEDULER_ROLE
    )

    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_SCHEDULING
    assert case.events.count() == events_before + 1
    event = case.events.order_by("id").last()
    assert event is not None
    assert event.event_type == case_status_event_type("AWAITING_SCHEDULING")
    assert event.payload == {
        "source": "FINAL_REPLY_POSTED",
        "target": "AWAITING_SCHEDULING",
        "reason": REOPEN_REASON,
    }
    # Os 5 campos de agendamento limpos no mesmo atomic.
    assert case.scheduled_unit is None
    assert case.scheduled_datetime is None
    assert case.scheduled_location == ""
    assert case.scheduled_by is None
    assert case.scheduled_decided_at is None
    assert case.scheduling_reopen_reason == REOPEN_REASON

    # Comunicação ao NIR: user message autoral com o motivo interpolado.
    messages = _user_messages(case)
    assert len(messages) == 2
    message = messages[-1]
    assert message.author == scheduler_user
    assert message.author_role == SCHEDULER_ROLE
    assert message.body == REPLY_REOPEN_TEMPLATE.format(reason=REOPEN_REASON)
    assert REOPEN_REASON in message.body


@pytest.mark.django_db
def test_reopen_unit_2_blocked(scheduler_user: User) -> None:
    """R2/R5: unidade 2 → erro nomeado "intercorrência desabilitada para
    unidade 2" e caso inalterado (sem evento, sem escrita, sem comunicação)."""
    case = _confirmed_case(2, created_by=scheduler_user)
    events_before = case.events.count()

    with pytest.raises(ValueError, match="intercorrência desabilitada para unidade 2"):
        reopen_scheduling_after_incident(
            case, reason=REOPEN_REASON, user=scheduler_user, role=SCHEDULER_ROLE
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.scheduled_unit == 2
    assert case.scheduled_datetime is not None
    assert case.scheduled_by == scheduler_user
    assert case.events.count() == events_before
    assert case.scheduling_reopen_reason == ""
    assert len(_user_messages(case)) == 1


@pytest.mark.django_db
def test_reopen_no_confirmed_schedule_blocked(scheduler_user: User) -> None:
    """R2: FINAL_REPLY_POSTED sem agendamento confirmado (negação médica, sem
    unidade) → erro nomeado e caso inalterado."""
    case = _case_in_state("FINAL_REPLY_POSTED", created_by=scheduler_user)
    assert case.scheduled_unit is None
    events_before = case.events.count()

    with pytest.raises(ValueError, match="intercorrência desabilitada"):
        reopen_scheduling_after_incident(
            case, reason=REOPEN_REASON, user=scheduler_user, role=SCHEDULER_ROLE
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.events.count() == events_before
    assert case.scheduling_reopen_reason == ""
    assert _user_messages(case) == []


# ── R3: estados fora da janela e motivo vazio ──────────────────────────────


@pytest.mark.django_db
def test_reopen_out_of_window_scheduling_confirmed(scheduler_user: User) -> None:
    """R3: caso em SCHEDULING_CONFIRMED (fora da janela) → erro nomeado sem efeito."""
    case = _case_in_state("SCHEDULING_CONFIRMED", created_by=scheduler_user)
    events_before = case.events.count()

    with pytest.raises(ValueError, match="estado"):
        reopen_scheduling_after_incident(
            case, reason=REOPEN_REASON, user=scheduler_user, role=SCHEDULER_ROLE
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULING_CONFIRMED
    assert case.events.count() == events_before
    assert case.scheduling_reopen_reason == ""
    assert _user_messages(case) == []


@pytest.mark.django_db
def test_reopen_out_of_window_awaiting_nir_ack(scheduler_user: User) -> None:
    """R3: caso em AWAITING_NIR_ACK (ciência do NIR já dada) → erro nomeado
    sem efeito (dados de agendamento preservados)."""
    case = _confirmed_case(1, created_by=scheduler_user)
    case.nir_acknowledge(user=scheduler_user, role=SCHEDULER_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_NIR_ACK
    events_before = case.events.count()

    with pytest.raises(ValueError, match="estado"):
        reopen_scheduling_after_incident(
            case, reason=REOPEN_REASON, user=scheduler_user, role=SCHEDULER_ROLE
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_NIR_ACK
    assert case.scheduled_unit == 1
    assert case.scheduled_by == scheduler_user
    assert case.events.count() == events_before
    assert case.scheduling_reopen_reason == ""
    assert len(_user_messages(case)) == 1


@pytest.mark.django_db
def test_reopen_empty_reason(scheduler_user: User) -> None:
    """R3: motivo vazio (após strip) → erro nomeado antes de qualquer escrita."""
    case = _confirmed_case(1, created_by=scheduler_user)
    events_before = case.events.count()

    with pytest.raises(ValueError, match="motivo"):
        reopen_scheduling_after_incident(
            case, reason="   ", user=scheduler_user, role=SCHEDULER_ROLE
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.scheduled_unit == 1
    assert case.scheduled_by == scheduler_user
    assert case.events.count() == events_before
    assert case.scheduling_reopen_reason == ""
    assert len(_user_messages(case)) == 1


# ── R4: ciclo reabrir → reconfirmar com nova resposta ──────────────────────


@pytest.mark.django_db
def test_reopen_then_reconfirm_cycle(scheduler_user: User) -> None:
    """R4/R5: reaberto por intercorrência, o confirmar do slice 001 aceita o
    caso (origem AWAITING_SCHEDULING pula o ``await``) e produz NOVA resposta
    final ao NIR com os novos dados — 2 eventos CASE_STATUS_* no ciclo."""
    case = _confirmed_case(1, created_by=scheduler_user)
    reopen_scheduling_after_incident(
        case, reason=REOPEN_REASON, user=scheduler_user, role=SCHEDULER_ROLE
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_SCHEDULING

    new_datetime = _future_datetime(days=5)
    new_location = "Bloco B — sala 7"
    confirm_case_scheduling(
        case,
        unit=1,
        scheduled_datetime=new_datetime,
        scheduled_location=new_location,
        user=scheduler_user,
        role=SCHEDULER_ROLE,
    )

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.scheduled_unit == 1
    assert case.scheduled_datetime == new_datetime
    assert case.scheduled_location == new_location
    assert case.scheduled_by == scheduler_user
    assert case.scheduled_decided_at is not None

    # Reconfirmação a partir de AWAITING_SCHEDULING: apenas confirm + final
    # reply (sem novo ``await``) — segunda resposta final na trilha.
    event_types = list(case.events.values_list("event_type", flat=True))
    assert event_types[-2:] == [
        case_status_event_type("SCHEDULING_CONFIRMED"),
        case_status_event_type("FINAL_REPLY_POSTED"),
    ]
    assert event_types.count(case_status_event_type("FINAL_REPLY_POSTED")) == 2
    assert event_types.count(case_status_event_type("AWAITING_SCHEDULING")) == 2

    # Nova resposta final ao NIR com os novos dados (1ª confirmação + 1ª
    # intercorrência + 2ª confirmação = 3 user messages).
    messages = _user_messages(case)
    assert len(messages) == 3
    assert messages[-1].body == REPLY_UNIT_1_TEMPLATE.format(
        location=new_location, scheduled_at=_display_local(new_datetime)
    )
    assert REOPEN_REASON not in messages[-1].body
