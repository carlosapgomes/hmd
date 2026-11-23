"""Testes da linha de crédito do médico (registro-profissional, slice 002, R1/R4/R5).

Cobre o helper único ``doctor_credit_line`` (3 formas: com registro, sem
registro, decisor nulo) e a composição na negativa médica
(``post_doctor_denial_reply``) — corpo completo com registro e omissão sem
falha com decisor nulo.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import ProfessionalCouncil, User
from apps.cases.closure import DENIAL_REPLY_TEMPLATE, post_doctor_denial_reply
from apps.cases.doctor_credit import doctor_credit_line
from apps.cases.models import (
    Case,
    CaseProcedure,
    CaseStatus,
    DoctorDisposition,
    MessageType,
)
from apps.cases.procedures import record_doctor_procedure_decisions

DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"

ANGIO_TYPE = "art_perif"
RADIO_TYPE = "nefrostomia"
ANGIO_LABEL = "Arteriografia periférica"
RADIO_LABEL = "Nefrostomia percutânea"
ANGIO_REASON = "sem indicação clínica"
RADIO_REASON = "risco elevado"


def _doctor(
    username: str,
    *,
    first_name: str = "Ana",
    last_name: str = "Souza",
    council: str = "",
    number: str = "",
) -> User:
    """Médico com nome completo e, opcionalmente, registro profissional."""
    return User.objects.create_user(
        username=username,
        password="senha-teste",
        first_name=first_name,
        last_name=last_name,
        professional_council=council,
        professional_council_number=number,
    )


@pytest.mark.django_db
def test_credit_line_with_registration(doctor_user: User) -> None:
    """R1: com registro → ``Médico responsável: {nome} — {CRM N}``."""
    doctor = _doctor("medica-registro", council=ProfessionalCouncil.CRM, number="12345")
    case = Case.objects.create(created_by=doctor_user, doctor_decided_by=doctor)

    assert doctor_credit_line(case) == "Médico responsável: Ana Souza — CRM 12345"


@pytest.mark.django_db
def test_credit_line_strips_registration_defensively(doctor_user: User) -> None:
    """R1: ``.strip()`` defensivo ao ler o registro formatado (texto livre)."""
    doctor = _doctor("medica-strip", council=ProfessionalCouncil.CRM, number="12345 ")
    case = Case.objects.create(created_by=doctor_user, doctor_decided_by=doctor)

    assert doctor_credit_line(case) == "Médico responsável: Ana Souza — CRM 12345"


@pytest.mark.django_db
def test_credit_line_without_registration(doctor_user: User) -> None:
    """R1/R5: sem registro → linha só com nome, sem separador."""
    doctor = _doctor("medica-sem-registro")
    case = Case.objects.create(created_by=doctor_user, doctor_decided_by=doctor)

    assert doctor_credit_line(case) == "Médico responsável: Ana Souza"


@pytest.mark.django_db
def test_credit_line_null_decider(doctor_user: User) -> None:
    """R1/R5: decisor nulo → ``""`` (linha omitida, sem falha)."""
    case = Case.objects.create(created_by=doctor_user)

    assert case.doctor_decided_by is None
    assert doctor_credit_line(case) == ""


def _drive_to_awaiting_doctor(case: Case) -> None:
    """Dirige o caso pelas transições do pipeline até ``AWAITING_DOCTOR``."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _denied_case(*, created_by: User, decided_by: User) -> Case:
    """Caso em ``DOCTOR_DENIED`` com as duas rows negadas (serviço do 03)."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in (ANGIO_TYPE, RADIO_TYPE):
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    _drive_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: ("denied", ANGIO_REASON), RADIO_TYPE: ("denied", RADIO_REASON)},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    return case


@pytest.mark.django_db
def test_denial_reply_ends_with_credit_line(doctor_user: User, nir_user: User) -> None:
    """R4: corpo da negativa termina com a linha (após os itens negados)."""
    doctor = _doctor("medica-negativa", council=ProfessionalCouncil.CRM, number="12345")
    case = _denied_case(created_by=nir_user, decided_by=doctor)

    post_doctor_denial_reply(case, user=doctor, role=DOCTOR_ROLE)

    messages = list(case.communication_messages.filter(message_type=MessageType.USER))
    assert len(messages) == 1
    expected = DENIAL_REPLY_TEMPLATE.format(
        denied_items=(f"- {ANGIO_LABEL}: {ANGIO_REASON}\n- {RADIO_LABEL}: {RADIO_REASON}")
    )
    assert messages[0].body == f"{expected}\nMédico responsável: Ana Souza — CRM 12345"
    assert messages[0].author == doctor


@pytest.mark.django_db
def test_denial_reply_without_decider_omits_line(doctor_user: User, nir_user: User) -> None:
    """R5: decisor nulo → linha omitida; postagem acontece normalmente."""
    case = Case.objects.create(created_by=nir_user)
    for procedure_type in (ANGIO_TYPE, RADIO_TYPE):
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    _drive_to_awaiting_doctor(case)
    # Decisão direta (sem carimbar doctor_decided_by) + rows negadas manuais.
    case.record_doctor_decision(accepted=False, user=doctor_user, role=DOCTOR_ROLE)
    CaseProcedure.objects.filter(case=case, procedure_type=ANGIO_TYPE).update(
        doctor_disposition=DoctorDisposition.DENIED, doctor_reason=ANGIO_REASON
    )
    CaseProcedure.objects.filter(case=case, procedure_type=RADIO_TYPE).update(
        doctor_disposition=DoctorDisposition.DENIED, doctor_reason=RADIO_REASON
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    assert case.doctor_decided_by is None

    post_doctor_denial_reply(case, user=doctor_user, role=DOCTOR_ROLE)

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    messages = list(case.communication_messages.filter(message_type=MessageType.USER))
    assert len(messages) == 1
    assert messages[0].body == DENIAL_REPLY_TEMPLATE.format(
        denied_items=(f"- {ANGIO_LABEL}: {ANGIO_REASON}\n- {RADIO_LABEL}: {RADIO_REASON}")
    )
    assert "Médico responsável:" not in messages[0].body
