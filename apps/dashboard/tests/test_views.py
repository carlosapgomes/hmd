"""Testes da view do painel gerencial (slice 003 + painel-gerencial-e-home/001).

Cobre ``dashboard:home`` sob o gate por papel ativo (change
painel-gerencial-e-home, slice 001, D1): ``manager``/``admin`` → 200 com as
métricas do período; ``nir``/``doctor``/``scheduler`` → 403 (guard na ROTA, não
só no menu); anônimo → redirect ao login. A cobertura anterior continua
(período validado contra o conjunto aceito — inválido/ausente → ``hoje`` —,
render zero-PHI e rótulos de unidade da fonte única): esses usuários passam a
logar com papel gerencial (R3). As fixtures dirigem casos aos estados reais
pelas operações públicas da FSM.

Slice 003 do change painel-lista-encerramento (R1/R2, D1/D3): as rotas de
encerramento administrativo (``admin_close_confirm``/``admin_close``) com 403
paramétrico, 404 e recusas sem efeito no caso.

Slice 002 do change painel-ats-parity (R1/R2, D2/D5b): a lista ganha paridade com
o dashboard do ats-web — filtros que compõem por AND (``scope``/``status``/
``procedure_type``/``q``/``date_from``/``date_to``), default SEM filtros
explícitos = hoje/hoje + escopo ``todos`` (todas as situações, inclusive
``CLEANED``), cards com a identificação do paciente (política corrigida pelo
dono: zero-PHI vale para o perímetro EXTERNO/LLM — a UI interna de funcionários
mostra o paciente) e o detail REAL do caso (``dashboard:case_detail``) que
hospeda a ação de encerramento administrativo.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.cases.closure import ADMINISTRATIVE_CLOSURE_REASONS
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseProcedure, CaseStatus, DoctorDisposition, SchedulingUnit
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.dashboard.case_labels import CASE_NEXT_STEP_LABELS, CASE_RESULT_LABELS
from apps.dashboard.views import CASE_LIST_PAGE_SIZE, CLOSE_SUCCESS_MESSAGE

SYSTEM_ROLE = "system"
MANAGER_ROLE = "manager"
ADMIN_ROLE = "admin"

# Papéis ativos sem acesso ao painel nem ao link da navbar (R1/R2).
NON_MANAGEMENT_ROLES = ("nir", "doctor", "scheduler")

PATIENT_NAME = "Maria da Silva"
RECORD_NUMBER = "33345"
# Data de nascimento da fixture zero-PHI (nenhum formato pode vazar na página).
PATIENT_BIRTH_DATE = date(1970, 5, 20)
PATIENT_BIRTH_DATE_TEXTS = ("20/05/1970", "1970-05-20")
# Unidade de origem do cabeçalho SESAB (change sesab-header-extraction).
ORIGIN_UNIT = "HELN - HOSPITAL ESTADUAL DO LESTE NORTE"

# Rótulos configurados do cenário de override (change unit-labels-env, R5).
CONFIGURED_UNIT_LABELS = {1: "Hemodinâmica HGRS", 2: "Unidade Satélite"}


def _make_user(username: str, role_name: str = MANAGER_ROLE) -> User:
    """Usuário com um papel (para o middleware resolver o papel ativo)."""
    role, _ = Role.objects.get_or_create(name=role_name)
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


# ── R1: acesso por papel ativo ─────────────────────────────────────────────


@pytest.mark.django_db
def test_dashboard_ok_for_manager(client: Client) -> None:
    """R1: papel ativo manager → 200 com as métricas do período."""
    client.force_login(_make_user("gestor-painel"))

    response = client.get(reverse("dashboard:home"))

    assert response.status_code == 200
    assert response.context["period"] == "hoje"
    assert "Painel gerencial" in response.content.decode()


@pytest.mark.django_db
def test_dashboard_ok_for_admin(client: Client) -> None:
    """R1: papel ativo admin → 200 com as métricas do período."""
    client.force_login(_make_user("admin-painel", ADMIN_ROLE))

    response = client.get(reverse("dashboard:home"), {"period": "30d"})

    assert response.status_code == 200
    assert response.context["period"] == "30d"


@pytest.mark.django_db
@pytest.mark.parametrize("role", NON_MANAGEMENT_ROLES)
def test_dashboard_403_for_other_roles(client: Client, role: str) -> None:
    """R1: papel ativo fora de manager/admin → 403 na ROTA (não só no menu)."""
    client.force_login(_make_user(f"fora-do-painel-{role}", role))

    response = client.get(reverse("dashboard:home"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_dashboard_anonymous_redirects(client: Client) -> None:
    """R1: anônimo → redirect ao login (composição login_required + role_required)."""
    response = client.get(reverse("dashboard:home"))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


# ── R2: período validado ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_invalid_period_defaults(client: Client) -> None:
    """R5: período inválido cai em ``hoje``."""
    client.force_login(_make_user("gestor-periodo-invalido"))

    response = client.get(reverse("dashboard:home"), {"period": "semana-passada"})

    assert response.status_code == 200
    assert response.context["period"] == "hoje"


@pytest.mark.django_db
def test_valid_period_is_respected(client: Client) -> None:
    """R2: período aceito é preservado no contexto."""
    client.force_login(_make_user("gestor-periodo-valido"))

    response = client.get(reverse("dashboard:home"), {"period": "30d"})

    assert response.status_code == 200
    assert response.context["period"] == "30d"


# ── R3: dados de paciente — métricas sem, lista com (política corrigida) ──


@pytest.mark.django_db
def test_metrics_section_has_no_patient_data(client: Client) -> None:
    """R3/D2 (política corrigida do dono): a SEÇÃO DE MÉTRICAS segue sem dados
    de paciente (nome, nº de registro, nascimento) — o invariante próprio da
    lista é REVERTIDO: o card passa a exibir a identificação do paciente.

    Não-vacuidade dupla: nome e nº de ocorrência estão na PÁGINA (card) e
    ausentes no trecho de métricas (antes do card da lista).
    """
    creator = _make_user("gestor-zero-phi")
    case = _confirmed_case(creator, SchedulingUnit.UNIT_1)
    case.patient_name = PATIENT_NAME
    case.patient_birth_date = PATIENT_BIRTH_DATE
    case.agency_record_number = RECORD_NUMBER
    case.save(update_fields=["patient_name", "patient_birth_date", "agency_record_number"])
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"))
    content = response.content.decode()
    metrics_section = content.split("Casos do período")[0]

    assert response.status_code == 200
    # A lista identifica o paciente (flip do invariante).
    assert PATIENT_NAME in content
    assert RECORD_NUMBER in content
    # As métricas continuam sem qualquer dado de paciente.
    assert PATIENT_NAME not in metrics_section
    assert RECORD_NUMBER not in metrics_section
    for birth_date_text in PATIENT_BIRTH_DATE_TEXTS:
        assert birth_date_text not in metrics_section
    assert "Painel gerencial" in content


@pytest.mark.django_db
def test_dashboard_renders_catalog_labels_and_avg_time(client: Client) -> None:
    """R3: tabela por tipo com labels do catálogo, unidade e tempo médio humanizado."""
    creator = _make_user("gestor-render")
    case = _confirmed_case(creator, SchedulingUnit.UNIT_1)
    decision = timezone.now()
    Case.objects.filter(pk=case.pk).update(created_at=decision - timedelta(hours=3, minutes=42))
    CaseProcedure.objects.create(
        case=case,
        procedure_type="art_perif",
        declared_by_nir=True,
        doctor_disposition=DoctorDisposition.APPROVED,
        doctor_decided_at=decision,
    )
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"period": "7d"})
    content = response.content.decode()

    assert response.status_code == 200
    assert "Arteriografia periférica" in content
    assert "Unidade 1" in content
    assert "3h 42m" in content


@pytest.mark.django_db
def test_dashboard_uses_configured_unit_labels(client: Client) -> None:
    """R2/R5: o painel exibe os rótulos de ``settings.UNIT_LABELS`` (fonte única)."""
    client.force_login(_make_user("gestor-labels"))

    with override_settings(UNIT_LABELS=CONFIGURED_UNIT_LABELS):
        response = client.get(reverse("dashboard:home"))

    content = response.content.decode()
    assert response.status_code == 200
    assert "Hemodinâmica HGRS" in content
    assert "Unidade Satélite" in content
    assert "Unidade 1" not in content
    assert "Unidade 2" not in content


# ── R2: link da navbar ─────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("role", (MANAGER_ROLE, ADMIN_ROLE))
def test_navbar_panel_link_visible_for_management_roles(client: Client, role: str) -> None:
    """R2: link "Painel" na navbar com papel ativo manager/admin (mesma condição da rota)."""
    client.force_login(_make_user(f"navbar-painel-{role}", role))

    response = client.get(reverse("home"), follow=True)
    content = response.content.decode()

    assert response.status_code == 200
    assert reverse("dashboard:home") in content
    assert ">Painel</a>" in content


@pytest.mark.django_db
@pytest.mark.parametrize("role", NON_MANAGEMENT_ROLES)
def test_navbar_panel_link_absent_for_other_roles(client: Client, role: str) -> None:
    """R2: link "Painel" ausente para nir/doctor/scheduler (UI e rota não divergem)."""
    client.force_login(_make_user(f"navbar-fora-{role}", role))

    # ``follow`` mantém o teste não-vacuoso quando a home despachar por papel
    # (change painel-gerencial-e-home, slice 002): a página final também é
    # renderizada a partir de base.html.
    response = client.get(reverse("home"), follow=True)

    assert response.status_code == 200
    assert reverse("dashboard:home") not in response.content.decode()


@pytest.mark.django_db
def test_navbar_panel_link_absent_for_anonymous(client: Client) -> None:
    """R2: anônimo não vê o link "Painel" (a navbar autenticada não é renderizada)."""
    response = client.get(reverse("login"))

    assert response.status_code == 200
    assert reverse("dashboard:home") not in response.content.decode()


# ── R1: lista de casos — filtros e default (painel-ats-parity, slice 002) ──


def _short_id(case: Case) -> str:
    """Prefixo de 8 caracteres do uid exibido no card (sem nº de ocorrência)."""
    return str(case.case_id)[:8]


def _awaiting_doctor_case(creator: User) -> Case:
    """Caso no pipeline até ``AWAITING_DOCTOR`` (ativo, em andamento)."""
    case = Case.objects.create(created_by=creator)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    return case


def _cleaned_case(creator: User) -> Case:
    """Caso encerrado pela ciência do NIR (``CLEANED``)."""
    case = _confirmed_case(creator, SchedulingUnit.UNIT_1)
    case.nir_acknowledge(user=None, role=SYSTEM_ROLE)
    case.start_cleaning(user=None, role=SYSTEM_ROLE)
    case.complete_cleaning(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.CLEANED
    return case


def _at(day: date, hour: int = 12, minute: int = 0) -> datetime:
    """Momento local determinístico do dia (fronteira de meia-noite evitada)."""
    naive = datetime.combine(day, time(hour=hour, minute=minute))
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _pin_created_at(case: Case, moment: datetime) -> None:
    """Fixa ``created_at`` sem passar pela FSM (a fonte imutável do painel)."""
    Case.objects.filter(pk=case.pk).update(created_at=moment)


def _detail_url(case: Case) -> str:
    """URL do detalhe do caso no painel (change painel-ats-parity, slice 002)."""
    return reverse("dashboard:case_detail", args=[case.case_id])


@pytest.mark.django_db
def test_list_default_shows_today_in_all_states(client: Client) -> None:
    """R1/D2/gate 1: sem filtros, a lista traz os casos de HOJE em TODOS os
    estados (incluindo ``CLEANED``) e deixa o de ONTEM fora — o default aplica
    hoje/hoje + escopo ``todos`` e NÃO herda o ``period`` das métricas."""
    creator = _make_user("gestor-lista-default")
    today = timezone.localdate()
    yesterday_case = Case.objects.create(created_by=creator)
    _pin_created_at(yesterday_case, _at(today - timedelta(days=1)))
    today_active = Case.objects.create(created_by=creator)
    _pin_created_at(today_active, _at(today, hour=11))
    today_cleaned = _cleaned_case(creator)
    _pin_created_at(today_cleaned, _at(today, hour=10))
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"))
    listed = [item["case_id"] for item in response.context["cases"]]

    assert response.status_code == 200
    assert listed == [today_active.case_id, today_cleaned.case_id]
    assert response.context["scope"] == "todos"
    assert response.context["date_from"] == today.isoformat()
    assert response.context["date_to"] == today.isoformat()


@pytest.mark.django_db
def test_list_scope_todos_includes_cleaned_cases(client: Client) -> None:
    """R1/spec: ``?scope=todos`` inclui os casos encerrados (filtro explícito →
    a janela de datas do default não se aplica)."""
    creator = _make_user("gestor-lista-todos")
    active = Case.objects.create(created_by=creator)
    cleaned = _cleaned_case(creator)
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"scope": "todos"})
    listed = {item["case_id"] for item in response.context["cases"]}

    assert response.status_code == 200
    assert listed == {active.case_id, cleaned.case_id}
    assert response.context["scope"] == "todos"


@pytest.mark.django_db
def test_list_invalid_scope_falls_back_to_default_scope(client: Client) -> None:
    """R1/D2: escopo desconhecido cai no default da paridade (``todos``) — o caso
    encerrado criado HOJE aparece (valor inválido não vira filtro)."""
    creator = _make_user("gestor-lista-escopo-invalido")
    cleaned = _cleaned_case(creator)
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"scope": "tudo"})

    assert response.status_code == 200
    assert response.context["scope"] == "todos"
    assert cleaned.case_id in {item["case_id"] for item in response.context["cases"]}


@pytest.mark.django_db
def test_list_status_filter_and_invalid_status(client: Client) -> None:
    """R1/spec: ``?status=`` filtra por status válido; valor fora das choices é ignorado."""
    creator = _make_user("gestor-lista-status")
    awaiting = _awaiting_doctor_case(creator)
    Case.objects.create(created_by=creator)  # NEW — em andamento
    client.force_login(creator)

    filtered = client.get(reverse("dashboard:home"), {"status": CaseStatus.AWAITING_DOCTOR})

    assert filtered.status_code == 200
    assert filtered.context["status"] == str(CaseStatus.AWAITING_DOCTOR)
    assert [item["case_id"] for item in filtered.context["cases"]] == [awaiting.case_id]

    ignored = client.get(reverse("dashboard:home"), {"status": "NAO_EXISTE"})

    assert ignored.context["status"] == ""
    assert len(ignored.context["cases"]) == 2


@pytest.mark.django_db
def test_list_search_by_record_number(client: Client) -> None:
    """R1/spec: ``?q=`` com 3+ caracteres casa o nº de ocorrência (``icontains``)."""
    creator = _make_user("gestor-busca-ocorrencia")
    target = Case.objects.create(created_by=creator, agency_record_number="2025000123")
    other = Case.objects.create(created_by=creator, agency_record_number="9999888877")
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"q": "00012"})

    assert response.status_code == 200
    assert [item["case_id"] for item in response.context["cases"]] == [target.case_id]
    assert other.case_id not in {item["case_id"] for item in response.context["cases"]}


@pytest.mark.django_db
def test_list_search_by_uid_prefix(client: Client) -> None:
    """R1/spec: ``?q=`` casa também o PREFIXO do uid do caso (``istartswith``)."""
    creator = _make_user("gestor-busca-uid")
    target = Case.objects.create(created_by=creator)
    other = Case.objects.create(created_by=creator)
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"q": _short_id(target)})

    assert response.status_code == 200
    assert [item["case_id"] for item in response.context["cases"]] == [target.case_id]
    assert other.case_id not in {item["case_id"] for item in response.context["cases"]}


@pytest.mark.django_db
def test_list_search_shorter_than_three_chars_is_ignored(client: Client) -> None:
    """R1/spec: busca com menos de 3 caracteres é ignorada (sem filtro de termo).

    O termo "20" É substring do nº de ocorrência do segundo caso — se o filtro
    fosse aplicado, ele desapareceria (o cenário discrimina).
    """
    creator = _make_user("gestor-busca-curta")
    first = Case.objects.create(created_by=creator, agency_record_number="2025")
    second = Case.objects.create(created_by=creator, agency_record_number="4025")
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"q": "20"})
    listed = {item["case_id"] for item in response.context["cases"]}

    assert response.status_code == 200
    assert response.context["q"] == "20"
    assert listed == {first.case_id, second.case_id}


@pytest.mark.django_db
def test_list_date_from_opens_the_window(client: Client) -> None:
    """R1/spec/gate 1: ``?date_from=`` (ISO) abre a janela — com filtro
    explícito o caso de ontem entra junto dos de hoje (default não se aplica)."""
    creator = _make_user("gestor-lista-date-from")
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    yesterday_case = Case.objects.create(created_by=creator)
    _pin_created_at(yesterday_case, _at(yesterday))
    today_case = Case.objects.create(created_by=creator)
    _pin_created_at(today_case, _at(today))
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"date_from": yesterday.isoformat()})
    listed = {item["case_id"] for item in response.context["cases"]}

    assert response.status_code == 200
    assert listed == {yesterday_case.case_id, today_case.case_id}
    assert response.context["date_from"] == yesterday.isoformat()
    assert response.context["date_to"] == ""


@pytest.mark.django_db
def test_list_date_to_bounds_the_window(client: Client) -> None:
    """R1/spec: ``?date_to=`` fecha a janela (só o caso de ontem, sem default)."""
    creator = _make_user("gestor-lista-date-to")
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    yesterday_case = Case.objects.create(created_by=creator)
    _pin_created_at(yesterday_case, _at(yesterday))
    today_case = Case.objects.create(created_by=creator)
    _pin_created_at(today_case, _at(today))
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"date_to": yesterday.isoformat()})
    listed = [item["case_id"] for item in response.context["cases"]]

    assert response.status_code == 200
    assert listed == [yesterday_case.case_id]
    assert response.context["date_to"] == yesterday.isoformat()
    assert response.context["date_from"] == ""


@pytest.mark.django_db
def test_list_invalid_iso_dates_are_ignored(client: Client) -> None:
    """R1/D2: data ISO inválida é tratada como ausente (sem 500 e sem filtro
    fantasma) — sem outro filtro explícito, o default hoje/hoje volta a valer."""
    creator = _make_user("gestor-lista-data-invalida")
    today = timezone.localdate()
    yesterday_case = Case.objects.create(created_by=creator)
    _pin_created_at(yesterday_case, _at(today - timedelta(days=1)))
    today_case = Case.objects.create(created_by=creator)
    _pin_created_at(today_case, _at(today))
    client.force_login(creator)

    response = client.get(
        reverse("dashboard:home"), {"date_from": "14/09/2026", "date_to": "2026-13-45"}
    )
    listed = [item["case_id"] for item in response.context["cases"]]

    assert response.status_code == 200
    assert listed == [today_case.case_id]
    assert response.context["date_from"] == today.isoformat()
    assert response.context["date_to"] == today.isoformat()


@pytest.mark.django_db
def test_list_inverted_dates_are_swapped(client: Client) -> None:
    """R1/D2: ``from > to`` normaliza por swap (sem intervalo vazio silencioso)."""
    creator = _make_user("gestor-lista-datas-invertidas")
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    yesterday_case = Case.objects.create(created_by=creator)
    _pin_created_at(yesterday_case, _at(yesterday))
    today_case = Case.objects.create(created_by=creator)
    _pin_created_at(today_case, _at(today))
    client.force_login(creator)

    response = client.get(
        reverse("dashboard:home"),
        {"date_from": today.isoformat(), "date_to": yesterday.isoformat()},
    )
    listed = {item["case_id"] for item in response.context["cases"]}

    assert response.status_code == 200
    assert listed == {yesterday_case.case_id, today_case.case_id}
    assert response.context["date_from"] == yesterday.isoformat()
    assert response.context["date_to"] == today.isoformat()


@pytest.mark.django_db
def test_list_procedure_type_filter_and_dropdown(client: Client) -> None:
    """R1/spec: ``?procedure_type=`` filtra pelo tipo DECLARADO pelo NIR e o
    formulário oferece o dropdown com os tipos do catálogo."""
    creator = _make_user("gestor-lista-tipo")
    perif = Case.objects.create(created_by=creator)
    CaseProcedure.objects.create(case=perif, procedure_type="art_perif", declared_by_nir=True)
    cardiaco = Case.objects.create(created_by=creator)
    CaseProcedure.objects.create(case=cardiaco, procedure_type="cat_cardiaco", declared_by_nir=True)
    # Row de tipo NÃO declarado pelo NIR não casa o filtro.
    undeclared = Case.objects.create(created_by=creator)
    CaseProcedure.objects.create(case=undeclared, procedure_type="art_perif", declared_by_nir=False)
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"procedure_type": "art_perif"})
    content = response.content.decode()

    assert response.status_code == 200
    assert [item["case_id"] for item in response.context["cases"]] == [perif.case_id]
    assert response.context["procedure_type"] == "art_perif"
    for profile in PROCEDURE_PROFILES:
        assert f'value="{profile.procedure_type}"' in content
        assert profile.label in content

    ignored = client.get(reverse("dashboard:home"), {"procedure_type": "nao_existe"})

    assert ignored.context["procedure_type"] == ""
    assert len(ignored.context["cases"]) == 3


@pytest.mark.django_db
def test_list_search_by_patient_name_composes_with_dates(client: Client) -> None:
    """R1/spec/gate 2: ``?q=`` casa o NOME do paciente (novo) e COMPÕE por AND
    com a janela de datas — homônimo de ontem não aparece no default de hoje."""
    creator = _make_user("gestor-busca-nome")
    today = timezone.localdate()
    target = Case.objects.create(created_by=creator, patient_name="Maria da Silva")
    _pin_created_at(target, _at(today))
    outside = Case.objects.create(created_by=creator, patient_name="Silva Antunes")
    _pin_created_at(outside, _at(today - timedelta(days=1)))
    other = Case.objects.create(created_by=creator, patient_name="João Souza")
    _pin_created_at(other, _at(today))
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"q": "silva"})
    listed = [item["case_id"] for item in response.context["cases"]]

    assert response.status_code == 200
    # Filtro explícito preserva o que veio: sem data informada NÃO há janela de
    # datas (o default hoje/hoje só vale quando nada é explícito).
    assert listed == [target.case_id, outside.case_id]
    assert other.case_id not in listed

    # Gate 2: compondo com a data, o homônimo de ONTEM não vaza.
    bounded = client.get(reverse("dashboard:home"), {"q": "silva", "date_from": today.isoformat()})
    bounded_listed = [item["case_id"] for item in bounded.context["cases"]]

    assert bounded_listed == [target.case_id]
    assert outside.case_id not in bounded_listed
    assert other.case_id not in bounded_listed


@pytest.mark.django_db
def test_list_card_shows_patient_identity_and_details_button(client: Client) -> None:
    """R2/spec: o card traz nome, idade, unidade de origem, exames, fase e
    data/hora de inserção, com o botão [Detalhes] (paridade ats-web)."""
    creator = _make_user("gestor-card-identidade")
    today = timezone.localdate()
    case = Case.objects.create(created_by=creator, agency_record_number=RECORD_NUMBER)
    _pin_created_at(case, _at(today, hour=8))
    case.patient_name = PATIENT_NAME
    case.patient_age = 84
    case.origin_unit = ORIGIN_UNIT
    case.save(update_fields=["patient_name", "patient_age", "origin_unit"])
    CaseProcedure.objects.create(case=case, procedure_type="art_perif", declared_by_nir=True)
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"))
    content = response.content.decode()

    assert response.status_code == 200
    assert f'<span class="case-patient-name">{PATIENT_NAME}</span>' in content
    assert "84 a" in content
    assert ORIGIN_UNIT in content
    assert RECORD_NUMBER in content
    assert "Arteriografia periférica" in content
    assert CASE_NEXT_STEP_LABELS[str(CaseStatus.NEW)] in content
    assert _at(today, hour=8).strftime("%d/%m/%Y %H:%M") in content
    assert reverse("dashboard:case_detail", args=[case.case_id]) in content


@pytest.mark.django_db
def test_list_card_zero_age_and_missing_identity_placeholder(client: Client) -> None:
    """R2: idade ``0`` é válida (``0 a`` EXIBE) e caso sem identificação mostra
    ``—`` no lugar do nome, sem sufixo de idade."""
    creator = _make_user("gestor-card-zero")
    newborn = Case.objects.create(created_by=creator, patient_name="RN de Ana", patient_age=0)
    Case.objects.create(created_by=creator)  # sem nome/idade
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"))
    content = response.content.decode()

    assert response.status_code == 200
    assert f'<span class="case-patient-name">{newborn.patient_name}</span>' in content
    assert "0 a" in content
    assert '<span class="case-patient-name">—</span>' in content
    # Só o recém-nascido tem idade: o caso sem demografia não ganha sufixo.
    assert content.count(" a</span>") == 1


@pytest.mark.django_db
def test_list_card_shows_case_data_and_next_step(client: Client) -> None:
    """R1: o card traz nº de ocorrência, status, tipos declarados, criação,
    resultado do desfecho e o próximo passo canônico (mapa do case_labels)."""
    creator = _make_user("gestor-lista-card")
    case = _confirmed_case(creator, SchedulingUnit.UNIT_1)
    case.agency_record_number = RECORD_NUMBER
    case.save(update_fields=["agency_record_number"])
    CaseProcedure.objects.create(case=case, procedure_type="art_perif", declared_by_nir=True)
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"))
    content = response.content.decode()

    assert response.status_code == 200
    assert RECORD_NUMBER in content
    assert CaseStatus.FINAL_REPLY_POSTED.label in content
    assert "Arteriografia periférica" in content
    assert CASE_NEXT_STEP_LABELS[str(CaseStatus.FINAL_REPLY_POSTED)] in content
    assert CASE_RESULT_LABELS[str(CaseStatus.SCHEDULING_CONFIRMED)] in content


@pytest.mark.django_db
def test_list_card_has_no_close_action_which_lives_in_detail(client: Client) -> None:
    """R2/spec: a ação de encerramento SAI do card e passa a viver no DETALHE —
    disponível para caso ativo e ausente em ``CLEANED``."""
    creator = _make_user("gestor-acao-detalhe")
    active = Case.objects.create(created_by=creator)
    cleaned = _cleaned_case(creator)
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"scope": "todos"})
    content = response.content.decode()

    assert response.status_code == 200
    assert reverse("dashboard:admin_close_confirm", args=[active.case_id]) not in content
    assert reverse("dashboard:admin_close_confirm", args=[cleaned.case_id]) not in content
    # Não-vacuidade: os DOIS cards estão na lista (escopo todos) com [Detalhes].
    assert _short_id(active) in content
    assert _short_id(cleaned) in content
    assert reverse("dashboard:case_detail", args=[cleaned.case_id]) in content

    active_detail = client.get(_detail_url(active)).content.decode()
    cleaned_detail = client.get(_detail_url(cleaned)).content.decode()

    assert reverse("dashboard:admin_close_confirm", args=[active.case_id]) in active_detail
    assert reverse("dashboard:admin_close_confirm", args=[cleaned.case_id]) not in cleaned_detail


@pytest.mark.django_db
def test_period_links_preserve_list_filters(client: Client) -> None:
    """R1/D2: trocar o período das MÉTRICAS não descarta os filtros da lista."""
    client.force_login(_make_user("gestor-periodo-filtros"))
    today = timezone.localdate().isoformat()

    filtered = client.get(
        reverse("dashboard:home"), {"procedure_type": "art_perif", "date_from": today}
    )
    body = filtered.content.decode()

    assert filtered.status_code == 200
    # O link troca APENAS o período: o escopo (default ``todos`` de um filtro
    # explícito) e os filtros da lista viajam na query string. O ``&`` depois do
    # período é literal do template (não escapado); o valor da query string é
    # escapado pelo autoescape.
    assert f"?period=7d&scope=todos&amp;procedure_type=art_perif&amp;date_from={today}" in body

    clean = client.get(reverse("dashboard:home"))

    # Sem filtros escolhidos o link carrega o DEFAULT resolvido (hoje/hoje +
    # todos) — paridade ats-web: clicar no período preserva a janela vigente.
    assert (
        f"?period=7d&scope=todos&amp;date_from={today}&amp;date_to={today}"
        in clean.content.decode()
    )


@pytest.mark.django_db
def test_metrics_period_is_independent_from_list_dates(client: Client) -> None:
    """R1/D2: o ``period`` continua mandando nas MÉTRICAS enquanto a lista usa as
    datas próprias (default hoje) — réguas separadas, molde ats-web."""
    creator = _make_user("gestor-metricas-periodo")
    today = timezone.localdate()
    yesterday_case = _confirmed_case(creator, SchedulingUnit.UNIT_1)
    _pin_created_at(yesterday_case, _at(today - timedelta(days=1)))
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"), {"period": "7d"})

    assert response.status_code == 200
    assert response.context["period"] == "7d"
    assert response.context["summary"]["total"] == 1
    assert response.context["cases"] == []


@pytest.mark.django_db
def test_list_pagination_preserves_filters(client: Client) -> None:
    """R1/spec: 25 casos por página com navegação que PRESERVA os filtros vigentes."""
    creator = _make_user("gestor-lista-paginacao")
    today = timezone.localdate()
    base = _at(today, hour=12)
    for index in range(CASE_LIST_PAGE_SIZE + 1):
        case = Case.objects.create(created_by=creator, agency_record_number=f"AR-{index:02d}")
        CaseProcedure.objects.create(case=case, procedure_type="art_perif", declared_by_nir=True)
        # ``created_at`` crescente de AR-00 (mais antigo) a AR-25 (mais novo),
        # pinado no MEIO do dia (meia-noite não vira a página de lugar).
        Case.objects.filter(pk=case.pk).update(created_at=base + timedelta(seconds=index))
    client.force_login(creator)
    # Ordem de ``_PRESERVED_FILTERS`` (a do link gerado): period, scope, status,
    # procedure_type, q, date_from, date_to — vazios são omitidos.
    filters = {
        "period": "7d",
        "scope": "todos",
        "procedure_type": "art_perif",
        "q": "AR-",
        "date_from": today.isoformat(),
        "date_to": today.isoformat(),
    }

    first = client.get(reverse("dashboard:home"), filters)
    first_body = first.content.decode()

    assert first.status_code == 200
    assert len(first.context["cases"]) == CASE_LIST_PAGE_SIZE
    assert "Página 1 de 2" in first_body
    # Página 1: os 25 mais recentes (AR-25…AR-01), nunca o mais antigo.
    assert "AR-25" in first_body
    assert "AR-01" in first_body
    assert "AR-00" not in first_body
    # O link da próxima página carrega os filtros vigentes (SSR puro, sem JS);
    # o valor urlencoded é escapado pelo autoescape (`&` → `&amp;`).
    expected_qs = urlencode(filters).replace("&", "&amp;")
    assert f'href="{reverse("dashboard:home")}?page=2&{expected_qs}"' in first_body

    second = client.get(reverse("dashboard:home"), {**filters, "page": "2"})
    second_body = second.content.decode()

    assert second.status_code == 200
    assert len(second.context["cases"]) == 1
    assert "AR-00" in second_body
    assert "AR-25" not in second_body
    assert "Página 2 de 2" in second_body


# ── R2: detalhe do caso no painel (change painel-ats-parity, slice 002) ────


@pytest.mark.django_db
def test_case_detail_shows_full_identity_and_procedures(client: Client) -> None:
    """R2/spec: o detalhe traz a identificação completa (nome, idade, sexo,
    raça/cor, unidade de origem, nº de ocorrência, inserção e fase) e os
    procedimentos declarados, com a ação de encerramento disponível."""
    creator = _make_user("gestor-detalhe")
    today = timezone.localdate()
    case = Case.objects.create(created_by=creator, agency_record_number=RECORD_NUMBER)
    _pin_created_at(case, _at(today, hour=8, minute=30))
    case.patient_name = PATIENT_NAME
    case.patient_age = 84
    case.patient_gender = "F"
    case.patient_race = "Parda"
    case.origin_unit = ORIGIN_UNIT
    case.save(
        update_fields=[
            "patient_name",
            "patient_age",
            "patient_gender",
            "patient_race",
            "origin_unit",
        ]
    )
    CaseProcedure.objects.create(case=case, procedure_type="art_perif", declared_by_nir=True)
    client.force_login(creator)

    response = client.get(_detail_url(case))
    content = response.content.decode()

    assert response.status_code == 200
    assert f'<span class="case-patient-name">{PATIENT_NAME}</span>' in content
    assert '<span class="case-patient-age">84 a</span>' in content
    assert '<span class="case-patient-gender">F</span>' in content
    assert '<span class="case-patient-race">Parda</span>' in content
    assert f'<span class="case-patient-origin">{ORIGIN_UNIT}</span>' in content
    assert RECORD_NUMBER in content
    assert _at(today, hour=8, minute=30).strftime("%d/%m/%Y %H:%M") in content
    assert CASE_NEXT_STEP_LABELS[str(CaseStatus.NEW)] in content
    assert "Arteriografia periférica" in content
    # Ação de encerramento do caso ativo vive AQUI (saiu do card).
    assert reverse("dashboard:admin_close_confirm", args=[case.case_id]) in content


@pytest.mark.django_db
def test_case_detail_cleaned_offers_no_close_action(client: Client) -> None:
    """R2/spec: caso ``CLEANED`` no detalhe não oferece o encerramento, mas a
    identificação continua visível."""
    creator = _make_user("gestor-detalhe-cleaned")
    cleaned = _cleaned_case(creator)
    cleaned.patient_name = PATIENT_NAME
    cleaned.save(update_fields=["patient_name"])
    client.force_login(creator)

    response = client.get(_detail_url(cleaned))
    content = response.content.decode()

    assert response.status_code == 200
    assert PATIENT_NAME in content
    assert CASE_NEXT_STEP_LABELS[str(CaseStatus.CLEANED)] in content
    assert reverse("dashboard:admin_close_confirm", args=[cleaned.case_id]) not in content


@pytest.mark.django_db
def test_case_detail_carries_list_filters(client: Client) -> None:
    """R2/D2: os filtros vigentes da lista viajam para o detalhe — voltar ao
    painel e encerrar preservam o contexto."""
    creator = _make_user("gestor-detalhe-filtros")
    case = Case.objects.create(created_by=creator)
    client.force_login(creator)
    today = timezone.localdate().isoformat()
    filters = {"period": "hoje", "scope": "todos", "date_from": today}

    response = client.get(_detail_url(case), {"scope": "todos", "date_from": today})
    content = response.content.decode()
    expected_qs = urlencode(filters).replace("&", "&amp;")

    assert response.status_code == 200
    assert f"{reverse('dashboard:home')}?{expected_qs}" in content
    assert f"?{expected_qs}" in content


@pytest.mark.django_db
def test_case_detail_404_for_unknown_case(client: Client) -> None:
    """R2/spec: caso inexistente → 404 sem vazar informação."""
    client.force_login(_make_user("gestor-detalhe-404"))

    response = client.get(reverse("dashboard:case_detail", args=[uuid.uuid4()]))

    assert response.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("role", NON_MANAGEMENT_ROLES)
def test_case_detail_403_for_other_roles(client: Client, role: str) -> None:
    """R2/spec: papel ativo fora de manager/admin → 403 no detalhe."""
    creator = _make_user(f"gestor-detalhe-origem-{role}")
    case = Case.objects.create(created_by=creator)
    client.force_login(_make_user(f"fora-detalhe-{role}", role))

    response = client.get(_detail_url(case))

    assert response.status_code == 403


@pytest.mark.django_db
def test_case_detail_anonymous_redirects_to_login(client: Client) -> None:
    """R2: anônimo → redirect ao login (composição do guard por papel)."""
    case = Case.objects.create(created_by=_make_user("gestor-detalhe-anonimo"))

    response = client.get(_detail_url(case))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


# ── R2: rota/UI do encerramento administrativo ────────────────────────────

CLOSE_REASON_CODE = "stuck_lock"
CLOSE_REASON_TEXT = "lock do worker nao liberado apos 24h"


def _close_url(case: Case) -> str:
    """URL do POST de encerramento administrativo do caso."""
    return reverse("dashboard:admin_close", args=[case.case_id])


@pytest.mark.django_db
def test_admin_close_confirm_renders_catalog_form(client: Client) -> None:
    """R2: a confirmação identifica o caso, traz o select do catálogo e a
    textarea obrigatória do motivo, além do destino do POST."""
    creator = _make_user("gestor-confirmacao")
    case = Case.objects.create(created_by=creator, agency_record_number=RECORD_NUMBER)
    client.force_login(creator)

    response = client.get(reverse("dashboard:admin_close_confirm", args=[case.case_id]))
    content = response.content.decode()

    assert response.status_code == 200
    assert RECORD_NUMBER in content
    assert _close_url(case) in content
    for code, label in ADMINISTRATIVE_CLOSURE_REASONS.items():
        assert f'value="{code}"' in content
        assert label in content
    assert 'name="reason_text"' in content
    assert "required" in content


@pytest.mark.django_db
def test_admin_close_post_encloses_and_returns_with_filters(client: Client) -> None:
    """R2/spec: POST encerra o caso com motivo do catálogo, flash de sucesso e
    redirect para o painel PRESERVANDO os filtros vigentes."""
    creator = _make_user("gestor-posto")
    case = Case.objects.create(created_by=creator, agency_record_number=RECORD_NUMBER)
    client.force_login(creator)
    filters = {"period": "7d", "scope": "todos"}

    response = client.post(
        _close_url(case),
        {"reason_code": CLOSE_REASON_CODE, "reason_text": CLOSE_REASON_TEXT, **filters},
    )

    assert response.status_code == 302
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    events = case.events.filter(event_type=CaseEventType.CASE_ADMINISTRATIVELY_CLOSED)
    assert events.count() == 1
    assert events.get().payload["reason_code"] == CLOSE_REASON_CODE

    location = response["Location"]
    assert location.startswith(reverse("dashboard:home"))
    assert "scope=todos" in location
    assert "period=7d" in location

    followed = client.get(location)
    body = followed.content.decode()
    assert followed.status_code == 200
    assert CLOSE_SUCCESS_MESSAGE in body
    # Os filtros preservados mantêm o caso encerrado visível na lista.
    assert RECORD_NUMBER in body


@pytest.mark.django_db
@pytest.mark.parametrize("role", NON_MANAGEMENT_ROLES)
def test_admin_close_routes_403_for_other_roles(client: Client, role: str) -> None:
    """R2/spec: nir/doctor/scheduler → 403 nas DUAS rotas, sem efeito no caso."""
    creator = _make_user(f"gestor-origem-403-{role}")
    case = Case.objects.create(created_by=creator)
    client.force_login(_make_user(f"fora-encerramento-{role}", role))

    confirm = client.get(reverse("dashboard:admin_close_confirm", args=[case.case_id]))
    posted = client.post(
        _close_url(case), {"reason_code": CLOSE_REASON_CODE, "reason_text": CLOSE_REASON_TEXT}
    )

    assert confirm.status_code == 403
    assert posted.status_code == 403
    case.refresh_from_db()
    assert case.status == CaseStatus.NEW
    assert not case.events.filter(event_type=CaseEventType.CASE_ADMINISTRATIVELY_CLOSED).exists()


@pytest.mark.django_db
def test_admin_close_routes_404_for_unknown_case(client: Client) -> None:
    """R2/spec: caso inexistente → 404 nas duas rotas."""
    client.force_login(_make_user("gestor-404"))
    unknown = uuid.uuid4()

    confirm = client.get(reverse("dashboard:admin_close_confirm", args=[unknown]))
    posted = client.post(
        reverse("dashboard:admin_close", args=[unknown]),
        {"reason_code": CLOSE_REASON_CODE, "reason_text": CLOSE_REASON_TEXT},
    )

    assert confirm.status_code == 404
    assert posted.status_code == 404


@pytest.mark.django_db
def test_admin_close_empty_reason_text_keeps_case(client: Client) -> None:
    """R2/spec: texto do motivo vazio → erro re-renderizado e caso intacto."""
    creator = _make_user("gestor-texto-vazio")
    case = Case.objects.create(created_by=creator)
    client.force_login(creator)

    response = client.post(
        _close_url(case), {"reason_code": CLOSE_REASON_CODE, "reason_text": "   "}
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "motivo do encerramento administrativo" in content
    case.refresh_from_db()
    assert case.status == CaseStatus.NEW
    assert not case.events.filter(event_type=CaseEventType.CASE_ADMINISTRATIVELY_CLOSED).exists()


@pytest.mark.django_db
def test_admin_close_invalid_reason_code_keeps_case(client: Client) -> None:
    """R2/spec: código fora do catálogo → erro de validação e caso intacto."""
    creator = _make_user("gestor-codigo-invalido")
    case = Case.objects.create(created_by=creator)
    client.force_login(creator)

    response = client.post(
        _close_url(case), {"reason_code": "motivo-inexistente", "reason_text": CLOSE_REASON_TEXT}
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "fora do catálogo" in content
    case.refresh_from_db()
    assert case.status == CaseStatus.NEW
    assert not case.events.filter(event_type=CaseEventType.CASE_ADMINISTRATIVELY_CLOSED).exists()


@pytest.mark.django_db
def test_admin_close_live_worker_lease_is_refused(client: Client) -> None:
    """R2/D2: lock de worker com lease VIVA → mensagem de erro e caso intacto
    (status e lock preservados)."""
    creator = _make_user("gestor-lease-viva")
    case = Case.objects.create(created_by=creator)
    Case.objects.filter(pk=case.pk).update(
        lock_context="worker_pipeline", locked_until=timezone.now() + timedelta(minutes=5)
    )
    client.force_login(creator)

    response = client.post(
        _close_url(case), {"reason_code": CLOSE_REASON_CODE, "reason_text": CLOSE_REASON_TEXT}
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "em processamento" in content
    case.refresh_from_db()
    assert case.status == CaseStatus.NEW
    assert case.lock_context == "worker_pipeline"
    assert case.locked_until is not None
    assert not case.events.filter(event_type=CaseEventType.CASE_ADMINISTRATIVELY_CLOSED).exists()
