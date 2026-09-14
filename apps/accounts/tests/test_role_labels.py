"""Testes dos rótulos de papel em português na UI (role-labels-ptbr, slice 001).

Cobre a fonte única ``ROLE_LABELS``/``role_label`` (R1), o filtro de template
``role_label`` (R2) e as superfícies de exibição (R3) — badge do papel ativo,
seleção de papel, home da conta, perfil e as menções a papel nas trilhas e
comunicações dos detalhes de caso.

Não-vacuidade (risco do design): os comentários HTML de ``base.html`` contêm as
chaves cruas (``doctor``/``manager``/...), então asserções de rótulo são
badge/botão-scoped (``>médico<``) e as de chave miram o wire/contexto
(``value="doctor"``, sessão) — nunca substring solta no body.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.accounts.role_labels import ROLE_LABELS, role_label
from apps.cases.events import CaseEventType
from apps.cases.models import (
    ActorType,
    Case,
    CaseCommunicationMessage,
    CaseEvent,
    MessageType,
)

USERNAME = "maria.hemodinamica"
PASSWORD = "senha-local-123"
DOCTOR_ROLE = "doctor"
MANAGER_ROLE = "manager"


def _create_user(*, username: str = USERNAME, role_names: Sequence[str]) -> User:
    """Cria usuário com os papéis informados (senha fixa ``PASSWORD``)."""
    user = User.objects.create_user(username=username, password=PASSWORD)
    for name in role_names:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


def _login(client: Client, *, username: str, role: str) -> User:
    """Autentica o usuário e fixa o papel ativo ``role`` na sessão."""
    user = User.objects.get(username=username)
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()
    return user


def _create_case_with_doctor_event_and_manager_message(
    *,
    creator: User,
    event_actor: User,
    message_author: User,
) -> Case:
    """Caso com evento decidido por ``doctor`` e mensagem postada por ``manager``."""
    case = Case.objects.create(created_by=creator)
    CaseEvent.objects.create(
        case=case,
        actor_type=ActorType.USER,
        actor=event_actor,
        actor_role=DOCTOR_ROLE,
        event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED,
        payload={},
    )
    CaseCommunicationMessage.objects.create(
        case=case,
        message_type=MessageType.USER,
        author=message_author,
        author_role=MANAGER_ROLE,
        body="Mensagem da supervisão.",
    )
    return case


# ── R1: fonte única ────────────────────────────────────────────────────────


def test_role_label_maps_all_five_roles() -> None:
    """R1: os 5 papéis do mapeamento têm os rótulos confirmados pelo dono.

    O assert do dicionário inteiro quebra sob mutação (não-vacuidade): trocar
    ou remover uma entrada falha aqui — o mapeamento não é implícito.
    """
    assert ROLE_LABELS == {
        "doctor": "médico",
        "scheduler": "agendador",
        "manager": "supervisor",
        "nir": "nir",
        "admin": "admin",
    }
    assert role_label("doctor") == "médico"
    assert role_label("scheduler") == "agendador"
    assert role_label("manager") == "supervisor"
    assert role_label("nir") == "nir"
    assert role_label("admin") == "admin"


def test_role_label_falls_back_to_raw_key() -> None:
    """R1/D3: chave fora do mapeamento exibe a própria chave (inclusive vazia)."""
    assert role_label("system") == "system"
    assert role_label("nurse") == "nurse"
    assert role_label("") == ""


# ── R3: superfícies ────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_active_role_badge_shows_label(client: Client) -> None:
    """R3: o badge de papel ativo exibe "médico", escopado ao badge (não à chave)."""
    _create_user(role_names=[DOCTOR_ROLE])
    _login(client, username=USERNAME, role=DOCTOR_ROLE)

    body = client.get(reverse("home"), follow=True).content.decode()

    assert 'title="Papel ativo">médico<' in body


@pytest.mark.django_db
def test_switch_role_lists_labels_and_submits_keys(client: Client) -> None:
    """R3: os botões exibem rótulos e cada form submete a chave crua."""
    _create_user(role_names=[DOCTOR_ROLE, MANAGER_ROLE])
    _login(client, username=USERNAME, role=DOCTOR_ROLE)

    body = client.get(reverse("switch_role")).content.decode()

    assert ">médico</button>" in body
    assert ">supervisor</button>" in body
    assert 'name="role" value="doctor"' in body
    assert 'name="role" value="manager"' in body


@pytest.mark.django_db
def test_selecting_labeled_supervisor_submits_manager_key(client: Client) -> None:
    """R3/spec: o POST da chave por trás do botão "supervisor" grava manager."""
    _create_user(role_names=[DOCTOR_ROLE, MANAGER_ROLE])
    _login(client, username=USERNAME, role=DOCTOR_ROLE)

    body = client.get(reverse("switch_role")).content.decode()
    selection = re.search(
        r'name="role" value="(\w+)"[^>]*>\s*<button[^>]*>supervisor</button>',
        body,
    )
    assert selection is not None
    key = selection.group(1)

    response = client.post(reverse("switch_role"), {"role": key})

    assert key == MANAGER_ROLE
    assert response.status_code == 302
    assert client.session["active_role"] == MANAGER_ROLE


@pytest.mark.django_db
def test_profile_lists_labels(client: Client) -> None:
    """R3: a lista de papéis do perfil exibe "supervisor" e "agendador"."""
    _create_user(role_names=["scheduler", MANAGER_ROLE])
    _login(client, username=USERNAME, role="scheduler")

    body = client.get(reverse("profile")).content.decode()

    assert "supervisor, agendador" in body


@pytest.mark.django_db
def test_account_home_lists_label_and_fallback(client: Client) -> None:
    """R3/D3: home da conta exibe o rótulo e a chave crua para papel sem mapeamento.

    Cenário análogo a ``test_home_dispatch.py``: papel ativo fora da tabela de
    áreas de trabalho cai no placeholder; a lista usa ``doctor`` (rótulo) e
    ``nurse`` (fallback = própria chave).
    """
    _create_user(role_names=[DOCTOR_ROLE, "nurse"])
    _login(client, username=USERNAME, role="nurse")

    response = client.get(reverse("home"))

    assert response.status_code == 200
    body = response.content.decode()
    assert ">médico</span>" in body  # rótulo, badge da lista
    assert ">nurse</span>" in body  # fallback, chave crua


@pytest.mark.django_db
def test_intake_trail_system_role_falls_back_to_raw_key(client: Client) -> None:
    """D3/spec: chave fora do mapeamento (``system``) exibe a própria chave na
    trilha do detalhe, sem erro de renderização."""
    creator = _create_user(username="nir.sistema", role_names=["nir"])
    event_actor = _create_user(username="ator.medico", role_names=[DOCTOR_ROLE])
    message_author = _create_user(username="autor.supervisor", role_names=[MANAGER_ROLE])
    case = _create_case_with_doctor_event_and_manager_message(
        creator=creator, event_actor=event_actor, message_author=message_author
    )
    CaseEvent.objects.create(
        case=case,
        actor_type=ActorType.SYSTEM,
        actor_role="system",
        event_type=CaseEventType.CASE_STATUS_PDF_EXTRACTING,
        payload={},
    )
    _login(client, username=creator.username, role="nir")

    response = client.get(reverse("intake:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "papel system" in body


@pytest.mark.django_db
def test_doctor_trail_shows_role_label(client: Client) -> None:
    """R3: o evento de decisão e a trilha do detalhe médico exibem "papel médico"."""
    creator = _create_user(username="nir.criador", role_names=["nir"])
    event_actor = _create_user(username="ator.medico", role_names=[DOCTOR_ROLE])
    message_author = _create_user(username="autor.supervisor", role_names=[MANAGER_ROLE])
    case = _create_case_with_doctor_event_and_manager_message(
        creator=creator, event_actor=event_actor, message_author=message_author
    )
    _create_user(username="medico.visualizador", role_names=[DOCTOR_ROLE])
    _login(client, username="medico.visualizador", role=DOCTOR_ROLE)

    body = client.get(reverse("doctor:case_detail", args=[case.case_id])).content.decode()

    # Evento de decisão (linha 85) + trilha (linha 317): dois pontos.
    assert body.count("papel médico") == 2
    assert "papel doctor" not in body


@pytest.mark.django_db
def test_intake_trail_and_communication_show_labels(client: Client) -> None:
    """R3: trilha e badge de comunicação do detalhe NIR exibem os rótulos."""
    creator = _create_user(username="nir.criador", role_names=["nir"])
    event_actor = _create_user(username="ator.medico", role_names=[DOCTOR_ROLE])
    message_author = _create_user(username="autor.supervisor", role_names=[MANAGER_ROLE])
    case = _create_case_with_doctor_event_and_manager_message(
        creator=creator, event_actor=event_actor, message_author=message_author
    )
    _login(client, username="nir.criador", role="nir")

    body = client.get(reverse("intake:case_detail", args=[case.case_id])).content.decode()

    assert "papel médico" in body
    assert ">supervisor</span>" in body
    assert "papel manager" not in body


@pytest.mark.django_db
def test_scheduler_communication_badge_shows_label(client: Client) -> None:
    """R3: badge de comunicação do detalhe do agendador exibe "supervisor"."""
    creator = _create_user(username="nir.criador", role_names=["nir"])
    event_actor = _create_user(username="ator.medico", role_names=[DOCTOR_ROLE])
    message_author = _create_user(username="autor.supervisor", role_names=[MANAGER_ROLE])
    case = _create_case_with_doctor_event_and_manager_message(
        creator=creator, event_actor=event_actor, message_author=message_author
    )
    _create_user(username="agendador.visualizador", role_names=["scheduler"])
    _login(client, username="agendador.visualizador", role="scheduler")

    body = client.get(reverse("scheduler:case_detail", args=[case.case_id])).content.decode()

    assert ">supervisor</span>" in body
