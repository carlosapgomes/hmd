"""Fix-1 P1-04: GET do detalhe médico com origem CNS (modo novo).

Prévio identificado por CNS (regulações distintas): o GET renderiza o rótulo
de origem CNS com a referência desambiguada e não cria casos/eventos. O
comportamento já está correto — passa imediatamente (sem RED fabricado).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import Case, CaseProcedure, DoctorDisposition

DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"

_NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)

_CNS_A = "207139200176053"


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
def test_detail_get_renders_cns_origin_without_writes(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """P1-04: prévio por CNS (nº distintos) → GET 200 com 'CNS', zero casos/eventos novos."""
    doctor = user_factory("medico-fix1-cns", (DOCTOR_ROLE,))
    decided_at = timezone.now() - timedelta(days=5)
    prior = Case.objects.create(created_by=nir_user)
    _declared(prior, ANGIO_TYPE)
    prior.patient_name = "Maria da Silva"
    prior.agency_record_number = "55501"
    prior.save(update_fields=["patient_name", "agency_record_number"])
    Case.objects.filter(pk=prior.pk).update(patient_cns=_CNS_A)
    CaseProcedure.objects.filter(case=prior, procedure_type=ANGIO_TYPE).update(
        doctor_disposition=DoctorDisposition.DENIED,
        doctor_reason="Plaquetas baixas.",
        doctor_decided_at=decided_at,
    )

    current = Case.objects.create(created_by=nir_user)
    _declared(current, ANGIO_TYPE)
    _awaiting(current)
    current.patient_name = "MARIA DA SILVA"
    current.agency_record_number = "55502"
    current.save(update_fields=["patient_name", "agency_record_number"])
    Case.objects.filter(pk=current.pk).update(patient_cns=_CNS_A)
    current.refresh_from_db()

    cases_before = Case.objects.count()
    events_before = current.events.count()
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[current.case_id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "CNS" in content
    assert f"55501 · {str(prior.case_id)[:8]}" in content
    current.refresh_from_db()
    assert Case.objects.count() == cases_before
    assert current.events.count() == events_before
