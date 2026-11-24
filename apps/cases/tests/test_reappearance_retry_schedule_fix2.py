"""Fix-2 P1 (R4/D6): falha de reagendamento no retry vencido.

Regressão: um retry PENDING com lease viva no prior, agenda durável da
tentativa vigente e retry vencido (revisão/tentativa/next_at válidos) NÃO
pode propagar a falha de ``q_schedule`` nem reverter a transação — deve
permanecer PENDING com desfecho observável ``schedule_failed`` + suporte
(``next_attempt_at`` com backoff do attempt vigente), sem Schedule falso,
sem fechar casos, sem evento de aplicação e com orçamento consistente.
Cobre RuntimeError (broker) e erro real de banco (OperationalError) com
recuperação transacional válida (savepoint), além do caminho inicial.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db.utils import OperationalError
from django.test import override_settings
from django.utils import timezone
from django_q.models import Schedule

from apps.accounts.models import User
from apps.cases import reappearance as reappearance_module
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock
from apps.cases.models import (
    Case,
    CaseEvent,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    ReappearanceState,
)

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)

SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
_PAIR_NUMBER = "fix2-8817"


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


def _identity(case: Case, *, number: str) -> None:
    case.patient_name = "Maria da Silva"
    case.agency_record_number = number
    case.patient_cns = ""
    case.origin_unit = ""
    case.patient_age = None
    case.save(
        update_fields=[
            "patient_name",
            "agency_record_number",
            "patient_cns",
            "origin_unit",
            "patient_age",
        ]
    )


def _make_pending_with_live_lease(nir_user: User) -> CaseReappearance:
    """Par forte + lease viva → PENDING com Schedule durável da tentativa 1."""
    prior = Case.objects.create(created_by=nir_user)
    _declared(prior, ANGIO_TYPE)
    _identity(prior, number=_PAIR_NUMBER)
    _awaiting(prior)
    Case.objects.filter(pk=prior.pk).update(created_at=timezone.now() - timedelta(days=5))
    prior.refresh_from_db()
    incoming = Case.objects.create(created_by=nir_user)
    _declared(incoming, ANGIO_TYPE)
    _identity(incoming, number=_PAIR_NUMBER)
    _awaiting(incoming)
    doctor = User.objects.create_user(username="med-fix2-attempt", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.PENDING
    assert link.attempt_count == 1
    assert Schedule.objects.filter(
        name=reappearance_module.retry_schedule_name(link.pk, 1, link.revision_digest),
        func=reappearance_module.RETRY_FUNC,
    ).exists()
    # Retry vencido com revisão/tentativa vigentes.
    CaseReappearance.objects.filter(pk=link.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    link.refresh_from_db()
    return link


def _assert_honest_schedule_failure(link: CaseReappearance, *, failed_attempt: int) -> None:
    link.refresh_from_db()
    prior = Case.objects.get(pk=link.prior_case_id)
    incoming = Case.objects.get(pk=link.incoming_case_id)
    assert link.state == ReappearanceState.PENDING
    assert link.prior_case_id == prior.pk
    assert link.last_reason_code == reappearance_module.REASON_SCHEDULE_FAILED
    assert link.next_attempt_at is not None
    assert link.attempt_count == failed_attempt
    assert link.applied_at is None
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert prior.lock_token is not None
    assert not incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()
    # Sem Schedule falso para a tentativa que falhou; agenda anterior intacta.
    assert not Schedule.objects.filter(
        name=reappearance_module.retry_schedule_name(link.pk, failed_attempt, link.revision_digest),
    ).exists()
    assert Schedule.objects.filter(
        name=reappearance_module.retry_schedule_name(
            link.pk, failed_attempt - 1, link.revision_digest
        ),
        func=reappearance_module.RETRY_FUNC,
    ).exists()


@NEW_MODE
@pytest.mark.django_db
def test_due_retry_schedule_runtime_failure_stays_pending(
    nir_user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """P1: broker indisponível no reagendamento vencido → PENDING schedule_failed."""
    link = _make_pending_with_live_lease(nir_user)
    revision = link.revision_digest
    attempt = link.attempt_count

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("broker indisponível")

    monkeypatch.setattr("django_q.tasks.schedule", _boom)
    with caplog.at_level("WARNING"):
        reappearance_module.handle_reappearance_retry(str(link.pk), revision, attempt)
    _assert_honest_schedule_failure(link, failed_attempt=attempt + 1)
    assert any("reappearance" in record.getMessage().lower() for record in caplog.records)


@NEW_MODE
@pytest.mark.django_db
def test_due_retry_schedule_db_failure_recovers_transactionally(
    nir_user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """P1: erro real de banco no reagendamento → PENDING schedule_failed válido."""
    link = _make_pending_with_live_lease(nir_user)
    revision = link.revision_digest
    attempt = link.attempt_count

    def _db_down(*args: object, **kwargs: object) -> object:
        from django.db import connection as _connection

        with _connection.cursor() as _cursor:
            _cursor.execute("SELECT * FROM pg_nonexistent_table_fix2")
        raise OperationalError("connection failed")  # pragma: no cover

    monkeypatch.setattr("django_q.tasks.schedule", _db_down)
    with caplog.at_level("WARNING"):
        reappearance_module.handle_reappearance_retry(str(link.pk), revision, attempt)
    _assert_honest_schedule_failure(link, failed_attempt=attempt + 1)
    assert any("reappearance" in record.getMessage().lower() for record in caplog.records)
    # Entrega duplicada/atrasada com tentativa obsoleta segue no-op honesto.
    events_before = CaseEvent.objects.count()
    reappearance_module.handle_reappearance_retry(str(link.pk), revision, attempt)
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.last_reason_code == reappearance_module.REASON_SCHEDULE_FAILED
    assert CaseEvent.objects.count() == events_before


@NEW_MODE
@pytest.mark.django_db
def test_initial_defer_schedule_db_failure_recovers_transactionally(
    nir_user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Ambos os caminhos partilham o helper: deferral inicial também sobrevive a DB."""

    def _db_down(*args: object, **kwargs: object) -> object:
        from django.db import connection as _connection

        with _connection.cursor() as _cursor:
            _cursor.execute("SELECT * FROM pg_nonexistent_table_fix2")
        raise OperationalError("connection failed")  # pragma: no cover

    monkeypatch.setattr("django_q.tasks.schedule", _db_down)
    prior = Case.objects.create(created_by=nir_user)
    _declared(prior, ANGIO_TYPE)
    _identity(prior, number=_PAIR_NUMBER)
    _awaiting(prior)
    Case.objects.filter(pk=prior.pk).update(created_at=timezone.now() - timedelta(days=5))
    prior.refresh_from_db()
    incoming = Case.objects.create(created_by=nir_user)
    _declared(incoming, ANGIO_TYPE)
    _identity(incoming, number=_PAIR_NUMBER)
    _awaiting(incoming)
    doctor = User.objects.create_user(username="med-fix2-inicial", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    with caplog.at_level("WARNING"):
        link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.PENDING
    assert link.last_reason_code == reappearance_module.REASON_SCHEDULE_FAILED
    assert link.next_attempt_at is not None
    assert Schedule.objects.count() == 0
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()
