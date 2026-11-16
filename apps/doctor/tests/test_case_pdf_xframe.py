"""Header X-Frame-Options: SAMEORIGIN no serve médico (slice PVP-001, R4).

O ``XFrameOptionsMiddleware`` nega framing por padrão (DENY); o decorator
``@xframe_options_sameorigin`` na view ``doctor:case_pdf`` libera o framing
same-origin para o ``<embed>`` dos cards. Guard (papel ativo + matriz D2),
corpo e status existentes seguem inalterados (cobertos por
``test_detail.py``).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseDocument, CaseProcedure, CaseStatus

DOCTOR_ROLE = "doctor"
ANGIO_TYPE = "art_perif"
SYSTEM_ROLE = "system"


def _awaiting_doctor_case(created_by: User) -> Case:
    """Caso em AWAITING_DOCTOR com 1 tipo declarado (setup da matriz)."""
    case = Case.objects.create(created_by=created_by)
    CaseProcedure.objects.create(case=case, procedure_type=ANGIO_TYPE, declared_by_nir=True)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    case.refresh_from_db()
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


@pytest.mark.django_db
def test_case_pdf_sameorigin(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R4: doctor:case_pdf responde X-Frame-Options: SAMEORIGIN (embed liberado)."""
    case = _awaiting_doctor_case(nir_user)
    document = _add_document(case, nir_user, position=1)
    doctor = user_factory("geral-pdf-xframe", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_pdf", args=[case.case_id, document.position]))

    assert response.status_code == 200
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
