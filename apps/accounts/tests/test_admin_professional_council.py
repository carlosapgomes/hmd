"""Provisionamento do par de conselho profissional no Django admin.

Cobre R1–R5 do slice 001 (change registro-profissional-resposta-final,
decisão D6):

- R1: o formulário de EDIÇÃO (``fieldsets``) expõe
  ``professional_council``/``professional_council_number`` juntos numa seção
  própria («Profissional»);
- R2: o formulário de CRIAÇÃO (``add_fieldsets``) expõe o mesmo par na mesma
  seção;
- R3: salvar o par completo persiste os dois valores e
  ``professional_registration_display`` devolve ``"{conselho} {número}"``;
- R4: salvar parcial (conselho sem número) é rejeitado pelo formulário do
  admin com erro apontando o campo pendente; nada é persistido;
- R5: salvar os dois vazios persiste vazio e o registro formatado devolve
  ``""``.

Molde: ``test_ad_upn.py`` (client autenticado como superusuário,
``admin_client``). Sem validação duplicada no admin — a par-ou-nada vive no
modelo (``User.clean()``); o admin só a superficia.
"""

from typing import Any, cast

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.accounts.admin import UserAdmin
from apps.accounts.models import User as UserModel

User = get_user_model()

ADMIN_USERNAME = "admin-conselho"
ADMIN_PASSWORD = "senha-admin-2026"
LOCAL_PASSWORD = "senha-local-forte-2026"
COUNCIL = "CRM"
COUNCIL_NUMBER = "12345"


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste (padrão dos testes do app)."""
    return Client()


@pytest.fixture
def admin_client(client: Client) -> Client:
    """Client autenticado como superusuário (superfície do Django admin)."""
    superuser = User.objects.create_superuser(
        username=ADMIN_USERNAME,
        email="admin@example.com",
        password=ADMIN_PASSWORD,
    )
    client.force_login(superuser)
    return client


@pytest.fixture
def user_admin() -> UserAdmin:
    """Instância do ``UserAdmin`` registrado (estrutura de fieldsets)."""
    return UserAdmin(model=User, admin_site=admin.site)


def _profissional_fields(user_admin: UserAdmin, *, add: bool = False) -> tuple[str, ...]:
    """Campos da seção «Profissional» (``fieldsets`` ou ``add_fieldsets``).

    Devolve tupla vazia quando a seção não existe (falha genuína de ausência).
    """
    fieldsets: Any = user_admin.add_fieldsets if add else user_admin.fieldsets
    for title, options in cast("list[tuple[str, dict[str, Any]]]", fieldsets or ()):
        if title == "Profissional":
            return tuple(cast("tuple[str, ...]", options["fields"]))
    return ()


def _change_payload(
    user: UserModel,
    *,
    professional_council: str,
    professional_council_number: str,
) -> dict[str, object]:
    """Payload do POST de edição (espelha os campos do formulário real).

    Campos não citados partem do estado atual do usuário (edição limpa).
    """
    return {
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "is_active": "on" if user.is_active else "",
        "ad_upn": user.ad_upn or "",
        "account_status": user.account_status,
        "roles": [],
        "specialties": [],
        "groups": [],
        "user_permissions": [],
        "last_login_0": "",
        "last_login_1": "",
        "date_joined_0": user.date_joined.strftime("%Y-%m-%d"),
        "date_joined_1": user.date_joined.strftime("%H:%M:%S"),
        "professional_council": professional_council,
        "professional_council_number": professional_council_number,
    }


def _add_payload(
    *,
    username: str,
    professional_council: str,
    professional_council_number: str,
) -> dict[str, object]:
    return {
        "username": username,
        "ad_upn": "",
        "usable_password": "true",
        "password1": LOCAL_PASSWORD,
        "password2": LOCAL_PASSWORD,
        "account_status": "active",
        "roles": [],
        "specialties": [],
        "professional_council": professional_council,
        "professional_council_number": professional_council_number,
    }


@pytest.mark.django_db
class TestProfessionalCouncilFieldsets:
    """R1/R2: a seção «Profissional» existe nos formulários de edição/criação."""

    def test_fieldsets_expose_professional_pair(self, user_admin: UserAdmin) -> None:
        """R1: ``fieldsets`` expõem o par junto na seção «Profissional»."""
        assert _profissional_fields(user_admin) == (
            "professional_council",
            "professional_council_number",
        )

    def test_add_fieldsets_expose_professional_pair(self, user_admin: UserAdmin) -> None:
        """R2: ``add_fieldsets`` expõem o par junto na seção «Profissional»."""
        assert _profissional_fields(user_admin, add=True) == (
            "professional_council",
            "professional_council_number",
        )

    def test_change_and_add_pages_render_pair_fields(self, admin_client: Client) -> None:
        """R1/R2: as páginas de edição e criação renderizam os dois campos."""
        user = User.objects.create_user(username="medico.render", password=LOCAL_PASSWORD)

        change_response = admin_client.get(reverse("admin:accounts_user_change", args=[user.pk]))
        assert change_response.status_code == 200
        change_body = change_response.content.decode()
        assert 'id="id_professional_council"' in change_body
        assert 'id="id_professional_council_number"' in change_body

        add_response = admin_client.get(reverse("admin:accounts_user_add"))
        assert add_response.status_code == 200
        add_body = add_response.content.decode()
        assert 'id="id_professional_council"' in add_body
        assert 'id="id_professional_council_number"' in add_body


@pytest.mark.django_db
class TestProfessionalCouncilAdd:
    """R3/R4/R5 (criação): o par é provisionado via formulário de criação."""

    def test_add_complete_pair_persists_and_formats(self, admin_client: Client) -> None:
        """R3: par completo persiste e ``professional_registration_display`` formata."""
        payload = _add_payload(
            username="medico.crm",
            professional_council=COUNCIL,
            professional_council_number=COUNCIL_NUMBER,
        )
        response = admin_client.post(reverse("admin:accounts_user_add"), payload)
        assert response.status_code == 302
        user = User.objects.get(username="medico.crm")
        assert user.professional_council == COUNCIL
        assert user.professional_council_number == COUNCIL_NUMBER
        assert user.professional_registration_display == "CRM 12345"

    def test_add_partial_pair_rejected_pointing_at_pending_field(
        self, admin_client: Client
    ) -> None:
        """R4: conselho sem número é rejeitado com erro no campo pendente."""
        payload = _add_payload(
            username="medico.parcial",
            professional_council=COUNCIL,
            professional_council_number="",
        )
        response = admin_client.post(reverse("admin:accounts_user_add"), payload)
        assert response.status_code == 200
        body = response.content.decode()
        assert "professional_council_number" in body
        assert "Preencha o número do conselho profissional" in body
        assert not User.objects.filter(username="medico.parcial").exists()

    def test_add_empty_pair_persists_empty(self, admin_client: Client) -> None:
        """R5: par vazio+vazio persiste vazio e o registro formatado é ``""``."""
        payload = _add_payload(
            username="medico.sem-registro",
            professional_council="",
            professional_council_number="",
        )
        response = admin_client.post(reverse("admin:accounts_user_add"), payload)
        assert response.status_code == 302
        user = User.objects.get(username="medico.sem-registro")
        assert user.professional_council == ""
        assert user.professional_council_number == ""
        assert user.professional_registration_display == ""


@pytest.mark.django_db
class TestProfessionalCouncilChange:
    """R3/R4/R5 (edição): o par é provisionado/editado via formulário de edição."""

    def test_change_complete_pair_persists_and_formats(self, admin_client: Client) -> None:
        """R3: editar com par completo persiste e formata ``"{conselho} {número}"``."""
        user = User.objects.create_user(username="medico.edit", password=LOCAL_PASSWORD)
        url = reverse("admin:accounts_user_change", args=[user.pk])
        payload = _change_payload(
            user,
            professional_council=COUNCIL,
            professional_council_number=COUNCIL_NUMBER,
        )
        response = admin_client.post(url, payload)
        assert response.status_code == 302
        user.refresh_from_db()
        assert user.professional_council == COUNCIL
        assert user.professional_council_number == COUNCIL_NUMBER
        assert user.professional_registration_display == "CRM 12345"

    def test_change_partial_pair_rejected_and_not_persisted(self, admin_client: Client) -> None:
        """R4: editar com conselho sem número é rejeitado; nada é persistido."""
        user = User.objects.create_user(username="medico.partial", password=LOCAL_PASSWORD)
        url = reverse("admin:accounts_user_change", args=[user.pk])
        payload = _change_payload(
            user,
            professional_council=COUNCIL,
            professional_council_number="",
        )
        response = admin_client.post(url, payload)
        assert response.status_code == 200
        body = response.content.decode()
        assert "professional_council_number" in body
        assert "Preencha o número do conselho profissional" in body
        user.refresh_from_db()
        assert user.professional_council == ""
        assert user.professional_council_number == ""

    def test_change_clears_filled_pair_to_empty(self, admin_client: Client) -> None:
        """R5: editar usuário com par preenchido salvando vazio persiste vazio."""
        user = User.objects.create_user(username="medico.clear", password=LOCAL_PASSWORD)
        user.professional_council = COUNCIL
        user.professional_council_number = COUNCIL_NUMBER
        user.save()
        assert user.professional_registration_display == "CRM 12345"
        url = reverse("admin:accounts_user_change", args=[user.pk])
        payload = _change_payload(
            user,
            professional_council="",
            professional_council_number="",
        )
        response = admin_client.post(url, payload)
        assert response.status_code == 302
        user.refresh_from_db()
        assert user.professional_council == ""
        assert user.professional_council_number == ""
        assert user.professional_registration_display == ""
