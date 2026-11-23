"""Disposição do ciclo substituído: worker, purga e trilha (slice 002, R5 — RED).

Worker de anexos em voo não repovoa original encerrado por reapresentação;
purga do original retido preserva elos/vínculos; trilha rotula os eventos.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.accounts.models import User
from apps.attachments.models import CaseAttachment
from apps.attachments.tasks import process_case_attachments
from apps.cases import reappearance as reappearance_module
from apps.cases.closure import purge_closed_case_data
from apps.cases.event_trail import event_badge_css, event_label
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseProcedure, CaseStatus

_NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"

_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _declared(case: Case, *procedure_types: str) -> None:
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)


def _awaiting(case: Case) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _identify(case: Case, *, number: str) -> None:
    case.patient_name = "Maria da Silva"
    case.agency_record_number = number
    case.save(update_fields=["patient_name", "agency_record_number"])


@_NEW_MODE
@override_settings(STORAGES=_TEST_STORAGES)
@pytest.mark.django_db
def test_attachment_worker_does_not_repopulate_substituted_original(
    nir_user: User,
) -> None:
    """R5: worker de anexos em voo no original substituído — sem repopulação."""
    prior = Case.objects.create(created_by=nir_user)
    _declared(prior, ANGIO_TYPE)
    _identify(prior, number="w-1")
    _awaiting(prior)
    CaseAttachment.objects.create(
        case=prior,
        original_filename="exame.jpg",
        content_type="image/jpeg",
        size_bytes=10,
        file=SimpleUploadedFile("exame.jpg", b"0123456789", content_type="image/jpeg"),
        uploaded_by=nir_user,
    )
    incoming = Case.objects.create(created_by=nir_user)
    _declared(incoming, ANGIO_TYPE)
    _identify(incoming, number="w-1")
    _awaiting(incoming)
    reappearance_module.resolve_medical_replacement(incoming)
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    process_case_attachments(prior.case_id)
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert prior.structured_data == {}
    assert not prior.events.filter(event_type=CaseEventType.CASE_ATTACHMENT_PROCESSED).exists()


@_NEW_MODE
@pytest.mark.django_db
@override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False)
def test_purge_of_substituted_original_preserves_chain_links(nir_user: User) -> None:
    """R5/D8: purga do original retido — dados dispostos, elos/vínculo intactos."""
    prior = Case.objects.create(created_by=nir_user)
    _declared(prior, ANGIO_TYPE)
    _identify(prior, number="p-1")
    _awaiting(prior)
    prior.extracted_text = "texto clinico"
    prior.save(update_fields=["extracted_text"])
    incoming = Case.objects.create(created_by=nir_user)
    _declared(incoming, ANGIO_TYPE)
    _identify(incoming, number="p-1")
    _awaiting(incoming)
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    result = purge_closed_case_data(prior)
    assert result.cleaned_fields
    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert prior.extracted_text == ""
    assert prior.clinical_data_disposed_at is not None
    assert reappearance_module.current_representative(prior).pk == incoming.pk
    assert incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()


@_NEW_MODE
@pytest.mark.django_db
def test_event_trail_labels_new_event_types() -> None:
    """R1/D6: trilha rotula aplicado/superado/esgotado (anti-drift cobre o resto)."""
    assert event_label(CaseEventType.CASE_REAPPEARANCE_APPLIED) == "Reapresentação aplicada"
    assert (
        event_label(CaseEventType.CASE_REAPPEARANCE_SUPERSEDED)
        == "Caso substituído por reapresentação"
    )
    assert (
        event_label(CaseEventType.CASE_REAPPEARANCE_RETRY_EXHAUSTED)
        == "Recuperação de reapresentação esgotada"
    )
    assert event_badge_css(CaseEventType.CASE_REAPPEARANCE_APPLIED) == "text-bg-secondary"
    assert event_badge_css(CaseEventType.CASE_REAPPEARANCE_RETRY_EXHAUSTED) == "text-bg-danger"
