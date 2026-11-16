"""Serve + viewer de PDF original do painel (slice PVP-005, R1).

``dashboard:case_pdf`` (``case/<uuid>/pdf/<position>/``) entrega o PDF do
documento via ``FileResponse`` (content-type ``application/pdf``, nome
original) com ``@xframe_options_sameorigin`` + ``Cache-Control: no-store``;
``dashboard:pdf_viewer`` (``.../viewer/``) renderiza o viewer mobile com
``viewer_title``/``pdf_url``/``fallback``/``back_url``. Ambos sob
``role_required("manager", "admin")`` (outro papel ativo → 403; anônimo →
login) e 404 para caso/position inexistente ou documento sem arquivo.
Molde: ``apps/scheduler/views.py::case_pdf`` (serve por position).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.cases.models import Case, CaseDocument

MANAGER_ROLE = "manager"
ADMIN_ROLE = "admin"
NIR_ROLE = "nir"

# Storage de teste: arquivos apenas em memória (nada de disco) — mesmo
# padrão de ``apps/scheduler/tests/test_detail_cards.py``.
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


def _serve_url(case: Case, position: int) -> str:
    return reverse("dashboard:case_pdf", args=[case.case_id, position])


def _viewer_url(case: Case, position: int, *, next_url: str = "") -> str:
    url = reverse("dashboard:pdf_viewer", args=[case.case_id, position])
    return f"{url}?next={next_url}" if next_url else url


# ── R1: serve ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_case_pdf_manager_200_with_headers(client: Client) -> None:
    """R1: manager recebe o PDF com content-type, nome original, SAMEORIGIN e no-store."""
    manager = _make_user("gestor-pdf-painel")
    case = Case.objects.create(created_by=manager)
    document = _add_document(case, manager, position=1)
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_serve_url(case, document.position))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Cache-Control"] == "no-store"
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert document.original_filename in response["Content-Disposition"]
    served_bytes = b"".join(response.streaming_content)  # type: ignore[attr-defined]
    assert served_bytes == b"%PDF-1.4 conteudo do pdf 1"


@pytest.mark.django_db
def test_case_pdf_admin_200(client: Client) -> None:
    """R1: papel ativo admin também serve o PDF do painel."""
    manager = _make_user("gestor-pdf-painel-admin")
    case = Case.objects.create(created_by=manager)
    document = _add_document(case, manager, position=1)
    admin = _make_user("admin-pdf-painel", ADMIN_ROLE)
    _login(client, admin, ADMIN_ROLE)

    response = client.get(_serve_url(case, document.position))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"


@pytest.mark.django_db
def test_case_pdf_403_for_other_role(client: Client) -> None:
    """R1: papel ativo fora de manager/admin → 403 (guard de papel), sem o PDF."""
    manager = _make_user("gestor-pdf-painel-403")
    case = Case.objects.create(created_by=manager)
    document = _add_document(case, manager, position=1)
    nir = _make_user("nir-pdf-painel", NIR_ROLE)
    _login(client, nir, NIR_ROLE)

    response = client.get(_serve_url(case, document.position))

    assert response.status_code == 403


@pytest.mark.django_db
def test_case_pdf_anonymous_redirects_to_login(client: Client) -> None:
    """R1: anônimo → redirect ao login (composição login_required + role_required)."""
    manager = _make_user("gestor-pdf-painel-anonimo")
    case = Case.objects.create(created_by=manager)
    document = _add_document(case, manager, position=1)

    response = client.get(_serve_url(case, document.position))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
def test_case_pdf_unknown_case_404(client: Client) -> None:
    """R1: caso inexistente → 404 sem vazar informação."""
    manager = _make_user("gestor-pdf-painel-404")
    _login(client, manager, MANAGER_ROLE)

    response = client.get(reverse("dashboard:case_pdf", args=[uuid.uuid4(), 1]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_case_pdf_unknown_position_404(client: Client) -> None:
    """R1: position sem documento no caso → 404."""
    manager = _make_user("gestor-pdf-painel-posicao")
    case = Case.objects.create(created_by=manager)
    _add_document(case, manager, position=1)
    _login(client, manager, MANAGER_ROLE)

    response = client.get(reverse("dashboard:case_pdf", args=[case.case_id, 9]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_case_pdf_document_without_file_404(client: Client) -> None:
    """R1: documento sem arquivo responde 404 fail-closed (lição P1-4 do PVP-002)."""
    manager = _make_user("gestor-pdf-painel-sem-arquivo")
    case = Case.objects.create(created_by=manager)
    CaseDocument(
        case=case,
        position=99,
        original_filename="",
        content_type="application/pdf",
        size_bytes=0,
        uploaded_by=manager,
    ).save()
    _login(client, manager, MANAGER_ROLE)

    response = client.get(reverse("dashboard:case_pdf", args=[case.case_id, 99]))

    assert response.status_code == 404


# ── R1: viewer ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_pdf_viewer_renders_contract_context(client: Client) -> None:
    """R1: viewer do painel renderiza o viewer mobile com título/URLs/volta."""
    manager = _make_user("gestor-viewer-painel")
    case = Case.objects.create(created_by=manager)
    document = _add_document(case, manager, position=1)
    detail_url = reverse("dashboard:case_detail", args=[case.case_id])
    serve_url = reverse("dashboard:case_pdf", args=[case.case_id, document.position])
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 200
    assert response.context["viewer_title"] == "PDF Original"
    assert response.context["pdf_url"] == serve_url
    assert response.context["fallback_pdf_url"] == serve_url
    assert response.context["back_url"] == detail_url
    body = response.content.decode()
    assert "PDF Original" in body
    assert "← Voltar" in body
    assert detail_url in body


@pytest.mark.django_db
def test_pdf_viewer_allows_admin_role(client: Client) -> None:
    """R1: papel ativo admin também abre o viewer do painel."""
    manager = _make_user("gestor-viewer-painel-admin")
    case = Case.objects.create(created_by=manager)
    document = _add_document(case, manager, position=1)
    detail_url = reverse("dashboard:case_detail", args=[case.case_id])
    admin = _make_user("admin-viewer-painel", ADMIN_ROLE)
    _login(client, admin, ADMIN_ROLE)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 200
    assert response.context["back_url"] == detail_url


@pytest.mark.django_db
def test_pdf_viewer_403_for_other_role(client: Client) -> None:
    """R1: papel ativo fora de manager/admin → 403 no viewer."""
    manager = _make_user("gestor-viewer-painel-403")
    case = Case.objects.create(created_by=manager)
    document = _add_document(case, manager, position=1)
    nir = _make_user("nir-viewer-painel", NIR_ROLE)
    _login(client, nir, NIR_ROLE)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 403


@pytest.mark.django_db
def test_pdf_viewer_anonymous_redirects_to_login(client: Client) -> None:
    """R1: anônimo → redirect ao login no viewer."""
    manager = _make_user("gestor-viewer-painel-anonimo")
    case = Case.objects.create(created_by=manager)
    document = _add_document(case, manager, position=1)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
def test_pdf_viewer_unknown_case_404(client: Client) -> None:
    """R1: caso inexistente → 404 no viewer."""
    manager = _make_user("gestor-viewer-painel-404")
    _login(client, manager, MANAGER_ROLE)

    response = client.get(reverse("dashboard:pdf_viewer", args=[uuid.uuid4(), 1]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_unknown_position_404(client: Client) -> None:
    """R1: position sem documento no caso → 404 no viewer."""
    manager = _make_user("gestor-viewer-painel-posicao")
    case = Case.objects.create(created_by=manager)
    _add_document(case, manager, position=1)
    _login(client, manager, MANAGER_ROLE)

    response = client.get(reverse("dashboard:pdf_viewer", args=[case.case_id, 2]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_document_without_file_404(client: Client) -> None:
    """R1: documento sem arquivo → 404 fail-closed no viewer (lição P1-4)."""
    manager = _make_user("gestor-viewer-painel-sem-arquivo")
    case = Case.objects.create(created_by=manager)
    CaseDocument(
        case=case,
        position=99,
        original_filename="",
        content_type="application/pdf",
        size_bytes=0,
        uploaded_by=manager,
    ).save()
    _login(client, manager, MANAGER_ROLE)

    response = client.get(reverse("dashboard:pdf_viewer", args=[case.case_id, 99]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_next_safe_accepted(client: Client) -> None:
    """R1: ?next= interno é aceito como volta do viewer."""
    manager = _make_user("gestor-viewer-painel-next")
    case = Case.objects.create(created_by=manager)
    document = _add_document(case, manager, position=1)
    safe_next = reverse("dashboard:home")
    _login(client, manager, MANAGER_ROLE)

    response = client.get(_viewer_url(case, document.position, next_url=safe_next))

    assert response.status_code == 200
    assert response.context["back_url"] == safe_next


@pytest.mark.django_db
def test_pdf_viewer_next_insecure_falls_back(client: Client) -> None:
    """R1: ?next= externo cai no fallback (detalhe do painel)."""
    manager = _make_user("gestor-viewer-painel-evil")
    case = Case.objects.create(created_by=manager)
    document = _add_document(case, manager, position=1)
    detail_url = reverse("dashboard:case_detail", args=[case.case_id])
    _login(client, manager, MANAGER_ROLE)

    response = client.get(
        _viewer_url(case, document.position, next_url="https://evil.example/"),
    )

    assert response.status_code == 200
    assert response.context["back_url"] == detail_url
    assert "evil.example" not in response.content.decode()
