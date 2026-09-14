"""Formulários do intake do NIR (change intake-nir-upload, slice 001, R4;
change intake-batch-semantics, slice 002).

Referência de padrão: ats-web ``apps/intake/forms.py`` — lá o form é vazio e a
validação de arquivos fica no serviço (o Django não tem ``FileField`` múltiplo
nativo). Divergência HMD (D1/R4): o form declara o campo de arquivos com o
atributo HTML ``multiple`` e o tipo do lote como um ``ChoiceField`` único em
radio — mas quem valida é sempre ``apps.intake.services`` (fonte única); o
campo de arquivos é um ``Field`` próprio cujo valor limpo é a lista de
``UploadedFile`` (``files.getlist``), sem validação de conteúdo aqui.

Semântica de lote (slice 002, R1/R2): cada PDF é o relatório de um paciente e
vira um caso, então o tipo é ÚNICO por envio (radio, obrigatório — tipo
ausente/fora do catálogo é erro de formulário, sem chegar ao serviço) e os
hints dos campos são montados com os números reais dos settings (D3) a cada
instanciação.
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


def _megabytes(size_in_bytes: int) -> int:
    """Limite em MB para as mensagens — os settings são literais em bytes (D3)."""
    return size_in_bytes // (1024 * 1024)


def _documents_help_text() -> str:
    """Hint dos relatórios (R2): semântica do lote + números reais dos settings.

    Montado na instanciação (e não no corpo da classe, que congela os valores
    no import) para que ``override_settings`` de um teste/ambiente seja o que
    aparece na tela.
    """
    return (
        f"Selecione de 1 a {settings.INTAKE_MAX_FILES_PER_BATCH} PDFs — "
        "cada PDF é o relatório de um paciente e vira um caso. "
        f"Limite de {_megabytes(settings.INTAKE_MAX_UPLOAD_BYTES_PER_FILE)} MB por arquivo "
        f"e {_megabytes(settings.INTAKE_MAX_UPLOAD_BYTES_PER_BATCH)} MB no total do envio."
    )


def _attachments_help_text() -> str:
    """Hint dos anexos (R2): limites do change 10 + regra do relatório único."""
    return (
        f"Anexos de evidência (exames/fotos) em jpg, png ou pdf — até "
        f"{settings.ATTACHMENTS_MAX_COUNT} arquivos de no máximo "
        f"{settings.ATTACHMENTS_MAX_SIZE_MB} MB cada, somente quando o envio "
        "tiver exatamente 1 relatório."
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
    """Envio do relatório: 1–N PDFs (1 caso por PDF) + 1 tipo único (R1/R2).

    Os campos de arquivo coletam valores sem validar (a validação real é do
    serviço — R3 do slice 001); o tipo ÚNICO é obrigatório e restrito ao
    catálogo, de modo que um envio sem tipo válido nem chega ao serviço (R1).
    """

    documents = MultiDocumentField(
        label="Arquivos PDF do relatório",
        required=False,
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
    # dos documentos acima. A regra do lote (anexos só com exatamente 1
    # relatório) também é do serviço; o hint a anuncia (R2) e o JS a reforça
    # desabilitando o input quando >1 arquivo é selecionado (R3).
    attachments = MultiDocumentField(
        label="Anexos (opcional)",
        required=False,
        widget=MultiPdfInput(
            attrs={
                "multiple": True,
                "accept": ".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf",
                "class": "form-control",
            }
        ),
    )
    procedure_type = forms.ChoiceField(
        label="Tipo de procedimento",
        required=True,
        help_text="Um tipo por envio, aplicado a todos os relatórios do lote.",
        choices=_PROCEDURE_TYPE_CHOICES,
        widget=forms.RadioSelect(
            attrs={"class": "form-check-input"},
        ),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Hints com os números dos settings vigentes (R2, D3)."""
        super().__init__(*args, **kwargs)
        self.fields["documents"].help_text = _documents_help_text()
        self.fields["attachments"].help_text = _attachments_help_text()


class CorrectedResubmissionForm(IntakeUploadForm):
    """Reenvio corrigido de um caso encerrado (nir-result-closure, D4/R4).

    Mesmo shape do envio em lote (documentos + tipo único declarado) com o
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
