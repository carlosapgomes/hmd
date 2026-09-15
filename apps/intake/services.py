"""Serviços do intake do NIR — lote, caso único e reenvios (R1–R4).

Semântica do change intake-batch-semantics (rev. 2, molde ats-web): cada PDF é
o relatório de um paciente e vira **um** ``Case`` (nunca N documentos por
caso) e o tipo de procedimento é único por envio/lote. As TRÊS entradas de
criação compartilham a primitiva de caso único (``create_case_with_documents``):
o envio em lote (``submit_report_batch``) cria N casos independentes com falhas
parciais (cada arquivo é uma transação; erro por arquivo preserva os casos já
criados), o reenvio de documentos do gate (``resubmit_case_documents``) exige
exatamente 1 PDF e o reenvio corrigido (``create_corrected_resubmission``) cria
1 caso novo vinculado com 1 PDF + 1 tipo + anexos.

Referência de padrão: ats-web ``apps/intake/services.py``
(``validate_single_file``/``validate_batch`` + ``process_uploaded_files``).
Divergências deliberadas do HMD: a validação é **PDF-only** própria
(content-type ``application/pdf`` + extensão + tamanho — o
``validate_attachment_file`` do ats-web aceita JPEG/PNG para anexos) e a
criação dispara o processamento FORA da transação (slice 003, D7: inline em
dev/teste ou enqueue no cluster pdf do django-q2).

Contrato de falha (D1): a validação de arquivo/tipo roda ANTES de persistir na
primitiva de caso único e, no lote, cada arquivo vira erro nomeado preservando
os casos já criados — inclusive exceção inesperada de persistência (storage/
DB), sem 500 silencioso. Na primitiva, uma transação única cria ``Case(NEW)`` +
1 ``CaseDocument`` + anexos opcionais + declaração do tipo
(``set_declared_procedures`` + evento). Como o rollback do banco não reverte o
filesystem, exceção após gravação de arquivos dispara limpeza compensatória
best-effort (unlink) — design D7.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from apps.attachments.models import CaseAttachment
from apps.attachments.services import AttachmentValidationError, validate_attachments
from apps.cases.events import CaseEventType
from apps.cases.locks import CaseLockConflictError
from apps.cases.models import (
    PROCEDURE_DIVERGENCE_REASON,
    ActorType,
    Case,
    CaseDocument,
    CaseEvent,
    CaseStatus,
)
from apps.cases.procedure_catalog import get_procedure_profile
from apps.cases.procedures import set_declared_procedures
from apps.intake.tasks import enqueue_case_processing

if TYPE_CHECKING:
    from apps.accounts.models import User

logger = logging.getLogger(__name__)

# Único content-type aceito para documentos do relatório (R3/D1).
PDF_CONTENT_TYPE = "application/pdf"

# Mensagem única do bloqueio fail-closed do intake (D7): usada pelo guard de
# serviço (ValueError nomeado) e pelo flash das views de POST.
INTAKE_DISABLED_MESSAGE = "O envio de relatórios está desabilitado neste ambiente."


class IntakeValidationError(ValueError):
    """Upload rejeitado SEM efeito: lote inválido ou intake desabilitado —
    nada foi persistido (R3; guard do slice 002 do pilot-deployment)."""


def _assert_intake_enabled() -> None:
    """Fail-closed: bloqueia o intake quando ``INTAKE_ENABLED`` é falso (D7).

    Guard no TOPO das três funções de criação (nova, reenvio corrigido e
    reprocessamento do gate), ANTES de qualquer validação, escrita de banco ou
    gravação de arquivo — cobre qualquer caller, não só as rotas HTTP
    (``ValueError`` nomeado: ``INTAKE_DISABLED_MESSAGE``). O default é true em
    dev/teste e false em produção (fase 1 do piloto).
    """
    if not settings.INTAKE_ENABLED:
        raise IntakeValidationError(INTAKE_DISABLED_MESSAGE)


class CaseNotRetainedError(Exception):
    """Ação de revisão do gate sobre caso fora da retenção — sem efeito (R3/D6).

    Pré-condição das ações do gate (liberar/reenviar): caso do criador (escopo
    da view) em ``PDF_EXTRACTING`` com ``manual_review_required=True``. Entre o
    GET do detalhe e o POST o caso pode ter saído da retenção (concorrência
    com outra ação do gate ou com o worker) — a divergência vira esta exceção e
    a view responde 400 sem efeito colateral.
    """


def _validate_document_file(uploaded_file: UploadedFile[Any]) -> None:
    """Valida um único arquivo do lote (PDF-only), nomeando o arquivo no erro."""
    file_name = uploaded_file.name or ""
    file_size = uploaded_file.size or 0
    content_type = (uploaded_file.content_type or "").lower()

    if content_type != PDF_CONTENT_TYPE:
        raise IntakeValidationError(
            f'"{file_name}" não é um PDF (tipo de conteúdo: {content_type or "não informado"}).'
        )
    if not file_name.lower().endswith(".pdf"):
        raise IntakeValidationError(f'"{file_name}" não tem extensão .pdf.')

    max_bytes = settings.INTAKE_MAX_UPLOAD_BYTES_PER_FILE
    if file_size > max_bytes:
        raise IntakeValidationError(
            f'"{file_name}" excede o limite de {max_bytes // (1024 * 1024)} MB '
            f"por arquivo ({file_size / (1024 * 1024):.1f} MB)."
        )


def _validate_batch(uploaded_files: list[UploadedFile[Any]]) -> None:
    """Valida os limites do LOTE (não-vazio, contagem e tamanho total).

    Validação de nível de lote (design D1, passo 3): roda uma vez por envio,
    ANTES do loop por arquivo. O tamanho/tipo de cada arquivo é validado no
    loop (``_validate_document_file``), para virar erro por arquivo.
    """
    if not uploaded_files:
        raise IntakeValidationError("Envie ao menos um arquivo PDF do relatório.")
    max_files = settings.INTAKE_MAX_FILES_PER_BATCH
    if len(uploaded_files) > max_files:
        raise IntakeValidationError(
            f"Máximo de {max_files} arquivos PDF por lote. Recebidos: {len(uploaded_files)}."
        )
    total_bytes = sum(uploaded_file.size or 0 for uploaded_file in uploaded_files)
    max_batch_bytes = settings.INTAKE_MAX_UPLOAD_BYTES_PER_BATCH
    if total_bytes > max_batch_bytes:
        raise IntakeValidationError(
            f"Tamanho total do lote ({total_bytes / (1024 * 1024):.1f} MB) "
            f"excede o limite de {max_batch_bytes // (1024 * 1024)} MB."
        )


def _validate_single_document(uploaded_files: list[UploadedFile[Any]]) -> None:
    """Exige EXATAMENTE 1 PDF (reenvio de documentos do gate — R3/D2).

    0 ou >1 arquivo → erro nomeado, nada alterado; com 1 arquivo valida o
    PDF/tamanho pela mesma checagem do lote (``_validate_document_file``).
    """
    if len(uploaded_files) != 1:
        raise IntakeValidationError(
            f"O reenvio aceita exatamente 1 PDF do relatório. Recebidos: {len(uploaded_files)}."
        )
    _validate_document_file(uploaded_files[0])


def _validate_declared_type(procedure_type: str) -> None:
    """Valida o tipo ÚNICO declarado do envio: presente e no catálogo (R2).

    Tipo ausente → erro nomeado; fora do catálogo →
    ``get_procedure_profile`` levanta ``KeyError`` nomeando o tipo.
    """
    if not procedure_type:
        raise IntakeValidationError("Informe um único tipo de procedimento do relatório.")
    try:
        get_procedure_profile(procedure_type)
    except KeyError as exc:
        raise IntakeValidationError(str(exc)) from None


def _delete_saved_files_best_effort(saved_names: Iterable[str]) -> None:
    """Limpeza compensatória dos arquivos físicos já gravados (best-effort, D7)."""
    for name in saved_names:
        try:
            default_storage.delete(name)
        except OSError:
            logger.warning("falha ao remover arquivo compensatório: %s", name, exc_info=True)


def submit_report_batch(
    *,
    user: User,
    role: str | None,
    files: Iterable[UploadedFile[Any]],
    procedure_type: str,
    attachments: Sequence[UploadedFile[Any]] = (),
) -> tuple[list[Case], list[str]]:
    """Envia 1–N PDFs (um relatório por PDF) declarando um tipo único (R1/D1).

    Molde ats-web (design D1), na ordem exata:

    1. Guard ``INTAKE_ENABLED`` (fail-closed, topo);
    2. Tipo único do lote (ausente/fora do catálogo → ``([], [erro])``);
    3. Limites do lote (vazio/contagem/tamanho total → ``([], [erro])``);
    4. Anexos (se houver): ``validate_attachments(.., pdf_count=len(files))`` —
       com >1 PDF o erro é NÃO-bloqueante (casos criados sem anexos); com
       exatamente 1 PDF anexo inválido ABORTA o envio inteiro;
    5. Loop por arquivo: arquivo inválido vira erro nomeado e os demais seguem;
       arquivo válido cria um caso próprio (transação independente);
    6. Fim: com exatamente 1 PDF e anexos válidos, os anexos vão para o caso.

    Falha de persistência num caso do lote é capturada no loop (D1) e vira erro
    por arquivo, preservando os casos já criados — sem 500 silencioso.

    Returns:
        ``(cases, errors)``: os casos criados (na ordem dos arquivos válidos) e
        as mensagens de erro (nomeando o arquivo ou o limite violado).
    """
    _assert_intake_enabled()
    uploaded_files = list(files)
    attachment_files = list(attachments)

    # 2. Tipo único do lote — sem tipo válido, nenhum caso é criado.
    try:
        _validate_declared_type(procedure_type)
    except IntakeValidationError as exc:
        return [], [str(exc)]

    # 3. Limites de lote (vazio/contagem/tamanho total).
    try:
        _validate_batch(uploaded_files)
    except IntakeValidationError as exc:
        return [], [str(exc)]

    # 4. Anexos: só com exatamente 1 PDF; multi-PDF registra erro não-bloqueante.
    attachment_error: str | None = None
    if attachment_files:
        try:
            validate_attachments(attachment_files, pdf_count=len(uploaded_files))
        except AttachmentValidationError as exc:
            attachment_error = str(exc)
            if len(uploaded_files) == 1:
                return [], [str(exc)]

    carries_attachments = bool(attachment_files) and attachment_error is None

    # 5. Loop por arquivo: cada caso é uma transação independente (parcial).
    cases: list[Case] = []
    errors: list[str] = []
    for uploaded_file in uploaded_files:
        try:
            case = create_case_with_documents(
                user=user,
                role=role,
                file=uploaded_file,
                procedure_type=procedure_type,
                attachments=attachment_files if carries_attachments else (),
            )
        except IntakeValidationError as exc:
            errors.append(str(exc))
        except Exception:  # noqa: BLE001 — erro por arquivo (storage/DB), sem 500
            logger.exception("intake_batch_case_failed file=%s", uploaded_file.name or "")
            errors.append(f'"{uploaded_file.name or ""}" não pôde ser processado (erro interno).')
        else:
            cases.append(case)

    # 6. Erro multi-PDF de anexos entra ao fim, após os casos criados.
    if attachment_error is not None:
        errors.append(attachment_error)

    return cases, errors


def create_case_with_documents(
    *,
    user: User,
    role: str | None,
    file: UploadedFile[Any],
    procedure_type: str,
    attachments: Sequence[UploadedFile[Any]] = (),
    corrects_case: Case | None = None,
    correction_reason: str = "",
    correction_created_by: User | None = None,
) -> Case:
    """Cria atomicamente um caso em NEW com 1 documento e 1 tipo (R2, primitiva).

    Primitiva de **caso único** compartilhada pelas três entradas (D2): valida
    TUDO antes de persistir (o PDF via ``_validate_document_file``, o tipo único
    via ``_validate_declared_type`` e — quando houver — os anexos via
    ``validate_attachments(.., pdf_count=1)``, fonte única nova de
    apps/attachments). No sucesso, numa transação única: ``Case(NEW,
    created_by=user)``, a row ``CaseDocument`` (position 1, arquivo gravado no
    storage), as rows ``CaseAttachment`` dos anexos (arquivos gravados antes do
    INSERT no path seguro por UUID, com ``uploaded_by=user`` e
    ``status=pending``) e a declaração via ``set_declared_procedures`` (com
    evento na trilha). Fora da transação, o processamento é disparado via
    ``enqueue_case_processing`` (inline em dev/teste ou enqueue no cluster pdf —
    D7, slice 003). Em exceção após gravações físicas, remove best-effort os
    arquivos já escritos (o rollback do banco não reverte o filesystem).

    Os kwargs aditivos ``attachments`` (design D2) e ``corrects_case``/
    ``correction_reason``/``correction_created_by`` (design D4) materializam,
    respectivamente, os anexos de evidência do caso e o vínculo de reenvio
    corrigido — os defaults ``()``/``None``/``""`` preservam o comportamento do
    change 04 (criação pura — regressão coberta por teste).
    """
    _assert_intake_enabled()
    attachment_files = list(attachments)
    _validate_document_file(file)
    _validate_declared_type(procedure_type)
    # Anexo inválido rejeita TUDO antes de qualquer gravação (nem caso, nem
    # documentos) — validação pura, sem efeito (R2/D2).
    validate_attachments(attachment_files, pdf_count=1)

    saved_file_names: list[str] = []
    try:
        with transaction.atomic():
            case = Case.objects.create(
                created_by=user,
                corrects_case=corrects_case,
                correction_reason=correction_reason,
                correction_created_by=correction_created_by,
            )
            document = CaseDocument(
                case=case,
                position=1,
                original_filename=file.name or "",
                content_type=(file.content_type or "").lower(),
                size_bytes=file.size or 0,
                uploaded_by=user,
            )
            # Grava o arquivo antes do INSERT para conhecer o nome no storage e
            # poder compensar (unlink) se algo falhar depois.
            document.file.save(document.original_filename, file, save=False)
            stored_name = document.file.name
            if stored_name:
                saved_file_names.append(stored_name)
            document.save()
            for uploaded_attachment in attachment_files:
                attachment = CaseAttachment(
                    case=case,
                    original_filename=uploaded_attachment.name or "",
                    content_type=(uploaded_attachment.content_type or "").lower(),
                    size_bytes=uploaded_attachment.size or 0,
                    uploaded_by=user,
                )
                # Arquivo gravado antes do INSERT (mesmo padrão dos docs): o
                # upload_to gera o UUID no callable e a limpeza compensatória
                # abaixo cobre os arquivos já escritos se algo falhar depois.
                attachment.file.save(
                    uploaded_attachment.name or "", uploaded_attachment, save=False
                )
                stored_name = attachment.file.name
                if stored_name:
                    saved_file_names.append(stored_name)
                attachment.save()
            set_declared_procedures(case, (procedure_type,), user=user, role=role)
    except BaseException:
        _delete_saved_files_best_effort(saved_file_names)
        raise
    # Fora da transação (D7): enfileira no cluster pdf ou executa inline.
    enqueue_case_processing(case)
    return case


# ── Ações de revisão do gate NIR (slice 005, design D6/D7) ─────────────────
# ``release_retained_case``/``resubmit_case_documents`` implementam as duas
# ações de revisão do gate (liberar/reenviar), restritas a caso retido do
# próprio criador. A liberação (R7, change llm-pipeline-per-type slice 004)
# DESPACHA pela retenção: formato (``PDF_EXTRACTING``) → ``complete_pdf_``
# ``extraction`` (comportamento atual) e divergência de procedimentos
# (``LLM_EXTRACTING`` + ``procedure_divergence``) →
# ``bypass_pipeline_divergence``. O reenvio continua exclusivo da retenção de
# formato. Ambas rodam em transação com ``select_for_update`` no ``Case`` e
# re-check DENTRO da transação das pré-condições, na ordem do escopo da view:
# 1) ownership (criador — caso alheio vira ``Http404``/not-found sem vazar
#    informação, mesmo status do filtro da view por criador); 2) retenção
#    (``CaseNotRetainedError`` → 400); 3) lock ativo não-expirado
#    (``CaseLockConflictError`` — D6). Retenção/ownership precedem o lock
#    ativo: caso não-retido responde 400 mesmo sob lease ativa (R3), e
#    nunca se revela estado/lock a quem não é o criador.
# Divergências marcadas vs ats-web ``scope_gate_bypass``: aqui o bypass usa
# transições existentes (nenhum estado novo) e o reenvio substitui os
# ``CaseDocument`` zerando flag/texto/nº antes de reenfileirar (D6/D7).


# Campos zerados pelo reenvio (R2): texto/nº extraídos, metadados do
# cabeçalho SESAB (changes sesab-header-extraction e painel-ats-parity, slices
# 001 — derivados do documento corrente, unidade de origem inclusa) e
# flag/motivo do gate.
_RESUBMIT_CLEARED_FIELDS = (
    "extracted_text",
    "agency_record_number",
    "agency_record_extracted_at",
    "patient_age",
    "patient_gender",
    "patient_race",
    "days_on_screen",
    "origin_unit",
    "manual_review_required",
    "manual_review_reason",
)


def _assert_owned_by(case: Case, user: User | None) -> None:
    """Re-check DENTRO da transação: caso é do criador (escopo por criador).

    O escopo por criador não vive só na view: sob o row lock, usuário que
    não é o criador (ou ``None``) → ``Http404`` (mesmo status/not-found do
    filtro da view), sem mensagem nem vazamento de estado — quem não é o
    criador não descobre sequer se o caso existe. Roda ANTES das checagens
    de retenção e de lock ativo (mesma precedência do escopo da view).
    """
    if user is None or case.created_by_id != user.pk:
        raise Http404


def _assert_no_active_lock(case: Case) -> None:
    """Conflita com lock ativo não-expirado (D6) — sem efeito.

    A semântica espelha ``apps/cases/locks.py``: ``locked_until`` no futuro =
    lock ativo de outro ator (ex.: worker reprocessando o caso) →
    ``CaseLockConflictError``; lease expirada/vazia → a ação prossegue
    (assumível, como no claim).
    """
    if case.locked_until is not None and case.locked_until > timezone.now():
        owner = case.locked_by.display_name if case.locked_by is not None else "sistema"
        context = case.lock_context or "(sem contexto)"
        raise CaseLockConflictError(
            f"caso {case.case_id} está sob lock ativo de {owner} "
            f"(contexto {context!r}) — aguarde o processamento terminar e tente novamente."
        )


def _assert_format_retained(case: Case) -> None:
    """Re-check DENTRO da transação: caso retido por FORMATO (R7).

    ``PDF_EXTRACTING`` + flag — a retenção do gate de regulação (slice 003).
    O reenvio de documentos é exclusivo desta retenção. Estado divergente do
    GET do detalhe (concorrência) → ``CaseNotRetainedError`` sem efeito.
    """
    if case.status != CaseStatus.PDF_EXTRACTING or not case.manual_review_required:
        raise CaseNotRetainedError(
            "Este caso não está retido para revisão do gate — recarregue a página."
        )


def _assert_releasable(case: Case) -> None:
    """Re-check DENTRO da transação: caso liberável pelo gate (R7).

    Liberação despacha pelas duas retenções suportadas: formato
    (``PDF_EXTRACTING`` + flag) ou divergência de procedimentos
    (``LLM_EXTRACTING`` + flag + ``manual_review_reason=procedure_divergence``).
    Qualquer outro estado/motivo → ``CaseNotRetainedError`` (400 sem efeito).
    """
    if case.status == CaseStatus.PDF_EXTRACTING and case.manual_review_required:
        return
    if (
        case.status == CaseStatus.LLM_EXTRACTING
        and case.manual_review_required
        and case.manual_review_reason == PROCEDURE_DIVERGENCE_REASON
    ):
        return
    raise CaseNotRetainedError(
        "Este caso não está retido para revisão do gate — recarregue a página."
    )


def release_retained_case(
    *,
    case: Case,
    user: User,
    role: str | None,
) -> CaseStatus:
    """Libera caso retido: despacho pela (estado, razão) da retenção (R7).

    ``select_for_update`` no ``Case`` + re-check DENTRO da transação das
    pré-condições na ordem do escopo: criador (not-found sem vazar
    informação), liberável e sem lock ativo. Efeito na mesma transação:

    - ``PDF_EXTRACTING`` (retenção de FORMATO) → ``complete_pdf_extraction``
      (→ ANONYMIZING), flags zeradas e ``CASE_GATE_BYPASSED`` com o motivo
      original — comportamento atual, intacto;
    - ``LLM_EXTRACTING`` + ``procedure_divergence`` →
      ``bypass_pipeline_divergence`` (→ LLM_SUMMARIZING, eventos de transição
      + ``CASE_GATE_BYPASSED`` com ``reason=procedure_divergence``), flags
      zeradas — o conjunto declarado é preservado (design D5).

    Devolve o estado-alvo alcançado (a view diferencia a mensagem de sucesso).

    Raises:
        Http404: caso não é do criador (escopo por criador, sem vazar informação).
        CaseNotRetainedError: caso fora das duas retenções (sem efeito).
        CaseLockConflictError: lock ativo não-expirado de outro ator (sem efeito).
    """
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        _assert_owned_by(locked, user)
        _assert_releasable(locked)
        _assert_no_active_lock(locked)
        if locked.status == CaseStatus.PDF_EXTRACTING:
            retention_reason = locked.manual_review_reason
            locked.complete_pdf_extraction(user=user, role=role)
            locked.manual_review_required = False
            locked.manual_review_reason = ""
            locked.save(update_fields=["manual_review_required", "manual_review_reason"])
            CaseEvent.objects.create(
                case_id=locked.case_id,
                event_type=CaseEventType.CASE_GATE_BYPASSED,
                actor_type=ActorType.USER if user is not None else ActorType.SYSTEM,
                actor=user,
                actor_role=role or "",
                payload={"reason": retention_reason},
            )
            return CaseStatus.ANONYMIZING

        # LLM_EXTRACTING + procedure_divergence: bypass auditado (R6/R7). A
        # transição grava os dois eventos; aqui só zeramos as flags (mesmo
        # atomic — a transição não conhece a retenção).
        locked.bypass_pipeline_divergence(user=user, role=role)
        locked.manual_review_required = False
        locked.manual_review_reason = ""
        locked.save(update_fields=["manual_review_required", "manual_review_reason"])
        return CaseStatus.LLM_SUMMARIZING


def resubmit_case_documents(
    *,
    case: Case,
    user: User,
    role: str | None,
    files: Iterable[UploadedFile[Any]],
) -> None:
    """Reenvia os documentos de um caso retido e reenfileira o processamento (R3).

    Substitui os documentos do caso retido por **exatamente 1** PDF novo
    (design D2/P0-1): 0 ou >1 arquivo → erro nomeado sem efeito (validação
    ANTES da transação, fonte única ``_validate_single_document``). Na
    transação, com ``select_for_update`` + re-check das pré-condições na
    ordem do escopo (criador → retido → sem lock ativo): remove os
    ``CaseDocument`` antigos, grava o novo (position 1) e zera
    ``extracted_text``/``agency_record_number``/``agency_record_extracted_at``/
    ``manual_review_*``. Exceção após gravação de arquivos físicos dispara a
    limpeza compensatória best-effort dos caminhos novos (D7); os arquivos
    antigos só são removidos após todas as escritas do banco terem sucesso (o
    rollback não reverte o filesystem). Fora da transação, reenfileira o
    processamento (inline em dev/teste ou enqueue no cluster pdf — slice 003).

    Raises:
        IntakeValidationError: não é exatamente 1 PDF válido (nada muda).
        Http404: caso não é do criador (escopo por criador, sem vazar informação).
        CaseNotRetainedError: caso fora da retenção (sem efeito).
        CaseLockConflictError: lock ativo não-expirado de outro ator (sem efeito).
    """
    _assert_intake_enabled()
    uploaded_files = list(files)
    _validate_single_document(uploaded_files)

    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        _assert_owned_by(locked, user)
        _assert_format_retained(locked)
        _assert_no_active_lock(locked)
        old_documents = list(locked.documents.all())
        old_file_names = [document.file.name for document in old_documents if document.file.name]
        locked.documents.all().delete()

        saved_file_names: list[str] = []
        try:
            for position, uploaded_file in enumerate(uploaded_files, start=1):
                document = CaseDocument(
                    case=locked,
                    position=position,
                    original_filename=uploaded_file.name or "",
                    content_type=(uploaded_file.content_type or "").lower(),
                    size_bytes=uploaded_file.size or 0,
                    uploaded_by=user,
                )
                # Grava o arquivo antes do INSERT para conhecer o nome no
                # storage e poder compensar (unlink) se algo falhar depois.
                document.file.save(document.original_filename, uploaded_file, save=False)
                stored_name = document.file.name
                if stored_name:
                    saved_file_names.append(stored_name)
                document.save()
            locked.extracted_text = ""
            locked.agency_record_number = ""
            locked.agency_record_extracted_at = None
            locked.patient_age = None
            locked.patient_gender = ""
            locked.patient_race = ""
            locked.days_on_screen = None
            locked.origin_unit = ""
            locked.manual_review_required = False
            locked.manual_review_reason = ""
            locked.save(update_fields=_RESUBMIT_CLEARED_FIELDS)
        except BaseException:
            _delete_saved_files_best_effort(saved_file_names)
            raise
        # Remoção física dos documentos antigos apenas após as escritas de
        # banco terem sucesso: rollback da transação restauraria as rows, e os
        # arquivos já removidos não voltariam (limitação documentada do D7).
        _delete_saved_files_best_effort(old_file_names)
    # Fora da transação (D7): reprocessa inline ou enfileira no cluster pdf.
    enqueue_case_processing(locked)


# ── Reenvio corrigido de caso encerrado (nir-result-closure, D4) ──────────


def create_corrected_resubmission(
    *,
    original_case: Case,
    user: User,
    role: str | None,
    file: UploadedFile[Any],
    procedure_type: str,
    correction_reason: str,
    attachments: Sequence[UploadedFile[Any]] = (),
) -> Case:
    """Cria um NOVO caso vinculado a um caso encerrado do criador (R2/D4).

    Reenvio corrigido (semântica ats-web, design D4/D5): de um caso ``CLEANED``
    do próprio criador, o NIR declara **exatamente 1 PDF**, um tipo único
    EXPLÍCITO (nunca herdado — R3) e um motivo obrigatório; no MESMO ``atomic``
    nasce um novo caso em ``NEW`` (pipeline completo) vinculado por
    ``corrects_case`` com ``correction_reason``/``correction_created_by``,
    o original ganha ``CASE_MARKED_SUPERSEDED`` (payload com o id do novo) e o
    novo ganha ``CASE_CORRECTION_CREATED`` (payload com id do original +
    motivo). O original NÃO muda de status nem de dados.

    Validações ANTES de qualquer criação, na ordem do escopo por criador:
    motivo não-vazio (strip), criador (``_assert_owned_by`` → ``Http404`` sem
    vazar informação), estado ``CLEANED``, o PDF único e o tipo único (fontes
    únicas do caso único — ``_validate_document_file``/``_validate_declared_type``).
    Anexos são um kwarg ADITIVO (design D2) repassado a
    ``create_case_with_documents``, que os valida (fonte única) antes de criar
    qualquer coisa — anexo inválido rejeita o reenvio inteiro sem efeito. O
    enqueue do worker pdf do novo caso já acontece dentro do
    ``create_case_with_documents`` (D7); o enqueue em cascata no atomic externo
    é inofensivo em prod (async pós-commit pelo broker) e, em teste com
    ``INTAKE_RUN_TASKS_INLINE=False``, é assertado — o comportamento inline é
    desvio conhecido (design D4).

    Raises:
        IntakeValidationError: motivo/estado/PDF/tipo inválidos (nada muda).
        AttachmentValidationError: anexo inválido (nada muda).
        Http404: caso não é do criador (escopo por criador, sem vazar informação).
    """
    _assert_intake_enabled()
    reason = (correction_reason or "").strip()
    if not reason:
        raise IntakeValidationError("Informe o motivo do reenvio corrigido.")
    _assert_owned_by(original_case, user)
    if original_case.status != CaseStatus.CLEANED:
        raise IntakeValidationError(
            "reenvio corrigido disponível apenas para casos encerrados (CLEANED) — "
            f"estado atual {original_case.status!r}"
        )
    _validate_document_file(file)
    _validate_declared_type(procedure_type)

    with transaction.atomic():
        new_case = create_case_with_documents(
            user=user,
            role=role,
            file=file,
            procedure_type=procedure_type,
            attachments=attachments,
            corrects_case=original_case,
            correction_reason=reason,
            correction_created_by=user,
        )
        actor_type = ActorType.USER if user is not None else ActorType.SYSTEM
        actor_role = role or ""
        CaseEvent.objects.create(
            case=original_case,
            event_type=CaseEventType.CASE_MARKED_SUPERSEDED,
            actor_type=actor_type,
            actor=user,
            actor_role=actor_role,
            payload={"corrected_case_id": str(new_case.case_id)},
        )
        CaseEvent.objects.create(
            case=new_case,
            event_type=CaseEventType.CASE_CORRECTION_CREATED,
            actor_type=actor_type,
            actor=user,
            actor_role=actor_role,
            payload={
                "original_case_id": str(original_case.case_id),
                "correction_reason": reason,
            },
        )
    return new_case
