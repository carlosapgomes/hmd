"""Serviço de notificações por marcos do caso (change dashboard-notifications-pwa, D1).

``create_milestone_notifications(event)`` traduz um evento-marco da trilha no
conjunto FECHADO de notificações in-app de D1:

- ``CASE_STATUS_FINAL_REPLY_POSTED`` → criador do caso (NIR), título fixo
  "Resposta final disponível" e preview pelo ``payload["source"]`` (mapa fixo
  PT-BR dos 3 desfechos, com fallback para source inesperado/ausente);
- ``CASE_STATUS_SCHEDULER_REQUESTED`` → fan-out para os usuários com papel
  ``scheduler`` (papel do modelo ``Role``, não papel de sessão) com conta
  ativa e não bloqueada;
- ``CASE_STATUS_AWAITING_SCHEDULING`` **com ``"reason" in payload"``** →
  criador do caso. É assim que a reabertura por intercorrência se marca
  (``Case.reopen_scheduling`` carrega ``extra_payload={"reason": ...}``); a
  entrada NORMAL na fila (``await_scheduling_confirmation``) não carrega
  ``reason`` e NÃO notifica.

Qualquer evento fora desse conjunto devolve ``[]`` sem tocar o banco (os
eventos de processamento/fechamento não notificam). O conteúdo é sempre texto
fixo + o caso referenciado — nenhum nome, número de registro ou motivo de
intercorrência entra no título/preview (zero PHI).

Idempotência estrutural: ``get_or_create`` por (``recipient``, ``event``) — o
mesmo par do ``UniqueConstraint`` de ``UserNotification`` —, então evento
reprocessado devolve as mesmas rows. Falha de notificação é suplementar: quem
chama da transição do caso (o signal) protege a chamada com ``try/except``.
"""

from __future__ import annotations

from apps.accounts.models import NotificationType, User, UserNotification
from apps.cases.events import CaseEventType
from apps.cases.models import CaseEvent

# Papel do modelo ``Role`` que recebe o marco de fila de agendamento.
SCHEDULER_ROLE_NAME = "scheduler"

FINAL_REPLY_TITLE = "Resposta final disponível"
# Fallback para ``source`` inesperado/ausente: o desfecho não é classificado,
# mas o NIR sempre sabe que há resposta final disponível.
FINAL_REPLY_PREVIEW_FALLBACK = "Resposta final disponível para o caso"
FINAL_REPLY_PREVIEW_BY_SOURCE: dict[str, str] = {
    "DOCTOR_DENIED": "Negativa médica",
    "SCHEDULING_DENIED": "Negativa de agendamento",
    "SCHEDULING_CONFIRMED": "Agendamento confirmado",
}

SCHEDULER_REQUESTED_TITLE = "Caso pronto para agendamento"
SCHEDULER_REQUESTED_PREVIEW = "Caso aguardando confirmação de agendamento"

SCHEDULING_REOPENED_TITLE = "Caso reaberto por intercorrência"
# O motivo da intercorrência é interno do fluxo — nunca entra no preview.
SCHEDULING_REOPENED_PREVIEW = "Reconfirme os dados do caso"


def create_milestone_notifications(event: CaseEvent) -> list[UserNotification]:
    """Cria as notificações do evento-marco (idempotente); ``[]`` se fora do conjunto."""
    if event.event_type == CaseEventType.CASE_STATUS_FINAL_REPLY_POSTED:
        return _create_final_reply_notification(event)
    if event.event_type == CaseEventType.CASE_STATUS_SCHEDULER_REQUESTED:
        return _create_scheduler_notifications(event)
    if (
        event.event_type == CaseEventType.CASE_STATUS_AWAITING_SCHEDULING
        and "reason" in event.payload
    ):
        return _create_scheduling_reopened_notification(event)
    return []


def _create_final_reply_notification(event: CaseEvent) -> list[UserNotification]:
    """Resposta final publicada → criador, com preview do desfecho (D1)."""
    preview = FINAL_REPLY_PREVIEW_BY_SOURCE.get(
        event.payload.get("source", ""), FINAL_REPLY_PREVIEW_FALLBACK
    )
    return [
        _create_notification(
            event=event,
            recipient=event.case.created_by,
            notification_type=NotificationType.FINAL_REPLY_POSTED,
            title=FINAL_REPLY_TITLE,
            body_preview=preview,
        )
    ]


def _create_scheduler_notifications(event: CaseEvent) -> list[UserNotification]:
    """Caso pronto para agendamento → fan-out dos schedulers com conta ativa."""
    recipients = User.objects.filter(
        roles__name=SCHEDULER_ROLE_NAME,
        is_active=True,
        account_status="active",
    ).distinct()
    return [
        _create_notification(
            event=event,
            recipient=recipient,
            notification_type=NotificationType.SCHEDULER_REQUESTED,
            title=SCHEDULER_REQUESTED_TITLE,
            body_preview=SCHEDULER_REQUESTED_PREVIEW,
        )
        for recipient in recipients
    ]


def _create_scheduling_reopened_notification(event: CaseEvent) -> list[UserNotification]:
    """Reabertura por intercorrência → criador, com preview fixo (D1)."""
    return [
        _create_notification(
            event=event,
            recipient=event.case.created_by,
            notification_type=NotificationType.SCHEDULING_REOPENED,
            title=SCHEDULING_REOPENED_TITLE,
            body_preview=SCHEDULING_REOPENED_PREVIEW,
        )
    ]


def _create_notification(
    *,
    event: CaseEvent,
    recipient: User,
    notification_type: NotificationType,
    title: str,
    body_preview: str,
) -> UserNotification:
    """Persiste (recipient, event) sem duplicar — chave de idempotência."""
    notification, _created = UserNotification.objects.get_or_create(
        recipient=recipient,
        event=event,
        defaults={
            "case_id": event.case_id,
            "notification_type": notification_type,
            "title": title,
            "body_preview": body_preview,
        },
    )
    return notification
