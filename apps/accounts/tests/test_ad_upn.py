"""Testes do provisionamento AD administrativo (change ad-kerberos, slice 001).

Cobre R1–R4 do slice:
- R1/R4: ``User.ad_upn`` (UPN completo ``cpf@dominio``) único e opcional, com
  validação de formato de sufixo livre (floresta multi-domínio);
- R2: ``apps/accounts/admin.py`` registra User/Role — changelist renderiza o
  campo e os formulários (add/change) expõem ``ad_upn`` e o M2M ``roles``;
- R3: salvar usuário com ``ad_upn`` no admin deixa a senha local inutilizável;
  usuários locais (sem ``ad_upn``) preservam a senha;
- R4: break-glass/seed sem ``ad_upn`` continua nullable em massa; migration
  aplicável em banco limpo (pytest ``--reuse-db`` cria o schema completo).
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role

User = get_user_model()

AD_UPN = "12345678901@dominio-teste.local"
OTHER_FOREST_DOMAIN_UPN = "12345678901@outro-hosp.org.br"
ADMIN_USERNAME = "admin-seed"
ADMIN_PASSWORD = "senha-admin-2026"
LOCAL_PASSWORD = "senha-local-forte-2026"


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


@pytest.mark.django_db
class TestAdUpnField:
    """R1/R4: campo ``ad_upn`` único, opcional e validado."""

    def test_create_user_with_ad_upn(self) -> None:
        user = User.objects.create_user(username="ad.um", password=LOCAL_PASSWORD, ad_upn=AD_UPN)
        assert User.objects.get(pk=user.pk).ad_upn == AD_UPN

    def test_unique_raises_integrity_error(self) -> None:
        User.objects.create_user(username="ad.um", password=LOCAL_PASSWORD, ad_upn=AD_UPN)
        with pytest.raises(IntegrityError):
            User.objects.create_user(username="ad.dois", password=LOCAL_PASSWORD, ad_upn=AD_UPN)

    def test_unique_raises_validation_error_on_full_clean(self) -> None:
        User.objects.create_user(username="ad.um", password=LOCAL_PASSWORD, ad_upn=AD_UPN)
        dupe = User(username="ad.dois", ad_upn=AD_UPN)
        with pytest.raises(ValidationError) as excinfo:
            dupe.full_clean()
        assert "ad_upn" in excinfo.value.message_dict

    def test_nullable_for_local_and_seed_users(self) -> None:
        """Seed/break-glass seguem sem ``ad_upn``: criação em massa é livre."""
        for username in ("local.um", "local.dois", "local.tres"):
            User.objects.create_user(username=username, password=LOCAL_PASSWORD)
        User.objects.create_superuser(
            username="break-glass", email="break@example.com", password=LOCAL_PASSWORD
        )
        assert User.objects.filter(ad_upn__isnull=True).count() == 4
        assert User.objects.get(username="break-glass").ad_upn is None

    @pytest.mark.parametrize(
        ("upn",),
        [
            (AD_UPN,),
            (OTHER_FOREST_DOMAIN_UPN,),
            ("99887766554@sesab.ba.gov.br",),
            ("usuario@dominio",),
        ],
    )
    def test_valid_upn_formats_accepted(self, upn: str) -> None:
        """Sufixos de outros domínios da floresta são aceitos (sufixo livre)."""
        user = User(username="valido", ad_upn=upn)
        user.full_clean(exclude=["password"])

    @pytest.mark.parametrize(
        ("upn",),
        [
            ("12345678901",),
            ("sem arroba@dominio",),
            ("cpf@",),
            ("@dominio",),
            ("cpf@@dominio",),
        ],
    )
    def test_invalid_upn_formats_rejected(self, upn: str) -> None:
        user = User(username="invalido", ad_upn=upn)
        with pytest.raises(ValidationError) as excinfo:
            user.full_clean(exclude=["password"])
        assert "ad_upn" in excinfo.value.message_dict


@pytest.mark.django_db
class TestAdminRegistration:
    """R2: Django admin registra User/Role para provisionamento."""

    def test_admin_changelist_renders_users_with_ad_upn(self, admin_client: Client) -> None:
        User.objects.create_user(username="maria.ad", password=LOCAL_PASSWORD, ad_upn=AD_UPN)
        response = admin_client.get(reverse("admin:accounts_user_changelist"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "maria.ad" in body
        assert AD_UPN in body

    def test_admin_form_includes_ad_upn_and_roles(self, admin_client: Client) -> None:
        user = User.objects.create_user(username="maria.ad", password=LOCAL_PASSWORD)

        add_response = admin_client.get(reverse("admin:accounts_user_add"))
        assert add_response.status_code == 200
        add_body = add_response.content.decode()
        assert 'id="id_ad_upn"' in add_body
        assert 'name="roles"' in add_body

        change_url = reverse("admin:accounts_user_change", args=[user.pk])
        change_response = admin_client.get(change_url)
        assert change_response.status_code == 200
        change_body = change_response.content.decode()
        assert 'id="id_ad_upn"' in change_body
        assert 'name="roles"' in change_body

    def test_admin_role_registered(self, admin_client: Client) -> None:
        role = Role.objects.create(name="doctor")
        response = admin_client.get(reverse("admin:accounts_role_change", args=[role.pk]))
        assert response.status_code == 200
        assert role.name in response.content.decode()


@pytest.mark.django_db
class TestAdminAdUpnPasswordRule:
    """R3: usuário com ``ad_upn`` não tem senha local utilizável."""

    def _add_payload(
        self,
        *,
        username: str,
        ad_upn: str,
        usable_password: str,
        password: str = "",
        roles: list[int] | None = None,
    ) -> dict[str, object]:
        return {
            "username": username,
            "ad_upn": ad_upn,
            "usable_password": usable_password,
            "password1": password,
            "password2": password,
            "account_status": "active",
            "roles": roles or [],
        }

    def test_ad_user_has_unusable_password(self, admin_client: Client) -> None:
        """R4: cadastrar usuário AD sem senha local deixa senha inutilizável."""
        role = Role.objects.create(name="doctor")
        payload = self._add_payload(
            username="maria.ad",
            ad_upn=AD_UPN,
            usable_password="false",
            roles=[role.pk],
        )
        response = admin_client.post(reverse("admin:accounts_user_add"), payload)
        assert response.status_code == 302
        user = User.objects.get(username="maria.ad")
        assert user.ad_upn == AD_UPN
        assert user.has_usable_password() is False
        assert list(user.roles.values_list("name", flat=True)) == ["doctor"]

    def test_ad_upn_force_disables_local_password_even_if_typed(self, admin_client: Client) -> None:
        """Senha local digitada para usuário AD é descartada (R3)."""
        payload = self._add_payload(
            username="joao.ad",
            ad_upn=OTHER_FOREST_DOMAIN_UPN,
            usable_password="true",
            password=LOCAL_PASSWORD,
        )
        response = admin_client.post(reverse("admin:accounts_user_add"), payload)
        assert response.status_code == 302
        user = User.objects.get(username="joao.ad")
        assert user.ad_upn == OTHER_FOREST_DOMAIN_UPN
        assert user.has_usable_password() is False

    def test_local_user_without_ad_upn_keeps_usable_password(self, admin_client: Client) -> None:
        """Usuário local (sem ``ad_upn``) criado no admin preserva a senha."""
        payload = self._add_payload(
            username="maria.local",
            ad_upn="",
            usable_password="true",
            password=LOCAL_PASSWORD,
        )
        response = admin_client.post(reverse("admin:accounts_user_add"), payload)
        assert response.status_code == 302
        user = User.objects.get(username="maria.local")
        assert user.ad_upn is None
        assert user.has_usable_password() is True
        assert user.check_password(LOCAL_PASSWORD)
