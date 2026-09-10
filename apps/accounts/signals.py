"""Signals do app de accounts (change dashboard-notifications-pwa, slice 001).

Um receiver em ``CaseEvent.post_save``: cada evento-marco da trilha delega o
conjunto FECHADO de notificações de D1 ao serviço
``create_milestone_notifications`` (filtro ``created`` evita reprocessar
updates). Notificação é suplementar: qualquer falha é logada e engolida — a
transição do caso NUNCA falha por causa dela (R4).

Sem ``transaction.on_commit``: a row de notificação nasce na mesma transação do
evento (se o caso sofrer rollback, a notificação some junto — desejável e mais
simples, D1).
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

import apps.accounts.notifications as notifications
from apps.cases.models import CaseEvent

logger = logging.getLogger(__name__)


@receiver(post_save, sender=CaseEvent)
def create_milestone_notifications_on_event(
    sender: type[CaseEvent],
    instance: CaseEvent,
    created: bool,
    **kwargs: object,
) -> None:
    """Cria as notificações dos marcos do caso sem bloquear a transição (R4)."""
    del sender, kwargs
    if not created:
        return
    try:
        # Savepoint (P2 review): um DatabaseError na notificação fica restrito
        # a este bloco — sem ele, ``mark_for_rollback_on_error`` marcaria a
        # transação do evento e a transição do caso seria revertida em
        # silêncio. O rollback do caso continua levando a notificação junto
        # quando é O CASO que falha (decisão D1).
        with transaction.atomic():
            notifications.create_milestone_notifications(instance)
    except Exception:
        logger.exception(
            "Falha ao criar notificações do evento %s do caso %s",
            instance.pk,
            instance.case_id,
        )
