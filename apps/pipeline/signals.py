"""Signals do app de pipeline (slice 006, R5, design D9).

Dois receivers em ``CaseEvent.post_save`` — a trilha é a fonte de verdade
(D5) e o evento de transição é o gatilho desacoplado das tasks do pipeline:

- **R5a — entrada real em ``LLM_EXTRACTING``**: ``event_type ==
  CASE_STATUS_LLM_EXTRACTING`` E ``payload["source"] == "ANONYMIZING"`` →
  enqueue (a transição de entrada é a ``complete_anonymization`` do change 05;
  a self-transition ``start_llm_extraction`` da própria task tem
  ``source=LLM_EXTRACTING`` e NÃO re-dispara — sem o filtro, o modo inline
  recursaria com o lock na mão e o async duplicaria tasks).
- **R5b — retomada pós-bypass**: ``event_type == CASE_STATUS_LLM_SUMMARIZING``
  E ``payload["source"] == "LLM_EXTRACTING"`` E ``actor_type == "user"`` →
  enqueue de retomada (bypass do NIR via ``bypass_pipeline_divergence``; o
  avanço natural do orquestrador — ``complete_llm_extraction`` com ator
  system — NÃO re-enfileira: a task segue em execução própria).

Ambos via ``transaction.on_commit`` (nunca dentro da transação que ainda vai
commitar — rollback não enfileira). Conflito de lock do claim em async é no-op
idempotente (política do orquestrador); a coordenação transição+release no
mesmo atomic (task de anonimização/desvio autorizado e orquestrador) garante
que os ``on_commit`` disparam com a lease do worker anterior já liberada.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, CaseEvent, CaseStatus


@receiver(post_save, sender=CaseEvent)
def enqueue_case_pipeline_on_entry(
    sender: type[CaseEvent],
    instance: CaseEvent,
    created: bool,
    **kwargs: object,
) -> None:
    """Enfileira o pipeline na entrada REAL do caso em LLM_EXTRACTING (R5a).

    Filtra updates (``created=False`` — re-save não re-enfileira) e o evento de
    transição para LLM_EXTRACTING apenas quando o ``source`` é ``ANONYMIZING``
    (a self-transition da task tem ``source=LLM_EXTRACTING`` e não dispara). O
    enqueue roda no commit da transação que persistiu o evento
    (``transaction.on_commit``): rollback não enfileira.

    Importação tardia de ``enqueue_case_pipeline`` evita import circular em
    runtime (tasks importa o orquestrador; este módulo é importado pelo
    ``AppsConfig.ready``).
    """
    del sender, kwargs
    if not created:
        return
    if instance.event_type != CaseEventType.CASE_STATUS_LLM_EXTRACTING:
        return
    if instance.payload.get("source") != CaseStatus.ANONYMIZING:
        return
    from apps.pipeline.tasks import enqueue_case_pipeline

    case_id = instance.case_id
    transaction.on_commit(lambda: enqueue_case_pipeline(case_id))


@receiver(post_save, sender=CaseEvent)
def enqueue_case_pipeline_resume_after_bypass(
    sender: type[CaseEvent],
    instance: CaseEvent,
    created: bool,
    **kwargs: object,
) -> None:
    """Enfileira a retomada do pipeline na liberação de divergência (R5b).

    Contrato final (correção do review): ``CASE_STATUS_LLM_SUMMARIZING`` com
    ``source == "LLM_EXTRACTING"`` E ``actor_type == "user"`` — o bypass do NIR
    (``bypass_pipeline_divergence``). O avanço natural do orquestrador
    (``complete_llm_extraction``, ator system) NÃO re-enfileira — a task segue
    em execução própria.
    """
    del sender, kwargs
    if not created:
        return
    if instance.event_type != CaseEventType.CASE_STATUS_LLM_SUMMARIZING:
        return
    if instance.payload.get("source") != CaseStatus.LLM_EXTRACTING:
        return
    if instance.actor_type != ActorType.USER:
        return
    from apps.pipeline.tasks import enqueue_case_pipeline

    case_id = instance.case_id
    transaction.on_commit(lambda: enqueue_case_pipeline(case_id))
