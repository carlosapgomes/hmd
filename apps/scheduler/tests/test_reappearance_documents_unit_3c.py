"""Testes HTTP de documentos/anexos de reapresentação no agendador (Unit 3C, R3.1–R3.4)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.http import FileResponse
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.attachments.models import AttachmentStatus, CaseAttachment
from apps.cases.closure import acknowledge_case_receipt, post_doctor_denial_reply
from apps.cases.models import (
    Case,
    CaseDocument,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    ReappearanceState,
    ResolutionKind,
)
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.scheduler.services import (
    confirm_case_scheduling,
    reopen_scheduling_after_incident,
)

SCHEDULER_ROLE = "scheduler"
ADMIN_ROLE = "admin"
DOCTOR_ROLE = "doctor"
NIR_ROLE = "nir"
MANAGER_ROLE = "manager"
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"

VALID_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
VALID_JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _fs_storages(root: Path) -> dict[str, dict[str, object]]:
    return {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(root)},
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


def _advance_to_awaiting_doctor(case: Case, procedure_types: Sequence[str] = (ANGIO_TYPE,)) -> None:
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


def _make_scheduler_requested(
    created_by: User,
    decided_by: User,
    procedure_types: Sequence[str] = (ANGIO_TYPE,),
) -> Case:
    case = Case.objects.create(created_by=created_by)
    _advance_to_awaiting_doctor(case, procedure_types)
    record_doctor_procedure_decisions(
        case,
        {pt: ("approved", "") for pt in procedure_types},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    return case


def _make_medical_denial_case(created_by: User, decided_by: User) -> Case:
    case = Case.objects.create(created_by=created_by)
    _advance_to_awaiting_doctor(case, (ANGIO_TYPE,))
    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: ("denied", "sem indicação clínica")},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    post_doctor_denial_reply(case, user=decided_by, role=DOCTOR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.scheduling_decided_at is None
    return case


def _add_doc(
    case: Case,
    uploaded_by: User,
    *,
    position: int = 1,
    filename: str = "relatorio.pdf",
    content: bytes = VALID_PDF_BYTES,
) -> CaseDocument:
    return CaseDocument.objects.create(
        case=case,
        position=position,
        file=SimpleUploadedFile(filename, content, content_type="application/pdf"),
        original_filename=filename,
        size_bytes=len(content),
        uploaded_by=uploaded_by,
    )


def _add_attachment(
    case: Case,
    uploaded_by: User,
    *,
    filename: str,
    content: bytes,
    content_type: str,
) -> CaseAttachment:
    return CaseAttachment.objects.create(
        case=case,
        file=SimpleUploadedFile(filename, content, content_type=content_type),
        original_filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        uploaded_by=uploaded_by,
        status=AttachmentStatus.PROCESSED,
    )


def _link_scheduling_repeat(
    original: Case,
    incoming: Case,
    *,
    state: str = ReappearanceState.APPLIED,
    resolution_kind: str = ResolutionKind.SCHEDULING_REPEAT,
    canonical_case: Case | None = None,
) -> CaseReappearance:
    if incoming.status != CaseStatus.CLEANED and state == ReappearanceState.APPLIED:
        Case.objects.filter(pk=incoming.pk).update(status=CaseStatus.CLEANED)
        incoming.refresh_from_db()
    return CaseReappearance.objects.create(
        incoming_case=incoming,
        prior_case=original,
        canonical_case=canonical_case if canonical_case is not None else original,
        revision_digest=f"rev-{uuid.uuid4().hex[:12]}",
        evidence_code="cns_exact",
        state=state,
        resolution_kind=resolution_kind,
        applied_at=timezone.now() if state == ReappearanceState.APPLIED else None,
    )


@pytest.mark.django_db
class TestReappearanceDocumentsUnit3C:
    """Cobertura integral R3.1–R3.4 com FileSystemStorage real e Client HTTP autenticado."""

    def test_serve_and_viewer_while_awaiting_scheduling_vs_original_404(
        self,
        client: Client,
        user_factory: Callable[..., User],
        login_user: Callable[[User, str], None],
        tmp_path: Path,
    ) -> None:
        nir = user_factory("nir-3c-1", (NIR_ROLE,))
        doctor = user_factory("doc-3c-1", (DOCTOR_ROLE,))
        scheduler = user_factory("sched-3c-1", (SCHEDULER_ROLE,))

        with override_settings(STORAGES=_fs_storages(tmp_path)):
            original = _make_scheduler_requested(nir, doctor)
            _add_doc(original, nir, position=1, filename="orig.pdf")

            incoming = Case.objects.create(created_by=nir)
            rec_doc = _add_doc(incoming, nir, position=1, filename="recebido.pdf")
            rec_pdf_att = _add_attachment(
                incoming,
                nir,
                filename="exame.pdf",
                content=VALID_PDF_BYTES,
                content_type="application/pdf",
            )
            rec_jpg_att = _add_attachment(
                incoming,
                nir,
                filename="foto.jpg",
                content=VALID_JPEG_BYTES,
                content_type="image/jpeg",
            )
            rec_png_att = _add_attachment(
                incoming,
                nir,
                filename="laudo.png",
                content=VALID_PNG_BYTES,
                content_type="image/png",
            )
            link = _link_scheduling_repeat(original, incoming)

            login_user(scheduler, SCHEDULER_ROLE)

            # Original PDF segue 404 pré-decisão
            assert (
                client.get(reverse("scheduler:case_pdf", args=[original.case_id, 1])).status_code
                == 404
            )
            assert (
                client.get(reverse("scheduler:pdf_viewer", args=[original.case_id, 1])).status_code
                == 404
            )

            # Serve relatório recebido (PDF)
            doc_serve_url = reverse(
                "scheduler:reappearance_document_serve",
                args=[original.case_id, link.id, rec_doc.pk],
            )
            resp = client.get(doc_serve_url)
            assert resp.status_code == 200
            assert cast(FileResponse, resp).getvalue() == VALID_PDF_BYTES
            assert resp["Content-Type"] == "application/pdf"
            assert resp["Cache-Control"] == "no-store"
            assert resp["X-Frame-Options"] == "SAMEORIGIN"
            assert "recebido.pdf" in resp["Content-Disposition"]

            # Viewer relatório recebido (PDF)
            doc_viewer_url = reverse(
                "scheduler:reappearance_document_viewer",
                args=[original.case_id, link.id, rec_doc.pk],
            )
            vresp = client.get(doc_viewer_url)
            assert vresp.status_code == 200
            html = vresp.content.decode()
            assert doc_serve_url in html
            assert reverse("scheduler:case_detail", args=[original.case_id]) in html

            # Serve anexo PDF
            att_pdf_serve_url = reverse(
                "scheduler:reappearance_attachment_serve",
                args=[original.case_id, link.id, rec_pdf_att.pk],
            )
            aresp = client.get(att_pdf_serve_url)
            assert aresp.status_code == 200
            assert cast(FileResponse, aresp).getvalue() == VALID_PDF_BYTES
            assert aresp["Content-Type"] == "application/pdf"
            assert aresp["Cache-Control"] == "no-store"
            assert aresp["X-Frame-Options"] == "SAMEORIGIN"

            # Viewer anexo PDF
            att_pdf_viewer_url = reverse(
                "scheduler:reappearance_attachment_viewer",
                args=[original.case_id, link.id, rec_pdf_att.pk],
            )
            avresp = client.get(att_pdf_viewer_url)
            assert avresp.status_code == 200
            assert att_pdf_serve_url in avresp.content.decode()

            # Serve anexo JPEG e PNG (200) vs Viewer imagem (404)
            for att, expected_bytes, expected_ct in (
                (rec_jpg_att, VALID_JPEG_BYTES, "image/jpeg"),
                (rec_png_att, VALID_PNG_BYTES, "image/png"),
            ):
                img_serve = reverse(
                    "scheduler:reappearance_attachment_serve",
                    args=[original.case_id, link.id, att.pk],
                )
                iresp = client.get(img_serve)
                assert iresp.status_code == 200
                assert cast(FileResponse, iresp).getvalue() == expected_bytes
                assert iresp["Content-Type"] == expected_ct
                assert iresp["Cache-Control"] == "no-store"
                assert iresp["X-Frame-Options"] == "SAMEORIGIN"

                img_viewer = reverse(
                    "scheduler:reappearance_attachment_viewer",
                    args=[original.case_id, link.id, att.pk],
                )
                assert client.get(img_viewer).status_code == 404

    def test_post_decision_reopened_cleaned_retained_and_flag_off(
        self,
        client: Client,
        user_factory: Callable[..., User],
        login_user: Callable[[User, str], None],
        tmp_path: Path,
    ) -> None:
        nir = user_factory("nir-3c-2", (NIR_ROLE,))
        doctor = user_factory("doc-3c-2", (DOCTOR_ROLE,))
        scheduler = user_factory("sched-3c-2", (SCHEDULER_ROLE,))

        with override_settings(STORAGES=_fs_storages(tmp_path)):
            original = _make_scheduler_requested(nir, doctor)
            _add_doc(original, nir, position=1, filename="orig.pdf")
            confirm_case_scheduling(
                original,
                unit=1,
                scheduled_datetime=timezone.now() + timedelta(days=2),
                scheduled_location="Sala 1",
                user=scheduler,
                role=SCHEDULER_ROLE,
            )
            original.refresh_from_db()

            incoming = Case.objects.create(created_by=nir)
            rec_doc = _add_doc(incoming, nir, position=1, filename="rec.pdf")
            link = _link_scheduling_repeat(original, incoming)

            doc_serve = reverse(
                "scheduler:reappearance_document_serve",
                args=[original.case_id, link.id, rec_doc.pk],
            )
            doc_viewer = reverse(
                "scheduler:reappearance_document_viewer",
                args=[original.case_id, link.id, rec_doc.pk],
            )

            login_user(scheduler, SCHEDULER_ROLE)

            # Pós-decisão autorizado: recebido 200 e original 200
            assert client.get(doc_serve).status_code == 200
            assert client.get(doc_viewer).status_code == 200
            assert (
                client.get(reverse("scheduler:case_pdf", args=[original.case_id, 1])).status_code
                == 200
            )

            # Reaberto por intercorrência (AWAITING_SCHEDULING): recebido 200, original 404
            reopen_scheduling_after_incident(
                original, reason="paciente sem jejum", user=scheduler, role=SCHEDULER_ROLE
            )
            original.refresh_from_db()
            assert original.status == CaseStatus.AWAITING_SCHEDULING
            assert client.get(doc_serve).status_code == 200
            assert client.get(doc_viewer).status_code == 200
            assert (
                client.get(reverse("scheduler:case_pdf", args=[original.case_id, 1])).status_code
                == 404
            )
            assert (
                client.get(reverse("scheduler:pdf_viewer", args=[original.case_id, 1])).status_code
                == 404
            )

            # Reconfirma e dá ciência NIR em modo retenção (CLEANED retido)
            confirm_case_scheduling(
                original,
                unit=2,
                scheduled_datetime=timezone.now() + timedelta(days=3),
                scheduled_location="Sala 2",
                user=scheduler,
                role=SCHEDULER_ROLE,
            )
            original.refresh_from_db()
            with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False):
                acknowledge_case_receipt(original, user=nir, role=NIR_ROLE)
            original.refresh_from_db()
            assert original.status == CaseStatus.CLEANED
            assert original.clinical_data_disposed_at is None

            # CLEANED retido: recebido 200 mesmo com flag OFF; original 404; ?next= inseguro cai no histórico
            with override_settings(CASE_REAPPEARANCE_ENABLED=False):
                assert client.get(doc_serve).status_code == 200
                vresp = client.get(doc_viewer, {"next": "https://evil.example/steal"})
                assert vresp.status_code == 200
                expected_hist = reverse("scheduler:historical_case_detail", args=[original.case_id])
                assert expected_hist in vresp.content.decode()

                # ?next= seguro interno é preservado
                safe_next = "/scheduler/historical/?q=123"
                vresp_safe = client.get(doc_viewer, {"next": safe_next})
                assert vresp_safe.status_code == 200
                assert safe_next in vresp_safe.content.decode()

            assert (
                client.get(reverse("scheduler:case_pdf", args=[original.case_id, 1])).status_code
                == 404
            )
            assert (
                client.get(reverse("scheduler:pdf_viewer", args=[original.case_id, 1])).status_code
                == 404
            )

    def test_404_after_minimization_and_purge_without_path_leak(
        self,
        client: Client,
        user_factory: Callable[..., User],
        login_user: Callable[[User, str], None],
        tmp_path: Path,
    ) -> None:
        nir = user_factory("nir-3c-3", (NIR_ROLE,))
        doctor = user_factory("doc-3c-3", (DOCTOR_ROLE,))
        scheduler = user_factory("sched-3c-3", (SCHEDULER_ROLE,))

        with override_settings(STORAGES=_fs_storages(tmp_path)):
            # Caso A: minimização na ciência
            orig_min = _make_scheduler_requested(nir, doctor)
            confirm_case_scheduling(
                orig_min,
                unit=1,
                scheduled_datetime=timezone.now() + timedelta(days=2),
                scheduled_location="Sala 1",
                user=scheduler,
                role=SCHEDULER_ROLE,
            )
            orig_min.refresh_from_db()
            inc_min = Case.objects.create(created_by=nir)
            doc_min = _add_doc(inc_min, nir, position=1, filename="min.pdf")
            att_min = _add_attachment(
                inc_min,
                nir,
                filename="min-att.pdf",
                content=VALID_PDF_BYTES,
                content_type="application/pdf",
            )
            link_min = _link_scheduling_repeat(orig_min, inc_min)

            # Caso B: retenção seguida de purga explícita
            orig_purge = _make_scheduler_requested(nir, doctor)
            confirm_case_scheduling(
                orig_purge,
                unit=1,
                scheduled_datetime=timezone.now() + timedelta(days=2),
                scheduled_location="Sala 1",
                user=scheduler,
                role=SCHEDULER_ROLE,
            )
            orig_purge.refresh_from_db()
            inc_purge = Case.objects.create(created_by=nir)
            doc_purge = _add_doc(inc_purge, nir, position=1, filename="purge.pdf")
            link_purge = _link_scheduling_repeat(orig_purge, inc_purge)
            with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False):
                acknowledge_case_receipt(orig_purge, user=nir, role=NIR_ROLE)

            # Executa minimização no Caso A
            with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=True):
                acknowledge_case_receipt(orig_min, user=nir, role=NIR_ROLE)

            # Executa purga no Caso B
            call_command("purge_closed_case_data")

            login_user(scheduler, SCHEDULER_ROLE)

            for url in (
                reverse(
                    "scheduler:reappearance_document_serve",
                    args=[orig_min.case_id, link_min.id, doc_min.pk],
                ),
                reverse(
                    "scheduler:reappearance_document_viewer",
                    args=[orig_min.case_id, link_min.id, doc_min.pk],
                ),
                reverse(
                    "scheduler:reappearance_attachment_serve",
                    args=[orig_min.case_id, link_min.id, att_min.pk],
                ),
                reverse(
                    "scheduler:reappearance_attachment_viewer",
                    args=[orig_min.case_id, link_min.id, att_min.pk],
                ),
                reverse(
                    "scheduler:reappearance_document_serve",
                    args=[orig_purge.case_id, link_purge.id, doc_purge.pk],
                ),
                reverse(
                    "scheduler:reappearance_document_viewer",
                    args=[orig_purge.case_id, link_purge.id, doc_purge.pk],
                ),
            ):
                resp = client.get(url)
                assert resp.status_code == 404
                body = resp.content.decode(errors="ignore")
                assert str(tmp_path) not in body
                assert "case_documents" not in body
                assert "case_attachments" not in body

    def test_roles_and_negative_authorization_matrix(
        self,
        client: Client,
        user_factory: Callable[..., User],
        login_user: Callable[[User, str], None],
        tmp_path: Path,
    ) -> None:
        nir = user_factory("nir-3c-4", (NIR_ROLE,))
        doctor = user_factory("doc-3c-4", (DOCTOR_ROLE,))
        manager = user_factory("mgr-3c-4", (MANAGER_ROLE,))
        scheduler = user_factory("sched-3c-4", (SCHEDULER_ROLE,))
        admin = user_factory("adm-3c-4", (ADMIN_ROLE,))

        with override_settings(STORAGES=_fs_storages(tmp_path)):
            orig1 = _make_scheduler_requested(nir, doctor)
            orig2 = _make_scheduler_requested(nir, doctor)
            inc1 = Case.objects.create(created_by=nir)
            inc2 = Case.objects.create(created_by=nir)
            doc1 = _add_doc(inc1, nir, position=1, filename="d1.pdf")
            doc2 = _add_doc(inc2, nir, position=1, filename="d2.pdf")
            att1 = _add_attachment(
                inc1,
                nir,
                filename="a1.pdf",
                content=VALID_PDF_BYTES,
                content_type="application/pdf",
            )
            link1 = _link_scheduling_repeat(orig1, inc1)
            link2 = _link_scheduling_repeat(orig2, inc2)

            valid_doc_serve = reverse(
                "scheduler:reappearance_document_serve", args=[orig1.case_id, link1.id, doc1.pk]
            )
            valid_doc_viewer = reverse(
                "scheduler:reappearance_document_viewer", args=[orig1.case_id, link1.id, doc1.pk]
            )
            valid_att_serve = reverse(
                "scheduler:reappearance_attachment_serve", args=[orig1.case_id, link1.id, att1.pk]
            )
            valid_att_viewer = reverse(
                "scheduler:reappearance_attachment_viewer", args=[orig1.case_id, link1.id, att1.pk]
            )

            # Papéis não scheduler/admin -> 403 em todas as 4 rotas
            for user, role in ((nir, NIR_ROLE), (doctor, DOCTOR_ROLE), (manager, MANAGER_ROLE)):
                login_user(user, role)
                for url in (valid_doc_serve, valid_doc_viewer, valid_att_serve, valid_att_viewer):
                    assert client.get(url).status_code == 403

            # Admin tem acesso (200)
            login_user(admin, ADMIN_ROLE)
            assert client.get(valid_doc_serve).status_code == 200

            login_user(scheduler, SCHEDULER_ROLE)

            # IDs trocados -> 404
            for url in (
                reverse(
                    "scheduler:reappearance_document_serve",
                    args=[orig2.case_id, link1.id, doc1.pk],
                ),
                reverse(
                    "scheduler:reappearance_document_serve",
                    args=[orig1.case_id, link2.id, doc1.pk],
                ),
                reverse(
                    "scheduler:reappearance_document_serve",
                    args=[orig1.case_id, link1.id, doc2.pk],
                ),
                reverse(
                    "scheduler:reappearance_attachment_serve",
                    args=[orig2.case_id, link1.id, att1.pk],
                ),
            ):
                assert client.get(url).status_code == 404

            # Vínculos pending / independent / medical_replacement -> 404
            for bad_state, bad_kind in (
                (ReappearanceState.PENDING, ResolutionKind.SCHEDULING_REPEAT),
                (ReappearanceState.INDEPENDENT, ResolutionKind.SCHEDULING_REPEAT),
                (ReappearanceState.APPLIED, ResolutionKind.MEDICAL_REPLACEMENT),
            ):
                inc_bad = Case.objects.create(created_by=nir)
                doc_bad = _add_doc(inc_bad, nir, position=1, filename="bad.pdf")
                link_bad = _link_scheduling_repeat(
                    orig1,
                    inc_bad,
                    state=bad_state,
                    resolution_kind=bad_kind,
                    canonical_case=inc_bad
                    if bad_kind == ResolutionKind.MEDICAL_REPLACEMENT
                    else orig1,
                )
                assert (
                    client.get(
                        reverse(
                            "scheduler:reappearance_document_serve",
                            args=[orig1.case_id, link_bad.id, doc_bad.pk],
                        )
                    ).status_code
                    == 404
                )

            # Original em AWAITING_DOCTOR -> 404
            awaiting_doc = Case.objects.create(created_by=nir)
            _advance_to_awaiting_doctor(awaiting_doc)
            inc_aw = Case.objects.create(created_by=nir)
            doc_aw = _add_doc(inc_aw, nir, position=1, filename="aw.pdf")
            link_aw = _link_scheduling_repeat(awaiting_doc, inc_aw)
            assert (
                client.get(
                    reverse(
                        "scheduler:reappearance_document_serve",
                        args=[awaiting_doc.case_id, link_aw.id, doc_aw.pk],
                    )
                ).status_code
                == 404
            )

            # Negativa médica sem scheduling_repeat aplicado -> 404
            med_denied = _make_medical_denial_case(nir, doctor)
            assert (
                client.get(
                    reverse(
                        "scheduler:reappearance_document_serve",
                        args=[med_denied.case_id, link1.id, doc1.pk],
                    )
                ).status_code
                == 404
            )

            # Arquivo físico ausente no disco -> 404 sem vazar path
            Path(doc1.file.path).unlink()
            missing_serve = client.get(valid_doc_serve)
            assert missing_serve.status_code == 404
            assert str(tmp_path) not in missing_serve.content.decode(errors="ignore")
            missing_viewer = client.get(valid_doc_viewer)
            assert missing_viewer.status_code == 404
            assert str(tmp_path) not in missing_viewer.content.decode(errors="ignore")
