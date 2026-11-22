"""Contadores das abas das filas via context processor (slice 004, correção R2).

Os totais Pendentes/Decididos (médico) e Pendentes/Processados (agendador)
chegam aos ``_nav.html`` pelo context processor ``queue_counters`` — sob os
MESMOS guards das views (``role_required`` doctor/admin e scheduler/admin,
mais a matriz de subtipo do médico via ``DoctorAccess``) e com contagens
leves por estado, sem queries globais sem guard.

RED: o processor ainda não existe e as filas não exibem os totais.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.context_processors import queue_counters
from apps.accounts.models import DoctorSpecialty, Role, User
from apps.cases.models import Case, CaseProcedure, CaseStatus

PASSWORD = "senha-teste"
ANGIO_TYPE = "art_perif"
CARDIO_TYPE = "cat_cardiaco"


def _create_user(username: str, roles: Sequence[str]) -> User:
    user = User.objects.create_user(username=username, password=PASSWORD)
    for name in roles:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


def _login(client: Client, user: User, role: str) -> None:
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _make_case(
    created_by: User,
    status: CaseStatus,
    procedure_types: Sequence[str] = (),
) -> Case:
    """Caso no estado informado (update por queryset — sem FSM, só contagem)."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )
    Case.objects.filter(pk=case.pk).update(status=status)
    case.refresh_from_db()
    return case


def _request_for(user: User, role: str) -> object:
    request = RequestFactory().get("/")
    request.user = user
    request.session = {"active_role": role}  # type: ignore[assignment]
    return request


def _assign_specialties(user: User, names: Sequence[str]) -> None:
    for name in names:
        DoctorSpecialty.objects.get_or_create(name=name)
    user.specialties.set(DoctorSpecialty.objects.filter(name__in=names))


@pytest.mark.django_db
def test_doctor_counters_nonzero_generalist() -> None:
    """Médico generalista: 2 aguardando + 1 decisão de hoje (totais, não por subtipo)."""
    creator = _create_user("cnt-nir-doc", ("nir",))
    doctor = _create_user("cnt-doc", ("doctor",))
    _make_case(creator, CaseStatus.AWAITING_DOCTOR, (ANGIO_TYPE,))
    _make_case(creator, CaseStatus.AWAITING_DOCTOR, (CARDIO_TYPE,))
    decided = _make_case(creator, CaseStatus.DOCTOR_DENIED, (ANGIO_TYPE,))
    # Slice 005: decididos conta a janela do dia por decisor (não o status).
    Case.objects.filter(pk=decided.pk).update(
        doctor_decided_at=timezone.now(), doctor_decided_by=doctor
    )

    assert queue_counters(_request_for(doctor, "doctor")) == {  # type: ignore[arg-type]
        "doctor_pending_count": 2,
        "doctor_decided_count": 1,
    }


@pytest.mark.django_db
def test_doctor_counters_scoped_by_specialty() -> None:
    """Especialista: os totais respeitam a matriz de subtipo (só angio)."""
    creator = _create_user("cnt-nir-esp", ("nir",))
    doctor = _create_user("cnt-doc-esp", ("doctor",))
    _assign_specialties(doctor, ("angio",))
    _make_case(creator, CaseStatus.AWAITING_DOCTOR, (ANGIO_TYPE,))
    _make_case(creator, CaseStatus.AWAITING_DOCTOR, (ANGIO_TYPE,))
    _make_case(creator, CaseStatus.AWAITING_DOCTOR, (CARDIO_TYPE,))
    denied = _make_case(creator, CaseStatus.DOCTOR_DENIED, (CARDIO_TYPE,))
    # Decisão de hoje do próprio médico, mas fora da matriz (cardio) ⇒ fora.
    Case.objects.filter(pk=denied.pk).update(
        doctor_decided_at=timezone.now(), doctor_decided_by=doctor
    )

    assert queue_counters(_request_for(doctor, "doctor")) == {  # type: ignore[arg-type]
        "doctor_pending_count": 2,
        "doctor_decided_count": 0,
    }


@pytest.mark.django_db
def test_doctor_counter_counts_team_decisions_in_viewer_subtype() -> None:
    """R4: o badge conta decisões de outro decisor no subtipo do visualizador.

    Coerente com a aba: decisão de hoje por outro médico em subtipo acessível
    conta; fora do acesso (cardio) e decisor nulo (fail-closed) não contam.
    """
    creator = _create_user("cnt-nir-equipe", ("nir",))
    viewer = _create_user("cnt-doc-equipe", ("doctor",))
    _assign_specialties(viewer, ("angio",))
    other = _create_user("cnt-doc-equipe-outro", ("doctor",))
    team = _make_case(creator, CaseStatus.SCHEDULER_REQUESTED, (ANGIO_TYPE,))
    Case.objects.filter(pk=team.pk).update(
        doctor_decided_at=timezone.now(), doctor_decided_by=other
    )
    out = _make_case(creator, CaseStatus.SCHEDULER_REQUESTED, (CARDIO_TYPE,))
    Case.objects.filter(pk=out.pk).update(doctor_decided_at=timezone.now(), doctor_decided_by=other)
    null = _make_case(creator, CaseStatus.SCHEDULER_REQUESTED, (ANGIO_TYPE,))
    Case.objects.filter(pk=null.pk).update(doctor_decided_at=timezone.now(), doctor_decided_by=None)

    assert queue_counters(_request_for(viewer, "doctor")) == {  # type: ignore[arg-type]
        "doctor_pending_count": 0,
        "doctor_decided_count": 1,
    }


@pytest.mark.django_db
def test_scheduler_counters_nonzero() -> None:
    """Agendador: 2 aguardando + 1 decisão de hoje (confirmação ou negação)."""
    creator = _create_user("cnt-nir-sch", ("nir",))
    scheduler = _create_user("cnt-sch", ("scheduler",))
    _make_case(creator, CaseStatus.SCHEDULER_REQUESTED)
    _make_case(creator, CaseStatus.AWAITING_SCHEDULING)
    processed = _make_case(creator, CaseStatus.FINAL_REPLY_POSTED)
    # Slice 005: processados conta a janela do dia por decisor (não o status).
    Case.objects.filter(pk=processed.pk).update(
        scheduling_decided_at=timezone.now(), scheduling_decided_by=scheduler
    )

    assert queue_counters(_request_for(scheduler, "scheduler")) == {  # type: ignore[arg-type]
        "scheduler_pending_count": 2,
        "scheduler_processed_count": 1,
    }


@pytest.mark.django_db
def test_scheduler_counter_counts_team_decisions() -> None:
    """R4: o badge conta decisões de outro agendador, coerente com a aba.

    Decisão de hoje por outro agendador conta; decisor nulo (fail-closed)
    e decisão de ontem não contam.
    """
    creator = _create_user("cnt-nir-equipe-sch", ("nir",))
    viewer = _create_user("cnt-sch-equipe", ("scheduler",))
    other = _create_user("cnt-sch-equipe-outro", ("scheduler",))
    team = _make_case(creator, CaseStatus.FINAL_REPLY_POSTED)
    Case.objects.filter(pk=team.pk).update(
        scheduling_decided_at=timezone.now(), scheduling_decided_by=other
    )
    null = _make_case(creator, CaseStatus.FINAL_REPLY_POSTED)
    Case.objects.filter(pk=null.pk).update(
        scheduling_decided_at=timezone.now(), scheduling_decided_by=None
    )
    old = _make_case(creator, CaseStatus.FINAL_REPLY_POSTED)
    Case.objects.filter(pk=old.pk).update(
        scheduling_decided_at=timezone.now() - timedelta(days=1, hours=2),
        scheduling_decided_by=other,
    )

    assert queue_counters(_request_for(viewer, "scheduler")) == {  # type: ignore[arg-type]
        "scheduler_pending_count": 0,
        "scheduler_processed_count": 1,
    }


@pytest.mark.django_db
def test_admin_counters_cover_both_areas() -> None:
    """Admin: totais das duas filas (doctor + scheduler) sob os dois guards."""
    creator = _create_user("cnt-nir-adm", ("nir",))
    admin = _create_user("cnt-adm", ("admin",))
    _make_case(creator, CaseStatus.AWAITING_DOCTOR, (ANGIO_TYPE,))
    decided = _make_case(creator, CaseStatus.SCHEDULER_REQUESTED)
    _make_case(creator, CaseStatus.SCHEDULER_REQUESTED)
    # Slice 005: a aba decididos do médico é a janela do dia por decisor —
    # o caso decidido hoje pelo admin conta, mesmo fora de AWAITING_DOCTOR.
    Case.objects.filter(pk=decided.pk).update(
        doctor_decided_at=timezone.now(), doctor_decided_by=admin
    )
    scheduled = _make_case(creator, CaseStatus.FINAL_REPLY_POSTED)
    Case.objects.filter(pk=scheduled.pk).update(
        scheduling_decided_at=timezone.now(), scheduling_decided_by=admin
    )

    assert queue_counters(_request_for(admin, "admin")) == {  # type: ignore[arg-type]
        "doctor_pending_count": 1,
        "doctor_decided_count": 1,
        "scheduler_pending_count": 2,
        "scheduler_processed_count": 1,
    }


@pytest.mark.django_db
def test_nir_and_anonymous_have_no_queue_counters() -> None:
    """Fora dos guards (nir/anônimo): nenhum total, nenhuma query de fila."""
    nir = _create_user("cnt-nir", ("nir",))
    assert queue_counters(_request_for(nir, "nir")) == {}  # type: ignore[arg-type]

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = {"active_role": ""}  # type: ignore[assignment]
    assert queue_counters(request) == {}


@pytest.mark.django_db
def test_doctor_queue_page_shows_nonzero_badges(client: Client) -> None:
    """A fila médica exibe os totais não-zero nas abas (badge visível)."""
    creator = _create_user("cnt-nir-page", ("nir",))
    doctor = _create_user("cnt-doc-page", ("doctor",))
    _make_case(creator, CaseStatus.AWAITING_DOCTOR, (ANGIO_TYPE,))
    _make_case(creator, CaseStatus.AWAITING_DOCTOR, (CARDIO_TYPE,))
    decided = _make_case(creator, CaseStatus.DOCTOR_DENIED, (ANGIO_TYPE,))
    Case.objects.filter(pk=decided.pk).update(
        doctor_decided_at=timezone.now(), doctor_decided_by=doctor
    )
    _login(client, doctor, "doctor")

    body = client.get(reverse("doctor:queue")).content.decode()

    assert 'data-count="2"' in body
    assert 'data-count="1"' in body


@pytest.mark.django_db
def test_scheduler_queue_page_shows_nonzero_badges(client: Client) -> None:
    """A fila do agendador exibe os totais não-zero nas abas (badge visível)."""
    creator = _create_user("cnt-nir-spage", ("nir",))
    scheduler = _create_user("cnt-sch-page", ("scheduler",))
    _make_case(creator, CaseStatus.SCHEDULER_REQUESTED)
    _make_case(creator, CaseStatus.SCHEDULER_REQUESTED)
    processed = _make_case(creator, CaseStatus.FINAL_REPLY_POSTED)
    Case.objects.filter(pk=processed.pk).update(
        scheduling_decided_at=timezone.now(), scheduling_decided_by=scheduler
    )
    _login(client, scheduler, "scheduler")

    body = client.get(reverse("scheduler:queue")).content.decode()

    assert 'data-count="2"' in body
    assert 'data-count="1"' in body
