"""Serve + viewer de anexo no painel (slice PVP-007, R1).

``dashboard:serve_attachment`` (``case/<uuid>/attachments/<int>/``, PK int —
D6) sob o guard do detalhe (``role_required("manager", "admin")``; outro papel
→ 403; anônimo → login), content-type ORIGINAL do anexo, ``FileResponse`` com
filename original, ``@xframe_options_sameorigin`` e ``Cache-Control:
no-store`` (como os serves do painel); anexo inexistente/de-outro-caso/sem
arquivo → 404 fail-closed (lição P1-4).

``dashboard:attachment_pdf_viewer`` (``.../attachments/<int>/viewer/``)
SOMENTE para ``application/pdf`` (imagem → 404 — imagens usam link direto +
``<img loading=lazy>``, sem viewer dedicado), mesmo guard do serve, render do
viewer mobile com título "Anexo" e ``?next=`` via ``resolve_safe_next_url``.
Molde: ``apps/dashboard/tests/test_case_pdf.py`` (PVP-005) + serves de anexo
de ``apps/intake/tests/test_serve_attachment.py`` (PVP-006).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.attachments.models import CaseAttachment
from apps.cases.models import Case

MANAGER_ROLE = "manager"
ADMIN_ROLE = "admin"
NIR_ROLE = "nir"

# Storage de teste: arquivos apenas em memória (nada de disco) — mesmo
# padrão de ``apps/dashboard/tests/test_case_pdf.py``.
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


def _login(client: Client, user: User, role: str) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


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
    """Anexo PDF fake (metadados apenas — o serve não lê o conteúdo)."""
    return SimpleUploadedFile(
        name,
        b"%PDF-1.4 conteudo do anexo pdf",
        content_type="application/pdf",
    )


def _jpeg_attachment(name: str = "foto-exame.jpg") -> SimpleUploadedFile:
    """Anexo JPEG fake (metadados apenas — o serve não lê o conteúdo)."""
    return SimpleUploadedFile(
        name,
        b"\xff\xd8\xff\xe0 conteudo fake do jpeg",
        content_type="image/jpeg",
    )


def _attachment_without_file(case: Case, user: User) -> CaseAttachment:
    """Row de anexo sem arquivo gravado (fail-closed → 404, lição P1-4)."""
    attachment = CaseAttachment(
        case=case,
        content_type="application/pdf",
        original_filename="sem-arquivo.pdf",
        size_bytes=0,
        uploaded_by=user,
    )
    attachment.save()
    return attachment


def _serve_url(case: Case, attachment: CaseAttachment) -> str:
    return reverse("dashboard:serve_attachment", args=[case.case_id, attachment.pk])


def _viewer_url(case: Case, attachment: CaseAttachment, *, next_url: str = "") -> str:
    url = reverse("dashboard:attachment_pdf_viewer", args=[case.case_id, attachment.pk])
    return f"{url}?next={next_url}" if next_url else url


# ── R1: serve ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_serve_attachment_pdf_manager_200_with_headers(client: Client) -> None:
    """R1: manager recebe o anexo PDF com content-type original, nome, SAMEORIGIN e no-store."""
    manager = _make_user("gestor-anexo-painel")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _pdf_attachment())
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Cache-Control"] == "no-store"
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert attachment.original_filename in response["Content-Disposition"]
    served_bytes = b"".join(response.streaming_content)  # type: ignore[attr-defined]
    assert served_bytes == b"%PDF-1.4 conteudo do anexo pdf"


@pytest.mark.django_db
def test_serve_attachment_jpeg_manager_200_with_content_type(client: Client) -> None:
    """R1: manager recebe o anexo JPEG com content-type image/jpeg e filename original."""
    manager = _make_user("gestor-anexo-jpeg-painel")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _jpeg_attachment())
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert response["Content-Disposition"] == 'inline; filename="foto-exame.jpg"'
    served_bytes = b"".join(response.streaming_content)  # type: ignore[attr-defined]
    assert served_bytes == b"\xff\xd8\xff\xe0 conteudo fake do jpeg"


@pytest.mark.django_db
def test_serve_attachment_admin_200(client: Client) -> None:
    """R1: papel ativo admin também serve o anexo do painel."""
    manager = _make_user("gestor-anexo-painel-admin")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _pdf_attachment())
    admin = _make_user("admin-anexo-painel", ADMIN_ROLE)
    _login(client, admin, ADMIN_ROLE)

    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"


@pytest.mark.django_db
def test_serve_attachment_403_for_other_role(client: Client) -> None:
    """R1: papel ativo fora de manager/admin → 403 (guard de papel), sem o arquivo."""
    manager = _make_user("gestor-anexo-painel-403")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _pdf_attachment())
    nir = _make_user("nir-anexo-painel", NIR_ROLE)
    _login(client, nir, NIR_ROLE)

    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 403


@pytest.mark.django_db
def test_serve_attachment_anonymous_redirects_to_login(client: Client) -> None:
    """R1: anônimo → redirect ao login (composição login_required + role_required)."""
    manager = _make_user("gestor-anexo-painel-anonimo")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _pdf_attachment())

    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
def test_serve_attachment_unknown_case_404(client: Client) -> None:
    """R1: caso inexistente → 404 sem vazar informação."""
    manager = _make_user("gestor-anexo-painel-404")
    _login(client, manager, MANAGER_ROLE)

    response = client.get(reverse("dashboard:serve_attachment", args=[uuid.uuid4(), 1]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_serve_attachment_unknown_attachment_404(client: Client) -> None:
    """R1: anexo inexistente no caso → 404."""
    manager = _make_user("gestor-anexo-painel-inexistente")
    case = Case.objects.create(created_by=manager)
    _add_attachment(case, manager, _pdf_attachment())
    _login(client, manager, MANAGER_ROLE)

    response = client.get(reverse("dashboard:serve_attachment", args=[case.case_id, 999999]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_serve_attachment_wrong_case_404(client: Client) -> None:
    """R1: anexo pertencente a outro caso → 404 sob este caso."""
    manager = _make_user("gestor-anexo-painel-outro-caso")
    case_one = Case.objects.create(created_by=manager)
    case_two = Case.objects.create(created_by=manager)
    attachment_of_other_case = _add_attachment(case_two, manager, _pdf_attachment())
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_serve_url(case_one, attachment_of_other_case))

    assert response.status_code == 404


@pytest.mark.django_db
def test_serve_attachment_without_file_404(client: Client) -> None:
    """R1: anexo sem arquivo responde 404 fail-closed (lição P1-4)."""
    manager = _make_user("gestor-anexo-painel-sem-arquivo")
    case = Case.objects.create(created_by=manager)
    attachment = _attachment_without_file(case, manager)
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 404


# ── R1: viewer ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_attachment_viewer_renders_contract_context(client: Client) -> None:
    """R1: viewer do anexo do painel renderiza o viewer mobile com título/URLs/volta."""
    manager = _make_user("gestor-viewer-anexo-painel")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _pdf_attachment())
    detail_url = reverse("dashboard:case_detail", args=[case.case_id])
    serve_url = reverse("dashboard:serve_attachment", args=[case.case_id, attachment.pk])
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 200
    assert response.context["viewer_title"] == "Anexo"
    assert response.context["pdf_url"] == serve_url
    assert response.context["fallback_pdf_url"] == serve_url
    assert response.context["back_url"] == detail_url
    body = response.content.decode()
    assert "Anexo" in body
    assert "← Voltar" in body
    assert detail_url in body


@pytest.mark.django_db
def test_attachment_viewer_allows_admin_role(client: Client) -> None:
    """R1: papel ativo admin também abre o viewer do anexo do painel."""
    manager = _make_user("gestor-viewer-anexo-painel-admin")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _pdf_attachment())
    detail_url = reverse("dashboard:case_detail", args=[case.case_id])
    admin = _make_user("admin-viewer-anexo-painel", ADMIN_ROLE)
    _login(client, admin, ADMIN_ROLE)

    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 200
    assert response.context["back_url"] == detail_url


@pytest.mark.django_db
def test_attachment_viewer_403_for_other_role(client: Client) -> None:
    """R1: papel ativo fora de manager/admin → 403 no viewer."""
    manager = _make_user("gestor-viewer-anexo-painel-403")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _pdf_attachment())
    nir = _make_user("nir-viewer-anexo-painel", NIR_ROLE)
    _login(client, nir, NIR_ROLE)

    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 403


@pytest.mark.django_db
def test_attachment_viewer_anonymous_redirects_to_login(client: Client) -> None:
    """R1: anônimo → redirect ao login no viewer."""
    manager = _make_user("gestor-viewer-anexo-painel-anonimo")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _pdf_attachment())

    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
def test_attachment_viewer_unknown_case_404(client: Client) -> None:
    """R1: caso inexistente → 404 no viewer."""
    manager = _make_user("gestor-viewer-anexo-painel-404")
    _login(client, manager, MANAGER_ROLE)

    response = client.get(reverse("dashboard:attachment_pdf_viewer", args=[uuid.uuid4(), 1]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_attachment_viewer_unknown_attachment_404(client: Client) -> None:
    """R1: anexo inexistente no caso → 404 no viewer."""
    manager = _make_user("gestor-viewer-anexo-painel-inexistente")
    case = Case.objects.create(created_by=manager)
    _add_attachment(case, manager, _pdf_attachment())
    _login(client, manager, MANAGER_ROLE)

    response = client.get(reverse("dashboard:attachment_pdf_viewer", args=[case.case_id, 999999]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_attachment_viewer_wrong_case_404(client: Client) -> None:
    """R1: anexo de outro caso → 404 no viewer sob este caso."""
    manager = _make_user("gestor-viewer-anexo-painel-outro-caso")
    case_one = Case.objects.create(created_by=manager)
    case_two = Case.objects.create(created_by=manager)
    attachment_of_other_case = _add_attachment(case_two, manager, _pdf_attachment())
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_viewer_url(case_one, attachment_of_other_case))

    assert response.status_code == 404


@pytest.mark.django_db
def test_attachment_viewer_without_file_404(client: Client) -> None:
    """R1: anexo sem arquivo → 404 fail-closed no viewer (lição P1-4)."""
    manager = _make_user("gestor-viewer-anexo-painel-sem-arquivo")
    case = Case.objects.create(created_by=manager)
    attachment = _attachment_without_file(case, manager)
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 404


@pytest.mark.django_db
def test_attachment_viewer_jpeg_404(client: Client) -> None:
    """R1: viewer é SÓ para PDF (D3) — anexo JPEG responde 404 (usa link direto + img lazy)."""
    manager = _make_user("gestor-viewer-anexo-painel-jpeg")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _jpeg_attachment())
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 404


@pytest.mark.django_db
def test_attachment_viewer_next_safe_accepted(client: Client) -> None:
    """R1: ?next= interno é aceito como volta do viewer."""
    manager = _make_user("gestor-viewer-anexo-painel-next")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _pdf_attachment())
    safe_next = reverse("dashboard:home")
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_viewer_url(case, attachment, next_url=safe_next))

    assert response.status_code == 200
    assert response.context["back_url"] == safe_next


@pytest.mark.django_db
def test_attachment_viewer_next_insecure_falls_back(client: Client) -> None:
    """R1: ?next= externo cai no fallback (detalhe do painel)."""
    manager = _make_user("gestor-viewer-anexo-painel-evil")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _pdf_attachment())
    detail_url = reverse("dashboard:case_detail", args=[case.case_id])
    _login(client, manager, MANAGER_ROLE)

    response = client.get(
        _viewer_url(case, attachment, next_url="https://evil.example/"),
    )

    assert response.status_code == 200
    assert response.context["back_url"] == detail_url
    assert "evil.example" not in response.content.decode()


@pytest.mark.django_db
def test_attachment_viewer_missing_next_falls_back(client: Client) -> None:
    """R1: ?next= ausente cai no fallback (detalhe do painel)."""
    manager = _make_user("gestor-viewer-anexo-painel-sem-next")
    case = Case.objects.create(created_by=manager)
    attachment = _add_attachment(case, manager, _pdf_attachment())
    detail_url = reverse("dashboard:case_detail", args=[case.case_id])
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 200
    assert response.context["back_url"] == detail_url
