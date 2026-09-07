"""Signals do app de anonimização (slice 004, design D7, R3).

Único receiver registrado: o trigger desacoplado da task de anonimização —
``CaseEvent`` é a fonte de verdade da trilha (D5) e o evento de transição para
``ANONYMIZING`` é o gatilho. A guarda anti-recursão usa ``event_type ==
CASE_STATUS_ANONYMIZING`` E ``payload["source"] == "PDF_EXTRACTING"`` (entrada
REAL no estado via ``complete_pdf_extraction``): a self-transition
``start_anonymization`` da própria task emite ``source=ANONYMIZING`` e NÃO
re-dispara — sem o filtro, o modo inline recursaria com o lock na mão e o
async duplicaria tasks. O enqueue é feito via ``transaction.on_commit`` (nunca
dentro da transação que ainda vai commitar — rollback não enfileira). Cobre os
3 paths de entrada em ANONYMIZING do change 04 (extração concluída, gate
liberado, reenvio reprocessado) sem tocar no intake.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.cases.events import CaseEventType
from apps.cases.models import CaseEvent, CaseStatus


@receiver(post_save, sender=CaseEvent)
def enqueue_case_anonymization_on_entry(
    sender: type[CaseEvent],
    instance: CaseEvent,
    created: bool,
    **kwargs: object,
) -> None:
    """Enfileira a anonimização na entrada REAL do caso em ANONYMIZING (R3).

    Filtra updates (``created=False`` — re-save não re-enfileira) e o evento
    de transição para ANONYMIZING apenas quando o ``source`` é
    ``PDF_EXTRACTING`` (a self-transition da task tem ``source=ANONYMIZING`` e
    não dispara). O enqueue roda no commit da transação que persistiu o evento
    (``transaction.on_commit``): rollback não enfileira.

    Importação tardia de ``enqueue_case_anonymization`` evita import circular
    em runtime (tasks importa models/serviços; este módulo é importado pelo
    ``AppsConfig.ready``).
    """

    if not created:
        return
    if instance.event_type != CaseEventType.CASE_STATUS_ANONYMIZING:
        return
    if instance.payload.get("source") != CaseStatus.PDF_EXTRACTING:
        return
    from apps.anonymization.tasks import enqueue_case_anonymization

    case_id = instance.case_id
    transaction.on_commit(lambda: enqueue_case_anonymization(case_id))
