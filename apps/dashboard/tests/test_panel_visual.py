"""Testes visuais estreitos do painel (ui-parity-intake-detail, slice 004).

R1/R2 (D4): a apresentação do painel segue o molde ats-web
(``templates/dashboard/index.html`` — toolbar de período, cards de
métrica e lista em ``case-card``) com OS MESMOS números e filtros de
antes. Estes testes cobrem só marcadores de conteúdo/estrutura do
template novo e a igualdade "valores renderizados = métricas" — NUNCA
assertam regras de contagem: a prova de que nenhuma query mudou é a
suíte existente (``test_metrics.py``/``test_views.py``) verde SEM
alteração nenhuma nos seus asserts. Nenhum ``.py`` de métrica/view é
tocado por este slice.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.cases.models import Case, CaseProcedure, CaseStatus, SchedulingUnit
from apps.dashboard.metrics import compute_summary

SYSTEM_ROLE = "system"
MANAGER_ROLE = "manager"

PATIENT_NAME = "Maria da Silva"
LONG_PATIENT_NAME = "Maria Joaquina Albuquerque Vasconcelos Ribeiro de Almeida e Sousa"
RECORD_NUMBER = "33345"


def _make_user(username: str) -> User:
    """Usuário com papel gerencial (o painel é exclusivo de manager/admin)."""
    role, _ = Role.objects.get_or_create(name=MANAGER_ROLE)
    user = User.objects.create_user(username=username, password="senha-teste")
    user.roles.add(role)
    return user


def _confirmed_case(creator: User, unit: int) -> Case:
    """Caso com agendamento confirmado (``FINAL_REPLY_POSTED``, source agendado)."""
    case = Case.objects.create(created_by=creator)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    case.record_doctor_decision(accepted=True, user=None, role=SYSTEM_ROLE)
    case.request_scheduling(user=None, role=SYSTEM_ROLE)
    case.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    case.confirm_scheduling(user=None, role=SYSTEM_ROLE)
    case.scheduled_unit = unit
    case.save(update_fields=["scheduled_unit"])
    case.post_final_reply(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


# Rótulos dos cards de resumo (texto do template) -> campo de ``compute_summary``.
_CARD_LABEL_TO_FIELD = {
    "Total": "total",
    "Agendados": "agendados",
    "Negados": "negados",
    "Em andamento": "em_andamento",
    "Encerrados": "encerrados",
    "Encerr. administrativos": "administratively_closed",
}

_SUMMARY_CARD_RE = re.compile(
    r'<div class="card metrics-summary-card[^"]*">\s*<div class="card-body">\s*'
    r'<div class="fs-3 fw-bold[^"]*">\s*(\d+)\s*</div>\s*'
    r'<div class="text-muted small text-uppercase">\s*([^<]+?)\s*</div>'
)


def _summary_card_values(html: str) -> dict[str, int]:
    """Valor exibido em cada card de resumo, associado ao seu rótulo.

    Extrai (rótulo -> valor) card a card, nunca por busca na página
    inteira — valores repetidos ou trocados entre cards não passam.
    """
    return {label.strip(): int(value) for value, label in _SUMMARY_CARD_RE.findall(html)}


@pytest.mark.django_db
def test_period_toolbar_uses_ats_web_markers(client: Client) -> None:
    """R1: seletor de período no padrão ats-web (card + toolbar + opções)."""
    client.force_login(_make_user("gestor-visual-toolbar"))

    response = client.get(reverse("dashboard:home"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "metrics-period-card" in content
    assert 'id="metrics-period-selector"' in content
    assert "metrics-period-toolbar" in content
    assert "metrics-period-options" in content
    assert "metrics-period-option" in content
    assert "metrics-period-title" in content
    assert "Período das métricas" in content
    # O período ativo (default ``hoje``) é marcado como no molde.
    assert "is-active" in content
    assert 'aria-current="page"' in content
    # Os quatro períodos do HMD continuam como opções do seletor.
    for label in ("Hoje", "7 dias", "30 dias", "Tudo"):
        assert label in content


@pytest.mark.django_db
def test_metric_cards_use_visual_markers_with_same_labels(client: Client) -> None:
    """R1: cards de resumo no padrão visual, com os seis rótulos de antes."""
    client.force_login(_make_user("gestor-visual-cards"))

    response = client.get(reverse("dashboard:home"))
    content = response.content.decode()

    assert response.status_code == 200
    assert content.count("metrics-summary-card") == 6
    for label in (
        "Total",
        "Agendados",
        "Negados",
        "Em andamento",
        "Encerrados",
        "Encerr. administrativos",
    ):
        assert label in content


@pytest.mark.django_db
def test_rendered_metric_values_equal_metrics_module(client: Client) -> None:
    """R1/R2: os números RENDERIZADOS são os de ``compute_summary`` (oráculo).

    População coerente (1 agendado + 1 em andamento): o contexto da view
    e o texto visível dos cards exibem exatamente os valores do módulo
    de métricas — divergência aqui seria mudança acidental de query.
    """
    creator = _make_user("gestor-visual-valores")
    _confirmed_case(creator, SchedulingUnit.UNIT_1)
    Case.objects.create(created_by=creator)  # NEW — em andamento
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"period": "hoje"})
    content = response.content.decode()
    expected = compute_summary("hoje")

    assert response.status_code == 200
    assert response.context["summary"] == expected
    assert expected == {
        "total": 2,
        "agendados": 1,
        "negados": 0,
        "em_andamento": 1,
        "encerrados": 0,
        "administratively_closed": 0,
    }
    # Oráculo por card: cada card exibe o campo correspondente —
    # associação rótulo->valor, robusta a valores repetidos/trocados
    # (busca por substring na página inteira não detectaria a troca).
    cards = _summary_card_values(content)
    assert set(cards) == set(_CARD_LABEL_TO_FIELD)
    for label, field in _CARD_LABEL_TO_FIELD.items():
        assert cards[label] == expected[field], label
    # A tabela por tipo e o tempo médio seguem os rótulos do painel.
    assert "Procedimentos por tipo" in content
    assert "Agendamentos por unidade" in content
    assert "Tempo médio até decisão médica" in content


def test_summary_card_oracle_catches_swapped_values() -> None:
    """Sensibilidade do oráculo: valores trocados entre cards falham.

    Fixture determinística no mesmo markup do template (Total=2,
    Agendados=1 trocados): a busca por substring na página inteira
    ainda encontraria ambos os valores, mas a associação por card
    detecta a troca — é esse o oráculo usado acima.
    """
    swapped = (
        '<div class="card metrics-summary-card h-100 text-center">'
        '<div class="card-body">'
        '<div class="fs-3 fw-bold">1</div>'
        '<div class="text-muted small text-uppercase">Total</div>'
        "</div></div>"
        '<div class="card metrics-summary-card h-100 text-center border-success">'
        '<div class="card-body">'
        '<div class="fs-3 fw-bold text-success">2</div>'
        '<div class="text-muted small text-uppercase">Agendados</div>'
        "</div></div>"
    )
    cards = _summary_card_values(swapped)
    # A busca antiga (substring na página) passaria nesta fixture...
    assert "1" in swapped and "2" in swapped
    # ...mas a associação por card rejeita a troca.
    assert cards != {"Total": 2, "Agendados": 1}
    assert cards["Total"] != 2
    assert cards["Agendados"] != 1


@pytest.mark.django_db
def test_period_links_preserve_list_filters(client: Client) -> None:
    """R1/R2: trocar o período na toolbar não descarta os filtros da lista."""
    client.force_login(_make_user("gestor-visual-filtros"))
    today = timezone.localdate().isoformat()

    filtered = client.get(
        reverse("dashboard:home"), {"procedure_type": "art_perif", "date_from": today}
    )
    body = filtered.content.decode()

    assert filtered.status_code == 200
    assert f"?period=7d&scope=todos&amp;procedure_type=art_perif&amp;date_from={today}" in body
    # Não-vacuidade: o link vive dentro da toolbar de período.
    toolbar_at = body.index("metrics-period-toolbar")
    link_at = body.index("?period=7d")
    cards_at = body.index("metrics-summary-card")
    assert toolbar_at < link_at < cards_at


@pytest.mark.django_db
def test_case_list_uses_case_cards(client: Client) -> None:
    """R1: a lista de casos renderiza em ``case-card`` (não em tabela)."""
    creator = _make_user("gestor-visual-lista")
    case = _confirmed_case(creator, SchedulingUnit.UNIT_1)
    case.patient_name = PATIENT_NAME
    case.patient_age = 84
    case.agency_record_number = RECORD_NUMBER
    case.save(update_fields=["patient_name", "patient_age", "agency_record_number"])
    CaseProcedure.objects.create(case=case, procedure_type="art_perif", declared_by_nir=True)
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"))
    content = response.content.decode()
    list_section = content.split("Casos do período")[-1]

    assert response.status_code == 200
    assert "case-card" in list_section
    assert f'<span class="case-patient-name">{PATIENT_NAME}</span>' in list_section
    assert "84 a" in list_section
    assert RECORD_NUMBER in list_section
    assert "Arteriografia periférica" in list_section
    assert reverse("dashboard:case_detail", args=[case.case_id]) in list_section
    assert "Detalhes" in list_section


@pytest.mark.django_db
def test_case_list_long_patient_name_not_truncated(client: Client) -> None:
    """P1: nome longo renderiza COMPLETO, sem truncamento (regressão visual).

    A identificação do paciente não pode usar ``text-truncate``: em
    nome longo/viewport estreita o Bootstrap ocultaria a identificação
    sem forma de expandir na lista (contrato do slice: identificação
    completa preservada).
    """
    creator = _make_user("gestor-visual-nome-longo")
    case = _confirmed_case(creator, SchedulingUnit.UNIT_1)
    case.patient_name = LONG_PATIENT_NAME
    case.patient_age = 84
    case.agency_record_number = RECORD_NUMBER
    case.save(update_fields=["patient_name", "patient_age", "agency_record_number"])
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"))
    content = response.content.decode()
    list_section = content.split("Casos do período")[-1]

    assert response.status_code == 200
    # O nome longo aparece inteiro na lista.
    assert LONG_PATIENT_NAME in list_section
    # E o contêiner da identificação não tem mecanismo de truncamento.
    match = re.search(r'<div class="([^"]*)">\s*<span class="case-patient-name">', list_section)
    assert match is not None
    assert "text-truncate" not in match.group(1)


@pytest.mark.django_db
def test_case_card_filter_form_and_detail_link_preserved(client: Client) -> None:
    """R1/R2: formulário de filtros e link [Detalhes] intactos nos cards."""
    creator = _make_user("gestor-visual-form")
    case = Case.objects.create(created_by=creator)
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"))
    content = response.content.decode()

    assert response.status_code == 200
    # Campos do formulário de filtros (nomes canônicos do GET).
    for field in ("scope", "status", "procedure_type", "date_from", "date_to", "q", "period"):
        assert f'name="{field}"' in content
    # O link de detalhes carrega os filtros vigentes (retorno preservado).
    detail_href = f"{reverse('dashboard:case_detail', args=[case.case_id])}?period=hoje"
    assert detail_href in content
    # O formulário vive na seção da lista (antes do primeiro card).
    list_section = content.split("Casos do período", 1)[-1]
    assert list_section.index("</form>") < list_section.index("case-card")
