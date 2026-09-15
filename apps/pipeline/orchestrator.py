"""Orquestrador do pipeline LLM (slice 006, R3, design D9).

``process_case_pipeline(case_id)`` é o entry point do worker/cluster ``llm``
(ou inline quando ``LLM_RUN_TASKS_INLINE`` é True — dev/teste), **idempotente
por estado** e o **único dono do ``fail_processing``** (serviços levantam
``LlmPipelineError``/``LlmError`` e nunca transicionam — D4/D8). Branch:

- ``LLM_EXTRACTING`` = pipeline completo sob lock ``worker_llm``/``system``:
  ``start_llm_extraction`` (self) → LLM1 → reconciliação; **divergência** →
  retido em ``LLM_EXTRACTING`` (flags/eventos gravados pelo llm1_service) —
  release e sai, sem LLM2; ok → ``complete_llm_extraction`` (→ LLM_SUMMARIZING)
  e continua na sumarização;
- ``LLM_SUMMARIZING`` = retomada pós-bypass: pula LLM1/reconciliação
  (reaproveita o artefato persistido) e roda policy/prior-case/LLM2/complete;
- demais estados → no-op com log (reexecução do q2 nunca duplica efeitos).

A sumarização roda **num único lock**: policy (``evaluate_case_policies``),
prior-case (``record_prior_case_lookups``), LLM2 (``run_llm2_summarization``)
e a transição de saída ``complete_llm_summarization`` (→ AWAITING_DOCTOR). A
coordenação **transição de saída + release no MESMO atomic** (padrão fixado no
change 05/finding P1) vale quando o evento da transição é consumido por um
signal: o release da lease roda no mesmo ``transaction.atomic()`` da
``complete_llm_summarization`` — o commit dispara os hooks ``on_commit`` (fila
médica do 07) já com a lease liberada. Exceção em qualquer etapa → refresh +
``fail_processing(tipo)`` → ``FAILED`` (fail-closed; artefatos parciais
permanecem mas o caso não avança). Conflito de claim de lock: inline →
``CaseLockConflictError`` propagada (anomalia real — a coordenação do 006
evita o conflito na cadeia); async → no-op com log (outro worker cuidando).
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django.db import transaction

from apps.cases.locks import CaseLock, CaseLockConflictError, claim_case_lock, release_case_lock
from apps.cases.models import Case, CaseStatus
from apps.pipeline.llm import LlmPipelineError
from apps.pipeline.llm1_service import run_llm1_extraction
from apps.pipeline.llm2_service import run_llm2_summarization
from apps.pipeline.policy import evaluate_case_policies
from apps.pipeline.prior_case import record_prior_case_lookups

logger = logging.getLogger(__name__)

# Cluster alternativo do django-q2 dedicado ao pipeline LLM (R4/D9).
LLM_CLUSTER = "llm"
# Contexto/papel do worker na concessão do lock de exclusividade do caso (D9).
WORKER_LOCK_CONTEXT = "worker_llm"
SYSTEM_ROLE = "system"


def _claim_worker_lock(case: Case, *, case_id: uuid.UUID) -> CaseLock | None:
    """Claim do lock do worker com a política de conflito do padrão change 05.

    A coordenação transição+release no mesmo atomic (desvio autorizado na task
    de anonimização e aqui na saída da sumarização) garante que, em inline, o
    claim da cadeia roda com a lease do worker anterior já liberada — conflito
    aqui é anomalia real → ``CaseLockConflictError`` propagada (fail-closed
    honesto, sem retry).

    Async (``False``): conflito é OUTRO worker cuidando do caso; a task vira
    no-op com log (a idempotência por estado cobre a reexecução).
    """
    try:
        return claim_case_lock(case, user=None, context=WORKER_LOCK_CONTEXT, role=SYSTEM_ROLE)
    except CaseLockConflictError:
        if not getattr(settings, "LLM_RUN_TASKS_INLINE", True):
            logger.warning(
                "process_case_pipeline: lock ativo de outro ator no caso %s — no-op "
                "(async; idempotência cobre)",
                case_id,
                exc_info=True,
            )
            return None
        raise


def _failure_reason(exc: Exception) -> str:
    """Motivo canônico da falha para o payload do ``fail_processing`` (D4)."""
    if isinstance(exc, LlmPipelineError):
        return exc.reason
    return str(exc)


def _is_pipeline_state(status: CaseStatus) -> bool:
    return status in (CaseStatus.LLM_EXTRACTING, CaseStatus.LLM_SUMMARIZING)


def process_case_pipeline(case_id: uuid.UUID) -> None:
    """Processa o caso pelo pipeline LLM (entry point do worker/cluster llm).

    Idempotência por estado: ``LLM_EXTRACTING`` = pipeline completo (LLM1 +
    reconciliação; divergência retém); ``LLM_SUMMARIZING`` = retomada
    (policy/prior/LLM2/complete); demais estados → log + no-op. Claim de lock
    ``context="worker_llm"``/``role="system"``; release no MESMO atomic da
    transição de saída (``complete_llm_summarization``) e o ``finally`` re-tenta
    nos demais caminhos; exceção → ``fail_processing`` (→ ``FAILED``) com o
    motivo na trilha.

    Raises:
        ValueError: caso inexistente.
        CaseLockConflictError: lock ativo de outro ator (inline — anomalia;
            propagada; em async o conflito é no-op com log).
    """
    try:
        case = Case.objects.get(case_id=case_id)
    except Case.DoesNotExist:
        logger.error("process_case_pipeline: caso %s não encontrado", case_id)
        raise ValueError(f"caso {case_id} não encontrado") from None

    if not _is_pipeline_state(case.status):
        logger.info(
            "process_case_pipeline: caso %s fora de LLM_EXTRACTING/LLM_SUMMARIZING "
            "(status=%s) — no-op",
            case_id,
            case.status,
        )
        return

    lock = _claim_worker_lock(case, case_id=case_id)
    if lock is None:
        # Conflito async (outro worker cuidando): no-op deliberado.
        return
    released = False
    try:
        # Releitura sob posse da lease: entre a checagem e o claim o caso pode
        # ter sido processado por outra execução — a decisão é sempre fresca.
        case.refresh_from_db()
        if not _is_pipeline_state(case.status):
            logger.info(
                "process_case_pipeline: caso %s processado entre o claim e o início "
                "(status=%s) — no-op",
                case_id,
                case.status,
            )
            return

        if case.status == CaseStatus.LLM_EXTRACTING:
            # Etapa LLM1 + reconciliação (a self-transition tem source
            # LLM_EXTRACTING e não re-dispara o signal de entrada).
            case.start_llm_extraction(user=None, role=SYSTEM_ROLE)
            extraction = run_llm1_extraction(case, user=None, role=SYSTEM_ROLE)
            # Gate do slice 002: a chamada LLM é longa e pode retornar DEPOIS do
            # encerramento administrativo — refresh ANTES do ramo de divergência
            # e de ``complete_llm_extraction`` (o save full da transição
            # ressuscitaria status/campos/lock de uma linha CLEANED).
            case.refresh_from_db()
            if case.status == CaseStatus.CLEANED:
                logger.info(
                    "process_case_pipeline: caso %s encerrado administrativamente após o "
                    "LLM1 — abortando",
                    case_id,
                )
                return
            if extraction.has_divergence:
                logger.info(
                    "process_case_pipeline: caso %s retido por divergência declarado×detectado "
                    "(sem LLM2)",
                    case_id,
                )
                return
            case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)

        # Sumarização (continuação da extração ou retomada pós-bypass).
        case.refresh_from_db()
        if case.status == CaseStatus.CLEANED:
            logger.info(
                "process_case_pipeline: caso %s encerrado administrativamente antes da "
                "sumarização — abortando",
                case_id,
            )
            return
        case.start_llm_summarization(user=None, role=SYSTEM_ROLE)
        evaluate_case_policies(case, user=None, role=SYSTEM_ROLE)
        record_prior_case_lookups(case, user=None, role=SYSTEM_ROLE)
        # Os serviços acima persistem em instâncias lockadas; a releitura
        # alinha o objeto em memória (policy_result etc.) antes da LLM2.
        case.refresh_from_db()
        if case.status == CaseStatus.CLEANED:
            logger.info(
                "process_case_pipeline: caso %s encerrado administrativamente antes do "
                "LLM2 — abortando",
                case_id,
            )
            return
        run_llm2_summarization(case, user=None, role=SYSTEM_ROLE)

        # Coordenação transição+release no MESMO atomic (padrão change 05): a
        # ``complete_llm_summarization`` emite o evento de entrada em
        # AWAITING_DOCTOR; qualquer hook ``on_commit`` (fila médica, 07) só
        # dispara com a lease ``worker_llm`` já liberada.
        with transaction.atomic():
            current = Case.objects.select_for_update().get(pk=case.pk)
            # Gate do slice 002: encerramento administrativo durante a chamada
            # llm2 (sentinel) → abort benigno, sem transição nem log de erro.
            if current.status == CaseStatus.CLEANED:
                return
            current.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
            try:
                release_case_lock(current, lock.token)
                released = True
            except CaseLockConflictError:
                # Lease expirada durante o processamento (anomalia rara): o
                # resultado da fase permanece e o finally re-tenta o release.
                logger.warning(
                    "process_case_pipeline: lease do caso %s expirou no atomic final",
                    case_id,
                    exc_info=True,
                )
    except Exception as exc:
        logger.exception("process_case_pipeline: pipeline falhou para o caso %s", case_id)
        # O rollback do atomic final desfez transição/eventos; a releitura
        # garante o estado real (e o fail_processing válido) antes do fail.
        case.refresh_from_db()
        # Gate do slice 002: falha APÓS o encerramento não chama
        # ``fail_processing`` (seria TransitionNotAllowed em CLEANED).
        if case.status == CaseStatus.CLEANED:
            logger.info(
                "process_case_pipeline: caso %s encerrado administrativamente — "
                "sem fail_processing",
                case_id,
            )
            return
        case.fail_processing(reason=_failure_reason(exc), user=None, role=SYSTEM_ROLE)
    finally:
        if not released:
            try:
                release_case_lock(case, lock.token)
            except CaseLockConflictError:
                logger.warning(
                    "process_case_pipeline: lease do caso %s expirou antes do release",
                    case_id,
                    exc_info=True,
                )
