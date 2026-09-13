"""Testes da view do painel gerencial (slice 003 + painel-gerencial-e-home/001).

Cobre ``dashboard:home`` sob o gate por papel ativo (change
painel-gerencial-e-home, slice 001, D1): ``manager``/``admin`` → 200 com as
métricas do período; ``nir``/``doctor``/``scheduler`` → 403 (guard na ROTA, não
só no menu); anônimo → redirect ao login. A cobertura anterior continua
(período validado contra o conjunto aceito — inválido/ausente → ``hoje`` —,
render zero-PHI e rótulos de unidade da fonte única): esses usuários passam a
logar com papel gerencial (R3). As fixtures dirigem casos aos estados reais
pelas operações públicas da FSM.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.cases.models import Case, CaseProcedure, CaseStatus, DoctorDisposition, SchedulingUnit

SYSTEM_ROLE = "system"
MANAGER_ROLE = "manager"
ADMIN_ROLE = "admin"

# Papéis ativos sem acesso ao painel nem ao link da navbar (R1/R2).
NON_MANAGEMENT_ROLES = ("nir", "doctor", "scheduler")

PATIENT_NAME = "Maria da Silva"
RECORD_NUMBER = "33345"

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


# ── R3: render zero-PHI ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_dashboard_renders_zero_phi(client: Client) -> None:
    """R5: a página renderiza as métricas SEM nome/nº de registro do paciente."""
    creator = _make_user("gestor-zero-phi")
    case = _confirmed_case(creator, SchedulingUnit.UNIT_1)
    case.patient_name = PATIENT_NAME
    case.agency_record_number = RECORD_NUMBER
    case.save(update_fields=["patient_name", "agency_record_number"])
    client.force_login(creator)

    response = client.get(reverse("dashboard:home"))
    content = response.content.decode()

    assert response.status_code == 200
    assert PATIENT_NAME not in content
    assert RECORD_NUMBER not in content
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
