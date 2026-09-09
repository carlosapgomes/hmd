"""Serviços de anexos — validação (change 10, slice 001, design D2/D7).

``validate_attachments`` é a FONTE ÚNICA da validação de anexos do intake:
contagem ≤ ``ATTACHMENTS_MAX_COUNT``, tamanho ≤ ``ATTACHMENTS_MAX_SIZE_MB`` por
arquivo e MIME ∈ ``ATTACHMENTS_ACCEPTED_MIME_TYPES`` (jpeg/png/pdf), com erro
nomeado (``AttachmentValidationError``, subclasse de ``ValueError`` — mesmo
contrato do ``_validate_batch`` do intake). Validação pura, SEM efeito: roda
antes de qualquer gravação na criação do caso (``create_case_with_documents``).
Anexos são opcionais — lista vazia é válida (default ``()`` preserva o
change 04).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile


class AttachmentValidationError(ValueError):
    """Lote de anexos inválido — nada foi persistido (R2/D2)."""


def validate_attachments(uploaded_files: Sequence[UploadedFile[Any]]) -> None:
    """Valida o lote de anexos antes de qualquer gravação (contagem/tamanho/MIME).

    Nomeia no erro o arquivo (tipo/tamanho) ou o limite (contagem), no mesmo
    espírito do ``_validate_batch`` do intake — anexos são opcionais, então um
    lote vazio passa sem validação de conteúdo.

    Raises:
        AttachmentValidationError: lote fora dos limites (nada foi gravado).
    """
    files = list(uploaded_files)
    if not files:
        return

    max_count = settings.ATTACHMENTS_MAX_COUNT
    if len(files) > max_count:
        raise AttachmentValidationError(
            f"Máximo de {max_count} anexos por caso. Recebidos: {len(files)}."
        )

    accepted_types = {
        mime.strip().lower() for mime in settings.ATTACHMENTS_ACCEPTED_MIME_TYPES if mime.strip()
    }
    accepted_text = ", ".join(sorted(accepted_types))
    max_bytes = settings.ATTACHMENTS_MAX_SIZE_MB * 1024 * 1024
    for uploaded_file in files:
        file_name = uploaded_file.name or ""
        file_size = uploaded_file.size or 0
        content_type = (uploaded_file.content_type or "").lower()
        if content_type not in accepted_types:
            raise AttachmentValidationError(
                f'"{file_name}" não é um anexo aceito (tipos permitidos: {accepted_text}).'
            )
        if file_size > max_bytes:
            raise AttachmentValidationError(
                f'"{file_name}" excede o limite de {settings.ATTACHMENTS_MAX_SIZE_MB} MB '
                f"por anexo ({file_size / (1024 * 1024):.1f} MB)."
            )
