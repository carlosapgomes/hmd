"""Intercorrência pós-encerramento (slice 002, R1–R3 + backfill + trilha).

Cobre o serviço de domínio ``apps/cases/closure.py`` (elegibilidade
fail-closed + abertura atômica + zeragem em qualquer chegada a ``CLEANED``),
a transição FSM ``reopen_post_closure`` (``CLEANED → AWAITING_SCHEDULING``),
a gravação do marcador ``clinical_data_disposed_at`` e os rótulos/badges dos
eventos novos na trilha.

Padrões reusados de ``test_closure_ack.py``: condução real do pipeline até
``FINAL_REPLY_POSTED`` → ciência do criador → ``CLEANED``; storage em
memória; pino explícito do modo de minimização por teste.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import timedelta
from importlib import import_module

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.closure import (
    get_post_closure_issue_ineligibility_reason,
    is_post_closure_issue_eligible,
    open_post_closure_issue,
)
from apps.cases.event_trail import event_badge_css, event_label
from apps.cases.events import CaseEventType
from apps.cases.models import (
    Case,
    CaseProcedure,
    CaseStatus,
    SchedulingUnit,
)
from apps.cases.procedure_review import event_detail_lines
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.scheduler.services import confirm_case_scheduling

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SCHEDULER_ROLE = "scheduler"
MANAGER_ROLE = "manager"
SYSTEM_ROLE = "system"

ANGIO_TYPE = "art_perif"
RADIO_TYPE = "nefrostomia"

REOPEN_REASON = "retorno dos sintomas — nova avaliação do agendamento solicitada"

# Storage de teste: arquivos apenas em memória (nada de disco/MEDIA_ROOT).
_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.fixture(autouse=True)
def _memory_storage() -> Iterator[None]:
    """Storage default em memória durante cada teste do módulo."""
    with override_settings(STORAGES=_TEST_STORAGES):
        yield


def _drive_to_awaiting_doctor(case: Case) -> None:
    """Dirige o caso pelas transições do pipeline até ``AWAITING_DOCTOR``."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _approve_and_request(case: Case, decided_by: User) -> None:
    """Decisão médica aprovada → fila do agendador (``SCHEDULER_REQUESTED``)."""
    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: ("approved", ""), RADIO_TYPE: ("approved", "")},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED


def _deny_and_reply(case: Case, decided_by: User) -> None:
    """Decisão médica negada + resposta final (sem agendamento confirmado)."""
    from apps.cases.closure import post_doctor_denial_reply

    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: ("denied", "sem indicação"), RADIO_TYPE: ("denied", "risco elevado")},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    post_doctor_denial_reply(case, user=decided_by, role=DOCTOR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED


def _new_case(created_by: User) -> Case:
    """Caso com as rows declaradas dos dois tipos representativos."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in (ANGIO_TYPE, RADIO_TYPE):
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )
    return case


def _confirm(
    case: Case,
    scheduler_user: User,
    *,
    unit: int = SchedulingUnit.UNIT_1,
    location: str = "HGRS — Hemodinâmica",
) -> None:
    """Confirma o agendamento (resposta final ao NIR, ``FINAL_REPLY_POSTED``)."""
    confirm_case_scheduling(
        case,
        unit=unit,
        scheduled_datetime=timezone.now() + timedelta(days=5),
        scheduled_location=location,
        user=scheduler_user,
        role=SCHEDULER_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED


def _close_cycle(case: Case, *, created_by: User, minimize: bool) -> None:
    """Ciência do criador → ``CLEANED`` no modo de disposição indicado."""
    from apps.cases.closure import acknowledge_case_receipt

    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=minimize):
        acknowledge_case_receipt(case, user=created_by, role=NIR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED


def _retained_closed_case(
    *,
    created_by: User,
    decided_by: User,
    scheduler_user: User,
) -> Case:
    """Caso ``CLEANED`` elegível: unidade 1 confirmada, dados retidos, sem issue."""
    case = _new_case(created_by)
    _drive_to_awaiting_doctor(case)
    _approve_and_request(case, decided_by)
    _confirm(case, scheduler_user)
    assert case.scheduled_unit == SchedulingUnit.UNIT_1
    _close_cycle(case, created_by=created_by, minimize=False)
    assert case.clinical_data_disposed_at is None
    return case


def _event_types(case: Case) -> list[str]:
    """Tipos de evento da trilha na ordem de gravação."""
    return [event.event_type for event in case.events.order_by("id")]


def _user_messages(case: Case) -> list[str]:
    """Corpos das mensagens manuais (user) da thread do caso, em ordem."""
    return list(
        case.communication_messages.filter(author__isnull=False)
        .order_by("created_at")
        .values_list("body", flat=True)
    )


# ── R1: elegibilidade — 5 ramos de inelegibilidade + caso elegível ─────────


@pytest.mark.django_db
def test_eligible_closed_case_with_confirmed_unit_1(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R1: ``CLEANED`` + unidade 1 confirmada + dados retidos + sem issue → elegível."""
    scheduler_user = user_factory("scheduler-elegivel", SCHEDULER_ROLE)
    case = _retained_closed_case(
        created_by=nir_user, decided_by=doctor_user, scheduler_user=scheduler_user
    )

    assert is_post_closure_issue_eligible(case) is True
    assert get_post_closure_issue_ineligibility_reason(case) == ""


@pytest.mark.django_db
def test_ineligible_with_active_issue(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R1: issue ativa bloqueia nova abertura com motivo legível."""
    scheduler_user = user_factory("scheduler-issue", SCHEDULER_ROLE)
    case = _retained_closed_case(
        created_by=nir_user, decided_by=doctor_user, scheduler_user=scheduler_user
    )
    open_post_closure_issue(case, reason=REOPEN_REASON, user=nir_user, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)

    assert is_post_closure_issue_eligible(fresh) is False
    assert "intercorrência" in get_post_closure_issue_ineligibility_reason(fresh).lower()


@pytest.mark.django_db
def test_ineligible_when_not_cleaned(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R1: caso fora de ``CLEANED`` é inelegível (motivo: não encerrado)."""
    scheduler_user = user_factory("scheduler-aberto", SCHEDULER_ROLE)
    case = _new_case(nir_user)
    _drive_to_awaiting_doctor(case)
    _approve_and_request(case, doctor_user)
    _confirm(case, scheduler_user)
    assert case.status == CaseStatus.FINAL_REPLY_POSTED

    assert is_post_closure_issue_eligible(case) is False
    reason = get_post_closure_issue_ineligibility_reason(case)
    assert "CLEANED" in reason or "encerrado" in reason.lower()


@pytest.mark.django_db
def test_ineligible_without_confirmed_scheduling(nir_user: User, doctor_user: User) -> None:
    """R1: ``CLEANED`` sem agendamento confirmado (negação médica) é inelegível."""
    case = _new_case(nir_user)
    _drive_to_awaiting_doctor(case)
    _deny_and_reply(case, doctor_user)
    _close_cycle(case, created_by=nir_user, minimize=False)
    assert case.scheduled_unit is None

    assert is_post_closure_issue_eligible(case) is False
    assert "agendamento" in get_post_closure_issue_ineligibility_reason(case).lower()


@pytest.mark.django_db
def test_ineligible_for_unit_2(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R1: agendamento anterior na unidade 2 é inelegível (regra do dono, D2)."""
    scheduler_user = user_factory("scheduler-u2", SCHEDULER_ROLE)
    case = _new_case(nir_user)
    _drive_to_awaiting_doctor(case)
    _approve_and_request(case, doctor_user)
    _confirm(case, scheduler_user, unit=SchedulingUnit.UNIT_2)
    _close_cycle(case, created_by=nir_user, minimize=False)
    assert case.scheduled_unit == SchedulingUnit.UNIT_2

    assert is_post_closure_issue_eligible(case) is False
    reason = get_post_closure_issue_ineligibility_reason(case)
    assert "unidade 1" in reason.lower()


@pytest.mark.django_db
def test_ineligible_when_clinical_data_disposed(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R1: dados dispostos (marcador persistido, D2) são inelegíveis — a
    detecção lê a coluna ``clinical_data_disposed_at``, nunca proxy de campos."""
    scheduler_user = user_factory("scheduler-disposto", SCHEDULER_ROLE)
    case = _new_case(nir_user)
    _drive_to_awaiting_doctor(case)
    _approve_and_request(case, doctor_user)
    _confirm(case, scheduler_user)
    case.summary_text = "Resumo clínico sintético gerado pelo LLM2."
    case.save(update_fields=["summary_text"])
    _close_cycle(case, created_by=nir_user, minimize=True)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.clinical_data_disposed_at is not None

    assert is_post_closure_issue_eligible(fresh) is False
    assert "dispost" in get_post_closure_issue_ineligibility_reason(fresh).lower()


# ── R2: abertura atômica — status+campos+evento+comunicação ────────────────


@pytest.mark.django_db
def test_open_moves_to_awaiting_scheduling_with_issue_and_snapshot(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R2: abertura válida → ``AWAITING_SCHEDULING`` + issue ativa + 5 campos
    limpos + ``scheduling_reopen_reason`` + evento com motivo e snapshot
    administrativo + comunicação com o motivo."""
    scheduler_user = user_factory("scheduler-abertura", SCHEDULER_ROLE)
    case = _retained_closed_case(
        created_by=nir_user, decided_by=doctor_user, scheduler_user=scheduler_user
    )
    scheduled_at = case.scheduled_datetime
    assert scheduled_at is not None
    events_before = case.events.count()

    open_post_closure_issue(case, reason=REOPEN_REASON, user=nir_user, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.AWAITING_SCHEDULING
    # Issue ativa registrada com autor e instante.
    assert fresh.post_schedule_issue_status == "opened"
    assert fresh.post_schedule_issue_reason == REOPEN_REASON
    assert fresh.post_schedule_issue_opened_by == nir_user
    assert fresh.post_schedule_issue_opened_at is not None
    # Os 5 campos de agendamento zerados; o motivo de reabertura persiste.
    assert fresh.scheduled_unit is None
    assert fresh.scheduled_datetime is None
    assert fresh.scheduled_location == ""
    assert fresh.scheduled_by is None
    assert fresh.scheduled_decided_at is None
    assert fresh.scheduling_reopen_reason == REOPEN_REASON
    # Eventos: transição com motivo + abertura com motivo e snapshot.
    assert fresh.events.count() == events_before + 2
    trail = _event_types(fresh)
    assert trail[-2:] == [
        CaseEventType.CASE_STATUS_AWAITING_SCHEDULING,
        CaseEventType.CASE_POST_CLOSURE_ISSUE_OPENED,
    ]
    transition = fresh.events.order_by("id")[events_before]
    assert transition.payload["source"] == CaseStatus.CLEANED.value
    assert transition.payload["reason"] == REOPEN_REASON
    opened = fresh.events.order_by("id")[events_before + 1]
    assert opened.payload["reason"] == REOPEN_REASON
    snapshot = opened.payload["scheduling_snapshot"]
    assert snapshot["scheduled_unit"] == SchedulingUnit.UNIT_1
    assert snapshot["scheduled_location"] == "HGRS — Hemodinâmica"
    assert snapshot["scheduled_datetime"] == scheduled_at.isoformat()
    # Comunicação da thread com o motivo interpolado.
    assert any(REOPEN_REASON in body for body in _user_messages(fresh))


@pytest.mark.django_db
def test_open_with_blank_reason_writes_nothing(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R2: motivo ausente/vazio é recusado sem NENHUMA escrita."""
    scheduler_user = user_factory("scheduler-motivo", SCHEDULER_ROLE)
    case = _retained_closed_case(
        created_by=nir_user, decided_by=doctor_user, scheduler_user=scheduler_user
    )
    events_before = case.events.count()
    messages_before = len(_user_messages(case))

    with pytest.raises(ValueError, match="motivo"):
        open_post_closure_issue(case, reason="   ", user=nir_user, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    assert fresh.events.count() == events_before
    assert fresh.post_schedule_issue_status == ""
    assert fresh.post_schedule_issue_reason == ""
    assert fresh.scheduling_reopen_reason == ""
    assert fresh.scheduled_unit == SchedulingUnit.UNIT_1
    assert len(_user_messages(fresh)) == messages_before


@pytest.mark.django_db
def test_open_ineligible_writes_nothing(nir_user: User, doctor_user: User) -> None:
    """R2: caso inelegível (sem agendamento confirmado) recusa sem escrita."""
    case = _new_case(nir_user)
    _drive_to_awaiting_doctor(case)
    _deny_and_reply(case, doctor_user)
    _close_cycle(case, created_by=nir_user, minimize=False)
    events_before = case.events.count()

    with pytest.raises(ValueError, match="[Aa]gendamento|elegível"):
        open_post_closure_issue(case, reason=REOPEN_REASON, user=nir_user, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    assert fresh.events.count() == events_before
    assert fresh.post_schedule_issue_status == ""


@pytest.mark.django_db
def test_open_failure_mid_chain_rolls_back_everything(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: falha no meio do atomic desfaz TUDO — status, campos, eventos e
    comunicação voltam ao estado inicial (sem escrita parcial)."""
    scheduler_user = user_factory("scheduler-rollback", SCHEDULER_ROLE)
    case = _retained_closed_case(
        created_by=nir_user, decided_by=doctor_user, scheduler_user=scheduler_user
    )
    events_before = case.events.count()

    def _exploding_post(locked_case: Case, *, user: User, role: str, body: str) -> object:
        del locked_case, user, role, body
        raise RuntimeError("thread indisponível")

    monkeypatch.setattr("apps.cases.closure.post_user_communication", _exploding_post)

    with pytest.raises(RuntimeError, match="thread indisponível"):
        open_post_closure_issue(case, reason=REOPEN_REASON, user=nir_user, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    assert fresh.events.count() == events_before
    assert fresh.post_schedule_issue_status == ""
    assert fresh.scheduled_unit == SchedulingUnit.UNIT_1


# ── R3: issue única + zeragem em qualquer chegada a CLEANED ───────────────


@pytest.mark.django_db
def test_second_open_while_issue_active_is_refused(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R3: issue ativa bloqueia nova abertura — sem escrita parcial."""
    scheduler_user = user_factory("scheduler-unica", SCHEDULER_ROLE)
    case = _retained_closed_case(
        created_by=nir_user, decided_by=doctor_user, scheduler_user=scheduler_user
    )
    open_post_closure_issue(case, reason=REOPEN_REASON, user=nir_user, role=NIR_ROLE)
    reopened = Case.objects.get(pk=case.case_id)
    events_after_first = reopened.events.count()

    with pytest.raises(ValueError, match="[Ii]ntercorrência"):
        open_post_closure_issue(reopened, reason="outro motivo", user=nir_user, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.AWAITING_SCHEDULING
    assert fresh.post_schedule_issue_status == "opened"
    assert fresh.post_schedule_issue_reason == REOPEN_REASON
    assert fresh.events.count() == events_after_first


@pytest.mark.django_db
def test_ack_of_new_cycle_clears_issue_with_audit_event(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R3: a ciência do novo ciclo zera a issue no MESMO atomic, com evento
    de auditoria próprio — o caso volta a ser elegível."""
    from apps.cases.closure import acknowledge_case_receipt

    scheduler_user = user_factory("scheduler-novo-ciclo", SCHEDULER_ROLE)
    case = _retained_closed_case(
        created_by=nir_user, decided_by=doctor_user, scheduler_user=scheduler_user
    )
    open_post_closure_issue(case, reason=REOPEN_REASON, user=nir_user, role=NIR_ROLE)
    reopened = Case.objects.get(pk=case.case_id)
    assert reopened.status == CaseStatus.AWAITING_SCHEDULING

    _confirm(reopened, scheduler_user, location="HGRS — Hemodinâmica, sala 2")
    assert reopened.status == CaseStatus.FINAL_REPLY_POSTED
    events_before_ack = reopened.events.count()

    acknowledge_case_receipt(reopened, user=nir_user, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    assert fresh.post_schedule_issue_status == ""
    assert fresh.post_schedule_issue_reason == ""
    assert fresh.post_schedule_issue_opened_by is None
    assert fresh.post_schedule_issue_opened_at is None
    trail = _event_types(fresh)
    assert CaseEventType.CASE_POST_CLOSURE_ISSUE_CLOSED in trail[events_before_ack:]
    # Novo ciclo concluído: elegível novamente a outra intercorrência.
    assert is_post_closure_issue_eligible(fresh) is True


@pytest.mark.django_db
def test_administrative_close_clears_issue_with_audit_event(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R3/D9: o encerramento administrativo zera a issue no MESMO atomic, com
    evento de auditoria registrando quem a fechou."""
    from apps.cases.closure import administratively_close_case

    scheduler_user = user_factory("scheduler-admin", SCHEDULER_ROLE)
    manager_user = user_factory("manager-admin", MANAGER_ROLE)
    case = _retained_closed_case(
        created_by=nir_user, decided_by=doctor_user, scheduler_user=scheduler_user
    )
    open_post_closure_issue(case, reason=REOPEN_REASON, user=nir_user, role=NIR_ROLE)
    reopened = Case.objects.get(pk=case.case_id)
    events_before = reopened.events.count()

    administratively_close_case(
        case=reopened,
        user=manager_user,
        active_role=MANAGER_ROLE,
        reason_code="processing_error",
        reason_text="travado aguardando reagendamento",
    )

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    assert fresh.post_schedule_issue_status == ""
    assert fresh.post_schedule_issue_reason == ""
    new_events = list(fresh.events.order_by("id"))[events_before:]
    closed = [
        event
        for event in new_events
        if event.event_type == CaseEventType.CASE_POST_CLOSURE_ISSUE_CLOSED
    ]
    assert len(closed) == 1
    assert closed[0].actor == manager_user
    assert closed[0].actor_role == MANAGER_ROLE


@pytest.mark.django_db(transaction=True)
def test_open_vs_administrative_close_race_resolves_under_lock(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R3/D9: corrida abertura×encerramento-administrativo — o
    ``select_for_update`` serializa os dois atomics; o estado final é sempre
    coerente (sem issue órfã, sem escrita parcial)."""
    from django.db import connection

    from apps.cases.closure import administratively_close_case

    scheduler_user = user_factory("scheduler-corrida", SCHEDULER_ROLE)
    manager_user = user_factory("manager-corrida", MANAGER_ROLE)
    case = _retained_closed_case(
        created_by=nir_user, decided_by=doctor_user, scheduler_user=scheduler_user
    )
    case_pk = case.case_id
    barrier = threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def _open() -> None:
        try:
            barrier.wait(timeout=30)
            loaded = Case.objects.get(pk=case_pk)
            open_post_closure_issue(loaded, reason=REOPEN_REASON, user=nir_user, role=NIR_ROLE)
            outcomes["open"] = "ok"
        except Exception as exc:  # noqa: BLE001 — o resultado é assertido abaixo
            outcomes["open"] = f"{type(exc).__name__}: {exc}"
        finally:
            connection.close()

    def _admin_close() -> None:
        try:
            barrier.wait(timeout=30)
            loaded = Case.objects.get(pk=case_pk)
            administratively_close_case(
                case=loaded,
                user=manager_user,
                active_role=MANAGER_ROLE,
                reason_code="processing_error",
                reason_text="corrida com a abertura",
            )
            outcomes["admin"] = "ok"
        except Exception as exc:  # noqa: BLE001 — o resultado é assertido abaixo
            outcomes["admin"] = f"{type(exc).__name__}: {exc}"
        finally:
            connection.close()

    first = threading.Thread(target=_open)
    second = threading.Thread(target=_admin_close)
    first.start()
    second.start()
    first.join(timeout=60)
    second.join(timeout=60)
    assert not first.is_alive() and not second.is_alive()

    # Partição exata da corrida (``select_for_update`` serializa os atomics):
    # a abertura SEMPRE conclui (o estado inicial é elegível e o admin nunca o
    # torna inelegível); o admin conclui se enxergar a issue (e a zera) ou
    # recusa "já encerrado" se ler o CLEANED inicial antes do lock da abertura.
    assert outcomes.get("open") == "ok"
    fresh = Case.objects.get(pk=case_pk)
    trail = _event_types(fresh)
    if outcomes.get("admin") == "ok":
        assert fresh.status == CaseStatus.CLEANED
        assert fresh.post_schedule_issue_status == ""
        assert CaseEventType.CASE_POST_CLOSURE_ISSUE_OPENED in trail
        assert CaseEventType.CASE_POST_CLOSURE_ISSUE_CLOSED in trail
    else:
        assert "já encerrado" in outcomes.get("admin", "")
        assert fresh.status == CaseStatus.AWAITING_SCHEDULING
        assert fresh.post_schedule_issue_status == "opened"
        assert fresh.post_schedule_issue_reason == REOPEN_REASON
        assert CaseEventType.CASE_POST_CLOSURE_ISSUE_OPENED in trail
        assert CaseEventType.CASE_POST_CLOSURE_ISSUE_CLOSED not in trail


# ── Marcador de disposição: gravado no atomic da minimização e da purga ───


@pytest.mark.django_db
def test_disposed_marker_written_in_minimizing_ack(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """D2: o ack COM minimização grava ``clinical_data_disposed_at`` no MESMO
    atomic da limpeza (o caso fica inelegível por dados dispostos)."""
    scheduler_user = user_factory("scheduler-marcador", SCHEDULER_ROLE)
    case = _new_case(nir_user)
    _drive_to_awaiting_doctor(case)
    _approve_and_request(case, doctor_user)
    _confirm(case, scheduler_user)
    _close_cycle(case, created_by=nir_user, minimize=True)

    assert case.status == CaseStatus.CLEANED
    assert case.clinical_data_disposed_at is not None
    assert is_post_closure_issue_eligible(case) is False


@pytest.mark.django_db
def test_retained_ack_keeps_disposed_marker_null(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """D2: o ack em retenção NÃO grava o marcador (dados preservados)."""
    scheduler_user = user_factory("scheduler-retido", SCHEDULER_ROLE)
    case = _retained_closed_case(
        created_by=nir_user, decided_by=doctor_user, scheduler_user=scheduler_user
    )

    assert case.status == CaseStatus.CLEANED
    assert case.clinical_data_disposed_at is None


@pytest.mark.django_db(transaction=True)
def test_disposed_marker_written_by_purge(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """D2: a purga retroativa grava ``clinical_data_disposed_at`` junto da
    limpeza (caso retido passa a inelegível por dados dispostos)."""
    from apps.cases.closure import purge_closed_case_data

    scheduler_user = user_factory("scheduler-purga", SCHEDULER_ROLE)
    case = _retained_closed_case(
        created_by=nir_user, decided_by=doctor_user, scheduler_user=scheduler_user
    )
    assert case.clinical_data_disposed_at is None
    # Dado retido para a purga ter o que limpar (sem nada retido é no-op).
    case.summary_text = "Resumo clínico sintético gerado pelo LLM2."
    case.save(update_fields=["summary_text"])

    purge_closed_case_data(case)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.clinical_data_disposed_at is not None
    assert is_post_closure_issue_eligible(fresh) is False


@pytest.mark.django_db
def test_backfill_marks_cleaned_with_empty_summary() -> None:
    """Migration 0012 (D2): backfill ``CLEANED`` com ``summary_text`` vazio ⇒
    ``disposed_at`` preenchido — casos retidos e não-encerrados intactos."""
    migration = import_module("apps.cases.migrations.0012_post_closure_issue")
    from django.apps import apps as django_apps

    case_model = django_apps.get_model("cases", "Case")
    cleaned_empty = case_model.objects.create(
        created_by=User.objects.create_user(username="backfill-cleaned"),
        status=CaseStatus.CLEANED,
        summary_text="",
    )
    cleaned_kept = case_model.objects.create(
        created_by=cleaned_empty.created_by,
        status=CaseStatus.CLEANED,
        summary_text="Resumo clínico sintético gerado pelo LLM2.",
    )
    open_case = case_model.objects.create(
        created_by=cleaned_empty.created_by,
        status=CaseStatus.FINAL_REPLY_POSTED,
        summary_text="",
    )

    migration.backfill_clinical_data_disposed_at(django_apps, None)

    cleaned_empty.refresh_from_db()
    cleaned_kept.refresh_from_db()
    open_case.refresh_from_db()
    assert cleaned_empty.clinical_data_disposed_at is not None
    assert cleaned_kept.clinical_data_disposed_at is None
    assert open_case.clinical_data_disposed_at is None


# ── Trilha: rótulo e badge dos eventos novos ───────────────────────────────


def test_new_issue_events_have_trail_label_and_badge() -> None:
    """Os dois eventos novos exibem rótulo legível (nunca o valor bruto) e
    badge mapeado na trilha (cobertura além do anti-drift do enum)."""
    for event_type in (
        CaseEventType.CASE_POST_CLOSURE_ISSUE_OPENED,
        CaseEventType.CASE_POST_CLOSURE_ISSUE_CLOSED,
    ):
        label = event_label(event_type)
        assert label.strip()
        assert label != event_type
        assert event_badge_css(event_type) != ""


@pytest.mark.django_db
def test_opened_event_projects_reason_and_snapshot_details(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """A trilha do evento de abertura projeta o motivo e o snapshot
    administrativo (unidade/data-hora/local), sem PHI."""
    scheduler_user = user_factory("scheduler-trilha", SCHEDULER_ROLE)
    case = _retained_closed_case(
        created_by=nir_user, decided_by=doctor_user, scheduler_user=scheduler_user
    )
    open_post_closure_issue(case, reason=REOPEN_REASON, user=nir_user, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    opened = fresh.events.get(event_type=CaseEventType.CASE_POST_CLOSURE_ISSUE_OPENED)
    lines = event_detail_lines(opened)

    assert any(REOPEN_REASON in line for line in lines)
    assert any("Unidade 1" in line for line in lines)
    assert any("HGRS — Hemodinâmica" in line for line in lines)
    joined = " ".join(lines)
    assert nir_user.username not in joined
