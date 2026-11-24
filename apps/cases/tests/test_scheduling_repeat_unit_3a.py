"""Reenvio na fila de agendamento — serviço de domínio (S3 unidade 3A, R1.3/R1.4/R2.1–R2.3).

Cobre: FSM ``close_as_reappearance`` com três fontes (17 estados intactos),
disposição separada ``close_incoming_as_scheduling_repeat`` (retém SEMPRE),
seleção conservadora D1/D3 com original no agendamento, regra D5 de lease
(humana preservada; só worker alheio adia), atomic pós-LLM1 (mutex antes de
row lock, deriva ×3), retry/reentrada/dedupe/limites/terminais/comando para
``resolution_kind=scheduling_repeat`` — sempre sem chamada LLM.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from django_fsm import TransitionNotAllowed
from django_q.models import Schedule

from apps.accounts.models import User
from apps.attachments.models import CaseAttachment
from apps.cases import closure as closure_module
from apps.cases import reappearance as reappearance_module
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock
from apps.cases.models import (
    Case,
    CaseDocument,
    CaseEvent,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    ReappearanceState,
    ResolutionKind,
)

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
FLAG_OFF = override_settings(CASE_REAPPEARANCE_ENABLED=False)
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
OTHER_TYPE = "cat_cardiaco"
_NAME = "Maria da Silva"
_NUMBER = "sr-3a-4417"
_CNS_A = "207139200176053"
_CNS_B = "605932160152243"

_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _llm_forbidden() -> object:
    raise AssertionError("scheduling_repeat/retry nunca chama LLM")


NO_LLM = override_settings(LLM_CLIENT_FACTORY=_llm_forbidden)


@pytest.fixture(autouse=True)
def _pipeline_dispatch_paused() -> Iterator[None]:
    with override_settings(LLM_RUN_TASKS_INLINE=False, ATTACHMENTS_RUN_TASKS_INLINE=False):
        yield


# ── helpers ─────────────────────────────────────────────────────────────────


def _declared(case: Case, *procedure_types: str) -> None:
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)


def _identity(
    case: Case,
    *,
    name: str = _NAME,
    number: str = _NUMBER,
    cns: str = "",
    origin: str = "",
    age: int | None = None,
) -> Case:
    case.patient_name = name
    case.agency_record_number = number
    case.patient_cns = cns
    case.origin_unit = origin
    case.patient_age = age
    case.save(
        update_fields=[
            "patient_name",
            "agency_record_number",
            "patient_cns",
            "origin_unit",
            "patient_age",
        ]
    )
    return case


def _to_extracting(case: Case) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.LLM_EXTRACTING


def _to_awaiting(case: Case) -> None:
    _to_extracting(case)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _backdate(case: Case, *, days_ago: int = 5) -> Case:
    Case.objects.filter(pk=case.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    case.refresh_from_db()
    return case


def _scheduling_prior(
    owner: User,
    *,
    status: CaseStatus = CaseStatus.AWAITING_SCHEDULING,
    days_ago: int = 5,
    case_id: uuid.UUID | None = None,
    number: str = _NUMBER,
    cns: str = "",
    origin: str = "",
) -> Case:
    case = Case.objects.create(created_by=owner, **({"case_id": case_id} if case_id else {}))
    _declared(case, ANGIO_TYPE)
    _identity(case, number=number, cns=cns, origin=origin)
    _to_awaiting(case)
    case.record_doctor_decision(accepted=True, user=None, role=SYSTEM_ROLE)
    case.request_scheduling(user=None, role=SYSTEM_ROLE)
    if status == CaseStatus.AWAITING_SCHEDULING:
        case.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    assert case.status == status
    return _backdate(case, days_ago=days_ago)


def _medical_prior(owner: User, *, days_ago: int = 4, number: str = _NUMBER) -> Case:
    case = Case.objects.create(created_by=owner)
    _declared(case, ANGIO_TYPE)
    _identity(case, number=number)
    _to_awaiting(case)
    return _backdate(case, days_ago=days_ago)


def _ready_incoming(
    owner: User,
    *,
    case_id: uuid.UUID | None = None,
    number: str = _NUMBER,
    cns: str = "",
    origin: str = "",
) -> Case:
    case = Case.objects.create(created_by=owner, **({"case_id": case_id} if case_id else {}))
    _declared(case, ANGIO_TYPE)
    _identity(case, number=number, cns=cns, origin=origin)
    _to_awaiting(case)
    return case


def _extracting_incoming(owner: User, *, summarizing: bool = False) -> tuple[Case, uuid.UUID]:
    """Incoming em processamento sob a PRÓPRIA lease ``worker_llm``."""
    case = Case.objects.create(created_by=owner)
    _declared(case, ANGIO_TYPE)
    _identity(case)
    _to_extracting(case)
    if summarizing:
        case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    lock = claim_case_lock(case, user=None, context="worker_llm", role=SYSTEM_ROLE)
    case.refresh_from_db()
    return case, lock.token


def _snapshot(case: Case) -> dict[str, Any]:
    """Todos os campos concretos do caso (comparação campo a campo, D5)."""
    fresh = Case.objects.get(pk=case.pk)
    return {f.attname: getattr(fresh, f.attname) for f in Case._meta.concrete_fields}


def _event_types(case: Case) -> list[str]:
    return list(case.events.order_by("id").values_list("event_type", flat=True))


def _human_lease(case: Case, user: User) -> None:
    claim_case_lock(case, user=user, context="scheduler_decision", role="scheduler")


def _worker_lease(case: Case, context: str = "worker_pdf") -> None:
    claim_case_lock(case, user=None, context=context, role=SYSTEM_ROLE)


def _link_for(incoming: Case) -> CaseReappearance:
    return CaseReappearance.objects.get(
        incoming_case=incoming, resolution_kind=ResolutionKind.SCHEDULING_REPEAT
    )


# ── R1.4: FSM ───────────────────────────────────────────────────────────────

_ACCEPTED_SOURCES = [
    CaseStatus.AWAITING_DOCTOR,
    CaseStatus.LLM_EXTRACTING,
    CaseStatus.LLM_SUMMARIZING,
]


def test_fsm_keeps_seventeen_states() -> None:
    assert len(CaseStatus.values) == 17


@pytest.mark.django_db
@pytest.mark.parametrize("source", _ACCEPTED_SOURCES)
def test_close_as_reappearance_accepts_three_sources(nir_user: User, source: CaseStatus) -> None:
    case = Case.objects.create(created_by=nir_user)
    _declared(case, ANGIO_TYPE)
    _identity(case)
    _to_extracting(case)
    if source == CaseStatus.LLM_SUMMARIZING:
        case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    elif source == CaseStatus.AWAITING_DOCTOR:
        case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
        case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == source
    case.close_as_reappearance(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    event = case.events.get(event_type=CaseEventType.CASE_STATUS_CLEANED)
    assert event.payload["source"] == source.value
    assert event.payload["target"] == "CLEANED"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "source",
    [status for status in CaseStatus if status not in _ACCEPTED_SOURCES],
)
def test_close_as_reappearance_refuses_every_other_source(
    nir_user: User, source: CaseStatus
) -> None:
    case = Case.objects.create(created_by=nir_user)
    Case.objects.filter(pk=case.pk).update(status=source)
    case.refresh_from_db()
    with pytest.raises(TransitionNotAllowed):
        case.close_as_reappearance(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == source
    assert not case.events.filter(event_type=CaseEventType.CASE_STATUS_CLEANED).exists()


@pytest.mark.django_db
def test_no_schema_migration_needed() -> None:
    out = StringIO()
    call_command("makemigrations", "--check", "--dry-run", stdout=out, stderr=out)
    assert "No changes detected" in out.getvalue()


# ── R2.2: disposição separada — retém SEMPRE ────────────────────────────────


def _clinical_incoming_with_files(owner: User, *, status: CaseStatus) -> Case:
    case = Case.objects.create(created_by=owner)
    _declared(case, ANGIO_TYPE)
    _identity(case)
    _to_extracting(case)
    if status == CaseStatus.LLM_SUMMARIZING:
        case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    elif status == CaseStatus.AWAITING_DOCTOR:
        case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
        case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    case.extracted_text = "texto clinico extraido"
    case.anonymized_text = "texto anonimizado <PESSOA_1>"
    case.pseudonym_map = {"<PESSOA_1>": {"value": "MARIA", "entity_type": "PERSON"}}
    case.structured_data = {"contexto": "clinico"}
    case.summary_text = "resumo clinico"
    case.suggested_action = {"aggregate": "aceitar"}
    case.policy_result = {ANGIO_TYPE: {"recommendation": "recomenda_aceitar"}}
    case.save()
    document = CaseDocument(
        case=case,
        position=1,
        original_filename="relatorio.pdf",
        content_type="application/pdf",
        size_bytes=20,
        uploaded_by=owner,
    )
    document.file.save("relatorio.pdf", ContentFile(b"%PDF-1.4 sintetico"), save=False)
    document.save()
    CaseAttachment.objects.create(
        case=case,
        original_filename="exame.jpg",
        content_type="image/jpeg",
        size_bytes=10,
        file=SimpleUploadedFile("exame.jpg", b"0123456789", content_type="image/jpeg"),
        uploaded_by=owner,
    )
    return case


@NEW_MODE
@override_settings(STORAGES=_TEST_STORAGES)
@pytest.mark.django_db
@pytest.mark.parametrize("minimization", [False, True])
@pytest.mark.parametrize("source", _ACCEPTED_SOURCES)
def test_close_incoming_as_scheduling_repeat_always_retains(
    nir_user: User, source: CaseStatus, minimization: bool
) -> None:
    incoming = _clinical_incoming_with_files(nir_user, status=source)
    before = {name: getattr(incoming, name) for name in closure_module._CLEANED_EMPTY_VALUES}
    file_names = closure_module._retained_file_names(incoming)
    assert len(file_names) == 2
    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=minimization):
        closure_module.close_incoming_as_scheduling_repeat(incoming, user=None, role=SYSTEM_ROLE)
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED
    assert incoming.clinical_data_disposed_at is None
    for name, value in before.items():
        assert getattr(incoming, name) == value
    assert incoming.documents.count() == 1
    assert incoming.attachments.count() == 1
    assert closure_module._retained_file_names(incoming) == file_names
    for name in file_names:
        assert default_storage.exists(name)
    assert not incoming.events.filter(
        event_type=CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME
    ).exists()


@NEW_MODE
@override_settings(STORAGES=_TEST_STORAGES)
@pytest.mark.django_db
def test_medical_replacement_disposition_is_untouched_and_separate(nir_user: User) -> None:
    original = _clinical_incoming_with_files(nir_user, status=CaseStatus.AWAITING_DOCTOR)
    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=True):
        closure_module.close_case_as_reappearance(original, user=None, role=SYSTEM_ROLE)
    original.refresh_from_db()
    assert original.status == CaseStatus.CLEANED
    assert original.clinical_data_disposed_at is not None
    assert original.documents.count() == 0
    assert original.extracted_text == ""
    # A substituição médica continua exigindo AWAITING_DOCTOR.
    llm_case, _token = _extracting_incoming(nir_user)
    with pytest.raises(ValueError):
        closure_module.close_case_as_reappearance(llm_case, user=None, role=SYSTEM_ROLE)


@pytest.mark.django_db
def test_close_incoming_refuses_other_states(nir_user: User) -> None:
    case = Case.objects.create(created_by=nir_user)
    _declared(case, ANGIO_TYPE)
    _identity(case)
    _to_extracting(case)
    case.fail_processing(reason="x", user=None, role=SYSTEM_ROLE)
    with pytest.raises(ValueError):
        closure_module.close_incoming_as_scheduling_repeat(case, user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED


@pytest.mark.django_db
def test_close_incoming_never_takes_another_actors_live_lease(
    nir_user: User, doctor_user: User
) -> None:
    for holder in (None, doctor_user):
        case = _ready_incoming(nir_user)
        claim_case_lock(
            case,
            user=holder,
            context="worker_llm" if holder is None else "doctor_decision",
            role=SYSTEM_ROLE if holder is None else "doctor",
        )
        before = _snapshot(case)
        with pytest.raises(ValueError):
            closure_module.close_incoming_as_scheduling_repeat(
                case, user=None, role=SYSTEM_ROLE, own_lock_token=uuid.uuid4()
            )
        assert _snapshot(case) == before
        assert _snapshot(case)["status"] == CaseStatus.AWAITING_DOCTOR


@pytest.mark.django_db
def test_close_incoming_releases_own_lease_cleanly_in_same_atomic(nir_user: User) -> None:
    case, token = _extracting_incoming(nir_user)
    closure_module.close_incoming_as_scheduling_repeat(
        case, user=None, role=SYSTEM_ROLE, own_lock_token=token
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert case.lock_token is None
    assert case.locked_until is None
    assert case.lock_context == ""
    assert CaseEventType.CASE_LOCK_RELEASED.value in _event_types(case)


@pytest.mark.django_db
def test_close_incoming_cleans_expired_residue_silently(nir_user: User) -> None:
    case, _token = _extracting_incoming(nir_user)
    Case.objects.filter(pk=case.pk).update(locked_until=timezone.now() - timedelta(minutes=1))
    released_before = _event_types(case).count(CaseEventType.CASE_LOCK_RELEASED.value)
    closure_module.close_incoming_as_scheduling_repeat(case, user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert case.lock_token is None
    assert case.lock_context == ""
    assert _event_types(case).count(CaseEventType.CASE_LOCK_RELEASED.value) == released_before


# ── R2.1/R1.3: resolução do caso pronto (entrypoint) ────────────────────────


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
@pytest.mark.parametrize("status", [CaseStatus.SCHEDULER_REQUESTED, CaseStatus.AWAITING_SCHEDULING])
def test_ready_incoming_archived_against_unique_strong_scheduling_original(
    nir_user: User, status: CaseStatus
) -> None:
    prior = _scheduling_prior(nir_user, status=status)
    prior_before = _snapshot(prior)
    prior_docs = prior.documents.count()
    prior_events = _event_types(prior)
    incoming = _ready_incoming(nir_user)
    outcome = reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert outcome.status == "archived"
    link = _link_for(incoming)
    assert outcome.link is not None and outcome.link.pk == link.pk
    assert link.state == ReappearanceState.APPLIED
    assert link.prior_case_id == prior.pk
    assert link.canonical_case_id == prior.pk
    assert link.applied_at is not None
    assert link.evidence_code == "occurrence_number"
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED
    assert incoming.clinical_data_disposed_at is None
    # Original: todos os campos intactos (status/decisões/espera/lease/docs).
    assert _snapshot(prior) == prior_before
    assert prior.documents.count() == prior_docs
    # Eventos correlacionados: SUPERSEDED no encerrado (incoming), APPLIED no canônico.
    superseded = incoming.events.get(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED)
    applied = prior.events.get(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED)
    assert not incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()
    assert set(superseded.payload) == {
        "reappearance_id",
        "canonical_case_id",
        "evidence_code",
        "resolution_kind",
        "revision_digest",
    }
    assert set(applied.payload) == {
        "reappearance_id",
        "incoming_case_id",
        "evidence_code",
        "resolution_kind",
        "revision_digest",
    }
    assert superseded.payload["reappearance_id"] == str(link.pk)
    assert applied.payload["reappearance_id"] == str(link.pk)
    assert superseded.payload["canonical_case_id"] == str(prior.pk)
    assert applied.payload["incoming_case_id"] == str(incoming.pk)
    for event in (superseded, applied):
        assert event.actor is None
        assert event.actor_type == "system"
        assert event.payload["resolution_kind"] == "scheduling_repeat"
        assert event.payload["revision_digest"] == link.revision_digest
        blob = json.dumps(event.payload)
        assert "Maria" not in blob and _NUMBER not in blob
    # Prior só ganhou o evento correlacionado (sem decisão/encerramento fictício).
    assert _event_types(prior) == [*prior_events, CaseEventType.CASE_REAPPEARANCE_APPLIED.value]
    forbidden = {
        CaseEventType.CASE_ADMINISTRATIVELY_CLOSED.value,
        CaseEventType.CASE_STATUS_DOCTOR_ACCEPTED.value,
        CaseEventType.CASE_STATUS_DOCTOR_DENIED.value,
        CaseEventType.CASE_STATUS_SCHEDULING_CONFIRMED.value,
        CaseEventType.CASE_STATUS_SCHEDULING_DENIED.value,
        CaseEventType.CASE_STATUS_FINAL_REPLY_POSTED.value,
    }
    assert forbidden.isdisjoint(_event_types(incoming))
    assert Schedule.objects.count() == 0
    # Idempotente: nova chamada sem novo efeito/evento.
    again = reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert again.status in {"archived", "aborted"}
    assert CaseReappearance.objects.filter(incoming_case=incoming).count() == 1
    assert CaseEvent.objects.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).count() == 1


@NEW_MODE
@pytest.mark.django_db
def test_ready_incoming_with_medical_decision_is_not_archived(nir_user: User) -> None:
    prior = _scheduling_prior(nir_user)
    incoming = _ready_incoming(nir_user)
    CaseProcedure.objects.filter(case=incoming).update(
        doctor_disposition="approved", doctor_decided_at=timezone.now()
    )
    outcome = reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert outcome.status == "none"
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()
    assert prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).count() == 0


def _ambiguity_strong_plus_weak(owner: User) -> Case:
    _scheduling_prior(owner, days_ago=5, number=_NUMBER)
    _scheduling_prior(owner, days_ago=3, number="", origin="Hospital Geral")
    return _ready_incoming(owner, origin="Hospital Geral")


def _ambiguity_strong_plus_conflict(owner: User) -> Case:
    _scheduling_prior(owner, days_ago=5, number=_NUMBER)
    _scheduling_prior(owner, days_ago=3, number="outro-9", cns=_CNS_B)
    return _ready_incoming(owner, cns=_CNS_A)


def _ambiguity_strong_scheduling_plus_medical(owner: User) -> Case:
    _scheduling_prior(owner, days_ago=5, number=_NUMBER)
    _medical_prior(owner, days_ago=4, number=_NUMBER)
    return _ready_incoming(owner)


def _ambiguity_two_strong_scheduling(owner: User) -> Case:
    _scheduling_prior(owner, days_ago=5, number=_NUMBER)
    _scheduling_prior(owner, days_ago=4, number=_NUMBER, status=CaseStatus.SCHEDULER_REQUESTED)
    return _ready_incoming(owner)


def _weak_alone(owner: User) -> Case:
    _scheduling_prior(owner, days_ago=5, number="", origin="Hospital Geral")
    return _ready_incoming(owner, number="", origin="Hospital Geral")


def _different_effective_set(owner: User) -> Case:
    _scheduling_prior(owner, days_ago=5)
    case = Case.objects.create(created_by=owner)
    _declared(case, OTHER_TYPE)
    _identity(case)
    _to_awaiting(case)
    return case


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
@pytest.mark.parametrize(
    "scenario",
    [
        _ambiguity_strong_plus_weak,
        _ambiguity_strong_plus_conflict,
        _ambiguity_strong_scheduling_plus_medical,
        _ambiguity_two_strong_scheduling,
        _weak_alone,
        _different_effective_set,
    ],
)
def test_conservative_ambiguity_never_automates(nir_user: User, scenario: Any) -> None:
    incoming = scenario(nir_user)
    others = list(Case.objects.exclude(pk=incoming.pk))
    assert others
    before = {case.pk: _snapshot(case) for case in others}
    discovered = reappearance_module._candidate_queryset(incoming)
    assert len(discovered) != 1 or scenario is _weak_alone  # não-vácuo: pool real
    outcome = reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert outcome.status == "none"
    assert not CaseReappearance.objects.exists()
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert {case.pk: _snapshot(case) for case in others} == before
    assert Schedule.objects.count() == 0


@NEW_MODE
@pytest.mark.django_db
def test_strict_anteriority_tuple_with_inverted_uuids(nir_user: User) -> None:
    low = uuid.UUID(int=1)
    high = uuid.UUID(int=2**100)
    stamp = timezone.now() - timedelta(days=2)

    def _arrange(prior_id: uuid.UUID, incoming_id: uuid.UUID) -> tuple[Case, Case]:
        CaseReappearance.objects.all().delete()
        Case.objects.all().delete()
        prior = _scheduling_prior(nir_user, case_id=prior_id)
        incoming = _ready_incoming(nir_user, case_id=incoming_id)
        Case.objects.filter(pk__in=[prior.pk, incoming.pk]).update(created_at=stamp)
        return prior, incoming

    prior, incoming = _arrange(high, low)  # prior NÃO é anterior (id maior)
    assert reappearance_module.resolve_scheduling_repeat(incoming.pk).status == "none"
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    prior, incoming = _arrange(low, high)  # prior anterior (id menor)
    assert reappearance_module.resolve_scheduling_repeat(incoming.pk).status == "archived"
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED


@NEW_MODE
@pytest.mark.django_db(transaction=True)
def test_original_that_left_the_queue_before_the_lock_is_not_cancelled(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg
    from django.conf import settings

    prior = _scheduling_prior(nir_user)
    incoming = _ready_incoming(nir_user)
    real_lock_group = reappearance_module._lock_group
    fired = {"done": False}

    def _scheduled_before_lock(case_ids: list[uuid.UUID]) -> dict[uuid.UUID, Case]:
        if not fired["done"]:
            fired["done"] = True
            db = settings.DATABASES["default"]
            with psycopg.connect(
                dbname=db["NAME"],
                user=db["USER"],
                password=db["PASSWORD"],
                host=db["HOST"],
                port=db["PORT"],
                autocommit=True,
            ) as conn:
                conn.execute(
                    "UPDATE cases_case SET status = %s WHERE case_id = %s",
                    (CaseStatus.SCHEDULING_CONFIRMED.value, prior.pk),
                )
        return real_lock_group(case_ids)

    monkeypatch.setattr(reappearance_module, "_lock_group", _scheduled_before_lock)
    outcome = reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert outcome.status == "none"
    prior.refresh_from_db()
    assert prior.status == CaseStatus.SCHEDULING_CONFIRMED
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()


# ── D5: lease ───────────────────────────────────────────────────────────────


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_human_scheduler_lease_on_original_is_preserved_and_does_not_defer(
    nir_user: User, doctor_user: User
) -> None:
    prior = _scheduling_prior(nir_user)
    _human_lease(prior, doctor_user)
    prior_before = _snapshot(prior)
    assert prior_before["lock_token"] is not None
    incoming = _ready_incoming(nir_user)
    outcome = reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert outcome.status == "archived"
    assert _snapshot(prior) == prior_before  # lease humana intacta, campo a campo
    assert Schedule.objects.count() == 0
    assert _link_for(incoming).state == ReappearanceState.APPLIED


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_other_worker_live_lease_on_original_defers_with_durable_once_schedule(
    nir_user: User,
) -> None:
    prior = _scheduling_prior(nir_user)
    _worker_lease(prior)
    prior_before = _snapshot(prior)
    incoming = _ready_incoming(nir_user)
    outcome = reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert outcome.status == "deferred"
    link = _link_for(incoming)
    assert outcome.link is not None and outcome.link.pk == link.pk
    assert link.state == ReappearanceState.PENDING
    assert link.canonical_case_id == prior.pk
    assert link.attempt_count == 1
    assert link.last_reason_code == "lease_busy"
    schedule = Schedule.objects.get()
    assert schedule.schedule_type == Schedule.ONCE
    assert schedule.cluster == "llm"
    assert schedule.func == reappearance_module.RETRY_FUNC
    assert schedule.name == reappearance_module.retry_schedule_name(
        link.pk, 1, link.revision_digest
    )
    assert link.revision_digest in (schedule.args or "")
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert _snapshot(prior) == prior_before  # lease alheia nunca tomada
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
    # Reentrada: mesmo vínculo, sem nova agenda/orçamento/evento.
    again = reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert again.status == "deferred"
    assert again.link is not None and again.link.pk == link.pk
    assert CaseReappearance.objects.count() == 1
    assert Schedule.objects.count() == 1
    link.refresh_from_db()
    assert link.attempt_count == 1


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_other_actor_live_lease_on_incoming_defers(nir_user: User) -> None:
    prior = _scheduling_prior(nir_user)
    prior_before = _snapshot(prior)
    incoming = _ready_incoming(nir_user)
    _worker_lease(incoming, "worker_attachments")
    incoming_before = _snapshot(incoming)
    outcome = reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert outcome.status == "deferred"
    assert _snapshot(incoming) == incoming_before
    assert _snapshot(prior) == prior_before
    assert _link_for(incoming).state == ReappearanceState.PENDING


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_retry_applies_same_link_after_lease_release_without_llm(nir_user: User) -> None:
    prior = _scheduling_prior(nir_user)
    _worker_lease(prior)
    incoming = _ready_incoming(nir_user)
    reappearance_module.resolve_scheduling_repeat(incoming.pk)
    link = _link_for(incoming)
    # Lease alheia ainda viva → reagenda (orçamento 2), sem aplicar.
    CaseReappearance.objects.filter(pk=link.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    reappearance_module.handle_reappearance_retry(link.pk, link.revision_digest, 1)
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.attempt_count == 2
    assert Schedule.objects.filter(cluster="llm").count() == 2
    # Lease liberada → mesmo vínculo é aplicado.
    Case.objects.filter(pk=prior.pk).update(
        locked_by=None,
        locked_at=None,
        locked_until=None,
        lock_token=None,
        lock_context="",
        lock_role="",
    )
    prior_before = _snapshot(prior)
    CaseReappearance.objects.filter(pk=link.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    reappearance_module.handle_reappearance_retry(link.pk, link.revision_digest, 2)
    applied = CaseReappearance.objects.get(pk=link.pk)
    assert applied.state == ReappearanceState.APPLIED
    assert applied.canonical_case_id == prior.pk
    assert CaseReappearance.objects.count() == 1
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED
    assert incoming.clinical_data_disposed_at is None
    assert _snapshot(prior) == prior_before
    assert Schedule.objects.count() == 0
    assert (
        incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).count() == 1
    )
    assert prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).count() == 1
    # Job atrasado/duplicado após aplicar é no-op.
    reappearance_module.handle_reappearance_retry(link.pk, link.revision_digest, 2)
    assert (
        incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).count() == 1
    )


def _deferred_link(owner: User) -> tuple[Case, Case, CaseReappearance]:
    prior = _scheduling_prior(owner)
    _worker_lease(prior)
    incoming = _ready_incoming(owner)
    reappearance_module.resolve_scheduling_repeat(incoming.pk)
    link = _link_for(incoming)
    CaseReappearance.objects.filter(pk=link.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    link.refresh_from_db()
    return prior, incoming, link


def _release_prior(prior: Case) -> None:
    Case.objects.filter(pk=prior.pk).update(
        locked_by=None,
        locked_at=None,
        locked_until=None,
        lock_token=None,
        lock_context="",
        lock_role="",
    )


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
@pytest.mark.parametrize("terminal", ["retry_exhausted", "window_exceeded", "state_ineligible"])
def test_terminal_reasons_block_retry_resolve_and_reenqueue(nir_user: User, terminal: str) -> None:
    prior, incoming, link = _deferred_link(nir_user)
    CaseReappearance.objects.filter(pk=link.pk).update(last_reason_code=terminal)
    _release_prior(prior)
    attempts = link.attempt_count
    reappearance_module.handle_reappearance_retry(link.pk, link.revision_digest, attempts)
    again = reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert again.status == "deferred"
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.attempt_count == attempts
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    outcome = reappearance_module.apply_reenqueue(link.pk)
    assert outcome.status == "refused"
    assert outcome.reason_code == terminal


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
@pytest.mark.parametrize(
    ("tweak", "reason"),
    [("attempts", "retry_exhausted"), ("window", "window_exceeded")],
)
def test_retry_limits_exhaust_once_on_incoming_only(
    nir_user: User, tweak: str, reason: str
) -> None:
    prior, incoming, link = _deferred_link(nir_user)
    if tweak == "attempts":
        CaseReappearance.objects.filter(pk=link.pk).update(attempt_count=24)
        attempt = 24
    else:
        CaseReappearance.objects.filter(pk=link.pk).update(
            first_attempt_at=timezone.now() - timedelta(hours=24, seconds=5)
        )
        attempt = link.attempt_count
    reappearance_module.handle_reappearance_retry(link.pk, link.revision_digest, attempt)
    reappearance_module.handle_reappearance_retry(link.pk, link.revision_digest, attempt)
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.last_reason_code == reason
    exhausted = CaseEventType.CASE_REAPPEARANCE_RETRY_EXHAUSTED
    assert incoming.events.filter(event_type=exhausted).count() == 1
    assert not prior.events.filter(event_type=exhausted).exists()
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_immutable_digest_drift_makes_retry_state_ineligible(nir_user: User) -> None:
    prior, incoming, link = _deferred_link(nir_user)
    _release_prior(prior)
    Case.objects.filter(pk=incoming.pk).update(origin_unit="Outra origem mudou")
    original_digest = link.revision_digest
    reappearance_module.handle_reappearance_retry(link.pk, original_digest, link.attempt_count)
    link.refresh_from_db()
    assert link.revision_digest == original_digest
    assert link.state == ReappearanceState.PENDING
    assert link.last_reason_code == "state_ineligible"
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_retry_does_not_archive_when_medical_decision_arrived(nir_user: User) -> None:
    prior, incoming, link = _deferred_link(nir_user)
    _release_prior(prior)
    CaseProcedure.objects.filter(case=incoming).update(
        doctor_disposition="denied", doctor_decided_at=timezone.now()
    )
    reappearance_module.handle_reappearance_retry(link.pk, link.revision_digest, link.attempt_count)
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.last_reason_code == "state_ineligible"
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR


@NO_LLM
@pytest.mark.django_db
def test_flag_off_blocks_new_resolutions_retries_and_budget(nir_user: User) -> None:
    with NEW_MODE:
        prior, incoming, link = _deferred_link(nir_user)
        before = CaseReappearance.objects.get(pk=link.pk)
    _release_prior(prior)
    with FLAG_OFF:
        reappearance_module.handle_reappearance_retry(link.pk, link.revision_digest, 1)
        assert reappearance_module.resolve_scheduling_repeat(incoming.pk).status == "none"
        assert (
            reappearance_module.archive_scheduling_repeat_after_llm1(
                case_pk=incoming.pk, lock_token=uuid.uuid4()
            ).status
            == "none"
        )
        outcome = reappearance_module.apply_reenqueue(link.pk)
        assert outcome.status == "refused" and outcome.reason_code == "flag_off"
    after = CaseReappearance.objects.get(pk=link.pk)
    assert after.attempt_count == before.attempt_count
    assert after.state == ReappearanceState.PENDING
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_flag_off_preserves_applied_link_read_and_metric_exclusion(nir_user: User) -> None:
    prior = _scheduling_prior(nir_user)
    incoming = _ready_incoming(nir_user)
    assert reappearance_module.resolve_scheduling_repeat(incoming.pk).status == "archived"
    with FLAG_OFF:
        assert incoming.pk in reappearance_module.technical_reappearance_case_ids()
        assert prior.pk not in reappearance_module.technical_reappearance_case_ids()
        info = reappearance_module.historical_reappearance_links(prior)
        assert info["immediate"] is not None
        assert CaseReappearance.objects.get(incoming_case=incoming).state == "applied"


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_schedule_failure_keeps_pending_observable_without_closing(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = _scheduling_prior(nir_user)
    _worker_lease(prior)
    incoming = _ready_incoming(nir_user)

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("broker indisponivel")

    monkeypatch.setattr("django_q.tasks.schedule", _boom)
    outcome = reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert outcome.status == "deferred"
    link = _link_for(incoming)
    assert link.state == ReappearanceState.PENDING
    assert link.last_reason_code == "schedule_failed"
    assert Schedule.objects.count() == 0
    failed = CaseEventType.CASE_REAPPEARANCE_SCHEDULE_FAILED
    assert incoming.events.filter(event_type=failed).count() == 1
    assert not prior.events.filter(event_type=failed).exists()
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_reenqueue_apply_and_cli_for_scheduling_repeat(nir_user: User) -> None:
    prior, incoming, link = _deferred_link(nir_user)
    Schedule.objects.all().delete()  # pendência sem agenda durável
    out = StringIO()
    call_command("reenqueue_pending_reappearances", str(link.pk), stdout=out)  # exit 0 (simulação)
    assert "SIMULAÇÃO" in out.getvalue()
    assert Schedule.objects.count() == 0
    out = StringIO()
    call_command("reenqueue_pending_reappearances", str(link.pk), "--apply", stdout=out)
    assert "AGENDADO" in out.getvalue()
    assert Schedule.objects.filter(cluster="llm").count() == 1
    reenqueued = CaseEventType.CASE_REAPPEARANCE_REENQUEUED
    assert incoming.events.filter(event_type=reenqueued).count() == 1
    assert not prior.events.filter(event_type=reenqueued).exists()
    # Agenda válida existente → NO-OP (exit 0), sem novo evento.
    out = StringIO()
    call_command("reenqueue_pending_reappearances", str(link.pk), "--apply", stdout=out)
    assert "NO-OP" in out.getvalue()
    assert incoming.events.filter(event_type=reenqueued).count() == 1
    # Exit 1: recusa (terminal) e vínculo inexistente; exit 2: UUID malformado.
    CaseReappearance.objects.filter(pk=link.pk).update(last_reason_code="retry_exhausted")
    with pytest.raises(CommandError):
        call_command("reenqueue_pending_reappearances", str(link.pk), "--apply", stdout=StringIO())
    with pytest.raises(CommandError):
        call_command(
            "reenqueue_pending_reappearances", str(uuid.uuid4()), "--apply", stdout=StringIO()
        )
    with pytest.raises(CommandError):
        call_command("reenqueue_pending_reappearances", "nao-e-uuid", stdout=StringIO())


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_reenqueue_refuses_scheduling_link_when_original_left_the_queue(nir_user: User) -> None:
    prior, _incoming, link = _deferred_link(nir_user)
    Case.objects.filter(pk=prior.pk).update(status=CaseStatus.SCHEDULING_CONFIRMED)
    Schedule.objects.all().delete()
    outcome = reappearance_module.apply_reenqueue(link.pk)
    assert outcome.status == "refused"
    assert outcome.reason_code == "state_ineligible"
    assert Schedule.objects.count() == 0


# ── R1.4: D4 — mutex antes de row lock, ordem do grupo, deriva ×3 ───────────


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_post_llm1_atomic_acquires_mutex_before_any_row_lock(
    nir_user: User, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prior = _scheduling_prior(nir_user)
    _medical_other = _medical_prior(nir_user, days_ago=4, number="outro-77")  # grupo c/ 3 linhas
    incoming, token = _extracting_incoming(nir_user)
    from django.db.backends.utils import CursorWrapper

    seen: list[str] = []
    real_execute = CursorWrapper.execute

    def _execute(self: Any, sql: Any, params: Any = None) -> Any:
        seen.append(str(sql))  # só registra: nunca limpa no mutex (AUD-07)
        return real_execute(self, sql, params)

    monkeypatch.setattr(CursorWrapper, "execute", _execute)
    outcome = reappearance_module.archive_scheduling_repeat_after_llm1(
        case_pk=incoming.pk, lock_token=token
    )
    monkeypatch.undo()
    assert outcome.status == "archived"
    advisory = [i for i, sql in enumerate(seen) if "pg_advisory_xact_lock" in sql]
    row_locks = [i for i, sql in enumerate(seen) if "FOR UPDATE" in sql.upper()]
    assert len(advisory) == 1
    assert row_locks and advisory[0] < row_locks[0]
    group_lock = next(sql for sql in seen if "FOR UPDATE" in sql.upper())
    assert "ORDER BY" in group_lock and "case_id" in group_lock.split("ORDER BY")[1]
    with capsys.disabled():
        print("\n--- SQL TRACE (atomic pós-LLM1) ---")
        for index, sql in enumerate(seen):
            marker = "MUTEX" if index in advisory else "ROWLOCK" if index in row_locks else "      "
            print(f"{index:03d} {marker} {sql[:160]}")
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_SCHEDULING
    assert connection.in_atomic_block


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_post_llm1_atomic_releases_own_lease_and_archives(nir_user: User) -> None:
    prior = _scheduling_prior(nir_user)
    incoming, token = _extracting_incoming(nir_user)
    outcome = reappearance_module.archive_scheduling_repeat_after_llm1(
        case_pk=incoming.pk, lock_token=token
    )
    assert outcome.status == "archived"
    assert outcome.lease_released is True
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED
    assert incoming.lock_token is None
    assert incoming.clinical_data_disposed_at is None
    assert _link_for(incoming).prior_case_id == prior.pk


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_drift_on_all_three_attempts_raises_and_preserves_everything(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = _scheduling_prior(nir_user)
    incoming, token = _extracting_incoming(nir_user)
    prior_before = _snapshot(prior)
    incoming_before = _snapshot(incoming)
    prior_events = _event_types(prior)
    incoming_events = _event_types(incoming)
    calls = {"check": 0, "mutex": 0}
    real_acquire = reappearance_module.acquire_reappearance_mutex

    def _always_drift(**kwargs: Any) -> None:
        calls["check"] += 1
        raise reappearance_module._DriftDetectedError("drift injetado")

    def _count_mutex(key: int) -> None:
        calls["mutex"] += 1
        real_acquire(key)

    monkeypatch.setattr(reappearance_module, "_check_drift", _always_drift)
    monkeypatch.setattr(reappearance_module, "acquire_reappearance_mutex", _count_mutex)
    with pytest.raises(reappearance_module.ReappearanceDriftError):
        reappearance_module.archive_scheduling_repeat_after_llm1(
            case_pk=incoming.pk, lock_token=token
        )
    assert calls == {"check": 3, "mutex": 3}
    assert _snapshot(prior) == prior_before
    assert _snapshot(incoming) == incoming_before
    assert _event_types(prior) == prior_events
    assert _event_types(incoming) == incoming_events
    assert not CaseReappearance.objects.exists()


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_ready_entrypoint_drift_preserves_ready_state_and_raises(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = _scheduling_prior(nir_user)
    incoming = _ready_incoming(nir_user)
    prior_before = _snapshot(prior)
    incoming_before = _snapshot(incoming)

    def _always_drift(**kwargs: Any) -> None:
        raise reappearance_module._DriftDetectedError("drift injetado")

    monkeypatch.setattr(reappearance_module, "_check_drift", _always_drift)
    with pytest.raises(reappearance_module.ReappearanceDriftError):
        reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert _snapshot(incoming) == incoming_before
    assert _snapshot(incoming)["status"] == CaseStatus.AWAITING_DOCTOR
    assert _snapshot(prior) == prior_before
    assert not CaseReappearance.objects.exists()


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_other_sources_do_not_pass_ready_only_entrypoint(nir_user: User) -> None:
    """``resolve_scheduling_repeat`` é só do caso pronto: LLM_EXTRACTING não arquiva."""
    _scheduling_prior(nir_user)
    incoming, _token = _extracting_incoming(nir_user)
    assert reappearance_module.resolve_scheduling_repeat(incoming.pk).status == "none"
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.LLM_EXTRACTING


# ── R2.2: worker de anexos tardio no incoming arquivado ─────────────────────


@NEW_MODE
@override_settings(STORAGES=_TEST_STORAGES)
@pytest.mark.django_db
def test_late_attachment_worker_is_noop_on_archived_incoming_without_deleting_files(
    nir_user: User,
) -> None:
    from apps.attachments.models import AttachmentStatus
    from apps.attachments.tasks import process_case_attachments

    prior = _scheduling_prior(nir_user)
    incoming = _clinical_incoming_with_files(nir_user, status=CaseStatus.LLM_EXTRACTING)
    file_names = closure_module._retained_file_names(incoming)
    assert len(file_names) == 2
    outcome = reappearance_module.archive_scheduling_repeat_after_llm1(
        case_pk=incoming.pk, lock_token=uuid.uuid4()
    )
    assert outcome.status == "archived"
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED
    assert incoming.attachments.get().status == AttachmentStatus.PENDING

    process_case_attachments(incoming.case_id)
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED
    assert incoming.clinical_data_disposed_at is None
    assert incoming.documents.count() == 1
    assert incoming.attachments.count() == 1
    assert incoming.attachments.get().status == AttachmentStatus.PENDING
    for name in file_names:
        assert default_storage.exists(name)
    assert not incoming.events.filter(
        event_type__in=[
            CaseEventType.CASE_ATTACHMENT_PROCESSED,
            CaseEventType.CASE_ATTACHMENT_FAILED,
        ]
    ).exists()
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_SCHEDULING


# ── R2.3: métricas excluem scheduling_repeat nos dois modos da flag ─────────


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
@pytest.mark.parametrize("flag_after", [True, False])
def test_dashboard_metrics_exclude_scheduling_repeat_in_both_flag_modes(
    nir_user: User, flag_after: bool
) -> None:
    from apps.cases.models import DoctorDisposition
    from apps.dashboard.metrics import compute_by_procedure_type, compute_summary

    prior = _scheduling_prior(nir_user)
    CaseProcedure.objects.filter(case=prior).update(
        doctor_disposition=DoctorDisposition.APPROVED,
        doctor_decided_at=timezone.now(),
    )
    incoming = _ready_incoming(nir_user)
    assert reappearance_module.resolve_scheduling_repeat(incoming.pk).status == "archived"

    with override_settings(CASE_REAPPEARANCE_ENABLED=flag_after):
        summary = compute_summary("tudo")
        by_type = {row["procedure_type"]: row for row in compute_by_procedure_type("tudo")}

    # Resumo: total=2 envios, encerrados=1 (incoming técnico), em_andamento=1 (prior).
    assert summary["total"] == 2
    assert summary["encerrados"] == 1
    assert summary["em_andamento"] == 1
    # Tabela por procedimento: apenas o canônico (approved=1, total=1, pending=0).
    assert by_type[ANGIO_TYPE]["total"] == 1
    assert by_type[ANGIO_TYPE]["approved"] == 1
    assert by_type[ANGIO_TYPE]["denied"] == 0
    assert by_type[ANGIO_TYPE]["pending"] == 0


@NEW_MODE
@NO_LLM
@pytest.mark.django_db
def test_ready_incoming_with_applied_medical_replacement_returns_none_not_archived(
    nir_user: User,
) -> None:
    """Fix 1: incoming AWAITING_DOCTOR já canônico de MEDICAL_REPLACEMENT → status='none'."""
    prior_medical = _medical_prior(nir_user, days_ago=6)
    prior_sched = _scheduling_prior(nir_user, days_ago=5)
    incoming = _ready_incoming(nir_user)
    CaseReappearance.objects.create(
        incoming_case=incoming,
        prior_case=prior_medical,
        canonical_case=incoming,
        revision_digest=reappearance_module.revision_digest_for_pair(incoming, prior_medical),
        evidence_code="occurrence_number",
        state=ReappearanceState.APPLIED,
        resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
        applied_at=timezone.now(),
    )
    outcome = reappearance_module.resolve_scheduling_repeat(incoming.pk)
    assert outcome.status == "none"
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert (
        CaseReappearance.objects.filter(
            incoming_case=incoming, resolution_kind=ResolutionKind.SCHEDULING_REPEAT
        ).count()
        == 0
    )
    assert not prior_sched.events.filter(
        event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED
    ).exists()
