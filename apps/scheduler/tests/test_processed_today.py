"""Aba «Processados Hoje» do agendador (slice 005, R2/R4).

R2: a aba ``processados`` lista decisões de agendamento (confirmação OU
negação) do dia corrente pelo próprio agendador
(``scheduling_decided_by`` + ``scheduling_decided_at`` do slice 004 na
janela local), qualquer status atual, ordenada por data/hora desc; caso de
dia anterior fora das abas (consultável no Histórico do slice 004).
R4: contadores, títulos («Processados Hoje») e estado vazio com a nova
semântica; pendentes inalteradas em escopo e ordenação.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.day_bounds import local_day_bounds
from apps.cases.models import Case, CaseProcedure, CaseStatus
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.scheduler.services import confirm_case_scheduling, deny_case_scheduling

SCHEDULER_ROLE = "scheduler"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"

ANGIO_TYPE = "art_perif"
CARDIO_TYPE = "cat_cardiaco"

_PROCESSING_STEPS: list[tuple[str, dict[str, object]]] = [
    ("start_pdf_extraction", {}),
    ("complete_pdf_extraction", {}),
    ("complete_anonymization", {}),
    ("complete_llm_extraction", {}),
    ("complete_llm_summarization", {}),
]


def _awaiting_scheduling_case(
    created_by: User,
    decided_by: User,
    procedure_types: Sequence[str],
) -> Case:
    """Caso em ``AWAITING_SCHEDULING`` (fila de agendamento)."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    for operation, extra in _PROCESSING_STEPS:
        getattr(case, operation)(user=None, role=SYSTEM_ROLE, **extra)
    record_doctor_procedure_decisions(
        case,
        {procedure_type: ("approved", "") for procedure_type in procedure_types},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    case.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_SCHEDULING
    return case


def _confirmed_case(
    created_by: User, decided_by: User, scheduled_by: User, *, patient_name: str = ""
) -> Case:
    """Caso confirmado hoje pelo agendador (serviço do slice 001)."""
    case = _awaiting_scheduling_case(created_by, decided_by, (ANGIO_TYPE,))
    confirm_case_scheduling(
        case,
        unit=1,
        scheduled_datetime=timezone.now() + timedelta(days=2),
        scheduled_location="Sala de hemodinâmica",
        user=scheduled_by,
        role=SCHEDULER_ROLE,
    )
    if patient_name:
        Case.objects.filter(pk=case.pk).update(patient_name=patient_name)
    case.refresh_from_db()
    return case


def _denied_case(
    created_by: User, decided_by: User, scheduled_by: User, *, patient_name: str = ""
) -> Case:
    """Caso negado hoje pelo agendador (negação também processa)."""
    case = _awaiting_scheduling_case(created_by, decided_by, (CARDIO_TYPE,))
    deny_case_scheduling(case, reason="sem vaga", user=scheduled_by, role=SCHEDULER_ROLE)
    if patient_name:
        Case.objects.filter(pk=case.pk).update(patient_name=patient_name)
    case.refresh_from_db()
    return case


def _backdate_scheduling_decision(case: Case, decided_at: datetime) -> None:
    """Reescreve o instante da decisão de agendamento — simula decisão antiga."""
    Case.objects.filter(pk=case.pk).update(scheduling_decided_at=decided_at)
    case.refresh_from_db()


def _processed_bodies(client: Client, params: dict[str, str] | None = None) -> tuple[str, str]:
    """Corpos da aba processados (página cheia + partial)."""
    query = {"tab": "processados"}
    if params:
        query.update(params)
    page = client.get(reverse("scheduler:queue"), query).content.decode()
    partial = client.get(reverse("scheduler:queue_partial"), query).content.decode()
    return page, partial


# ── R2: janela do dia por decisor ─────────────────────────────────────────


@pytest.mark.django_db
def test_processed_today_lists_only_current_day(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: só a decisão de hoje aparece — a de ontem some da aba processados."""
    doctor = user_factory("medico-hoje-proc", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-hoje-unico", (SCHEDULER_ROLE,))
    today = _confirmed_case(nir_user, doctor, scheduler, patient_name="Paciente Hoje Proc")
    yesterday = _confirmed_case(nir_user, doctor, scheduler, patient_name="Paciente Ontem Proc")
    start, _ = local_day_bounds()
    _backdate_scheduling_decision(yesterday, start - timedelta(hours=1))
    login_user(scheduler, SCHEDULER_ROLE)

    page, partial = _processed_bodies(client)

    assert str(today.case_id) in page
    assert "Paciente Hoje Proc" in page
    assert str(today.case_id) in partial
    assert str(yesterday.case_id) not in page
    assert "Paciente Ontem Proc" not in page
    assert str(yesterday.case_id) not in partial


@pytest.mark.django_db
def test_processed_today_includes_denial(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: negação de hoje do próprio agendador TAMBÉM é processada."""
    doctor = user_factory("medico-nega-proc", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-nega-hoje", (SCHEDULER_ROLE,))
    denied = _denied_case(nir_user, doctor, scheduler, patient_name="Paciente Negado Hoje")
    login_user(scheduler, SCHEDULER_ROLE)

    page, _ = _processed_bodies(client)

    assert str(denied.case_id) in page
    assert "Paciente Negado Hoje" in page


@pytest.mark.django_db
def test_processed_today_includes_only_own_decisions(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: decisões de hoje de OUTRO agendador não aparecem na minha aba."""
    doctor = user_factory("medico-outro-proc", (DOCTOR_ROLE,))
    mine = user_factory("agendador-proprio-hoje", (SCHEDULER_ROLE,))
    other = user_factory("agendador-outro-hoje", (SCHEDULER_ROLE,))
    own = _confirmed_case(nir_user, doctor, mine, patient_name="Paciente Proprio Proc")
    alien = _confirmed_case(nir_user, doctor, other, patient_name="Paciente Alheio Proc")
    login_user(mine, SCHEDULER_ROLE)

    page, _ = _processed_bodies(client)

    assert str(own.case_id) in page
    assert "Paciente Proprio Proc" in page
    assert str(alien.case_id) not in page
    assert "Paciente Alheio Proc" not in page


@pytest.mark.django_db
def test_processed_yesterday_in_no_tab_but_in_historical(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: decisão de ontem fora das abas, mas consultável no Histórico (004)."""
    doctor = user_factory("medico-ontem-proc", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-ontem-nenhuma", (SCHEDULER_ROLE,))
    old = _confirmed_case(nir_user, doctor, scheduler, patient_name="Paciente Ontem Historico")
    _backdate_scheduling_decision(old, timezone.now() - timedelta(days=1, hours=2))
    login_user(user_factory("agendador-visor-ontem", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    awaiting_body = client.get(reverse("scheduler:queue")).content.decode()
    processed_body, _ = _processed_bodies(client)

    assert "Paciente Ontem Historico" not in awaiting_body
    assert "Paciente Ontem Historico" not in processed_body

    historical = client.get(
        reverse("scheduler:historical_search"), {"q": "Paciente Ontem Historico"}
    ).content.decode()
    assert "Paciente Ontem Historico" in historical


@pytest.mark.django_db
def test_processed_null_decider_excluded_from_everyone(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: decisão com decisor nulo (fail-closed) fora da aba de todos."""
    doctor = user_factory("medico-nulo-proc", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-nulo-fora", (SCHEDULER_ROLE,))
    legacy = _confirmed_case(nir_user, doctor, scheduler, patient_name="Paciente Nulo Proc")
    Case.objects.filter(pk=legacy.pk).update(scheduling_decided_by=None)
    legacy.refresh_from_db()
    assert legacy.scheduling_decided_at is not None
    assert legacy.scheduling_decided_by_id is None
    login_user(user_factory("agendador-visor-nulo", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    page, _ = _processed_bodies(client)

    assert "Paciente Nulo Proc" not in page


@pytest.mark.django_db
def test_processed_today_ordered_by_decision_desc(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: ordenação pela data/hora da decisão de agendamento, mais recente primeiro."""
    doctor = user_factory("medico-ordem-proc", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-ordem-hoje", (SCHEDULER_ROLE,))
    older = _confirmed_case(nir_user, doctor, scheduler, patient_name="Paciente Ordem Velha Proc")
    newer = _denied_case(nir_user, doctor, scheduler, patient_name="Paciente Ordem Nova Proc")
    start, _ = local_day_bounds()
    _backdate_scheduling_decision(older, start + timedelta(hours=2))
    _backdate_scheduling_decision(newer, start + timedelta(hours=6))
    login_user(scheduler, SCHEDULER_ROLE)

    page, _ = _processed_bodies(client)

    assert str(newer.case_id) in page
    assert str(older.case_id) in page
    assert page.index(str(newer.case_id)) < page.index(str(older.case_id))


# ── R4: contadores, títulos e estado vazio ────────────────────────────────


@pytest.mark.django_db
def test_processed_today_counts_titles_and_empty_state(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4: badges contam decisões de hoje do próprio agendador; títulos «Hoje»."""
    doctor = user_factory("medico-conta-proc", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-conta-hoje", (SCHEDULER_ROLE,))
    other = user_factory("agendador-conta-outro", (SCHEDULER_ROLE,))
    _confirmed_case(nir_user, doctor, scheduler, patient_name="Paciente Conta Um Proc")
    _denied_case(nir_user, doctor, scheduler, patient_name="Paciente Conta Dois Proc")
    stale = _confirmed_case(nir_user, doctor, scheduler, patient_name="Paciente Conta Velha Proc")
    _backdate_scheduling_decision(stale, timezone.now() - timedelta(days=1, hours=2))
    _confirmed_case(nir_user, doctor, other, patient_name="Paciente Conta Alheia Proc")
    login_user(scheduler, SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue"), {"tab": "processados"}).content.decode()

    assert "Processados Hoje" in body
    assert 'data-count="2"' in body
    assert "Paciente Conta Alheia Proc" not in body

    login_user(user_factory("agendador-vazio-hoje", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)
    empty_body = client.get(reverse("scheduler:queue"), {"tab": "processados"}).content.decode()

    assert "Nenhum caso processado hoje" in empty_body
    assert 'data-count="0"' in empty_body
