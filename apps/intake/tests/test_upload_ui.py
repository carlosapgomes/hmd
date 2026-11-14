"""Testes da tela de envio em lote (slice 002 do intake-batch-semantics).

Cobre: R1 (tipo único em select canônico — slice 002, era radio;
    ausente/inválido → erro de FORMULÁRIO sem
chamar o serviço), R2 (hints com os números REAIS dos settings — arquivos por
lote, tamanho por arquivo e total, e a regra "anexos só com exatamente 1
relatório"), R3 (a home registra ``static/js/intake-upload.js`` e expõe os
ids/attrs que o script consome — sem framework de browser tests no repo, o
comportamento é coberto pela estrutura do template + do script) e R4 (contrato
redirect × resultado do design D4: lote e/ou erros → página de resultado com
"N casos criados" e os erros por arquivo; 1 caso sem erros → detalhe, coberto
por ``test_my_cases.py``/``test_creation.py``).

Os casos nascem em ``NEW``: o módulo desliga o processamento automático
(``INTAKE_RUN_TASKS_INLINE=False``), como os demais testes do intake.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from django import forms
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseStatus
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.intake.forms import IntakeUploadForm

NIR_ROLE = "nir"
ANGIO_TYPE = "art_perif"


@pytest.fixture(autouse=True)
def _creation_without_processing() -> Iterator[None]:
    """Criação apenas (R7 do slice 001): sem processamento pós-criação."""
    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


@pytest.fixture
def _service_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Registra chamadas ao serviço de lote — o form inválido não pode chamá-lo."""
    calls: list[dict[str, object]] = []

    def _submit(**kwargs: object) -> tuple[list[Case], list[str]]:
        calls.append(kwargs)
        return [], []

    monkeypatch.setattr("apps.intake.views.submit_report_batch", _submit)
    return calls


# ── R1: tipo único do lote ────────────────────────────────────────────────


def test_form_single_procedure_type_select() -> None:
    """R1: o tipo é um ``ChoiceField`` único (select canônico — slice 002 do
    ui-parity-intake-detail, era radio) com as choices do catálogo; o campo
    múltiplo antigo não existe mais."""
    form = IntakeUploadForm()

    field = form.fields["procedure_type"]
    assert isinstance(field, forms.ChoiceField)
    assert isinstance(field.widget, forms.Select)
    assert field.required is True
    assert "um tipo por envio" in str(field.help_text).lower()
    assert "procedure_types" not in form.fields

    rendered = str(form["procedure_type"])
    assert "<select" in rendered
    assert 'type="radio"' not in rendered
    assert rendered.count('name="procedure_type"') == 1
    assert rendered.count("<option") == len(PROCEDURE_PROFILES) + 1
    for profile in PROCEDURE_PROFILES:
        assert f'value="{profile.procedure_type}"' in rendered
        assert profile.label in rendered


def test_form_requires_type() -> None:
    """R1: tipo ausente e tipo fora do catálogo são erros de FORMULÁRIO."""
    missing = IntakeUploadForm(data={"procedure_type": ""})
    assert not missing.is_valid()
    assert "procedure_type" in missing.errors

    invalid = IntakeUploadForm(data={"procedure_type": "procedimento_inexistente"})
    assert not invalid.is_valid()
    assert "procedure_type" in invalid.errors


@pytest.mark.django_db
def test_upload_without_type_never_calls_service(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    _service_spy: list[dict[str, object]],
) -> None:
    """R1: POST sem tipo re-renderiza o form com erro e NÃO chama o serviço."""
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:home"),
        {"documents": [pdf_factory()]},
    )

    assert response.status_code == 200
    assert _service_spy == []
    assert Case.objects.count() == 0
    assert "procedure_type" in response.context["form"].errors


@pytest.mark.django_db
def test_upload_with_invalid_type_never_calls_service(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    _service_spy: list[dict[str, object]],
) -> None:
    """R1: POST com tipo fora do catálogo também não chega ao serviço."""
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:home"),
        {"documents": [pdf_factory()], "procedure_type": "procedimento_inexistente"},
    )

    assert response.status_code == 200
    assert _service_spy == []
    assert Case.objects.count() == 0
    assert "procedure_type" in response.context["form"].errors


# ── R2: hints com os números dos settings ─────────────────────────────────


def test_help_texts_default_limits() -> None:
    """R2: hints trazem os números dos settings (30 arquivos / 20 MB / 100 MB)."""
    form = IntakeUploadForm()

    documents_help = str(form.fields["documents"].help_text)
    assert "30" in documents_help
    assert "20 MB" in documents_help
    assert "100 MB" in documents_help
    assert "cada PDF é o relatório de um paciente e vira um caso" in documents_help

    attachments_help = str(form.fields["attachments"].help_text)
    assert "exatamente 1 relatório" in attachments_help


def test_help_texts_follow_settings_overrides() -> None:
    """R2: os números dos hints são dinâmicos (não hardcoded) — mudam com os
    settings usados na instanciação do form."""
    with override_settings(
        INTAKE_MAX_FILES_PER_BATCH=7,
        INTAKE_MAX_UPLOAD_BYTES_PER_FILE=3 * 1024 * 1024,
        INTAKE_MAX_UPLOAD_BYTES_PER_BATCH=9 * 1024 * 1024,
    ):
        form = IntakeUploadForm()

    documents_help = str(form.fields["documents"].help_text)
    assert "7" in documents_help
    assert "3 MB" in documents_help
    assert "9 MB" in documents_help
    assert "20 MB" not in documents_help
    assert "100 MB" not in documents_help


# ── R3: JS de anexos desabilitáveis (template + estrutura do script) ──────


@pytest.mark.django_db
def test_home_registers_upload_script_with_attrs(
    client: Client,
    nir_user: User,
) -> None:
    """R3: a home registra o JS novo e expõe os ids/attrs que ele consome; o
    hint de anexos nasce oculto e o input segue múltiplo (quem desabilita é o
    JS, não o HTML)."""
    client.force_login(nir_user)

    response = client.get(reverse("intake:home"))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'src="/static/js/intake-upload.js"' in body
    assert "defer" in body
    assert "data-intake-upload" in body
    assert 'id="id_documents"' in body
    assert 'data-documents-field="id_documents"' in body
    assert 'id="id_attachments"' in body
    assert 'data-attachments-field="id_attachments"' in body
    assert "data-attachments-group" in body
    assert 'id="intake-attachments-single-hint"' in body
    assert 'data-attachments-single-hint="intake-attachments-single-hint"' in body
    assert "d-none" in body
    assert "anexos só com exatamente 1 relatório" in body.lower()
    assert 'name="attachments"' in body
    input_documents = re.search(r'<input[^>]*id="id_documents"[^>]*>', body)
    assert input_documents is not None and "multiple" in input_documents.group(0)


def test_upload_script_structure() -> None:
    """R3: o script do intake existe, usa os seletores do template, desabilita
    o input de anexos (``disabled`` + classe visual + ``aria-disabled``), nunca
    submete o form e NÃO é registrado no service worker (out of scope do slice)."""
    script_path = finders.find("js/intake-upload.js")
    assert script_path is not None
    script = Path(script_path).read_text(encoding="utf-8")

    for token in (
        "[data-intake-upload]",
        "dataset.documentsField",
        "dataset.attachmentsField",
        "[data-attachments-group]",
        "dataset.attachmentsSingleHint",
        ".disabled =",
        "aria-disabled",
        "classList.toggle",
        "addEventListener",
    ):
        assert token in script, f"token ausente no script: {token}"
    assert ".submit(" not in script

    sw_path = finders.find("js/sw.js")
    assert sw_path is not None
    assert "intake-upload.js" not in Path(sw_path).read_text(encoding="utf-8")


# ── R4: resultado do envio (design D4) ────────────────────────────────────


@pytest.mark.django_db
def test_upload_batch_result_shows_created_and_errors(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4/design D4: lote com falha parcial → página de resultado com a
    contagem de casos criados, o erro nomeando o arquivo e o link Meus casos."""
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:home"),
        {
            "documents": [
                pdf_factory(name="paciente-a.pdf"),
                pdf_factory(name="foto.jpg", content_type="image/jpeg"),
                pdf_factory(name="paciente-b.pdf"),
            ],
            "procedure_type": ANGIO_TYPE,
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "2 casos criados" in body
    assert "foto.jpg" in body
    assert reverse("intake:my_cases") in body
    assert sorted(case.documents.get().original_filename for case in Case.objects.all()) == [
        "paciente-a.pdf",
        "paciente-b.pdf",
    ]


@pytest.mark.django_db
def test_upload_batch_all_invalid_shows_zero_created(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: nenhum caso criado e há erros → página de resultado com
    "0 casos criados" e o erro nomeando o arquivo (nada persistido)."""
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:home"),
        {
            "documents": [pdf_factory(name="foto.jpg", content_type="image/jpeg")],
            "procedure_type": ANGIO_TYPE,
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "0 casos criados" in body
    assert "foto.jpg" in body
    assert Case.objects.count() == 0


@pytest.mark.django_db
def test_upload_single_case_with_errors_shows_result(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: 1 arquivo válido + 1 inválido (NÃO é o caso "1 caso e zero erros")
    → página de resultado com "1 caso criado" e o erro do arquivo."""
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:home"),
        {
            "documents": [
                pdf_factory(name="paciente-a.pdf"),
                pdf_factory(name="foto.jpg", content_type="image/jpeg"),
            ],
            "procedure_type": ANGIO_TYPE,
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "1 caso criado" in body
    assert "foto.jpg" in body
    assert Case.objects.count() == 1


@pytest.mark.django_db
def test_upload_batch_success_without_errors_shows_result(client: Client, nir_user: User) -> None:
    """R4 (review slice 002, P2): lote N>1 SEM erros também renderiza a página
    de resultado com a contagem — ramo puro-sucesso sem bloco de erros."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    pdf1 = SimpleUploadedFile("lote-a.pdf", b"%PDF-lote-a", content_type="application/pdf")
    pdf2 = SimpleUploadedFile("lote-b.pdf", b"%PDF-lote-b", content_type="application/pdf")
    client.force_login(nir_user)
    session = client.session
    session["active_role"] = "nir"
    session.save()

    response = client.post(
        reverse("intake:home"),
        {"documents": [pdf1, pdf2], "procedure_type": "art_perif"},
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "2 casos criados" in body
    assert "arquivos rejeitados" not in body


# ── Slice 001 ui-parity-intake-detail: upload com drag-and-drop + preview ──
#
# R1 (zona + preview + autoridade do servidor), R2 (sem JS o input nativo
# segue submetível), R3 (reenvio corrigido usa o mesmo widget) e R4 (nenhum
# gate de confirmação de anexos — o gate do ats-web NÃO é portado).


def _attachment_file(
    name: str = "foto.jpg", content_type: str = "image/jpeg"
) -> SimpleUploadedFile:
    """Anexo fake (conteúdo não lido; validação é por metadados)."""
    return SimpleUploadedFile(name, b"anexo fake", content_type=content_type)


def _drive_to_cleaned(case: Case, *, nir: User, doctor: User) -> None:
    """Dirige o caso pelas operações FSM até ``CLEANED`` (setup do reenvio)."""
    case.start_pdf_extraction(user=None, role="system")
    case.complete_pdf_extraction(user=None, role="system")
    case.complete_anonymization(user=None, role="system")
    case.complete_llm_extraction(user=None, role="system")
    case.complete_llm_summarization(user=None, role="system")
    case.record_doctor_decision(accepted=False, user=doctor, role="doctor")
    case.post_final_reply(user=doctor, role="doctor")
    case.nir_acknowledge(user=nir, role="nir")
    case.start_cleaning(user=nir, role="nir")
    case.complete_cleaning(user=nir, role="nir")
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED


@pytest.mark.django_db
def test_home_upload_zone_and_preview_markers(
    client: Client,
    nir_user: User,
) -> None:
    """R1: a home expõe a zona (drag-and-drop + clique), o preview (lista,
    contagem e tamanho total) e o alerta de lote; o input de documentos
    vive dentro da zona e segue um input nativo visível (R2: sem JS ele
    submete o form sozinho — nunca é escondido pelo HTML)."""
    client.force_login(nir_user)

    response = client.get(reverse("intake:home"))

    assert response.status_code == 200
    body = response.content.decode()
    for token in (
        'id="upload-zone"',
        "upload-zone__icon",
        "upload-zone__text",
        "upload-zone__hint",
        'id="upload-preview"',
        'id="batch-count"',
        'id="batch-total-size"',
        'id="file-list"',
        'id="upload-alert"',
        'id="attachment-preview"',
        'id="attachment-count"',
        'id="attachment-total-size"',
        'id="attachment-list"',
    ):
        assert token in body, f"marcador ausente na home: {token}"
    # O input de documentos está dentro da zona (entre a abertura da zona
    # e o preview) e é visível sem JS: sem ``d-none``/``novalidate``.
    zone_open = body.index('id="upload-zone"')
    documents_input = body.index('id="id_documents"')
    preview_open = body.index('id="upload-preview"')
    assert zone_open < documents_input < preview_open
    input_tag = re.search(r'<input[^>]*id="id_documents"[^>]*>', body)
    assert input_tag is not None
    assert "d-none" not in input_tag.group(0)
    assert "multiple" in input_tag.group(0)
    assert "novalidate" not in body


def test_upload_script_dragdrop_preview_structure() -> None:
    """R1/R4: o script implementa drag-and-drop (dragover/dragleave/drop),
    preview com lista/contagem/tamanho, remoção por arquivo e sincronização
    dos arrastados via ``DataTransfer`` no submit — com os limites HMD
    (30 arquivos, 20 MB/arquivo, 100 MB/lote; anexos 5 × 10 MB) e SEM o
    gate de confirmação do ats-web (nenhum token de confirmação)."""
    script_path = finders.find("js/intake-upload.js")
    assert script_path is not None
    script = Path(script_path).read_text(encoding="utf-8")

    for token in (
        "upload-zone",
        "upload-preview",
        "batch-count",
        "batch-total-size",
        "file-list",
        "upload-alert",
        "attachment-preview",
        "attachment-list",
        "attachment-count",
        "attachment-total-size",
        "dragover",
        "dragleave",
        "drop",
        "drag-over",
        "DataTransfer",
        "dataTransfer",
        "handleFiles",
        "handleAttachmentFiles",
        "formatSize",
        "btn-remove-file",
        "btn-remove-attachment",
        "showAlert",
        "upload-zone__text",
        "upload-zone__hint",
        'addEventListener("submit"',
        # Limites HMD (config/settings/base.py), não os do molde (600 MB).
        "30",
        "20 * 1024 * 1024",
        "100 * 1024 * 1024",
        "10 * 1024 * 1024",
    ):
        assert token in script, f"token ausente no script: {token}"
    for forbidden in (
        "attachmentConfirm",
        "attachment-confirm",
        "attachment_confirmed",
        "Confirme que revisou",
    ):
        assert forbidden not in script, f"gate do ats-web vazou: {forbidden}"


@pytest.mark.django_db
def test_upload_multi_pdf_with_attachment_is_non_blocking_via_view(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1 (autoridade do servidor): 2 PDFs + anexo → os 2 casos são criados
    sem anexos e o erro nomeado (não-bloqueante) informa que anexos exigem
    exatamente 1 relatório."""
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:home"),
        {
            "documents": [pdf_factory(name="paciente-a.pdf"), pdf_factory(name="paciente-b.pdf")],
            "procedure_type": ANGIO_TYPE,
            "attachments": [_attachment_file()],
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "2 casos criados" in body
    assert "exatamente 1" in body
    assert Case.objects.count() == 2
    assert all(case.attachments.count() == 0 for case in Case.objects.all())


@pytest.mark.django_db
def test_upload_single_pdf_with_attachment_needs_no_confirmation(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: anexo com 1 relatório submete SEM confirmação extra — o POST não
    envia nenhum campo de confirmação e o caso nasce com o anexo."""
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:home"),
        {
            "documents": [pdf_factory(name="paciente-a.pdf")],
            "procedure_type": ANGIO_TYPE,
            "attachments": [_attachment_file()],
        },
    )

    # 1 caso e zero erros → redirect ao detalhe (contrato D4); nenhum
    # gate de confirmação é exigido no caminho.
    assert response.status_code == 302
    case = Case.objects.get()
    assert response["Location"] == reverse("intake:case_detail", args=[case.case_id])
    assert case.attachments.count() == 1
    assert case.attachments.get().original_filename == "foto.jpg"


@pytest.mark.django_db
def test_corrected_resubmission_uses_same_upload_widget(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R3: o reenvio corrigido (1 PDF) carrega o mesmo script e expõe a
    mesma zona/preview, com o input de documentos SEM ``multiple`` (1 PDF)
    e o de anexos múltiplo."""
    doctor = user_factory("doctor-resub-widget", "doctor")
    original = Case.objects.create(created_by=nir_user)
    _drive_to_cleaned(original, nir=nir_user, doctor=doctor)
    client.force_login(nir_user)

    response = client.get(reverse("intake:case_resubmit", args=[original.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'src="/static/js/intake-upload.js"' in body
    assert "data-intake-upload" in body
    assert 'data-documents-field="id_documents"' in body
    assert 'data-attachments-field="id_attachments"' in body
    for token in (
        'id="upload-zone"',
        "upload-zone__text",
        "upload-zone__hint",
        'id="upload-preview"',
        'id="batch-count"',
        'id="batch-total-size"',
        'id="file-list"',
        'id="upload-alert"',
        'id="attachment-preview"',
        'id="attachment-list"',
        'id="intake-attachments-single-hint"',
    ):
        assert token in body, f"marcador ausente no reenvio: {token}"
    documents_tag = re.search(r'<input[^>]*id="id_documents"[^>]*>', body)
    assert documents_tag is not None and "multiple" not in documents_tag.group(0)
    attachments_tag = re.search(r'<input[^>]*id="id_attachments"[^>]*>', body)
    assert attachments_tag is not None and "multiple" in attachments_tag.group(0)


def test_upload_script_preserves_all_selected_files_for_server() -> None:
    """P1-A (fix-1): o widget NUNCA exclui arquivos — preview/avisa, mas
    TODOS os selecionados (não-PDF por extensão, duplicados nome+tamanho,
    acima dos limites) vão para os inputs via ``DataTransfer`` para o
    servidor, autoridade, nomeá-los (R1). O comportamento é provado no
    browser pela jornada ``drive-upload-dragdrop``; aqui ficam os tokens
    estruturais (falham se o script voltar a filtrar)."""
    script_path = finders.find("js/intake-upload.js")
    assert script_path is not None
    script = Path(script_path).read_text(encoding="utf-8")

    # Estado preserva tudo; avisos são só informativos (nunca descartam).
    for token in (
        "selectedFiles",
        "selectedAttachments",
        "warnFileIssues",
        "warnAttachmentIssues",
    ):
        assert token in script, f"token ausente no script: {token}"
    # Nenhuma rejeição client-side: sem validação com poder de descarte,
    # sem contadores de "arquivos novos" filtrados, sem descarte de
    # duplicado nem retornos antecipados por contagem/tamanho.
    for forbidden in (
        "validation.valid",
        "newFiles",
        "newAttachments",
        "já foi adicionado",
        "Selecione no máximo",
    ):
        assert forbidden not in script, f"filtragem client-side voltou: {forbidden}"


@pytest.mark.django_db
def test_upload_invalid_attachment_with_single_pdf_aborts(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """P1-A parity (specs/attachments): 1 PDF válido + anexo inválido
    (acima do limite) ABORTA — nenhum caso é criado e o erro nomeia o
    anexo. É o outcome que o widget antigo impedia ao filtrar o anexo
    oversized antes do submit."""
    client.force_login(nir_user)
    oversized = SimpleUploadedFile(
        "anexo-grande.jpg", b"x" * (2 * 1024 * 1024), content_type="image/jpeg"
    )

    with override_settings(ATTACHMENTS_MAX_SIZE_MB=1):
        response = client.post(
            reverse("intake:home"),
            {
                "documents": [pdf_factory(name="paciente-a.pdf")],
                "procedure_type": ANGIO_TYPE,
                "attachments": [oversized],
            },
        )

    assert response.status_code == 200
    body = response.content.decode()
    assert "anexo-grande.jpg" in body
    assert Case.objects.count() == 0


@pytest.mark.django_db
def test_upload_duplicate_names_processed_per_file(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/P1-A parity: dois PDFs com o MESMO nome são submetidos e o
    servidor processa arquivo a arquivo (um caso por arquivo válido) —
    o cliente não pode colapsar duplicados (o widget antigo os
    descartava pelo alerta)."""
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:home"),
        {
            "documents": [
                pdf_factory(name="relatorio.pdf"),
                pdf_factory(name="relatorio.pdf"),
            ],
            "procedure_type": ANGIO_TYPE,
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "2 casos criados" in body
    assert Case.objects.count() == 2


@pytest.mark.django_db
def test_corrected_resubmission_with_attachment_needs_no_confirmation(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3+R4: o reenvio corrigido com 1 PDF + anexo válido cria o novo caso
    com o anexo, sem campo de confirmação — mesmo comportamento do envio."""
    doctor = user_factory("doctor-resub-att-widget", "doctor")
    original = Case.objects.create(created_by=nir_user)
    _drive_to_cleaned(original, nir=nir_user, doctor=doctor)
    client.force_login(nir_user)
    url = reverse("intake:case_resubmit", args=[original.case_id])

    response = client.post(
        url,
        {
            "documents": [pdf_factory(name="corrigido.pdf")],
            "procedure_type": ANGIO_TYPE,
            "correction_reason": "laudo ilegível, reenvio com foto do exame",
            "attachments": [_attachment_file()],
        },
        follow=True,
    )

    assert response.status_code == 200
    new_case = Case.objects.exclude(pk=original.pk).get()
    assert new_case.corrects_case_id == original.case_id
    assert new_case.attachments.count() == 1
    original.refresh_from_db()
    assert original.attachments.count() == 0


@pytest.mark.django_db
def test_corrected_resubmission_with_invalid_attachment_aborts(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3 parity (specs/attachments): reenvio corrigido com 1 PDF + anexo
    inválido (acima do limite) ABORTA — nenhum caso novo é criado e o
    erro nomeia o anexo, como no envio. Paridade do POST para o caminho
    inválido; o caminho válido (widget + submit sem confirmação) é
    provado de verdade no browser pela jornada ``resubmit-real``."""
    doctor = user_factory("doctor-resub-att-invalid-widget", "doctor")
    original = Case.objects.create(created_by=nir_user)
    _drive_to_cleaned(original, nir=nir_user, doctor=doctor)
    client.force_login(nir_user)
    url = reverse("intake:case_resubmit", args=[original.case_id])
    oversized = SimpleUploadedFile(
        "anexo-grande.jpg", b"x" * (2 * 1024 * 1024), content_type="image/jpeg"
    )

    with override_settings(ATTACHMENTS_MAX_SIZE_MB=1):
        response = client.post(
            url,
            {
                "documents": [pdf_factory(name="corrigido.pdf")],
                "procedure_type": ANGIO_TYPE,
                "correction_reason": "laudo ilegível, reenvio com foto do exame",
                "attachments": [oversized],
            },
            follow=True,
        )

    assert response.status_code == 200
    body = response.content.decode()
    assert "anexo-grande.jpg" in body
    assert Case.objects.exclude(pk=original.pk).count() == 0


def test_upload_script_reenables_attachments_on_submit_for_server() -> None:
    """P1-1 (fix-2): anexos já selecionados são submetidos com >1 relatório.

    Com >1 relatório o input de anexos fica ``disabled`` durante a edição
    (regra visual/hint preservada), mas o navegador não submete controles
    desabilitados — o servidor nunca veria um anexo já selecionado para
    emitir o erro não-bloqueante nomeado (R1, specs/attachments). O
    handler de submit reabilita o input antes de sincronizar, então o
    servidor, autoridade, cria os casos sem anexos e nomeia o erro.
    """
    script_path = finders.find("js/intake-upload.js")
    assert script_path is not None
    script = Path(script_path).read_text(encoding="utf-8")

    # A regra da edição (disabled + hint) é preservada.
    assert ".disabled =" in script
    # O submit reabilita os anexos já selecionados e sincroniza.
    submit_index = script.index('addEventListener("submit"')
    tail = script[submit_index:]
    assert "disabled = false" in tail
    assert "syncFiles" in tail
