"""Unit F — disposição do original substituído com arquivos reais (R5.1/AUD-10d).

Original médico substituído (``close_case_as_reappearance`` via resolução):
retenção preserva rows/arquivos/marcador null; minimização remove rows e
arquivos físicos após o commit (nunca sob lock) e grava o marcador; worker
atrasado não repovoa o CLEANED; purga posterior mantém os elos/vínculos.
Sem efeito de agendamento (S3): incoming de scheduling_repeat não existe em
S2 e nada aqui o implementa.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.accounts.models import Role, User
from apps.attachments.models import CaseAttachment
from apps.attachments.tasks import process_case_attachments
from apps.cases import reappearance as reappearance_module
from apps.cases.closure import purge_closed_case_data
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseDocument, CaseProcedure, CaseStatus

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"


@pytest.fixture
def nir_user() -> User:
    role, _ = Role.objects.get_or_create(name="nir")
    user = User.objects.create_user(username="nir-reap-f", password="senha-teste")
    user.roles.add(role)
    return user


def _filesystem_storages(tmp_path: Path) -> dict[str, dict[str, object]]:
    return {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path / "media")},
        },
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


def _attach_real_files(case: Case, owner: User) -> tuple[list[str], list[str]]:
    """Anexa um documento PDF e um anexo imagem com arquivos reais."""
    document = CaseDocument.objects.create(
        case=case,
        file=SimpleUploadedFile("relatorio.pdf", b"%PDF-1.4 fake", content_type="application/pdf"),
        position=1,
        original_filename="relatorio.pdf",
        content_type="application/pdf",
        size_bytes=13,
        uploaded_by=owner,
    )
    attachment = CaseAttachment.objects.create(
        case=case,
        original_filename="exame.jpg",
        content_type="image/jpeg",
        size_bytes=10,
        file=SimpleUploadedFile("exame.jpg", b"0123456789", content_type="image/jpeg"),
        uploaded_by=owner,
    )
    document_name = document.file.name
    attachment_name = attachment.file.name
    assert document_name is not None and attachment_name is not None
    return [document_name], [attachment_name]


def _make_pair_with_files(owner: User, *, number: str) -> tuple[Case, Case]:
    """Original substituível + incoming, ambos com arquivos reais no original."""
    prior = Case.objects.create(created_by=owner)
    _declared(prior, ANGIO_TYPE)
    _identify(prior, number=number)
    _awaiting(prior)
    prior.extracted_text = "texto clinico"
    prior.anonymized_text = "texto anonimizado"
    prior.save(update_fields=["extracted_text", "anonymized_text"])
    incoming = Case.objects.create(created_by=owner)
    _declared(incoming, ANGIO_TYPE)
    _identify(incoming, number=number)
    _awaiting(incoming)
    return prior, incoming


@NEW_MODE
@override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False)
@pytest.mark.django_db
def test_retention_preserves_rows_files_and_null_marker(nir_user: User, tmp_path: Path) -> None:
    """R5.1: retenção — rows, arquivos reais e marcador null preservados."""
    with override_settings(STORAGES=_filesystem_storages(tmp_path)):
        prior, incoming = _make_pair_with_files(nir_user, number="f-ret-1")
        doc_names, att_names = _attach_real_files(prior, nir_user)
        assert all(default_storage.exists(name) for name in doc_names + att_names)
        link = reappearance_module.resolve_medical_replacement(incoming)
        assert link is not None
        prior.refresh_from_db()
        assert prior.status == CaseStatus.CLEANED
        assert prior.extracted_text == "texto clinico"
        assert prior.clinical_data_disposed_at is None
        assert prior.documents.count() == 1
        assert prior.attachments.count() == 1
        assert all(default_storage.exists(name) for name in doc_names + att_names)
        assert (tmp_path / "media" / doc_names[0]).exists()
        assert (tmp_path / "media" / att_names[0]).exists()


@NEW_MODE
@override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=True)
@pytest.mark.django_db(transaction=True)
def test_minimization_removes_rows_and_physical_files_after_commit(
    nir_user: User, tmp_path: Path
) -> None:
    """R5.1: minimização — rows/arquivos removidos pós-commit, marcador gravado."""
    with override_settings(STORAGES=_filesystem_storages(tmp_path)):
        prior, incoming = _make_pair_with_files(nir_user, number="f-min-1")
        doc_names, att_names = _attach_real_files(prior, nir_user)
        assert all(default_storage.exists(name) for name in doc_names + att_names)
        _, incoming_doc_names = _attach_real_files(incoming, nir_user)
        link = reappearance_module.resolve_medical_replacement(incoming)
        assert link is not None
        prior.refresh_from_db()
        assert prior.status == CaseStatus.CLEANED
        assert prior.extracted_text == ""
        assert prior.anonymized_text == ""
        assert prior.clinical_data_disposed_at is not None
        assert prior.documents.count() == 0
        assert prior.attachments.count() == 0
        assert not any(default_storage.exists(name) for name in doc_names + att_names)
        assert not (tmp_path / "media" / doc_names[0]).exists()
        assert not (tmp_path / "media" / att_names[0]).exists()
        # Incoming mantido: arquivos intactos.
        incoming.refresh_from_db()
        assert incoming.documents.count() == 1
        assert all(default_storage.exists(name) for name in incoming_doc_names)
        # Elos preservados.
        assert reappearance_module.current_representative(prior).pk == incoming.pk
        assert incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
        assert prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()


@NEW_MODE
@override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=True)
@pytest.mark.django_db(transaction=True)
def test_late_attachment_worker_cannot_repopulate_disposed_original(
    nir_user: User, tmp_path: Path
) -> None:
    """R5.1: worker de anexos atrasado no original disposto — sem repopulação."""
    with override_settings(STORAGES=_filesystem_storages(tmp_path)):
        prior, incoming = _make_pair_with_files(nir_user, number="f-worker-1")
        _attach_real_files(prior, nir_user)
        assert reappearance_module.resolve_medical_replacement(incoming) is not None
        prior.refresh_from_db()
        assert prior.attachments.count() == 0
        process_case_attachments(prior.case_id)
        prior.refresh_from_db()
        assert prior.status == CaseStatus.CLEANED
        assert prior.structured_data == {}
        assert prior.attachments.count() == 0
        assert not prior.events.filter(event_type=CaseEventType.CASE_ATTACHMENT_PROCESSED).exists()


@NEW_MODE
@override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False)
@pytest.mark.django_db(transaction=True)
def test_purge_after_retention_removes_real_files_and_preserves_links(
    nir_user: User, tmp_path: Path
) -> None:
    """R5.1/D8: purga do original retido — arquivos removidos, elos intactos."""
    with override_settings(STORAGES=_filesystem_storages(tmp_path)):
        prior, incoming = _make_pair_with_files(nir_user, number="f-purge-1")
        doc_names, att_names = _attach_real_files(prior, nir_user)
        assert reappearance_module.resolve_medical_replacement(incoming) is not None
        prior.refresh_from_db()
        assert all(default_storage.exists(name) for name in doc_names + att_names)
        result = purge_closed_case_data(prior)
        assert result.files_attempted == 2
        assert result.files_failed == 0
        prior.refresh_from_db()
        assert prior.status == CaseStatus.CLEANED
        assert prior.extracted_text == ""
        assert prior.clinical_data_disposed_at is not None
        assert prior.documents.count() == 0
        assert prior.attachments.count() == 0
        assert not any(default_storage.exists(name) for name in doc_names + att_names)
        assert reappearance_module.current_representative(prior).pk == incoming.pk
        assert incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
        # Idempotente.
        second = purge_closed_case_data(prior)
        assert second.files_attempted == 0


@NEW_MODE
@pytest.mark.django_db
def test_command_simulation_and_apply_on_applied_link_are_safe(nir_user: User) -> None:
    """R5.3: comando sobre vínculo aplicado — simulação 0, --apply recusa (sem escrita)."""
    prior, incoming = _make_pair_with_files(nir_user, number="f-cmd-1")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    call_command("reenqueue_pending_reappearances", str(link.pk))
    with pytest.raises(CommandError):
        call_command("reenqueue_pending_reappearances", str(link.pk), "--apply")
