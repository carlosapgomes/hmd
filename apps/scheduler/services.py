"""Serviços transacionais de agendamento (change scheduler-multi-unit, D2).

O dono dos serviços de agendamento é o app ``apps.scheduler`` (decisão do
design D2 — o núcleo ``apps/cases`` fica com FSM/locks/eventos; espelha o
change 07, onde a UI do papel vive no próprio app). Cada operação valida o
caso sob ``select_for_update`` e, no MESMO ``transaction.atomic()``, encadeia
as transições FSM até ``FINAL_REPLY_POSTED``, persiste os campos de
agendamento e posta a resposta final ao NIR na thread de comunicações
(``post_user_communication`` — user message autoral do agendador, D2, nunca
notice system). Validações levantam ``ValueError`` nomeado antes de qualquer
escrita; ``TransitionNotAllowed`` inesperado do encadeamento propaga tipado
para a view tratar (slice 003) — o atomic desfaz qualquer escrita parcial.

Branch por estado no encadeamento (review do plano P1-1): a transição
``await_scheduling_confirmation`` tem source único ``SCHEDULER_REQUESTED`` na
FSM real do change 03 — em casos já em ``AWAITING_SCHEDULING`` (reabertos por
intercorrência, slice 002) o ``await`` é PULADO. Textos de resposta são
constantes do módulo (dados, não strings espalhadas): unidade 2 usa o texto
EXATO do plano §4, sem ponto final.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.cases.communications import post_user_communication
from apps.cases.models import Case, CaseStatus, SchedulingUnit

if TYPE_CHECKING:
    from apps.accounts.models import User

# Formato de exibição da data/hora local nas respostas ao NIR (mesmo formato
# do presenter médico do change 07: ``%d/%m/%Y %H:%M``).
_SCHEDULED_DATETIME_FORMAT = "%d/%m/%Y %H:%M"

# Respostas finais ao NIR (D2, constantes do módulo): unidade 1 interpola
# local + data/hora local formatada; unidade 2 é o texto EXATO do plano §4
# (sem ponto final); negação interpola o motivo obrigatório.
REPLY_UNIT_1_TEMPLATE = (
    "Agendamento confirmado — {location}, {scheduled_at}. Comparecer com documentos e exames."
)
REPLY_UNIT_2_TEXT = "Recusar o relatório — caso agendado na Unidade 2, que comunicará a Secretaria"
REPLY_DENY_TEMPLATE = "Agendamento negado — {reason}."

# Campos persistidos na decisão do agendador (D1), salvos no mesmo atomic das
# transições FSM.
_SCHEDULING_FIELDS = (
    "scheduled_unit",
    "scheduled_datetime",
    "scheduled_location",
    "scheduled_by",
    "scheduled_decided_at",
    "scheduling_denial_reason",
)

_SCHEDULING_READY_STATES = frozenset(
    {CaseStatus.SCHEDULER_REQUESTED, CaseStatus.AWAITING_SCHEDULING}
)
_VALID_UNITS = frozenset({SchedulingUnit.UNIT_1, SchedulingUnit.UNIT_2})


def _validate_scheduling_ready_state(case: Case) -> None:
    """Estado pronto para a decisão do agendador (R2): erro nomeado caso contrário."""
    if case.status not in _SCHEDULING_READY_STATES:
        raise ValueError(
            f"agendamento indisponível no estado {case.status!r} — "
            "esperado SCHEDULER_REQUESTED ou AWAITING_SCHEDULING"
        )


def _validate_scheduled_unit(unit: int) -> None:
    """Unidade de destino ∈ {1, 2} (R2/plano §4): erro nomeado caso contrário."""
    if unit not in _VALID_UNITS:
        raise ValueError(f"unidade de agendamento inválida: {unit!r} — esperado 1 ou 2")


def _validate_scheduled_datetime(scheduled_datetime: datetime) -> None:
    """Data/hora aware e não no passado (R2): erro nomeado caso contrário."""
    if not timezone.is_aware(scheduled_datetime):
        raise ValueError("data/hora do agendamento deve ser aware (com fuso horário)")
    if scheduled_datetime < timezone.now():
        raise ValueError(
            "data/hora do agendamento no passado: "
            f"{timezone.localtime(scheduled_datetime).strftime(_SCHEDULED_DATETIME_FORMAT)}"
        )


def _format_scheduled_datetime(scheduled_datetime: datetime) -> str:
    """Data/hora da resposta ao NIR no fuso local da aplicação."""
    return timezone.localtime(scheduled_datetime).strftime(_SCHEDULED_DATETIME_FORMAT)


def _enter_scheduling_queue(case: Case, *, user: User, role: str) -> None:
    """Branch por estado (R2/P1-1): dispara o ``await`` apenas da origem
    ``SCHEDULER_REQUESTED`` — em ``AWAITING_SCHEDULING`` (reaberto por
    intercorrência, slice 002) o caso já passou pela entrada de fila e o
    ``await`` levantaria ``TransitionNotAllowed`` (source único na FSM).
    """
    if case.status == CaseStatus.SCHEDULER_REQUESTED:
        case.await_scheduling_confirmation(user=user, role=role)


def confirm_case_scheduling(
    case: Case,
    *,
    unit: int,
    scheduled_datetime: datetime,
    scheduled_location: str,
    user: User,
    role: str,
) -> None:
    """Confirma o agendamento do caso (R2) até ``FINAL_REPLY_POSTED``.

    Valida estado ∈ {``SCHEDULER_REQUESTED``, ``AWAITING_SCHEDULING``},
    ``unit ∈ {1, 2}`` e data/hora aware não no passado — erros nomeados antes
    de qualquer escrita. No MESMO atomic: encadeia ``await`` (só da origem
    ``SCHEDULER_REQUESTED``) → ``confirm_scheduling`` → ``post_final_reply``
    (3 ou 2 eventos ``CASE_STATUS_*``), persiste os 5 campos
    (``scheduled_unit``/``scheduled_datetime``/``scheduled_location``/
    ``scheduled_by=user``/``scheduled_decided_at=now``) e posta a resposta
    final ao NIR por unidade (unidade 1 interpola local + data/hora local;
    unidade 2 é o texto exato do plano §4, sem ponto final).
    """
    location = scheduled_location.strip()
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        _validate_scheduling_ready_state(locked)
        _validate_scheduled_unit(unit)
        _validate_scheduled_datetime(scheduled_datetime)

        _enter_scheduling_queue(locked, user=user, role=role)
        locked.confirm_scheduling(user=user, role=role)
        locked.post_final_reply(user=user, role=role)

        locked.scheduled_unit = unit
        locked.scheduled_datetime = scheduled_datetime
        locked.scheduled_location = location
        locked.scheduled_by = user
        locked.scheduled_decided_at = timezone.now()
        locked.save(update_fields=_SCHEDULING_FIELDS)

        if unit == SchedulingUnit.UNIT_1:
            body = REPLY_UNIT_1_TEMPLATE.format(
                location=location, scheduled_at=_format_scheduled_datetime(scheduled_datetime)
            )
        else:
            body = REPLY_UNIT_2_TEXT
        post_user_communication(locked, user=user, role=role, body=body)


def deny_case_scheduling(
    case: Case,
    *,
    reason: str,
    user: User,
    role: str,
) -> None:
    """Nega o agendamento do caso (R4) até ``FINAL_REPLY_POSTED``.

    Motivo obrigatório (não vazio após ``strip``) e mesmo branch por estado do
    confirmar (em ``AWAITING_SCHEDULING`` pula o ``await``). No MESMO atomic:
    encadeia ``await`` (só da origem ``SCHEDULER_REQUESTED``) →
    ``deny_scheduling`` → ``post_final_reply``, persiste
    ``scheduling_denial_reason`` e posta a resposta final ao NIR com o motivo.
    """
    stripped_reason = reason.strip()
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        _validate_scheduling_ready_state(locked)
        if not stripped_reason:
            raise ValueError("informe o motivo da negação do agendamento")

        _enter_scheduling_queue(locked, user=user, role=role)
        locked.deny_scheduling(user=user, role=role)
        locked.post_final_reply(user=user, role=role)

        locked.scheduling_denial_reason = stripped_reason
        locked.save(update_fields=_SCHEDULING_FIELDS)

        body = REPLY_DENY_TEMPLATE.format(reason=stripped_reason)
        post_user_communication(locked, user=user, role=role, body=body)
