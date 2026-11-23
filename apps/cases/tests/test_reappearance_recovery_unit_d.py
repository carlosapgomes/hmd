"""Recuperação observável (S2 unidade D — R4.10/R4.11, AUD-03).

Eventos do ciclo (falha de agenda, esgotamento, reenqueue) SOMENTE no
incoming, aviso atual nos detalhes NIR/gerencial desse incoming (nunca no
prior), distinção agenda real × sem agenda × terminal, regularização após
agenda/aplicação sem apagar histórico, e o comando técnico
``reenqueue_pending_reappearances`` (simulação zero-escritas, --apply só
agenda, idempotente, recusas sem reset, exits 0/1/2).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django_q.models import Schedule

from apps.accounts.models import User
from apps.cases import reappearance as reappearance_module
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock, release_case_lock
from apps.cases.models import (
    Case,
    CaseEvent,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    ReappearanceState,
)

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
FLAG_OFF = override_settings(CASE_REAPPEARANCE_ENABLED=False)
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
_NUMBER = "unit-d-5521"

# Literais (não o enum) para o RED ser comportamental: os tipos novos ainda
# não existem no enum — a ausência do comportamento é o que deve falhar.
SCHEDULE_FAILED = "CASE_REAPPEARANCE_SCHEDULE_FAILED"
REENQUEUED = "CASE_REAPPEARANCE_REENQUEUED"
EXHAUSTED = CaseEventType.CASE_REAPPEARANCE_RETRY_EXHAUSTED

SCHEDULED_TEXT = "Recuperação de reapresentação agendada"
FAILED_TEXT = "Falha ao agendar a recuperação de reapresentação"
TERMINAL_TEXT = "Recuperação de reapresentação esgotada"
WARNING_TEXTS = (SCHEDULED_TEXT, FAILED_TEXT, TERMINAL_TEXT)


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


def _identity(case: Case, *, number: str = _NUMBER) -> None:
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


def _make_pair(owner: User) -> tuple[Case, Case]:
    prior = Case.objects.create(created_by=owner)
    _declared(prior, ANGIO_TYPE)
    _identity(prior)
    _awaiting(prior)
    Case.objects.filter(pk=prior.pk).update(created_at=timezone.now() - timedelta(days=5))
    prior.refresh_from_db()
    incoming = Case.objects.create(created_by=owner)
    _declared(incoming, ANGIO_TYPE)
    _identity(incoming)
    _awaiting(incoming)
    return prior, incoming


def _make_pending(owner: User, *, username: str = "med-unit-d") -> CaseReappearance:
    prior, incoming = _make_pair(owner)
    doctor = User.objects.create_user(username=username, password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.PENDING
    assert link.attempt_count == 1
    return link


def _make_schedule_failed(
    owner: User, monkeypatch: pytest.MonkeyPatch, *, username: str = "med-unit-d-fail"
) -> CaseReappearance:
    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("broker indisponível")

    monkeypatch.setattr("django_q.tasks.schedule", _boom)
    prior, incoming = _make_pair(owner)
    doctor = User.objects.create_user(username=username, password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.last_reason_code == reappearance_module.REASON_SCHEDULE_FAILED
    assert Schedule.objects.count() == 0
    return link


def _login(client: Client, user: User, role: str) -> None:
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _nir_get(client: Client, case: Case) -> str:
    response = client.get(reverse("intake:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    return response.content.decode()


def _manager_get(client: Client, case: Case) -> str:
    response = client.get(reverse("dashboard:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    return response.content.decode()


def _run_command(*args: object) -> tuple[int, str]:
    out = StringIO()
    try:
        call_command("reenqueue_pending_reappearances", *args, stdout=out)
    except SystemExit as exc:
        return int(exc.code or 0), out.getvalue()
    except CommandError as exc:
        # ``call_command`` embrulha o erro do parser (exit real 2 no
        # subprocesso — ver logs unit-D-cli-invalid.log) em CommandError.
        if "invalid UUID" in str(exc):
            return 2, f"{out.getvalue()}\n{exc}"
        return 1, f"{out.getvalue()}\n{exc}"
    return 0, out.getvalue()


# ── R4.10: eventos do ciclo somente no incoming ────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_schedule_failure_event_only_on_incoming_and_deduped(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falha de agenda: 1 evento no incoming, zero no prior, sem duplicar."""
    link = _make_schedule_failed(nir_user, monkeypatch)
    incoming = Case.objects.get(pk=link.incoming_case_id)
    prior = Case.objects.get(pk=link.prior_case_id)
    events = incoming.events.filter(event_type=SCHEDULE_FAILED)
    assert events.count() == 1
    assert not prior.events.filter(event_type=SCHEDULE_FAILED).exists()
    payload = events.first()
    assert payload is not None
    assert payload.payload["reappearance_id"] == str(link.pk)
    assert payload.payload["reason_code"] == reappearance_module.REASON_SCHEDULE_FAILED
    assert payload.actor is None
    # Reentrada com a mesma falha consecutiva não duplica o evento.
    reappearance_module.resolve_medical_replacement(incoming)
    assert incoming.events.filter(event_type=SCHEDULE_FAILED).count() == 1
    assert not prior.events.filter(event_type=SCHEDULE_FAILED).exists()


@NEW_MODE
@pytest.mark.django_db
def test_schedule_failure_event_payload_is_sanitized(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Payload sanitizado: sem nome, nº de ocorrência ou CNS em claro."""
    link = _make_schedule_failed(nir_user, monkeypatch)
    incoming = Case.objects.get(pk=link.incoming_case_id)
    event = incoming.events.filter(event_type=SCHEDULE_FAILED).first()
    assert event is not None
    blob = str(event.payload)
    assert "Maria da Silva" not in blob
    assert _NUMBER not in blob
    assert set(event.payload) <= {"reappearance_id", "attempts", "reason_code", "next_attempt_at"}


@NEW_MODE
@pytest.mark.django_db
def test_exhausted_event_only_on_incoming_and_deduped(nir_user: User) -> None:
    """Esgotamento: evento único no incoming, nunca no prior."""
    link = _make_pending(nir_user)
    CaseReappearance.objects.filter(pk=link.pk).update(
        attempt_count=reappearance_module.RETRY_MAX_ATTEMPTS
    )
    link.refresh_from_db()
    CaseReappearance.objects.filter(pk=link.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    reappearance_module.handle_reappearance_retry(
        str(link.pk), link.revision_digest, link.attempt_count
    )
    incoming = Case.objects.get(pk=link.incoming_case_id)
    prior = Case.objects.get(pk=link.prior_case_id)
    assert incoming.events.filter(event_type=EXHAUSTED).count() == 1
    assert not prior.events.filter(event_type=EXHAUSTED).exists()
    # Job duplicado tardio não duplica o evento de esgotamento.
    reappearance_module.handle_reappearance_retry(
        str(link.pk), link.revision_digest, link.attempt_count
    )
    assert incoming.events.filter(event_type=EXHAUSTED).count() == 1


@NEW_MODE
@pytest.mark.django_db
def test_audit_failure_marks_no_false_success(
    nir_user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Se a auditoria da falha não persiste, nada alega agenda/sucesso."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("broker indisponível")

    monkeypatch.setattr("django_q.tasks.schedule", _boom)
    original_create = CaseEvent.objects.create

    def _create_failing_event(*args: object, **kwargs: object) -> object:
        if kwargs.get("event_type") == SCHEDULE_FAILED:
            raise RuntimeError("auditoria indisponível")
        return original_create(*args, **kwargs)

    monkeypatch.setattr(CaseEvent.objects, "create", _create_failing_event)
    prior, incoming = _make_pair(nir_user)
    doctor = User.objects.create_user(username="med-unit-d-audit", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    with caplog.at_level("WARNING"):
        link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.PENDING
    assert link.last_reason_code == reappearance_module.REASON_SCHEDULE_FAILED
    assert Schedule.objects.count() == 0
    assert not incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
    assert any("reappearance" in record.getMessage().lower() for record in caplog.records)


# ── R4.10: aviso atual nos detalhes autorizados ────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_nir_incoming_scheduled_warning_and_get_is_readonly(
    client: Client, nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    """Lease viva + agenda real: aviso de agendada no NIR, GET sem escrita."""
    link = _make_pending(nir_user)
    incoming = Case.objects.get(pk=link.incoming_case_id)
    _login(client, user_factory("nir-d-sched", "nir"), "nir")
    events_before = CaseEvent.objects.count()
    attempt_before = link.attempt_count
    content = _nir_get(client, incoming)
    assert SCHEDULED_TEXT in content
    assert FAILED_TEXT not in content
    assert TERMINAL_TEXT not in content
    assert "{#" not in content  # comentário de template nunca vaza no HTML
    assert CaseEvent.objects.count() == events_before
    link.refresh_from_db()
    assert link.attempt_count == attempt_before


@NEW_MODE
@pytest.mark.django_db
def test_nir_incoming_schedule_failed_warning_without_schedule(
    client: Client, nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    """schedule_failed sem agenda: aviso de falha + suporte, sem alegar agenda."""
    link = _make_pending(nir_user)
    Schedule.objects.all().delete()
    CaseReappearance.objects.filter(pk=link.pk).update(
        last_reason_code=reappearance_module.REASON_SCHEDULE_FAILED
    )
    incoming = Case.objects.get(pk=link.incoming_case_id)
    _login(client, user_factory("nir-d-failed", "nir"), "nir")
    content = _nir_get(client, incoming)
    assert FAILED_TEXT in content
    assert SCHEDULED_TEXT not in content
    assert "suporte" in content.lower()


@NEW_MODE
@pytest.mark.django_db
def test_manager_incoming_terminal_warning(
    client: Client, nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    """Terminal no gerencial do incoming: aviso de esgotamento + suporte."""
    link = _make_pending(nir_user)
    CaseReappearance.objects.filter(pk=link.pk).update(
        last_reason_code=reappearance_module.REASON_RETRY_EXHAUSTED
    )
    incoming = Case.objects.get(pk=link.incoming_case_id)
    _login(client, user_factory("gestor-d-terminal", "manager"), "manager")
    content = _manager_get(client, incoming)
    assert TERMINAL_TEXT in content
    assert SCHEDULED_TEXT not in content
    assert "suporte" in content.lower()


@NEW_MODE
@pytest.mark.django_db
def test_prior_never_shows_recovery_warning(
    client: Client, nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    """Nunca no prior: NIR e gerencial do prior sem nenhum aviso de recuperação."""
    link = _make_pending(nir_user)
    prior = Case.objects.get(pk=link.prior_case_id)
    _login(client, user_factory("nir-d-prior", "nir"), "nir")
    assert all(text not in _nir_get(client, prior) for text in WARNING_TEXTS)
    _login(client, user_factory("gestor-d-prior", "manager"), "manager")
    assert all(text not in _manager_get(client, prior) for text in WARNING_TEXTS)


@NEW_MODE
@pytest.mark.django_db
def test_applied_regularizes_warning_and_keeps_two_events(
    client: Client, nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    """Após aplicação: sem aviso atual; os dois eventos correlacionados ficam."""
    prior, incoming = _make_pair(nir_user)
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.APPLIED
    _login(client, user_factory("nir-d-applied", "nir"), "nir")
    content = _nir_get(client, incoming)
    assert all(text not in content for text in WARNING_TEXTS)
    assert incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).count() == 1
    assert prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).count() == 1


# ── R4.11: comando técnico ─────────────────────────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_simulation_writes_nothing(nir_user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulação (default): zero escritas, saída SIMULAÇÃO, exit 0."""
    link = _make_schedule_failed(nir_user, monkeypatch)
    schedules_before = Schedule.objects.count()
    events_before = CaseEvent.objects.count()
    link.refresh_from_db()
    snapshot = (link.attempt_count, link.last_reason_code, link.next_attempt_at)
    code, output = _run_command(str(link.pk))
    assert code == 0
    assert "SIMULAÇÃO" in output
    assert Schedule.objects.count() == schedules_before
    assert CaseEvent.objects.count() == events_before
    link.refresh_from_db()
    assert (link.attempt_count, link.last_reason_code, link.next_attempt_at) == snapshot


@NEW_MODE
@pytest.mark.django_db
def test_apply_schedules_once_and_repeat_is_noop(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--apply agenda o efeito (sem resolver/tomar lease); repetir é NO-OP."""
    link = _make_schedule_failed(nir_user, monkeypatch)
    monkeypatch.undo()  # broker volta: o --apply deve agendar de fato
    attempt_before = link.attempt_count
    code, output = _run_command(str(link.pk), "--apply")
    assert code == 0
    assert "AGENDADO" in output
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.attempt_count == attempt_before + 1
    assert link.last_reason_code == reappearance_module.REASON_LEASE_BUSY
    schedules = list(Schedule.objects.filter(func=reappearance_module.RETRY_FUNC))
    assert len(schedules) == 1
    assert schedules[0].cluster == reappearance_module.RETRY_CLUSTER
    assert link.revision_digest in (schedules[0].args or "")
    incoming = Case.objects.get(pk=link.incoming_case_id)
    prior = Case.objects.get(pk=link.prior_case_id)
    assert incoming.events.filter(event_type=REENQUEUED).count() == 1
    assert not prior.events.filter(event_type=REENQUEUED).exists()
    assert not incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert prior.lock_token is not None
    # Repetir com agenda válida: NO-OP, sem nova tentativa/evento/agenda.
    schedules_before = Schedule.objects.count()
    events_before = CaseEvent.objects.count()
    code, output = _run_command(str(link.pk), "--apply")
    assert code == 0
    assert "NO-OP" in output
    assert Schedule.objects.count() == schedules_before
    assert CaseEvent.objects.count() == events_before
    link.refresh_from_db()
    assert link.attempt_count == attempt_before + 1


@NEW_MODE
@pytest.mark.django_db
def test_simulation_of_valid_schedule_reports_noop_without_writing(nir_user: User) -> None:
    """Simulação com agenda válida existente: SIMULAÇÃO/NO-OP, sem escrita."""
    link = _make_pending(nir_user)
    schedules_before = Schedule.objects.count()
    code, output = _run_command(str(link.pk))
    assert code == 0
    assert "SIMULAÇÃO" in output
    assert "NO-OP" in output
    assert Schedule.objects.count() == schedules_before


@NEW_MODE
@pytest.mark.django_db
def test_apply_refusal_flag_off_without_writing(nir_user: User) -> None:
    """Flag off: --apply recusado (exit 1), simulação segue read-only (exit 0)."""
    link = _make_pending(nir_user)
    Schedule.objects.all().delete()
    CaseReappearance.objects.filter(pk=link.pk).update(
        last_reason_code=reappearance_module.REASON_SCHEDULE_FAILED
    )
    with FLAG_OFF:
        code, output = _run_command(str(link.pk), "--apply")
        assert code == 1
        assert "RECUSADO" in output
        code, output = _run_command(str(link.pk))
        assert code == 0
        assert "SIMULAÇÃO" in output
    assert Schedule.objects.count() == 0
    link.refresh_from_db()
    assert link.attempt_count == 1


@NEW_MODE
@pytest.mark.django_db
def test_apply_refusal_applied_without_writing(nir_user: User) -> None:
    """Vínculo aplicado: --apply recusado sem reativar, simulação exit 0."""
    prior, incoming = _make_pair(nir_user)
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.APPLIED
    events_before = CaseEvent.objects.count()
    code, output = _run_command(str(link.pk), "--apply")
    assert code == 1
    assert "RECUSADO" in output
    code, output = _run_command(str(link.pk))
    assert code == 0
    assert "SIMULAÇÃO" in output
    asserts = CaseEvent.objects.count()
    assert asserts == events_before
    assert Schedule.objects.count() == 0


@NEW_MODE
@pytest.mark.django_db
def test_apply_refusal_terminal_without_reset(nir_user: User) -> None:
    """Terminal (retry_exhausted): recusa sem reset de orçamento/revisão."""
    link = _make_pending(nir_user)
    CaseReappearance.objects.filter(pk=link.pk).update(
        last_reason_code=reappearance_module.REASON_RETRY_EXHAUSTED,
        attempt_count=3,
    )
    link.refresh_from_db()
    first = link.first_attempt_at
    code, output = _run_command(str(link.pk), "--apply")
    assert code == 1
    assert "RECUSADO" in output
    link.refresh_from_db()
    assert link.attempt_count == 3
    assert link.first_attempt_at == first
    assert link.last_reason_code == reappearance_module.REASON_RETRY_EXHAUSTED
    # Simulação de inelegibilidade também é só diagnóstico (exit 0).
    code, output = _run_command(str(link.pk))
    assert code == 0
    assert "SIMULAÇÃO" in output


@NEW_MODE
@pytest.mark.django_db
def test_apply_refusal_stale_revision_without_retarget(nir_user: User) -> None:
    """Revisão obsoleta (digest divergente): recusa sem retarget/reset."""
    link = _make_pending(nir_user)
    Schedule.objects.all().delete()
    CaseReappearance.objects.filter(pk=link.pk).update(
        last_reason_code=reappearance_module.REASON_SCHEDULE_FAILED
    )
    incoming = Case.objects.get(pk=link.incoming_case_id)
    # Idade 0 (informada, compatível) muda o digest mantendo o par forte.
    incoming.patient_age = 0
    incoming.save(update_fields=["patient_age"])
    digest_before = link.revision_digest
    code, output = _run_command(str(link.pk), "--apply")
    assert code == 1
    assert "RECUSADO" in output
    link.refresh_from_db()
    assert link.revision_digest == digest_before
    assert link.attempt_count == 1
    assert Schedule.objects.count() == 0


@NEW_MODE
@pytest.mark.django_db
def test_apply_refusal_budget_exhausted(nir_user: User) -> None:
    """Orçamento esgotado (24 tentativas): recusa sem consumir/resetar."""
    link = _make_pending(nir_user)
    Schedule.objects.all().delete()
    CaseReappearance.objects.filter(pk=link.pk).update(
        last_reason_code=reappearance_module.REASON_LEASE_BUSY,
        attempt_count=reappearance_module.RETRY_MAX_ATTEMPTS,
        first_attempt_at=timezone.now() - timedelta(hours=1),
    )
    code, output = _run_command(str(link.pk), "--apply")
    assert code == 1
    assert "RECUSADO" in output
    link.refresh_from_db()
    assert link.attempt_count == reappearance_module.RETRY_MAX_ATTEMPTS
    assert Schedule.objects.count() == 0


@NEW_MODE
@pytest.mark.django_db
def test_missing_link_exits_1(nir_user: User) -> None:
    """Vínculo inexistente: exit 1, sem escrita."""
    events_before = CaseEvent.objects.count()
    code, output = _run_command(str(uuid.uuid4()), "--apply")
    assert code == 1
    assert "RECUSADO" in output
    assert CaseEvent.objects.count() == events_before


@NEW_MODE
@pytest.mark.django_db
def test_invalid_uuid_exits_2() -> None:
    """UUID malformado: uso inválido (exit 2), sem execução."""
    code, _output = _run_command("nao-e-uuid", "--apply")
    assert code == 2


@NEW_MODE
@pytest.mark.django_db
def test_apply_persistence_failure_is_honest(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falha de persistência no --apply: exit 1, sem alegar AGENDADO."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("banco indisponível")

    link = _make_pending(nir_user)
    Schedule.objects.all().delete()
    CaseReappearance.objects.filter(pk=link.pk).update(
        last_reason_code=reappearance_module.REASON_SCHEDULE_FAILED
    )
    monkeypatch.setattr("django_q.tasks.schedule", _boom)
    code, output = _run_command(str(link.pk), "--apply")
    assert code == 1
    assert "AGENDADO" not in output
    link.refresh_from_db()
    assert link.attempt_count == 1
    assert link.last_reason_code == reappearance_module.REASON_SCHEDULE_FAILED
    assert Schedule.objects.count() == 0
    incoming = Case.objects.get(pk=link.incoming_case_id)
    assert not incoming.events.filter(event_type=REENQUEUED).exists()


@NEW_MODE
@pytest.mark.django_db
def test_reenqueue_regularizes_warning_and_preserves_history(
    client: Client,
    nir_user: User,
    monkeypatch: pytest.MonkeyPatch,
    user_factory: Callable[[str, str], User],
) -> None:
    """Ciclo completo: falha visível → reenqueue → aviso agendada, histórico fica."""
    link = _make_schedule_failed(nir_user, monkeypatch)
    monkeypatch.undo()  # broker volta: o --apply deve agendar de fato
    incoming = Case.objects.get(pk=link.incoming_case_id)
    _login(client, user_factory("nir-d-cycle", "nir"), "nir")
    assert FAILED_TEXT in _nir_get(client, incoming)
    code, output = _run_command(str(link.pk), "--apply")
    assert code == 0
    assert "AGENDADO" in output
    content = _nir_get(client, incoming)
    assert SCHEDULED_TEXT in content
    assert FAILED_TEXT not in content
    assert incoming.events.filter(event_type=SCHEDULE_FAILED).count() == 1
    assert incoming.events.filter(event_type=REENQUEUED).count() == 1
    # A trilha legível mostra os dois rótulos do ciclo.
    assert "Falha ao agendar recuperação de reapresentação" in content
    assert "Recuperação de reapresentação reenfileirada" in content


@NEW_MODE
@pytest.mark.django_db
def test_apply_does_not_apply_or_take_lease(nir_user: User) -> None:
    """--apply só agenda: não resolve, não encerra, não toma a lease viva."""
    link = _make_pending(nir_user)
    Schedule.objects.all().delete()
    CaseReappearance.objects.filter(pk=link.pk).update(
        last_reason_code=reappearance_module.REASON_SCHEDULE_FAILED
    )
    prior = Case.objects.get(pk=link.prior_case_id)
    lock_token = prior.lock_token
    code, _output = _run_command(str(link.pk), "--apply")
    assert code == 0
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    prior.refresh_from_db()
    incoming = Case.objects.get(pk=link.incoming_case_id)
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert prior.lock_token == lock_token
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert not incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
    assert lock_token is not None
    release_case_lock(prior, lock_token)  # sanidade: token segue válido
