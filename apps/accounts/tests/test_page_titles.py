"""Títulos de página das views de conta (ui-parity-foundation, slice 003, R2).

Toda página listada renderiza exatamente um ``h1.page-title`` com o nome da
visão, distinto do ``<title>`` (que leva a marca). RED: ``h1.page-title``
ausente nos HTMLs antes da fundação CSS.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.accounts.views import home_view

PASSWORD = "senha-teste"
EXTERNAL_IP = "203.0.113.10"

_PAGE_TITLE_RE = re.compile(r'<h1[^>]*class="[^"]*page-title[^"]*"[^>]*>(.*?)</h1>', re.DOTALL)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


def _create_user(username: str, *role_names: str) -> User:
    """Cria usuário com os papéis informados (senha fixa)."""
    user = User.objects.create_user(username=username, password=PASSWORD)
    for name in role_names:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


def _login(client: Client, user: User, role: str) -> None:
    """Autentica o usuário e fixa o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _assert_page_title(body: str, expected: str) -> None:
    """Asserta exatamente um ``h1.page-title`` com o nome da visão."""
    titles = _PAGE_TITLE_RE.findall(body)
    assert len(titles) == 1, f"esperado 1 h1.page-title, encontrados {len(titles)}"
    text = re.sub(r"<[^>]+>", "", titles[0]).strip()
    assert expected in text, f"título {text!r} não contém {expected!r}"
    title_match = _TITLE_RE.search(body)
    assert title_match is not None
    assert text != title_match.group(1).strip()


@pytest.mark.django_db
def test_login_page_title(client: Client) -> None:
    """Login (anônimo) tem ``h1.page-title`` com o nome da visão."""
    response = client.get(reverse("login"))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Login")


@pytest.mark.django_db
def test_home_fallback_page_title() -> None:
    """Home placeholder (sem papel ativo) tem ``h1.page-title`` de boas-vindas."""
    user = _create_user("sem-papel-titulo", "nir")
    request = RequestFactory().get(reverse("home"))
    request.session = {}  # type: ignore[assignment]
    request.user = user
    response = home_view(request)
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Bem-vindo(a)")


@pytest.mark.django_db
def test_manual_page_title(client: Client) -> None:
    """Manual tem ``h1.page-title`` com o nome da visão."""
    user = _create_user("nir-manual-titulo", "nir")
    _login(client, user, "nir")
    response = client.get(reverse("manual"))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Manual de uso")


@pytest.mark.django_db
def test_notifications_page_title(client: Client) -> None:
    """Notificações tem ``h1.page-title`` com o nome da visão."""
    user = _create_user("nir-notif-titulo", "nir")
    _login(client, user, "nir")
    response = client.get(reverse("notifications"))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Notificações")


@pytest.mark.django_db
def test_profile_page_title(client: Client) -> None:
    """Perfil tem ``h1.page-title`` com o nome da visão."""
    user = _create_user("nir-perfil-titulo", "nir")
    _login(client, user, "nir")
    response = client.get(reverse("profile"))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Meu perfil")


@pytest.mark.django_db
def test_switch_role_page_title(client: Client) -> None:
    """Seleção de papel tem ``h1.page-title`` com o nome da visão."""
    user = _create_user("nir-papel-titulo", "nir")
    _login(client, user, "nir")
    response = client.get(reverse("switch_role"))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Selecionar papel")


@pytest.mark.django_db
@override_settings(INTRANET_IP_RANGE="10.0.0.0/8", INTRANET_RESTRICTED_ROLES=["nir"])
def test_intranet_blocked_page_title(client: Client) -> None:
    """Página de bloqueio (403 do guard) tem ``h1.page-title`` com a mensagem."""
    user = _create_user("nir-bloqueio-titulo", "nir")
    _login(client, user, "nir")
    response = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)
    assert response.status_code == 403
    _assert_page_title(response.content.decode(), "rede interna")
