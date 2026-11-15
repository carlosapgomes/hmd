"""Rota viewer de PDF original do agendador (slice PVP-004, R1).

``scheduler:pdf_viewer`` (``case/<uuid>/pdf/<position>/viewer/``) sob o MESMO
gate fail-closed de ``scheduler:case_pdf`` (papel ativo ``scheduler``/``admin``
+ ``scheduled_by == request.user`` + status em ``PDF_ALLOWED_STATUSES``;
fora do gate → 404 sem distinguir), ``?next=`` via ``resolve_safe_next_url``
(fallback ao detalhe do agendador) e render do viewer mobile com título/URLs
do contrato. 404 também para position inexistente e documento sem arquivo
(lição P1-4 do PVP-002).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import Case, CaseDocument, CaseProcedure, CaseStatus
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.scheduler.services import confirm_case_scheduling, reopen_scheduling_after_incident

SCHEDULER_ROLE = "scheduler"
ADMIN_ROLE = "admin"
DOCTOR_ROLE = "doctor"
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


def _scheduler_requested_case(
    created_by: User, decided_by: User, procedure_types: Sequence[str]
) -> Case:
    """Caso decidido pelo médico (≥1 aprovado) chegando a ``SCHEDULER_REQUESTED``."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    for operation in (
        "start_pdf_extraction",
        "complete_pdf_extraction",
        "complete_anonymization",
        "complete_llm_extraction",
        "complete_llm_summarization",
    ):
        getattr(case, operation)(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    record_doctor_procedure_decisions(
        case,
        {procedure_type: ("approved", "") for procedure_type in procedure_types},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    return case


def _confirmed_case(unit: int, *, created_by: User, decided_by: User, scheduled_by: User) -> Case:
    """Caso confirmado pelo serviço: FINAL_REPLY_POSTED (autor = scheduled_by)."""
    case = _scheduler_requested_case(created_by, decided_by, (ANGIO_TYPE,))
    confirm_case_scheduling(
        case,
        unit=unit,
        scheduled_datetime=timezone.now() + timedelta(days=2),
        scheduled_location="Sala de hemodinâmica",
        user=scheduled_by,
        role=SCHEDULER_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


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


def _login(client: Client, user: User, role: str) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _viewer_url(case: Case, position: int, *, next_url: str = "") -> str:
    url = reverse("scheduler:pdf_viewer", args=[case.case_id, position])
    return f"{url}?next={next_url}" if next_url else url


@pytest.mark.django_db
def test_pdf_viewer_renders_mobile_viewer_with_contract_context(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: viewer do caso processado pelo próprio agendador renderiza o viewer."""
    doctor = user_factory("medico-viewer", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-viewer", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    document = _add_document(case, nir_user, position=1)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    serve_url = reverse("scheduler:case_pdf", args=[case.case_id, document.position])
    _login(client, scheduler, SCHEDULER_ROLE)

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
def test_pdf_viewer_other_scheduler_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: outro agendador (detalhe permitido) recebe 404 fail-closed no viewer."""
    doctor = user_factory("medico-viewer-outro", (DOCTOR_ROLE,))
    owner = user_factory("agendador-viewer-owner", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=owner)
    document = _add_document(case, nir_user, position=1)
    other = user_factory("agendador-viewer-estrangeiro", (SCHEDULER_ROLE,))
    _login(client, other, SCHEDULER_ROLE)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_reopened_case_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: caso reaberto por intercorrência (scheduled_by limpo) → 404."""
    doctor = user_factory("medico-viewer-reaberto", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-viewer-reaberto", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    document = _add_document(case, nir_user, position=1)
    reopen_scheduling_after_incident(
        case,
        reason="vaga desmarcada pela unidade de origem",
        user=scheduler,
        role=SCHEDULER_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_SCHEDULING
    _login(client, scheduler, SCHEDULER_ROLE)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_requires_scheduler_or_admin_role(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: papel ativo fora de scheduler/admin → 403 (guard de papel)."""
    doctor = user_factory("medico-viewer-papel", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-viewer-papel", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    document = _add_document(case, nir_user, position=1)
    nir = user_factory("nir-viewer-agendador", (NIR_ROLE,))
    _login(client, nir, NIR_ROLE)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 403


@pytest.mark.django_db
def test_pdf_viewer_allows_admin_role(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: papel ativo admin fora do gate de scheduled_by → 404 (gate fail-closed)."""
    doctor = user_factory("medico-viewer-admin", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-viewer-admin", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    document = _add_document(case, nir_user, position=1)
    admin = user_factory("admin-viewer-agendador", (ADMIN_ROLE,))
    _login(client, admin, ADMIN_ROLE)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_unknown_case_404(
    client: Client,
    user_factory: Callable[..., User],
) -> None:
    """R1: caso inexistente → 404 (sem distinguir existência)."""
    scheduler = user_factory("agendador-viewer-404", (SCHEDULER_ROLE,))
    _login(client, scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:pdf_viewer", args=[uuid.uuid4(), 1]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_unknown_position_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: position sem documento no caso → 404."""
    doctor = user_factory("medico-viewer-posicao", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-viewer-posicao", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _add_document(case, nir_user, position=1)
    _login(client, scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:pdf_viewer", args=[case.case_id, 2]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_document_without_file_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: documento sem arquivo responde 404 fail-closed (lição P1-4)."""
    doctor = user_factory("medico-viewer-sem-arquivo", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-viewer-sem-arquivo", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    CaseDocument(
        case=case,
        position=99,
        original_filename="",
        content_type="application/pdf",
        size_bytes=0,
        uploaded_by=nir_user,
    ).save()
    case.refresh_from_db()
    _login(client, scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:pdf_viewer", args=[case.case_id, 99]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_viewer_next_safe_accepted(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: ?next= interno é aceito como volta do viewer."""
    doctor = user_factory("medico-viewer-next", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-viewer-next", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    document = _add_document(case, nir_user, position=1)
    _login(client, scheduler, SCHEDULER_ROLE)
    safe_next = reverse("scheduler:queue")

    response = client.get(_viewer_url(case, document.position, next_url=safe_next))

    assert response.status_code == 200
    assert response.context["back_url"] == safe_next


@pytest.mark.django_db
def test_pdf_viewer_next_insecure_falls_back(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: ?next= externo cai no fallback (detalhe do agendador)."""
    doctor = user_factory("medico-viewer-evil", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-viewer-evil", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    document = _add_document(case, nir_user, position=1)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    _login(client, scheduler, SCHEDULER_ROLE)

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
    """R1: sem ?next= o Voltar aponta o detalhe do agendador."""
    doctor = user_factory("medico-viewer-sem-next", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-viewer-sem-next", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    document = _add_document(case, nir_user, position=1)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    _login(client, scheduler, SCHEDULER_ROLE)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 200
    assert response.context["back_url"] == detail_url


@pytest.mark.django_db
def test_pdf_viewer_anonymous_redirects_login(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: anônimo → redirect ao login (semântica do role_required)."""
    doctor = user_factory("medico-viewer-anonimo", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-viewer-anonimo", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    document = _add_document(case, nir_user, position=1)

    response = client.get(_viewer_url(case, document.position))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))
