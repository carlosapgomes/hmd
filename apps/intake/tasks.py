"""django-q2 tasks do processamento de PDF do intake (slice 003, design D2/D4/D5).

Entry point ``process_case_documents`` roda no cluster ``pdf`` (ou inline quando
``INTAKE_RUN_TASKS_INLINE`` é True — dev/teste) e é **idempotente por estado**:
branch por estado do caso — ``NEW`` → ``start_pdf_extraction`` e processa;
``PDF_EXTRACTING`` (caso retido pelo gate/reenviado) → PULA o start (a
transição exige source ``NEW``) e processa; demais estados → no-op com log
(reexecução do q2 nunca duplica eventos). A operação roda sob lock do caso
(``context="worker_pdf"``, ``role="system"``); a lease é liberada no MESMO
atomic da fase final (ver coordenação abaixo) e o ``finally`` da task re-tenta
o release apenas nos caminhos de exceção/no-op; erro de extração →
``fail_processing`` (→ ``FAILED``) com o motivo na trilha; resultado do gate →
conclusão (``ANONYMIZING`` + evento
``CASE_EXTRACTION_COMPLETED``) ou retenção (permanece ``PDF_EXTRACTING`` com
``manual_review_required`` + evento ``CASE_GATE_MANUAL_REVIEW``).

Coordenação com a anonimização (finding P1 do review): a transição de saída
(``complete_pdf_extraction`` — a que emite o evento de entrada em
``ANONYMIZING``) e o ``release_case_lock`` rodam no MESMO
``transaction.atomic()`` (fase final em ``_extract_and_decide``). Assim os
hooks ``on_commit`` dos eventos dessa transição — o enqueue da task de
anonimização — só disparam quando aquele atomic commitar, já com a lease
``worker_pdf`` liberada. Sem essa ordem, em dev single-process (ambas as flags
inline) a anonimização rodaria ainda DENTRO desta task com o lock ativo e o
claim ``worker_anonymization`` conflitaria (caso wrongly FAILED).

``enqueue_case_processing`` é o serviço de criação (D7): enfileira no cluster
``pdf`` quando fora de inline; senão executa a task sincronamente.
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_q.tasks import async_task

from apps.cases.events import CaseEventType
from apps.cases.locks import CaseLock, CaseLockConflictError, claim_case_lock, release_case_lock
from apps.cases.models import ActorType, Case, CaseEvent, CaseStatus
from apps.intake.pdf_utils import (
    extract_agency_record_number,
    extract_document_text,
    extract_header_metadata,
    strip_watermark,
)
from apps.intake.regulation_gate import GateResult, evaluate_regulation_report

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.cases.models import CaseDocument

logger = logging.getLogger(__name__)

# Cluster alternativo do django-q2 dedicado à extração de PDF (D2).
PDF_CLUSTER = "pdf"
# Contexto/papel do worker na concessão do lock de exclusividade do caso (D5).
WORKER_LOCK_CONTEXT = "worker_pdf"
SYSTEM_ROLE = "system"


def process_case_documents(case_id: uuid.UUID, user: User | None = None) -> None:
    """Processa os documentos PDF do caso (entry point do worker/cluster pdf).

    Branch por estado (D5): ``NEW`` → ``start_pdf_extraction`` (→ PDF_EXTRACTING,
    evento) e processa; ``PDF_EXTRACTING`` (retido/reenviado) → pula o start e
    processa; demais estados → log + no-op (idempotência — reexecução não
    duplica eventos). Claim de lock ``context="worker_pdf"``/``role="system"``;
    o release roda na fase final dentro de ``_extract_and_decide`` (mesmo atomic
    da transição de saída — coordenação com a anonimização) e o ``finally``
    re-tenta apenas quando a fase não liberou (exceção/no-op/lease expirada);
    erro de extração → ``fail_processing(reason)`` → ``FAILED``; ``user`` é
    aceito para paridade com as operações auditadas, mas o pipeline atua como
    ator sistema.

    Raises:
        ValueError: caso inexistente.
        CaseLockConflictError: lock ativo de outro ator (propagada — o retry do
            django-q2 reprocessa quando a lease for liberada/expirada).
    """
    try:
        case = Case.objects.get(case_id=case_id)
    except Case.DoesNotExist:
        logger.error("process_case_documents: caso %s não encontrado", case_id)
        raise ValueError(f"caso {case_id} não encontrado") from None

    if case.status not in (CaseStatus.NEW, CaseStatus.PDF_EXTRACTING):
        logger.info(
            "process_case_documents: caso %s já fora de NEW/PDF_EXTRACTING (status=%s) — no-op",
            case_id,
            case.status,
        )
        return

    lock = claim_case_lock(case, user=None, context=WORKER_LOCK_CONTEXT, role=SYSTEM_ROLE)
    released = False
    try:
        # Releitura sob posse da lease: entre a checagem e o claim o caso pode
        # ter sido processado por outra execução — a decisão é sempre fresca.
        case.refresh_from_db()
        if case.status == CaseStatus.NEW:
            case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
        elif case.status != CaseStatus.PDF_EXTRACTING:
            logger.info(
                "process_case_documents: caso %s processado entre o claim e o start (status=%s) — no-op",
                case_id,
                case.status,
            )
            return

        try:
            # Fase final (finding P1): transição de saída + release da lease no
            # MESMO atomic — os hooks on_commit da entrada em ANONYMIZING só
            # disparam depois de a lease worker_pdf ser liberada. Devolve True
            # quando o release rodou dentro do atomic; False quando a lease
            # expirou antes (o finally re-tenta).
            released = _extract_and_decide(case, lock=lock)
        except Exception as exc:
            logger.exception("process_case_documents: extração falhou para o caso %s", case_id)
            # O rollback do atomic final desfez transição/eventos; a releitura
            # garante o estado real (e o fail_processing válido) antes do fail.
            case.refresh_from_db()
            # Gate do slice 002: o erro pode ocorrer DEPOIS do encerramento
            # administrativo (worker com lease expirada) — fail_processing em
            # CLEANED seria TransitionNotAllowed; o passo encerra em silêncio.
            if case.status == CaseStatus.CLEANED:
                logger.info(
                    "process_case_documents: caso %s encerrado administrativamente durante "
                    "a extração — sem fail_processing",
                    case_id,
                )
                return
            case.fail_processing(reason=_failure_reason(exc), user=None, role=SYSTEM_ROLE)
    finally:
        if not released:
            try:
                release_case_lock(case, lock.token)
            except CaseLockConflictError:
                # Lease expirada durante o processamento: o lock se perdeu, mas o
                # resultado do processamento não deve ser mascarado pelo release.
                logger.warning(
                    "process_case_documents: lease do caso %s expirou antes do release",
                    case_id,
                    exc_info=True,
                )


def enqueue_case_processing(case: Case) -> None:
    """Enfileira o processamento do caso no cluster pdf (ou inline, dev/teste).

    Com ``INTAKE_RUN_TASKS_INLINE=True`` executa a task sincronamente (a criação
    já chega processada — UX imediata e testes determinísticos); senão enfileira
    via django-q2 com ``q_options={"cluster": "pdf"}`` (D2). Chamado pelo
    serviço de criação APÓS a transação (D7).
    """
    if getattr(settings, "INTAKE_RUN_TASKS_INLINE", True):
        process_case_documents(case.case_id)
        return
    async_task(
        "apps.intake.tasks.process_case_documents",
        case.case_id,
        q_options={"cluster": PDF_CLUSTER, "task_name": f"pdf:{case.case_id}"},
    )


# ── Helpers internos ────────────────────────────────────────────────────────


def _extract_document_text(document: CaseDocument) -> str:
    """Extrai o texto do documento independente do storage (arquivo temporário).

    ``extract_document_text`` (pdf_utils) abre por caminho, mas o storage pode
    ser in-memory (suíte — sem ``.path``): o conteúdo é copiado para um arquivo
    temporário sempre (custo desprezível e funciona para qualquer storage).
    """
    document.file.open("rb")
    try:
        content = document.file.read()
    finally:
        document.file.close()
    descriptor, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        return extract_document_text(path)
    finally:
        os.unlink(path)


def _extract_and_decide(case: Case, *, lock: CaseLock) -> bool:
    """Extrai os documentos na ordem, aplica o gate e decide o destino do caso.

    Pré-condição: caso sob lock do worker em ``PDF_EXTRACTING`` (o start do NEW
    já aconteceu no chamador). Nº de ocorrência vem do TEXTO BRUTO concatenado
    ANTES do ``strip_watermark`` (ordem obrigatória, D3); o texto armazenado é o
    limpo. Gate ok → ``complete_pdf_extraction`` (→ ANONYMIZING) + evento
    ``CASE_EXTRACTION_COMPLETED``; falha do gate → retenção em ``PDF_EXTRACTING``
    com ``manual_review_required``/``manual_review_reason`` + evento
    ``CASE_GATE_MANUAL_REVIEW``. Os metadados do cabeçalho SESAB (idade/sexo/
    raça/dias em tela — change sesab-header-extraction, D3/D4) são extraídos do
    texto LIMPO e gravados no MESMO write do ``extracted_text``; nenhum evento
    carrega seus valores. Exceção de extração propaga ao chamador (que
    converte em ``fail_processing``).

    O ``release_case_lock`` roda DENTRO do mesmo atomic da transição de saída
    (finding P1): os hooks ``on_commit`` registrados pelos eventos desta fase —
    o enqueue da anonimização na entrada em ANONYMIZING — só disparam quando
    este atomic commitar, com a lease ``worker_pdf`` já liberada (sem isso, o
    modo inline single-process conflitaria no claim ``worker_anonymization``).

    Returns:
        ``True`` quando a lease do worker foi liberada neste atomic; ``False``
        quando o release conflitou (lease expirada durante o processamento — o
        finally da task re-tenta o release mantendo o resultado da fase).
    """
    raw_chunks: list[str] = []
    for document in case.documents.all():
        raw_chunks.append(_extract_document_text(document))
    raw_text = "\n".join(chunk for chunk in raw_chunks if chunk)

    # Ordem obrigatória (D3/R5b): o nº é extraído do bruto, antes do strip.
    record_number = extract_agency_record_number(raw_text)
    cleaned_text = strip_watermark(raw_text)
    gate = evaluate_regulation_report(cleaned_text)
    # Metadados do cabeçalho padrão SESAB (sesab-header-extraction, slice 001,
    # D3/D4): extração PURA sobre o texto limpo, fora do atomic; a
    # persistência vai no mesmo write do ``extracted_text`` (writer único).
    header_metadata = extract_header_metadata(cleaned_text)

    with transaction.atomic():
        current = Case.objects.select_for_update().get(pk=case.pk)
        # Gate do slice 002 (painel-lista-encerramento): o passo longo (extração
        # de N PDFs) pode retornar DEPOIS do encerramento administrativo com a
        # lease expirada. Re-lê a linha ANTES de qualquer write — inclusive o
        # evento de retenção ``CASE_GATE_MANUAL_REVIEW``, que commita este
        # atomic via ``return False`` no release — e aborta em CLEANED.
        if current.status == CaseStatus.CLEANED:
            logger.info(
                "process_case_documents: caso %s encerrado administrativamente durante "
                "a extração — abortando sem persistir",
                case.pk,
            )
            return False
        current.extracted_text = cleaned_text
        current.agency_record_number = record_number or ""
        current.agency_record_extracted_at = timezone.now()
        current.patient_age = header_metadata.age
        current.patient_gender = header_metadata.gender or ""
        current.patient_race = header_metadata.race or ""
        current.days_on_screen = header_metadata.days_on_screen
        current.origin_unit = header_metadata.origin_unit or ""
        current.save(
            update_fields=[
                "extracted_text",
                "agency_record_number",
                "agency_record_extracted_at",
                "patient_age",
                "patient_gender",
                "patient_race",
                "days_on_screen",
                "origin_unit",
            ]
        )
        if gate.ok:
            current.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
            _record_system_event(
                current,
                event_type=CaseEventType.CASE_EXTRACTION_COMPLETED,
                payload={
                    "record_number": current.agency_record_number,
                    "text_length": len(current.extracted_text),
                },
            )
        else:
            current.manual_review_required = True
            current.manual_review_reason = _retention_reason(gate)
            current.save(update_fields=["manual_review_required", "manual_review_reason"])
            _record_system_event(
                current,
                event_type=CaseEventType.CASE_GATE_MANUAL_REVIEW,
                payload={
                    "reason_code": gate.reason_code,
                    "reason": current.manual_review_reason,
                    "text_length": gate.text_length,
                },
            )

        # Release da lease DENTRO deste atomic (último passo da fase final): o
        # commit deste bloco é o que dispara os hooks on_commit dos eventos da
        # transição — que passam a rodar só com a lease já liberada. Conflito
        # aqui só ocorre com a própria lease expirada (o row lock deste atomic
        # impede claim alheio): o resultado da fase permanece e o finally da
        # task re-tenta o release (contrato original).
        try:
            release_case_lock(current, lock.token)
        except CaseLockConflictError:
            return False
    return True


def _record_system_event(case: Case, *, event_type: str, payload: dict[str, object]) -> None:
    """Grava um evento não-transicional da trilha com ator sistema (D5)."""
    CaseEvent.objects.create(
        case=case,
        event_type=event_type,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role=SYSTEM_ROLE,
        payload=payload,
    )


def _retention_reason(gate: GateResult) -> str:
    """Motivo da retenção exibido ao NIR (flag ``manual_review_reason``, D4)."""
    return f"documento fora do padrão SESAB do relatório (motivo: {gate.reason_code})"


def _failure_reason(exc: Exception) -> str:
    """Motivo da falha de extração para o payload do ``fail_processing`` (R2)."""
    return f"falha na extração do PDF: {exc}"
