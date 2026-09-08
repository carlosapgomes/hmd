"""Testes dos serviços de agendamento (scheduler-multi-unit, slice 001, R1–R6).

Cobre os campos de agendamento do ``Case`` (R1 — shape e migration única
``0008_case_scheduling``) e os serviços transacionais de ``apps/scheduler``
(R2–R5): confirmação com unidade 1|2 encadeando as transições FSM até
``FINAL_REPLY_POSTED`` no mesmo atomic com **branch por estado** (o
``await_scheduling_confirmation`` tem source único ``SCHEDULER_REQUESTED`` na
FSM real — origem ``AWAITING_SCHEDULING`` pula o ``await``), persistência dos
5 campos, post da resposta final ao NIR por unidade (unidade 2 = texto EXATO
do plano §4, sem ponto final), negação com motivo obrigatório e os regimes de
erro nomeado (R3) sem escrita parcial (R5). Os cenários espelham os da spec
``scheduling`` ("Confirmação na unidade 1 publica resposta com data",
"Confirmação na unidade 2 publica recusa ao relatório", "Data no passado é
rejeitada", "Negação publica motivo ao NIR", "Negação sem motivo é rejeitada").
"""

from __future__ import annotations

from datetime import datetime, timedelta
from importlib.util import find_spec
from typing import Any, cast

import pytest
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.cases.events import case_status_event_type
from apps.cases.models import Case, CaseStatus, MessageType
from apps.scheduler.services import (
    REPLY_DENY_TEMPLATE,
    REPLY_UNIT_1_TEMPLATE,
    REPLY_UNIT_2_TEXT,
    confirm_case_scheduling,
    deny_case_scheduling,
)

SCHEDULER_ROLE = "scheduler"
SYSTEM_ROLE = "system"

# Formato de exibição das respostas ao NIR (espelho do presenter médico).
_DATETIME_DISPLAY_FORMAT = "%d/%m/%Y %H:%M"

# Caminho (operação, kwargs) de NEW até cada estado usado como source.
_STEPS_TO_STATE: dict[str, list[tuple[str, dict[str, object]]]] = {
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


def _user_messages(case: Case) -> list[object]:
    """Mensagens manuais (user) da thread do caso, em ordem."""
    return list(case.communication_messages.filter(message_type=MessageType.USER))


@pytest.fixture
def scheduler_user() -> User:
    """Agendador ator dos serviços (papel ``scheduler``)."""
    return _make_user("agendador-slice-001", SCHEDULER_ROLE)


# ── R1: campos de agendamento + migration única 0008 ───────────────────────


def test_migration_fields() -> None:
    """R1: shape dos 7 campos de D1 no ``Case`` (null/blank; unit choices 1|2)."""
    scheduled_unit = cast(Any, Case._meta.get_field("scheduled_unit"))
    assert scheduled_unit.__class__.__name__ == "PositiveSmallIntegerField"
    assert dict(scheduled_unit.choices) == {1: "Unidade 1", 2: "Unidade 2"}
    assert scheduled_unit.null is True
    assert scheduled_unit.blank is True

    scheduled_datetime = cast(Any, Case._meta.get_field("scheduled_datetime"))
    assert scheduled_datetime.__class__.__name__ == "DateTimeField"
    assert scheduled_datetime.null is True
    assert scheduled_datetime.blank is True

    scheduled_location = cast(Any, Case._meta.get_field("scheduled_location"))
    assert scheduled_location.__class__.__name__ == "CharField"
    assert scheduled_location.max_length == 200
    assert scheduled_location.blank is True

    scheduled_by = cast(Any, Case._meta.get_field("scheduled_by"))
    assert scheduled_by.remote_field.on_delete.__name__ == "SET_NULL"
    assert scheduled_by.remote_field.related_name == "cases_scheduled"
    assert scheduled_by.null is True
    assert scheduled_by.blank is True

    scheduled_decided_at = cast(Any, Case._meta.get_field("scheduled_decided_at"))
    assert scheduled_decided_at.__class__.__name__ == "DateTimeField"
    assert scheduled_decided_at.null is True
    assert scheduled_decided_at.blank is True

    scheduling_denial_reason = cast(Any, Case._meta.get_field("scheduling_denial_reason"))
    assert scheduling_denial_reason.__class__.__name__ == "TextField"
    assert scheduling_denial_reason.blank is True

    scheduling_reopen_reason = cast(Any, Case._meta.get_field("scheduling_reopen_reason"))
    assert scheduling_reopen_reason.__class__.__name__ == "TextField"
    assert scheduling_reopen_reason.blank is True


def test_migration_0008_case_scheduling_exists() -> None:
    """R1: a migration única ``0008_case_scheduling`` existe (sem drift no gate)."""
    assert find_spec("apps.cases.migrations.0008_case_scheduling") is not None


# ── R2/R3: confirmação unidade 1 a partir de SCHEDULER_REQUESTED ───────────


@pytest.mark.django_db
def test_confirm_unit_1_from_scheduler_requested(scheduler_user: User) -> None:
    """R2/R6: confirmar unidade 1 → FINAL_REPLY_POSTED com 3 eventos na ordem,
    campos persistidos e resposta final com local + data/hora local."""
    case = _case_in_state("SCHEDULER_REQUESTED", created_by=scheduler_user)
    events_before = case.events.count()
    scheduled_at = _future_datetime(days=2)
    location = "Sala de hemodinâmica"

    confirm_case_scheduling(
        case,
        unit=1,
        scheduled_datetime=scheduled_at,
        scheduled_location=location,
        user=scheduler_user,
        role=SCHEDULER_ROLE,
    )

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.events.count() == events_before + 3
    event_types = list(case.events.order_by("id").values_list("event_type", flat=True))
    assert event_types[-3:] == [
        case_status_event_type("AWAITING_SCHEDULING"),
        case_status_event_type("SCHEDULING_CONFIRMED"),
        case_status_event_type("FINAL_REPLY_POSTED"),
    ]
    # Os 5 campos persistidos no mesmo atomic.
    assert case.scheduled_unit == 1
    assert case.scheduled_datetime is not None
    assert case.scheduled_datetime == scheduled_at
    assert case.scheduled_location == location
    assert case.scheduled_by == scheduler_user
    assert case.scheduled_decided_at is not None

    # Resposta final ao NIR: user message com local + data/hora local.
    messages = _user_messages(case)
    assert len(messages) == 1
    message = cast(Any, messages[0])
    assert message.author == scheduler_user
    assert message.author_role == SCHEDULER_ROLE
    expected_body = REPLY_UNIT_1_TEMPLATE.format(
        location=location, scheduled_at=_display_local(scheduled_at)
    )
    assert message.body == expected_body
    assert location in message.body
    assert _display_local(scheduled_at) in message.body


@pytest.mark.django_db
def test_confirm_unit_2_exact_text(scheduler_user: User) -> None:
    """R2/R6: confirmar unidade 2 → resposta final é o texto EXATO do plano §4
    (sem ponto final), com campos persistidos e 3 eventos na ordem."""
    # A constante é o próprio contrato: texto exato do plano §4, sem ponto.
    assert REPLY_UNIT_2_TEXT == (
        "Recusar o relatório — caso agendado na Unidade 2, que comunicará a Secretaria"
    )
    assert not REPLY_UNIT_2_TEXT.endswith(".")

    case = _case_in_state("SCHEDULER_REQUESTED", created_by=scheduler_user)
    events_before = case.events.count()
    scheduled_at = _future_datetime(hours=30)
    location = "Unidade 2 (internet)"

    confirm_case_scheduling(
        case,
        unit=2,
        scheduled_datetime=scheduled_at,
        scheduled_location=location,
        user=scheduler_user,
        role=SCHEDULER_ROLE,
    )

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.events.count() == events_before + 3
    event_types = list(case.events.order_by("id").values_list("event_type", flat=True))
    assert event_types[-3:] == [
        case_status_event_type("AWAITING_SCHEDULING"),
        case_status_event_type("SCHEDULING_CONFIRMED"),
        case_status_event_type("FINAL_REPLY_POSTED"),
    ]
    assert case.scheduled_unit == 2
    assert case.scheduled_datetime == scheduled_at
    assert case.scheduled_location == location
    assert case.scheduled_by == scheduler_user
    assert case.scheduled_decided_at is not None

    messages = _user_messages(case)
    assert len(messages) == 1
    assert cast(Any, messages[0]).body == REPLY_UNIT_2_TEXT


@pytest.mark.django_db
def test_confirm_from_awaiting_scheduling_skips_await(scheduler_user: User) -> None:
    """R2/R6: origem AWAITING_SCHEDULING (reaberto por intercorrência) pula o
    await — apenas 2 eventos CASE_STATUS_* no encadeamento."""
    case = _case_in_state("AWAITING_SCHEDULING", created_by=scheduler_user)
    events_before = case.events.count()

    confirm_case_scheduling(
        case,
        unit=1,
        scheduled_datetime=_future_datetime(days=1),
        scheduled_location="Centro cirúrgico",
        user=scheduler_user,
        role=SCHEDULER_ROLE,
    )

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.events.count() == events_before + 2
    event_types = list(case.events.order_by("id").values_list("event_type", flat=True))
    assert event_types[-2:] == [
        case_status_event_type("SCHEDULING_CONFIRMED"),
        case_status_event_type("FINAL_REPLY_POSTED"),
    ]
    assert case.scheduled_unit == 1
    assert case.scheduled_datetime is not None
    assert case.scheduled_location == "Centro cirúrgico"
    assert case.scheduled_by == scheduler_user
    assert len(_user_messages(case)) == 1


@pytest.mark.django_db
def test_confirm_past_datetime_rejected(scheduler_user: User) -> None:
    """R3/R6: data/hora no passado → erro nomeado antes de qualquer escrita."""
    case = _case_in_state("SCHEDULER_REQUESTED", created_by=scheduler_user)
    events_before = case.events.count()

    with pytest.raises(ValueError, match="passado"):
        confirm_case_scheduling(
            case,
            unit=1,
            scheduled_datetime=_future_datetime(days=-2),
            scheduled_location="Sala 1",
            user=scheduler_user,
            role=SCHEDULER_ROLE,
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.events.count() == events_before
    assert case.scheduled_unit is None
    assert case.scheduled_datetime is None
    assert case.scheduled_location == ""
    assert case.scheduled_by is None
    assert case.scheduled_decided_at is None
    assert _user_messages(case) == []


@pytest.mark.django_db
def test_confirm_naive_datetime_rejected(scheduler_user: User) -> None:
    """R2/R3: data/hora naive (sem fuso) → erro nomeado, sem escrita."""
    case = _case_in_state("SCHEDULER_REQUESTED", created_by=scheduler_user)
    events_before = case.events.count()
    naive_datetime = datetime.now() + timedelta(days=1)

    with pytest.raises(ValueError, match="aware"):
        confirm_case_scheduling(
            case,
            unit=1,
            scheduled_datetime=naive_datetime,
            scheduled_location="Sala 1",
            user=scheduler_user,
            role=SCHEDULER_ROLE,
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.events.count() == events_before
    assert _user_messages(case) == []


@pytest.mark.django_db
@pytest.mark.parametrize("unit", [0, 3])
def test_confirm_invalid_unit_rejected(scheduler_user: User, unit: int) -> None:
    """R2/R3: unidade fora de {1, 2} → erro nomeado, sem escrita."""
    case = _case_in_state("SCHEDULER_REQUESTED", created_by=scheduler_user)
    events_before = case.events.count()

    with pytest.raises(ValueError, match="unidade"):
        confirm_case_scheduling(
            case,
            unit=unit,
            scheduled_datetime=_future_datetime(days=1),
            scheduled_location="Sala 1",
            user=scheduler_user,
            role=SCHEDULER_ROLE,
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.events.count() == events_before
    assert _user_messages(case) == []


# ── R4: negação com motivo obrigatório ─────────────────────────────────────


@pytest.mark.django_db
def test_deny_publishes_reason(scheduler_user: User) -> None:
    """R4/R6: negar com motivo → FINAL_REPLY_POSTED (3 eventos na ordem),
    motivo persistido e resposta final ao NIR contendo o motivo."""
    case = _case_in_state("SCHEDULER_REQUESTED", created_by=scheduler_user)
    events_before = case.events.count()
    reason = "vaga não disponível na data solicitada"

    deny_case_scheduling(case, reason=reason, user=scheduler_user, role=SCHEDULER_ROLE)

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.events.count() == events_before + 3
    event_types = list(case.events.order_by("id").values_list("event_type", flat=True))
    assert event_types[-3:] == [
        case_status_event_type("AWAITING_SCHEDULING"),
        case_status_event_type("SCHEDULING_DENIED"),
        case_status_event_type("FINAL_REPLY_POSTED"),
    ]
    assert case.scheduling_denial_reason == reason

    messages = _user_messages(case)
    assert len(messages) == 1
    message = cast(Any, messages[0])
    assert message.author == scheduler_user
    assert message.author_role == SCHEDULER_ROLE
    assert message.body == REPLY_DENY_TEMPLATE.format(reason=reason)
    assert reason in message.body


@pytest.mark.django_db
def test_deny_from_awaiting_scheduling_skips_await(scheduler_user: User) -> None:
    """R4/R6: negar a partir de AWAITING_SCHEDULING pula o await (2 eventos)."""
    case = _case_in_state("AWAITING_SCHEDULING", created_by=scheduler_user)
    events_before = case.events.count()

    deny_case_scheduling(
        case, reason="insuficiência de documentação", user=scheduler_user, role=SCHEDULER_ROLE
    )

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.events.count() == events_before + 2
    event_types = list(case.events.order_by("id").values_list("event_type", flat=True))
    assert event_types[-2:] == [
        case_status_event_type("SCHEDULING_DENIED"),
        case_status_event_type("FINAL_REPLY_POSTED"),
    ]
    assert case.scheduling_denial_reason == "insuficiência de documentação"
    assert len(_user_messages(case)) == 1


@pytest.mark.django_db
def test_deny_empty_reason_rejected(scheduler_user: User) -> None:
    """R3/R6: negar sem motivo (após strip) → erro nomeado, sem escrita."""
    case = _case_in_state("SCHEDULER_REQUESTED", created_by=scheduler_user)
    events_before = case.events.count()

    with pytest.raises(ValueError, match="motivo"):
        deny_case_scheduling(case, reason="   ", user=scheduler_user, role=SCHEDULER_ROLE)

    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.events.count() == events_before
    assert case.scheduling_denial_reason == ""
    assert _user_messages(case) == []


# ── R5: estado errado / falha no encadeamento → sem escrita parcial ────────


@pytest.mark.django_db
def test_wrong_state_no_partial_write(scheduler_user: User) -> None:
    """R5/R6: confirmar caso fora do par {SCHEDULER_REQUESTED,
    AWAITING_SCHEDULING} → erro nomeado tipado, sem qualquer efeito."""
    case = Case.objects.create(created_by=scheduler_user)
    assert case.status == CaseStatus.NEW

    with pytest.raises(ValueError, match="estado"):
        confirm_case_scheduling(
            case,
            unit=1,
            scheduled_datetime=_future_datetime(days=1),
            scheduled_location="Sala 1",
            user=scheduler_user,
            role=SCHEDULER_ROLE,
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.NEW
    assert case.events.count() == 0
    assert case.scheduled_unit is None
    assert case.scheduled_datetime is None
    assert case.scheduled_by is None
    assert _user_messages(case) == []


@pytest.mark.django_db
def test_mid_chain_failure_rolls_back_everything(scheduler_user: User) -> None:
    """R5: perda de corrida (2ª chamada com estado já final) desfaz TUDO da
    transação — eventos, comunicação e campos do caso voltam ao estado inicial."""
    case = _case_in_state("SCHEDULER_REQUESTED", created_by=scheduler_user)
    events_before = case.events.count()

    with pytest.raises(ValueError, match="estado"):
        with transaction.atomic():
            confirm_case_scheduling(
                case,
                unit=1,
                scheduled_datetime=_future_datetime(days=1),
                scheduled_location="Sala 1",
                user=scheduler_user,
                role=SCHEDULER_ROLE,
            )
            # 2ª confirmação concorrente: o caso já está em FINAL_REPLY_POSTED
            # dentro da mesma transação → erro nomeado, tudo desfeito.
            confirm_case_scheduling(
                case,
                unit=2,
                scheduled_datetime=_future_datetime(days=2),
                scheduled_location="Unidade 2",
                user=scheduler_user,
                role=SCHEDULER_ROLE,
            )

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.SCHEDULER_REQUESTED
    assert fresh.events.count() == events_before
    assert fresh.scheduled_unit is None
    assert fresh.scheduled_datetime is None
    assert fresh.scheduled_by is None
    assert _user_messages(fresh) == []
