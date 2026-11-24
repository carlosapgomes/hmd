"""Testes de UI do agendador para reapresentações e espera herdada (Slice 004, Unit 4A)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from apps.accounts.models import Role, User
from apps.attachments.models import AttachmentStatus, CaseAttachment
from apps.cases.closure import acknowledge_case_receipt
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
from apps.scheduler.services import confirm_case_scheduling

SCHEDULER_ROLE = "scheduler"
DOCTOR_ROLE = "doctor"
NIR_ROLE = "nir"
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
CARDIO_TYPE = "cat_cardiaco"

VALID_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
VALID_JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


def _fs_storages(root: Path) -> dict[str, dict[str, object]]:
    return {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(root)},
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


def _create_user(username: str, role: str, *, display_name: str = "") -> User:
    user = User.objects.create_user(
        username=username,
        password="secret",
        first_name=display_name or username,
        is_active=True,
    )
    role_obj, _ = Role.objects.get_or_create(name=role)
    user.roles.add(role_obj)
    return user


def _login_as(client: Client, user: User, role: str) -> None:
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _advance_to_awaiting_doctor(
    case: Case,
    procedure_types: Sequence[str] = (ANGIO_TYPE,),
) -> None:
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


def _make_scheduler_requested(
    created_by: User,
    decided_by: User,
    *,
    patient_name: str = "Paciente Teste",
    agency_record_number: str = "100200",
    procedure_types: Sequence[str] = (ANGIO_TYPE,),
    days_on_screen: int | None = 3,
    patient_age: int | None = 55,
) -> Case:
    case = Case.objects.create(
        created_by=created_by,
        patient_name=patient_name,
        agency_record_number=agency_record_number,
        days_on_screen=days_on_screen,
        patient_age=patient_age,
    )
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


def _make_applied_reappearance(
    *,
    canonical: Case,
    submitter: User,
    record_number: str,
    applied_at: datetime,
    attempt_count: int = 1,
    add_pdf: bool = True,
    add_image_att: bool = False,
    add_pdf_att: bool = False,
) -> CaseReappearance:
    incoming = Case.objects.create(
        created_by=submitter,
        patient_name=canonical.patient_name or "Paciente Teste",
        agency_record_number=record_number,
        status=CaseStatus.CLEANED,
    )
    if add_pdf:
        CaseDocument.objects.create(
            case=incoming,
            uploaded_by=submitter,
            position=1,
            original_filename=f"relatorio_{record_number}.pdf",
            size_bytes=len(VALID_PDF_BYTES),
            file=SimpleUploadedFile(
                f"relatorio_{record_number}.pdf", VALID_PDF_BYTES, content_type="application/pdf"
            ),
        )
    if add_image_att:
        CaseAttachment.objects.create(
            case=incoming,
            uploaded_by=submitter,
            original_filename=f"exame_{record_number}.jpg",
            content_type="image/jpeg",
            size_bytes=len(VALID_JPEG_BYTES),
            status=AttachmentStatus.PROCESSED,
            file=SimpleUploadedFile(
                f"exame_{record_number}.jpg", VALID_JPEG_BYTES, content_type="image/jpeg"
            ),
        )
    if add_pdf_att:
        CaseAttachment.objects.create(
            case=incoming,
            uploaded_by=submitter,
            original_filename=f"laudo_{record_number}.pdf",
            content_type="application/pdf",
            size_bytes=len(VALID_PDF_BYTES),
            status=AttachmentStatus.PROCESSED,
            file=SimpleUploadedFile(
                f"laudo_{record_number}.pdf", VALID_PDF_BYTES, content_type="application/pdf"
            ),
        )
    return CaseReappearance.objects.create(
        incoming_case=incoming,
        prior_case=canonical,
        canonical_case=canonical,
        revision_digest=f"v1:{uuid.uuid4().hex}",
        evidence_code="occurrence_number",
        state=ReappearanceState.APPLIED,
        resolution_kind=ResolutionKind.SCHEDULING_REPEAT,
        applied_at=applied_at,
        attempt_count=attempt_count,
    )


@pytest.mark.django_db
class TestSchedulerQueueReappearanceCounterR1:
    """R1: contador de reapresentações, último recebimento, link âncora e zero N+1."""

    def test_three_distinct_incomings_plus_retry_shows_count_3_and_anchor_on_queue_and_partial(
        self, client: Client, tmp_path: Path
    ) -> None:
        nir = _create_user("nir-r1", NIR_ROLE, display_name="NIR Um")
        doctor = _create_user("doc-r1", DOCTOR_ROLE)
        scheduler = _create_user("sched-r1", SCHEDULER_ROLE)

        with override_settings(STORAGES=_fs_storages(tmp_path)):
            canonical = _make_scheduler_requested(
                nir, doctor, patient_name="Maria Reap", procedure_types=(ANGIO_TYPE, CARDIO_TYPE)
            )
            t1 = timezone.now() - timedelta(hours=5)
            t2 = timezone.now() - timedelta(hours=3)
            t3 = timezone.now() - timedelta(minutes=45)
            _make_applied_reappearance(
                canonical=canonical, submitter=nir, record_number="300001", applied_at=t1
            )
            _make_applied_reappearance(
                canonical=canonical, submitter=nir, record_number="300002", applied_at=t2
            )
            _make_applied_reappearance(
                canonical=canonical,
                submitter=nir,
                record_number="300003",
                applied_at=t3,
                attempt_count=2,
            )

        _login_as(client, scheduler, SCHEDULER_ROLE)
        expected_last = date_format(timezone.localtime(t3), "d/m/Y H:i")
        detail_url = reverse("scheduler:case_detail", args=[canonical.case_id])
        expected_anchor = f"{detail_url}#reapresentacoes-recebidas"

        for url in (
            reverse("scheduler:queue"),
            reverse("scheduler:queue_partial"),
            f"{reverse('scheduler:queue')}?procedure_type={ANGIO_TYPE}",
            f"{reverse('scheduler:queue_partial')}?procedure_type={ANGIO_TYPE}",
        ):
            resp = client.get(url)
            assert resp.status_code == 200
            html = resp.content.decode()
            assert "Caso reapresentado 3 vezes enquanto aguarda agendamento" in html
            assert expected_last in html
            assert expected_anchor in html
            assert 'class="position-relative z-2' in html or "position-relative z-2" in html

    def test_singular_label_for_one_reappearance(self, client: Client, tmp_path: Path) -> None:
        nir = _create_user("nir-r1-sing", NIR_ROLE)
        doctor = _create_user("doc-r1-sing", DOCTOR_ROLE)
        scheduler = _create_user("sched-r1-sing", SCHEDULER_ROLE)

        with override_settings(STORAGES=_fs_storages(tmp_path)):
            canonical = _make_scheduler_requested(nir, doctor, patient_name="Singular Pac")
            _make_applied_reappearance(
                canonical=canonical,
                submitter=nir,
                record_number="300010",
                applied_at=timezone.now() - timedelta(minutes=10),
            )

        _login_as(client, scheduler, SCHEDULER_ROLE)
        html = client.get(reverse("scheduler:queue")).content.decode()
        assert "Caso reapresentado 1 vez enquanto aguarda agendamento" in html

    def test_query_count_constant_between_1_and_5_cards_with_reappearances(
        self, client: Client, tmp_path: Path
    ) -> None:
        nir = _create_user("nir-r1-q", NIR_ROLE)
        doctor = _create_user("doc-r1-q", DOCTOR_ROLE)
        scheduler = _create_user("sched-r1-q", SCHEDULER_ROLE)
        _login_as(client, scheduler, SCHEDULER_ROLE)

        with override_settings(STORAGES=_fs_storages(tmp_path)):
            c1 = _make_scheduler_requested(nir, doctor, patient_name="Card 1")
            for idx in range(2):
                _make_applied_reappearance(
                    canonical=c1,
                    submitter=nir,
                    record_number=f"4000{idx}",
                    applied_at=timezone.now() - timedelta(minutes=10 + idx),
                )

            # Warmup session/auth queries
            client.get(reverse("scheduler:queue_partial"))
            with CaptureQueriesContext(connection) as ctx1:
                resp1 = client.get(reverse("scheduler:queue_partial"))
                assert resp1.status_code == 200

            for card_idx in range(2, 6):
                c = _make_scheduler_requested(nir, doctor, patient_name=f"Card {card_idx}")
                for idx in range(3):
                    _make_applied_reappearance(
                        canonical=c,
                        submitter=nir,
                        record_number=f"4{card_idx}00{idx}",
                        applied_at=timezone.now() - timedelta(minutes=10 + idx),
                    )

            with CaptureQueriesContext(connection) as ctx5:
                resp5 = client.get(reverse("scheduler:queue_partial"))
                assert resp5.status_code == 200

        assert len(ctx5) <= len(ctx1) + 1


@pytest.mark.django_db
class TestSchedulerCaseDetailReappearancesR2:
    """R2: seção separada no detalhe de trabalho, lazy-embed, viewer mobile e confirm POST."""

    def test_case_detail_shows_reappearances_section_lazy_embed_and_confirm_post_works(
        self, client: Client, tmp_path: Path
    ) -> None:
        nir = _create_user("nir-r2", NIR_ROLE, display_name="Operador NIR R2")
        doctor = _create_user("doc-r2", DOCTOR_ROLE)
        scheduler = _create_user("sched-r2", SCHEDULER_ROLE)

        with override_settings(STORAGES=_fs_storages(tmp_path)):
            canonical = _make_scheduler_requested(
                nir, doctor, patient_name="Paciente R2", agency_record_number="500100"
            )
            CaseDocument.objects.create(
                case=canonical,
                uploaded_by=nir,
                position=1,
                original_filename="original_aprovado.pdf",
                size_bytes=len(VALID_PDF_BYTES),
                file=SimpleUploadedFile(
                    "original_aprovado.pdf", VALID_PDF_BYTES, content_type="application/pdf"
                ),
            )
            t1 = timezone.now() - timedelta(hours=2)
            link = _make_applied_reappearance(
                canonical=canonical,
                submitter=nir,
                record_number="500101",
                applied_at=t1,
                add_pdf=True,
                add_image_att=True,
                add_pdf_att=True,
            )

            _login_as(client, scheduler, SCHEDULER_ROLE)
            detail_url = reverse("scheduler:case_detail", args=[canonical.case_id])
            resp = client.get(detail_url)
            assert resp.status_code == 200
            html = resp.content.decode()

            # Página de trabalho única com POST confirm/deny e lock_token
            assert reverse("scheduler:case_confirm", args=[canonical.case_id]) in html
            assert reverse("scheduler:case_deny", args=[canonical.case_id]) in html
            assert 'name="lock_token"' in html

            # Original PDF não liberado antes da decisão
            assert "original_aprovado.pdf" not in html
            assert "Visualizar PDF Original" not in html

            # Seção de reapresentações recebidas
            assert 'id="reapresentacoes-recebidas"' in html
            assert "não substitui o relatório aprovado pelo médico" in html
            assert "Operador NIR R2" in html
            assert "500101" in html
            assert date_format(timezone.localtime(t1), "d/m/Y H:i") in html
            assert f"reapp-doc-{link.id}-" in html
            assert f"reapp-att-{link.id}-" in html
            assert "pdf-lazy-embed.js" in html

            # Embeds têm data-src sem src
            embeds = re.findall(r"<embed\b[^>]*>", html)
            assert len(embeds) == 2
            for embed_tag in embeds:
                assert 'data-src="' in embed_tag
                assert ' src="' not in embed_tag

            # Imagem via serve, sem viewer link
            att_img = CaseAttachment.objects.get(case=link.incoming_case, content_type="image/jpeg")
            img_serve = reverse(
                "scheduler:reappearance_attachment_serve",
                args=[canonical.case_id, link.id, att_img.pk],
            )
            img_viewer = reverse(
                "scheduler:reappearance_attachment_viewer",
                args=[canonical.case_id, link.id, att_img.pk],
            )
            assert img_serve in html
            assert img_viewer not in html

            # Mobile viewer link para PDF recebido inclui ?next= apontando ao detalhe
            doc = CaseDocument.objects.get(case=link.incoming_case, position=1)
            doc_viewer = reverse(
                "scheduler:reappearance_document_viewer",
                args=[canonical.case_id, link.id, doc.pk],
            )
            assert f"{doc_viewer}?next=" in html

            # Confirmar agendamento via POST na mesma página de trabalho continua funcionando
            lock_token_match = re.search(r'name="lock_token" value="([^"]+)"', html)
            assert lock_token_match is not None
            future_date = (timezone.localdate() + timedelta(days=2)).isoformat()
            post_resp = client.post(
                reverse("scheduler:case_confirm", args=[canonical.case_id]),
                data={
                    "lock_token": lock_token_match.group(1),
                    "unit": "1",
                    "scheduled_date": future_date,
                    "scheduled_time": "10:30",
                    "scheduled_location": "Sala 1",
                },
            )
            assert post_resp.status_code == 302
            canonical.refresh_from_db()
            assert canonical.status == CaseStatus.FINAL_REPLY_POSTED


@pytest.mark.django_db
class TestSchedulerHistoricalReappearancesR3:
    """R3: histórico CLEANED retido vs minimizado/purgado, original protegido, flag off."""

    def test_cleaned_retained_shows_reappearances_while_original_stays_404(
        self, client: Client, tmp_path: Path
    ) -> None:
        nir = _create_user("nir-r3", NIR_ROLE, display_name="NIR Retido")
        doctor = _create_user("doc-r3", DOCTOR_ROLE)
        scheduler = _create_user("sched-r3", SCHEDULER_ROLE)

        with override_settings(
            STORAGES=_fs_storages(tmp_path),
            CASE_CLOSURE_MINIMIZATION_ENABLED=False,
            CASE_REAPPEARANCE_ENABLED=False,
        ):
            canonical = _make_scheduler_requested(nir, doctor, patient_name="Retido Hist")
            CaseDocument.objects.create(
                case=canonical,
                uploaded_by=nir,
                position=1,
                original_filename="orig_cleaned.pdf",
                size_bytes=len(VALID_PDF_BYTES),
                file=SimpleUploadedFile(
                    "orig_cleaned.pdf", VALID_PDF_BYTES, content_type="application/pdf"
                ),
            )
            link = _make_applied_reappearance(
                canonical=canonical,
                submitter=nir,
                record_number="600101",
                applied_at=timezone.now() - timedelta(hours=1),
                add_pdf=True,
            )
            confirm_case_scheduling(
                canonical,
                unit=1,
                scheduled_datetime=timezone.now() + timedelta(days=2),
                scheduled_location="Hemodinâmica",
                user=scheduler,
                role=SCHEDULER_ROLE,
            )
            acknowledge_case_receipt(canonical, user=nir, role=NIR_ROLE)
            canonical.refresh_from_db()
            assert canonical.status == CaseStatus.CLEANED

            _login_as(client, scheduler, SCHEDULER_ROLE)
            hist_url = reverse("scheduler:historical_case_detail", args=[canonical.case_id])
            resp = client.get(hist_url)
            assert resp.status_code == 200
            html = resp.content.decode()

            assert 'id="reapresentacoes-recebidas"' in html
            assert "pdf-lazy-embed.js" in html
            assert "orig_cleaned.pdf" not in html
            assert "Visualizar PDF Original" not in html

            doc = CaseDocument.objects.get(case=link.incoming_case, position=1)
            doc_viewer = reverse(
                "scheduler:reappearance_document_viewer",
                args=[canonical.case_id, link.id, doc.pk],
            )
            assert doc_viewer in html
            # ?next= aponta ao historical_case_detail
            assert "historical" in html

            # PDF original continua 404
            assert (
                client.get(reverse("scheduler:case_pdf", args=[canonical.case_id, 1])).status_code
                == 404
            )

    def test_cleaned_minimized_or_purged_shows_unavailable_without_broken_links(
        self, client: Client, tmp_path: Path
    ) -> None:
        nir = _create_user("nir-r3-min", NIR_ROLE)
        doctor = _create_user("doc-r3-min", DOCTOR_ROLE)
        scheduler = _create_user("sched-r3-min", SCHEDULER_ROLE)

        with override_settings(
            STORAGES=_fs_storages(tmp_path),
            CASE_CLOSURE_MINIMIZATION_ENABLED=False,
        ):
            canonical = _make_scheduler_requested(nir, doctor, patient_name="Purgado Hist")
            link = _make_applied_reappearance(
                canonical=canonical,
                submitter=nir,
                record_number="600201",
                applied_at=timezone.now() - timedelta(hours=1),
                add_pdf=True,
                add_image_att=True,
            )
            confirm_case_scheduling(
                canonical,
                unit=1,
                scheduled_datetime=timezone.now() + timedelta(days=2),
                scheduled_location="Hemodinâmica",
                user=scheduler,
                role=SCHEDULER_ROLE,
            )
            acknowledge_case_receipt(canonical, user=nir, role=NIR_ROLE)
            call_command("purge_closed_case_data")

            _login_as(client, scheduler, SCHEDULER_ROLE)
            hist_url = reverse("scheduler:historical_case_detail", args=[canonical.case_id])
            resp = client.get(hist_url)
            assert resp.status_code == 200
            html = resp.content.decode()

            assert 'id="reapresentacoes-recebidas"' in html
            assert "Documentos removidos por minimização ou purga" in html
            assert f"/reappearances/{link.id}/" not in html


@pytest.mark.django_db
class TestSchedulerInheritedWaitR4:
    """R4: espera herdada da fila médica chega ao card/partial e ordenação FIFO do agendamento."""

    def test_inherited_wait_ordering_and_display_with_age_zero_and_days_null(
        self, client: Client
    ) -> None:
        nir = _create_user("nir-r4", NIR_ROLE)
        doctor = _create_user("doc-r4", DOCTOR_ROLE)
        scheduler = _create_user("sched-r4", SCHEDULER_ROLE)

        now = timezone.now()
        # Caso A: criado hoje, mas herdou espera de 5 dias atrás; days_on_screen=None, age=0
        case_inherited = _make_scheduler_requested(
            nir,
            doctor,
            patient_name="Herdado Antigo",
            agency_record_number="700001",
            days_on_screen=None,
            patient_age=0,
        )
        inherited_dt = now - timedelta(days=5)
        Case.objects.filter(pk=case_inherited.pk).update(
            reappearance_wait_started_at=inherited_dt,
            created_at=now - timedelta(hours=1),
        )

        # Caso B: criado há 2 dias, sem herança, days_on_screen=None
        case_older_created = _make_scheduler_requested(
            nir,
            doctor,
            patient_name="Criado Dois Dias",
            agency_record_number="700002",
            days_on_screen=None,
            patient_age=40,
        )
        Case.objects.filter(pk=case_older_created.pk).update(created_at=now - timedelta(days=2))

        # Caso C: criado hoje, days_on_screen=10 (prioridade primária acima de None)
        _make_scheduler_requested(
            nir,
            doctor,
            patient_name="Dez Dias Tela",
            agency_record_number="700003",
            days_on_screen=10,
            patient_age=60,
        )

        _login_as(client, scheduler, SCHEDULER_ROLE)
        for url in (reverse("scheduler:queue"), reverse("scheduler:queue_partial")):
            resp = client.get(url)
            assert resp.status_code == 200
            html = resp.content.decode()
            pos_days = html.index("Dez Dias Tela")
            pos_inherited = html.index("Herdado Antigo")
            pos_older = html.index("Criado Dois Dias")
            assert pos_days < pos_inherited < pos_older
            assert "Herdado Antigo · 0 a" in html
            assert "5\xa0dias" in html or "5 dias" in html

        # created_at real intacto
        case_inherited.refresh_from_db()
        assert case_inherited.created_at > now - timedelta(hours=2)

        # Aba Processados Hoje não alterada (usa created_at, sem contador de reapresentação)
        confirm_case_scheduling(
            case_inherited,
            unit=1,
            scheduled_datetime=now + timedelta(days=1),
            scheduled_location="Sala 2",
            user=scheduler,
            role=SCHEDULER_ROLE,
        )
        proc_html = client.get(f"{reverse('scheduler:queue')}?tab=processados").content.decode()
        assert "Herdado Antigo" in proc_html
        assert "Recebido há" in proc_html
        assert "Caso reapresentado" not in proc_html


@pytest.mark.django_db
class TestManualReappearanceSchedulerR5:
    """R5: manual documenta contador, seção de recebidos, histórico retido e espera herdada."""

    def test_manual_documents_scheduler_reappearance_flow(self, client: Client) -> None:
        scheduler = _create_user("sched-r5-man", SCHEDULER_ROLE)
        _login_as(client, scheduler, SCHEDULER_ROLE)
        html = client.get(reverse("manual")).content.decode().lower()
        assert "reapresent" in html
        assert "espera herdada" in html
        assert "histórico" in html
        assert "não substitui" in html or "não suspende" in html
