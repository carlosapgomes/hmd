"""Espera herdada na fila médica (slice 002, R3 — RED).

Cards/partial/polling e FIFO usam o marco efetivo (menor da cadeia);
``days_on_screen`` do relatório novo segue principal; criação intacta; abas
decididas intactas.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases import reappearance as reappearance_module
from apps.cases.models import Case, CaseProcedure, CaseStatus

_NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"


def _declared(case: Case, *procedure_types: str) -> None:
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)


def _awaiting(case: Case) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _identified(case: Case, *, name: str, number: str) -> None:
    case.patient_name = name
    case.agency_record_number = number
    case.save(update_fields=["patient_name", "agency_record_number"])


def _login(client: Client, doctor: User) -> None:
    client.force_login(doctor)
    session = client.session
    session["active_role"] = "doctor"
    session.save()


def _substituted_pair(nir_user: User, *, number: str, prior_days_ago: int = 5) -> Case:
    prior = Case.objects.create(created_by=nir_user)
    _declared(prior, ANGIO_TYPE)
    _identified(prior, name="Maria da Silva", number=number)
    _awaiting(prior)
    Case.objects.filter(pk=prior.pk).update(
        created_at=timezone.now() - timedelta(days=prior_days_ago)
    )
    prior.refresh_from_db()
    incoming = Case.objects.create(created_by=nir_user)
    _declared(incoming, ANGIO_TYPE)
    _identified(incoming, name="Maria da Silva", number=number)
    _awaiting(incoming)
    reappearance_module.resolve_medical_replacement(incoming)
    incoming.refresh_from_db()
    return incoming


@_NEW_MODE
@pytest.mark.django_db
def test_pending_card_uses_inherited_wait_in_html(
    client: Client, user_factory: Callable[..., User], nir_user: User
) -> None:
    """R3: card do novo exibe espera herdada (5 dias), não a criação de hoje."""
    doctor = user_factory("geral-espera", ("doctor",))
    incoming = _substituted_pair(nir_user, number="wait-1")
    assert incoming.created_at.date() == timezone.localdate()
    _login(client, doctor)
    response = client.get(reverse("doctor:queue"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Aguardando há" in content
    assert "5\xa0dias" in content or "5 dias" in content
    partial = client.get(reverse("doctor:queue_partial"))
    assert partial.status_code == 200
    partial_content = partial.content.decode()
    assert "Aguardando há" in partial_content
    assert "5\xa0dias" in partial_content or "5 dias" in partial_content


@_NEW_MODE
@pytest.mark.django_db
def test_fifo_uses_effective_marker_as_tiebreak(
    client: Client, user_factory: Callable[..., User], nir_user: User
) -> None:
    """R3: desempate FIFO pelo marco efetivo (herdado antigo antes de novo recente)."""
    doctor = user_factory("geral-fifo", ("doctor",))
    incoming = _substituted_pair(nir_user, number="fifo-1")
    fresh = Case.objects.create(created_by=nir_user)
    _declared(fresh, ANGIO_TYPE)
    _identified(fresh, name="Joao Pereira", number="fifo-2")
    _awaiting(fresh)
    _login(client, doctor)
    response = client.get(reverse("doctor:queue"))
    assert response.status_code == 200
    items = list(response.context["items"])
    assert [item["case_id"] for item in items] == [incoming.case_id, fresh.case_id]
    assert items[0]["waiting_since"] != incoming.created_at
    assert items[1]["waiting_since"] == fresh.created_at


@_NEW_MODE
@pytest.mark.django_db
def test_days_on_screen_stays_primary_and_creation_intact(
    client: Client, user_factory: Callable[..., User], nir_user: User
) -> None:
    """R3: ordenação principal por days_on_screen do novo; criação real intacta."""
    doctor = user_factory("geral-dias", ("doctor",))
    incoming = _substituted_pair(nir_user, number="dias-1")
    created = incoming.created_at
    Case.objects.filter(pk=incoming.pk).update(days_on_screen=1)
    other = Case.objects.create(created_by=nir_user)
    _declared(other, ANGIO_TYPE)
    _identified(other, name="Ana Souza", number="dias-9")
    _awaiting(other)
    Case.objects.filter(pk=other.pk).update(days_on_screen=30)
    incoming.refresh_from_db()
    assert incoming.created_at == created
    assert incoming.days_on_screen == 1
    _login(client, doctor)
    response = client.get(reverse("doctor:queue"))
    items = list(response.context["items"])
    assert [item["case_id"] for item in items] == [other.case_id, incoming.case_id]


@_NEW_MODE
@pytest.mark.django_db
def test_plain_case_keeps_created_at_wait(
    client: Client, user_factory: Callable[..., User], nir_user: User
) -> None:
    """R3: sem reapresentação — espera usa criação como antes."""
    doctor = user_factory("geral-simples", ("doctor",))
    case = Case.objects.create(created_by=nir_user)
    _declared(case, ANGIO_TYPE)
    _identified(case, name="Maria da Silva", number="simples-1")
    _awaiting(case)
    _login(client, doctor)
    response = client.get(reverse("doctor:queue"))
    items = list(response.context["items"])
    assert len(items) == 1
    assert items[0]["waiting_since"] == case.created_at
