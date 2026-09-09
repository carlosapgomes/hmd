"""Formulários do intake do NIR (change intake-nir-upload, slice 001, R4).

Referência de padrão: ats-web ``apps/intake/forms.py`` — lá o form é vazio e a
validação de arquivos fica no serviço (o Django não tem ``FileField`` múltiplo
nativo). Divergência HMD (D1/R4): o form declara o campo de arquivos com o
atributo HTML ``multiple`` e o campo de tipos como checkboxes do catálogo —
mas quem valida é sempre ``apps.intake.services`` (fonte única); o campo de
arquivos é um ``Field`` próprio cujo valor limpo é a lista de ``UploadedFile``
(``files.getlist``), sem validação de conteúdo aqui.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from apps.cases.procedure_catalog import PROCEDURE_PROFILES

# Ordem do registro do catálogo (code-first); labels da fonte clínica.
_PROCEDURE_TYPE_CHOICES = tuple(
    (profile.procedure_type, profile.label) for profile in PROCEDURE_PROFILES
)


class MultiPdfInput(forms.ClearableFileInput):
    """Input de arquivo com seleção múltipla (R4).

    O ``FileInput``/``ClearableFileInput`` nativo levanta ValueError quando o
    atributo ``multiple`` é passado sem ``allow_multiple_selected = True``; a
    flag também faz ``value_from_datadict`` ler via ``files.getlist`` (lista de
    arquivos em vez de um único).
    """

    allow_multiple_selected = True


class MultiDocumentField(forms.Field):
    """Campo de 1–N arquivos: valor limpo é a lista de ``UploadedFile``.

    A validação real (PDF-only, contagem, tamanho) fica no serviço (R3) — aqui
    apenas o valor é coletado; vazio vira lista vazia e é validado lá.
    """

    widget = MultiPdfInput

    def to_python(self, value: Any) -> list[UploadedFile[Any]]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]


class IntakeUploadForm(forms.Form):
    """Envio do relatório: 1–N PDFs + ao menos um tipo declarado (R4)."""

    documents = MultiDocumentField(
        label="Arquivos PDF do relatório",
        required=False,
        help_text=(
            "Selecione de 1 a N PDFs na ordem em que compõem o relatório (apenas application/pdf)."
        ),
        widget=MultiPdfInput(
            attrs={
                "multiple": True,
                "accept": ".pdf,application/pdf",
                "class": "form-control",
            }
        ),
    )
    # Anexos de evidência (change attachment-processing-ocr, slice 001, R4):
    # campo múltiplo em adição aos PDFs do relatório — quem valida é sempre o
    # serviço (apps/attachments/services.py, fonte única), no mesmo espírito
    # dos documentos acima.
    attachments = MultiDocumentField(
        label="Anexos (opcional)",
        required=False,
        help_text=(
            f"Anexos de evidência (exames/fotos) em jpg, png ou pdf — até "
            f"{settings.ATTACHMENTS_MAX_COUNT} arquivos de no máximo "
            f"{settings.ATTACHMENTS_MAX_SIZE_MB} MB cada."
        ),
        widget=MultiPdfInput(
            attrs={
                "multiple": True,
                "accept": ".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf",
                "class": "form-control",
            }
        ),
    )
    procedure_types = forms.MultipleChoiceField(
        label="Tipos de procedimento declarados",
        required=False,
        help_text="Marque ao menos um tipo de procedimento do relatório.",
        choices=_PROCEDURE_TYPE_CHOICES,
        widget=forms.CheckboxSelectMultiple(
            attrs={"class": "form-check-input"},
        ),
    )


class CorrectedResubmissionForm(IntakeUploadForm):
    """Reenvio corrigido de um caso encerrado (nir-result-closure, D4/R4).

    Mesmo shape do envio do slice 001 (documentos + tipos declarados) com o
    campo adicional ``correction_reason`` (motivo obrigatório do reenvio). A
    validação de conteúdo segue nas fontes únicas do serviço
    (``apps.intake.services.create_corrected_resubmission``) — aqui apenas o
    valor é coletado, no mesmo espírito do ``IntakeUploadForm``.
    """

    correction_reason = forms.CharField(
        label="Motivo do reenvio corrigido",
        required=False,
        help_text=("Explique o que motivou o reenvio corrigido (campo obrigatório)."),
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3},
        ),
    )
