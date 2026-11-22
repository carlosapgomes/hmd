"""Formulários do intake do NIR (change intake-nir-upload, slice 001, R4;
change intake-batch-semantics, slice 002).

Referência de padrão: ats-web ``apps/intake/forms.py`` — lá o form é vazio e a
validação de arquivos fica no serviço (o Django não tem ``FileField`` múltiplo
nativo). Divergência HMD (D1/R4): o form declara o campo de arquivos com o
atributo HTML ``multiple`` e o tipo do lote como um ``ChoiceField`` único em
select canônico (combobox pesquisável com JS — slice 002) — mas quem valida
é sempre ``apps.intake.services`` (fonte única); o
campo de arquivos é um ``Field`` próprio cujo valor limpo é a lista de
``UploadedFile`` (``files.getlist``), sem validação de conteúdo aqui.

Semântica de lote (slice 002, R1/R2): cada PDF é o relatório de um paciente e
vira um caso, então o tipo é ÚNICO por envio (select, obrigatório — tipo
ausente/fora do catálogo é erro de formulário, sem chegar ao serviço) e os
hints dos campos são montados com os números reais dos settings (D3) a cada
instanciação. O reenvio corrigido (slice 003, R1) sobrescreve o hint do campo
de documentos: ali o envio é um só relatório corrigido.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from apps.cases.procedure_catalog import PROCEDURE_PROFILES

# Ordem do registro do catálogo (code-first); labels da fonte clínica.
# A opção vazia é o placeholder do select canônico (ui-parity-intake-detail,
# slice 002): sem ela o navegador pré-selecionaria o 1º tipo e um envio sem
# escolha submteria valor — vazio deve cair no "Este campo é obrigatório."
# como o radio sem marcação. Busca do combobox restrita a label + código
# canônico (sem sinônimos: o catálogo não tem aliases — R3 do slice).
_PROCEDURE_TYPE_CHOICES = (
    ("", "Selecione o tipo de procedimento…"),
    *((profile.procedure_type, profile.label) for profile in PROCEDURE_PROFILES),
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


def _corrected_documents_help_text() -> str:
    """Hint dos documentos do reenvio corrigido (slice 003, R1).

    O campo herdava o hint do LOTE ("1 a N PDFs, cada PDF vira um caso"); o
    reenvio cria UM caso a partir de um relatório corrigido, então só o limite
    por arquivo segue valendo.
    """
    return (
        "Selecione exatamente 1 PDF do relatório corrigido — o reenvio cria um novo "
        "caso a partir dele. "
        f"Limite de {_megabytes(settings.INTAKE_MAX_UPLOAD_BYTES_PER_FILE)} MB por arquivo."
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
    serviço — R3 do slice 001); o tipo ÚNICO (select canônico, slice 002) é
    obrigatório e restrito ao
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
    # Select canônico com progressive enhancement do combobox pesquisável
    # (slice 002, D2): sem JS o select submete o código; com JS o input
    # espelha nele apenas a opção confirmada (nunca texto livre). A validação
    # é a do ChoiceField (mesmos textos do radio).
    procedure_type = forms.ChoiceField(
        label="Tipo de procedimento",
        required=True,
        help_text="Um tipo por envio, aplicado a todos os relatórios do lote.",
        choices=_PROCEDURE_TYPE_CHOICES,
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "data-procedure-combobox": "",
                "data-combobox-placeholder": "Digite para buscar — ex.: cateterismo, nefrostomia…",
            },
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
    valor é coletado, no mesmo espírito do ``IntakeUploadForm``. O hint de
    ``documents`` é o do reenvio (exatamente 1 PDF do relatório corrigido),
    NÃO o do lote (R1 do slice 003).
    """

    correction_reason = forms.CharField(
        label="Motivo do reenvio corrigido",
        required=False,
        help_text=("Explique o que motivou o reenvio corrigido (campo obrigatório)."),
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3},
        ),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Hint, label e widget do campo de documentos do reenvio (slice 003, R1).

        O ``__init__`` herdado monta hint/label/widget do LOTE; como o reenvio
        aceita exatamente 1 PDF, o texto é sobrescrito, o label vai para o
        singular e o input perde o ``multiple`` depois do ``super`` — sem isso
        a página pediria seleção múltipla (o server recusa 0/mais de 1). O
        campo continua ``MultiDocumentField``: o valor limpo segue sendo a lista
        de 1 arquivo que o serviço espera.
        """
        super().__init__(*args, **kwargs)
        documents = self.fields["documents"]
        documents.label = "Arquivo PDF do relatório corrigido"
        documents.help_text = _corrected_documents_help_text()
        documents.widget.attrs.pop("multiple", None)


# Mensagem do motivo da intercorrência pós-encerramento (paridade-ats,
# slice 003, D3): texto livre obrigatório, reuso do padrão do desmarcar —
# sem catálogo fechado e sem campo de mensagem separado.
POST_CLOSURE_ISSUE_REASON_REQUIRED_MESSAGE = "Informe o motivo da intercorrência pós-encerramento."


class PostClosureIssueForm(forms.Form):
    """Abertura de intercorrência pós-encerramento pelo NIR (slice 003, R3).

    Motivo único de texto livre, obrigatório após ``strip`` (mesmo critério
    do serviço ``open_post_closure_issue``). A defesa de mutação continua no
    serviço transacional — aqui apenas o valor é coletado e normalizado.
    """

    reason = forms.CharField(
        label="Motivo da intercorrência",
        required=False,
        help_text="Descreva o motivo da reabertura (campo obrigatório).",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3},
        ),
    )

    def clean_reason(self) -> str:
        """Motivo normalizado (strip) e obrigatório — erro nomeado quando vazio."""
        value = self.cleaned_data.get("reason", "")
        if not isinstance(value, str) or not value.strip():
            raise forms.ValidationError(POST_CLOSURE_ISSUE_REASON_REQUIRED_MESSAGE)
        return value.strip()


# Opções do select de confirmação: exatamente os 14 tipos do catálogo
# (confirm-nir-procedure-review, slice 002, R3/D2) — seleção única, sem
# pacote/multiselect; a cardinalidade de campo repetido é validada na view
# via ``request.POST.getlist`` (o ChoiceField sozinho toma o último valor).
_PROCEDURE_REVIEW_CHOICES = tuple(
    (profile.procedure_type, profile.label) for profile in PROCEDURE_PROFILES
)


class ProcedureReviewForm(forms.Form):
    """Confirmação explícita do procedimento pelo NIR (slice 002, R3/D2).

    Select único dos 14 tipos (atual pré-selecionado pela view, inclusive
    não detectado), justificativa livre de 1–500 caracteres úteis após trim
    (sem NUL, sem truncamento silencioso) e confirmação explícita de leitura
    — sem nenhum campo de upload. Os limites espelham os do serviço
    ``confirm_case_procedure_review`` (a defesa de mutação continua nele);
    revisão e fingerprint viajam em hidden fields para a revalidação
    transacional da fonte vigente.
    """

    procedure_type = forms.ChoiceField(
        label="Procedimento confirmado",
        required=True,
        choices=_PROCEDURE_REVIEW_CHOICES,
        error_messages={
            "required": "Selecione o procedimento confirmado.",
            "invalid_choice": "Selecione um procedimento válido do catálogo.",
        },
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "data-procedure-combobox": "",
                "data-combobox-placeholder": "Digite para buscar — ex.: cateterismo, nefrostomia…",
            },
        ),
    )
    review_justification = forms.CharField(
        label="Justificativa da confirmação",
        required=False,
        help_text="Explique o motivo da confirmação (1–500 caracteres).",
        error_messages={
            "required": "Informe a justificativa da confirmação (1-500 caracteres).",
        },
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3, "maxlength": "500"},
        ),
    )
    review_acknowledged = forms.BooleanField(
        label="Confirmo que li o relatório principal do caso",
        required=True,
        error_messages={
            "required": "Confirme que leu o relatório principal para validar o procedimento.",
        },
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    review_event_id = forms.IntegerField(
        error_messages={
            "required": "Revisão de origem inválida; reabra o caso e confirme novamente.",
            "invalid": "Revisão de origem inválida; reabra o caso e confirme novamente.",
        },
        widget=forms.HiddenInput,
    )
    source_fingerprint = forms.CharField(
        error_messages={
            "required": "Identificação da fonte ausente; reabra o caso e confirme novamente.",
        },
        widget=forms.HiddenInput,
    )

    def clean_review_justification(self) -> str:
        """Trim + 1–500 úteis, sem NUL (mesmos limites do serviço, D2)."""
        value = self.cleaned_data.get("review_justification", "")
        if not isinstance(value, str):
            raise forms.ValidationError(
                "Informe a justificativa da confirmação (1-500 caracteres)."
            )
        text = value.strip()
        if not text:
            raise forms.ValidationError(
                "Informe a justificativa da confirmação (1-500 caracteres)."
            )
        if len(text) > 500:
            raise forms.ValidationError("Justificativa excede 500 caracteres.")
        if "\x00" in text:
            raise forms.ValidationError("Justificativa contém caractere inválido.")
        return text
