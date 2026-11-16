"""Rota viewer de PDF original do médico (slice PVP-003, R1).

``doctor:pdf_viewer`` (``case/<uuid>/pdf/<position>/viewer/``) sob o MESMO
guard de ``doctor:case_pdf`` (papel ativo ``doctor``/``admin`` +
``can_access_case`` com o papel ativo da sessão — subtipo fora da matriz e
papel errado → 403, como no serve), ``?next=`` via ``resolve_safe_next_url``
(fallback ao detalhe) e render do viewer mobile com título/URLs do contrato.
404 fail-closed: caso inexistente, position inexistente ou documento sem
arquivo (lição P1-4 do PVP-002).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Sequence

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseDocument, CaseProcedure, CaseStatus

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
        original_filename=f"relatorio-sesab-{position}.pdf",
        content_type="application/pdf",
        size_bytes=len(uploaded.read()),
        uploaded_by=uploaded_by,
    )


def _make_awaiting_case_with_document(created_by: User) -> tuple[Case, CaseDocument]:
    """Caso em AWAITING_DOCTOR com 1 documento (cenário base do viewer)."""
    case = _create_case_with_declared(created_by, (ANGIO_TYPE,))
    _advance_to_awaiting_doctor(case)
    document = _add_document(case, created_by, position=1)
    return case, document


def _login(client: Client, user: User, role: str) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _viewer_url(case: Case, position: int, *, next_url: str = "") -> str:
    url = reverse("doctor:pdf_viewer", args=[case.case_id, position])
    return f"{url}?next={next_url}" if next_url else url


@pytest.mark.django_db
def test_pdf_viewer_renders_mobile_viewer_with_contract_context(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: viewer do caso acessível renderiza o viewer mobile com o contrato."""
    case, document = _make_awaiting_case_with_document(nir_user)
    detail_url = reverse("doctor:case_detail", args=[case.case_id])
    serve_url = reverse("doctor:case_pdf", args=[case.case_id, document.position])
    doctor = user_factory("medico-viewer", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

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
def test_pdf_viewer_unknown_case_404(
    client: Client,
    user_factory: Callable[..., User],
) -> None:
    """R1: caso inexistente → 404 (sem distinguir existência)."""
    doctor = user_factory("medico-viewer-404", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:pdf_viewer", args=[uuid.uuid4(), 1]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_unknown_position_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: position sem documento no caso → 404."""
    case, _document = _make_awaiting_case_with_document(nir_user)
    doctor = user_factory("medico-viewer-posicao", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:pdf_viewer", args=[case.case_id, 2]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_document_without_file_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: documento sem arquivo responde 404 fail-closed (lição P1-4)."""
    case = _create_case_with_declared(nir_user, (ANGIO_TYPE,))
    _advance_to_awaiting_doctor(case)
    CaseDocument(
        case=case,
        position=99,
        original_filename="",
        content_type="application/pdf",
        size_bytes=0,
        uploaded_by=nir_user,
    ).save()
    case.refresh_from_db()
    doctor = user_factory("medico-viewer-sem-arquivo", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:pdf_viewer", args=[case.case_id, 99]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_outside_subtype_uses_case_pdf_guard(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    assign_specialties: Callable[[User, Sequence[str]], None],
) -> None:
    """R1/R4: subtipo fora da matriz usa o MESMO guard do ``case_pdf`` (403).

    Tensão de contrato registrada: a spec genérica fala em 404 fora da
    matriz, mas o guard vigente do médico (``case_pdf``, já aceito, com teste
    pinando 403) nega com 403 — o viewer espelha o serve (R4: guards
    preservados), sem distinguir os motivos no corpo.
    """
    case, document = _make_awaiting_case_with_document(nir_user)
    cardio_doctor = user_factory("doc-cardio-viewer", (DOCTOR_ROLE,))
    assign_specialties(cardio_doctor, ("cardio",))
    _login(client, cardio_doctor, DOCTOR_ROLE)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 403


@pytest.mark.django_db
def test_pdf_viewer_requires_doctor_or_admin_role(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: papel ativo fora de doctor/admin → 403 (guard de papel)."""
    case, document = _make_awaiting_case_with_document(nir_user)
    nir = user_factory("nir-viewer", (NIR_ROLE,))
    _login(client, nir, NIR_ROLE)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 403


@pytest.mark.django_db
def test_pdf_viewer_allows_admin_role(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: papel ativo admin acessa (linha admin da matriz D2)."""
    case, document = _make_awaiting_case_with_document(nir_user)
    admin = user_factory("admin-viewer", (ADMIN_ROLE,))
    _login(client, admin, ADMIN_ROLE)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 200


@pytest.mark.django_db
def test_pdf_viewer_next_safe_accepted(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: ?next= interno é aceito como volta do viewer."""
    case, document = _make_awaiting_case_with_document(nir_user)
    doctor = user_factory("medico-viewer-next", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    safe_next = reverse("doctor:queue")

    response = client.get(_viewer_url(case, document.position, next_url=safe_next))

    assert response.status_code == 200
    assert response.context["back_url"] == safe_next


@pytest.mark.django_db
def test_pdf_viewer_next_insecure_falls_back(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: ?next= externo cai no fallback (detalhe do caso)."""
    case, document = _make_awaiting_case_with_document(nir_user)
    detail_url = reverse("doctor:case_detail", args=[case.case_id])
    doctor = user_factory("medico-viewer-evil", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(
        _viewer_url(case, document.position, next_url="https://evil.example/"),
    )

    assert response.status_code == 200
    assert response.context["back_url"] == detail_url
    assert "evil.example" not in response.content.decode()


@pytest.mark.django_db
def test_pdf_viewer_without_next_falls_back(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: sem ?next= o Voltar aponta o detalhe do caso."""
    case, document = _make_awaiting_case_with_document(nir_user)
    detail_url = reverse("doctor:case_detail", args=[case.case_id])
    doctor = user_factory("medico-viewer-sem-next", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 200
    assert response.context["back_url"] == detail_url


@pytest.mark.django_db
def test_pdf_viewer_anonymous_redirects_login(
    client: Client,
    nir_user: User,
) -> None:
    """R1: anônimo → redirect ao login (semântica do role_required)."""
    case, document = _make_awaiting_case_with_document(nir_user)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))
