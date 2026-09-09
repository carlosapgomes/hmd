"""Testes do model ``CaseAttachment`` e da validação de anexos (R1/R2).

R1: ``CaseAttachment`` nasce com os campos de D1 (FK ``case`` PROTECT com
reverso ``attachments``, ``file`` com upload path seguro por UUID no callable,
identificadores do upload e campos de processamento/verificação vazios/null,
``status=pending``). R2: ``validate_attachments`` rejeita contagem/tamanho/MIME
fora dos limites com erro nomeado (``AttachmentValidationError``, subclasse de
``ValueError``) — sem efeito colateral (validação pura, antes de qualquer
gravação).
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, models
from django.test import override_settings

from apps.accounts.models import User
from apps.attachments.models import AttachmentStatus, CaseAttachment
from apps.attachments.services import AttachmentValidationError, validate_attachments
from apps.cases.models import Case

# Basename do arquivo no storage: uuid4-hex (32) + extensão derivada do MIME.
_UUID4_HEX_EXT_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|pdf)$")


def _save_attachment(
    case: Case,
    user: User,
    uploaded: SimpleUploadedFile,
) -> CaseAttachment:
    """Persiste um anexo no mesmo padrão do serviço de criação (R3): arquivo
    gravado antes do INSERT com o nome gerado pelo path callable."""
    attachment = CaseAttachment(
        case=case,
        content_type=(uploaded.content_type or "").lower(),
        size_bytes=uploaded.size or 0,
        uploaded_by=user,
    )
    attachment.file.save(uploaded.name or "", uploaded, save=False)
    attachment.save()
    return attachment


# ── R1: model ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_attachment_fields(
    user_factory: Callable[[str], User],
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: campos de D1 — FK PROTECT com reverso ``attachments``, FileField com
    path callable, identificadores e status default ``pending``."""
    user = user_factory("fields")
    case = Case.objects.create(created_by=user)
    uploaded = attachment_factory(name="evidencia-1.jpg", content_type="image/jpeg")

    attachment = _save_attachment(case, user, uploaded)

    case_fk = CaseAttachment._meta.get_field("case")
    assert isinstance(case_fk, models.ForeignKey)
    assert case_fk.remote_field.on_delete is models.PROTECT
    assert case_fk.remote_field.get_accessor_name() == "attachments"
    file_field = CaseAttachment._meta.get_field("file")
    assert isinstance(file_field, models.FileField)
    assert callable(file_field.upload_to)

    # Reverso ``attachments`` do caso lista a row criada.
    assert list(case.attachments.all()) == [attachment]
    assert attachment.content_type == "image/jpeg"
    assert attachment.size_bytes == uploaded.size
    assert attachment.uploaded_by == user
    assert attachment.created_at is not None
    assert attachment.status == AttachmentStatus.PENDING


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("content_type", "extension"),
    [
        ("image/jpeg", ".jpg"),
        ("image/png", ".png"),
        ("application/pdf", ".pdf"),
    ],
)
def test_upload_path_safe_uuid_per_mime(
    content_type: str,
    extension: str,
    user_factory: Callable[[str], User],
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/D1: path ``case_attachments/<case_id>/<uuid4-hex>.<ext>`` — UUID
    gerado no callable, nome original nunca no path, ext derivada do MIME."""
    user = user_factory(content_type.replace("/", "-"))
    case = Case.objects.create(created_by=user)
    original_name = "nome-original-controlado-pelo-usuario"
    uploaded = attachment_factory(name=original_name, content_type=content_type)

    attachment = _save_attachment(case, user, uploaded)

    stored_name = attachment.file.name or ""
    prefix = f"case_attachments/{case.case_id}/"
    assert stored_name.startswith(prefix)
    basename = stored_name.rsplit("/", 1)[-1]
    assert _UUID4_HEX_EXT_RE.fullmatch(basename) is not None
    assert basename.endswith(extension)
    # Nome original nunca no path (colisão/sobrescrita/path traversal).
    assert original_name not in stored_name

    # Cada arquivo recebe um UUID distinto dentro da mesma pasta do caso.
    second = _save_attachment(case, user, attachment_factory(content_type=content_type))
    assert second.file.name != stored_name
    assert (second.file.name or "").startswith(prefix)


@pytest.mark.django_db
def test_processing_fields_empty_or_null_on_creation(
    user_factory: Callable[[str], User],
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/D1: campos de processamento/verificação nascem vazios/null (status
    ``pending``) — preenchidos apenas pelos workers dos slices 002/003."""
    user = user_factory("defaults")
    case = Case.objects.create(created_by=user)

    attachment = _save_attachment(case, user, attachment_factory(content_type="application/pdf"))

    assert attachment.status == AttachmentStatus.PENDING
    assert attachment.extraction_method == ""
    assert attachment.extracted_text == ""
    assert attachment.anonymized_text == ""
    assert attachment.pseudonym_map == {}
    assert attachment.patient_match is None
    assert attachment.verification_summary == ""
    assert attachment.verification_evidence == ""
    assert attachment.processed_at is None
    assert attachment.failed_reason == ""


@pytest.mark.django_db
def test_case_delete_protected_by_attachment(
    user_factory: Callable[[str], User],
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: FK ``case`` PROTECT — caso com anexo não é apagável (cascata nunca
    remove anexo por engano)."""
    user = user_factory("protect")
    case = Case.objects.create(created_by=user)
    _save_attachment(case, user, attachment_factory())

    with pytest.raises(IntegrityError):
        case.delete()


# ── R2: validação ─────────────────────────────────────────────────────────


def test_validate_attachments_error_is_named_value_error() -> None:
    """R2: o erro de anexo é nomeado (``AttachmentValidationError``-style) e é
    um ``ValueError`` (mesmo contrato das validações do intake)."""
    assert issubclass(AttachmentValidationError, ValueError)


def test_validate_attachments_empty_batch_is_valid() -> None:
    """R2: lista vazia é válida — anexos são opcionais (default ``()``)."""
    validate_attachments([])


def test_validate_attachments_accepts_allowed_types(
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: jpeg/png/pdf passam sem erro (mesmo lote)."""
    validate_attachments(
        [
            attachment_factory(content_type="image/jpeg"),
            attachment_factory(content_type="image/png"),
            attachment_factory(content_type="application/pdf"),
        ]
    )


@pytest.mark.parametrize("content_type", ["text/plain", "image/gif", "application/zip"])
def test_validate_attachments_rejects_type(
    content_type: str,
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: MIME fora de {jpeg, png, pdf} é rejeitado nomeando o arquivo."""
    uploaded = attachment_factory(name="arquivo-estranho", content_type=content_type)

    with pytest.raises(AttachmentValidationError, match="arquivo-estranho"):
        validate_attachments([uploaded])


def test_validate_attachments_rejects_oversize(
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: arquivo acima de ATTACHMENTS_MAX_SIZE_MB é rejeitado nomeando-o."""
    del attachment_factory
    oversized = SimpleUploadedFile(
        "grande.jpg",
        b"x" * (1024 * 1024 + 1),
        content_type="image/jpeg",
    )

    with override_settings(ATTACHMENTS_MAX_SIZE_MB=1):
        with pytest.raises(AttachmentValidationError, match="grande.jpg"):
            validate_attachments([oversized])


def test_validate_attachments_rejects_over_count(
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: lote acima de ATTACHMENTS_MAX_COUNT é rejeitado nomeando o limite."""
    batch = [
        attachment_factory(content_type="image/jpeg"),
        attachment_factory(content_type="image/png"),
        attachment_factory(content_type="application/pdf"),
    ]

    with override_settings(ATTACHMENTS_MAX_COUNT=2):
        with pytest.raises(AttachmentValidationError, match="Máximo de 2"):
            validate_attachments(batch)
