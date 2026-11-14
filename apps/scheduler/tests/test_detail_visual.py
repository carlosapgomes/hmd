"""Paridade visual do detalhe/confirmação do agendador (slice 003).

R1/R3: o detalhe do agendador usa o layout ats-web (``detail-layout`` +
cards ``report-card``), com os forms embutidos de confirmação/negação em
cartões ``scheduler-decision-form-card`` — lock tokens, heartbeat, leave e
validações intactos (só classes/estrutura). Escopo estreito: marcadores
e conteúdo apenas.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseProcedure, CaseStatus
from apps.cases.procedures import record_doctor_procedure_decisions

SCHEDULER_ROLE = "scheduler"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"


def _make_scheduler_requested_case(
    created_by: User,
    decided_by: User,
    procedure_types: Sequence[str],
) -> Case:
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    record_doctor_procedure_decisions(
        case,
        {procedure_type: ("approved", "") for procedure_type in procedure_types},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    Case.objects.filter(pk=case.pk).update(
        patient_name="Paciente Visual Agendador",
        agency_record_number="44456",
    )
    case.summary_text = "Diagnóstico visual: dor torácica estável."
    case.save(update_fields=["summary_text"])
    case.refresh_from_db()
    return case


def _login(client: Client, user: User, role: str) -> None:
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


@pytest.mark.django_db
def test_scheduler_detail_uses_ats_web_report_layout(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: detalhe do agendador no layout ats-web, conteúdo idêntico."""
    doctor = user_factory("medico-visual-agendador", (DOCTOR_ROLE,))
    case = _make_scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-visual-detalhe", (SCHEDULER_ROLE,))
    _login(client, scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "detail-layout" in body
    assert "report-card" in body
    assert "summary-box" in body
    assert "Paciente Visual Agendador" in body
    assert "44456" in body
    assert "Diagnóstico visual: dor torácica estável." in body
    assert "Arteriografia periférica" in body
    assert "Aprovado" in body
    assert "Comunicações" in body


@pytest.mark.django_db
def test_scheduler_confirm_deny_use_decision_form_cards(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: confirmação/negação em cartões de decisão, reserva intacta."""
    doctor = user_factory("medico-visual-forms", (DOCTOR_ROLE,))
    case = _make_scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-visual-forms", (SCHEDULER_ROLE,))
    _login(client, scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "scheduler-decision-form-card" in body
    assert "Confirmar agendamento" in body
    assert "Negar agendamento" in body
    # Reserva/heartbeat/leave intactos (só classes/estrutura mudam).
    assert 'id="work-lock-config"' in body
    assert 'name="lock_token"' in body
    assert "Sair sem decidir" in body
    assert reverse("scheduler:case_confirm", args=[case.case_id]) in body
    assert reverse("scheduler:case_deny", args=[case.case_id]) in body
