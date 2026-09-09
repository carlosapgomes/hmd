"""Fixtures dos testes de ``apps.attachments`` (slice 001, R1/R2).

Uploads ficam em memória (``InMemoryStorage``) — réplica do conftest do
intake: a suíte nunca escreve em ``MEDIA_ROOT`` nem depende do filesystem
(hermética, no mesmo espírito do banco de teste isolado).
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.accounts.models import User

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
def user_factory() -> Callable[[str], User]:
    """Cria usuário do caso (sem papéis — testes de model/serviço puros)."""

    def _factory(suffix: str) -> User:
        return User.objects.create_user(
            username=f"nir-att-{suffix}",
            password="senha-teste",
        )

    return _factory


@pytest.fixture
def attachment_factory() -> Callable[..., SimpleUploadedFile]:
    """Fábrica de arquivos de anexo fake (conteúdo não lido neste slice)."""

    counter = itertools.count(1)

    def _factory(
        name: str | None = None,
        content_type: str = "image/jpeg",
        content: bytes | None = None,
    ) -> SimpleUploadedFile:
        ordinal = next(counter)
        extension = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "application/pdf": ".pdf",
        }.get(content_type, ".bin")
        filename = name or f"anexo-{ordinal}{extension}"
        return SimpleUploadedFile(
            filename,
            content or b"anexo fake (conteudo nao lido neste slice)",
            content_type=content_type,
        )

    return _factory
