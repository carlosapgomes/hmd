"""django-q2 tasks da anonimização (slice 004, design D7, R2/R4).

Entry point ``process_case_anonymization`` roda no cluster ``anonymization``
(ou inline quando ``ANONYMIZATION_RUN_TASKS_INLINE`` é True — dev/teste) e é
**idempotente por estado**: apenas casos em ``ANONYMIZING`` processam; demais
estados → no-op com log (reexecução do q2 nunca duplica efeitos). A operação
roda sob lock do caso (``context="worker_anonymization"``, ``role="system"``)
com release no ``finally`` do token do claim.

Conflito de claim (finding P1 do review): a coordenação que evita o conflito
inline vive no intake — a task-pdf executa o ``release_case_lock`` no MESMO
``transaction.atomic()`` da transição de saída (``complete_pdf_extraction``),
então os hooks ``on_commit`` da entrada em ANONYMIZING disparam só depois de a
lease ``worker_pdf`` ser liberada (dev single-process roda a anonimização já
sem contenção). Aqui a política de conflito é: inline → ``CaseLockConflictError``
propagada (fail-closed honesto); async → conflito é outro worker cuidando do
caso: no-op com log (a idempotência por estado cobre a reexecução) — sem
relaxar a exclusividade dos locks em hipótese nenhuma.

Pipeline fail-closed: ``start_anonymization`` (evento de início) → porteiro do
texto vazio (``extracted_text == ""`` → ``fail_processing("sem texto
extraído")``) → sucesso num único ``transaction.atomic()`` envolvendo o
wrapper do slice 003 (persistência de artefatos + evento) e a transição
``complete_anonymization`` (→ ``LLM_EXTRACTING``) — falha no meio não deixa
``anonymized_text`` preenchido; exceção do serviço → ``fail_processing`` (→
``FAILED``) com o motivo na trilha.

``enqueue_case_anonymization`` é o trigger do signal (R3): enfileira no
cluster ``anonymization`` quando fora de inline; senão executa a task
sincronamente. A self-transition ``start_anonymization`` emite
``source=ANONYMIZING`` e NÃO re-dispara (guarda do signal) — sem o filtro, o
modo inline recursaria com o lock na mão e o async duplicaria tasks.
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django.db import transaction
from django_q.tasks import async_task

from apps.anonymization.services import anonymize_case_text
from apps.cases.locks import CaseLock, CaseLockConflictError, claim_case_lock, release_case_lock
from apps.cases.models import Case, CaseStatus

logger = logging.getLogger(__name__)

# Cluster alternativo do django-q2 dedicado à anonimização (R1/D7): separado
# do ``pdf`` para o modelo spaCy não competir com a extração no mesmo worker.
ANONYMIZATION_CLUSTER = "anonymization"
# Contexto/papel do worker na concessão do lock de exclusividade do caso (D7).
WORKER_LOCK_CONTEXT = "worker_anonymization"
SYSTEM_ROLE = "system"

# Motivo canônico do fail-closed por ausência de texto (R4): o núcleo do 003 é
# defensivo e avançaria com texto vazio — quem falha fechado é a task.
EMPTY_TEXT_REASON = "sem texto extraído"


def _claim_worker_lock(case: Case, *, case_id: uuid.UUID) -> CaseLock | None:
    """Claim do lock do worker com a política de conflito do finding P1.

    A coordenação com a task-pdf (release da lease no mesmo atomic da transição
    de saída — ``apps/intake/tasks.py``) garante que, em inline, o claim roda
    SEMPRE após a lease ``worker_pdf`` ter sido liberada: conflito aqui é
    anomalia real → ``CaseLockConflictError`` propagada (fail-closed honesto,
    sem retry).

    Async (``False``): conflito é OUTRO worker cuidando do caso; a task vira
    no-op com log (a idempotência por estado cobre a reexecução) — sem retry,
    sem relaxar a exclusividade dos locks.

    Returns:
        ``CaseLock`` concedido, ou ``None`` quando o conflito async vira no-op.
    """
    try:
        return claim_case_lock(case, user=None, context=WORKER_LOCK_CONTEXT, role=SYSTEM_ROLE)
    except CaseLockConflictError:
        if not getattr(settings, "ANONYMIZATION_RUN_TASKS_INLINE", True):
            logger.warning(
                "process_case_anonymization: lock ativo de outro ator no caso %s — no-op "
                "(async; idempotência cobre)",
                case_id,
                exc_info=True,
            )
            return None
        raise


def _is_administratively_closed(case: Case) -> bool:
    """Gate do slice 002: o caso foi encerrado administrativamente (CLEANED)?

    Re-lê a row fresca (a instância em memória pode ser pré-encerramento) e
    devolve ``True`` quando o status persistido é ``CLEANED``. O worker em voo
    com lease expirada retorna depois do encerramento e não pode persistir
    artefatos nem a transição: o ``save()`` full do serviço ressuscitaria
    status, campos clínicos e os campos de lock de uma linha minimizada.
    """
    case.refresh_from_db()
    return bool(case.status == CaseStatus.CLEANED)


def process_case_anonymization(case_id: uuid.UUID) -> None:
    """Anonimiza o caso (entry point do worker/cluster anonymization).

    Idempotência por estado: apenas ``ANONYMIZING`` processa; ``NEW``,
    ``PDF_EXTRACTING``, ``LLM_EXTRACTING`` e demais → log + no-op. Claim de
    lock ``context="worker_anonymization"``/``role="system"``; a lease é
    liberada no MESMO atomic da transição de saída consumida por signal
    (``complete_anonymization`` — entrada em ``LLM_EXTRACTING``; coordenação do
    change 05, estendida no slice 006 do pipeline LLM) e o ``finally`` re-tenta
    o release nos demais caminhos; erro do serviço → ``fail_processing`` (→
    ``FAILED``) sem ``anonymized_text`` preenchido (fail-closed). Política de
    conflito em ``_claim_worker_lock``: inline propaga (coordenação com o
    release da task-pdf evita o conflito); async vira no-op com log.

    Raises:
        ValueError: caso inexistente.
        CaseLockConflictError: lock ativo de outro ator (inline — anomalia;
            propagada; em async o conflito é no-op com log, coberto pela
            idempotência por estado).
    """
    try:
        case = Case.objects.get(case_id=case_id)
    except Case.DoesNotExist:
        logger.error("process_case_anonymization: caso %s não encontrado", case_id)
        raise ValueError(f"caso {case_id} não encontrado") from None

    if case.status != CaseStatus.ANONYMIZING:
        logger.info(
            "process_case_anonymization: caso %s fora de ANONYMIZING (status=%s) — no-op",
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
        if case.status != CaseStatus.ANONYMIZING:
            logger.info(
                "process_case_anonymization: caso %s processado entre o claim e o início "
                "(status=%s) — no-op",
                case_id,
                case.status,
            )
            return

        case.start_anonymization(user=None, role=SYSTEM_ROLE)
        # Porteiro fail-closed (R4): caso em ANONYMIZING sem texto extraído não
        # avança — quem valida o texto vazio é a task, não o núcleo do 003.
        if case.extracted_text == "":
            case.fail_processing(reason=EMPTY_TEXT_REASON, user=None, role=SYSTEM_ROLE)
            return

        # Gate do slice 002 (painel-lista-encerramento): re-lê ANTES de
        # ``anonymize_case_text``/``complete_anonymization`` — o ``save()`` full
        # do serviço ressuscitaria ``status``/campos clínicos/lock de uma linha
        # CLEANED (worker com lease expirada que retorna após o encerramento).
        if _is_administratively_closed(case):
            logger.info(
                "process_case_anonymization: caso %s encerrado administrativamente durante "
                "a anonimização — abortando sem persistir",
                case_id,
            )
            return

        try:
            # Persistência (wrapper 003) e transição FSM atômicas: em falha
            # nada é escrito — ``anonymized_text`` permanece vazio (fail-closed).
            # O release da lease roda no MESMO atomic da transição de saída cujo
            # evento é consumido por signal (a entrada em LLM_EXTRACTING dispara
            # o enqueue do pipeline LLM — slice 006): o commit garante o release
            # antes dos hooks ``on_commit`` (padrão change 05/finding P1; desvio
            # autorizado no slice 006). O ``finally`` re-tenta nos demais
            # caminhos (falhas/estados não avançados) — release duplo é no-op.
            with transaction.atomic():
                # Gate serializado do slice 002: re-leitura sob lock de linha
                # fecha a janela entre o gate externo e o save full do serviço
                # (encerramento commitando durante a anonimização em si).
                locked = Case.objects.select_for_update().get(pk=case.pk)
                if locked.status == CaseStatus.CLEANED:
                    return
                anonymize_case_text(case)
                case.complete_anonymization(user=None, role=SYSTEM_ROLE)
                try:
                    release_case_lock(case, lock.token)
                    released = True
                except CaseLockConflictError:
                    # Lease expirada durante o processamento (anomalia rara): o
                    # resultado da fase permanece; o finally re-tenta o release.
                    logger.warning(
                        "process_case_anonymization: lease do caso %s expirou no atomic final",
                        case_id,
                        exc_info=True,
                    )
        except Exception as exc:
            logger.exception(
                "process_case_anonymization: anonimização falhou para o caso %s", case_id
            )
            # Descarta qualquer estado sujo em memória do atomic que falhou
            # antes do fail_processing (o banco já está íntegro).
            case.refresh_from_db()
            # Gate do slice 002: falha APÓS o encerramento não chama
            # ``fail_processing`` (seria TransitionNotAllowed em CLEANED).
            if case.status == CaseStatus.CLEANED:
                logger.info(
                    "process_case_anonymization: caso %s encerrado administrativamente "
                    "durante a anonimização — sem fail_processing",
                    case_id,
                )
                return
            case.fail_processing(reason=str(exc), user=None, role=SYSTEM_ROLE)
    finally:
        if not released:
            try:
                release_case_lock(case, lock.token)
            except CaseLockConflictError:
                # Lease expirada durante o processamento (ou já liberada no
                # atomic de sucesso): o lock se perdeu, mas o resultado do
                # processamento não deve ser mascarado pelo release.
                logger.warning(
                    "process_case_anonymization: lease do caso %s expirou antes do release",
                    case_id,
                    exc_info=True,
                )


def enqueue_case_anonymization(case_id: uuid.UUID) -> None:
    """Enfileira a anonimização do caso no cluster anonymization (ou inline).

    Com ``ANONYMIZATION_RUN_TASKS_INLINE=True`` executa a task sincronamente
    (testes determinísticos); senão enfileira via django-q2 com
    ``q_options={"cluster": "anonymization"}`` (R2). Chamado pelo receiver do
    signal R3 via ``transaction.on_commit`` (rollback não enfileira).
    """
    if getattr(settings, "ANONYMIZATION_RUN_TASKS_INLINE", True):
        process_case_anonymization(case_id)
        return
    async_task(
        "apps.anonymization.tasks.process_case_anonymization",
        case_id,
        q_options={
            "cluster": ANONYMIZATION_CLUSTER,
            "task_name": f"anonymization:{case_id}",
        },
    )
