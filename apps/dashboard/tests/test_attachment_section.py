"""Seção de anexos no detalhe do painel (slice PVP-007, R2).

A seção "Anexos" entra APÓS os cards de documento (D8): cada anexo listado
tem nome, status legível (``get_status_display``) e visualização do arquivo —
PDF → sub-collapse lazy (``data-pdf-doc-collapse`` + ``embed.pdf-lazy-embed``
com ``data-src`` SEM ``src``) + link mobile (``d-md-none``) ao viewer
dedicado identificando o arquivo ("Anexo N — nome") com ``?next=``; JPEG →
collapse com ``<img loading="lazy" src=serve>`` + link direto (sem viewer).
Bloco ``<noscript>`` com links diretos por anexo. Sem anexos: seção omitida.

Molde: ``apps/dashboard/tests/test_detail_cards.py`` (PVP-005) + seção de
anexos de ``apps/intake/tests/test_attachment_cards.py`` (PVP-006).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from html import unescape

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.attachments.models import CaseAttachment
from apps.cases.models import Case, CaseDocument

MANAGER_ROLE = "manager"

# Storage de teste: arquivos apenas em memória (nada de disco) — mesmo
# padrão de ``apps/dashboard/tests/test_detail_cards.py``.
_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.fixture(autouse=True)
def _memory_storage() -> Iterator[None]:
    """Storage default em memória durante cada teste do arquivo."""
    with override_settings(STORAGES=_TEST_STORAGES):
        yield


def _make_user(username: str, role_name: str = MANAGER_ROLE) -> User:
    """Usuário com um papel (para o middleware resolver o papel ativo)."""
    role, _ = Role.objects.get_or_create(name=role_name)
    user = User.objects.create_user(username=username, password="senha-teste")
    user.roles.add(role)
    return user


def _login(client: Client, user: User, role: str = MANAGER_ROLE) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _add_document(case: Case, uploaded_by: User, *, position: int) -> CaseDocument:
    """Anexa um CaseDocument fake ao caso na posição informada."""
    uploaded = SimpleUploadedFile(
        f"relatorio-{position}.pdf",
        f"%PDF-1.4 conteudo do pdf {position}".encode(),
        content_type="application/pdf",
    )
    return CaseDocument.objects.create(
        case=case,
        file=uploaded,
        position=position,
        original_filename=f"relatorio-painel-{position}.pdf",
        content_type="application/pdf",
        size_bytes=len(uploaded.read()),
        uploaded_by=uploaded_by,
    )


def _add_attachment(case: Case, uploaded_by: User, uploaded: SimpleUploadedFile) -> CaseAttachment:
    """Persiste um anexo com arquivo (espelho de ``_save_attachment`` de attachments)."""
    attachment = CaseAttachment(
        case=case,
        content_type=(uploaded.content_type or "").lower(),
        original_filename=uploaded.name or "",
        size_bytes=uploaded.size or 0,
        uploaded_by=uploaded_by,
    )
    attachment.file.save(uploaded.name or "", uploaded, save=False)
    attachment.save()
    return attachment


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


def _detail_url(case: Case) -> str:
    return reverse("dashboard:case_detail", args=[case.case_id])


def _detail_body(client: Client, case: Case) -> str:
    response = client.get(_detail_url(case))
    assert response.status_code == 200
    return response.content.decode()


def _serve_url(case: Case, attachment: CaseAttachment) -> str:
    return reverse("dashboard:serve_attachment", args=[case.case_id, attachment.pk])


def _viewer_url(case: Case, attachment: CaseAttachment) -> str:
    return reverse("dashboard:attachment_pdf_viewer", args=[case.case_id, attachment.pk])


# ── R2: seção de anexos ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_pdf_attachment_has_name_status_open_link_and_lazy_collapse(
    client: Client,
) -> None:
    """R2: anexo PDF lista nome + status legível + "Abrir anexo" + collapse lazy sem src."""
    manager = _make_user("gestor-secao-anexo-pdf")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _pdf_attachment())
    detail_url = _detail_url(case)
    serve_url = _serve_url(case, attachment)
    viewer_url = _viewer_url(case, attachment)
    _login(client, manager)

    body = _detail_body(client, case)
    plain = unescape(body)

    assert "Anexos" in body
    assert attachment.original_filename in body
    assert attachment.get_status_display() in body
    assert "Abrir anexo" in body
    assert f'href="{serve_url}"' in body
    assert 'target="_blank"' in body
    assert f'id="attachment-collapse-{attachment.pk}"' in body
    assert "data-pdf-doc-collapse" in body
    assert f'data-src="{serve_url}"' in plain
    assert "pdf-lazy-embed" in body
    assert "<embed src=" not in body
    assert viewer_url in plain
    assert detail_url in plain


@pytest.mark.django_db
def test_pdf_attachment_mobile_link_identifies_file(client: Client) -> None:
    """R2: link mobile ao viewer identifica o arquivo ("Anexo N — nome")."""
    manager = _make_user("gestor-secao-anexo-mobile")
    case = Case.objects.create(created_by=manager)
    _add_attachment(case, manager, _pdf_attachment("anexo-um.pdf"))
    _add_attachment(case, manager, _pdf_attachment("anexo-dois.pdf"))
    _login(client, manager)

    body = _detail_body(client, case)

    assert "Anexo 1 — anexo-um.pdf" in body
    assert "Anexo 2 — anexo-dois.pdf" in body


@pytest.mark.django_db
def test_jpeg_attachment_has_lazy_image_and_direct_link(client: Client) -> None:
    """R2: anexo JPEG ganha collapse com <img loading=lazy src=serve>, sem viewer."""
    manager = _make_user("gestor-secao-anexo-jpeg")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _jpeg_attachment())
    serve_url = _serve_url(case, attachment)
    _login(client, manager)

    body = _detail_body(client, case)
    plain = unescape(body)

    assert "Anexos" in body
    assert attachment.original_filename in body
    assert f'src="{serve_url}"' in plain
    assert 'loading="lazy"' in body
    attachment_block = plain[plain.index("Anexos") :]
    assert "<embed" not in attachment_block
    assert "data-pdf-doc-collapse" not in attachment_block
    assert f"attachments/{attachment.pk}/viewer/" not in plain
    assert f'id="attachment-collapse-{attachment.pk}"' in body


@pytest.mark.django_db
def test_attachments_noscript_has_direct_links(client: Client) -> None:
    """R2: bloco <noscript> com links diretos de abertura por anexo."""
    manager = _make_user("gestor-secao-anexo-noscript")
    case = Case.objects.create(created_by=manager)
    _add_attachment(case, manager, _pdf_attachment())
    _add_attachment(case, manager, _jpeg_attachment())
    attachments = list(case.attachments.all())
    _login(client, manager)

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
    assert len(noscript_blocks) >= 1, "esperado >=1 bloco noscript com links de anexo"
    combined = "\n".join(noscript_blocks)
    for attachment in attachments:
        assert _serve_url(case, attachment) in combined
        assert attachment.original_filename in combined


@pytest.mark.django_db
def test_detail_without_attachments_omits_section(client: Client) -> None:
    """R2: caso sem anexos → nenhuma seção de anexos é renderizada."""
    manager = _make_user("gestor-secao-sem-anexo")
    case = Case.objects.create(created_by=manager)
    _add_document(case, manager, position=1)
    _login(client, manager)

    body = _detail_body(client, case)

    assert "Anexos" not in body
    assert "Abrir anexo" not in body
    assert "attachment-collapse-" not in body
    assert "attachments/" not in unescape(body)
    # O comentário descritivo da seção vive DENTRO do {% if %}: nenhuma
    # substring característica dele pode vazar no HTML sem anexos.
    assert "Seção de anexos" not in body
    assert "get_status_display" not in body


@pytest.mark.django_db
def test_attachment_section_position_after_document_cards_before_trail(
    client: Client,
) -> None:
    """R2/D8: a seção de anexos fica APÓS os cards de documento e ANTES da trilha."""
    manager = _make_user("gestor-secao-anexo-posicao")
    case = Case.objects.create(created_by=manager)
    _add_document(case, manager, position=1)
    _add_attachment(case, manager, _pdf_attachment())
    _login(client, manager)

    body = _detail_body(client, case)

    pdf_index = body.index("Visualizar PDF Original")
    attachments_index = body.index("Anexos")
    trail_index = body.index("Trilha de eventos")
    assert pdf_index < attachments_index < trail_index, (
        "seção fora da ordem D8 (pdf → anexos → trilha)"
    )


@pytest.mark.django_db
def test_two_attachments_are_distinguishable(client: Client) -> None:
    """R2: dois anexos (PDF + JPEG) renderizam distinguíveis no detalhe do painel."""
    manager = _make_user("gestor-secao-dois-anexos")
    case = Case.objects.create(created_by=manager)
    _add_attachment(case, manager, _pdf_attachment("anexo-um.pdf"))
    _add_attachment(case, manager, _jpeg_attachment())
    _login(client, manager)

    body = _detail_body(client, case)

    assert "anexo-um.pdf" in body
    assert "foto-exame.jpg" in body
    mobile_viewer_anchors = re.findall(r"(<a[^>]*d-md-none[^>]*>.*?</a>)", body, re.DOTALL)
    viewer_links = [a for a in mobile_viewer_anchors if "/viewer/" in a.split(">", 1)[0]]
    assert len(viewer_links) == 1, (
        f"esperado 1 link mobile do viewer (só o PDF), obtidos {len(viewer_links)}"
    )
    assert (
        "Anexo 1 — anexo-um.pdf"
        in re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", viewer_links[0])).strip()
    )
