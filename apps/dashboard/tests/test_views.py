"""Testes da view do painel gerencial (slice 003, R2/R3/R4/R5).

Cobre ``dashboard:home``: exige login (anônimo → redirect login), valida o
período contra o conjunto aceito (inválido/ausente → ``hoje``), renderiza o
template zero-PHI (nenhum nome/nº de registro de paciente) e o link "Painel" da
navbar visível a todo autenticado (sem gate de papel). As fixtures dirigem
casos aos estados reais pelas operações públicas da FSM.
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
NIR_ROLE = "nir"

PATIENT_NAME = "Maria da Silva"
RECORD_NUMBER = "33345"

# Rótulos configurados do cenário de override (change unit-labels-env, R5).
CONFIGURED_UNIT_LABELS = {1: "Hemodinâmica HGRS", 2: "Unidade Satélite"}


def _make_user(username: str) -> User:
    """Usuário com um papel (para o middleware resolver o papel ativo)."""
    role, _ = Role.objects.get_or_create(name=NIR_ROLE)
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


# ── R2: acesso e período ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_anonymous_redirect(client: Client) -> None:
    """R5: anônimo é redirecionado ao login."""
    response = client.get(reverse("dashboard:home"))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
def test_invalid_period_defaults(client: Client) -> None:
    """R5: período inválido cai em ``hoje``."""
    client.force_login(_make_user("nir-periodo-invalido"))

    response = client.get(reverse("dashboard:home"), {"period": "semana-passada"})

    assert response.status_code == 200
    assert response.context["period"] == "hoje"


@pytest.mark.django_db
def test_valid_period_is_respected(client: Client) -> None:
    """R2: período aceito é preservado no contexto."""
    client.force_login(_make_user("nir-periodo-valido"))

    response = client.get(reverse("dashboard:home"), {"period": "30d"})

    assert response.status_code == 200
    assert response.context["period"] == "30d"


# ── R3: render zero-PHI ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_dashboard_renders_zero_phi(client: Client) -> None:
    """R5: a página renderiza as métricas SEM nome/nº de registro do paciente."""
    creator = _make_user("nir-zero-phi")
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
    creator = _make_user("nir-render")
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
    client.force_login(_make_user("nir-labels"))

    with override_settings(UNIT_LABELS=CONFIGURED_UNIT_LABELS):
        response = client.get(reverse("dashboard:home"))

    content = response.content.decode()
    assert response.status_code == 200
    assert "Hemodinâmica HGRS" in content
    assert "Unidade Satélite" in content
    assert "Unidade 1" not in content
    assert "Unidade 2" not in content


# ── R4: link da navbar ─────────────────────────────────────────────────────


@pytest.mark.django_db
def test_navbar_link(client: Client) -> None:
    """R4/R5: link "Painel" visível a todo autenticado, apontando para ``dashboard:home``."""
    client.force_login(_make_user("nir-navbar"))

    response = client.get(reverse("home"))
    content = response.content.decode()

    assert response.status_code == 200
    assert reverse("dashboard:home") in content
    assert "Painel" in content


@pytest.mark.django_db
def test_navbar_link_absent_for_anonymous(client: Client) -> None:
    """R4: anônimo não vê o link "Painel" (a navbar autenticada não é renderizada)."""
    response = client.get(reverse("login"))

    assert response.status_code == 200
    assert reverse("dashboard:home") not in response.content.decode()
