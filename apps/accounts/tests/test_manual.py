"""Testes do manual de usuário por papel (change 11, slice 005, R1–R4).

Cobre a página estática que descreve o fluxo REAL do HMD:

- R1: rota de nome GLOBAL ``manual`` em ``/manual/`` (sem namespace, como as
  demais rotas de ``apps.accounts``), login required, renderiza
  ``templates/accounts/manual.html``;
- R2: visão geral do ciclo com os 17 estados REAIS de ``CaseStatus``, seções
  por papel (NIR/médico/agendador) e transversal (notificações/painel/PWA),
  HTML imprimível (CSS ``@media print``), sem dados dinâmicos de caso;
- R3: link "Manual" na navbar de ``base.html``, visível a autenticados e
  aberto em nova aba;
- R4: os marcadores abaixo são a assert anti-desatualização mínima — cada um
  aponta para uma operação REAL de um slice anterior (anexos jpg/png/pdf,
  verificação de paciente, ciência/reenvio, intercorrência/unidades). Se o
  fluxo mudar, o manual precisa mudar junto.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.cases.models import CaseStatus

PASSWORD = "senha-teste"
MANUAL_PATH = "/manual/"

# Marcos por papel/seção (R2/R4): strings que o template DEVE conter.
NIR_MARKERS = ("Enviar relatório", "anexos", "Meus casos", "ciência", "reenvio")
DOCTOR_MARKERS = (
    "Fila médica",
    "verificação de paciente",
    "alerta consultivo",
    "decisão por procedimento",
)
SCHEDULER_MARKERS = (
    "Fila de agendamento",
    "Unidade 1",
    "Unidade 2",
    "intercorrência",
    "PDF do relatório",
)
CROSS_CUTTING_MARKERS = ("Notificações", "Painel", "PWA")


def _create_user(username: str, *roles: str) -> User:
    """Cria usuário com os papéis informados (senha fixa)."""
    user = User.objects.create_user(username=username, password=PASSWORD)
    for name in roles:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def nir_user() -> User:
    return _create_user("nir-manual", "nir")


def _manual_body(client: Client, user: User) -> str:
    """Renderiza o manual autenticado e devolve o HTML."""
    client.force_login(user)
    response = client.get(MANUAL_PATH)
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
class TestManualRoute:
    """R1: rota global ``manual`` em ``/manual/``, login required (D5)."""

    def test_manual_route_name_and_path(self) -> None:
        assert reverse("manual") == MANUAL_PATH

    def test_manual_route_login_required(self, client: Client) -> None:
        response = client.get(MANUAL_PATH)

        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("login"))

    def test_manual_route_renders_template(self, client: Client, nir_user: User) -> None:
        client.force_login(nir_user)

        response = client.get(MANUAL_PATH)

        assert response.status_code == 200
        template_names = {template.name for template in response.templates if template.name}
        assert "accounts/manual.html" in template_names


@pytest.mark.django_db
class TestManualContent:
    """R2: conteúdo REAL do fluxo, por papel e transversal, imprimível."""

    def test_manual_lists_all_real_case_states(self, client: Client, nir_user: User) -> None:
        body = _manual_body(client, nir_user)

        for status in CaseStatus:
            assert status.value in body, f"estado ausente no manual: {status.value}"

        # Bidirecional (P2 review): o conjunto de estados citados no parágrafo
        # "Estados possíveis" é EXATAMENTE o do enum — nada inventado a mais.
        states_paragraph = body.split("Estados possíveis")[1].split("</p>")[0]
        cited = set(re.findall(r"[A-Z][A-Z_]+", states_paragraph))
        assert cited == {status.value for status in CaseStatus}

    def test_manual_has_role_and_cross_cutting_sections(
        self, client: Client, nir_user: User
    ) -> None:
        body = _manual_body(client, nir_user)

        for marker in (
            *NIR_MARKERS,
            *DOCTOR_MARKERS,
            *SCHEDULER_MARKERS,
            *CROSS_CUTTING_MARKERS,
        ):
            assert marker in body, f"marcador ausente no manual: {marker}"

    def test_manual_mentions_attachments_and_patient_verification(
        self, client: Client, nir_user: User
    ) -> None:
        body = _manual_body(client, nir_user)

        assert "JPEG" in body
        assert "PNG" in body
        assert "verificação de paciente" in body
        assert "alerta consultivo" in body

    def test_manual_mentions_ack_and_resubmit(self, client: Client, nir_user: User) -> None:
        body = _manual_body(client, nir_user)

        assert "ciência" in body
        assert "reenvio" in body

    def test_manual_mentions_intercurrence_and_units(self, client: Client, nir_user: User) -> None:
        body = _manual_body(client, nir_user)

        assert "intercorrência" in body
        assert "Unidade 1" in body
        assert "Unidade 2" in body

    def test_manual_is_printable(self, client: Client, nir_user: User) -> None:
        body = _manual_body(client, nir_user)

        assert "@media print" in body


@pytest.mark.django_db
class TestNavbarManualLink:
    """R3: link "Manual" na navbar — autenticados, nova aba."""

    def test_navbar_manual_link(self, client: Client, nir_user: User) -> None:
        client.force_login(nir_user)

        body = client.get(reverse("home")).content.decode()

        assert reverse("manual") in body
        assert 'target="_blank"' in body
        assert ">Manual</a>" in body

    def test_navbar_manual_link_hidden_when_anonymous(self, client: Client) -> None:
        body = client.get(reverse("login")).content.decode()

        assert reverse("manual") not in body
