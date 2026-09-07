"""Fixtures dos testes de ``apps.intake`` (change intake-nir-upload, slice 001).

Uploads ficam em memória (``InMemoryStorage``): a suíte nunca escreve em
``MEDIA_ROOT`` nem depende do filesystem (hermética, no mesmo espírito do
banco de teste isolado). O ``override_settings(STORAGES=...)`` zera o cache do
storage via signal ``setting_changed`` (``django.test.signals``) — cada teste
recria o storage padrão sem vazar estado entre testes.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from apps.accounts.models import Role, User

# Storage de teste: arquivos apenas em memória (nada de disco).
_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.fixture(autouse=True)
def _memory_storage() -> Iterator[None]:
    """Storage default em memória durante cada teste do diretório."""
    with override_settings(STORAGES=_TEST_STORAGES):
        yield


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste."""
    return Client()


@pytest.fixture
def user_factory() -> Callable[[str, str], User]:
    """Cria usuário com um papel (papel criado se não existir)."""

    def _factory(username: str, role_name: str) -> User:
        role, _ = Role.objects.get_or_create(name=role_name)
        user = User.objects.create_user(username=username, password="senha-teste")
        user.roles.add(role)
        return user

    return _factory


@pytest.fixture
def nir_user(user_factory: Callable[[str, str], User]) -> User:
    """NIR com papel único (middleware define o papel ativo automaticamente)."""
    return user_factory("nir-intake", "nir")


@pytest.fixture
def pdf_factory() -> Callable[..., SimpleUploadedFile]:
    """Fábrica de arquivos PDF fake (metadados apenas — o slice não lê o PDF)."""

    counter = itertools.count(1)

    def _factory(
        name: str | None = None, content_type: str = "application/pdf"
    ) -> SimpleUploadedFile:
        ordinal = next(counter)
        filename = name or f"relatorio-{ordinal}.pdf"
        return SimpleUploadedFile(
            filename,
            b"%PDF-1.4 relatorio fake (conteudo nao lido neste slice)",
            content_type=content_type,
        )

    return _factory
