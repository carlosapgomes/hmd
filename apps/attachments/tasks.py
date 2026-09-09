"""django-q2 tasks do processamento de anexos (change 10, slice 002, D3).

Entry point ``process_case_attachments(case_id)`` roda no cluster
``attachments`` (ou inline quando ``ATTACHMENTS_RUN_TASKS_INLINE`` é True —
dev/teste, padrão dos workers existentes) e é **idempotente por etapa**: anexo
com ``extracted_text``/``extraction_method`` já persistidos NÃO re-extraí nem
re-envia ao OCR externo (retry do q2 nunca duplica o envio nem o evento de
auditoria); anexo em estado terminal (``processed``/``failed``) é no-op; a row
é RE-LIDA antes de cada gravação/evento — anexo removido pela ciência do NIR
durante a corrida → ``CaseAttachment.DoesNotExist`` → no-op silencioso SEM
evento.

Pipeline por anexo (fail-closed por anexo — anexo é suplementar, o caso nunca
é bloqueado): ``pending``/``processing`` → status ``processing`` → extração
híbrida (``extraction.py``) → persistência de ``extracted_text``/
``extraction_method`` (o status PERMANECE ``processing`` — a verificação do
slice 003 fecha como ``processed``). **Evento de auditoria
``CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED`` (ator system, payload
filename+método) é gravado ANTES de qualquer envio externo** (hook injetado na
extração, que só o dispara no caminho vision). Falha em qualquer etapa do anexo
→ ``failed`` + ``failed_reason`` + ``CASE_ATTACHMENT_FAILED``, sem afetar os
demais anexos nem o caso.

``enqueue_case_attachments`` é o serviço de enqueue do signal (R4): executa a
task sincronamente fora de inline; senão enfileira via django-q2 com
``q_options={"cluster": "attachments"}`` (produção/compose com o serviço
worker-attachments).
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django_q.tasks import async_task

from apps.attachments.extraction import extract_attachment_text
from apps.attachments.models import AttachmentStatus, CaseAttachment, ExtractionMethod
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent

logger = logging.getLogger(__name__)

# Cluster alternativo do django-q2 dedicado aos anexos (D3).
ATTACHMENTS_CLUSTER = "attachments"
SYSTEM_ROLE = "system"


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

    attachment_pks = list(case.attachments.values_list("pk", flat=True))
    for attachment_pk in attachment_pks:
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
    """Processa UM anexo (idempotente por etapa; no-op em corrida).

    Re-leitura da row: removida → no-op silencioso SEM evento (o ack×worker da
    ciência do NIR nunca gera ruído na trilha). ``pending``/``processing`` →
    extração híbrida com hook de auditoria antes do OCR externo → persistência
    de ``extracted_text``/``extraction_method`` (status permanece
    ``processing``; slice 003 fecha como ``processed``). Exceção → ``failed``
    com motivo + ``CASE_ATTACHMENT_FAILED``.
    """
    try:
        attachment = CaseAttachment.objects.select_related("case").get(
            pk=attachment_pk, case_id=case_id
        )
    except CaseAttachment.DoesNotExist:
        logger.info(
            "process_case_attachments: anexo %s removido antes do processamento — no-op",
            attachment_pk,
        )
        return

    if attachment.status in (AttachmentStatus.PROCESSED, AttachmentStatus.FAILED):
        return
    if attachment.extracted_text or attachment.extraction_method:
        # Idempotência por etapa: retry nunca re-extraí nem re-envia ao OCR
        # externo (nem duplica o evento de auditoria).
        return

    attachment.status = AttachmentStatus.PROCESSING
    attachment.save(update_fields=["status"])

    def _dispatch_event() -> None:
        _record_event(
            attachment,
            event_type=CaseEventType.CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED,
            payload={
                "filename": attachment.original_filename,
                "method": ExtractionMethod.VISION,
            },
        )

    try:
        text, method = extract_attachment_text(attachment, on_external_dispatch=_dispatch_event)
    except Exception as exc:
        _fail_attachment(case_id, attachment_pk, exc)
        return

    # Re-leitura da row antes da gravação: removida no meio (ciência do NIR) →
    # no-op silencioso, sem tentar gravar em row inexistente.
    try:
        current = CaseAttachment.objects.get(pk=attachment_pk, case_id=case_id)
    except CaseAttachment.DoesNotExist:
        logger.info(
            "process_case_attachments: anexo %s removido durante a extração — no-op",
            attachment_pk,
        )
        return

    current.extracted_text = text
    current.extraction_method = method
    current.save(update_fields=["extracted_text", "extraction_method"])


def _fail_attachment(case_id: uuid.UUID, attachment_pk: int, exc: Exception) -> None:
    """Marca o anexo como ``failed`` com motivo + evento (fail-closed por anexo).

    Re-lê a row primeiro: removida pela ciência do NIR → no-op sem evento.
    """
    try:
        attachment = CaseAttachment.objects.select_related("case").get(
            pk=attachment_pk, case_id=case_id
        )
    except CaseAttachment.DoesNotExist:
        logger.info(
            "process_case_attachments: anexo %s removido antes do fail — no-op",
            attachment_pk,
        )
        return
    reason = f"falha na extração do anexo: {exc}"
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
