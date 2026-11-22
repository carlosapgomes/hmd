"""Serve de anexo do NIR (slice PVP-006, R1+R2).

``intake:serve_attachment`` (``cases/<uuid>/attachments/<int>/``, PK int —
D6) sob o MESMO guard de ``serve_document`` (queryset por criador → 404
alheio/inexistente), content-type ORIGINAL do anexo, ``FileResponse`` com
filename original e ``@xframe_options_sameorigin``; anexo de outro caso,
inexistente ou sem arquivo → 404 fail-closed (lição P1-4).

``intake:attachment_pdf_viewer`` (``.../attachments/<int>/viewer/``) SOMENTE
para ``application/pdf`` (imagem ou outro → 404 — imagens usam link direto),
mesmo guard do serve, render do viewer mobile com título "Anexo" e
``?next=`` via ``resolve_safe_next_url``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

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
    """Criação sem processamento automático: o teste controla os estados."""

    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


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


def _create_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    attachments: list[SimpleUploadedFile] | None = None,
) -> Case:
    """Cria um caso NEW com 1 PDF e os anexos informados, via serviço real."""
    return create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(),
        procedure_type="art_perif",
        attachments=attachments or [],
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
    return reverse("intake:serve_attachment", args=[case.case_id, attachment.pk])


def _viewer_url(case: Case, attachment: CaseAttachment, *, next_url: str = "") -> str:
    url = reverse("intake:attachment_pdf_viewer", args=[case.case_id, attachment.pk])
    return f"{url}?next={next_url}" if next_url else url


# ── R1: serve ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_serve_attachment_pdf_ok(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: anexo PDF do próprio caso → 200 com content-type original, filename e SAMEORIGIN."""
    uploaded = _pdf_attachment()
    case = _create_case(nir_user, pdf_factory, [_jpeg_attachment(), uploaded])
    attachment = case.attachments.get(original_filename="laudo-anexo.pdf")

    client.force_login(nir_user)
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
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: anexo JPEG do próprio caso → 200 com content-type image/jpeg e filename original."""
    case = _create_case(nir_user, pdf_factory, [_jpeg_attachment()])
    attachment = case.attachments.get()

    client.force_login(nir_user)
    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert response["Content-Disposition"] == 'inline; filename="foto-exame.jpg"'
    served_bytes = b"".join(response.streaming_content)  # type: ignore[attr-defined]
    assert served_bytes == b"\xff\xd8\xff\xe0 conteudo fake do jpeg"


@pytest.mark.django_db
def test_serve_attachment_foreign_case_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1 INVERTIDO (visibilidade-por-equipe slice 002, D6/D8: nome preservado,
    corpo invertido): anexo de caso alheio é servido com 200 a qualquer NIR
    (equipe — qualquer criador, guard de papel)."""
    other_nir = user_factory("nir-alheio-anexo", NIR_ROLE)
    foreign_case = _create_case(other_nir, pdf_factory, [_pdf_attachment()])
    foreign_attachment = foreign_case.attachments.get()

    client.force_login(nir_user)
    response = client.get(_serve_url(foreign_case, foreign_attachment))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert response["Content-Disposition"] == 'inline; filename="laudo-anexo.pdf"'
    served_bytes = b"".join(response.streaming_content)  # type: ignore[attr-defined]
    assert served_bytes == b"%PDF-1.4 conteudo do anexo pdf"


@pytest.mark.django_db
def test_serve_attachment_wrong_case_404(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: anexo pertencente a outro caso do mesmo NIR → 404 sob este caso."""
    case_one = _create_case(nir_user, pdf_factory, [_pdf_attachment("anexo-um.pdf")])
    case_two = _create_case(nir_user, pdf_factory, [_pdf_attachment("anexo-dois.pdf")])
    attachment_of_other_case = case_two.attachments.get()

    client.force_login(nir_user)
    response = client.get(_serve_url(case_one, attachment_of_other_case))

    assert response.status_code == 404


@pytest.mark.django_db
def test_serve_attachment_unknown_attachment_404(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: anexo inexistente no caso → 404."""
    case = _create_case(nir_user, pdf_factory, [_pdf_attachment()])

    client.force_login(nir_user)
    response = client.get(
        reverse("intake:serve_attachment", args=[case.case_id, 999999]),
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_serve_attachment_unknown_case_404(
    client: Client,
    nir_user: User,
) -> None:
    """R1: caso inexistente → 404."""
    client.force_login(nir_user)
    response = client.get(
        reverse("intake:serve_attachment", args=[uuid.uuid4(), 1]),
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_serve_attachment_without_file_404(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: anexo sem arquivo responde 404 fail-closed (lição P1-4)."""
    case = _create_case(nir_user, pdf_factory)
    attachment = _attachment_without_file(case, nir_user)

    client.force_login(nir_user)
    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 404


@pytest.mark.django_db
def test_serve_attachment_requires_nir_role(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: papel ativo fora do NIR → 403 (guard de papel)."""
    case = _create_case(nir_user, pdf_factory, [_pdf_attachment()])
    attachment = case.attachments.get()
    doctor = user_factory("medico-anexo", "doctor")

    client.force_login(doctor)
    response = client.get(_serve_url(case, attachment))

    assert response.status_code == 403


# ── R2: viewer (só PDF) ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_attachment_pdf_viewer_renders_with_contract_context(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: viewer do anexo PDF renderiza o viewer mobile com título "Anexo"."""
    case = _create_case(nir_user, pdf_factory, [_pdf_attachment()])
    attachment = case.attachments.get()
    detail_url = reverse("intake:case_detail", args=[case.case_id])
    serve_url = _serve_url(case, attachment)

    client.force_login(nir_user)
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
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: anexo JPEG não tem viewer dedicado → 404 (usa link direto + img)."""
    case = _create_case(nir_user, pdf_factory, [_jpeg_attachment()])
    attachment = case.attachments.get()

    client.force_login(nir_user)
    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 404


@pytest.mark.django_db
def test_attachment_pdf_viewer_foreign_case_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2 INVERTIDO (visibilidade-por-equipe slice 002, D6/D8: nome preservado,
    corpo invertido): viewer de anexo de caso alheio responde 200 com o
    contrato do viewer mobile a qualquer NIR (paridade serve×viewer)."""
    other_nir = user_factory("nir-alheio-viewer", NIR_ROLE)
    foreign_case = _create_case(other_nir, pdf_factory, [_pdf_attachment()])
    foreign_attachment = foreign_case.attachments.get()
    detail_url = reverse("intake:case_detail", args=[foreign_case.case_id])
    serve_url = _serve_url(foreign_case, foreign_attachment)

    client.force_login(nir_user)
    response = client.get(_viewer_url(foreign_case, foreign_attachment))

    assert response.status_code == 200
    assert response.context["viewer_title"] == "Anexo"
    assert response.context["pdf_url"] == serve_url
    assert response.context["fallback_pdf_url"] == serve_url
    assert response.context["back_url"] == detail_url


@pytest.mark.django_db
def test_attachment_pdf_viewer_unknown_attachment_404(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: anexo inexistente no caso → 404."""
    case = _create_case(nir_user, pdf_factory, [_pdf_attachment()])

    client.force_login(nir_user)
    response = client.get(
        reverse("intake:attachment_pdf_viewer", args=[case.case_id, 999999]),
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_attachment_pdf_viewer_without_file_404(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: anexo PDF sem arquivo → 404 fail-closed (lição P1-4)."""
    case = _create_case(nir_user, pdf_factory)
    attachment = _attachment_without_file(case, nir_user)

    client.force_login(nir_user)
    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 404


@pytest.mark.django_db
def test_attachment_pdf_viewer_requires_nir_role(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: papel ativo fora do NIR → 403 (guard de papel)."""
    case = _create_case(nir_user, pdf_factory, [_pdf_attachment()])
    attachment = case.attachments.get()
    doctor = user_factory("medico-viewer-anexo", "doctor")

    client.force_login(doctor)
    response = client.get(_viewer_url(case, attachment))

    assert response.status_code == 403


@pytest.mark.django_db
def test_attachment_pdf_viewer_next_safe_accepted(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: ?next= interno é aceito como volta do viewer."""
    case = _create_case(nir_user, pdf_factory, [_pdf_attachment()])
    attachment = case.attachments.get()
    safe_next = reverse("intake:my_cases")

    client.force_login(nir_user)
    response = client.get(_viewer_url(case, attachment, next_url=safe_next))

    assert response.status_code == 200
    assert response.context["back_url"] == safe_next


@pytest.mark.django_db
def test_attachment_pdf_viewer_next_insecure_falls_back(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: ?next= externo cai no fallback (detalhe do caso)."""
    case = _create_case(nir_user, pdf_factory, [_pdf_attachment()])
    attachment = case.attachments.get()
    detail_url = reverse("intake:case_detail", args=[case.case_id])

    client.force_login(nir_user)
    response = client.get(_viewer_url(case, attachment, next_url="https://evil.example/"))

    assert response.status_code == 200
    assert response.context["back_url"] == detail_url
    assert "evil.example" not in response.content.decode()
