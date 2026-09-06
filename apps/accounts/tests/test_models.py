"""Testes dos modelos de ``apps.accounts`` (slice 003, R1–R3).

Cobre:
- R1: ``Role`` com nome único; ``User(AbstractUser)`` com ``roles`` M2M,
  ``account_status`` (default ``active``) e conselho profissional opcional;
- R2: ``User.clean`` rejeita conselho sem número e número sem conselho com
  ``ValidationError`` associada ao campo (divergência deliberada vs ats-web);
- R3: ``display_name`` e ``professional_registration_display``.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.accounts.models import Role

User = get_user_model()

EXPECTED_ROLE_NAMES = ["nir", "doctor", "scheduler", "manager", "admin"]


@pytest.mark.django_db
class TestRoleModel:
    """R1: modelo ``Role``."""

    def test_create_role_and_str(self) -> None:
        role = Role.objects.create(name="nir")
        assert role.name == "nir"
        assert str(role) == "nir"

    def test_role_name_unique(self) -> None:
        Role.objects.create(name="doctor")
        with pytest.raises(IntegrityError):
            Role.objects.create(name="doctor")


@pytest.mark.django_db
class TestUserModel:
    """R1: modelo ``User(AbstractUser)``."""

    def test_user_can_be_created_without_roles(self) -> None:
        user = User.objects.create_user(username="sem-papel", password="testpass123")
        assert user.roles.count() == 0
        assert list(user.roles.all()) == []

    def test_user_can_have_multiple_roles(self) -> None:
        user = User.objects.create_user(username="multi", password="testpass123")
        role_nir = Role.objects.create(name="nir")
        role_doctor = Role.objects.create(name="doctor")
        user.roles.add(role_nir, role_doctor)
        assert user.roles.count() == 2
        assert set(user.roles.values_list("name", flat=True)) == {"nir", "doctor"}

    def test_account_status_default_active(self) -> None:
        user = User.objects.create_user(username="ativo", password="testpass123")
        assert user.account_status == "active"


@pytest.mark.django_db
class TestUserProfessionalCouncil:
    """R2: validação par-ou-nada do conselho profissional."""

    def test_default_empty(self) -> None:
        user = User.objects.create_user(username="sem-conselho", password="testpass123")
        assert user.professional_council == ""
        assert user.professional_council_number == ""

    def test_both_empty_valid(self) -> None:
        user = User.objects.create_user(username="vazio", password="testpass123")
        user.full_clean()

    def test_both_filled_valid(self) -> None:
        user = User.objects.create_user(username="crm", password="testpass123")
        user.professional_council = "CRM"
        user.professional_council_number = "12345"
        user.full_clean()

    def test_council_requires_pair(self) -> None:
        """Conselho sem número e número sem conselho são rejeitados no campo."""
        user = User.objects.create_user(username="so-conselho", password="testpass123")
        user.professional_council = "CRM"
        with pytest.raises(ValidationError) as excinfo:
            user.full_clean()
        assert "professional_council_number" in excinfo.value.message_dict

        user2 = User.objects.create_user(username="so-numero", password="testpass123")
        user2.professional_council_number = "67890"
        with pytest.raises(ValidationError) as excinfo2:
            user2.full_clean()
        assert "professional_council" in excinfo2.value.message_dict


@pytest.mark.django_db
class TestUserDisplayHelpers:
    """R3: helpers de exibição do ``User``."""

    def test_display_name_uses_full_name(self) -> None:
        user = User.objects.create_user(
            username="doc@test.com",
            password="pass123",
            first_name="Maria",
            last_name="Silva",
        )
        assert user.display_name == "Maria Silva"

    def test_display_name_fallback(self) -> None:
        """display_name cai para o username quando não há nome completo."""
        user = User.objects.create_user(username="drjoao", password="pass123")
        assert user.display_name == "drjoao"

    def test_professional_registration_display_with_council(self) -> None:
        user = User.objects.create_user(username="crm@test.com", password="pass123")
        user.professional_council = "CRM"
        user.professional_council_number = "12345"
        user.save()
        assert user.professional_registration_display == "CRM 12345"

    def test_professional_registration_display_empty(self) -> None:
        user = User.objects.create_user(username="sem-conselho", password="pass123")
        assert user.professional_registration_display == ""
