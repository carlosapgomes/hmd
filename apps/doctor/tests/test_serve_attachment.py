"""Serve de anexo do médico (slice PVP-006, R1+R2).

``doctor:serve_attachment`` (``case/<uuid>/attachments/<int>/``, PK int —
D6) sob o MESMO guard de ``doctor:case_pdf`` (papel ativo ``doctor``/
``admin`` + ``can_access_case`` — subtipo fora da matriz → 403), content-type
ORIGINAL do anexo, ``FileResponse`` com filename original e
``@xframe_options_sameorigin``; fora da matriz/alheio? não-distinguível →
404? NÃO: fora da matriz → 403 (paridade serve×viewer do PVP-003, R4 de lá);
alheio/inexistente/sem arquivo → 404 fail-closed (lição P1-4).

``doctor:attachment_pdf_viewer`` (``.../attachments/<int>/viewer/``) SOMENTE
para ``application/pdf`` (imagem ou outro → 404 — imagens usam link direto),
mesmo guard do serve, render do viewer mobile com título "Anexo" e
``?next=`` via ``resolve_safe_next_url``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Sequence

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.attachments.models import CaseAttachment
from apps.cases.models import Case, CaseProcedure, CaseStatus

DOCTOR_ROLE = "doctor"
ADMIN_ROLE = "admin"
NIR_ROLE = "nir"
SYSTEM_ROLE = "system"

ANGIO_TYPE = "art_perif"

# Storage de teste: arquivos apenas em memória (nada de disco) — mesmo
# padrão de ``apps/intake/tests/conftest.py``.
_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.fixture(autouse=True)
def _memory_storage() -> Iterator[None]:
    """Storage default em memória durante cada teste do arquivo."""
    with override_settings(STORAGES=_TEST_STORAGES):
        yield


def _create_case_with_declared(created_by: User, procedure_types: Sequence[str]) -> Case:
    """Cria um caso com as rows declaradas (sem transições de status)."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )
    return case


def _advance_to_awaiting_doctor(case: Case) -> None:
    """Dirige o caso pelas transições do pipeline até ``AWAITING_DOCTOR``."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


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


def _make_awaiting_case_with_attachments(
    created_by: User,
    uploads: Sequence[SimpleUploadedFile],
) -> Case:
    """Caso em AWAITING_DOCTOR com os anexos dados (cenário base)."""
    case = _create_case_with_declared(created_by, (ANGIO_TYPE,))
    _advance_to_awaiting_doctor(case)
    for upload in uploads:
        _add_attachment(case, created_by, upload)
    return case


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


def _login(client: Client, user: User, role: str) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _serve_url(case: Case, attachment: CaseAttachment) -> str:
    return reverse("doctor:serve_attachment", args=[case.case_id, attachment.pk])


def _viewer_url(case: Case, attachment: CaseAttachment, *, next_url: str = "") -> str:
    url = reverse("doctor:attachment_pdf_viewer", args=[case.case_id, attachment.pk])
    return f"{url}?next={next_url}" if next_url else url


# ── R1: serve ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_serve_attachment_pdf_ok(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: anexo PDF acessível → 200 com content-type original, filename e SAMEORIGIN."""
    case = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment()])
    attachment = case.attachments.get()
    doctor = user_factory("medico-anexo", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert response["Content-Disposition"] == 'inline; filename="laudo-anexo.pdf"'
    served_bytes = b"".join(response.streaming_content)  # type: ignore[attr-defined]
    assert served_bytes == b"%PDF-1.4 conteudo do anexo pdf"


@pytest.mark.django_db
def test_serve_attachment_jpeg_ok(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: anexo JPEG acessível → 200 com content-type image/jpeg e filename original."""
    case = _make_awaiting_case_with_attachments(nir_user, [_jpeg_attachment()])
    attachment = case.attachments.get()
    doctor = user_factory("medico-anexo-jpeg", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert response["Content-Disposition"] == 'inline; filename="foto-exame.jpg"'
    served_bytes = b"".join(response.streaming_content)  # type: ignore[attr-defined]
    assert served_bytes == b"\xff\xd8\xff\xe0 conteudo fake do jpeg"


@pytest.mark.django_db
def test_serve_attachment_outside_subtype_uses_case_pdf_guard(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    assign_specialties: Callable[[User, Sequence[str]], None],
) -> None:
    """R1/R4: fora da matriz → 403 com o MESMO guard de ``case_pdf`` (PVP-003)."""
    case = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment()])
    attachment = case.attachments.get()
    cardio_doctor = user_factory("doc-cardio-anexo", (DOCTOR_ROLE,))
    assign_specialties(cardio_doctor, ("cardio",))
    _login(client, cardio_doctor, DOCTOR_ROLE)

    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 403


@pytest.mark.django_db
def test_serve_attachment_unknown_case_404(
    client: Client,
    user_factory: Callable[..., User],
) -> None:
    """R1: caso inexistente → 404."""
    doctor = user_factory("medico-anexo-404", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:serve_attachment", args=[uuid.uuid4(), 1]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_serve_attachment_unknown_attachment_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: anexo inexistente no caso → 404."""
    case = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment()])
    doctor = user_factory("medico-anexo-sem", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:serve_attachment", args=[case.case_id, 999999]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_serve_attachment_wrong_case_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: anexo de outro caso → 404 sob este caso (sem distinguir motivo)."""
    case_one = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment("anexo-um.pdf")])
    case_two = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment("anexo-dois.pdf")])
    attachment_of_other_case = case_two.attachments.get()
    doctor = user_factory("medico-anexo-outro", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(_serve_url(case_one, attachment_of_other_case))

    assert response.status_code == 404


@pytest.mark.django_db
def test_serve_attachment_without_file_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: anexo sem arquivo → 404 fail-closed (lição P1-4)."""
    case = _make_awaiting_case_with_attachments(nir_user, [])
    attachment = _attachment_without_file(case, nir_user)
    doctor = user_factory("medico-anexo-vazio", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 404


@pytest.mark.django_db
def test_serve_attachment_requires_doctor_or_admin_role(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: papel ativo fora de doctor/admin → 403 (guard de papel)."""
    case = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment()])
    attachment = case.attachments.get()
    nir = user_factory("nir-anexo", (NIR_ROLE,))
    _login(client, nir, NIR_ROLE)

    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 403


@pytest.mark.django_db
def test_serve_attachment_allows_admin_role(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: papel ativo admin acessa (linha admin da matriz D2)."""
    case = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment()])
    attachment = case.attachments.get()
    admin = user_factory("admin-anexo", (ADMIN_ROLE,))
    _login(client, admin, ADMIN_ROLE)

    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"


# ── R2: viewer (só PDF) ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_attachment_pdf_viewer_renders_with_contract_context(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: viewer do anexo PDF acessível renderiza o viewer mobile com título "Anexo"."""
    case = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment()])
    attachment = case.attachments.get()
    detail_url = reverse("doctor:case_detail", args=[case.case_id])
    serve_url = _serve_url(case, attachment)
    doctor = user_factory("medico-viewer-anexo", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

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
def test_attachment_pdf_viewer_jpeg_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: anexo JPEG não tem viewer dedicado → 404 (usa link direto + img)."""
    case = _make_awaiting_case_with_attachments(nir_user, [_jpeg_attachment()])
    attachment = case.attachments.get()
    doctor = user_factory("medico-viewer-jpeg", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 404


@pytest.mark.django_db
def test_attachment_pdf_viewer_outside_subtype_uses_case_pdf_guard(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    assign_specialties: Callable[[User, Sequence[str]], None],
) -> None:
    """R2/R4: viewer fora da matriz → 403 com o MESMO guard do serve (PVP-003)."""
    case = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment()])
    attachment = case.attachments.get()
    cardio_doctor = user_factory("doc-cardio-viewer-anexo", (DOCTOR_ROLE,))
    assign_specialties(cardio_doctor, ("cardio",))
    _login(client, cardio_doctor, DOCTOR_ROLE)

    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 403


@pytest.mark.django_db
def test_attachment_pdf_viewer_unknown_attachment_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: anexo inexistente no caso → 404."""
    case = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment()])
    doctor = user_factory("medico-viewer-sem", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(
        reverse("doctor:attachment_pdf_viewer", args=[case.case_id, 999999]),
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_attachment_pdf_viewer_without_file_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: anexo PDF sem arquivo → 404 fail-closed (lição P1-4)."""
    case = _make_awaiting_case_with_attachments(nir_user, [])
    attachment = _attachment_without_file(case, nir_user)
    doctor = user_factory("medico-viewer-vazio", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 404


@pytest.mark.django_db
def test_attachment_pdf_viewer_requires_doctor_or_admin_role(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: papel ativo fora de doctor/admin → 403 (guard de papel)."""
    case = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment()])
    attachment = case.attachments.get()
    nir = user_factory("nir-viewer-anexo", (NIR_ROLE,))
    _login(client, nir, NIR_ROLE)

    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 403


@pytest.mark.django_db
def test_attachment_pdf_viewer_next_safe_accepted(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: ?next= interno é aceito como volta do viewer."""
    case = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment()])
    attachment = case.attachments.get()
    safe_next = reverse("doctor:queue")
    doctor = user_factory("medico-viewer-next", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(_viewer_url(case, attachment, next_url=safe_next))

    assert response.status_code == 200
    assert response.context["back_url"] == safe_next


@pytest.mark.django_db
def test_attachment_pdf_viewer_next_insecure_falls_back(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: ?next= externo cai no fallback (detalhe do caso)."""
    case = _make_awaiting_case_with_attachments(nir_user, [_pdf_attachment()])
    attachment = case.attachments.get()
    detail_url = reverse("doctor:case_detail", args=[case.case_id])
    doctor = user_factory("medico-viewer-evil", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(_viewer_url(case, attachment, next_url="https://evil.example/"))

    assert response.status_code == 200
    assert response.context["back_url"] == detail_url
    assert "evil.example" not in response.content.decode()
