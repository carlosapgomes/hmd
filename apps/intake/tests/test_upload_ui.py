"""Testes da tela de envio em lote (slice 002 do intake-batch-semantics).

Cobre: R1 (tipo único em radio, ausente/inválido → erro de FORMULÁRIO sem
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
from apps.cases.models import Case
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


def test_form_single_procedure_type_radio() -> None:
    """R1: o tipo é um ``ChoiceField`` único (radio) com as choices do
    catálogo; o campo múltiplo antigo não existe mais."""
    form = IntakeUploadForm()

    field = form.fields["procedure_type"]
    assert isinstance(field, forms.ChoiceField)
    assert isinstance(field.widget, forms.RadioSelect)
    assert field.required is True
    assert "um tipo por envio" in str(field.help_text).lower()
    assert "procedure_types" not in form.fields

    rendered = str(form["procedure_type"])
    assert 'type="radio"' in rendered
    assert rendered.count('name="procedure_type"') == len(PROCEDURE_PROFILES)
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
    assert [case.documents.get().original_filename for case in Case.objects.all()] == [
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
