"""Rota viewer de PDF original do NIR (slice PVP-002, R1).

``intake:pdf_viewer`` (``cases/<uuid>/documents/<pk>/viewer/``) com o mesmo
guard fail-closed de ``serve_document`` (queryset por criador → 404
alheio/inexistente), ``?next=`` via ``resolve_safe_next_url`` (fallback ao
detalhe) e render do viewer mobile com título/URLs do contrato.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseDocument
from apps.intake.services import create_case_with_documents

NIR_ROLE = "nir"


@pytest.fixture(autouse=True)
def _no_inline_processing() -> Iterator[None]:
    """Criação sem processamento automático: o teste controla os estados."""

    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


def _create_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> Case:
    """Cria um caso NEW com 1 PDF do próprio NIR."""
    return create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(),
        procedure_type="art_perif",
    )


def _viewer_url(case: Case, document_pk: int, *, next_url: str = "") -> str:
    url = reverse("intake:pdf_viewer", args=[case.case_id, document_pk])
    return f"{url}?next={next_url}" if next_url else url


@pytest.mark.django_db
def test_pdf_viewer_renders_mobile_viewer_with_contract_context(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: viewer do próprio caso renderiza o viewer mobile com o contrato."""
    case = _create_case(nir_user, pdf_factory)
    document = list(case.documents.all())[0]
    detail_url = reverse("intake:case_detail", args=[case.case_id])
    serve_url = reverse("intake:serve_document", args=[case.case_id, document.pk])

    client.force_login(nir_user)
    response = client.get(_viewer_url(case, document.pk))

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
def test_pdf_viewer_foreign_case_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1 INVERTIDO (visibilidade-por-equipe slice 002, D6/D8: nome preservado,
    corpo invertido): viewer de caso alheio responde 200 com o contrato do
    viewer mobile a qualquer NIR (paridade serve×viewer, guard de papel)."""
    other_nir = user_factory("nir-alheio", NIR_ROLE)
    foreign_case = _create_case(other_nir, pdf_factory)
    foreign_document = list(foreign_case.documents.all())[0]
    detail_url = reverse("intake:case_detail", args=[foreign_case.case_id])
    serve_url = reverse("intake:serve_document", args=[foreign_case.case_id, foreign_document.pk])

    client.force_login(nir_user)
    response = client.get(_viewer_url(foreign_case, foreign_document.pk))

    assert response.status_code == 200
    assert response.context["viewer_title"] == "PDF Original"
    assert response.context["pdf_url"] == serve_url
    assert response.context["fallback_pdf_url"] == serve_url
    assert response.context["back_url"] == detail_url
    assert str(foreign_case.case_id) in response.content.decode()


@pytest.mark.django_db
def test_pdf_viewer_unknown_document_404(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: documento inexistente no caso → 404."""
    case = _create_case(nir_user, pdf_factory)

    client.force_login(nir_user)
    response = client.get(_viewer_url(case, 999999))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_unknown_case_404(
    client: Client,
    nir_user: User,
) -> None:
    """R1: caso inexistente → 404."""
    client.force_login(nir_user)
    response = client.get(
        reverse("intake:pdf_viewer", args=[uuid.uuid4(), 1]),
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_next_safe_accepted(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: ?next= interno é aceito como volta do viewer."""
    case = _create_case(nir_user, pdf_factory)
    document = list(case.documents.all())[0]
    safe_next = reverse("intake:my_cases")

    client.force_login(nir_user)
    response = client.get(_viewer_url(case, document.pk, next_url=safe_next))

    assert response.status_code == 200
    assert response.context["back_url"] == safe_next


@pytest.mark.django_db
def test_pdf_viewer_next_insecure_falls_back(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: ?next= externo cai no fallback (detalhe do caso)."""
    case = _create_case(nir_user, pdf_factory)
    document = list(case.documents.all())[0]
    detail_url = reverse("intake:case_detail", args=[case.case_id])

    client.force_login(nir_user)
    response = client.get(_viewer_url(case, document.pk, next_url="https://evil.example/"))

    assert response.status_code == 200
    assert response.context["back_url"] == detail_url
    assert "evil.example" not in response.content.decode()


@pytest.mark.django_db
def test_pdf_viewer_without_next_falls_back(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: sem ?next= o Voltar aponta o detalhe do caso."""
    case = _create_case(nir_user, pdf_factory)
    document = list(case.documents.all())[0]
    detail_url = reverse("intake:case_detail", args=[case.case_id])

    client.force_login(nir_user)
    response = client.get(_viewer_url(case, document.pk))

    assert response.status_code == 200
    assert response.context["back_url"] == detail_url


@pytest.mark.django_db
def test_pdf_viewer_document_without_file_404(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: documento sem arquivo responde 404 fail-closed (sem distinguir motivo)."""
    case = _create_case(nir_user, pdf_factory)
    orphan = CaseDocument(
        case=case,
        position=99,
        original_filename="",
        content_type="application/pdf",
        size_bytes=0,
        uploaded_by=nir_user,
    )
    orphan.save()

    client.force_login(nir_user)
    response = client.get(_viewer_url(case, orphan.pk))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_requires_nir_role(
    client: Client,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: papel fora do NIR é negado com 403 (guard de papel)."""
    nir_owner = user_factory("nir-dono", NIR_ROLE)
    case = _create_case(nir_owner, pdf_factory)
    document = list(case.documents.all())[0]
    doctor = user_factory("medico-viewer", "doctor")

    client.force_login(doctor)
    response = client.get(_viewer_url(case, document.pk))

    assert response.status_code == 403
