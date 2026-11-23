"""Card de prior-case no modo novo (slice 001, R4 — RED).

No modo novo o card exibe data, desfecho, motivo REAL e origem compartilhada
(``occurrence_number``/``patient_cns``/``nir_confirmed``) com a referência
desambiguada — e o GET do detalhe é read-only (nenhum evento criado).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import Case, CaseProcedure, DoctorDisposition
from apps.doctor.presenters import build_case_detail_context

DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"

_NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)

_CNS_A = "207139200176053"


def _supports_patient_cns() -> bool:
    """O campo ``Case.patient_cns`` já existe? (RED honesto pré-migration)."""
    return any(field.name == "patient_cns" for field in Case._meta.get_fields())


def _declared(case: Case, procedure_type: str) -> None:
    CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)


def _awaiting(case: Case) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == "AWAITING_DOCTOR"


def _login(client: Client, user: User, role: str) -> None:
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


@_NEW_MODE
@pytest.mark.django_db
def test_card_shows_cns_origin_and_disambiguated_reference(
    user_factory: Callable[..., User],
) -> None:
    """R4: prévio por CNS → card com origem CNS, motivo real e referência
    ``nº · <8 chars>`` (nunca uid completo)."""
    owner = user_factory("dono-card-cns", ("nir",))
    decided_at = timezone.now() - timedelta(days=5)
    prior = Case.objects.create(created_by=owner)
    _declared(prior, ANGIO_TYPE)
    prior.patient_name = "Maria da Silva"
    prior.agency_record_number = "44401"
    prior.save(update_fields=["patient_name", "agency_record_number"])
    if _supports_patient_cns():
        Case.objects.filter(pk=prior.pk).update(patient_cns=_CNS_A)
    CaseProcedure.objects.filter(case=prior, procedure_type=ANGIO_TYPE).update(
        doctor_disposition=DoctorDisposition.DENIED,
        doctor_reason="INR elevado — não liberar.",
        doctor_decided_at=decided_at,
    )

    current = Case.objects.create(created_by=owner)
    _declared(current, ANGIO_TYPE)
    _awaiting(current)
    current.patient_name = "Maria da Silva"
    current.agency_record_number = "44402"
    current.save(update_fields=["patient_name", "agency_record_number"])
    if _supports_patient_cns():
        Case.objects.filter(pk=current.pk).update(patient_cns=_CNS_A)
    current.refresh_from_db()

    context = cast(dict[str, Any], build_case_detail_context(current))

    assert len(context["prior_cases"]) == 1
    card = context["prior_cases"][0]
    assert card["procedure_type"] == ANGIO_TYPE
    assert card["decision_label"] == "Negado"
    assert card["reason"] == "INR elevado — não liberar."
    assert card["origin_label"] == "CNS"
    assert card["prior_case_reference"] == f"44401 · {str(prior.case_id)[:8]}"


@_NEW_MODE
@pytest.mark.django_db
def test_detail_get_creates_no_events_and_renders_origin(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R4/runtime: GET do detalhe renderiza a origem e não escreve eventos."""
    from django.urls import reverse

    doctor = user_factory("geral-prior-s1", (DOCTOR_ROLE,))
    decided_at = timezone.now() - timedelta(days=5)
    prior = Case.objects.create(created_by=nir_user)
    _declared(prior, ANGIO_TYPE)
    prior.patient_name = "Maria da Silva"
    prior.agency_record_number = "44411"
    prior.save(update_fields=["patient_name", "agency_record_number"])
    CaseProcedure.objects.filter(case=prior, procedure_type=ANGIO_TYPE).update(
        doctor_disposition=DoctorDisposition.DENIED,
        doctor_reason="Plaquetas baixas.",
        doctor_decided_at=decided_at,
    )
    current = Case.objects.create(created_by=nir_user)
    _declared(current, ANGIO_TYPE)
    _awaiting(current)
    current.patient_name = "Maria da Silva"
    current.agency_record_number = "44411"
    current.save(update_fields=["patient_name", "agency_record_number"])
    current.refresh_from_db()
    events_before = current.events.count()
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[current.case_id]))

    assert response.status_code == 200
    assert "Nº de ocorrência" in response.content.decode()
    current.refresh_from_db()
    assert current.events.count() == events_before
