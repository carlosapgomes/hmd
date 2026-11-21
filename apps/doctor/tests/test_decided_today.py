"""Aba «Decididos Hoje» do médico + carimbos médicos (slice 005, R1/R3/R4).

R1: a aba ``decididos`` lista decisões médicas do dia corrente pelo próprio
médico (``doctor_decided_by`` + ``doctor_decided_at`` na janela local),
qualquer status atual (inclui caso encerrado no mesmo dia), ordenada por
data/hora desc; caso de ontem fora de TODAS as abas; decisor nulo
(backfill fail-closed) fora da aba de todos.
R3: ``record_doctor_procedure_decisions`` carimba o par
``doctor_decided_at``/``by`` no MESMO atomic (+ anti-drift contra
``Max(rows)``); migration 0014 com backfill auditável por eventos.
R4: contadores, títulos («Casos decididos hoje») e estado vazio com a nova
semântica; pendentes inalteradas.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from importlib import import_module

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.day_bounds import local_day_bounds
from apps.cases.models import ActorType, Case, CaseEvent, CaseProcedure, CaseStatus
from apps.cases.procedures import record_doctor_procedure_decisions

DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"

ANGIO_TYPE = "art_perif"
CARDIO_TYPE = "cat_cardiaco"


def _advance_to_awaiting_doctor(case: Case) -> None:
    """Dirige o caso pelo pipeline até ``AWAITING_DOCTOR``."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _decided_case(
    created_by: User,
    decided_by: User,
    procedure_types: Sequence[str],
    *,
    decisions: dict[str, tuple[str, str]] | None = None,
    patient_name: str = "",
) -> Case:
    """Caso decidido pelo serviço (ator real, instante real = hoje)."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    _advance_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case,
        decisions or {procedure_type: ("denied", "") for procedure_type in procedure_types},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    if patient_name:
        Case.objects.filter(pk=case.pk).update(patient_name=patient_name)
    case.refresh_from_db()
    return case


def _backdate_decision(case: Case, decided_at: datetime) -> None:
    """Reescreve o instante da decisão (caso + rows) — simula decisão antiga."""
    CaseProcedure.objects.filter(case=case).update(doctor_decided_at=decided_at)
    Case.objects.filter(pk=case.pk).update(doctor_decided_at=decided_at)
    case.refresh_from_db()


def _decided_bodies(client: Client, params: dict[str, str] | None = None) -> tuple[str, str]:
    """Corpos da aba decididos (página cheia + partial)."""
    query = {"tab": "decididos"}
    if params:
        query.update(params)
    page = client.get(reverse("doctor:queue"), query).content.decode()
    partial = client.get(reverse("doctor:queue_partial"), query).content.decode()
    return page, partial


# ── R1: janela do dia por decisor ─────────────────────────────────────────


@pytest.mark.django_db
def test_decided_today_lists_only_current_day(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1: só a decisão de hoje aparece — a de ontem some da aba decididos."""
    doctor = user_factory("medico-hoje-unico", (DOCTOR_ROLE,))
    today = _decided_case(nir_user, doctor, (ANGIO_TYPE,), patient_name="Paciente Hoje Unico")
    yesterday = _decided_case(nir_user, doctor, (CARDIO_TYPE,), patient_name="Paciente Ontem Fora")
    start, _ = local_day_bounds()
    _backdate_decision(yesterday, start - timedelta(hours=1))
    login_user(doctor, DOCTOR_ROLE)

    page, partial = _decided_bodies(client)

    assert str(today.case_id) in page
    assert "Paciente Hoje Unico" in page
    assert str(today.case_id) in partial
    assert str(yesterday.case_id) not in page
    assert "Paciente Ontem Fora" not in page
    assert str(yesterday.case_id) not in partial


@pytest.mark.django_db
def test_decided_today_includes_only_own_decisions(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1: decisões de hoje de OUTRO médico não aparecem na minha aba."""
    mine = user_factory("medico-proprio-hoje", (DOCTOR_ROLE,))
    other = user_factory("medico-outro-hoje", (DOCTOR_ROLE,))
    own = _decided_case(nir_user, mine, (ANGIO_TYPE,), patient_name="Paciente Proprio")
    alien = _decided_case(nir_user, other, (CARDIO_TYPE,), patient_name="Paciente Alheio")
    login_user(mine, DOCTOR_ROLE)

    page, _ = _decided_bodies(client)

    assert str(own.case_id) in page
    assert "Paciente Proprio" in page
    assert str(alien.case_id) not in page
    assert "Paciente Alheio" not in page


@pytest.mark.django_db
def test_decided_today_includes_case_closed_same_day(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1: caso decidido hoje e já encerrado (CLEANED) permanece listado."""
    doctor = user_factory("medico-encerrado-hoje", (DOCTOR_ROLE,))
    closed = _decided_case(nir_user, doctor, (ANGIO_TYPE,), patient_name="Paciente Encerrado Hoje")
    Case.objects.filter(pk=closed.pk).update(status=CaseStatus.CLEANED)
    closed.refresh_from_db()
    assert closed.status == CaseStatus.CLEANED
    login_user(doctor, DOCTOR_ROLE)

    page, _ = _decided_bodies(client)

    assert str(closed.case_id) in page
    assert "Paciente Encerrado Hoje" in page


@pytest.mark.django_db
def test_decided_yesterday_in_no_tab(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1: decisão de ontem não aparece em NENHUMA aba do médico."""
    doctor = user_factory("medico-ontem-nenhuma", (DOCTOR_ROLE,))
    old = _decided_case(nir_user, doctor, (ANGIO_TYPE,), patient_name="Paciente Ontem Nenhuma")
    _backdate_decision(old, timezone.now() - timedelta(days=1, hours=2))
    login_user(user_factory("medico-visor-ontem", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    awaiting_body = client.get(reverse("doctor:queue")).content.decode()
    decided_body, _ = _decided_bodies(client)

    assert "Paciente Ontem Nenhuma" not in awaiting_body
    assert "Paciente Ontem Nenhuma" not in decided_body


@pytest.mark.django_db
def test_null_decider_excluded_from_everyone(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1: decisão legada com decisor nulo (backfill fail-closed) fora de todos."""
    doctor = user_factory("medico-nulo-fora", (DOCTOR_ROLE,))
    legacy = _decided_case(nir_user, doctor, (ANGIO_TYPE,), patient_name="Paciente Legado Nulo")
    Case.objects.filter(pk=legacy.pk).update(doctor_decided_by=None)
    legacy.refresh_from_db()
    assert legacy.doctor_decided_at is not None
    assert legacy.doctor_decided_by_id is None
    login_user(user_factory("medico-visor-nulo", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    page, _ = _decided_bodies(client)

    assert "Paciente Legado Nulo" not in page


@pytest.mark.django_db
def test_decided_today_ordered_by_decision_desc(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1: ordenação pela data/hora da decisão, mais recente primeiro."""
    doctor = user_factory("medico-ordem-hoje", (DOCTOR_ROLE,))
    older = _decided_case(nir_user, doctor, (ANGIO_TYPE,), patient_name="Paciente Ordem Velha")
    newer = _decided_case(nir_user, doctor, (CARDIO_TYPE,), patient_name="Paciente Ordem Nova")
    start, _ = local_day_bounds()
    _backdate_decision(older, start + timedelta(hours=2))
    _backdate_decision(newer, start + timedelta(hours=6))
    login_user(doctor, DOCTOR_ROLE)

    page, _ = _decided_bodies(client)

    assert page.index(str(newer.case_id)) < page.index(str(older.case_id))


# ── R4: contadores, títulos e estado vazio ────────────────────────────────


@pytest.mark.django_db
def test_decided_today_counts_titles_and_empty_state(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4: badges contam decisões de hoje do próprio médico; títulos «Hoje»."""
    doctor = user_factory("medico-conta-hoje", (DOCTOR_ROLE,))
    other = user_factory("medico-conta-outro", (DOCTOR_ROLE,))
    _decided_case(nir_user, doctor, (ANGIO_TYPE,), patient_name="Paciente Conta Um")
    _decided_case(nir_user, doctor, (CARDIO_TYPE,), patient_name="Paciente Conta Dois")
    stale = _decided_case(nir_user, doctor, (ANGIO_TYPE,), patient_name="Paciente Conta Velha")
    _backdate_decision(stale, timezone.now() - timedelta(days=1, hours=2))
    _decided_case(nir_user, other, (ANGIO_TYPE,), patient_name="Paciente Conta Alheia")
    login_user(doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()

    assert "Casos decididos hoje" in body
    assert "Decididos Hoje" in body
    assert 'data-count="2"' in body
    assert "Paciente Conta Alheia" not in body

    login_user(user_factory("medico-vazio-hoje", (DOCTOR_ROLE,)), DOCTOR_ROLE)
    empty_body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()

    assert "Nenhum caso decidido hoje" in empty_body
    assert 'data-count="0"' in empty_body


# ── R3: carimbo no atomic + anti-drift ────────────────────────────────────


@pytest.mark.django_db
def test_doctor_decision_stamps_case_pair(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3: a decisão médica carimba ``doctor_decided_at``/``by`` no mesmo atomic."""
    doctor = user_factory("medico-carimbo-hoje", (DOCTOR_ROLE,))
    case = _decided_case(nir_user, doctor, (ANGIO_TYPE,))

    fresh = Case.objects.get(pk=case.pk)

    assert fresh.doctor_decided_at is not None
    assert fresh.doctor_decided_by_id == doctor.pk
    assert abs((timezone.now() - fresh.doctor_decided_at).total_seconds()) < 120


@pytest.mark.django_db
def test_doctor_decided_at_matches_max_rows(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3 (anti-drift): ``Case.doctor_decided_at == Max(procedures__doctor_decided_at)``."""
    from django.db.models import Max

    doctor = user_factory("medico-antidrift", (DOCTOR_ROLE,))
    case = _decided_case(nir_user, doctor, (ANGIO_TYPE, CARDIO_TYPE))

    fresh = Case.objects.annotate(max_row=Max("procedures__doctor_decided_at")).get(pk=case.pk)

    assert fresh.doctor_decided_at is not None
    assert fresh.max_row is not None
    assert fresh.doctor_decided_at == fresh.max_row


# ── R3: backfill auditável por eventos ────────────────────────────────────


def _run_doctor_backfill() -> None:
    """Executa a função de backfill da migration 0014 (D5)."""
    from django.apps import apps as django_apps

    migration = import_module("apps.cases.migrations.0014_doctor_decided_stamps")
    migration.backfill_doctor_decided(django_apps, None)


def _legacy_case_with_event(
    created_by: User,
    decided_by: User | None,
    *,
    accepted: bool,
    patient_name: str,
) -> Case:
    """Caso legado: só FSM + evento (sem carimbo) — como antes da migration."""
    case = Case.objects.create(created_by=created_by)
    CaseProcedure.objects.create(case=case, procedure_type=ANGIO_TYPE, declared_by_nir=True)
    _advance_to_awaiting_doctor(case)
    case.record_doctor_decision(accepted=accepted, user=decided_by, role=DOCTOR_ROLE)
    Case.objects.filter(pk=case.pk).update(
        patient_name=patient_name,
        doctor_decided_at=None,
        doctor_decided_by=None,
    )
    case.refresh_from_db()
    return case


@pytest.mark.django_db
def test_backfill_covers_legacy_decision_with_actor(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """Backfill 0014: última decisão com ator preenche o par (timestamp+ator)."""
    doctor = user_factory("medico-backfill-hoje", (DOCTOR_ROLE,))
    legacy = _legacy_case_with_event(
        nir_user, doctor, accepted=False, patient_name="Paciente Backfill Negado"
    )
    assert legacy.doctor_decided_at is None

    _run_doctor_backfill()

    fresh = Case.objects.get(pk=legacy.pk)
    assert fresh.doctor_decided_at is not None
    assert fresh.doctor_decided_by_id == doctor.pk
    event = CaseEvent.objects.get(case=legacy, event_type="CASE_STATUS_DOCTOR_DENIED")
    assert fresh.doctor_decided_at == event.timestamp


@pytest.mark.django_db
def test_backfill_uses_latest_decision_event(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """Backfill 0014: com negação e aceitação na trilha, vale o ÚLTIMO evento."""
    doctor = user_factory("medico-backfill-ultimo", (DOCTOR_ROLE,))
    legacy = _legacy_case_with_event(
        nir_user, doctor, accepted=False, patient_name="Paciente Backfill Ultimo"
    )
    # Segundo evento posterior na trilha (a trilha é a fonte; vale o último).
    CaseEvent.objects.create(
        case=legacy,
        event_type="CASE_STATUS_DOCTOR_ACCEPTED",
        actor_type=ActorType.USER,
        actor=doctor,
        actor_role=DOCTOR_ROLE,
        payload={"source": "AWAITING_DOCTOR", "target": "DOCTOR_ACCEPTED"},
    )

    _run_doctor_backfill()

    fresh = Case.objects.get(pk=legacy.pk)
    assert fresh.doctor_decided_by_id == doctor.pk
    latest = (
        CaseEvent.objects.filter(
            case=legacy,
            event_type__in=("CASE_STATUS_DOCTOR_ACCEPTED", "CASE_STATUS_DOCTOR_DENIED"),
        )
        .order_by("timestamp", "id")
        .last()
    )
    assert latest is not None
    assert fresh.doctor_decided_at == latest.timestamp


@pytest.mark.django_db
def test_backfill_fail_closed_null_actor(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """Backfill 0014 fail-closed: evento sem ator ⇒ campos ficam nulos."""
    doctor = user_factory("medico-backfill-nulo", (DOCTOR_ROLE,))
    legacy = _legacy_case_with_event(
        nir_user, None, accepted=True, patient_name="Paciente Backfill Nulo"
    )
    assert doctor.pk is not None

    _run_doctor_backfill()

    fresh = Case.objects.get(pk=legacy.pk)
    assert fresh.doctor_decided_at is None
    assert fresh.doctor_decided_by_id is None


@pytest.mark.django_db
def test_backfill_flow_lists_decision_in_decider_tab(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Fluxo do backfill 0014 (P2 do slice 005): seed pré-deploy (decisão só em
    evento+row, carimbos nulos, instante ``day_bounds``) → backfill → carimbos
    com o ator → decisão na aba «Decididos Hoje» do decisor certo, fora da de
    outro médico."""
    doctor = user_factory("medico-backfill-fluxo", (DOCTOR_ROLE,))
    other = user_factory("medico-backfill-outro", (DOCTOR_ROLE,))
    legacy = _legacy_case_with_event(
        nir_user, doctor, accepted=False, patient_name="Paciente Backfill Fluxo"
    )
    assert legacy.doctor_decided_at is None
    start, _ = local_day_bounds()
    decided_at = start + timedelta(hours=2)
    CaseEvent.objects.filter(case=legacy).update(timestamp=decided_at)
    CaseProcedure.objects.filter(case=legacy).update(doctor_decided_at=decided_at)

    _run_doctor_backfill()

    fresh = Case.objects.get(pk=legacy.pk)
    assert fresh.doctor_decided_at == decided_at
    assert fresh.doctor_decided_by_id == doctor.pk

    login_user(doctor, DOCTOR_ROLE)
    page, _ = _decided_bodies(client)

    assert str(fresh.case_id) in page
    assert "Paciente Backfill Fluxo" in page

    login_user(other, DOCTOR_ROLE)
    alien_page, _ = _decided_bodies(client)

    assert str(fresh.case_id) not in alien_page
    assert "Paciente Backfill Fluxo" not in alien_page
