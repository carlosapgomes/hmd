"""Testes do switch-role com zero papéis (slice 005 do change 02).

Cobre o fechamento da nota de arquivamento do change 01: ``switch_role_view``
é o path isento do ``ActiveRoleMiddleware``, então a validação de lista vazia
precisa acontecer na própria view. GET e POST com 0 papéis encerram a sessão
com ``NO_ROLES_MESSAGE`` e redirecionam ao login; o fluxo com ≥1 papel fica
inalterado (regressão coberta por ``test_active_role.py``).
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User

USERNAME = "sem.papel"
PASSWORD = "senha-local-123"
ZERO_ROLES_MESSAGE = "não possui papéis atribuídos"


def _create_user_with_roles(*, role_names: list[str]) -> User:
    """Cria usuário com os papéis informados (senha fixa ``PASSWORD``)."""
    user = User.objects.create_user(username=USERNAME, password=PASSWORD)
    for name in role_names:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste."""
    return Client()


@pytest.mark.django_db
class TestSwitchRoleZeroRoles:
    """R1/R2/R4: zero papéis encerra a sessão no GET e no POST."""

    def test_get_zero_roles_logs_out(self, client: Client) -> None:
        """GET em /switch-role/ com 0 papéis encerra a sessão e vai ao login."""
        _create_user_with_roles(role_names=[])
        client.force_login(User.objects.get(username=USERNAME))

        response = client.get(reverse("switch_role"))

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("login")
        assert "_auth_user_id" not in client.session

        # A mensagem explicativa aparece na tela de login.
        login_page = client.get(reverse("login"))
        assert login_page.status_code == 200
        assert ZERO_ROLES_MESSAGE in login_page.content.decode()

    def test_post_zero_roles_logs_out(self, client: Client) -> None:
        """POST em /switch-role/ com 0 papéis encerra a sessão e vai ao login.

        A validação roda antes de qualquer processamento do payload.
        """
        _create_user_with_roles(role_names=[])
        client.force_login(User.objects.get(username=USERNAME))

        response = client.post(reverse("switch_role"), {"role": "doctor"})

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("login")
        assert "_auth_user_id" not in client.session

        # A mensagem explicativa aparece na tela de login.
        login_page = client.get(reverse("login"))
        assert login_page.status_code == 200
        assert ZERO_ROLES_MESSAGE in login_page.content.decode()
