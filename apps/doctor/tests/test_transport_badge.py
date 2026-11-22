"""Badge de transporte nos cards da fila médica (transport-badge, slice 002, R3/R5).

R3: cards das abas Pendentes E Decididos Hoje exibem o badge — ``mobile_icu``
com ênfase, ``basic`` neutro, ausente sem badge. R5 (paridade): rótulo
canônico do ``TransportType`` e classe do mapeamento único
``TRANSPORT_BADGE_CLASSES`` — o mesmo HTML exato afirmado em «meus casos»
e na fila do agendador.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseProcedure, CaseStatus, TransportType
from apps.cases.procedures import record_doctor_procedure_decisions

DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"

ANGIO_TYPE = "art_perif"

ICU_BADGE = '<span class="badge rounded-pill text-bg-warning">🚑 UTI Móvel</span>'
BASIC_BADGE = '<span class="badge rounded-pill text-bg-info">🚑 Ambulância Básica</span>'

_CARD_OPENING = '<div class="col-12" data-doctor-queue-card'


def _advance_to_awaiting_doctor(case: Case) -> None:
    """Dirige o caso pelas transições do pipeline até ``AWAITING_DOCTOR``."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _make_awaiting_case(
    created_by: User,
    procedure_types: Sequence[str],
    *,
    patient_name: str = "",
    transport_type: str | None = None,
) -> Case:
    """Caso em ``AWAITING_DOCTOR`` com os tipos declarados e o transporte."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )
    _advance_to_awaiting_doctor(case)
    Case.objects.filter(pk=case.pk).update(
        patient_name=patient_name,
        transport_type=transport_type,
    )
    case.refresh_from_db()
    return case


def _card_block(body: str, marker: object) -> str:
    """Fatia o card localizado pelo ``marker`` (uid do caso)."""
    anchor = body.index(str(marker))
    start = body.rfind(_CARD_OPENING, 0, anchor)
    assert start != -1
    end = body.find(_CARD_OPENING, anchor)
    return body[start : end if end != -1 else len(body)]


@pytest.mark.django_db
def test_pending_card_with_mobile_icu_shows_emphasis_badge(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R3/R5: pendente ``mobile_icu`` → badge «UTI Móvel» (``text-bg-warning``)."""
    case = _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente UTI Movel",
        transport_type=TransportType.MOBILE_ICU.value,
    )
    login_user(user_factory("medico-transporte-uti", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    card = _card_block(body, case.case_id)
    assert ICU_BADGE in card
    assert BASIC_BADGE not in card


@pytest.mark.django_db
def test_pending_card_with_basic_shows_neutral_badge(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R3/R5: pendente ``basic`` → badge «Ambulância Básica» (``text-bg-info``)."""
    case = _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente Basica",
        transport_type=TransportType.BASIC.value,
    )
    login_user(user_factory("medico-transporte-basica", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    card = _card_block(body, case.case_id)
    assert BASIC_BADGE in card
    assert ICU_BADGE not in card


@pytest.mark.django_db
def test_pending_card_without_transport_shows_no_badge(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R3: pendente sem transporte → nenhum badge, sem placeholder."""
    case = _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente Sem Transporte",
        transport_type=None,
    )
    login_user(user_factory("medico-transporte-ausente", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    card = _card_block(body, case.case_id)
    assert "🚑" not in card
    assert "UTI Móvel" not in card
    assert "Ambulância Básica" not in card
    assert "Paciente Sem Transporte" in card


@pytest.mark.django_db
def test_decided_card_with_mobile_icu_shows_emphasis_badge(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R3/R5: aba Decididos Hoje exibe o mesmo badge de ênfase."""
    doctor = user_factory("medico-transporte-decidido", (DOCTOR_ROLE,))
    case = _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente Decidido UTI",
        transport_type=TransportType.MOBILE_ICU.value,
    )
    record_doctor_procedure_decisions(
        case, {ANGIO_TYPE: ("approved", "")}, user=doctor, role=DOCTOR_ROLE
    )
    case.refresh_from_db()
    login_user(doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()

    card = _card_block(body, case.case_id)
    assert ICU_BADGE in card
