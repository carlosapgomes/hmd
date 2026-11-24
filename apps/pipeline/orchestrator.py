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

from apps.cases.locks import (
    CaseLock,
    CaseLockConflictError,
    assert_case_lock,
    claim_case_lock,
    release_case_lock,
)
from apps.cases.models import Case, CaseStatus
from apps.cases.procedure_review import (
    anonymized_text_digest_for_case,
    has_procedure_review_marker,
    is_gate_reason,
    is_processing_complete,
    latest_confirmation_event,
    latest_confirmation_validity,
    pseudonym_map_digest_for_case,
    record_review_invalidated,
    source_fingerprint_for_case,
)
from apps.cases.procedures import get_declared_procedure_types
from apps.cases.reappearance import (
    ReappearanceDriftError,
    archive_scheduling_repeat_after_llm1,
    finalize_summarization,
)
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


def _fail_processing_if_still_own_pipeline_case(case_pk: uuid.UUID, lock_token: uuid.UUID) -> str:
    """FAILED guardado sob UM row lock (B-guard, R4.2).

    Checagem de estado+posse e a mutação ``fail_processing`` ocorrem no
    mesmo ``atomic`` com ``select_for_update`` na linha do incoming: um
    concorrente não pode trocar estado/lease entre a guarda e a escrita
    (o ``save`` full de ``_run_transition`` sobre a linha travada fresca
    nunca ressuscita estado/lease obsoletos). Sem mutex advisory após o
    row lock e sem tocar outro caso. Retorna ``"applied"``,
    ``"not-pipeline"`` (pronto/decidido/encerrado — preservado) ou
    ``"no-lease"`` (lease alheia/expirada — preservada, sem efeito).
    """
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case_pk)
        if not _is_pipeline_state(locked.status):
            return "not-pipeline"
        try:
            assert_case_lock(locked, lock_token)
        except CaseLockConflictError:
            return "no-lease"
        locked.fail_processing(reason=ReappearanceDriftError.reason, user=None, role=SYSTEM_ROLE)
        return "applied"


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


def _reconcile_stale_authority(case: Case, *, lock_token: uuid.UUID) -> str:
    """HD-001: descarta a resposta estagnada e refaz a reconciliação automática.

    Devolve ``"cleaned"`` (abortar), ``"archived"`` (reapresentação de
    agendamento arquivada com própria lease liberada), ``"retain"``
    (divergência vigente) ou ``"continue"`` (extração completa, seguir para
    sumarização).
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
    sched = archive_scheduling_repeat_after_llm1(
        case_pk=case.pk,
        lock_token=lock_token,
        reconciled=True,
        user=None,
        role=SYSTEM_ROLE,
    )
    if sched.status in ("archived", "aborted"):
        return "archived" if sched.lease_released else "cleaned"
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    return "continue"


def _normalize_recovery_call(
    case_id: uuid.UUID | str, confirmation_event_id: int | str | None
) -> tuple[uuid.UUID, int | None]:
    """Normaliza os argumentos serializados do Schedule de recovery (D7).

    O scheduler do django-q2 avalia os args persistidos (``ast.literal_eval``)
    e entrega ``(str_uuid, int)`` — a normalização aqui garante que o destino
    persistido execute corretamente. Seleção inválida (não-inteiro) é erro de
    código: levanta ``ValueError`` sem efeito colateral.
    """
    normalized_case_id = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id))
    if confirmation_event_id is None:
        return normalized_case_id, None
    if isinstance(confirmation_event_id, bool):
        raise ValueError(f"confirmation_event_id inválido: {confirmation_event_id!r}")
    try:
        normalized_confirmation = int(confirmation_event_id)
    except (TypeError, ValueError):
        raise ValueError(f"confirmation_event_id inválido: {confirmation_event_id!r}") from None
    return normalized_case_id, normalized_confirmation


def _is_stale_recovery(case: Case, confirmation_event_id: int) -> bool:
    """Recovery refere-se a confirmação diferente da vigente? (D7).

    Job atrasado (outra confirmação, caso concluído/encerrado ou sem
    confirmação) é no-op: nunca reescreve a seleção nem duplica efeitos —
    a autoridade vigente continua revalidada pelos guards normais.
    """
    latest = latest_confirmation_event(case)
    return latest is None or latest.pk != confirmation_event_id


def process_case_pipeline(
    case_id: uuid.UUID | str, confirmation_event_id: int | str | None = None
) -> None:
    """Processa o caso pelo pipeline LLM (entry point do worker/cluster llm).

    Idempotência por estado: ``LLM_EXTRACTING`` = pipeline completo (LLM1 +
    reconciliação; divergência retém); ``LLM_SUMMARIZING`` = retomada
    (policy/prior/LLM2/complete); demais estados → log + no-op. Claim de lock
    ``context="worker_llm"``/``role="system"``; release no MESMO atomic da
    transição de saída (``complete_llm_summarization``) e o ``finally`` re-tenta
    nos demais caminhos; exceção → ``fail_processing`` (→ ``FAILED``) com o
    motivo na trilha.

    Raises:
        ValueError: caso inexistente ou ``confirmation_event_id`` inválido.
        CaseLockConflictError: lock ativo de outro ator (inline — anomalia;
            propagada; em async o conflito é no-op com log).

    ``confirmation_event_id`` (só o recovery ONCE fornece) fixa a execução à
    confirmação vigente: job atrasado de outra confirmação é no-op (nunca
    reescreve a seleção); entradas normais (sem o argumento) seguem intactas.
    """
    normalized_case_id, normalized_confirmation = _normalize_recovery_call(
        case_id, confirmation_event_id
    )
    # A partir daqui o fluxo usa o UUID normalizado (o Scheduler entrega str).
    case_id = normalized_case_id
    try:
        case = Case.objects.get(case_id=case_id)
    except Case.DoesNotExist:
        logger.error("process_case_pipeline: caso %s não encontrado", case_id)
        raise ValueError(f"caso {case_id} não encontrado") from None

    if normalized_confirmation is not None and _is_stale_recovery(case, normalized_confirmation):
        logger.info(
            "process_case_pipeline: recovery do caso %s refere-se a confirmacao "
            "diferente da vigente (%s) — no-op",
            case_id,
            normalized_confirmation,
        )
        return

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
            sched = archive_scheduling_repeat_after_llm1(
                case_pk=case.pk,
                lock_token=lock.token,
                reconciled=True,
                user=None,
                role=SYSTEM_ROLE,
            )
            if sched.status in ("archived", "aborted"):
                released = sched.lease_released
                return
            case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
        elif case.status == CaseStatus.LLM_SUMMARIZING:
            sched = archive_scheduling_repeat_after_llm1(
                case_pk=case.pk,
                lock_token=lock.token,
                reconciled=False,
                user=None,
                role=SYSTEM_ROLE,
            )
            if sched.status in ("archived", "aborted"):
                released = sched.lease_released
                return

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
                outcome = _reconcile_stale_authority(case, lock_token=lock.token)
                if outcome == "archived":
                    released = True
                    return
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
            # Atomic final reestruturado (slice 002, D4 FINAL): mutex da série
            # antes do primeiro row lock, grupo ordenado por case_id,
            # revalidação, conclusão da sumarização, tentativa de substituição
            # médica e liberação da própria lease na MESMA transação — sem
            # chamar resolução depois de lock incoming prévio.
            finalization = finalize_summarization(
                case_pk=case.pk,
                lock_token=lock.token,
                marked_execution=marked_execution,
                execution_digests=execution_digests,
                execution_authority=execution_authority,
                execution_declared=execution_declared,
                user=None,
                role=SYSTEM_ROLE,
            )
            if finalization.status == "aborted":
                return
            if finalization.status == "failed":
                raise LlmPipelineError(
                    finalization.failure_reason or "procedure_review_incomplete",
                    "execução estagnada (integridade) antes da transição final — "
                    "sem encaminhamento.",
                )
            if finalization.status == "stale":
                outcome = _reconcile_stale_authority(case, lock_token=lock.token)
                if outcome == "archived":
                    released = True
                    return
                if outcome in ("cleaned", "retain"):
                    return
                continue
            released = finalization.lease_released
            break
    except ReappearanceDriftError:
        # D4/AUD-01: deriva persistente após 3 tentativas — erro técnico
        # explícito, sem repetir LLM nem agendar recuperação. O rollback do
        # atomic final preservou prior/decisões; só a própria lease é liberada
        # (finally) e o incoming ainda em processamento vai ao FAILED guardado.
        logger.exception(
            "process_case_pipeline: deriva de reapresentação persistente no caso %s — "
            "encaminhando ao FAILED sem reprocessamento",
            case_id,
        )
        # D4/R4.2 (B-guard): FAILED só p/ incoming AINDA em processamento e
        # sob a PRÓPRIA lease, com checagem+efeito sob um row lock — caso
        # pronto/decidido/encerrado ou sob lease de outro ator é preservado:
        # nunca TransitionNotAllowed, nunca sobrescrever a execução alheia
        # com instância obsoleta. O finally libera só a própria lease.
        outcome = _fail_processing_if_still_own_pipeline_case(case.pk, lock.token)
        if outcome != "applied":
            logger.info(
                "process_case_pipeline: caso %s preservado após deriva (%s) — sem fail_processing",
                case_id,
                outcome,
            )
            return
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
