"""Testes da Unidade 3B (Slice 003 — R4.1–R4.4 e R5.1–R5.3).

Cobre:
- R4.1: Disposição de grupo em ``acknowledge_case_receipt`` e
  ``administratively_close_case`` (minimização limpa canônico + todos os
  incomings ``scheduling_repeat`` aplicados no mesmo atomic, remove arquivos
  pós-commit; retenção preserva o grupo inteiro).
- R4.2: Proteção de incoming ``CLEANED`` vinculado a canônico não-``CLEANED``
  (inclusive ``FINAL_REPLY_POSTED``) no serviço e na CLI (``--dry-run`` e real).
- R4.3: Grupo reabrível indivisível (``purge_closed_case_data(incoming)`` com
  canônico ``CLEANED`` e ``clinical_data_disposed_at IS NULL`` redireciona ao
  grupo inteiro e marca ``clinical_data_disposed_at`` no canônico); deduplicação
  por canônico na CLI (``--dry-run`` e real).
- R4.4: ``atomic(durable=True)`` preservado, deleção física síncrona pós-commit,
  eventos PII-free por caso, falha física por caso com ``CommandError``,
  idempotência na reexecução.
- R5.1: Trace SQL de trava de grupo ordenada por PK (``ORDER BY case_id FOR UPDATE``)
  e injeção de deriva (máx. 3 tentativas, ``ClosureGroupDriftError`` sem efeito parcial).
- R5.2 / R5.3: Corridas reais em PostgreSQL com conexões separadas e barreiras
  determinísticas (``threading.Barrier``) + ``FileSystemStorage`` real.
"""

from __future__ import annotations

import io
import tempfile
import threading
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import connections, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.attachments.models import AttachmentStatus, CaseAttachment
from apps.cases import closure as closure_module
from apps.cases.closure import (
    PurgeResult,
    acknowledge_case_receipt,
    administratively_close_case,
    open_post_closure_issue,
    purge_closed_case_data,
)
from apps.cases.events import CaseEventType
from apps.cases.models import (
    Case,
    CaseDocument,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    ReappearanceState,
    ResolutionKind,
    SchedulingUnit,
)
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.cases.reappearance import resolve_scheduling_repeat
from apps.scheduler.services import confirm_case_scheduling, deny_case_scheduling

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SCHEDULER_ROLE = "scheduler"
MANAGER_ROLE = "manager"
SYSTEM_ROLE = "system"

ANGIO_TYPE = "art_perif"
PATIENT_NAME = "Maria da Silva"
RECORD_NUMBER = "44556"

EXTRACTED_TEXT = "TEXTO-CLINICO-SECRETO-3B"
ANONYMIZED_TEXT = "TEXTO-ANONIMIZADO-SECRETO-3B"
SUMMARY_TEXT = "RESUMO-SECRETO-3B"
DOCUMENT_FILENAME = "relatorio-secreto-3b.pdf"
ATTACHMENT_FILENAME = "anexo-secreto-3b.jpg"

_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _file_name(obj: CaseDocument | CaseAttachment) -> str:
    name = obj.file.name
    assert isinstance(name, str) and name
    return name


def _noop_enqueue(case_id: object) -> None:
    del case_id


def _make_user(username: str, role_name: str) -> User:
    role, _ = Role.objects.get_or_create(name=role_name)
    user, created = User.objects.get_or_create(username=username)
    if created:
        user.set_password("senha-teste")
        user.save()
    user.roles.add(role)
    return user


def _seed_clinical_data(
    case: Case, *, name: str = PATIENT_NAME, record: str = RECORD_NUMBER
) -> None:
    case.patient_name = name
    case.agency_record_number = record
    case.extracted_text = EXTRACTED_TEXT
    case.anonymized_text = ANONYMIZED_TEXT
    case.pseudonym_map = {"<PESSOA_1>": name}
    case.structured_data = {"procedures": [{"procedure_type": ANGIO_TYPE}]}
    case.summary_text = SUMMARY_TEXT
    case.suggested_action = {"text": "Agendar"}
    case.policy_result = {"verdict": "allowed"}
    case.save()


def _attach_doc(case: Case, uploaded_by: User, filename: str = DOCUMENT_FILENAME) -> CaseDocument:
    uploaded = SimpleUploadedFile(filename, b"%PDF-1.4 conteudo 3B", content_type="application/pdf")
    doc = CaseDocument(
        case=case,
        position=1,
        original_filename=filename,
        content_type="application/pdf",
        size_bytes=uploaded.size or 0,
        uploaded_by=uploaded_by,
    )
    doc.file.save(filename, uploaded, save=False)
    doc.save()
    return doc


def _attach_att(
    case: Case, uploaded_by: User, filename: str = ATTACHMENT_FILENAME
) -> CaseAttachment:
    uploaded = SimpleUploadedFile(filename, b"\xff\xd8\xff\xe0anexo 3B", content_type="image/jpeg")
    att = CaseAttachment(
        case=case,
        original_filename=filename,
        content_type="image/jpeg",
        size_bytes=uploaded.size or 0,
        uploaded_by=uploaded_by,
        status=AttachmentStatus.PROCESSED,
        extracted_text="texto anexo 3B",
        anonymized_text="anon anexo 3B",
        pseudonym_map={"<PESSOA_1>": PATIENT_NAME},
        processed_at=timezone.now(),
    )
    att.file.save(filename, uploaded, save=False)
    att.save()
    return att


def _drive_to_awaiting_doctor(case: Case) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)


def _make_scheduler_requested(
    owner: User, doctor: User, *, name: str = PATIENT_NAME, record: str = RECORD_NUMBER
) -> Case:
    case = Case.objects.create(created_by=owner)
    CaseProcedure.objects.create(case=case, procedure_type=ANGIO_TYPE, declared_by_nir=True)
    _seed_clinical_data(case, name=name, record=record)
    _drive_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case, {ANGIO_TYPE: ("approved", "")}, user=doctor, role=DOCTOR_ROLE
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    return case


def _make_cleaned_via_ack(
    owner: User,
    doctor: User,
    scheduler: User,
    *,
    minimize: bool = False,
    unit: int = SchedulingUnit.UNIT_1,
) -> Case:
    case = _make_scheduler_requested(owner, doctor)
    _attach_doc(case, owner)
    _attach_att(case, owner)
    confirm_case_scheduling(
        case,
        unit=unit,
        scheduled_datetime=timezone.now() + timedelta(days=3),
        scheduled_location="HGRS",
        user=scheduler,
        role=SCHEDULER_ROLE,
    )
    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=minimize):
        acknowledge_case_receipt(case, user=owner, role=NIR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    return case


def _link_scheduling_repeat_incoming(
    canonical: Case, owner: User, *, suffix: str = "1"
) -> tuple[Case, CaseReappearance]:
    incoming = Case.objects.create(created_by=owner)
    CaseProcedure.objects.create(case=incoming, procedure_type=ANGIO_TYPE, declared_by_nir=True)
    _seed_clinical_data(
        incoming, name=canonical.patient_name, record=canonical.agency_record_number
    )
    incoming.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    incoming.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    incoming.complete_anonymization(user=None, role=SYSTEM_ROLE)
    incoming.close_as_reappearance(user=None, role=SYSTEM_ROLE)
    _attach_doc(incoming, owner, f"inc-{suffix}.pdf")
    _attach_att(incoming, owner, f"inc-{suffix}.jpg")
    link = CaseReappearance.objects.create(
        incoming_case=incoming,
        prior_case=canonical,
        canonical_case=canonical,
        revision_digest=f"v1:test-{suffix}-{incoming.case_id}",
        evidence_code="occurrence_number",
        state=ReappearanceState.APPLIED,
        resolution_kind=ResolutionKind.SCHEDULING_REPEAT,
        applied_at=timezone.now(),
    )
    return incoming, link


def _run_purge_cli(*, dry_run: bool = False) -> str:
    out = io.StringIO()
    options: dict[str, object] = {"stdout": out}
    if dry_run:
        options["dry_run"] = True
    call_command("purge_closed_case_data", **options)
    return out.getvalue()


class _Base3BTransactionTestCase(TransactionTestCase):
    def setUp(self) -> None:
        self._storage_override = override_settings(STORAGES=_TEST_STORAGES)
        self._storage_override.enable()
        self.addCleanup(self._storage_override.disable)
        for target in (
            "apps.anonymization.tasks.enqueue_case_anonymization",
            "apps.pipeline.tasks.enqueue_case_pipeline",
        ):
            p = patch(target, _noop_enqueue)
            p.start()
            self.addCleanup(p.stop)
        self.nir = _make_user("nir-3b", NIR_ROLE)
        self.doctor = _make_user("doc-3b", DOCTOR_ROLE)
        self.scheduler = _make_user("sch-3b", SCHEDULER_ROLE)
        self.manager = _make_user("mgr-3b", MANAGER_ROLE)


# ── R4.1: Disposição de grupo em ack e admin closure ──────────────────────────


class TestGroupDispositionR41(_Base3BTransactionTestCase):
    def test_acknowledge_minimization_cleans_canonical_and_all_incomings(self) -> None:
        canonical = _make_scheduler_requested(self.nir, self.doctor)
        can_doc = _file_name(_attach_doc(canonical, self.nir, "can.pdf"))
        can_att = _file_name(_attach_att(canonical, self.nir, "can.jpg"))
        inc1, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="1")
        inc2, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="2")
        inc1_doc = _file_name(inc1.documents.get())
        inc1_att = _file_name(inc1.attachments.get())
        inc2_doc = _file_name(inc2.documents.get())
        inc2_att = _file_name(inc2.attachments.get())

        confirm_case_scheduling(
            canonical,
            unit=SchedulingUnit.UNIT_1,
            scheduled_datetime=timezone.now() + timedelta(days=2),
            scheduled_location="HGRS",
            user=self.scheduler,
            role=SCHEDULER_ROLE,
        )

        with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=True):
            acknowledge_case_receipt(canonical, user=self.nir, role=NIR_ROLE)

        for member in (canonical, inc1, inc2):
            member.refresh_from_db()
            assert member.status == CaseStatus.CLEANED
            assert member.clinical_data_disposed_at is not None
            assert member.extracted_text == ""
            assert member.summary_text == ""
            assert member.documents.count() == 0
            assert member.attachments.count() == 0

        for fname in (can_doc, can_att, inc1_doc, inc1_att, inc2_doc, inc2_att):
            assert not default_storage.exists(fname)

    def test_acknowledge_retention_preserves_canonical_and_incomings(self) -> None:
        canonical = _make_scheduler_requested(self.nir, self.doctor)
        can_doc = _file_name(_attach_doc(canonical, self.nir, "can-ret.pdf"))
        inc1, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="ret1")
        inc1_doc = _file_name(inc1.documents.get())

        confirm_case_scheduling(
            canonical,
            unit=SchedulingUnit.UNIT_1,
            scheduled_datetime=timezone.now() + timedelta(days=2),
            scheduled_location="HGRS",
            user=self.scheduler,
            role=SCHEDULER_ROLE,
        )

        with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False):
            acknowledge_case_receipt(canonical, user=self.nir, role=NIR_ROLE)

        for member in (canonical, inc1):
            member.refresh_from_db()
            assert member.status == CaseStatus.CLEANED
            assert member.clinical_data_disposed_at is None
            assert member.extracted_text == EXTRACTED_TEXT
            assert member.documents.count() == 1
        assert default_storage.exists(can_doc)
        assert default_storage.exists(inc1_doc)

    def test_admin_close_minimization_cleans_canonical_and_incomings(self) -> None:
        canonical = _make_scheduler_requested(self.nir, self.doctor)
        can_doc = _file_name(_attach_doc(canonical, self.nir, "adm-can.pdf"))
        inc1, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="adm1")
        inc1_doc = _file_name(inc1.documents.get())

        with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=True):
            administratively_close_case(
                case=canonical,
                user=self.manager,
                active_role=MANAGER_ROLE,
                reason_code="other",
                reason_text="encerramento de grupo",
            )

        for member in (canonical, inc1):
            member.refresh_from_db()
            assert member.status == CaseStatus.CLEANED
            assert member.clinical_data_disposed_at is not None
            assert member.extracted_text == ""
            assert member.documents.count() == 0
        assert not default_storage.exists(can_doc)
        assert not default_storage.exists(inc1_doc)

    def test_admin_close_retention_preserves_canonical_and_incomings(self) -> None:
        canonical = _make_scheduler_requested(self.nir, self.doctor)
        can_doc = _file_name(_attach_doc(canonical, self.nir, "adm-ret-can.pdf"))
        inc1, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="admret1")
        inc1_doc = _file_name(inc1.documents.get())

        with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False):
            administratively_close_case(
                case=canonical,
                user=self.manager,
                active_role=MANAGER_ROLE,
                reason_code="other",
                reason_text="encerramento retido",
            )

        for member in (canonical, inc1):
            member.refresh_from_db()
            assert member.status == CaseStatus.CLEANED
            assert member.clinical_data_disposed_at is None
            assert member.extracted_text == EXTRACTED_TEXT
            assert member.documents.count() == 1
        assert default_storage.exists(can_doc)
        assert default_storage.exists(inc1_doc)


# ── R4.2 / R4.3 / R4.4: Purga retroativa de grupo + proteção + CLI ───────────


class TestGroupPurgeR42ToR44(_Base3BTransactionTestCase):
    def test_r42_protects_incoming_when_canonical_in_final_reply_posted(self) -> None:
        canonical = _make_scheduler_requested(self.nir, self.doctor)
        inc1, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="prot1")
        inc1_doc = _file_name(inc1.documents.get())

        confirm_case_scheduling(
            canonical,
            unit=SchedulingUnit.UNIT_1,
            scheduled_datetime=timezone.now() + timedelta(days=2),
            scheduled_location="HGRS",
            user=self.scheduler,
            role=SCHEDULER_ROLE,
        )
        canonical.refresh_from_db()
        assert canonical.status == CaseStatus.FINAL_REPLY_POSTED

        result = purge_closed_case_data(inc1)
        assert result.protected is True
        assert result.documents_deleted == 0
        assert result.files_attempted == 0

        inc1.refresh_from_db()
        assert inc1.clinical_data_disposed_at is None
        assert inc1.extracted_text == EXTRACTED_TEXT
        assert inc1.documents.count() == 1
        assert default_storage.exists(inc1_doc)
        assert inc1.events.filter(event_type=CaseEventType.CASE_RETAINED_DATA_PURGED).count() == 0

        dry_out = _run_purge_cli(dry_run=True)
        assert inc1.display_reference in dry_out
        assert "Protegido" in dry_out
        assert EXTRACTED_TEXT not in dry_out

        real_out = _run_purge_cli(dry_run=False)
        assert inc1.display_reference in real_out
        assert "Protegido" in real_out
        inc1.refresh_from_db()
        assert inc1.documents.count() == 1
        assert default_storage.exists(inc1_doc)

    def test_r43_purge_incoming_with_reopenable_canonical_disposes_entire_group(self) -> None:
        canonical = _make_cleaned_via_ack(self.nir, self.doctor, self.scheduler, minimize=False)
        # Simular canônico sem dados próprios mas ainda reabrível (clinical_data_disposed_at IS NULL)
        canonical.documents.all().delete()
        canonical.attachments.all().delete()
        for f in ("extracted_text", "anonymized_text", "summary_text"):
            setattr(canonical, f, "")
        canonical.pseudonym_map = {}
        canonical.structured_data = {}
        canonical.suggested_action = {}
        canonical.policy_result = {}
        canonical.save()
        assert canonical.clinical_data_disposed_at is None

        inc1, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="reop1")
        inc2, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="reop2")
        inc1_doc = _file_name(inc1.documents.get())
        inc2_doc = _file_name(inc2.documents.get())

        result = purge_closed_case_data(inc1)
        assert result.protected is False
        assert result.documents_deleted == 2
        assert result.attachments_deleted == 2
        assert result.files_attempted == 4
        assert result.files_deleted == 4
        assert result.files_failed == 0

        canonical.refresh_from_db()
        inc1.refresh_from_db()
        inc2.refresh_from_db()
        assert canonical.clinical_data_disposed_at is not None
        assert inc1.clinical_data_disposed_at is not None
        assert inc2.clinical_data_disposed_at is not None
        assert not default_storage.exists(inc1_doc)
        assert not default_storage.exists(inc2_doc)

        # Canônico não pode mais ser reaberto após purga do grupo
        with pytest.raises(ValueError, match="dispost"):
            open_post_closure_issue(canonical, reason="tardia", user=self.nir, role=NIR_ROLE)

    def test_r43_purge_residual_incoming_when_canonical_already_disposed(self) -> None:
        canonical = _make_cleaned_via_ack(self.nir, self.doctor, self.scheduler, minimize=True)
        orig_disposed_at = canonical.clinical_data_disposed_at
        assert orig_disposed_at is not None
        events_before = canonical.events.count()

        inc1, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="resid1")
        inc1_doc = _file_name(inc1.documents.get())

        result = purge_closed_case_data(inc1)
        assert result.protected is False
        assert result.documents_deleted == 1
        assert result.attachments_deleted == 1
        assert result.files_attempted == 2
        assert result.files_deleted == 2

        canonical.refresh_from_db()
        inc1.refresh_from_db()
        assert canonical.clinical_data_disposed_at == orig_disposed_at
        assert canonical.events.count() == events_before
        assert inc1.clinical_data_disposed_at is not None
        assert inc1.documents.count() == 0
        assert not default_storage.exists(inc1_doc)
        assert inc1.events.filter(event_type=CaseEventType.CASE_RETAINED_DATA_PURGED).count() == 1
        assert (
            inc1.events.filter(event_type=CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME).count()
            == 1
        )

        # Também via CLI: purga residual sem erro e preserva canônico
        inc2, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="resid2")
        cli_out = _run_purge_cli(dry_run=False)
        assert canonical.display_reference in cli_out
        inc2.refresh_from_db()
        canonical.refresh_from_db()
        assert inc2.clinical_data_disposed_at is not None
        assert inc2.documents.count() == 0
        assert canonical.clinical_data_disposed_at == orig_disposed_at
        assert canonical.events.count() == events_before

    def test_r42_cli_handles_concurrent_canonical_reopen_gracefully(self) -> None:
        reopened_canon = _make_cleaned_via_ack(
            self.nir, self.doctor, self.scheduler, minimize=False
        )
        inc_protected, _ = _link_scheduling_repeat_incoming(
            reopened_canon, self.nir, suffix="concreop"
        )
        other_canon = _make_cleaned_via_ack(self.nir, self.doctor, self.scheduler, minimize=False)

        real_purge = closure_module.purge_closed_case_data

        def _reopen_first_then_purge(case: Case) -> PurgeResult:
            if case.pk == reopened_canon.pk:
                open_post_closure_issue(
                    reopened_canon, reason="reabertura concorrente", user=self.nir, role=NIR_ROLE
                )
            return real_purge(case)

        with patch(
            "apps.cases.management.commands.purge_closed_case_data.purge_closed_case_data",
            side_effect=_reopen_first_then_purge,
        ):
            cli_out = _run_purge_cli(dry_run=False)

        assert inc_protected.display_reference in cli_out
        assert "Protegido" in cli_out
        assert "1 purgados, 1 protegidos" in cli_out
        inc_protected.refresh_from_db()
        other_canon.refresh_from_db()
        assert inc_protected.documents.count() == 1
        assert other_canon.clinical_data_disposed_at is not None

    def test_r43_cli_deduplicates_group_in_dry_run_and_real_execution(self) -> None:
        canonical = _make_cleaned_via_ack(self.nir, self.doctor, self.scheduler, minimize=False)
        inc1, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="dedup1")
        inc2, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="dedup2")

        dry_out = _run_purge_cli(dry_run=True)
        assert dry_out.count(canonical.display_reference) == 1
        assert "1 seriam purgados" in dry_out

        real_out = _run_purge_cli(dry_run=False)
        assert real_out.count(canonical.display_reference) == 1
        assert "1 purgados" in real_out

        # Reexecução é no-op completo
        second_out = _run_purge_cli(dry_run=False)
        assert "0 purgados" in second_out
        assert canonical.display_reference not in second_out
        assert inc1.display_reference not in second_out
        assert inc2.display_reference not in second_out

    def test_r44_physical_failure_per_case_and_durable_atomic(self) -> None:
        canonical = _make_cleaned_via_ack(self.nir, self.doctor, self.scheduler, minimize=False)
        inc1, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="fail1")

        # Durable atomic preservado
        with pytest.raises(RuntimeError, match="durable atomic block cannot be nested"):
            with transaction.atomic():
                purge_closed_case_data(inc1)

        with patch.object(default_storage, "delete", side_effect=OSError("disk error")):
            out = io.StringIO()
            with pytest.raises(CommandError):
                call_command("purge_closed_case_data", stdout=out)
            output = out.getvalue()
            assert "INCOMPLETA" in output

        canonical.refresh_from_db()
        inc1.refresh_from_db()
        assert canonical.clinical_data_disposed_at is not None
        assert inc1.clinical_data_disposed_at is not None
        for member in (canonical, inc1):
            assert (
                member.events.filter(event_type=CaseEventType.CASE_RETAINED_DATA_PURGED).count()
                == 1
            )
            outcome = member.events.get(event_type=CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME)
            assert outcome.payload["files_failed"] == 2
            assert outcome.payload["files_deleted"] == 0


# ── R5.1: SQL trace PK-ordered + Drift injection ─────────────────────────────


class TestGroupLockSqlTraceAndDriftR51(_Base3BTransactionTestCase):
    def _assert_pk_ordered_for_update(self, queries: list[dict[str, str]]) -> None:
        for_update_sqls = [
            q["sql"] for q in queries if "FOR UPDATE" in q["sql"] and "cases_case" in q["sql"]
        ]
        assert for_update_sqls, "esperada query SELECT ... FOR UPDATE em cases_case"
        assert any(
            'ORDER BY "cases_case"."case_id" ASC FOR UPDATE' in sql
            or "ORDER BY" in sql
            and "case_id" in sql
            for sql in for_update_sqls
        ), f"SQL FOR UPDATE sem ORDER BY case_id: {for_update_sqls}"

    def test_sql_trace_pk_ordered_on_all_three_closure_entrypoints(self) -> None:
        # 1. acknowledge_case_receipt
        c1 = _make_scheduler_requested(self.nir, self.doctor)
        _link_scheduling_repeat_incoming(c1, self.nir, suffix="sql1")
        confirm_case_scheduling(
            c1,
            unit=SchedulingUnit.UNIT_1,
            scheduled_datetime=timezone.now() + timedelta(days=2),
            scheduled_location="HGRS",
            user=self.scheduler,
            role=SCHEDULER_ROLE,
        )
        with CaptureQueriesContext(connections["default"]) as ctx1:
            acknowledge_case_receipt(c1, user=self.nir, role=NIR_ROLE)
        self._assert_pk_ordered_for_update(ctx1.captured_queries)

        # 2. administratively_close_case
        c2 = _make_scheduler_requested(self.nir, self.doctor)
        _link_scheduling_repeat_incoming(c2, self.nir, suffix="sql2")
        with CaptureQueriesContext(connections["default"]) as ctx2:
            administratively_close_case(
                case=c2,
                user=self.manager,
                active_role=MANAGER_ROLE,
                reason_code="other",
                reason_text="sql trace",
            )
        self._assert_pk_ordered_for_update(ctx2.captured_queries)

        # 3. purge_closed_case_data
        c3 = _make_cleaned_via_ack(self.nir, self.doctor, self.scheduler, minimize=False)
        inc3, _ = _link_scheduling_repeat_incoming(c3, self.nir, suffix="sql3")
        with CaptureQueriesContext(connections["default"]) as ctx3:
            purge_closed_case_data(inc3)
        self._assert_pk_ordered_for_update(ctx3.captured_queries)

    def test_drift_injection_retries_and_fails_after_3_with_zero_partial_writes(self) -> None:
        canonical = _make_cleaned_via_ack(self.nir, self.doctor, self.scheduler, minimize=False)
        inc1, _ = _link_scheduling_repeat_incoming(canonical, self.nir, suffix="drift1")

        # Deriva persistente nas 3 tentativas
        real_discover = closure_module._discover_scheduling_group
        calls = {"n": 0}

        def _always_drifting(case_id: uuid.UUID) -> tuple[uuid.UUID, set[uuid.UUID]]:
            calls["n"] += 1
            canon, members = real_discover(case_id)
            if calls["n"] % 2 == 0:  # pós-lock revalidation
                return canon, members | {uuid.uuid4()}
            return canon, members

        with patch.object(closure_module, "_discover_scheduling_group", _always_drifting):
            with pytest.raises(closure_module.ClosureGroupDriftError):
                purge_closed_case_data(inc1)

        assert calls["n"] == 6  # 3 tentativas × (descoberta + revalidação)
        canonical.refresh_from_db()
        inc1.refresh_from_db()
        assert canonical.clinical_data_disposed_at is None
        assert inc1.clinical_data_disposed_at is None
        assert canonical.documents.count() == 1
        assert inc1.documents.count() == 1

        # Deriva transitória (1ª tentativa deriva, 2ª sucede)
        calls["n"] = 0

        def _transient_drift(case_id: uuid.UUID) -> tuple[uuid.UUID, set[uuid.UUID]]:
            calls["n"] += 1
            canon, members = real_discover(case_id)
            if calls["n"] == 2:
                return canon, members | {uuid.uuid4()}
            return canon, members

        with patch.object(closure_module, "_discover_scheduling_group", _transient_drift):
            result = purge_closed_case_data(inc1)

        assert calls["n"] == 4
        assert result.documents_deleted == 2
        canonical.refresh_from_db()
        assert canonical.clinical_data_disposed_at is not None


# ── R5.2 / R5.3: Corridas reais PostgreSQL com barreiras ─────────────────────


def _run_two_threads(
    fn1: Callable[[], object],
    fn2: Callable[[], object],
    *,
    timeout: float = 15.0,
) -> tuple[list[object | BaseException], list[object | BaseException]]:
    out1: list[object | BaseException] = []
    out2: list[object | BaseException] = []

    def _w1() -> None:
        try:
            out1.append(fn1())
        except BaseException as exc:
            out1.append(exc)
        finally:
            connections.close_all()

    def _w2() -> None:
        try:
            out2.append(fn2())
        except BaseException as exc:
            out2.append(exc)
        finally:
            connections.close_all()

    t1 = threading.Thread(target=_w1)
    t2 = threading.Thread(target=_w2)
    t1.start()
    t2.start()
    t1.join(timeout=timeout)
    t2.join(timeout=timeout)
    assert not t1.is_alive() and not t2.is_alive(), "Deadlock detectado (timeout)"
    return out1, out2


class TestPgConcurrencyR52R53(_Base3BTransactionTestCase):
    @override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
    def test_r52_scheduling_repeat_vs_confirm_scheduling(self) -> None:
        prior = _make_scheduler_requested(self.nir, self.doctor, name=PATIENT_NAME, record="90001")
        incoming = Case.objects.create(created_by=self.nir)
        CaseProcedure.objects.create(case=incoming, procedure_type=ANGIO_TYPE, declared_by_nir=True)
        _seed_clinical_data(incoming, name=PATIENT_NAME, record="90001")
        _drive_to_awaiting_doctor(incoming)

        barrier = threading.Barrier(2, timeout=10.0)

        def _t_confirm() -> None:
            barrier.wait()
            confirm_case_scheduling(
                prior,
                unit=SchedulingUnit.UNIT_1,
                scheduled_datetime=timezone.now() + timedelta(days=4),
                scheduled_location="HGRS",
                user=self.scheduler,
                role=SCHEDULER_ROLE,
            )

        def _t_repeat() -> object:
            barrier.wait()
            return resolve_scheduling_repeat(incoming.case_id)

        out1, out2 = _run_two_threads(_t_confirm, _t_repeat)
        assert not isinstance(out1[0], BaseException), out1[0]
        assert not isinstance(out2[0], BaseException), out2[0]
        prior.refresh_from_db()
        assert prior.status == CaseStatus.FINAL_REPLY_POSTED

    @override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
    def test_r52_scheduling_repeat_vs_deny_scheduling(self) -> None:
        prior = _make_scheduler_requested(self.nir, self.doctor, name=PATIENT_NAME, record="90002")
        incoming = Case.objects.create(created_by=self.nir)
        CaseProcedure.objects.create(case=incoming, procedure_type=ANGIO_TYPE, declared_by_nir=True)
        _seed_clinical_data(incoming, name=PATIENT_NAME, record="90002")
        _drive_to_awaiting_doctor(incoming)

        barrier = threading.Barrier(2, timeout=10.0)

        def _t_deny() -> None:
            barrier.wait()
            deny_case_scheduling(prior, reason="sem vaga", user=self.scheduler, role=SCHEDULER_ROLE)

        def _t_repeat() -> object:
            barrier.wait()
            return resolve_scheduling_repeat(incoming.case_id)

        out1, out2 = _run_two_threads(_t_deny, _t_repeat)
        assert not isinstance(out1[0], BaseException), out1[0]
        assert not isinstance(out2[0], BaseException), out2[0]
        prior.refresh_from_db()
        assert prior.status == CaseStatus.FINAL_REPLY_POSTED

    @override_settings(
        CASE_REAPPEARANCE_ENABLED=True,
        CASE_REAPPEARANCE_WINDOW_DAYS=15,
        CASE_CLOSURE_MINIMIZATION_ENABLED=True,
    )
    def test_r52_admin_close_vs_scheduling_repeat(self) -> None:
        prior = _make_scheduler_requested(self.nir, self.doctor, name=PATIENT_NAME, record="90003")
        _attach_doc(prior, self.nir, "p90003.pdf")
        incoming = Case.objects.create(created_by=self.nir)
        CaseProcedure.objects.create(case=incoming, procedure_type=ANGIO_TYPE, declared_by_nir=True)
        _seed_clinical_data(incoming, name=PATIENT_NAME, record="90003")
        _drive_to_awaiting_doctor(incoming)
        _attach_doc(incoming, self.nir, "i90003.pdf")

        barrier = threading.Barrier(2, timeout=10.0)

        def _t_admin() -> None:
            barrier.wait()
            administratively_close_case(
                case=prior,
                user=self.manager,
                active_role=MANAGER_ROLE,
                reason_code="other",
                reason_text="corrida admin",
            )

        def _t_repeat() -> object:
            barrier.wait()
            return resolve_scheduling_repeat(incoming.case_id)

        out1, out2 = _run_two_threads(_t_admin, _t_repeat)
        assert not isinstance(out1[0], BaseException), out1[0]
        assert not isinstance(out2[0], BaseException), out2[0]
        prior.refresh_from_db()
        incoming.refresh_from_db()
        assert prior.status == CaseStatus.CLEANED
        assert prior.clinical_data_disposed_at is not None
        # Se scheduling_repeat aplicou antes do admin_close, incoming também foi disposto!
        if CaseReappearance.objects.filter(
            incoming_case=incoming,
            resolution_kind=ResolutionKind.SCHEDULING_REPEAT,
            state=ReappearanceState.APPLIED,
        ).exists():
            assert incoming.status == CaseStatus.CLEANED
            assert incoming.clinical_data_disposed_at is not None
            assert incoming.documents.count() == 0

    @override_settings(
        CASE_REAPPEARANCE_ENABLED=True,
        CASE_REAPPEARANCE_WINDOW_DAYS=15,
        CASE_CLOSURE_MINIMIZATION_ENABLED=True,
    )
    def test_r52_acknowledge_vs_scheduling_repeat(self) -> None:
        prior = _make_scheduler_requested(self.nir, self.doctor, name=PATIENT_NAME, record="90004")
        _attach_doc(prior, self.nir, "p90004.pdf")
        confirm_case_scheduling(
            prior,
            unit=SchedulingUnit.UNIT_1,
            scheduled_datetime=timezone.now() + timedelta(days=2),
            scheduled_location="HGRS",
            user=self.scheduler,
            role=SCHEDULER_ROLE,
        )
        incoming = Case.objects.create(created_by=self.nir)
        CaseProcedure.objects.create(case=incoming, procedure_type=ANGIO_TYPE, declared_by_nir=True)
        _seed_clinical_data(incoming, name=PATIENT_NAME, record="90004")
        _drive_to_awaiting_doctor(incoming)

        barrier = threading.Barrier(2, timeout=10.0)

        def _t_ack() -> None:
            barrier.wait()
            acknowledge_case_receipt(prior, user=self.nir, role=NIR_ROLE)

        def _t_repeat() -> object:
            barrier.wait()
            return resolve_scheduling_repeat(incoming.case_id)

        out1, out2 = _run_two_threads(_t_ack, _t_repeat)
        assert not isinstance(out1[0], BaseException), out1[0]
        assert not isinstance(out2[0], BaseException), out2[0]
        prior.refresh_from_db()
        assert prior.status == CaseStatus.CLEANED
        assert prior.clinical_data_disposed_at is not None
        assert not CaseReappearance.objects.filter(
            incoming_case=incoming,
            resolution_kind=ResolutionKind.SCHEDULING_REPEAT,
            state=ReappearanceState.APPLIED,
        ).exists()

    @override_settings(
        CASE_REAPPEARANCE_ENABLED=True,
        CASE_REAPPEARANCE_WINDOW_DAYS=15,
    )
    def test_r52_purge_vs_scheduling_repeat(self) -> None:
        prior = _make_cleaned_via_ack(self.nir, self.doctor, self.scheduler, minimize=False)
        incoming = Case.objects.create(created_by=self.nir)
        CaseProcedure.objects.create(case=incoming, procedure_type=ANGIO_TYPE, declared_by_nir=True)
        _seed_clinical_data(incoming, name=PATIENT_NAME, record=RECORD_NUMBER)
        _drive_to_awaiting_doctor(incoming)

        barrier = threading.Barrier(2, timeout=10.0)

        def _t_purge() -> object:
            barrier.wait()
            return purge_closed_case_data(prior)

        def _t_repeat() -> object:
            barrier.wait()
            return resolve_scheduling_repeat(incoming.case_id)

        out1, out2 = _run_two_threads(_t_purge, _t_repeat)
        assert not isinstance(out1[0], BaseException), out1[0]
        assert not isinstance(out2[0], BaseException), out2[0]
        prior.refresh_from_db()
        assert prior.clinical_data_disposed_at is not None
        assert not CaseReappearance.objects.filter(
            incoming_case=incoming,
            resolution_kind=ResolutionKind.SCHEDULING_REPEAT,
            state=ReappearanceState.APPLIED,
        ).exists()

    def test_r53_purge_vs_open_post_closure_issue_both_interleavings_real_fs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hmd-3b-r53-") as tmp_dir:
            media_dir = Path(tmp_dir)
            fs_storages = {
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": str(media_dir)},
                },
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            }
            with override_settings(STORAGES=fs_storages):
                # Interleaving A: purga descobre grupo CLEANED pré-lock, reabertura ganha
                # o lock antes de purga travar; purga revalida sob lock e protege tudo.
                c_a = _make_cleaned_via_ack(self.nir, self.doctor, self.scheduler, minimize=False)
                inc_a, _ = _link_scheduling_repeat_incoming(c_a, self.nir, suffix="r53a")
                can_file_a = media_dir / _file_name(c_a.documents.get())
                inc_file_a = media_dir / _file_name(inc_a.documents.get())
                assert can_file_a.exists() and inc_file_a.exists()

                discovered_a = threading.Barrier(2, timeout=10.0)
                reopen_committed_a = threading.Event()
                real_discover = closure_module._discover_scheduling_group
                real_lock = closure_module._lock_closure_group
                purge_tid_a: list[int] = []
                discover_calls_a = {"n": 0}

                def _discover_hook_a(case_id: uuid.UUID) -> tuple[uuid.UUID, set[uuid.UUID]]:
                    res = real_discover(case_id)
                    if purge_tid_a and threading.get_ident() == purge_tid_a[0]:
                        discover_calls_a["n"] += 1
                        if discover_calls_a["n"] == 1:
                            discovered_a.wait()
                    return res

                def _lock_hook_a(member_ids: object) -> dict[uuid.UUID, Case]:
                    if purge_tid_a and threading.get_ident() == purge_tid_a[0]:
                        assert reopen_committed_a.wait(timeout=10.0), "timeout esperando reabertura"
                    return real_lock(member_ids)  # type: ignore[arg-type]

                def _t_purge_a() -> object:
                    purge_tid_a.append(threading.get_ident())
                    return purge_closed_case_data(inc_a)

                def _t_reopen_a() -> object:
                    discovered_a.wait()
                    res = open_post_closure_issue(
                        c_a, reason="reabertura vence", user=self.nir, role=NIR_ROLE
                    )
                    reopen_committed_a.set()
                    return res

                with (
                    patch.object(closure_module, "_discover_scheduling_group", _discover_hook_a),
                    patch.object(closure_module, "_lock_closure_group", _lock_hook_a),
                ):
                    out_r, out_p = _run_two_threads(_t_reopen_a, _t_purge_a)
                assert not isinstance(out_r[0], BaseException), out_r[0]
                assert isinstance(out_p[0], PurgeResult), out_p[0]
                assert out_p[0].protected is True
                assert can_file_a.exists() and inc_file_a.exists()

                # Interleaving B: purga segura lock do grupo no PG enquanto
                # open_post_closure_issue bloqueia em SELECT ... FOR UPDATE; purga commita
                # clinical_data_disposed_at e open_post_closure_issue falha com ValueError.
                c_b = _make_cleaned_via_ack(self.nir, self.doctor, self.scheduler, minimize=False)
                inc_b, _ = _link_scheduling_repeat_incoming(c_b, self.nir, suffix="r53b")
                can_file_b = media_dir / _file_name(c_b.documents.get())
                inc_file_b = media_dir / _file_name(inc_b.documents.get())
                assert can_file_b.exists() and inc_file_b.exists()

                lock_held_b = threading.Barrier(2, timeout=10.0)
                purge_tid_b: list[int] = []

                def _lock_hook_b(member_ids: object) -> dict[uuid.UUID, Case]:
                    locked = real_lock(member_ids)  # type: ignore[arg-type]
                    if purge_tid_b and threading.get_ident() == purge_tid_b[0]:
                        lock_held_b.wait()
                    return locked

                def _t_purge_b() -> object:
                    purge_tid_b.append(threading.get_ident())
                    return purge_closed_case_data(inc_b)

                def _t_reopen_b() -> object:
                    lock_held_b.wait()
                    return open_post_closure_issue(
                        c_b, reason="reabertura perde", user=self.nir, role=NIR_ROLE
                    )

                with patch.object(closure_module, "_lock_closure_group", _lock_hook_b):
                    out_pb, out_rb = _run_two_threads(_t_purge_b, _t_reopen_b)
                assert isinstance(out_pb[0], PurgeResult), out_pb[0]
                assert isinstance(out_rb[0], ValueError), out_rb[0]
                c_b.refresh_from_db()
                assert c_b.clinical_data_disposed_at is not None
                assert not can_file_b.exists() and not inc_file_b.exists()

    def test_r53_purge_vs_open_post_closure_issue_free_running_real_fs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hmd-3b-r53-free-") as tmp_dir:
            media_dir = Path(tmp_dir)
            fs_storages = {
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": str(media_dir)},
                },
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            }
            with override_settings(STORAGES=fs_storages):
                c = _make_cleaned_via_ack(self.nir, self.doctor, self.scheduler, minimize=False)
                inc, _ = _link_scheduling_repeat_incoming(c, self.nir, suffix="r53free")
                can_file = media_dir / _file_name(c.documents.get())
                inc_file = media_dir / _file_name(inc.documents.get())
                assert can_file.exists() and inc_file.exists()

                barrier = threading.Barrier(2, timeout=10.0)

                def _t_purge() -> object:
                    barrier.wait()
                    return purge_closed_case_data(inc)

                def _t_reopen() -> object:
                    barrier.wait()
                    return open_post_closure_issue(
                        c, reason="corrida livre", user=self.nir, role=NIR_ROLE
                    )

                out_p, out_r = _run_two_threads(_t_purge, _t_reopen)
                assert isinstance(out_p[0], PurgeResult), out_p[0]
                c.refresh_from_db()
                if out_p[0].protected:
                    # Reabertura venceu
                    assert not isinstance(out_r[0], BaseException), out_r[0]
                    assert c.status == CaseStatus.AWAITING_SCHEDULING
                    assert can_file.exists() and inc_file.exists()
                else:
                    # Purga venceu
                    assert isinstance(out_r[0], ValueError), out_r[0]
                    assert c.clinical_data_disposed_at is not None
                    assert not can_file.exists() and not inc_file.exists()
