"""Signals do app de attachments (change attachment-processing-ocr, slice 002).

Um receiver em ``CaseEvent.post_save``: o evento não-transicional
``CASE_ANONYMIZATION_COMPLETED`` (a anonimização do relatório principal
terminou — o mapa do caso está pronto e o processamento dos anexos pode rodar
em paralelo ao pipeline LLM, design D3) dispara o enqueue do worker
``process_case_attachments`` via ``transaction.on_commit`` (rollback não
enfileira). O filtro anti-recursão ``payload["source"]`` NÃO se aplica aqui:
este evento é gravado apenas pelo serviço de anonimização — o worker de anexos
emite ``CASE_ATTACHMENT_*``, nunca re-dispara este receiver.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.cases.events import CaseEventType
from apps.cases.models import CaseEvent


@receiver(post_save, sender=CaseEvent)
def enqueue_attachments_after_anonymization(
    sender: type[CaseEvent],
    instance: CaseEvent,
    created: bool,
    **kwargs: object,
) -> None:
    """Enfileira o processamento dos anexos após a anonimização do caso (R4).

    Filtra updates (``created=False`` — re-save não re-enfileira) e o evento
    canônico da anonimização concluída. O enqueue roda no commit da transação
    que persistiu o evento (``transaction.on_commit``): rollback não enfileira.
    Caso sem anexos → a task retorna imediato.

    Importação tardia de ``enqueue_case_attachments`` evita import circular em
    runtime (tasks importa models/serviços; este módulo é importado pelo
    ``AppsConfig.ready``).
    """
    del sender, kwargs
    if not created:
        return
    if instance.event_type != CaseEventType.CASE_ANONYMIZATION_COMPLETED:
        return
    from apps.attachments.tasks import enqueue_case_attachments

    case_id = instance.case_id
    transaction.on_commit(lambda: enqueue_case_attachments(case_id))
