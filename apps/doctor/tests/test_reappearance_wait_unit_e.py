"""Espera herdada cadeia de 3 + ordenação oficial (slice 002 unidade E, R3.1/R3.2).

Tempo congelado via timestamps determinísticos e serviço real A→B→C:
menor marco herdado, ``created_at``/``days_on_screen`` intactos, fila
completa e partial com o marco, ``days_on_screen`` NOVO como chave
principal (incl. nulos e empates), FIFO pelo marco efetivo próprio,
sem regressão de caso simples e aba «Decididos Hoje» intacta.
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


def _make(owner: User, *, name: str, number: str, days_ago: int = 0) -> Case:
    case = Case.objects.create(created_by=owner)
    _declared(case, ANGIO_TYPE)
    _identified(case, name=name, number=number)
    _awaiting(case)
    if days_ago:
        Case.objects.filter(pk=case.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        case.refresh_from_db()
    return case


def _make_chain3(owner: User, *, number: str, prior_days_ago: int = 5) -> tuple[Case, Case, Case]:
    prior_a = _make(owner, name="Maria da Silva", number=number, days_ago=prior_days_ago)
    incoming_b = _make(owner, name="Maria da Silva", number=number)
    assert reappearance_module.resolve_medical_replacement(incoming_b) is not None
    incoming_c = _make(owner, name="Maria da Silva", number=number)
    assert reappearance_module.resolve_medical_replacement(incoming_c) is not None
    prior_a.refresh_from_db()
    incoming_b.refresh_from_db()
    incoming_c.refresh_from_db()
    return prior_a, incoming_b, incoming_c


@_NEW_MODE
@pytest.mark.django_db
def test_chain3_frozen_marker_service_and_http_full_partial(
    client: Client, user_factory: Callable[..., User], nir_user: User
) -> None:
    """R3.1: A→B→C pelo serviço real; marco congelado de A, criação intacta, HTML."""
    doctor = user_factory("geral-cadeia3", ("doctor",))
    prior_a, incoming_b, incoming_c = _make_chain3(nir_user, number="cadeia3-1")
    frozen_marker = prior_a.created_at
    b_created = incoming_b.created_at
    c_created = incoming_c.created_at
    Case.objects.filter(pk=incoming_c.pk).update(days_on_screen=3)
    incoming_c.refresh_from_db()
    assert incoming_b.reappearance_wait_started_at == frozen_marker
    assert incoming_c.reappearance_wait_started_at == frozen_marker
    assert incoming_b.created_at == b_created
    assert incoming_c.created_at == c_created
    assert incoming_c.days_on_screen == 3
    _login(client, doctor)
    for url in (reverse("doctor:queue"), reverse("doctor:queue_partial")):
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "Aguardando há" in content
        assert "5\xa0dias" in content or "5 dias" in content
        items = list(response.context["items"])
        assert [item["case_id"] for item in items] == [incoming_c.case_id]
        assert items[0]["waiting_since"] == frozen_marker
        assert items[0]["days_on_screen"] == 3


@_NEW_MODE
@pytest.mark.django_db
def test_days_on_screen_primary_nulls_and_ties_fifo_by_effective_marker(
    client: Client, user_factory: Callable[..., User], nir_user: User
) -> None:
    """R3.2/AUD-10b: days desc nulls-last; empate FIFO pelo marco efetivo."""
    doctor = user_factory("geral-ordem-e", ("doctor",))
    _prior_a, _incoming_b, chain_c = _make_chain3(nir_user, number="ordem-e-1", prior_days_ago=9)
    # chain_c: days None, marco efetivo há 9 dias, criado hoje.
    tie_old = _make(nir_user, name="Ana Souza", number="ordem-e-2", days_ago=8)
    tie_new = _make(nir_user, name="Joao Pereira", number="ordem-e-3", days_ago=3)
    high = _make(nir_user, name="Beto Lima", number="ordem-e-4")
    plain_old = _make(nir_user, name="Ceci Ramos", number="ordem-e-5", days_ago=6)
    plain_fresh = _make(nir_user, name="Duda Reis", number="ordem-e-6")
    Case.objects.filter(pk=tie_old.pk).update(days_on_screen=4)
    Case.objects.filter(pk=tie_new.pk).update(days_on_screen=4)
    Case.objects.filter(pk=high.pk).update(days_on_screen=8)
    _login(client, doctor)
    response = client.get(reverse("doctor:queue"))
    assert response.status_code == 200
    items = list(response.context["items"])
    assert [item["case_id"] for item in items] == [
        high.case_id,
        tie_old.case_id,
        tie_new.case_id,
        chain_c.case_id,
        plain_old.case_id,
        plain_fresh.case_id,
    ]
    by_id = {item["case_id"]: item for item in items}
    assert by_id[chain_c.case_id]["waiting_since"] == _prior_a.created_at
    assert by_id[chain_c.case_id]["waiting_since"] != chain_c.created_at
    assert by_id[plain_fresh.case_id]["waiting_since"] == plain_fresh.created_at
    assert by_id[plain_fresh.case_id]["days_on_screen"] is None


@_NEW_MODE
@pytest.mark.django_db
def test_plain_case_wait_regression(
    client: Client, user_factory: Callable[..., User], nir_user: User
) -> None:
    """R3.2: caso simples sem vínculo mantém espera na própria criação."""
    doctor = user_factory("geral-simples-e", ("doctor",))
    case = _make(nir_user, name="Maria da Silva", number="simples-e-1", days_ago=3)
    assert case.reappearance_wait_started_at is None
    _login(client, doctor)
    response = client.get(reverse("doctor:queue"))
    assert response.status_code == 200
    items = list(response.context["items"])
    assert [item["case_id"] for item in items] == [case.case_id]
    assert items[0]["waiting_since"] == case.created_at
    content = response.content.decode()
    assert "3\xa0dias" in content or "3 dias" in content


@_NEW_MODE
@pytest.mark.django_db
def test_decided_today_unchanged_by_substitution(
    client: Client, user_factory: Callable[..., User], nir_user: User
) -> None:
    """R3.2/AUD-10b: «Decididos Hoje» por decisor/data; substituído segue pendente."""
    doctor = user_factory("geral-decididos-e", ("doctor",))
    _prior_a, _incoming_b, chain_c = _make_chain3(nir_user, number="decididos-e-1")
    now = timezone.now()
    decided_late = _make(nir_user, name="Ana Souza", number="decididos-e-2")
    decided_early = _make(nir_user, name="Joao Pereira", number="decididos-e-3")
    Case.objects.filter(pk=decided_late.pk).update(
        status=CaseStatus.DOCTOR_DENIED,
        doctor_decided_by=doctor,
        doctor_decided_at=now,
    )
    Case.objects.filter(pk=decided_early.pk).update(
        status=CaseStatus.DOCTOR_ACCEPTED,
        doctor_decided_by=doctor,
        doctor_decided_at=now - timedelta(hours=2),
    )
    _login(client, doctor)
    decided_response = client.get(reverse("doctor:queue"), {"tab": "decididos"})
    assert decided_response.status_code == 200
    decided_items = list(decided_response.context["items"])
    assert [item["case_id"] for item in decided_items] == [
        decided_late.case_id,
        decided_early.case_id,
    ]
    assert chain_c.case_id not in [item["case_id"] for item in decided_items]
    pending_response = client.get(reverse("doctor:queue"))
    pending_items = list(pending_response.context["items"])
    assert [item["case_id"] for item in pending_items] == [chain_c.case_id]
