"""Testes da home por papel ativo (change painel-gerencial-e-home, slice 002).

Cobre:
- R1: ``home_view`` redireciona (302) cada papel ativo à sua área de trabalho
  (``nir``→intake, ``doctor``→fila médica, ``scheduler``→fila de agendamento,
  ``manager``/``admin``→painel);
- R2: sem papel ativo na sessão a home cai no placeholder (fallback) — 200, sem
  redirect — com o texto novo (a substituição pelas filas já aconteceu e o
  fallback orienta trocar de papel/sair);
- R3: login ponta a ponta do papel ``nir`` aterrissa no formulário de envio;
- R4: menu NIR com "Enviar relatório" antes de "Meus casos".
"""

from collections.abc import Sequence

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.accounts.views import home_view

USERNAME = "maria.hemodinamica"
PASSWORD = "senha-local-123"
WELCOME = "Bem-vindo(a)"
SWITCH_HINT = "Troque de papel"
# Promessa do placeholder antigo ("as filas do seu papel substituirão esta
# tela nas próximas versões"): a substituição aconteceu (D5).
STALE_PROMISE = "substituirão esta tela"

# Papel ativo → nome da rota de destino (D2; 5 papéis, 4 destinos).
HOME_ROUTES = [
    ("nir", "intake:home"),
    ("doctor", "doctor:queue"),
    ("scheduler", "scheduler:queue"),
    ("manager", "dashboard:home"),
    ("admin", "dashboard:home"),
]


def _create_user(
    *,
    username: str = USERNAME,
    role_names: Sequence[str],
    is_superuser: bool = False,
) -> User:
    """Cria usuário com os papéis informados (senha fixa ``PASSWORD``).

    ``is_superuser`` replica o admin local por design (ADR-0009): superusuário
    sem ``ad_upn``, o único perfil autenticável pelo fluxo local de senha.
    """
    user = User.objects.create_user(username=username, password=PASSWORD)
    if is_superuser:
        user.is_superuser = True
        user.save(update_fields=["is_superuser"])
    for name in role_names:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


def _login_with_active_role(client: Client, *, username: str, role: str) -> User:
    """Autentica o usuário e fixa o papel ativo ``role`` na sessão."""
    user = User.objects.get(username=username)
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()
    return user


@pytest.mark.django_db
@pytest.mark.parametrize(("role", "destination"), HOME_ROUTES)
def test_home_redirects_per_active_role(client: Client, role: str, destination: str) -> None:
    """R1: cada papel ativo é despachado (302) à sua área de trabalho."""
    _create_user(username=f"usuario-home-{role}", role_names=(role,))
    _login_with_active_role(client, username=f"usuario-home-{role}", role=role)

    response = client.get(reverse("home"))

    assert response.status_code == 302
    assert response.headers["Location"] == reverse(destination)


@pytest.mark.django_db
def test_home_without_active_role_shows_fallback() -> None:
    """R2: sem papel ativo na sessão a home renderiza o placeholder (200).

    Chamada direta da view: o ``ActiveRoleMiddleware`` sempre define/sela um
    papel ativo antes de a requisição chegar aqui (papel único → auto-set;
    multi-role → re-seleção), então o fallback só é observável na view.
    """
    user = _create_user(username="sem-papel-ativo", role_names=("nir",))
    request = RequestFactory().get(reverse("home"))
    request.session = {}  # type: ignore[assignment]
    request.user = user

    response = home_view(request)

    assert response.status_code == 200
    assert "Location" not in response
    body = response.content.decode()
    assert WELCOME in body
    assert SWITCH_HINT in body
    assert STALE_PROMISE not in body


@pytest.mark.django_db
def test_login_nir_lands_on_intake_home(client: Client) -> None:
    """R3: login ``nir`` → home → redirect → formulário de envio renderizado."""
    _create_user(username="regulador.nir", role_names=("nir",), is_superuser=True)

    login = client.post(reverse("login"), {"username": "regulador.nir", "password": PASSWORD})
    assert login.status_code == 302
    assert login.headers["Location"] == reverse("home")

    response = client.get(reverse("home"), follow=True)

    assert response.status_code == 200
    assert response.redirect_chain == [(reverse("intake:home"), 302)]
    body = response.content.decode()
    assert "Envio de relatório de regulação" in body
    assert 'name="documents"' in body


@pytest.mark.django_db
def test_nir_nav_order_send_report_first(client: Client) -> None:
    """R4: "Enviar relatório" precede "Meus casos" no HTML de uma página nir."""
    _create_user(username="regulador.nir", role_names=("nir",))
    _login_with_active_role(client, username="regulador.nir", role="nir")

    body = client.get(reverse("intake:home")).content.decode()

    assert body.index("Enviar relatório") < body.index("Meus casos")
