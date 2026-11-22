"""Testes de filtros server-side, cards e partial de "meus casos"
(ui-parity-queues, slice 003, R1–R3).

R1: cards ``case-card`` compactos (OLI-004) com nome, nº de ocorrência,
status, procedimentos e retenção (quando houver); a navegação das abas
é só o header (``Meus Casos``/``Casos Encerrados`` com contadores) —
sem pills internas duplicadas.
R2: filtros server-side ``q`` (nº de ocorrência, ≥3 chars; mais curtos
ignorados, nunca zerando por erro), ``status`` e ``procedure_type``
(valores do catálogo de 14 tipos do ``IntakeUploadForm``) compostos por
AND e preservados entre página cheia e partial; escopo por criador em
qualquer combinação (caso alheio nunca aparece); filtros aplicam ANTES
dos sufixos de desambiguação (D4, como as abas); sem paginação.
R3: partial ``intake:my_cases_partial`` (mesmo fragmento do server-side,
D1) com auto-refresh htmx de 20 s preservando aba e filtros; caso fora
do filtro não aparece no refresh.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseStatus
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.intake.services import create_case_with_documents

NIR_ROLE = "nir"


@pytest.fixture(autouse=True)
def _creation_without_processing() -> Iterator[None]:
    """Criação sem processamento: estados controlados pelo teste."""

    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


def _create_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    *,
    procedure_type: str = "art_perif",
    record_number: str = "",
    patient_name: str = "",
    patient_age: int | None = None,
    status: CaseStatus | None = None,
) -> Case:
    """Cria um caso NEW com 1 PDF e 1 tipo declarado, ajustando exibição."""
    case = create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(),
        procedure_type=procedure_type,
    )
    updates: dict[str, object] = {
        "agency_record_number": record_number,
        "patient_name": patient_name,
        "patient_age": patient_age,
    }
    if status is not None:
        updates["status"] = status
    Case.objects.filter(pk=case.pk).update(**updates)
    case.refresh_from_db()
    return case


def _card_slice(body: str, case_id: object) -> str:
    """Fatia o HTML do card do caso pelo marcador ``data-my-cases-card``.

    No padrão OLI-004 o href é o CTA final (``stretched-link``), não um
    ``<a>`` que envolve o card — o marcador do container é a fronteira.
    """
    chunks = body.split("data-my-cases-card")
    for chunk in chunks:
        if str(case_id) in chunk:
            return chunk
    raise AssertionError(f"card do caso {case_id} ausente no corpo")


# ── R1: cards compactos + navegação única no header ────────────────────


@pytest.mark.django_db
def test_cards_have_parity_markers(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/R4 (OLI-004): cards compactos 4/3/3/2 sem summary-box/waiting-time."""
    case = _create_case(
        nir_user,
        pdf_factory,
        record_number="5040778",
        patient_name="Paciente Cartao",
        patient_age=64,
    )

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    card = _card_slice(body, case.case_id)
    assert "case-card" in body
    assert "summary-box" not in card
    assert "waiting-time" not in card
    assert "list-group-item" not in card
    assert "status-badge" in card
    assert "patient-name" in card
    assert "Paciente Cartao" in card
    assert "64 a" in card
    assert "5040778" in card
    assert "Ver detalhes" in card
    assert "col-md-4" in card
    assert "col-md-3" in card
    assert "col-md-2" in card


@pytest.mark.django_db
def test_pills_with_counters_stay(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1 (OLI-004/D9; reenvio-corrigido-qualquer-nir slice 002): navegação
    única no header, sem pills internas — só ativos, com o contador 2 no
    header (Meus Casos); «Casos Encerrados» é a busca global, sem badge.
    """
    _create_case(nir_user, pdf_factory, record_number="5040778")
    _create_case(nir_user, pdf_factory, record_number="5040779")
    _create_case(nir_user, pdf_factory, record_number="5040780", status=CaseStatus.CLEANED)

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    assert "Meus Casos" in body
    assert "Casos Encerrados" in body
    assert ">Ativos<" not in body
    assert 'aria-label="Filtro pela situação do caso"' not in body
    assert 'href="/intake/cases/?tab=active"' in body
    assert 'href="/intake/cases/?tab=closed"' not in body
    # OLI-005 (D11): contador único por data-count + pseudo-elemento;
    # sem badge <span> inline no link (o número não é texto do DOM).
    assert 'data-count="2"' in body
    assert 'data-count="1"' not in body
    assert "nav-count-badge--neutral" in body
    assert ">2</span>" not in body
    assert ">1</span>" not in body


@pytest.mark.django_db
def test_filter_form_with_catalog_options(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/R2: formulário com ``q``/``status``/``procedure_type`` (14 tipos).

    Os valores do filtro de tipo são os do catálogo (mesmos do
    ``IntakeUploadForm.procedure_type``) — nenhum valor inventado.
    """
    _create_case(nir_user, pdf_factory)

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    assert 'name="q"' in body
    assert 'name="status"' in body
    assert 'name="procedure_type"' in body
    assert 'id="my-cases-content"' in body
    assert 'hx-trigger="every 20s"' in body
    partial_url = reverse("intake:my_cases_partial")
    assert f'hx-get="{partial_url}' in body
    for profile in PROCEDURE_PROFILES:
        assert f'value="{profile.procedure_type}"' in body
    assert body.count('value="art_perif"') >= 1


# ── R2: filtros server-side ─────────────────────────────────────────────


@pytest.mark.django_db
def test_q_filters_by_occurrence_number(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: ``q`` (≥3 chars) filtra pelo nº de ocorrência."""
    matching = _create_case(nir_user, pdf_factory, record_number="5040778")
    other = _create_case(nir_user, pdf_factory, record_number="5040999")

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases"), {"q": "5040778"}).content.decode()

    assert str(matching.case_id) in body
    assert str(other.case_id) not in body


@pytest.mark.django_db
def test_q_shorter_than_three_chars_is_ignored(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: ``q`` com <3 chars é ignorado (não quebra nem zera a lista)."""
    first = _create_case(nir_user, pdf_factory, record_number="5040778")
    second = _create_case(nir_user, pdf_factory, record_number="5040999")

    client.force_login(nir_user)
    response = client.get(reverse("intake:my_cases"), {"q": "50"})
    body = response.content.decode()

    assert response.status_code == 200
    assert str(first.case_id) in body
    assert str(second.case_id) in body


@pytest.mark.django_db
def test_status_filter(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: ``status`` filtra pelos estados do caso."""
    failed = _create_case(nir_user, pdf_factory, record_number="5040778", status=CaseStatus.FAILED)
    fresh = _create_case(nir_user, pdf_factory, record_number="5040999")

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases"), {"status": CaseStatus.FAILED}).content.decode()

    assert str(failed.case_id) in body
    assert str(fresh.case_id) not in body


@pytest.mark.django_db
def test_procedure_type_filter(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: ``procedure_type`` filtra pelo tipo declarado (catálogo HMD)."""
    matching = _create_case(
        nir_user, pdf_factory, procedure_type="cat_cardiaco", record_number="5040778"
    )
    other = _create_case(nir_user, pdf_factory, procedure_type="art_perif", record_number="5040999")

    client.force_login(nir_user)
    body = client.get(
        reverse("intake:my_cases"), {"procedure_type": "cat_cardiaco"}
    ).content.decode()

    assert str(matching.case_id) in body
    assert str(other.case_id) not in body


@pytest.mark.django_db
def test_filters_compose_with_and(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: ``q`` + ``status`` + ``procedure_type`` compõem por AND."""
    everything = _create_case(
        nir_user,
        pdf_factory,
        procedure_type="cat_cardiaco",
        record_number="5040778",
        status=CaseStatus.FAILED,
    )
    wrong_status = _create_case(
        nir_user,
        pdf_factory,
        procedure_type="cat_cardiaco",
        record_number="5040779",
    )
    wrong_type = _create_case(
        nir_user,
        pdf_factory,
        procedure_type="art_perif",
        record_number="5040780",
        status=CaseStatus.FAILED,
    )

    client.force_login(nir_user)
    body = client.get(
        reverse("intake:my_cases"),
        {
            "q": "504077",
            "status": CaseStatus.FAILED,
            "procedure_type": "cat_cardiaco",
        },
    ).content.decode()

    assert str(everything.case_id) in body
    assert str(wrong_status.case_id) not in body
    assert str(wrong_type.case_id) not in body


@pytest.mark.django_db
def test_cross_creator_with_filters_is_empty(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: caso ALHEIO casando todos os filtros nunca aparece (escopo intacto)."""
    other_nir = user_factory("nir-alheio-filtro", NIR_ROLE)
    foreign = _create_case(
        other_nir,
        pdf_factory,
        procedure_type="cat_cardiaco",
        record_number="5040778",
        status=CaseStatus.FAILED,
    )
    own = _create_case(
        nir_user,
        pdf_factory,
        procedure_type="cat_cardiaco",
        record_number="5040778",
        status=CaseStatus.FAILED,
    )

    client.force_login(nir_user)
    body = client.get(
        reverse("intake:my_cases"),
        {
            "q": "5040778",
            "status": CaseStatus.FAILED,
            "procedure_type": "cat_cardiaco",
        },
    ).content.decode()

    assert str(own.case_id) in body
    assert str(foreign.case_id) not in body


@pytest.mark.django_db
def test_filters_apply_before_disambiguation_suffix(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/D4: o nº duplicado FORA do filtro não gera sufixo no restante.

    Dois casos com o mesmo nº; filtrando por status só um permanece — sem
    sufixo curto (os filtros aplicam antes do cálculo, como as abas).
    """
    kept = _create_case(nir_user, pdf_factory, record_number="5040778", status=CaseStatus.FAILED)
    filtered_out = _create_case(nir_user, pdf_factory, record_number="5040778")

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases"), {"status": CaseStatus.FAILED}).content.decode()

    card = _card_slice(body, kept.case_id)
    assert "Nº de ocorrência: <strong>5040778</strong>" in card
    visible = re.sub(r'href="[^"]*"', "", card)
    assert kept.short_id not in visible
    assert str(filtered_out.case_id) not in body


@pytest.mark.django_db
def test_filters_preserved_in_partial_url(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/D4 (reenvio-corrigido-qualquer-nir slice 002): o ``?tab=`` legado cai
    na default de ativos e os filtros viajam na URL do partial."""
    _create_case(nir_user, pdf_factory, record_number="5040778")

    client.force_login(nir_user)
    body = client.get(
        reverse("intake:my_cases"),
        {
            "tab": "closed",
            "q": "5040778",
            "status": CaseStatus.NEW,
            "procedure_type": "art_perif",
        },
    ).content.decode()

    # Django escapa ``&`` como ``&amp;`` nos atributos — a URL é a mesma.
    assert (
        f'hx-get="{reverse("intake:my_cases_partial")}?tab=active&amp;q=5040778'
        f'&amp;status=NEW&amp;procedure_type=art_perif"' in body
    )
    # O formulário preserva os valores digitados/escolhidos.
    assert 'value="5040778"' in body
    assert re.search(r'value="NEW" selected', body) is not None
    assert re.search(r'value="art_perif" selected', body) is not None


# ── R3: partial + auto-refresh ──────────────────────────────────────────


@pytest.mark.django_db
def test_partial_renders_same_fragment(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3/D1: o partial renderiza SÓ o fragmento (sem a página cheia)."""
    case = _create_case(
        nir_user,
        pdf_factory,
        record_number="5040778",
        patient_name="Paciente Parcial",
        patient_age=71,
    )

    client.force_login(nir_user)
    response = client.get(reverse("intake:my_cases_partial"))
    body = response.content.decode()

    assert response.status_code == 200
    assert str(case.case_id) in body
    assert "Paciente Parcial" in body
    assert "71 a" in body
    assert "case-card" in body
    # Fragmento apenas (OLI-004): inclui o cabeçalho neutro da lista, mas
    # sem o título da página, o form de filtros ou o CTA do topo.
    assert "Meus Casos" in body
    assert "Todos os seus encaminhamentos" not in body
    assert 'name="q"' not in body
    assert "Enviar relatório" not in body


@pytest.mark.django_db
def test_partial_respects_filters_and_tab(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: o partial aplica os mesmos filtros/aba da página cheia."""
    matching = _create_case(
        nir_user,
        pdf_factory,
        procedure_type="cat_cardiaco",
        record_number="5040778",
        status=CaseStatus.FAILED,
    )
    wrong_type = _create_case(
        nir_user, pdf_factory, record_number="5040999", status=CaseStatus.FAILED
    )
    closed = _create_case(
        nir_user,
        pdf_factory,
        procedure_type="cat_cardiaco",
        record_number="5040778",
        status=CaseStatus.CLEANED,
    )

    client.force_login(nir_user)
    body = client.get(
        reverse("intake:my_cases_partial"),
        {
            "q": "5040778",
            "status": CaseStatus.FAILED,
            "procedure_type": "cat_cardiaco",
        },
    ).content.decode()

    assert str(matching.case_id) in body
    assert str(wrong_type.case_id) not in body
    # Caso fora do filtro (inclusive CLEANED em outra aba) não aparece.
    assert str(closed.case_id) not in body


@pytest.mark.django_db
def test_partial_off_filter_case_never_appears(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: caso criado fora do filtro ativo não aparece no refresh."""
    client.force_login(nir_user)
    body = client.get(
        reverse("intake:my_cases_partial"), {"status": CaseStatus.FAILED}
    ).content.decode()
    assert "case-card" not in body

    off_filter = _create_case(nir_user, pdf_factory, record_number="5040778")
    body = client.get(
        reverse("intake:my_cases_partial"), {"status": CaseStatus.FAILED}
    ).content.decode()

    assert str(off_filter.case_id) not in body
    assert "case-card" not in body


@pytest.mark.django_db
def test_partial_empty_for_cross_creator(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3/R2: o partial também respeita o escopo por criador."""
    other_nir = user_factory("nir-alheio-parcial", NIR_ROLE)
    foreign = _create_case(other_nir, pdf_factory, record_number="5040778")

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases_partial"), {"q": "5040778"}).content.decode()

    assert str(foreign.case_id) not in body
    assert "case-card" not in body


@pytest.mark.django_db
def test_partial_requires_nir_role(
    client: Client,
    user_factory: Callable[[str, str], User],
) -> None:
    """R3: papel ativo fora de nir → 403 também no partial."""
    doctor = user_factory("doctor-parcial", "doctor")

    client.force_login(doctor)

    assert client.get(reverse("intake:my_cases_partial")).status_code == 403
