"""Badge de transporte nos cards da fila do agendador (transport-badge, slice 002, R4/R5).

R4: cards das abas Pendentes E Processados Hoje exibem o badge —
``mobile_icu`` com ênfase, ``basic`` neutro, ausente sem badge. R5
(paridade): rótulo canônico do ``TransportType`` e classe do mapeamento
único ``TRANSPORT_BADGE_CLASSES`` — o mesmo HTML exato afirmado em «meus
casos» e na fila médica.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import Case, CaseProcedure, CaseStatus, TransportType
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.scheduler.services import confirm_case_scheduling

ANGIO_TYPE = "art_perif"
SCHEDULER_ROLE = "scheduler"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"

ICU_BADGE = '<span class="badge rounded-pill text-bg-warning">🚑 UTI Móvel</span>'
BASIC_BADGE = '<span class="badge rounded-pill text-bg-info">🚑 Ambulância Básica</span>'

_PROCESSING_STEPS: list[tuple[str, dict[str, object]]] = [
    ("start_pdf_extraction", {}),
    ("complete_pdf_extraction", {}),
    ("complete_anonymization", {}),
    ("complete_llm_extraction", {}),
    ("complete_llm_summarization", {}),
]


def _run_processing(case: Case) -> None:
    """Dirige o caso pelas transições do pipeline até ``AWAITING_DOCTOR``."""
    for operation, extra in _PROCESSING_STEPS:
        getattr(case, operation)(user=None, role=SYSTEM_ROLE, **extra)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _make_pending_case(
    created_by: User,
    decided_by: User,
    procedure_types: Sequence[str],
    *,
    patient_name: str = "",
    transport_type: str | None = None,
) -> Case:
    """Caso na fila do agendador (``SCHEDULER_REQUESTED``) com o transporte."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    _run_processing(case)
    record_doctor_procedure_decisions(
        case,
        {procedure_type: ("approved", "") for procedure_type in procedure_types},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    Case.objects.filter(pk=case.pk).update(
        patient_name=patient_name,
        transport_type=transport_type,
    )
    case.refresh_from_db()
    return case


def _card_wrapper(body: str, marker: object) -> str:
    """Fatia o card (wrapper ``data-scheduler-queue-card``) pelo marker."""
    anchor = body.index(str(marker))
    start = body.rindex("data-scheduler-queue-card", 0, anchor)
    start = body.rindex("<div", 0, start)
    end = body.find("data-scheduler-queue-card", anchor)
    end = body.rfind("<div", 0, end) if end != -1 else len(body)
    return body[start:end]


@pytest.mark.django_db
def test_pending_card_with_mobile_icu_shows_emphasis_badge(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4/R5: pendente ``mobile_icu`` → badge «UTI Móvel» (``text-bg-warning``)."""
    doctor = user_factory("medico-transporte-uti", (DOCTOR_ROLE,))
    case = _make_pending_case(
        nir_user,
        doctor,
        (ANGIO_TYPE,),
        patient_name="Paciente UTI Movel",
        transport_type=TransportType.MOBILE_ICU.value,
    )
    login_user(user_factory("agendador-transporte-uti", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    card = _card_wrapper(body, case.case_id)
    assert ICU_BADGE in card
    assert BASIC_BADGE not in card


@pytest.mark.django_db
def test_pending_card_with_basic_shows_neutral_badge(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4/R5: pendente ``basic`` → badge «Ambulância Básica» (``text-bg-info``)."""
    doctor = user_factory("medico-transporte-basica", (DOCTOR_ROLE,))
    case = _make_pending_case(
        nir_user,
        doctor,
        (ANGIO_TYPE,),
        patient_name="Paciente Basica",
        transport_type=TransportType.BASIC.value,
    )
    login_user(user_factory("agendador-transporte-basica", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    card = _card_wrapper(body, case.case_id)
    assert BASIC_BADGE in card
    assert ICU_BADGE not in card


@pytest.mark.django_db
def test_pending_card_without_transport_shows_no_badge(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4: pendente sem transporte → nenhum badge, sem placeholder."""
    doctor = user_factory("medico-transporte-ausente", (DOCTOR_ROLE,))
    case = _make_pending_case(
        nir_user,
        doctor,
        (ANGIO_TYPE,),
        patient_name="Paciente Sem Transporte",
        transport_type=None,
    )
    login_user(user_factory("agendador-transporte-ausente", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    card = _card_wrapper(body, case.case_id)
    assert "🚑" not in card
    assert "UTI Móvel" not in card
    assert "Ambulância Básica" not in card
    assert "Paciente Sem Transporte" in card


@pytest.mark.django_db
def test_processed_card_with_mobile_icu_shows_emphasis_badge(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4/R5: aba Processados Hoje exibe o mesmo badge de ênfase."""
    doctor = user_factory("medico-transporte-processado", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-transporte-processado", (SCHEDULER_ROLE,))
    case = _make_pending_case(
        nir_user,
        doctor,
        (ANGIO_TYPE,),
        patient_name="Paciente Processado UTI",
        transport_type=TransportType.MOBILE_ICU.value,
    )
    confirm_case_scheduling(
        case,
        unit=2,
        scheduled_datetime=timezone.now() + timedelta(days=2),
        scheduled_location="Sala de hemodinâmica",
        user=scheduler,
        role=SCHEDULER_ROLE,
    )
    case.refresh_from_db()
    login_user(scheduler, SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue"), {"tab": "processados"}).content.decode()

    card = _card_wrapper(body, case.case_id)
    assert ICU_BADGE in card
