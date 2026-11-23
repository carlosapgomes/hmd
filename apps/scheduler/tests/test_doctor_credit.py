"""Testes da linha de crédito do médico nas confirmações (slice 002, R2/R3/R5).

Cobre ``confirm_case_scheduling`` com decisor médico carimbado: corpo completo
da unidade 1 e da unidade 2 terminando com ``Médico responsável: ...``, a
degradação sem registro (só nome) e a omissão com decisor nulo (corpo-base
exato, sem falha). ``reply_unit_2_text()`` permanece intacta (R3) — a
composição acontece no serviço.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

import pytest
from django.utils import timezone

from apps.accounts.models import ProfessionalCouncil, Role, User
from apps.cases.models import Case, CaseStatus, MessageType
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.scheduler.services import (
    REPLY_UNIT_1_TEMPLATE,
    confirm_case_scheduling,
    reply_unit_2_text,
)

SCHEDULER_ROLE = "scheduler"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"

_DISPLAY_FORMAT = "%d/%m/%Y %H:%M"


def _make_user(username: str, role: str) -> User:
    """Cria usuário com um papel (papel criado se não existir)."""
    role_obj, _ = Role.objects.get_or_create(name=role)
    user = User.objects.create_user(username=username, password="senha-teste")
    user.roles.add(role_obj)
    return user


def _doctor_with_registration(username: str, *, number: str = "12345") -> User:
    """Médico Ana Souza com CRM (nome completo + registro)."""
    return User.objects.create_user(
        username=username,
        password="senha-teste",
        first_name="Ana",
        last_name="Souza",
        professional_council=ProfessionalCouncil.CRM,
        professional_council_number=number,
    )


def _scheduler_requested_case(created_by: User, decided_by: User) -> Case:
    """Caso aprovado pelo médico (``SCHEDULER_REQUESTED``) com decisor carimbado."""
    from apps.cases.models import CaseProcedure

    case = Case.objects.create(created_by=created_by)
    CaseProcedure.objects.create(case=case, procedure_type=ANGIO_TYPE, declared_by_nir=True)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    record_doctor_procedure_decisions(
        case, {ANGIO_TYPE: ("approved", "")}, user=decided_by, role=DOCTOR_ROLE
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.doctor_decided_by == decided_by
    return case


def _case_without_decider(created_by: User) -> Case:
    """Caso em ``SCHEDULER_REQUESTED`` SEM decisor (``user=None`` nas transições)."""
    case = Case.objects.create(created_by=created_by)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    case.record_doctor_decision(accepted=True, user=None, role=SYSTEM_ROLE)
    case.request_scheduling(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.doctor_decided_by is None
    return case


def _future_datetime(**kwargs: int) -> datetime:
    return timezone.now() + timedelta(**kwargs)


def _display_local(value: datetime) -> str:
    return timezone.localtime(value).strftime(_DISPLAY_FORMAT)


def _user_bodies(case: Case) -> list[str]:
    messages = case.communication_messages.filter(message_type=MessageType.USER)
    return [cast(Any, message).body for message in messages]


@pytest.mark.django_db
def test_confirm_unit_1_ends_with_credit_line() -> None:
    """R2: corpo U1 = template + ``\\n`` + linha com registro."""
    scheduler = _make_user("agendador-credito-u1", SCHEDULER_ROLE)
    doctor = _doctor_with_registration("medica-credito-u1")
    case = _scheduler_requested_case(scheduler, doctor)
    scheduled_at = _future_datetime(days=2)
    location = "Sala de hemodinâmica"

    confirm_case_scheduling(
        case,
        unit=1,
        scheduled_datetime=scheduled_at,
        scheduled_location=location,
        user=scheduler,
        role=SCHEDULER_ROLE,
    )

    base = REPLY_UNIT_1_TEMPLATE.format(
        location=location, scheduled_at=_display_local(scheduled_at)
    )
    assert _user_bodies(case) == [f"{base}\nMédico responsável: Ana Souza — CRM 12345"]


@pytest.mark.django_db
def test_confirm_unit_2_ends_with_credit_line() -> None:
    """R3: corpo U2 = texto-base + ``\\n`` + linha; base intacta."""
    assert reply_unit_2_text() == (
        "Recusar o relatório — caso agendado na Unidade 2, que comunicará a Secretaria"
    )
    scheduler = _make_user("agendador-credito-u2", SCHEDULER_ROLE)
    doctor = _doctor_with_registration("medica-credito-u2")
    case = _scheduler_requested_case(scheduler, doctor)

    confirm_case_scheduling(
        case,
        unit=2,
        scheduled_datetime=_future_datetime(days=2),
        scheduled_location="Unidade 2 (internet)",
        user=scheduler,
        role=SCHEDULER_ROLE,
    )

    assert _user_bodies(case) == [
        f"{reply_unit_2_text()}\nMédico responsável: Ana Souza — CRM 12345"
    ]


@pytest.mark.django_db
def test_confirm_without_registration_credit_name_only() -> None:
    """R5: médico sem registro → linha só com nome."""
    scheduler = _make_user("agendador-credito-sem", SCHEDULER_ROLE)
    doctor = User.objects.create_user(
        username="medica-credito-sem",
        password="senha-teste",
        first_name="Ana",
        last_name="Souza",
    )
    case = _scheduler_requested_case(scheduler, doctor)
    scheduled_at = _future_datetime(days=2)
    location = "Sala de hemodinâmica"

    confirm_case_scheduling(
        case,
        unit=1,
        scheduled_datetime=scheduled_at,
        scheduled_location=location,
        user=scheduler,
        role=SCHEDULER_ROLE,
    )

    base = REPLY_UNIT_1_TEMPLATE.format(
        location=location, scheduled_at=_display_local(scheduled_at)
    )
    assert _user_bodies(case) == [f"{base}\nMédico responsável: Ana Souza"]


@pytest.mark.django_db
def test_confirm_without_decider_omits_line() -> None:
    """R5: decisor nulo → corpo-base exato, postagem normal."""
    scheduler = _make_user("agendador-credito-nulo", SCHEDULER_ROLE)
    case = _case_without_decider(scheduler)
    scheduled_at = _future_datetime(days=2)
    location = "Sala de hemodinâmica"

    confirm_case_scheduling(
        case,
        unit=1,
        scheduled_datetime=scheduled_at,
        scheduled_location=location,
        user=scheduler,
        role=SCHEDULER_ROLE,
    )

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert _user_bodies(case) == [
        REPLY_UNIT_1_TEMPLATE.format(location=location, scheduled_at=_display_local(scheduled_at))
    ]
