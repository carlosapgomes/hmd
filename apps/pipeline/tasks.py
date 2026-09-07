"""django-q2 tasks do pipeline LLM (slice 006, R3–R5, design D9).

Entry point ``process_case_pipeline`` roda no cluster ``llm`` (ou inline quando
``LLM_RUN_TASKS_INLINE`` é True — dev/teste) e é **idempotente por estado**
(``LLM_EXTRACTING`` = pipeline completo; ``LLM_SUMMARIZING`` pós-bypass =
retomada; demais → no-op com log). A operação roda sob lock do caso
(``context="worker_llm"``/``role="system"``) — a implementação vive no
orquestrador (``apps/pipeline/orchestrator.py``), que é o único dono do
``fail_processing``. Este módulo expõe apenas o cluster e o ``enqueue``
consumido pelos signals (R5): enfileira no cluster ``llm`` quando fora de
inline; senão executa a task sincronamente (testes determinísticos e cadeia
inline de dev).
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django_q.tasks import async_task

logger = logging.getLogger(__name__)

# Cluster alternativo do django-q2 dedicado ao pipeline LLM (R4/D9): separado
# dos demais para as chamadas LLM não competirem com a extração/anonimização.
LLM_CLUSTER = "llm"


def enqueue_case_pipeline(case_id: uuid.UUID) -> None:
    """Enfileira o pipeline LLM do caso no cluster llm (ou inline, dev/teste).

    Com ``LLM_RUN_TASKS_INLINE=True`` executa a task sincronamente; senão
    enfileira via django-q2 com ``q_options={"cluster": "llm"}`` (R4). Chamado
    pelos receivers de signal via ``transaction.on_commit`` (rollback não
    enfileira) — o módulo de signals faz a importação tardia para evitar
    import circular em runtime.
    """
    if getattr(settings, "LLM_RUN_TASKS_INLINE", True):
        from apps.pipeline.orchestrator import process_case_pipeline

        process_case_pipeline(case_id)
        return
    async_task(
        "apps.pipeline.orchestrator.process_case_pipeline",
        case_id,
        q_options={
            "cluster": LLM_CLUSTER,
            "task_name": f"llm:{case_id}",
        },
    )
