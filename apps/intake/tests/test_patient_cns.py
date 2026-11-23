"""Reset imediato do CNS no reenvio de documentos (slice 001, R1 — RED).

O reenvio substitui os documentos do caso retido: o CNS estrutural anterior
não pode permanecer como evidência de identidade — ``resubmit_case_documents``
zera ``patient_cns`` imediatamente (contrato em ``_RESUBMIT_CLEARED_FIELDS`` e
na atribuição efetiva antes de reagendar), e o reprocessamento posterior
sobrescreve (inclusive com vazio).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.accounts.models import User
from apps.cases.models import Case, CaseStatus
from apps.intake import services as intake_services
from apps.intake.services import create_case_with_documents, resubmit_case_documents

NIR_ROLE = "nir"
SYSTEM_ROLE = "system"
_CNS_A = "207139200176053"


@pytest.fixture(autouse=True)
def _no_inline_processing() -> Iterator[None]:
    with override_settings(
        INTAKE_RUN_TASKS_INLINE=False,
        ANONYMIZATION_RUN_TASKS_INLINE=False,
    ):
        yield


def _supports_patient_cns() -> bool:
    """O campo ``Case.patient_cns`` já existe? (RED honesto pré-migration)."""
    return any(field.name == "patient_cns" for field in Case._meta.get_fields())


def _make_retained_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> Case:
    case = create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(name="relatorio-retido.pdf"),
        procedure_type="art_perif",
    )
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.manual_review_required = True
    case.manual_review_reason = "documento fora do padrão SESAB do relatório (motivo: x)"
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    return case


@pytest.mark.django_db
def test_resubmit_cleared_fields_include_patient_cns() -> None:
    """R1: ``patient_cns`` pertence ao contrato de zeragem do reenvio."""
    assert "patient_cns" in intake_services._RESUBMIT_CLEARED_FIELDS


@pytest.mark.django_db
def test_resubmit_zeroes_previous_patient_cns(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: reenvio zera o CNS anterior imediatamente (sem resto de identidade)."""
    case = _make_retained_case(nir_user, pdf_factory)
    if _supports_patient_cns():
        Case.objects.filter(pk=case.pk).update(patient_cns=_CNS_A)
        case.refresh_from_db()
        assert case.patient_cns == _CNS_A

    resubmit_case_documents(
        case=case,
        user=nir_user,
        role=NIR_ROLE,
        files=[pdf_factory(name="novo-relatorio.pdf")],
    )

    case.refresh_from_db()
    assert getattr(case, "patient_cns", "STALE") == ""
    assert case.status == CaseStatus.PDF_EXTRACTING
