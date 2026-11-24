"""Perfil sem troca de senha (bloqueio-troca-de-senha, slice 001, R1-R4).

O perfil é página só de leitura: exibe os dados da conta e a orientação
de que a troca de senha é feita pessoalmente na TI do hospital; qualquer
escrita (POST) responde 405 sem alterar credenciais nem a sessão.
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User

USERNAME = "maria.hemodinamica"
DISPLAY_NAME = "Maria Silva"
PASSWORD = "senha-local-123"
GUIDANCE = "Precisa trocar a senha? Procure pessoalmente a TI do hospital."


def _create_user() -> User:
    """Cria usuário autenticável (admin local por design, ADR-0009)."""
    user = User.objects.create_user(username=USERNAME, password=PASSWORD)
    user.is_superuser = True
    user.first_name = "Maria"
    user.last_name = "Silva"
    user.email = "maria@example.com"
    user.professional_council = "CRM"
    user.professional_council_number = "12345"
    user.save(
        update_fields=[
            "is_superuser",
            "first_name",
            "last_name",
            "email",
            "professional_council",
            "professional_council_number",
        ]
    )
    role, _ = Role.objects.get_or_create(name="doctor")
    user.roles.add(role)
    return user


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste."""
    return Client()


@pytest.mark.django_db
class TestProfileNoPasswordChange:
    """R1-R4: perfil só de leitura, sem affordance de troca de senha."""

    def test_profile_get_displays_data_and_guidance_without_password_fields(
        self, client: Client
    ) -> None:
        """R1: GET autenticado exibe dados + orientação, nenhum campo de senha."""
        _create_user()
        client.post(reverse("login"), {"username": USERNAME, "password": PASSWORD})

        response = client.get(reverse("profile"))
        assert response.status_code == 200
        body = response.content.decode()
        assert USERNAME in body
        assert DISPLAY_NAME in body
        assert "maria@example.com" in body
        assert "médico" in body
        assert "CRM 12345" in body
        assert GUIDANCE in body
        assert 'type="password"' not in body
        assert "old_password" not in body
        assert "new_password" not in body
        assert "Alterar senha" not in body

    def test_profile_post_is_refused_without_changing_password(self, client: Client) -> None:
        """R2: POST com os campos do formulário antigo responde 405."""
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
        assert response.status_code == 405

        user = User.objects.get(username=USERNAME)
        assert user.check_password(PASSWORD)
        assert not user.check_password(new_password)
        # Sessão continua válida após a escrita recusada.
        assert client.get(reverse("profile")).status_code == 200

    def test_profile_requires_login(self, client: Client) -> None:
        """R3: usuário anônimo é redirecionado ao login."""
        response = client.get(reverse("profile"))
        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("login"))

    def test_profile_data_card_uses_full_width(self, client: Client) -> None:
        """R4: card de dados em largura total, sem coluna de senha."""
        _create_user()
        client.post(reverse("login"), {"username": USERNAME, "password": PASSWORD})

        response = client.get(reverse("profile"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "col-md-6" not in body
        assert 'class="col-12"' in body
        assert body.index('class="col-12"') < body.index('<dl class="row profile-dl')
