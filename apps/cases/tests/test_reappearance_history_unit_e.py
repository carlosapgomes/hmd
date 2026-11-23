"""Histórico, payload e cadeia A→B→C (slice 002 unidade E, R1.3/R1.4 — RED).

Payload não-vácuo dos eventos correlacionados (chaves exatas, sem PHI),
FSM de 17 estados sem fontes novas, rótulos/badges da trilha, cadeia
operacional imutável com hrefs exatos guardados (criador/não-criador,
representante ativo e CLEANED, ciclo, negativos de guarda e re-render 400)
e separação explícita de ``prior_context`` da cadeia operacional.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases import reappearance as reappearance_module
from apps.cases.event_trail import build_event_trail_context, event_badge_css, event_label
from apps.cases.events import CaseEventType
from apps.cases.models import (
    Case,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    ReappearanceState,
    ResolutionKind,
)

_NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
_NAME = "Maria Sigilosa Silva"
_NUMBER = "PRIV-XYZ-987654"
_CNS = "207139200176053"
_APPLIED_KEYS = {
    "reappearance_id",
    "prior_case_id",
    "evidence_code",
    "resolution_kind",
    "revision_digest",
}
_SUPERSEDED_KEYS = {
    "reappearance_id",
    "incoming_case_id",
    "evidence_code",
    "resolution_kind",
    "revision_digest",
}


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


def _identity(case: Case, *, name: str = _NAME, number: str = _NUMBER, cns: str = _CNS) -> Case:
    case.patient_name = name
    case.agency_record_number = number
    case.patient_cns = cns
    case.origin_unit = "Hospital Geral"
    case.save(update_fields=["patient_name", "agency_record_number", "patient_cns", "origin_unit"])
    return case


def _make_case(owner: User, *, days_ago: int = 0) -> Case:
    case = Case.objects.create(created_by=owner)
    _declared(case, ANGIO_TYPE)
    _identity(case)
    _awaiting(case)
    if days_ago:
        Case.objects.filter(pk=case.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        case.refresh_from_db()
    return case


def _make_chain(owner: User) -> tuple[Case, Case, Case]:
    prior_a = _make_case(owner, days_ago=6)
    incoming_b = _make_case(owner)
    assert reappearance_module.resolve_medical_replacement(incoming_b) is not None
    incoming_c = _make_case(owner)
    assert reappearance_module.resolve_medical_replacement(incoming_c) is not None
    prior_a.refresh_from_db()
    incoming_b.refresh_from_db()
    incoming_c.refresh_from_db()
    return prior_a, incoming_b, incoming_c


def _login_nir(client: Client, user: User) -> None:
    client.force_login(user)
    session = client.session
    session["active_role"] = "nir"
    session.save()


def _detail_url(case: Case) -> str:
    route = (
        "intake:closed_case_detail" if case.status == CaseStatus.CLEANED else "intake:case_detail"
    )
    return reverse(route, args=[case.case_id])


@_NEW_MODE
@pytest.mark.django_db
def test_applied_superseded_payload_exact_keys_and_no_phi(nir_user: User) -> None:
    """R1.3/AUD-11: chaves exatas por evento, correlação e ausência de PHI."""
    prior, incoming = _make_chain(nir_user)[:2]
    link = CaseReappearance.objects.get(
        incoming_case=incoming, resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT
    )
    applied = incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).first()
    superseded = prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).first()
    assert applied is not None and superseded is not None
    assert set(applied.payload) == _APPLIED_KEYS
    assert set(superseded.payload) == _SUPERSEDED_KEYS
    assert applied.payload["reappearance_id"] == str(link.pk)
    assert superseded.payload["reappearance_id"] == str(link.pk)
    assert applied.payload["prior_case_id"] == str(prior.pk)
    assert superseded.payload["incoming_case_id"] == str(incoming.pk)
    for payload in (applied.payload, superseded.payload):
        assert payload["evidence_code"] == "occurrence_number"
        assert payload["resolution_kind"] == ResolutionKind.MEDICAL_REPLACEMENT.value
        assert payload["revision_digest"] == link.revision_digest
        blob = str(payload)
        assert _NAME not in blob
        assert _NUMBER not in blob
        assert _CNS not in blob
        assert "Hospital Geral" not in blob


@_NEW_MODE
@pytest.mark.django_db
def test_fsm_stays_17_states_and_refuses_non_awaiting_sources(nir_user: User) -> None:
    """R1.3: 17 estados, sem estados novos; só AWAITING_DOCTOR fecha."""
    from django_fsm import TransitionNotAllowed

    assert len(CaseStatus) == 17
    for status in (CaseStatus.NEW, CaseStatus.AWAITING_SCHEDULING, CaseStatus.CLEANED):
        case = Case.objects.create(created_by=nir_user)
        Case.objects.filter(pk=case.pk).update(status=status)
        case.refresh_from_db()
        with pytest.raises(TransitionNotAllowed):
            case.close_as_reappearance(user=None, role=SYSTEM_ROLE)


@_NEW_MODE
@pytest.mark.django_db
def test_trail_labels_badges_for_correlated_events(nir_user: User) -> None:
    """R1.3: rótulos/badges anti-drift dos dois eventos na trilha única."""
    prior = _make_case(nir_user, days_ago=6)
    incoming = _make_case(nir_user)
    assert reappearance_module.resolve_medical_replacement(incoming) is not None
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert event_label(CaseEventType.CASE_REAPPEARANCE_APPLIED) == "Reapresentação aplicada"
    assert (
        event_label(CaseEventType.CASE_REAPPEARANCE_SUPERSEDED)
        == "Caso substituído por reapresentação"
    )
    assert event_badge_css(CaseEventType.CASE_REAPPEARANCE_APPLIED) == "text-bg-secondary"
    assert event_badge_css(CaseEventType.CASE_REAPPEARANCE_SUPERSEDED) == "text-bg-secondary"
    incoming_types = [item["event_type"] for item in build_event_trail_context(incoming)]
    prior_types = [item["event_type"] for item in build_event_trail_context(prior)]
    assert CaseEventType.CASE_REAPPEARANCE_APPLIED in incoming_types
    assert CaseEventType.CASE_REAPPEARANCE_SUPERSEDED not in incoming_types
    assert CaseEventType.CASE_REAPPEARANCE_SUPERSEDED in prior_types
    assert CaseEventType.CASE_REAPPEARANCE_APPLIED not in prior_types


@_NEW_MODE
@pytest.mark.django_db
def test_history_exact_hrefs_follow_and_noncreator(
    client: Client, user_factory: Callable[[str, str], User], nir_user: User
) -> None:
    """R1.4/AUD-10a: hrefs exatos B (histórico) e C (ativo); follow 200; não-criador."""
    prior_a, incoming_b, incoming_c = _make_chain(nir_user)
    assert incoming_b.status == CaseStatus.CLEANED
    assert incoming_c.status == CaseStatus.AWAITING_DOCTOR
    expected_immediate = _detail_url(incoming_b)
    expected_representative = _detail_url(incoming_c)
    assert expected_immediate == reverse("intake:closed_case_detail", args=[incoming_b.case_id])
    assert expected_representative == reverse("intake:case_detail", args=[incoming_c.case_id])
    for viewer in (nir_user, user_factory("nir-nao-criador", "nir")):
        _login_nir(client, viewer)
        response = client.get(reverse("intake:closed_case_detail", args=[prior_a.case_id]))
        assert response.status_code == 200
        content = response.content.decode()
        assert f'href="{expected_immediate}"' in content
        assert f'href="{expected_representative}"' in content
        assert client.get(expected_immediate).status_code == 200
        assert client.get(expected_representative).status_code == 200
    # Elo B também mostra o representante C (elo imediato de B é C).
    response_b = client.get(reverse("intake:closed_case_detail", args=[incoming_b.case_id]))
    assert response_b.status_code == 200
    content_b = response_b.content.decode()
    assert f'href="{expected_representative}"' in content_b


@_NEW_MODE
@pytest.mark.django_db
def test_history_representative_cleaned_uses_closed_detail(client: Client, nir_user: User) -> None:
    """R1.4/AUD-10a: representante C também CLEANED → href do detalhe histórico."""
    prior_a, incoming_b, incoming_c = _make_chain(nir_user)
    Case.objects.filter(pk=incoming_c.pk).update(status=CaseStatus.CLEANED)
    incoming_c.refresh_from_db()
    expected_representative = reverse("intake:closed_case_detail", args=[incoming_c.case_id])
    _login_nir(client, nir_user)
    response = client.get(reverse("intake:closed_case_detail", args=[prior_a.case_id]))
    assert response.status_code == 200
    content = response.content.decode()
    assert f'href="{expected_representative}"' in content
    assert client.get(expected_representative).status_code == 200
    assert f'href="{_detail_url(incoming_b)}"' in content


@_NEW_MODE
@pytest.mark.django_db
def test_history_cycle_terminates_with_guarded_hrefs(client: Client, nir_user: User) -> None:
    """R1.4: ciclo A→B→A não trava; hrefs guardados apontam ao elo seguinte."""
    case_a = _make_case(nir_user)
    case_b = _make_case(nir_user)
    CaseReappearance.objects.create(
        incoming_case=case_b,
        prior_case=case_a,
        revision_digest="v1:cycle-ab",
        evidence_code="occurrence_number",
        state=ReappearanceState.APPLIED,
        resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
    )
    CaseReappearance.objects.create(
        incoming_case=case_a,
        prior_case=case_b,
        revision_digest="v1:cycle-ba",
        evidence_code="occurrence_number",
        state=ReappearanceState.APPLIED,
        resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
    )
    Case.objects.filter(pk__in=[case_a.pk, case_b.pk]).update(status=CaseStatus.CLEANED)
    case_a.refresh_from_db()
    case_b.refresh_from_db()
    assert reappearance_module.current_representative(case_a).pk == case_b.pk
    _login_nir(client, nir_user)
    response = client.get(reverse("intake:closed_case_detail", args=[case_a.case_id]))
    assert response.status_code == 200
    content = response.content.decode()
    assert f'href="{reverse("intake:closed_case_detail", args=[case_b.case_id])}"' in content


@_NEW_MODE
@pytest.mark.django_db
def test_history_unauthorized_and_out_of_scope_negatives(
    client: Client, user_factory: Callable[[str, str], User], nir_user: User
) -> None:
    """R1.4: papel errado 403, inexistente 404, ativo fora do escopo histórico 404."""
    prior_a, _incoming_b, incoming_c = _make_chain(nir_user)
    doctor = user_factory("medico-hist", "doctor")
    _login_nir(client, doctor)
    # force_login troca o usuário mas o papel ativo precisa ser doctor.
    session = client.session
    session["active_role"] = "doctor"
    session.save()
    assert (
        client.get(reverse("intake:closed_case_detail", args=[prior_a.case_id])).status_code == 403
    )
    _login_nir(client, nir_user)
    assert client.get(reverse("intake:closed_case_detail", args=[uuid.uuid4()])).status_code == 404
    # C ativo não está no universo histórico (sem issue) → 404 sem vazar.
    assert (
        client.get(reverse("intake:closed_case_detail", args=[incoming_c.case_id])).status_code
        == 404
    )


@_NEW_MODE
@pytest.mark.django_db
def test_closed_detail_post_ineligible_400_preserves_reappearance_link(
    client: Client, nir_user: User
) -> None:
    """R1.4/AUD-09: POST inelegível (400) não perde o vínculo de reapresentação."""
    prior_a, incoming_b, incoming_c = _make_chain(nir_user)
    _login_nir(client, nir_user)
    response = client.post(
        reverse("intake:closed_case_detail", args=[prior_a.case_id]), {"reason": "qualquer"}
    )
    assert response.status_code == 400
    content = response.content.decode()
    assert f'href="{_detail_url(incoming_b)}"' in content
    assert f'href="{_detail_url(incoming_c)}"' in content
    prior_a.refresh_from_db()
    assert prior_a.status == CaseStatus.CLEANED
    assert prior_a.post_schedule_issue_status == ""


@_NEW_MODE
@pytest.mark.django_db
def test_prior_context_alone_does_not_create_operational_chain(
    client: Client, nir_user: User
) -> None:
    """R1.4: ``prior_context`` sozinho não é cadeia operacional (sem card/link)."""
    case_a = _make_case(nir_user)
    other = _make_case(nir_user)
    CaseReappearance.objects.create(
        incoming_case=other,
        prior_case=case_a,
        revision_digest="v1:ctx-only",
        evidence_code="occurrence_number",
        state=ReappearanceState.APPLIED,
        resolution_kind=ResolutionKind.PRIOR_CONTEXT,
    )
    Case.objects.filter(pk=case_a.pk).update(status=CaseStatus.CLEANED)
    case_a.refresh_from_db()
    links = reappearance_module.historical_reappearance_links(case_a)
    assert links["immediate"] is None
    _login_nir(client, nir_user)
    response = client.get(reverse("intake:closed_case_detail", args=[case_a.case_id]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Reapresentação do mesmo pedido" not in content
    assert str(other.case_id) not in content


@_NEW_MODE
@pytest.mark.django_db
def test_prior_context_does_not_shadow_medical_immediate(nir_user: User) -> None:
    """R1.4: vínculo de contexto coexistente não consome nem desvia a cadeia de fila."""
    prior_a, incoming_b, _incoming_c = _make_chain(nir_user)
    other = _make_case(nir_user)
    CaseReappearance.objects.create(
        incoming_case=other,
        prior_case=prior_a,
        revision_digest="v1:ctx-shadow",
        evidence_code="occurrence_number",
        state=ReappearanceState.APPLIED,
        resolution_kind=ResolutionKind.PRIOR_CONTEXT,
    )
    links = reappearance_module.historical_reappearance_links(prior_a)
    immediate = links["immediate"]
    assert isinstance(immediate, Case)
    assert immediate.pk == incoming_b.pk
    assert reappearance_module.current_representative(prior_a).pk == _incoming_c.pk
