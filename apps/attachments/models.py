"""Models de anexos (change attachment-processing-ocr, slice 001, design D1).

``CaseAttachment`` é a row do anexo clínico (jpg/png/pdf) enviado pelo NIR na
criação do caso/reenvio corrigido — evidência humana suplementar ao relatório
principal (que segue PDF-only em ``CaseDocument``). Espelho enxuto do ats-web
sem supressão/fase/sha (D1): campos exatos do design — FK ``case`` PROTECT com
reverso ``attachments``, ``file`` com path seguro por UUID no callable
(``case_attachments/<case_id>/<uuid4-hex>.<ext>``; nome original nunca no
path), identificadores do upload e os campos de processamento/verificação que
nascem vazios/null (``status=pending``) e são preenchidos pelos workers dos
slices 002/003.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.cases.models import Case


class AttachmentStatus(models.TextChoices):
    """Status do processamento do anexo (D3): pendente → processando → processado|falhou."""

    PENDING = "pending", "Pendente"
    PROCESSING = "processing", "Processando"
    PROCESSED = "processed", "Processado"
    FAILED = "failed", "Falhou"


class ExtractionMethod(models.TextChoices):
    """Método de extração de texto do anexo (D3): PyMuPDF local ou OCR externo."""

    LOCAL_PDF = "local_pdf", "Extração local (PDF)"
    VISION = "vision", "OCR externo (vision)"


class PatientMatch(models.TextChoices):
    """Resultado da verificação de identidade do paciente no anexo (D4)."""

    MATCH = "match", "Coincidente"
    MISMATCH = "mismatch", "Divergente"
    UNKNOWN = "unknown", "Desconhecido"


# Extensão de arquivo derivada do MIME aceito (D1/D7): usada pelo path
# callable; os MIMEs em si são validados na fonte única de
# ``apps/attachments/services.py`` antes de qualquer gravação.
_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/pdf": ".pdf",
}


def _extension_for_content_type(content_type: str) -> str:
    """Extensão de arquivo do anexo a partir do content-type aceito."""
    return _CONTENT_TYPE_EXTENSIONS.get((content_type or "").lower(), ".bin")


def case_attachment_upload_path(instance: CaseAttachment, filename: str) -> str:
    """Path de storage seguro do anexo (R1/D1) — espelho do ``case_document_upload_path``.

    Pasta por caso (``case_attachments/<case_id>/``) com nome UUID4-hex + ext
    derivada do MIME aceito: o nome original do upload (controlado pelo
    usuário) nunca entra no path (sem colisão/sobrescrita e path traversal no
    filesystem local). O UUID é gerado AQUI no callable — o arquivo é gravado
    antes do INSERT, então o pk da row ainda não existe (nunca usar pk no path).
    """
    del filename
    extension = _extension_for_content_type(instance.content_type)
    return f"case_attachments/{instance.case_id}/{uuid.uuid4().hex}{extension}"


class CaseAttachment(models.Model):
    """Anexo clínico (jpg/png/pdf) de um caso (change 10, slice 001, D1)."""

    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name="attachments")
    file = models.FileField(upload_to=case_attachment_upload_path, max_length=255)
    # Nome original do upload (espelho do CaseDocument): o path de storage usa
    # UUID e nunca o nome controlado pelo usuário — o NIR precisa ver um nome
    # legível na listagem (spec attachments R1).
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="case_attachments_uploaded",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Processamento (D3/D4): nasce ``pending`` com os campos de extração/
    # verificação vazios/null — preenchidos pelos workers dos slices 002/003.
    status = models.CharField(
        max_length=20,
        choices=AttachmentStatus.choices,
        default=AttachmentStatus.PENDING,
    )
    extraction_method = models.CharField(
        max_length=20,
        choices=ExtractionMethod.choices,
        blank=True,
    )
    extracted_text = models.TextField(blank=True)
    anonymized_text = models.TextField(blank=True)
    pseudonym_map = models.JSONField(default=dict, blank=True)
    patient_match = models.CharField(
        max_length=10,
        choices=PatientMatch.choices,
        null=True,
        blank=True,
    )
    verification_summary = models.TextField(blank=True)
    verification_evidence = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    failed_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"CaseAttachment {self.pk} [{self.status}]"
