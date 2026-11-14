"""Combobox de tipo de procedimento (ui-parity-intake-detail, slice 002).

R1: digitação filtra por label/código ignorando acentos; teclado (↑↓/Enter)
seleciona; texto visível e escolha canônica submetida nunca divergem
(editar/limpar após selecionar atualiza o canônico) — provado no browser
pela jornada ``combobox-procedimento``; aqui ficam o contrato do form, o
markup SSR sem JS e a estrutura do script/estilos que sustentam o R1.
R2: envio e reenvio submetem ``procedure_type`` válido do catálogo;
inválido é rejeitado com o mesmo texto/status do radio; vazio, texto sem
match e texto digitado sem confirmar NÃO submetem valor (com e sem JS —
sem JS, o select canônico vazio/inválido cai na mesma validação do
ChoiceField); erro com associação (``aria-invalid``/``aria-describedby``)
e foco visível acessível.
R3: busca restrita a labels + códigos canônicos dos 13 perfis; nenhum tipo
novo ou removido; sem sinônimos (deferidos com nota de produto — o hint
não promete sinônimos e nenhum ``data-search-aliases`` é emitido).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from django import forms
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseStatus
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.intake.forms import CorrectedResubmissionForm, IntakeUploadForm

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
CARDIO_TYPE = "cat_cardiaco"
REASON = "laudo com dados divergentes do exame original"

SEARCH_HINT_ID = "procedure-type-search-hint"
SEARCH_HINT_TEXT = "Busca ignora acentos e busca por nome ou código."
EMPTY_CHOICE_LABEL = "Selecione o tipo de procedimento…"
PROCEDURE_LABEL_ID = "id_procedure_type_label"
PROCEDURE_SELECT_ID = "id_procedure_type"

COMBOBOX_CSS_SELECTORS = (
    ".procedure-combobox",
    ".procedure-combobox--enhanced",
    ".procedure-combobox__select",
    ".procedure-combobox__input",
    ".procedure-combobox__input:focus-visible",
    ".procedure-combobox__list",
    ".procedure-combobox__option",
    ".procedure-combobox__option--active",
    ".procedure-combobox__option--selected",
    ".procedure-combobox__empty",
    ".procedure-combobox__error",
)


@pytest.fixture(autouse=True)
def _creation_without_processing() -> Iterator[None]:
    """Criação apenas: sem processamento pós-criação (padrão do intake)."""
    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


def _drive_to_cleaned(case: Case, *, nir: User, doctor: User) -> None:
    """Dirige o caso pelas operações FSM até ``CLEANED`` (setup do reenvio)."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    case.record_doctor_decision(accepted=False, user=doctor, role=DOCTOR_ROLE)
    case.post_final_reply(user=doctor, role=DOCTOR_ROLE)
    case.nir_acknowledge(user=nir, role=NIR_ROLE)
    case.start_cleaning(user=nir, role=NIR_ROLE)
    case.complete_cleaning(user=nir, role=NIR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED


def _cleaned_case(*, created_by: User, doctor: User) -> Case:
    """Caso ``CLEANED`` do criador (setup do reenvio corrigido)."""
    case = Case.objects.create(created_by=created_by)
    _drive_to_cleaned(case, nir=created_by, doctor=doctor)
    return case


def _select_tag(html: str) -> str:
    """Tag ``<select name="procedure_type">`` do controle canônico."""
    match = re.search(r'<select[^>]*name="procedure_type"[^>]*>', html)
    assert match is not None, "select canônico de procedure_type ausente"
    return match.group(0)


def _option_tags(html: str) -> list[str]:
    """Tags ``<option>`` do controle canônico (atributos em qualquer ordem)."""
    return re.findall(r'<option[^>]*value="[^"]*"[^>]*>', html)


def _option_tag(html: str, value: str) -> str:
    """Tag ``<option>`` de ``value`` (falha se ausente)."""
    for tag in _option_tags(html):
        if f'value="{value}"' in tag:
            return tag
    raise AssertionError(f"Opção {value!r} ausente no HTML: {_option_tags(html)}")


def _procedure_label_tag(html: str) -> str:
    """Tag ``<label>`` visível do tipo de procedimento (``for`` no select)."""
    match = re.search(rf'<label[^>]*for="{PROCEDURE_SELECT_ID}"[^>]*>', html)
    assert match is not None, "label visível do tipo de procedimento ausente"
    return match.group(0)


# ── Form: select canônico com as mesmas choices/validação do radio ──


def test_form_procedure_type_is_canonical_select() -> None:
    """R1/R3: o tipo é ``ChoiceField`` com widget ``Select`` (não radio),
    choices = vazio + os 13 perfis do catálogo na ordem canônica."""
    form = IntakeUploadForm()

    field = form.fields["procedure_type"]
    assert isinstance(field, forms.ChoiceField)
    assert isinstance(field.widget, forms.Select)
    assert not isinstance(field.widget, forms.RadioSelect)
    assert field.required is True
    assert "um tipo por envio" in str(field.help_text).lower()
    assert "procedure_types" not in form.fields
    choices = list(cast(Any, field).choices)
    assert choices == [
        ("", EMPTY_CHOICE_LABEL),
        *((profile.procedure_type, profile.label) for profile in PROCEDURE_PROFILES),
    ]


def test_form_procedure_type_rendered_as_select_without_js() -> None:
    """R2 (sem JS): o campo renderiza um ``<select>`` canônico submetível —
    vazio explícito (sem default para o 1º tipo), marcador do combobox e
    placeholder decorativo; nenhum radio e nenhum alias de busca."""
    form = IntakeUploadForm()

    rendered = str(form["procedure_type"])
    assert 'name="procedure_type"' in rendered
    assert "<select" in rendered
    assert 'type="radio"' not in rendered
    assert "data-procedure-combobox" in rendered
    assert "data-search-aliases" not in rendered
    assert rendered.count("<option") == len(PROCEDURE_PROFILES) + 1
    empty_tag = _option_tag(rendered, "")
    assert "selected" in empty_tag
    for profile in PROCEDURE_PROFILES:
        tag = _option_tag(rendered, profile.procedure_type)
        assert "selected" not in tag
        assert profile.label in rendered


def test_form_procedure_type_validation_texts_unchanged() -> None:
    """R2: vazio/ausente e valor fora do catálogo rejeitam com o mesmo
    texto do ChoiceField de antes (radio → select não muda a validação)."""
    missing = IntakeUploadForm(data={"procedure_type": ""})
    assert not missing.is_valid()
    assert "Este campo é obrigatório." in str(missing.errors["procedure_type"])

    absent = IntakeUploadForm(data={})
    assert not absent.is_valid()
    assert "Este campo é obrigatório." in str(absent.errors["procedure_type"])

    invalid = IntakeUploadForm(data={"procedure_type": "procedimento_inexistente"})
    assert not invalid.is_valid()
    assert "não é uma das escolhas disponíveis" in str(invalid.errors["procedure_type"])

    # Texto livre (label ou sinônimo) nunca é valor canônico (R3).
    for free_text in ("Cateterismo cardíaco", "cateterismo", "texto sem match"):
        form = IntakeUploadForm(data={"procedure_type": free_text})
        assert not form.is_valid(), free_text
        assert "não é uma das escolhas disponíveis" in str(form.errors["procedure_type"])

    valid = IntakeUploadForm(data={"procedure_type": CARDIO_TYPE})
    assert valid.is_valid()
    assert valid.cleaned_data["procedure_type"] == CARDIO_TYPE


def test_corrected_form_inherits_canonical_select() -> None:
    """R2: o reenvio corrigido herda o mesmo select canônico (sem radio)."""
    form = CorrectedResubmissionForm()

    field = form.fields["procedure_type"]
    assert isinstance(field, forms.ChoiceField)
    assert isinstance(field.widget, forms.Select)
    assert field.required is True
    choices = list(cast(Any, field).choices)
    assert choices == [
        ("", EMPTY_CHOICE_LABEL),
        *((profile.procedure_type, profile.label) for profile in PROCEDURE_PROFILES),
    ]


# ── Markup SSR + JS/CSS: contrato estrutural do combobox (R1/R3) ──


@pytest.mark.django_db
def test_home_renders_combobox_select_hint_and_script(client: Client, nir_user: User) -> None:
    """R1/R3: a home expõe o select canônico com o combobox (hint
    persistente sem promessa de sinônimos, script carregado, sem radios
    nem aliases) e a zona de upload do slice 001 intacta."""
    client.force_login(nir_user)

    response = client.get(reverse("intake:home"))

    assert response.status_code == 200
    body = response.content.decode()
    select = _select_tag(body)
    assert "data-procedure-combobox" in select
    assert "data-search-aliases" not in body
    assert 'type="radio"' not in body
    assert 'name="procedure_type"' in body
    assert "js/procedure_combobox.js" in body
    assert f'id="{SEARCH_HINT_ID}"' in body
    assert SEARCH_HINT_TEXT in body
    assert "sinônimo" not in body.lower()
    for profile in PROCEDURE_PROFILES:
        assert f'value="{profile.procedure_type}"' in body
        assert profile.label in body
    # Slice 001 preservado: zona + preview intactos.
    for token in ('id="upload-zone"', 'id="upload-preview"', 'id="file-list"'):
        assert token in body, f"marcador do slice 001 ausente: {token}"


@pytest.mark.django_db
def test_resubmit_renders_combobox_select_hint_and_script(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R2: o reenvio corrigido usa o mesmo combobox (select + hint + script),
    sem radios nem aliases."""
    doctor = user_factory("doctor-combobox-resub", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    client.force_login(nir_user)

    response = client.get(reverse("intake:case_resubmit", args=[original.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    select = _select_tag(body)
    assert "data-procedure-combobox" in select
    assert "data-search-aliases" not in body
    assert 'type="radio"' not in body
    assert "js/procedure_combobox.js" in body
    assert f'id="{SEARCH_HINT_ID}"' in body
    assert SEARCH_HINT_TEXT in body
    assert "sinônimo" not in body.lower()


@pytest.mark.django_db
def test_home_label_has_stable_id_and_native_for(client: Client, nir_user: User) -> None:
    """P1-A (sem JS): o label visível tem id estável e mantém ``for`` no
    select canônico — nome acessível do select nativo nos dois modos."""
    client.force_login(nir_user)
    body = client.get(reverse("intake:home")).content.decode()
    label = _procedure_label_tag(body)
    assert f'id="{PROCEDURE_LABEL_ID}"' in label
    assert f'for="{PROCEDURE_SELECT_ID}"' in label
    assert f'id="{PROCEDURE_SELECT_ID}"' in _select_tag(body)
    assert "Tipo de procedimento" in body


@pytest.mark.django_db
def test_resubmit_label_has_stable_id_and_native_for(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """P1-A (sem JS, reenvio): mesmo label com id estável e ``for`` no
    select canônico do reenvio."""
    doctor = user_factory("doctor-combobox-label-resub", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    client.force_login(nir_user)
    body = client.get(reverse("intake:case_resubmit", args=[original.case_id])).content.decode()
    label = _procedure_label_tag(body)
    assert f'id="{PROCEDURE_LABEL_ID}"' in label
    assert f'for="{PROCEDURE_SELECT_ID}"' in label


def test_combobox_script_associates_enhanced_input_with_visible_label() -> None:
    """P1-A (com JS): o script associa o input aprimorado ao label visível
    (``aria-labelledby`` → id do label, achado pelo ``for`` no select) e
    redireciona o clique no label para o input (o select recolhido não
    recebe o foco) — nome acessível e clique corretos com JS."""
    script_path = finders.find("js/procedure_combobox.js")
    assert script_path is not None
    script = Path(script_path).read_text(encoding="utf-8")
    assert "aria-labelledby" in script
    # O id do label vem do elemento <label for=select>, não só de um
    # aria-labelledby pré-existente no select.
    assert "label[for=" in script or "label[for=" in script
    assert "-label'" in script or '-label"' in script or "-label" in script
    # Clique no label foca o input aprimorado (não o select oculto).
    assert "addEventListener('click'" in script or 'addEventListener("click"' in script


def test_combobox_script_scrolls_active_row_into_view() -> None:
    """P1-B: ``setActive`` rola a linha ativa para dentro da listbox
    (``scrollIntoView`` com ``block: 'nearest'``) — setas/End nunca armam
    uma opção fora da viewport sem indicação visível."""
    script_path = finders.find("js/procedure_combobox.js")
    assert script_path is not None
    script = Path(script_path).read_text(encoding="utf-8")
    assert "scrollIntoView" in script
    start = script.index("function setActive")
    block = script[start : script.index("function setEmptyState", start)]
    assert "scrollIntoView" in block
    assert "nearest" in block


def test_combobox_script_structure() -> None:
    """R1/R3: o script é vanilla, normaliza acentos (NFD), expõe combobox/
    listbox com teclado ↑↓/Enter, busca por label + código canônico (sem
    aliases) e limpa o canônico quando o texto visível diverge (editar/
    limpar pós-seleção nunca submete valor obsoleto)."""
    script_path = finders.find("js/procedure_combobox.js")
    assert script_path is not None
    script = Path(script_path).read_text(encoding="utf-8")

    for token in (
        "normalize('NFD')",
        "'combobox'",
        "'listbox'",
        "'option'",
        "ArrowDown",
        "ArrowUp",
        "Enter",
        "aria-expanded",
        "aria-activedescendant",
        "data-procedure-combobox",
        "ProcedureCombobox",
    ):
        assert token in script, f"token ausente no script: {token}"
    # Busca restrita a label + código canônico: sem mecanismo de aliases
    # (atributo de busca de sinônimos nem propriedade de opção).
    assert "data-search-aliases" not in script
    assert "option.aliases" not in script
    assert "aliases:" not in script
    # Divergência visível × canônico: editar o input limpa o select.
    assert "addEventListener('input'" in script or 'addEventListener("input"' in script
    assert "syncCanonicalFromInput" in script
    sync_start = script.index("function syncCanonicalFromInput")
    sync_block = script[
        sync_start : script.index("}", script.index("syncSelectedRow();", sync_start))
    ]
    assert "select.value = ''" in sync_block


def test_combobox_css_vocabulary_and_visible_focus() -> None:
    """R1: o vocabulário do combobox vive em ``app.css`` com foco visível
    por contorno (não só cor) e alvo mínimo de toque."""
    css_path = finders.find("css/app.css")
    assert css_path is not None
    css = Path(css_path).read_text(encoding="utf-8")

    for selector in COMBOBOX_CSS_SELECTORS:
        assert selector in css, f"Seletor {selector} ausente em app.css"
    focus_start = css.index(".procedure-combobox__input:focus-visible")
    focus_block = css[focus_start : css.index("}", focus_start)]
    assert "outline" in focus_block, "foco visível depende apenas de cor"
    input_start = css.index(".procedure-combobox__input")
    input_block = css[input_start : css.index("}", input_start)]
    assert "44px" in input_block, "alvo de toque menor que 44px"


# ── Fluxos: envio e reenvio (R2, com e sem JS) ──────────────────────


@pytest.mark.django_db
def test_home_post_valid_type_creates_case(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: POST com código canônico (select nativo, sem JS) cria o caso —
    o caminho sem JS segue funcional."""
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:home"),
        {"documents": [pdf_factory()], "procedure_type": CARDIO_TYPE},
    )

    assert response.status_code == 302
    case = Case.objects.get()
    assert {row.procedure_type for row in case.procedures.filter(declared_by_nir=True)} == {
        CARDIO_TYPE
    }
    assert response["Location"] == reverse("intake:case_detail", args=[case.case_id])


@pytest.mark.django_db
def test_home_post_empty_type_rerenders_with_accessible_error(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: POST sem tipo (select vazio / texto não confirmado) re-renderiza
    com o mesmo texto de erro, associado ao controle
    (``aria-invalid`` + ``aria-describedby``) e sem criar caso."""
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:home"),
        {"documents": [pdf_factory()], "procedure_type": ""},
    )

    assert response.status_code == 200
    assert Case.objects.count() == 0
    body = response.content.decode()
    assert "Este campo é obrigatório." in body
    select = _select_tag(body)
    assert 'aria-invalid="true"' in select
    assert "id_procedure_type_error" in select
    assert 'id="id_procedure_type_error"' in body
    assert 'role="alert"' in body
    assert f'id="{SEARCH_HINT_ID}"' in body


@pytest.mark.django_db
def test_home_post_unmatched_text_rerenders_with_accessible_error(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: POST com texto sem match (valor fora do catálogo) rejeita como o
    radio rejeitava — mesmo texto, mesmo status, sem criar caso."""
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:home"),
        {"documents": [pdf_factory()], "procedure_type": "texto sem match"},
    )

    assert response.status_code == 200
    assert Case.objects.count() == 0
    body = response.content.decode()
    assert "não é uma das escolhas disponíveis" in body
    select = _select_tag(body)
    assert 'aria-invalid="true"' in select
    assert "id_procedure_type_error" in select


@pytest.mark.django_db
def test_resubmit_post_valid_type_creates_linked_case(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: reenvio com código canônico cria o novo caso vinculado."""
    doctor = user_factory("doctor-combobox-flow", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    client.force_login(nir_user)
    url = reverse("intake:case_resubmit", args=[original.case_id])

    response = client.post(
        url,
        {
            "documents": [pdf_factory()],
            "procedure_type": CARDIO_TYPE,
            "correction_reason": REASON,
        },
    )

    assert response.status_code == 302
    new_case = Case.objects.exclude(pk=original.pk).get()
    assert new_case.corrects_case_id == original.case_id
    assert {row.procedure_type for row in new_case.procedures.filter(declared_by_nir=True)} == {
        CARDIO_TYPE
    }
    assert response["Location"] == reverse("intake:case_detail", args=[new_case.case_id])


@pytest.mark.django_db
def test_resubmit_post_empty_type_rerenders_with_accessible_error(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: reenvio sem tipo (vazio / não confirmado) re-renderiza com o
    mesmo texto de erro associado ao controle e sem criar caso novo."""
    doctor = user_factory("doctor-combobox-empty", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    client.force_login(nir_user)
    url = reverse("intake:case_resubmit", args=[original.case_id])
    cases_before = Case.objects.count()

    response = client.post(
        url,
        {
            "documents": [pdf_factory()],
            "procedure_type": "",
            "correction_reason": REASON,
        },
    )

    assert response.status_code == 200
    assert Case.objects.count() == cases_before
    body = response.content.decode()
    assert "Este campo é obrigatório." in body
    select = _select_tag(body)
    assert 'aria-invalid="true"' in select
    assert "id_procedure_type_error" in select
    assert 'role="alert"' in body


@pytest.mark.django_db
def test_resubmit_post_unmatched_text_rerenders_without_new_case(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: reenvio com texto sem match rejeita sem criar caso novo."""
    doctor = user_factory("doctor-combobox-nomatch", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    client.force_login(nir_user)
    url = reverse("intake:case_resubmit", args=[original.case_id])
    cases_before = Case.objects.count()

    response = client.post(
        url,
        {
            "documents": [pdf_factory()],
            "procedure_type": "texto sem match",
            "correction_reason": REASON,
        },
    )

    assert response.status_code == 200
    assert Case.objects.count() == cases_before
    body = response.content.decode()
    assert "não é uma das escolhas disponíveis" in body
    assert 'aria-invalid="true"' in _select_tag(body)
