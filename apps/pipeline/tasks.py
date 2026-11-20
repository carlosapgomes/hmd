"""django-q2 tasks do pipeline LLM (slice 006, R3–R5, design D9).

Entry point ``process_case_pipeline`` roda no cluster ``llm`` (ou inline quando
``LLM_RUN_TASKS_INLINE`` é True — dev/teste) e é **idempotente por estado**
(``LLM_EXTRACTING`` = pipeline completo; ``LLM_SUMMARIZING`` pós-bypass =
retomada; demais → no-op com log). A operação roda sob lock do caso
(``context="worker_llm"``/``role="system"``) — a implementação vive no
orquestrador (``apps/pipeline/orchestrator.py``), que é o único dono do
``fail_processing``. Este módulo expõe o cluster e o ``enqueue`` consumido
pelos signals (R5): enfileira no cluster ``llm`` quando fora de inline; senão
executa a task sincronamente (testes determinísticos e cadeia inline de dev).

Recuperação de enqueue pós-confirmação (confirm-nir 003, D7):
``enqueue_procedure_review_reprocessing`` é o DONO ÚNICO do dispatch do
reprocessamento — chamado só pelo callback ``on_commit(robust=False)`` do
signal do pedido FSM. Falha de dispatch NÃO desfaz a confirmação commitada:
agenda recovery ONCE (cluster ``llm``, 60 s, alvo
``apps.pipeline.orchestrator.process_case_pipeline``) em transação própria com
a auditoria ``CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED`` e propaga
``ProcedureReviewEnqueueAfterCommitError`` à view, que traduz em 302-aviso
(recovery programado) ou 503-suporte. Payloads/logs técnicos carregam só
código + outcome (nunca exceção livre, justificativa ou dado clínico).
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django_q.tasks import async_task

from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, CaseEvent

logger = logging.getLogger(__name__)

# Cluster alternativo do django-q2 dedicado ao pipeline LLM (R4/D9): separado
# dos demais para as chamadas LLM não competirem com a extração/anonimização.
LLM_CLUSTER = "llm"

# Recovery do reprocessamento pós-confirmação (D7): alvo executável pelo
# scheduler, atraso fixo e cluster explícito (nunca o default não implantado).
RECOVERY_FUNC = "apps.pipeline.orchestrator.process_case_pipeline"
RECOVERY_CLUSTER = LLM_CLUSTER
RECOVERY_DELAY_SECONDS = 60

# Códigos técnicos do outcome de enqueue (payload terse do ENQUEUE_FAILED —
# sem mensagem livre, justificativa ou dado clínico).
REASON_ENQUEUE_FAILED = "enqueue_failed"
REASON_SCHEDULE_FAILED = "schedule_failed"
REASON_RECOVERY_FAILED = "recovery_failed"


class ProcedureReviewEnqueueAfterCommitError(Exception):
    """Dispatch pós-confirmação falhou; a confirmação permanece commitada.

    Carregada pelo callback ``on_commit`` do signal do pedido FSM e capturada
    SOMENTE pela view de confirmação: ``recovery_scheduled=True`` vira
    redirect 302 com aviso; ``False`` vira detalhe 503 com orientação de
    suporte. A mensagem técnica nunca carrega PHI — só ids e código.
    """

    def __init__(
        self,
        *,
        case_id: uuid.UUID,
        confirmation_event_id: int,
        recovery_scheduled: bool,
        reason_code: str,
    ) -> None:
        self.case_id = case_id
        self.confirmation_event_id = confirmation_event_id
        self.recovery_scheduled = recovery_scheduled
        self.reason_code = reason_code
        super().__init__(
            "enqueue pós-confirmação falhou "
            f"(caso {case_id}, confirmação {confirmation_event_id}): "
            f"{reason_code}; recovery_scheduled={recovery_scheduled}"
        )


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


def recovery_schedule_name(case_id: uuid.UUID, confirmation_event_id: int) -> str:
    """Nome do recovery por caso+confirmação (deduplicação sem residual).

    O scheduler do django-q2 DELETA o Schedule ONCE após o dispatch — nome
    determinístico não deixa residual que bloquearia um recovery futuro do
    mesmo caso; ``q_schedule`` recusa nome duplicado pendente (deduplicação).
    """
    return f"procedure-review-recovery:{case_id}:{confirmation_event_id}"


def schedule_procedure_review_recovery(*, case_id: uuid.UUID, confirmation_event_id: int) -> bool:
    """Agenda o recovery ONCE do reprocessamento (idempotente, D7).

    Destino explícito ao cluster ``llm`` (o único que executa o pipeline —
    schedules com cluster NULL só seriam consumidos pelo default, não
    implantado), alvo ``process_case_pipeline`` com UUID serializado em str +
    ``confirmation_event_id`` esperado, 60 s de atraso. Recovery pendente por
    caso+confirmação não duplica (nome existente → True sem reagendar).

    Returns:
        True quando há recovery pendente (criado agora ou já existente).
    Raises:
        Exception: falha de persistência do Schedule (o chamador audita e
            traduz em ``ProcedureReviewEnqueueAfterCommitError``).
    """
    from django_q.models import Schedule
    from django_q.tasks import schedule as q_schedule

    name = recovery_schedule_name(case_id, confirmation_event_id)
    if Schedule.objects.filter(name=name, func=RECOVERY_FUNC).exists():
        logger.info(
            "procedure_review_recovery_deduped caso=%s confirmacao=%s",
            case_id,
            confirmation_event_id,
        )
        return True
    try:
        q_schedule(
            RECOVERY_FUNC,
            str(case_id),
            int(confirmation_event_id),
            schedule_type=Schedule.ONCE,
            next_run=timezone.now() + timedelta(seconds=RECOVERY_DELAY_SECONDS),
            name=name,
            cluster=RECOVERY_CLUSTER,
        )
    except IntegrityError:
        # Corrida com outro worker/callback: recovery já pendente — deduplica.
        logger.info(
            "procedure_review_recovery_raced caso=%s confirmacao=%s",
            case_id,
            confirmation_event_id,
        )
        return True
    return True


def _record_enqueue_failed(
    *,
    case_id: uuid.UUID,
    confirmation_event_id: int,
    recovery_scheduled: bool,
    reason_code: str,
) -> None:
    """Auditoria terse da falha de enqueue (só código + outcome)."""
    CaseEvent.objects.create(
        case_id=case_id,
        event_type=CaseEventType.CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={
            "confirmation_event_id": confirmation_event_id,
            "recovery_scheduled": recovery_scheduled,
            "reason_code": reason_code,
        },
    )


def enqueue_procedure_review_reprocessing(
    *, case_id: uuid.UUID, confirmation_event_id: int
) -> None:
    """Dispatch seguro do reprocessamento pós-confirmação (D7, dono único).

    Sucesso → retorna sem efeito colateral (o pipeline roda inline ou via
    broker). Falha de dispatch → tenta o recovery ONCE em savepoint e commita
    a auditoria ``…ENQUEUE_FAILED`` em transação PRÓPRIA (posterior ao commit
    durável da confirmação), ANTES de propagar a exceção tipada à view. Se a
    transação de recovery/auditoria inteira falhar, propaga com
    ``recovery_scheduled=False`` e log técnico sanitizado — sem reverter a
    confirmação, sem alegar auditoria persistida e sem anunciar Schedule.

    Raises:
        ProcedureReviewEnqueueAfterCommitError: sempre que o dispatch falha,
            com o outcome do recovery para a view traduzir.
    """
    try:
        enqueue_case_pipeline(case_id)
        return
    except Exception:
        logger.exception(
            "procedure_review_enqueue_failed caso=%s confirmacao=%s — tentando recovery programado",
            case_id,
            confirmation_event_id,
        )
    try:
        with transaction.atomic():
            try:
                with transaction.atomic():
                    scheduled = schedule_procedure_review_recovery(
                        case_id=case_id,
                        confirmation_event_id=confirmation_event_id,
                    )
            except Exception:
                logger.exception(
                    "procedure_review_schedule_failed caso=%s confirmacao=%s",
                    case_id,
                    confirmation_event_id,
                )
                scheduled = False
            _record_enqueue_failed(
                case_id=case_id,
                confirmation_event_id=confirmation_event_id,
                recovery_scheduled=scheduled,
                reason_code=REASON_ENQUEUE_FAILED if scheduled else REASON_SCHEDULE_FAILED,
            )
    except Exception:
        logger.exception(
            "procedure_review_recovery_failed caso=%s confirmacao=%s"
            " — recovery e auditoria nao persistidos",
            case_id,
            confirmation_event_id,
        )
        raise ProcedureReviewEnqueueAfterCommitError(
            case_id=case_id,
            confirmation_event_id=confirmation_event_id,
            recovery_scheduled=False,
            reason_code=REASON_RECOVERY_FAILED,
        ) from None
    raise ProcedureReviewEnqueueAfterCommitError(
        case_id=case_id,
        confirmation_event_id=confirmation_event_id,
        recovery_scheduled=scheduled,
        reason_code=REASON_ENQUEUE_FAILED if scheduled else REASON_SCHEDULE_FAILED,
    ) from None
