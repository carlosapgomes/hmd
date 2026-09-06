"""Testes do comando ``seed_admin`` (slice 003, R4 e R6).

Cobre:
- R4: criação idempotente dos 5 papéis fixos e do superusuário multi-role
  multi-role com credenciais via env ``DJANGO_SUPERUSER_USERNAME/PASSWORD``,
  senha hasheada, sem duplicação em execuções repetidas;
- R6: superusuário com ``is_staff = is_superuser = True``.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.accounts.models import Role

User = get_user_model()

EXPECTED_ROLES = ["nir", "doctor", "scheduler", "manager", "admin"]
SEED_USERNAME = "admin"
SEED_PASSWORD = "hmd-admin-secret"


def _seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Executa o seed com as credenciais de ambiente configuradas."""
    monkeypatch.setenv("DJANGO_SUPERUSER_USERNAME", SEED_USERNAME)
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", SEED_PASSWORD)
    call_command("seed_admin")


@pytest.mark.django_db
class TestSeedAdmin:
    """R4/R6: seed administrativo idempotente."""

    def test_seed_creates_roles_and_superuser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed(monkeypatch)

        role_names = set(Role.objects.values_list("name", flat=True))
        assert role_names == set(EXPECTED_ROLES)

        user = User.objects.get(username=SEED_USERNAME)
        assert set(user.roles.values_list("name", flat=True)) == set(EXPECTED_ROLES)
        assert user.roles.count() == 5

    def test_seed_password_hashed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed(monkeypatch)

        user = User.objects.get(username=SEED_USERNAME)
        assert user.password != SEED_PASSWORD
        assert user.check_password(SEED_PASSWORD)

    def test_seed_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Duas execuções não duplicam papéis nem superusuário (R4)."""
        _seed(monkeypatch)
        _seed(monkeypatch)

        assert Role.objects.count() == 5
        assert Role.objects.filter(name__in=EXPECTED_ROLES).count() == 5
        assert User.objects.filter(username=SEED_USERNAME).count() == 1

        user = User.objects.get(username=SEED_USERNAME)
        assert user.roles.count() == 5
        assert user.check_password(SEED_PASSWORD)

    def test_superuser_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Superusuário criado com is_staff = is_superuser = True (R6)."""
        _seed(monkeypatch)

        user = User.objects.get(username=SEED_USERNAME)
        assert user.is_staff is True
        assert user.is_superuser is True
        assert user.is_active is True
