"""Paridade visual do detalhe do NIR (ui-parity-intake-detail, slice 003).

R1/R3: o detalhe do NIR usa o layout ats-web (``detail-layout`` + cards
``report-card`` com bloco de resultado ``report-block-body``), com o
MESMO conteúdo (identificação mínima, procedimentos, documentos, anexos
e comunicações). Escopo estreito: marcadores/conteúdo apenas.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case
from apps.intake.services import create_case_with_documents

NIR_ROLE = "nir"


@pytest.fixture(autouse=True)
def _no_inline_processing() -> Iterator[None]:
    """Criação sem processamento automático: o teste controla os estados."""

    with override_settings(
        INTAKE_RUN_TASKS_INLINE=False,
        ANONYMIZATION_RUN_TASKS_INLINE=False,
    ):
        yield


def _create_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    *,
    patient_name: str = "Paciente Visual NIR",
    record_number: str = "5040778",
) -> Case:
    case = create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(),
        procedure_type="art_perif",
    )
    Case.objects.filter(pk=case.pk).update(
        patient_name=patient_name,
        agency_record_number=record_number,
        patient_age=84,
    )
    case.refresh_from_db()
    return case


@pytest.mark.django_db
def test_intake_detail_uses_ats_web_report_layout(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: detalhe do NIR no layout ats-web, conteúdo idêntico."""
    case = _create_case(nir_user, pdf_factory)
    client.force_login(nir_user)

    response = client.get(reverse("intake:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "detail-layout" in body
    assert "report-card" in body
    assert "report-block-body" in body
    assert "Paciente Visual NIR" in body
    assert "84 a" in body
    assert "5040778" in body
    assert "Procedimentos do caso" in body
    assert "Arteriografia periférica" in body
    assert "Documentos" in body
    assert "Comunicações" in body
