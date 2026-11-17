"""django-q2 tasks do processamento de anexos (change attachment-processing-ocr,
slices 002/003, D3/R5).

Entry point ``process_case_attachments(case_id)`` roda no cluster
``attachments`` (ou inline quando ``ATTACHMENTS_RUN_TASKS_INLINE`` é True —
dev/teste, padrão dos workers existentes) e é **idempotente por etapa**: anexo
com ``extracted_text``/``extraction_method`` já persistidos NÃO re-extraí nem
re-envia ao OCR externo (retry do q2 nunca duplica o envio nem o evento de
auditoria); anexo com ``anonymized_text`` já persistido pula direto para a
verificação; anexo em estado terminal (``processed``/``failed``) é no-op; a row
é RE-LIDA antes de cada gravação/evento — anexo removido pela ciência do NIR
durante a corrida → ``CaseAttachment.DoesNotExist`` → no-op silencioso SEM
evento.

Gates D8 (ADR-0010, final): gates rápidos de status entre etapas (re-leitura
sem lock, fast path) e **transações CURTAS** com
``Case.objects.select_for_update()`` em cada ponto de decisão/escrita — o
status é re-lido sob o lock DENTRO da mesma transação da escrita/evento
(persistência de extração, anonimização, verificação em sucesso e em falha,
``_fail_attachment``); ``CLEANED`` → no-op silencioso (commit sem escrita,
sem evento, sem fail). **Nenhuma transação longa segura o lock durante
chamadas externas** e não há compensação: a trilha segue append-only. A
chamada LLM da verificação fica FORA de qualquer transação, entre a
pré-checagem gateada e a persistência gateada (as peças públicas de
``verification.py`` — request/persist — sem duplicar o pipeline; o
``verify_attachment`` sem gate orquestra as mesmas peças como referência).

**Dispatch durável**: no fluxo de extração com OCR externo, após a
rasterização, uma transação curta re-lê o status sob o lock e insere o
``CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED`` com COMMIT ANTES do envio HTTP
imediato (nenhuma I/O entre commit e envio; o callback de dispatch injeta
essa transação e a extração chama o OCR logo em seguida). O registro de
auditoria sobrevive a falha/crash após o PHI ter saído. Se a transação do
evento abortar por ``CLEANED`` → no-op (sem fail). **Janela residual
aceita**: entre o commit do evento de dispatch e o início do envio
(µs–ms). Alternativas rejeitadas na D8: segurar o lock durante o envio
fecharia a janela, mas o rollback apagaria a auditoria após o PHI sair;
lease operacional conflitaria com o pipeline LLM paralelo.

Pipeline por anexo (fail-closed por anexo — anexo é suplementar, o caso nunca
é bloqueado): ``pending``/``processing`` → status ``processing`` → extração
híbrida (``extraction.py``) → persistência de ``extracted_text``/
``extraction_method`` → anonimização alinhada ao caso (R1: ``anonymize_text``
semeado com ``case.pseudonym_map`` via ``anonymize_attachment_text`` — texto
anonimizado + mapa do anexo persistidos na row) → verificação LLM
(``verification``, R4: fecha como ``processed`` com patient_match/evento ou
como ``failed``). Texto extraído vazio pós-extração → ``failed`` com motivo
claro (R5). **Evento de auditoria
``CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED`` (ator system, payload
filename+método) é gravado duravelmente ANTES de qualquer envio externo**
(transação curta do callback, que só dispara no caminho vision). Falha em
qualquer etapa do anexo → ``failed`` + ``failed_reason`` +
``CASE_ATTACHMENT_FAILED`` (transação curta gateada — em caso aberto o
DISPATCHED já commitado permanece), sem afetar os demais anexos nem o caso.

``enqueue_case_attachments`` é o serviço de enqueue do signal (R4): executa a
task sincronamente fora de inline; senão enfileira via django-q2 com
``q_options={"cluster": "attachments"}`` (produção/compose com o serviço
worker-attachments).
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django.db import transaction
from django_q.tasks import async_task

from apps.anonymization.services import anonymize_attachment_text
from apps.attachments.extraction import extract_attachment_text
from apps.attachments.models import AttachmentStatus, CaseAttachment, ExtractionMethod
from apps.attachments.verification import (
    VERIFICATION_FAILURE_EXCEPTIONS,
    VerificationOutcome,
    persist_processed_outcome,
    persist_verification_failure,
    request_verification_outcome,
)
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent, CaseStatus

logger = logging.getLogger(__name__)

# Cluster alternativo do django-q2 dedicado aos anexos (D3).
ATTACHMENTS_CLUSTER = "attachments"
SYSTEM_ROLE = "system"


class _CaseClosedBeforeDispatchError(Exception):
    """Controle interno: o caso foi encerrado antes do dispatch (aborta o envio)."""


def _lock_case(case_id: uuid.UUID) -> Case | None:
    """Trava a linha do Case (``select_for_update``); caso inexistente → None."""
    try:
        return Case.objects.select_for_update().get(pk=case_id)
    except Case.DoesNotExist:
        return None


def _case_is_cleaned(case_id: uuid.UUID) -> bool:
    """Gate rápido D8: o caso já foi encerrado (``CLEANED``)? Re-lê o status.

    Fast path entre etapas (sem lock) — a decisão autoritativa é sempre
    re-lida sob lock dentro da transação curta de cada escrita.
    """
    return Case.objects.filter(pk=case_id, status=CaseStatus.CLEANED).exists()


def process_case_attachments(case_id: uuid.UUID) -> None:
    """Processa os anexos pendentes do caso (entry point do worker/cluster).

    Caso inexistente ou sem anexos → no-op (retorno imediato). A fila de pks é
    um snapshot da iteração; cada anexo é re-lido antes de gravar/emitir (a
    ciência do NIR pode remover a row durante a corrida). Falha por anexo →
    ``failed`` + ``CASE_ATTACHMENT_FAILED``; os demais anexos seguem.
    """
    try:
        case = Case.objects.get(case_id=case_id)
    except Case.DoesNotExist:
        logger.warning("process_case_attachments: caso %s não encontrado — no-op", case_id)
        return
    if case.status == CaseStatus.CLEANED:
        return

    attachment_pks = list(case.attachments.values_list("pk", flat=True))
    for attachment_pk in attachment_pks:
        if _case_is_cleaned(case_id):
            return
        _process_attachment(case_id, attachment_pk)


def enqueue_case_attachments(case_id: uuid.UUID) -> None:
    """Enfileira o processamento dos anexos no cluster attachments (ou inline).

    Com ``ATTACHMENTS_RUN_TASKS_INLINE=True`` executa a task sincronamente
    (testes determinísticos); senão enfileira via django-q2 com
    ``q_options={"cluster": "attachments"}`` (D3). Chamado pelo receiver do
    signal R4 via ``transaction.on_commit`` (rollback não enfileira).
    """
    if getattr(settings, "ATTACHMENTS_RUN_TASKS_INLINE", True):
        process_case_attachments(case_id)
        return
    async_task(
        "apps.attachments.tasks.process_case_attachments",
        case_id,
        q_options={
            "cluster": ATTACHMENTS_CLUSTER,
            "task_name": f"attachments:{case_id}",
        },
    )


# ── Helpers internos ──────────────────────────────────────────────────────


def _process_attachment(case_id: uuid.UUID, attachment_pk: int) -> None:
    """Processa UM anexo ponta a ponta (idempotente por etapa; no-op em corrida).

    Extração (quando ainda não há texto/método) → anonimização alinhada ao caso
    (quando ainda não há texto anonimizado) → verificação LLM (fecha como
    ``processed`` ou ``failed``). Cada etapa re-lê a row: removida pela ciência
    do NIR → no-op silencioso SEM evento. Falha em qualquer etapa → ``failed``
    com motivo claro + ``CASE_ATTACHMENT_FAILED`` (fail-closed por anexo).
    """
    if _case_is_cleaned(case_id):
        return
    attachment = _read_attachment(case_id, attachment_pk)
    if attachment is None:
        return
    if attachment.status in (AttachmentStatus.PROCESSED, AttachmentStatus.FAILED):
        return

    if not (attachment.extracted_text or attachment.extraction_method):
        attachment = _run_extraction_stage(case_id, attachment_pk)
        if attachment is None:
            return
    elif attachment.status != AttachmentStatus.PROCESSING:
        # Save em transação curta gateada (D8): lock + status sob o lock.
        with transaction.atomic():
            locked_case = _lock_case(case_id)
            if locked_case is None or locked_case.status == CaseStatus.CLEANED:
                return
            current = _read_attachment(case_id, attachment_pk)
            if current is None:
                return
            current.status = AttachmentStatus.PROCESSING
            current.save(update_fields=["status"])
            attachment = current

    if not attachment.extracted_text.strip():
        # R5: texto extraído vazio (ex.: OCR externo devolveu vazio) — motivo
        # claro, sem tentar anonimizar/verificar conteúdo inexistente.
        method = attachment.extraction_method or "método desconhecido"
        _fail_attachment(
            case_id,
            attachment_pk,
            reason=f"texto extraído vazio após a extração ({method}) — anexo sem conteúdo processável",
        )
        return

    if _case_is_cleaned(case_id):
        return
    if not attachment.anonymized_text and not attachment.pseudonym_map:
        _anonymize_stage(case_id, attachment_pk, attachment)

    if _case_is_cleaned(case_id):
        return
    try:
        _verify_stage(case_id, attachment_pk)
    except Exception as exc:  # rede de segurança — verificação inesperada
        _fail_attachment(case_id, attachment_pk, reason=f"falha na verificação do anexo: {exc}")


def _read_attachment(case_id: uuid.UUID, attachment_pk: int) -> CaseAttachment | None:
    """Re-lê a row com o caso; removida (ciência do NIR) → ``None`` (no-op)."""
    try:
        return CaseAttachment.objects.select_related("case").get(pk=attachment_pk, case_id=case_id)
    except CaseAttachment.DoesNotExist:
        logger.info(
            "process_case_attachments: anexo %s removido antes do processamento — no-op",
            attachment_pk,
        )
        return None


def _run_extraction_stage(case_id: uuid.UUID, attachment_pk: int) -> CaseAttachment | None:
    """Etapa de extração híbrida de UM anexo (idempotente; R3/D3).

    ``pending``/``processing`` → status ``processing`` → extração com hook de
    auditoria durável ANTES do OCR externo → persistência gateada de
    ``extracted_text``/``extraction_method`` (status permanece
    ``processing``). Devolve a row re-lida após a gravação; ``None`` quando
    removida/encerrada no meio. Exceção → ``failed`` com motivo +
    ``CASE_ATTACHMENT_FAILED`` (o DISPATCHED já commitado permanece).
    """
    if _case_is_cleaned(case_id):
        return None
    # Transação curta 1: lock + status sob o lock + status processing.
    with transaction.atomic():
        locked_case = _lock_case(case_id)
        if locked_case is None or locked_case.status == CaseStatus.CLEANED:
            return None
        try:
            attachment = CaseAttachment.objects.select_related("case").get(
                pk=attachment_pk, case_id=case_id
            )
        except CaseAttachment.DoesNotExist:
            return None
        attachment.status = AttachmentStatus.PROCESSING
        attachment.save(update_fields=["status"])

    def _dispatch_event() -> None:
        # Dispatch durável (D8): transação curta com lock+gate insere o
        # evento e COMMITA antes do envio HTTP imediato da extração.
        with transaction.atomic():
            locked = _lock_case(case_id)
            if locked is None or locked.status == CaseStatus.CLEANED:
                raise _CaseClosedBeforeDispatchError
            _record_event(
                attachment,
                event_type=CaseEventType.CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED,
                payload={
                    "filename": attachment.original_filename,
                    "method": ExtractionMethod.VISION,
                },
            )

    def _pre_send_guard() -> None:
        # Guard por página seguinte (D8/P1 review 3, P2-1): transação curta
        # re-lendo o status sob lock antes das páginas 2..N — CLEANED aborta
        # as restantes (exceção tipada propagada pela extração, sem capturar).
        # A 1ª página já está coberta pela transação do dispatch acima
        # (lock+gate+evento na mesma transação) — nenhuma transação extra
        # entre o commit do evento e o 1º envio.
        with transaction.atomic():
            locked = _lock_case(case_id)
            if locked is None or locked.status == CaseStatus.CLEANED:
                raise _CaseClosedBeforeDispatchError

    failure: Exception | None = None
    text = ""
    method = ""
    try:
        text, method = extract_attachment_text(
            attachment,
            on_external_dispatch=_dispatch_event,
            pre_send_guard=_pre_send_guard,
        )
    except _CaseClosedBeforeDispatchError:
        return None
    except Exception as exc:  # falha de extração/OCR (fail-closed por anexo)
        failure = exc
    if failure is not None:
        _fail_attachment(case_id, attachment_pk, reason=f"falha na extração do anexo: {failure}")
        return None
    # Transação curta 2: persistência gateada sob o lock.
    result: CaseAttachment | None = None
    with transaction.atomic():
        locked = _lock_case(case_id)
        if locked is None or locked.status == CaseStatus.CLEANED:
            return None
        current = _read_attachment(case_id, attachment_pk)
        if current is None:
            return None
        current.extracted_text = text
        current.extraction_method = method
        current.save(update_fields=["extracted_text", "extraction_method"])
        result = current
    return result


def _anonymize_stage(
    case_id: uuid.UUID,
    attachment_pk: int,
    attachment: CaseAttachment,
) -> None:
    """Anonimiza o texto do anexo no espaço de tokens do caso (R1/D4).

    Cálculo fora de transação; persistência em transação curta gateada
    (lock + status sob o lock; ``CLEANED`` → no-op sem escrita). Sem evento
    próprio (o evento fecha na verificação). Exceção → ``failed`` +
    ``CASE_ATTACHMENT_FAILED`` via ``_fail_attachment`` gateado.
    """
    if _case_is_cleaned(case_id):
        return
    try:
        result = anonymize_attachment_text(attachment.case, attachment.extracted_text)
    except Exception as exc:
        _fail_attachment(case_id, attachment_pk, reason=f"falha na anonimização do anexo: {exc}")
        return
    with transaction.atomic():
        locked_case = _lock_case(case_id)
        if locked_case is None or locked_case.status == CaseStatus.CLEANED:
            return
        current = _read_attachment(case_id, attachment_pk)
        if current is None:
            return
        current.anonymized_text = result.anonymized_text
        current.pseudonym_map = result.pseudonym_map
        current.save(update_fields=["anonymized_text", "pseudonym_map"])


def _verify_stage(case_id: uuid.UUID, attachment_pk: int) -> None:
    """Etapa de verificação LLM (R4) com persistência gateada (D8).

    Pré-checagem em transação curta (lock + gate + anexo não-terminal), chamada
    LLM FORA de qualquer transação, e persistência em transação curta gateada
    (sucesso e falha) — apenas gates + a chamada às peças públicas de
    ``verification.py`` (``request_verification_outcome`` + classificação
    ``VERIFICATION_FAILURE_EXCEPTIONS`` + persistências), a mesma orquestração
    de ``verify_attachment`` sem lista/regra duplicada. Exceção inesperada
    propaga para a rede de segurança de ``_process_attachment`` (que chama
    ``_fail_attachment`` gateado).
    """
    # Pré-checagem gateada (transação curta).
    with transaction.atomic():
        locked_case = _lock_case(case_id)
        if locked_case is None or locked_case.status == CaseStatus.CLEANED:
            return
        attachment = _read_attachment(case_id, attachment_pk)
        if attachment is None or attachment.status in (
            AttachmentStatus.PROCESSED,
            AttachmentStatus.FAILED,
        ):
            return
    try:
        outcome = request_verification_outcome(attachment)
    except VERIFICATION_FAILURE_EXCEPTIONS as exc:
        _gated_mark_failed(case_id, attachment_pk, exc)
        return
    _gated_persist_processed(case_id, attachment_pk, outcome)


def _gated_persist_processed(
    case_id: uuid.UUID, attachment_pk: int, outcome: VerificationOutcome
) -> None:
    """Persiste a verificação em transação curta gateada (CLEANED → no-op)."""
    with transaction.atomic():
        locked_case = _lock_case(case_id)
        if locked_case is None or locked_case.status == CaseStatus.CLEANED:
            return
        current = _read_attachment(case_id, attachment_pk)
        if current is None:
            return
        persist_processed_outcome(current, outcome)


def _gated_mark_failed(case_id: uuid.UUID, attachment_pk: int, exc: Exception) -> None:
    """Marca failed da verificação em transação curta gateada (CLEANED → no-op)."""
    with transaction.atomic():
        locked_case = _lock_case(case_id)
        if locked_case is None or locked_case.status == CaseStatus.CLEANED:
            return
        current = _read_attachment(case_id, attachment_pk)
        if current is None:
            return
        persist_verification_failure(current, exc)


def _fail_attachment(case_id: uuid.UUID, attachment_pk: int, *, reason: str) -> None:
    """Marca o anexo como ``failed`` com motivo + evento (fail-closed por anexo).

    Transação curta própria com lock+gate (D8): ``CLEANED`` → no-op
    silencioso (nunca grava failed/evento em caso encerrado); em caso
    aberto, marca failed normalmente (DISPATCHED já commitado permanece).
    """
    with transaction.atomic():
        locked_case = _lock_case(case_id)
        if locked_case is None or locked_case.status == CaseStatus.CLEANED:
            return
        attachment = _read_attachment(case_id, attachment_pk)
        if attachment is None:
            return
        attachment.status = AttachmentStatus.FAILED
        attachment.failed_reason = reason
        attachment.save(update_fields=["status", "failed_reason"])
        _record_event(
            attachment,
            event_type=CaseEventType.CASE_ATTACHMENT_FAILED,
            payload={
                "filename": attachment.original_filename,
                "reason": reason,
            },
        )


def _record_event(
    attachment: CaseAttachment,
    *,
    event_type: str,
    payload: dict[str, object],
) -> None:
    """Grava um evento não-transicional da trilha do caso com ator sistema."""
    CaseEvent.objects.create(
        case=attachment.case,
        event_type=event_type,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role=SYSTEM_ROLE,
        payload=payload,
    )
