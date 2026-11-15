"""Header X-Frame-Options: SAMEORIGIN no serve do agendador (slice PVP-001, R4).

O ``XFrameOptionsMiddleware`` nega framing por padrão (DENY); o decorator
``@xframe_options_sameorigin`` na view ``scheduler:case_pdf`` libera o framing
same-origin para o ``<embed>`` dos cards. Gate (scheduled_by + status),
corpo, ``Cache-Control: no-store`` e 404 fail-closed existentes seguem
inalterados (cobertos por ``test_views.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import Case, CaseDocument, CaseProcedure, CaseStatus
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.scheduler.services import confirm_case_scheduling

SCHEDULER_ROLE = "scheduler"
DOCTOR_ROLE = "doctor"
ANGIO_TYPE = "art_perif"
SYSTEM_ROLE = "system"

_PROCESSING_STEPS: list[str] = [
    "start_pdf_extraction",
    "complete_pdf_extraction",
    "complete_anonymization",
    "complete_llm_extraction",
    "complete_llm_summarization",
]


def _confirmed_case(unit: int, *, created_by: User, decided_by: User, scheduled_by: User) -> Case:
    """Caso confirmado pelo serviço: FINAL_REPLY_POSTED (autor = scheduled_by)."""
    case = Case.objects.create(created_by=created_by)
    CaseProcedure.objects.create(case=case, procedure_type=ANGIO_TYPE, declared_by_nir=True)
    for operation in _PROCESSING_STEPS:
        getattr(case, operation)(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    record_doctor_procedure_decisions(
        case, {ANGIO_TYPE: ("approved", "")}, user=decided_by, role=DOCTOR_ROLE
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
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
        f"%PDF-1.4 conteudo um {position}".encode(),
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


@pytest.mark.django_db
def test_case_pdf_sameorigin(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R4: scheduler:case_pdf responde X-Frame-Options: SAMEORIGIN (embed liberado)."""
    doctor = user_factory("medico-pdf-xframe", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-pdf-xframe", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    document = _add_document(case, nir_user, position=1)
    _login(client, scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_pdf", args=[case.case_id, document.position]))

    assert response.status_code == 200
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
