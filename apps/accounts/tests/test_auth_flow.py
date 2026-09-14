"""Testes do fluxo de login/logout local (slice 004, R1–R6; adaptado p/ slice 003).

Desde o slice 003 do change ad-kerberos, a autenticação local comum deixou de
existir: ``LocalAccountBackend`` autentica o **admin local por design** —
superusuário sem ``ad_upn``, em qualquer ambiente, sem flag (ADR-0009,
change admin-local-identity). Os fluxos aqui exercitam esse caminho:

- R1/R6: backend recusa contas ``blocked``/``removed`` mesmo com senha
  correta, com mensagem genérica (sem revelar o motivo interno);
- R2/R6: login break-glass válido cria sessão; senha errada nega com mensagem
  genérica; profile exige login; logout encerra a sessão e volta ao login;
- R2: troca de senha local no perfil preserva a sessão e a nova senha autentica;
- R4: home autenticada placeholder exige login e cumprimenta o usuário;
- R5: usuário autenticado em ``/login/`` é redirecionado para a home.
"""

from collections.abc import Sequence

import re

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User

USERNAME = "maria.hemodinamica"
DISPLAY_NAME = "Maria Silva"
PASSWORD = "senha-local-123"
GENERIC_ERROR = "Usuário ou senha inválidos."


def _create_user(
    *,
    username: str = USERNAME,
    first_name: str = "",
    last_name: str = "",
    account_status: str = "active",
    role_names: Sequence[str] = ("doctor",),
) -> User:
    """Cria usuário de teste do fluxo local (senha fixa ``PASSWORD``).

    O fluxo local é o admin por design (ADR-0009): superusuário **sem**
    ``ad_upn``, autenticável em qualquer ambiente — sem flag de habilitação.
    """
    user = User.objects.create_user(username=username, password=PASSWORD)
    user.is_superuser = True
    user.first_name = first_name
    user.last_name = last_name
    user.account_status = account_status
    user.save(update_fields=["is_superuser", "first_name", "last_name", "account_status"])
    for name in role_names:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste."""
    return Client()


@pytest.mark.django_db
class TestLoginFlow:
    """R2/R6: login, sessão, perfil, logout e troca de senha."""

    def test_login_logout_profile(self, client: Client) -> None:
        _create_user(first_name="Maria", last_name="Silva")

        # GET login renderiza o formulário.
        response = client.get(reverse("login"))
        assert response.status_code == 200

        # Credenciais válidas: cria sessão e redireciona para a home.
        response = client.post(reverse("login"), {"username": USERNAME, "password": PASSWORD})
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("home")
        # follow=True: a home despacha o papel ativo à sua fila (change
        # painel-gerencial-e-home, slice 002); a navbar da página final segue
        # exibindo o nome do usuário.
        response = client.get(reverse("home"), follow=True)
        assert response.status_code == 200
        assert DISPLAY_NAME in response.content.decode()

        # Profile exige login e, autenticado, exibe os dados do usuário.
        response = client.get(reverse("profile"))
        assert response.status_code == 200
        body = response.content.decode()
        assert USERNAME in body
        assert DISPLAY_NAME in body
        # Rótulo de papel (role-labels-ptbr): lista do perfil, NÃO substring
        # solta — o badge do base.html também imprime "médico" e mascararia
        # uma regressão da lista.
        assert re.search(r"Papéis</dt>\s*<dd[^>]*>\s*médico\b", body)

        # Logout encerra a sessão e volta ao login; profile volta a exigir login.
        response = client.post(reverse("logout"))
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("login")
        response = client.get(reverse("profile"))
        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("login"))

    def test_wrong_password_rejected_with_generic_message(self, client: Client) -> None:
        _create_user()

        response = client.post(
            reverse("login"),
            {"username": USERNAME, "password": "senha-errada"},
        )
        assert response.status_code == 200
        assert GENERIC_ERROR in response.content.decode()
        # Nenhuma sessão foi criada: a home continua exigindo login.
        assert client.get(reverse("home")).status_code == 302

    def test_blocked_account_cannot_login(self, client: Client) -> None:
        """R1/R6: conta bloqueada não autentica com credenciais corretas."""
        _create_user(account_status="blocked")

        response = client.post(reverse("login"), {"username": USERNAME, "password": PASSWORD})
        assert response.status_code == 200
        body = response.content.decode()
        assert GENERIC_ERROR in body
        assert "bloqueada" not in body
        assert client.get(reverse("home")).status_code == 302

    def test_removed_account_cannot_login(self, client: Client) -> None:
        """R1/R6: conta removida não autentica com credenciais corretas."""
        _create_user(account_status="removed")

        response = client.post(reverse("login"), {"username": USERNAME, "password": PASSWORD})
        assert response.status_code == 200
        body = response.content.decode()
        assert GENERIC_ERROR in body
        assert "removida" not in body
        assert client.get(reverse("home")).status_code == 302

    def test_profile_password_change(self, client: Client) -> None:
        """R2: troca de senha local no perfil preserva sessão e autentica."""
        _create_user()
        client.post(reverse("login"), {"username": USERNAME, "password": PASSWORD})

        new_password = "Nova-senha-forte-2024"
        response = client.post(
            reverse("profile"),
            {
                "old_password": PASSWORD,
                "new_password1": new_password,
                "new_password2": new_password,
            },
        )
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("profile")

        # Sessão mantida após a troca e a nova senha autentica.
        user = User.objects.get(username=USERNAME)
        assert user.check_password(new_password)
        assert client.get(reverse("profile")).status_code == 200

        client.post(reverse("logout"))
        response = client.post(reverse("login"), {"username": USERNAME, "password": new_password})
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("home")


@pytest.mark.django_db
class TestHomePlaceholder:
    """R4: home autenticada placeholder."""

    def test_home_requires_login(self, client: Client) -> None:
        """Usuário anônimo é redirecionado ao login (R4)."""
        response = client.get(reverse("home"))
        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("login"))

    def test_home_renders_app_name(self, client: Client) -> None:
        """Home autenticada (fallback) renderiza nome do app e papéis do usuário (R3/R4).

        Usa um papel fora do dispatcher (change painel-gerencial-e-home, slice
        002): papéis com fila são redirecionados à sua área de trabalho, então
        o placeholder só é alcançável para papel sem destino na tabela.
        """
        _create_user(first_name="Maria", last_name="Silva", role_names=("nurse",))
        client.post(reverse("login"), {"username": USERNAME, "password": PASSWORD})

        response = client.get(reverse("home"))
        assert response.status_code == 200
        body = response.content.decode()
        assert settings.APP_DISPLAY_NAME in body
        assert DISPLAY_NAME in body
        assert "nurse" in body


@pytest.mark.django_db
class TestLoginRedirectAuthenticated:
    """R5: /login/ com sessão ativa redireciona para a home."""

    def test_login_redirects_authenticated(self, client: Client) -> None:
        _create_user()
        client.post(reverse("login"), {"username": USERNAME, "password": PASSWORD})

        response = client.get(reverse("login"))
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("home")
