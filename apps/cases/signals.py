"""Signals do app de casos (change 03, slice 005).

Único receiver registrado: a projeção de comunicações — ``CaseEvent`` é a
fonte de verdade da trilha (D5) e cada evento persistido dispara, via
``post_save``, a criação da mensagem ``system`` quando o tipo está no conjunto
inicial projetado (D8). Divergência vs ats-web: não há receiver de
``Case.post_save`` (o HMD grava os eventos diretamente dentro da operação
transacionada — gravação direta, D5); o receiver de ``CaseEvent.post_save``
existe apenas para a projeção de comunicações.
"""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.cases.models import CaseEvent


@receiver(post_save, sender=CaseEvent)
def create_case_event_system_notice(
    sender: type[CaseEvent],
    instance: CaseEvent,
    created: bool,
    **kwargs: object,
) -> None:
    """Projeta um ``CaseEvent`` suportado em mensagem sistêmica na thread (R3).

    Ignora updates (``created=False``) e eventos fora do conjunto inicial; a
    idempotência por ``source_event`` O2O cobre a repetição da chamada (R4).
    Mensagens system não geram notificação (R5).
    """
    if not created:
        return

    # Importação tardia para evitar import circular em runtime.
    from apps.cases.communications import create_system_communication_notice_for_event

    create_system_communication_notice_for_event(instance)
