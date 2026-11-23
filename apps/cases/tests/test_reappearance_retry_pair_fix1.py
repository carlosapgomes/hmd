"""Fix-1 P1 (R1/R4): retry vinculado ao par registrado.

Regressão: um retry pendente NÃO pode aplicar o vínculo antigo ao encerrar um
caso diferente quando o prior original sai de elegibilidade e outro plausível
vira candidato único. O efeito pendente deve terminar como inelegível honesto
(``state_ineligible`` + evento técnico único), sem fechar nenhum caso, sem
reatribuir o vínculo e sem consumir revisão alheia.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.cases import reappearance as reappearance_module
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock
from apps.cases.models import (
    Case,
    CaseEvent,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    ReappearanceState,
)

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)

SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
_PAIR_NUMBER = "fix1-4471"


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


def _identity(case: Case, *, number: str) -> None:
    case.patient_name = "Maria da Silva"
    case.agency_record_number = number
    case.patient_cns = ""
    case.origin_unit = ""
    case.patient_age = None
    case.save(
        update_fields=[
            "patient_name",
            "agency_record_number",
            "patient_cns",
            "origin_unit",
            "patient_age",
        ]
    )


@NEW_MODE
@pytest.mark.django_db
def test_retry_nao_aplica_vinculo_antigo_ao_fechar_caso_diferente(
    nir_user: User,
) -> None:
    """P1: retry preso ao par; candidato trocado → inelegível, sem fechar nada."""
    prior_old = Case.objects.create(created_by=nir_user)
    _declared(prior_old, ANGIO_TYPE)
    _identity(prior_old, number=_PAIR_NUMBER)
    _awaiting(prior_old)
    Case.objects.filter(pk=prior_old.pk).update(created_at=timezone.now() - timedelta(days=5))
    prior_old.refresh_from_db()
    incoming = Case.objects.create(created_by=nir_user)
    _declared(incoming, ANGIO_TYPE)
    _identity(incoming, number=_PAIR_NUMBER)
    _awaiting(incoming)

    doctor = User.objects.create_user(username="med-fix1-par", password="x")
    claim_case_lock(prior_old, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.PENDING
    assert link.prior_case_id == prior_old.pk
    revision = link.revision_digest
    attempt = link.attempt_count

    # Prior registrado sai de elegibilidade (correção do nº — padrão de fixture).
    prior_old.agency_record_number = "fix1-divergente"
    prior_old.save(update_fields=["agency_record_number"])

    # Outro plausível com a identidade original, ordenado como descobrível.
    prior_new = Case.objects.create(created_by=nir_user)
    _declared(prior_new, ANGIO_TYPE)
    _identity(prior_new, number=_PAIR_NUMBER)
    _awaiting(prior_new)
    Case.objects.filter(pk=prior_new.pk).update(created_at=incoming.created_at - timedelta(days=1))
    prior_new.refresh_from_db()

    # Não-vácuo: o substituto existe/casa e o vínculo ainda nomeia o original.
    replacement = reappearance_module.find_medical_replacement_candidate(incoming)
    assert replacement is not None
    assert replacement.pk == prior_new.pk
    assert link.prior_case_id == prior_old.pk

    # Retry vencido com revisão/tentativa válidas.
    CaseReappearance.objects.filter(pk=link.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    link.refresh_from_db()
    reappearance_module.handle_reappearance_retry(str(link.pk), revision, attempt)

    link.refresh_from_db()
    prior_old.refresh_from_db()
    prior_new.refresh_from_db()
    incoming.refresh_from_db()
    assert prior_new.status == CaseStatus.AWAITING_DOCTOR
    assert prior_old.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert link.state == ReappearanceState.PENDING
    assert link.prior_case_id == prior_old.pk
    assert link.revision_digest == revision
    assert not incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
    assert not prior_new.events.filter(
        event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED
    ).exists()
    assert not prior_old.events.filter(
        event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED
    ).exists()
    assert incoming.reappearance_wait_started_at is None
    assert prior_old.lock_token is not None
    assert link.last_reason_code == reappearance_module.REASON_STATE_INELIGIBLE
    assert (
        incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_RETRY_EXHAUSTED).count()
        == 1
    )

    # Repetição idempotente: sem novo evento nem mudança de estado.
    events_before = CaseEvent.objects.count()
    reappearance_module.handle_reappearance_retry(str(link.pk), revision, attempt)
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.prior_case_id == prior_old.pk
    assert link.revision_digest == revision
    assert CaseEvent.objects.count() == events_before
    prior_new.refresh_from_db()
    assert prior_new.status == CaseStatus.AWAITING_DOCTOR
