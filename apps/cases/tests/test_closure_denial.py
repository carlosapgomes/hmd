"""Testes do fechamento por negativa médica (nir-result-closure, slice 001, R1/R2/R4).

Cobre o serviço ``post_doctor_denial_reply`` de ``apps/cases/closure.py``:
negativa total publica a resposta final ao NIR na thread — transição
``DOCTOR_DENIED → FINAL_REPLY_POSTED`` (evento ``CASE_STATUS_FINAL_REPLY_POSTED``
com ator/papel do médico) e comunicação ``user`` com ``DENIAL_REPLY_TEMPLATE``
interpolado contendo o label legível e o motivo de CADA procedimento negado —
no MESMO atomic (falha no post da comunicação desfaz transição, evento e
mensagem juntos); estado errado é recusado com ``ValueError`` nomeado sem
efeito (R1); estado certo sem rows negadas (dados inconsistentes) é recusado
sem efeito (R2). Cenários da matriz do slice:
``test_denial_reply_publishes_motives`` / ``test_denial_reply_wrong_state_rejected`` /
``test_denial_reply_no_rows_rejected``.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import User
from apps.cases.closure import DENIAL_REPLY_TEMPLATE, post_doctor_denial_reply
from apps.cases.events import case_status_event_type
from apps.cases.models import (
    Case,
    CaseCommunicationMessage,
    CaseProcedure,
    CaseStatus,
    DoctorDisposition,
    MessageType,
)
from apps.cases.procedures import record_doctor_procedure_decisions

DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"

# Tipos representativos do catálogo (same-label check da resposta final).
ANGIO_TYPE = "art_perif"
RADIO_TYPE = "nefrostomia"

ANGIO_LABEL = "Arteriografia periférica"
RADIO_LABEL = "Nefrostomia percutânea"

ANGIO_REASON = "sem indicação clínica"
RADIO_REASON = "risco elevado"


def _create_case_with_declared(created_by: User) -> Case:
    """Cria um caso com as rows declaradas dos dois tipos representativos."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in (ANGIO_TYPE, RADIO_TYPE):
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )
    return case


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
    case = _create_case_with_declared(created_by)
    _drive_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case,
        {
            ANGIO_TYPE: ("denied", ANGIO_REASON),
            RADIO_TYPE: ("denied", RADIO_REASON),
        },
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    return case


def _user_messages(case: Case) -> list[CaseCommunicationMessage]:
    """Mensagens manuais (user) da thread do caso, em ordem."""
    return list(case.communication_messages.filter(message_type=MessageType.USER))


# ── R1/R4: negativa total publica a resposta final ao NIR ───────────────────


@pytest.mark.django_db
def test_denial_reply_publishes_motives(nir_user: User, doctor_user: User) -> None:
    """R1/R4: resposta final por serviço → FINAL_REPLY_POSTED com evento e
    comunicação autoral trazendo label + motivo de cada procedimento negado."""
    case = _denied_case(created_by=nir_user, decided_by=doctor_user)

    post_doctor_denial_reply(case, user=doctor_user, role=DOCTOR_ROLE)

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED

    # Transição pública: evento CASE_STATUS_FINAL_REPLY_POSTED com o médico.
    final_event = case.events.get(event_type=case_status_event_type(CaseStatus.FINAL_REPLY_POSTED))
    assert final_event.actor == doctor_user
    assert final_event.actor_role == DOCTOR_ROLE
    assert final_event.payload == {
        "source": CaseStatus.DOCTOR_DENIED.value,
        "target": CaseStatus.FINAL_REPLY_POSTED.value,
    }

    # Comunicação user autoral do médico no mesmo atomic: a resposta é o
    # template canônico interpolado com cada procedimento negado — label
    # legível do catálogo + motivo real da row, na ordem canônica do catálogo.
    messages = _user_messages(case)
    assert len(messages) == 1
    message = messages[0]
    assert message.author == doctor_user
    assert message.author_role == DOCTOR_ROLE
    assert message.body == DENIAL_REPLY_TEMPLATE.format(
        denied_items=(f"- {ANGIO_LABEL}: {ANGIO_REASON}\n- {RADIO_LABEL}: {RADIO_REASON}")
    )


@pytest.mark.django_db
def test_denial_reply_mid_failure_rolls_back_everything(
    nir_user: User,
    doctor_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1 (atomic): falha no post da comunicação (após a transição) desfaz
    transição, evento e mensagem juntos — caso permanece DOCTOR_DENIED."""
    case = _denied_case(created_by=nir_user, decided_by=doctor_user)
    events_before = case.events.count()

    def flaky_create(**kwargs: object) -> CaseCommunicationMessage:
        raise RuntimeError("falha simulada no post da resposta final")

    monkeypatch.setattr(CaseCommunicationMessage.objects, "create", flaky_create)

    with pytest.raises(RuntimeError, match="post da resposta"):
        post_doctor_denial_reply(case, user=doctor_user, role=DOCTOR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.DOCTOR_DENIED
    assert fresh.events.count() == events_before
    assert not fresh.events.filter(
        event_type=case_status_event_type(CaseStatus.FINAL_REPLY_POSTED)
    ).exists()
    assert _user_messages(fresh) == []


# ── R1: estado errado recusado sem efeito ──────────────────────────────────


@pytest.mark.django_db
def test_denial_reply_wrong_state_rejected(nir_user: User, doctor_user: User) -> None:
    """R1: caso fora de DOCTOR_DENIED é recusado com erro nomeado, sem efeito."""
    case = _create_case_with_declared(nir_user)
    _drive_to_awaiting_doctor(case)
    events_before = case.events.count()

    with pytest.raises(ValueError, match="DOCTOR_DENIED"):
        post_doctor_denial_reply(case, user=doctor_user, role=DOCTOR_ROLE)

    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert case.events.count() == events_before
    assert not case.events.filter(
        event_type=case_status_event_type(CaseStatus.FINAL_REPLY_POSTED)
    ).exists()
    assert _user_messages(case) == []


# ── R2: estado certo sem rows negadas (dados inconsistentes) ───────────────


@pytest.mark.django_db
def test_denial_reply_no_rows_rejected(nir_user: User, doctor_user: User) -> None:
    """R2: DOCTOR_DENIED sem rows negadas → erro nomeado, sem efeito."""
    case = _create_case_with_declared(nir_user)
    _drive_to_awaiting_doctor(case)
    # Inconsistência deliberada: estado DOCTOR_DENIED via transição direta sem
    # rows negadas (todas PENDING) — nada a compor na resposta.
    case.record_doctor_decision(accepted=False, user=doctor_user, role=DOCTOR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    assert not CaseProcedure.objects.filter(
        case=case, doctor_disposition=DoctorDisposition.DENIED
    ).exists()
    events_before = case.events.count()

    with pytest.raises(ValueError, match="negad"):
        post_doctor_denial_reply(case, user=doctor_user, role=DOCTOR_ROLE)

    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    assert case.events.count() == events_before
    assert not case.events.filter(
        event_type=case_status_event_type(CaseStatus.FINAL_REPLY_POSTED)
    ).exists()
    assert _user_messages(case) == []
