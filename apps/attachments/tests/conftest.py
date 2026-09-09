"""Fixtures dos testes de ``apps.attachments`` (slices 001 e 002).

Uploads ficam em memória (``InMemoryStorage``) — réplica do conftest do
intake: a suíte nunca escreve em ``MEDIA_ROOT`` nem depende do filesystem
(hermética, no mesmo espírito do banco de teste isolado). O slice 002 soma as
fixtures de anexos processáveis: ``owner_user``, ``case_factory``,
``attachment_record_factory`` (row com conteúdo real) e ``pdf_bytes_factory``
(PDFs reais gerados pelo PyMuPDF em memória — zero binários no repositório,
padrão do ``write_pdf`` dos testes do intake).
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator

import pymupdf
import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.accounts.models import User
from apps.attachments.models import AttachmentStatus, CaseAttachment
from apps.cases.models import Case

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
def owner_user(user_factory: Callable[[str], User]) -> User:
    """Dono (criador) padrão dos casos dos testes do slice 002."""
    return user_factory("owner")


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


@pytest.fixture
def pdf_bytes_factory() -> Callable[[list[str]], bytes]:
    """Gera um PDF real (PyMuPDF) com uma página por texto; retorna os bytes.

    Página com texto ``""`` (ou whitespace) → página sem camada de texto
    (PDF-imagem/scan fake para o caminho vision). Espelho do ``write_pdf`` do
    intake (``apps/intake/tests/test_pdf_utils.py``).
    """

    def _factory(pages: list[str]) -> bytes:
        document = pymupdf.open()  # type: ignore[no-untyped-call]
        try:
            for page_text in pages:
                page = document.new_page()
                if page_text.strip():
                    page.insert_text((72, 72), page_text)
            data = document.tobytes()  # type: ignore[no-untyped-call]
            return bytes(data)
        finally:
            document.close()  # type: ignore[no-untyped-call]

    return _factory


@pytest.fixture
def case_factory() -> Callable[[User], Case]:
    """Cria um ``Case`` ``NEW`` com o dono dado (sem serviço de intake)."""

    def _factory(owner: User) -> Case:
        return Case.objects.create(created_by=owner)

    return _factory


@pytest.fixture
def attachment_record_factory() -> Callable[..., CaseAttachment]:
    """Persiste um ``CaseAttachment`` com o conteúdo dado (storage in-memory).

    Espelho do ``_save_attachment`` do ``test_upload`` (arquivo gravado antes
    do INSERT com o nome gerado pelo path callable); expõe override de status
    e de texto/método já extraídos (cenários de idempotência por etapa).
    """

    counter = itertools.count(1)

    def _factory(
        case: Case,
        *,
        content: bytes,
        user: User,
        content_type: str = "image/jpeg",
        name: str | None = None,
        status: str = AttachmentStatus.PENDING,
        extracted_text: str = "",
        extraction_method: str = "",
    ) -> CaseAttachment:
        ordinal = next(counter)
        extension = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "application/pdf": ".pdf",
        }.get(content_type, ".bin")
        filename = name or f"anexo-{ordinal}{extension}"
        attachment = CaseAttachment(
            case=case,
            content_type=content_type,
            size_bytes=len(content),
            uploaded_by=user,
            # Nome legível do upload (payload da auditoria/trilha usa
            # ``original_filename``, nunca o path UUID).
            original_filename=filename,
            status=status,
            extracted_text=extracted_text,
            extraction_method=extraction_method,
        )
        attachment.file.save(filename, ContentFile(content), save=False)
        attachment.save()
        return attachment

    return _factory
