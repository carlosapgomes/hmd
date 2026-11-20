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
from apps.cases.procedure_review import (
    REASON_ANONYMIZATION_MISSING,
    REASON_ANONYMIZED_MISMATCH,
    REASON_DECLARATION_CHANGED,
    REASON_MAP_MISMATCH,
    REASON_SOURCE_CHANGED,
    REASON_SOURCE_UNAVAILABLE,
    anonymized_text_digest_for_case,
    has_procedure_review_marker,
    is_gate_reason,
    is_processing_complete,
    latest_confirmation_validity,
    pseudonym_map_digest_for_case,
    record_review_invalidated,
    source_fingerprint_for_case,
)
from apps.cases.procedures import get_declared_procedure_types
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


def _assert_confirmation_for_dispatch(case: Case) -> None:
    """Valida a autoridade antes de um dispatch externo (D3, sem CLEANED).

    Sem confirmação → retorna (automático). Válida → retorna (confirmado).
    Gate (revisão/declaração íntegres) → audita INVALIDATED uma vez e retorna
    (automático segue). Integridade/malformação → audita e levanta
    ``LlmPipelineError(motivo)`` SEM dispatch (o handler vira FAILED). O
    chamador decide ``CLEANED`` antes — caso encerrado nunca invalida/falha.
    """
    confirmation_event, validity = latest_confirmation_validity(case)
    if confirmation_event is None or validity is None:
        return
    if validity.valid:
        return
    record_review_invalidated(case, confirmation_event, validity.reason)
    if is_gate_reason(validity.reason):
        return
    raise LlmPipelineError(
        validity.reason,
        "confirmação de procedimento inválida (integridade) — sem dispatch.",
    )


def _authority_snapshot(case: Case) -> tuple[int | None, bool | None, str | None]:
    """Snapshot (confirmação, válida, motivo) para detectar estagnação nova."""
    event, validity = latest_confirmation_validity(case)
    return (
        event.pk if event is not None else None,
        validity.valid if validity is not None else None,
        validity.reason if validity is not None else None,
    )


def _is_new_gate_stale(
    pre: tuple[int | None, bool | None, str | None],
    post: tuple[int | None, bool | None, str | None],
) -> bool:
    """Autoridade ficou estagnada (gate) DURANTE esta execução?

    Só quando a pós-LLM2 é gate inválida e difere do snapshot pré-dispatch:
    um INVALIDATED histórico de gate já consumido (mesma confirmação/motivo)
    não re-dispara a reconciliação (HD-001).
    """
    _event_pk, valid, reason = post
    return valid is False and reason is not None and is_gate_reason(reason) and post != pre


def _assert_processing_complete(case: Case) -> None:
    """Guard de completude dos casos marcados (D6) antes do encaminhamento."""
    if has_procedure_review_marker(case) and not is_processing_complete(case):
        raise LlmPipelineError(
            "procedure_review_incomplete",
            "caso marcado sem extração válida, resumo útil ou análise completa "
            "do conjunto efetivo — sem encaminhamento.",
        )


def _capture_execution_digests(case: Case) -> dict[str, str | None]:
    """Snapshot pós-LLM2 da integridade da execução (só hashes, nunca textos)."""
    return {
        "source_fingerprint": source_fingerprint_for_case(case),
        "anonymized_text_digest": anonymized_text_digest_for_case(case),
        "pseudonym_map_digest": pseudonym_map_digest_for_case(case),
    }


def _execution_digest_failure(snapshot: dict[str, str | None], case: Case) -> str | None:
    """Motivo de integridade se a linha travada diverge do snapshot da execução."""
    current_source = source_fingerprint_for_case(case)
    if snapshot["source_fingerprint"] is not None and current_source is None:
        return REASON_SOURCE_UNAVAILABLE
    if current_source != snapshot["source_fingerprint"]:
        return REASON_SOURCE_CHANGED
    if anonymized_text_digest_for_case(case) != snapshot["anonymized_text_digest"]:
        if not (case.anonymized_text or ""):
            return REASON_ANONYMIZATION_MISSING
        return REASON_ANONYMIZED_MISMATCH
    if pseudonym_map_digest_for_case(case) != snapshot["pseudonym_map_digest"]:
        return REASON_MAP_MISMATCH
    return None


def _reconcile_stale_authority(case: Case) -> str:
    """HD-001: descarta a resposta estagnada e refaz a reconciliação automática.

    Devolve ``"cleaned"`` (abortar), ``"retain"`` (divergência vigente) ou
    ``"continue"`` (extração completa, seguir para sumarização).
    """
    _event, _validity = latest_confirmation_validity(case)
    if _event is not None and _validity is not None and not _validity.valid:
        record_review_invalidated(case, _event, _validity.reason)
    case.return_to_llm_extracting(user=None, role=SYSTEM_ROLE)
    case.start_llm_extraction(user=None, role=SYSTEM_ROLE)
    extraction = run_llm1_extraction(case, user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    if case.status == CaseStatus.CLEANED:
        return "cleaned"
    if extraction.has_divergence:
        logger.info(
            "process_case_pipeline: caso %s retido após reconciliação HD-001 "
            "(divergência automática vigente, sem LLM2)",
            case.case_id,
        )
        return "retain"
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    return "continue"


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
        _assert_confirmation_for_dispatch(case)

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

        # Sumarização (continuação da extração ou retomada pós-bypass), com
        # reconciliação HD-001: autoridade estagnada durante a LLM2 com
        # integridade intacta descarta a resposta e refaz LLM1 automática.
        while True:
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
            case.refresh_from_db()
            if case.status == CaseStatus.CLEANED:
                logger.info(
                    "process_case_pipeline: caso %s encerrado administrativamente antes do "
                    "LLM2 — abortando",
                    case_id,
                )
                return
            _assert_confirmation_for_dispatch(case)
            pre_authority = _authority_snapshot(case)
            stale_gate = False
            try:
                run_llm2_summarization(case, user=None, role=SYSTEM_ROLE)
            except LlmPipelineError as llm2_exc:
                if not is_gate_reason(llm2_exc.reason):
                    raise
                stale_gate = True
            case.refresh_from_db()
            if case.status == CaseStatus.CLEANED:
                return
            post_authority = _authority_snapshot(case)
            if stale_gate or _is_new_gate_stale(pre_authority, post_authority):
                # HD-001: resposta estagnada já descartada pela LLM2 (nunca
                # persistida). Audita uma vez, retorna via operação nomeada e
                # refaz a extração/reconciliação automática vigente.
                outcome = _reconcile_stale_authority(case)
                if outcome in ("cleaned", "retain"):
                    return
                continue
            # Snapshot da execução pós-LLM2 (fix-2 P1-2): capturado ANTES das
            # checagens externas ao lock — qualquer mutação entre aqui e o
            # atomic final diverge da linha travada e é revalidada abaixo.
            marked_execution = has_procedure_review_marker(case)
            execution_digests = _capture_execution_digests(case) if marked_execution else None
            execution_authority = post_authority
            execution_declared = get_declared_procedure_types(case)
            _assert_confirmation_for_dispatch(case)
            _assert_processing_complete(case)
            final_failure: str | None = None
            final_stale_gate = False
            with transaction.atomic():
                current = Case.objects.select_for_update().get(pk=case.pk)
                if current.status == CaseStatus.CLEANED:
                    return
                if marked_execution and execution_digests is not None:
                    digest_failure = _execution_digest_failure(execution_digests, current)
                    fresh_authority = _authority_snapshot(current)
                    _fresh_event, fresh_validity = latest_confirmation_validity(current)
                    if digest_failure is not None:
                        final_failure = digest_failure
                    elif (
                        fresh_authority != execution_authority
                        and fresh_validity is not None
                        and not fresh_validity.valid
                    ):
                        if is_gate_reason(fresh_validity.reason):
                            final_stale_gate = True
                        else:
                            final_failure = fresh_validity.reason
                    elif get_declared_procedure_types(current) != execution_declared:
                        final_stale_gate = True
                    if final_failure is not None or final_stale_gate:
                        if _fresh_event is not None:
                            record_reason = final_failure
                            if record_reason is None:
                                record_reason = (
                                    fresh_validity.reason
                                    if fresh_validity is not None and not fresh_validity.valid
                                    else REASON_DECLARATION_CHANGED
                                )
                            record_review_invalidated(current, _fresh_event, record_reason)
                if final_failure is None and not final_stale_gate:
                    _assert_processing_complete(current)
                    current.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
                    try:
                        release_case_lock(current, lock.token)
                        released = True
                    except CaseLockConflictError:
                        logger.warning(
                            "process_case_pipeline: lease do caso %s expirou no atomic final",
                            case_id,
                            exc_info=True,
                        )
            if final_failure is not None:
                raise LlmPipelineError(
                    final_failure,
                    "execução estagnada (integridade) antes da transição final — "
                    "sem encaminhamento.",
                )
            if final_stale_gate:
                outcome = _reconcile_stale_authority(case)
                if outcome in ("cleaned", "retain"):
                    return
                continue
            break
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
