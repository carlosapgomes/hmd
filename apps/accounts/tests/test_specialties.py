"""Testes do slice 001 (change doctor-queue-decision): subtipos de médico.

Cobre R1–R5 do slice:
- R1: model ``DoctorSpecialty(name unique)`` com ``__str__`` e M2M
  ``User.specialties`` (vazio = generalista);
- R2: data migration da ``0003_doctor_specialty`` semeia os 4 subtipos com
  nomes congelados na migration (idempotente — get_or_create);
- R3: helper puro ``user_doctor_subtypes`` (generalista, 1 e N subtipos);
- R4: Django admin expõe ``specialties`` no UserAdmin (atribuição manual);
- R5: invariante DB == ``VALID_DOCTOR_SUBTYPES`` — a migration não importa o
  catálogo (histórico congelado); a fonte única é garantida por este teste.
"""

from collections.abc import Callable
from importlib import import_module
from typing import cast

import pytest
from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import DoctorSpecialty
from apps.accounts.subtypes import user_doctor_subtypes
from apps.cases.procedure_catalog import VALID_DOCTOR_SUBTYPES

User = get_user_model()

EXPECTED_SEED = ("angio", "neuro", "cardio", "radio")
ADMIN_USERNAME = "admin-specialties"
ADMIN_PASSWORD = "senha-admin-2026"


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


def _migration_seed_fn() -> Callable[[object, object], None]:
    """Função de seed da data migration ``0003`` (nomes congelados lá)."""
    migration = import_module("apps.accounts.migrations.0003_doctor_specialty")
    seed = getattr(migration, "seed_doctor_specialties")
    assert callable(seed)
    return cast(Callable[[object, object], None], seed)


@pytest.mark.django_db
class TestDoctorSpecialtyModel:
    """R1: model ``DoctorSpecialty`` espelhando o padrão de ``Role``."""

    def test_specialty_model(self) -> None:
        specialty = DoctorSpecialty.objects.create(name="ficticio")
        assert specialty.name == "ficticio"
        assert str(specialty) == "ficticio"

    def test_specialty_name_unique(self) -> None:
        DoctorSpecialty.objects.create(name="ficticio")
        with pytest.raises(IntegrityError):
            DoctorSpecialty.objects.create(name="ficticio")


@pytest.mark.django_db
class TestSeedMigration:
    """R2: data migration ``0003`` semeia os 4 subtipos, idempotente."""

    def test_seed_creates_four_subtypes(self) -> None:
        names = set(DoctorSpecialty.objects.values_list("name", flat=True))
        assert names == set(EXPECTED_SEED)

    def test_seed_is_idempotent(self) -> None:
        seed = _migration_seed_fn()
        seed(global_apps, None)
        seed(global_apps, None)
        assert DoctorSpecialty.objects.count() == len(EXPECTED_SEED)
        assert set(DoctorSpecialty.objects.values_list("name", flat=True)) == set(EXPECTED_SEED)

    def test_seed_recreates_missing_subtype(self) -> None:
        DoctorSpecialty.objects.filter(name="radio").delete()
        seed = _migration_seed_fn()
        seed(global_apps, None)
        assert DoctorSpecialty.objects.count() == len(EXPECTED_SEED)
        assert DoctorSpecialty.objects.filter(name="radio").exists()


@pytest.mark.django_db
class TestDbCatalogInvariant:
    """R5: o banco semeado iguala a fonte única do catálogo."""

    def test_db_matches_valid_doctor_subtypes(self) -> None:
        db_names = set(DoctorSpecialty.objects.values_list("name", flat=True))
        assert db_names == set(VALID_DOCTOR_SUBTYPES)


@pytest.mark.django_db
class TestUserSpecialtiesM2M:
    """R1: ``User.specialties`` M2M nos dois lados; vazio = generalista."""

    def test_user_without_specialties_is_generalist(self) -> None:
        user = User.objects.create_user(username="generalista", password="testpass123")
        assert user.specialties.count() == 0
        assert list(user.specialties.all()) == []

    def test_m2m_on_both_sides(self) -> None:
        user = User.objects.create_user(username="doc.angio", password="testpass123")
        specialty = DoctorSpecialty.objects.get(name="angio")
        user.specialties.add(specialty)
        assert list(user.specialties.all()) == [specialty]
        assert list(specialty.users.all()) == [user]
        assert specialty.users.filter(pk=user.pk).exists()


@pytest.mark.django_db
class TestUserDoctorSubtypesHelper:
    """R3: helper puro ``user_doctor_subtypes``."""

    def test_helper_generalist_returns_empty_set(self) -> None:
        user = User.objects.create_user(username="generalista", password="testpass123")
        assert user_doctor_subtypes(user) == set()

    def test_helper_with_one_subtype(self) -> None:
        user = User.objects.create_user(username="doc.angio", password="testpass123")
        user.specialties.add(DoctorSpecialty.objects.get(name="angio"))
        assert user_doctor_subtypes(user) == {"angio"}

    def test_helper_with_multiple_subtypes(self) -> None:
        user = User.objects.create_user(username="doc.multi", password="testpass123")
        user.specialties.set(DoctorSpecialty.objects.filter(name__in=("neuro", "cardio")))
        assert user_doctor_subtypes(user) == {"neuro", "cardio"}


@pytest.mark.django_db
class TestAdminSpecialties:
    """R4: ``specialties`` exposto no UserAdmin para atribuição manual."""

    def test_admin_exposes_specialties(self, admin_client: Client) -> None:
        user = User.objects.create_user(username="doc.especial", password="testpass123")

        add_response = admin_client.get(reverse("admin:accounts_user_add"))
        assert add_response.status_code == 200
        assert 'name="specialties"' in add_response.content.decode()

        change_url = reverse("admin:accounts_user_change", args=[user.pk])
        change_response = admin_client.get(change_url)
        assert change_response.status_code == 200
        assert 'name="specialties"' in change_response.content.decode()

    def test_admin_assigns_specialties(self, admin_client: Client) -> None:
        neuro_pk = DoctorSpecialty.objects.get(name="neuro").pk
        cardio_pk = DoctorSpecialty.objects.get(name="cardio").pk
        payload = {
            "username": "doc.admin",
            "usable_password": "true",
            "password1": "senha-forte-2026",
            "password2": "senha-forte-2026",
            "account_status": "active",
            "specialties": [neuro_pk, cardio_pk],
        }
        response = admin_client.post(reverse("admin:accounts_user_add"), payload)
        assert response.status_code == 302
        user = User.objects.get(username="doc.admin")
        assert set(user.specialties.values_list("name", flat=True)) == {"neuro", "cardio"}
