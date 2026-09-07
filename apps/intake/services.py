"""Serviço atômico de criação de caso com upload multi-PDF (slice 001, R3/D7).

Referência de padrão: ats-web ``apps/intake/services.py``
(``validate_single_file``/``validate_batch`` + ``process_uploaded_files``).
Divergências deliberadas do HMD (D1/D7): um upload vira **um** ``Case`` com
1–N ``CaseDocument`` ordenados (o ats-web cria um caso por PDF); a validação é
**PDF-only** própria (content-type ``application/pdf`` + extensão + tamanho) —
o ``validate_attachment_file`` do ats-web NÃO serve (aceita JPEG/PNG para
anexos); a criação dispara o processamento FORA da transação (slice 003,
D7: inline em dev/teste ou enqueue no cluster pdf do django-q2).

Contrato (R3): toda validação roda ANTES de qualquer persistência — falha =
zero efeito no banco e o erro nomeia o arquivo/tipo inválido. No sucesso, uma
transação única cria o ``Case(NEW)``, grava os arquivos físicos dos documentos
e declara os procedimentos (``set_declared_procedures`` + evento na trilha).
Como o rollback do banco não reverte o filesystem, exceção após gravação de
arquivos dispara limpeza compensatória best-effort (unlink) — design D7.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from apps.cases.models import Case, CaseDocument
from apps.cases.procedure_catalog import get_procedure_profile
from apps.cases.procedures import set_declared_procedures
from apps.intake.tasks import enqueue_case_processing

if TYPE_CHECKING:
    from apps.accounts.models import User

logger = logging.getLogger(__name__)

# Único content-type aceito para documentos do relatório (R3/D1).
PDF_CONTENT_TYPE = "application/pdf"


class IntakeValidationError(ValueError):
    """Lote de upload inválido — nada foi persistido (R3)."""


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

    max_bytes = settings.INTAKE_MAX_FILE_MB * 1024 * 1024
    if file_size > max_bytes:
        raise IntakeValidationError(
            f'"{file_name}" excede o limite de {settings.INTAKE_MAX_FILE_MB} MB '
            f"por arquivo ({file_size / (1024 * 1024):.1f} MB)."
        )


def _validate_batch(uploaded_files: list[UploadedFile[Any]]) -> None:
    """Valida contagem (1..INTAKE_MAX_DOCUMENTS) e cada arquivo do lote."""
    if not uploaded_files:
        raise IntakeValidationError("Envie ao menos um arquivo PDF do relatório.")
    max_count = settings.INTAKE_MAX_DOCUMENTS
    if len(uploaded_files) > max_count:
        raise IntakeValidationError(
            f"Máximo de {max_count} arquivos PDF por caso. Recebidos: {len(uploaded_files)}."
        )
    for uploaded_file in uploaded_files:
        _validate_document_file(uploaded_file)


def _validate_declared_types(procedure_types: Iterable[str]) -> None:
    """Valida tipos declarados: ao menos um e todos do catálogo (erro nomeia o tipo)."""
    declared = tuple(procedure_types)
    if not declared:
        raise IntakeValidationError("Declare ao menos um tipo de procedimento do relatório.")
    for procedure_type in declared:
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


def create_case_with_documents(
    *,
    user: User,
    role: str | None,
    files: Iterable[UploadedFile[Any]],
    procedure_types: Iterable[str],
) -> Case:
    """Cria atomicamente um caso em NEW com N documentos e tipos declarados (R3).

    Valida tudo antes de persistir (contagem, PDF-only por arquivo, tipos do
    catálogo). No sucesso, numa transação única: ``Case(NEW, created_by=user)``,
    as rows ``CaseDocument`` (position 1..N, arquivos gravados no storage) e a
    declaração via ``set_declared_procedures`` (com evento na trilha). Fora da
    transação, o processamento é disparado via ``enqueue_case_processing``
    (inline em dev/teste ou enqueue no cluster pdf — D7, slice 003). Em exceção
    após gravações físicas, remove best-effort os arquivos já escritos (o
    rollback do banco não reverte o filesystem).
    """
    uploaded_files = list(files)
    declared_types = tuple(procedure_types)
    _validate_batch(uploaded_files)
    _validate_declared_types(declared_types)

    saved_file_names: list[str] = []
    try:
        with transaction.atomic():
            case = Case.objects.create(created_by=user)
            for position, uploaded_file in enumerate(uploaded_files, start=1):
                document = CaseDocument(
                    case=case,
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
            set_declared_procedures(case, declared_types, user=user, role=role)
    except BaseException:
        _delete_saved_files_best_effort(saved_file_names)
        raise
    # Fora da transação (D7): enfileira no cluster pdf ou executa inline.
    enqueue_case_processing(case)
    return case
