"""Visualização dos arquivos de anexo no detalhe do NIR (slice PVP-006, R3).

Cada anexo listado ganha link "Abrir anexo" (``target=_blank``, serve) +
visualização colapsável por anexo: PDF → sub-collapse com
``data-pdf-doc-collapse`` + ``embed.pdf-lazy-embed`` com ``data-src`` SEM
``src`` (contrato lazy) + link mobile ao attachment viewer identificando o
arquivo; JPEG/PNG → collapse com ``<img loading="lazy" src=serve>`` + link
direto. Bloco ``<noscript>`` com links diretos. Sem anexos: página idêntica
a antes (sem bloco "Anexos").
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from html import unescape

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.attachments.models import CaseAttachment
from apps.cases.models import Case
from apps.intake.services import create_case_with_documents

NIR_ROLE = "nir"


@pytest.fixture(autouse=True)
def _no_inline_processing() -> Iterator[None]:
    """Criação sem processamento automático: o teste controla o conteúdo."""

    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


def _pdf_attachment(name: str = "laudo-anexo.pdf") -> SimpleUploadedFile:
    """Anexo PDF fake (metadados apenas — a página não lê o conteúdo)."""
    return SimpleUploadedFile(
        name,
        b"%PDF-1.4 conteudo do anexo pdf",
        content_type="application/pdf",
    )


def _jpeg_attachment(name: str = "foto-exame.jpg") -> SimpleUploadedFile:
    """Anexo JPEG fake (metadados apenas — a página não lê o conteúdo)."""
    return SimpleUploadedFile(
        name,
        b"\xff\xd8\xff\xe0 conteudo fake do jpeg",
        content_type="image/jpeg",
    )


def _create_case_with_attachments(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    attachments: list[SimpleUploadedFile],
) -> Case:
    """Cria um caso NEW com 1 PDF e os anexos informados, via serviço real."""
    return create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(),
        procedure_type="art_perif",
        attachments=attachments,
    )


def _detail_body(client: Client, case: Case) -> str:
    response = client.get(reverse("intake:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    return response.content.decode()


def _serve_url(case: Case, attachment: CaseAttachment) -> str:
    return reverse("intake:serve_attachment", args=[case.case_id, attachment.pk])


def _viewer_url(case: Case, attachment: CaseAttachment) -> str:
    return reverse("intake:attachment_pdf_viewer", args=[case.case_id, attachment.pk])


@pytest.mark.django_db
def test_pdf_attachment_has_open_link_and_lazy_collapse(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: anexo PDF ganha "Abrir anexo" + sub-collapse lazy com embed sem src."""
    case = _create_case_with_attachments(nir_user, pdf_factory, [_pdf_attachment()])
    attachment = case.attachments.get()
    serve_url = _serve_url(case, attachment)
    viewer_url = _viewer_url(case, attachment)

    client.force_login(nir_user)
    body = _detail_body(client, case)
    plain = unescape(body)

    # Link de abertura direta (target=_blank, rota de serve do papel).
    assert "Abrir anexo" in body
    assert f'href="{serve_url}"' in body
    assert 'target="_blank"' in body
    # Sub-collapse próprio do anexo com o contrato lazy do embed.
    assert f'id="attachment-collapse-{attachment.pk}"' in body
    assert "data-pdf-doc-collapse" in body
    assert f'data-src="{serve_url}"' in plain
    assert "pdf-lazy-embed" in body
    assert "<embed src=" not in body
    # Link mobile ao viewer dedicado com next do detalhe, identificando o arquivo.
    assert viewer_url in plain
    assert "laudo-anexo.pdf" in body
    assert reverse("intake:case_detail", args=[case.case_id]) in plain


@pytest.mark.django_db
def test_pdf_attachment_mobile_link_identifies_file(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: com dois PDFs, os links mobile do viewer distinguem os arquivos."""
    case = _create_case_with_attachments(
        nir_user,
        pdf_factory,
        [_pdf_attachment("anexo-um.pdf"), _pdf_attachment("anexo-dois.pdf")],
    )

    client.force_login(nir_user)
    body = _detail_body(client, case)

    assert "Anexo 1 — anexo-um.pdf" in body
    assert "Anexo 2 — anexo-dois.pdf" in body


@pytest.mark.django_db
def test_jpeg_attachment_has_lazy_image_and_direct_link(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: anexo JPEG ganha collapse com <img loading=lazy src=serve>, sem viewer."""
    case = _create_case_with_attachments(nir_user, pdf_factory, [_jpeg_attachment()])
    attachment = case.attachments.get()
    serve_url = _serve_url(case, attachment)

    client.force_login(nir_user)
    body = _detail_body(client, case)
    plain = unescape(body)

    assert "Abrir anexo" in body
    assert f'src="{serve_url}"' in plain
    assert 'loading="lazy"' in body
    # O card do relatório tem o próprio embed lazy; o bloco do anexo usa <img>.
    attachment_block = plain[plain.index("Anexos") :]
    assert "<embed" not in attachment_block
    assert "data-pdf-doc-collapse" not in attachment_block
    # Imagem não tem viewer dedicado: nenhuma URL de viewer do anexo na página.
    assert f"attachments/{attachment.pk}/viewer/" not in plain
    assert f'id="attachment-collapse-{attachment.pk}"' in body


@pytest.mark.django_db
def test_attachments_noscript_has_direct_links(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: bloco <noscript> com links diretos de abertura por anexo."""
    case = _create_case_with_attachments(
        nir_user,
        pdf_factory,
        [_pdf_attachment(), _jpeg_attachment()],
    )
    attachments = list(case.attachments.all())

    client.force_login(nir_user)
    body = _detail_body(client, case)
    plain = unescape(body)

    noscript_blocks = []
    search_from = 0
    while True:
        start = body.find("<noscript>", search_from)
        if start < 0:
            break
        end = body.find("</noscript>", start)
        assert end > start
        noscript_blocks.append(plain[start:end])
        search_from = end + len("</noscript>")
    combined = "\n".join(noscript_blocks)
    for attachment in attachments:
        assert _serve_url(case, attachment) in combined
        assert attachment.original_filename in combined


@pytest.mark.django_db
def test_detail_without_attachments_has_no_attachment_block(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: caso sem anexos → página idêntica a antes (sem bloco nem URLs)."""
    case = _create_case_with_attachments(nir_user, pdf_factory, [])

    client.force_login(nir_user)
    body = _detail_body(client, case)

    assert "Anexos" not in body
    assert "Abrir anexo" not in body
    assert "attachment-collapse-" not in body
    assert "attachments/" not in unescape(body)
