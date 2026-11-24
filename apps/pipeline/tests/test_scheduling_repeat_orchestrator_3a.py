"""Reenvio no agendamento pelo orquestrador REAL (S3 unidade 3A, R1.1–R1.4, R2.1/R2.2).

Doubles SOMENTE na fronteira LLM (``LLM_CLIENT_FACTORY``): arquivamento pós-LLM1
nos três caminhos (principal, retomada em ``LLM_SUMMARIZING`` e HD-001),
divergência mantém o gate, autoridade, deriva ×3 → FAILED guardado,
adiamento por lease de worker alheio e retry sem LLM.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings
from django.utils import timezone
from django_q.models import Schedule

from apps.accounts.models import User
from apps.attachments.models import CaseAttachment
from apps.cases import reappearance as reappearance_module
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock
from apps.cases.models import (
    ActorType,
    Case,
    CaseDocument,
    CaseEvent,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    ReappearanceState,
    ResolutionKind,
)
from apps.pipeline import orchestrator as orchestrator_module
from apps.pipeline.orchestrator import process_case_pipeline
from apps.pipeline.tests.test_human_confirmed_procedures import (
    _confirmed_case_ready,
)
from apps.pipeline.tests.test_reappearance_orchestrator import (
    ANGIO_TYPE,
    FAKE_MODEL_LLM1,
    FAKE_MODEL_LLM2,
    NEW_MODE,
    OTHER_TYPE,
    SYSTEM_ROLE,
    ScriptedLlmClient,
    _artifact,
    _extracting_incoming,
    _llm2_response,
    _seeded_prompts,  # noqa: F401 — fixture autouse do módulo reutilizado
)

_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.fixture
def owner_user() -> User:
    return User.objects.create_user(username="dono-sr3a", password="senha-teste")


@pytest.fixture
def nir_user(owner_user: User) -> User:
    from apps.accounts.models import Role

    role, _ = Role.objects.get_or_create(name="nir")
    owner_user.roles.add(role)
    return owner_user


@pytest.fixture
def scheduler_user() -> User:
    return User.objects.create_user(username="agendador-sr3a", password="senha-teste")


def _run(case: Case, fake: ScriptedLlmClient) -> None:
    with override_settings(
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    ):
        process_case_pipeline(case.case_id)


def _scheduling_prior(
    owner: User,
    *,
    status: CaseStatus = CaseStatus.AWAITING_SCHEDULING,
    declared: tuple[str, ...] = (ANGIO_TYPE,),
    name: str = "Maria da Silva",
    number: str = "orc-1",
) -> Case:
    prior = Case.objects.create(created_by=owner)
    for procedure_type in declared:
        CaseProcedure.objects.create(
            case=prior, procedure_type=procedure_type, declared_by_nir=True
        )
    prior.patient_name = name
    prior.agency_record_number = number
    prior.save(update_fields=["patient_name", "agency_record_number"])
    prior.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    prior.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    prior.complete_anonymization(user=None, role=SYSTEM_ROLE)
    prior.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    prior.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    prior.record_doctor_decision(accepted=True, user=None, role=SYSTEM_ROLE)
    prior.request_scheduling(user=None, role=SYSTEM_ROLE)
    if status == CaseStatus.AWAITING_SCHEDULING:
        prior.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    assert prior.status == status
    Case.objects.filter(pk=prior.pk).update(created_at=timezone.now() - timedelta(days=3))
    prior.refresh_from_db()
    return prior


def _snapshot(case: Case) -> dict[str, Any]:
    fresh = Case.objects.get(pk=case.pk)
    return {f.attname: getattr(fresh, f.attname) for f in Case._meta.concrete_fields}


def _event_types(case: Case) -> list[str]:
    return list(case.events.order_by("id").values_list("event_type", flat=True))


def _models(fake: ScriptedLlmClient) -> list[str]:
    return [model for model, _messages, _schema in fake.calls]


def _sched_link(incoming: Case) -> CaseReappearance:
    return CaseReappearance.objects.get(
        incoming_case=incoming, resolution_kind=ResolutionKind.SCHEDULING_REPEAT
    )


def _attach_files(case: Case, owner: User) -> list[str]:
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
    attachment = CaseAttachment.objects.create(
        case=case,
        original_filename="exame.jpg",
        content_type="image/jpeg",
        size_bytes=10,
        file=ContentFile(b"0123456789", name="exame.jpg"),
        uploaded_by=owner,
    )
    assert document.file.name is not None
    assert attachment.file.name is not None
    return [document.file.name, attachment.file.name]


# ── R1.1: caminho principal (LLM_EXTRACTING) ────────────────────────────────


@NEW_MODE
@override_settings(STORAGES=_TEST_STORAGES)
@pytest.mark.django_db
@pytest.mark.parametrize("minimization", [False, True])
def test_main_path_archives_after_llm1_without_llm2_and_retains_everything(
    owner_user: User, scheduler_user: User, minimization: bool
) -> None:
    prior = _scheduling_prior(owner_user)
    claim_case_lock(prior, user=scheduler_user, context="scheduler_decision", role="scheduler")
    prior_before = _snapshot(prior)
    incoming = _extracting_incoming(owner=owner_user, number="orc-1")
    file_names = _attach_files(incoming, owner_user)
    seen_at_llm1: dict[str, Any] = {}

    class _Probe(ScriptedLlmClient):
        def complete(
            self,
            model: str,
            messages: Sequence[dict[str, str]],
            *,
            json_schema: dict[str, Any] | None = None,
        ) -> str:
            seen_at_llm1["status"] = Case.objects.get(pk=incoming.pk).status
            seen_at_llm1["links"] = CaseReappearance.objects.count()
            return super().complete(model, messages, json_schema=json_schema)

    fake = _Probe([json.dumps(_artifact([ANGIO_TYPE]))])  # LLM2 sem resposta → falharia
    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=minimization):
        _run(incoming, fake)
    # Nunca fecha pré-LLM1.
    assert seen_at_llm1 == {"status": CaseStatus.LLM_EXTRACTING, "links": 0}
    assert _models(fake) == [FAKE_MODEL_LLM1]
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED
    link = _sched_link(incoming)
    assert link.state == ReappearanceState.APPLIED
    assert link.prior_case_id == prior.pk and link.canonical_case_id == prior.pk
    # Própria lease worker_llm liberada no mesmo atomic.
    assert incoming.lock_token is None and incoming.locked_until is None
    assert incoming.lock_context == ""
    # Retenção total, mesmo com minimização ligada.
    assert incoming.clinical_data_disposed_at is None
    assert incoming.structured_data != {}
    assert incoming.anonymized_text != ""
    assert incoming.documents.count() == 1 and incoming.attachments.count() == 1
    for name in file_names:
        assert default_storage.exists(name)
    # Sem LLM2, sem fila médica, sem decisões fictícias.
    types = _event_types(incoming)
    assert CaseEventType.CASE_LLM1_COMPLETED.value in types
    assert CaseEventType.CASE_LLM2_COMPLETED.value not in types
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value not in types
    assert CaseEventType.CASE_STATUS_LLM_SUMMARIZING.value not in types
    assert CaseEventType.CASE_STATUS_FAILED.value not in types
    # Original completamente intacto (inclui lease humana do agendador).
    assert _snapshot(prior) == prior_before
    assert prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).count() == 1


@NEW_MODE
@pytest.mark.django_db
def test_divergence_keeps_gate_without_archiving_or_llm2(owner_user: User) -> None:
    prior = _scheduling_prior(owner_user)
    prior_before = _snapshot(prior)
    incoming = _extracting_incoming(owner=owner_user, number="orc-1")
    fake = ScriptedLlmClient(
        [json.dumps(_artifact([OTHER_TYPE])), json.dumps(_llm2_response([OTHER_TYPE]))]
    )
    _run(incoming, fake)
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.LLM_EXTRACTING
    assert incoming.manual_review_required is True
    assert _models(fake) == [FAKE_MODEL_LLM1]
    assert not CaseReappearance.objects.exists()
    assert incoming.lock_token is None  # lease própria liberada pelo finally
    assert _snapshot(prior) == prior_before


@NEW_MODE
@pytest.mark.django_db
def test_weak_alone_and_ambiguity_never_archive_in_pipeline(owner_user: User) -> None:
    _scheduling_prior(owner_user, number="")  # sem regulação: só nome → sem candidato forte
    weak = Case.objects.filter(patient_name="Maria da Silva").get()
    Case.objects.filter(pk=weak.pk).update(origin_unit="Hospital Geral")
    incoming = _extracting_incoming(owner=owner_user, number="")
    Case.objects.filter(pk=incoming.pk).update(origin_unit="Hospital Geral")
    fake = ScriptedLlmClient(
        [json.dumps(_artifact([ANGIO_TYPE])), json.dumps(_llm2_response([ANGIO_TYPE]))]
    )
    _run(incoming, fake)
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR  # fluxo normal
    assert not CaseReappearance.objects.exists()
    assert _models(fake) == [FAKE_MODEL_LLM1, FAKE_MODEL_LLM2]


@pytest.mark.django_db
def test_flag_off_pipeline_runs_normally(owner_user: User) -> None:
    with override_settings(CASE_REAPPEARANCE_ENABLED=False):
        _scheduling_prior(owner_user)
        incoming = _extracting_incoming(owner=owner_user, number="orc-1")
        fake = ScriptedLlmClient(
            [json.dumps(_artifact([ANGIO_TYPE])), json.dumps(_llm2_response([ANGIO_TYPE]))]
        )
        _run(incoming, fake)
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()


# ── R1.1: retomada em LLM_SUMMARIZING (pós-bypass) ──────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_resumption_in_llm_summarizing_archives_before_llm2(owner_user: User) -> None:
    prior = _scheduling_prior(owner_user, status=CaseStatus.SCHEDULER_REQUESTED)
    prior_before = _snapshot(prior)
    incoming = _extracting_incoming(owner=owner_user, number="orc-1")
    # Retenção por divergência legada, depois liberação do NIR (bypass).
    first = ScriptedLlmClient([json.dumps(_artifact([OTHER_TYPE]))])
    _run(incoming, first)
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.LLM_EXTRACTING and incoming.manual_review_required
    incoming.bypass_pipeline_divergence(user=None, role="nir")
    incoming.manual_review_required = False
    incoming.manual_review_reason = ""
    incoming.save(update_fields=["manual_review_required", "manual_review_reason"])
    assert incoming.status == CaseStatus.LLM_SUMMARIZING
    resumed = ScriptedLlmClient([])  # qualquer chamada LLM falharia o teste
    _run(incoming, resumed)
    assert resumed.calls == []
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED
    assert _sched_link(incoming).state == ReappearanceState.APPLIED
    assert incoming.lock_token is None
    assert incoming.clinical_data_disposed_at is None
    assert CaseEventType.CASE_LLM2_COMPLETED.value not in _event_types(incoming)
    assert _snapshot(prior) == prior_before


# ── R1.1/R1.2: confirmação de procedimento (conjunto efetivo) ───────────────


def _identify(case: Case, *, name: str = "Maria da Silva", number: str = "orc-1") -> None:
    Case.objects.filter(pk=case.pk).update(patient_name=name, agency_record_number=number)
    case.refresh_from_db()


@NEW_MODE
@pytest.mark.django_db
def test_confirmation_with_same_effective_set_archives(nir_user: User) -> None:
    prior = _scheduling_prior(nir_user)  # declara art_perif
    prior_before = _snapshot(prior)
    case = _confirmed_case_ready(nir_user)  # efetivo = {art_perif}
    _identify(case)
    fake = ScriptedLlmClient([json.dumps(_artifact(["nefrostomia"]))])
    _run(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert _models(fake) == [FAKE_MODEL_LLM1]
    assert _sched_link(case).prior_case_id == prior.pk
    assert case.lock_token is None
    assert _snapshot(prior) == prior_before


@NEW_MODE
@pytest.mark.django_db
def test_confirmation_with_different_effective_set_never_archives(nir_user: User) -> None:
    _scheduling_prior(nir_user, declared=(OTHER_TYPE,))
    case = _confirmed_case_ready(nir_user)  # efetivo = {art_perif} ≠ {cat_cardiaco}
    _identify(case)
    fake = ScriptedLlmClient(
        [json.dumps(_artifact(["nefrostomia"])), json.dumps(_llm2_response([ANGIO_TYPE]))]
    )
    _run(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()
    assert _models(fake) == [FAKE_MODEL_LLM1, FAKE_MODEL_LLM2]


def _archive_hook(case: Case, *, reconciled: bool, lock_token: uuid.UUID | None = None) -> Any:
    token = (
        lock_token
        if lock_token is not None
        else claim_case_lock(case, user=None, context="worker_llm", role=SYSTEM_ROLE).token
    )
    return reappearance_module.archive_scheduling_repeat_after_llm1(
        case_pk=case.pk, lock_token=token, reconciled=reconciled
    )


@NEW_MODE
@pytest.mark.django_db
@pytest.mark.parametrize("reconciled", [False, True])
def test_integrity_invalid_authority_never_archives(nir_user: User, reconciled: bool) -> None:
    prior = _scheduling_prior(nir_user)
    prior_before = _snapshot(prior)
    case = _confirmed_case_ready(nir_user)
    _identify(case)
    # Integridade: fonte alterada após a confirmação (autoridade inválida).
    Case.objects.filter(pk=case.pk).update(extracted_text="Fonte alterada apos confirmar.")
    case.refresh_from_db()
    outcome = _archive_hook(case, reconciled=reconciled)
    assert outcome.status == "none"
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert not CaseReappearance.objects.exists()
    assert _snapshot(prior) == prior_before
    assert prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).count() == 0


@NEW_MODE
@pytest.mark.django_db
def test_stale_gate_authority_blocks_without_reconciliation_and_archives_after_it(
    nir_user: User,
) -> None:
    from apps.cases.procedures import set_declared_procedures

    prior = _scheduling_prior(nir_user, declared=(OTHER_TYPE,))
    prior_before = _snapshot(prior)
    case = _confirmed_case_ready(nir_user)  # confirmado = {art_perif}
    _identify(case)
    # Declaração mudou após a confirmação → autoridade obsoleta (gate), mas o
    # conjunto efetivo vigente ({cat_cardiaco}) é o do original (não-vácuo).
    set_declared_procedures(case, (OTHER_TYPE,), user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    claim = claim_case_lock(case, user=None, context="worker_llm", role=SYSTEM_ROLE)
    blocked = _archive_hook(case, reconciled=False, lock_token=claim.token)
    assert blocked.status == "none"
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert not CaseReappearance.objects.exists()
    assert _snapshot(prior) == prior_before
    # Após a reconciliação automática vigente (LLM1 refeita) o gate-obsoleto cai
    # no fluxo automático e o arquivamento é permitido.
    reconciled = _archive_hook(case, reconciled=True, lock_token=claim.token)
    assert reconciled.status == "archived"
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED


# ── R1.1: HD-001 (_reconcile_stale_authority) ───────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_hd001_reconciliation_archives_after_relllm1_without_second_llm2(
    nir_user: User,
) -> None:
    from apps.cases.procedure_review import (
        anonymized_text_digest_for_case,
        pseudonym_map_digest_for_case,
        source_fingerprint_for_case,
    )

    case = _confirmed_case_ready(nir_user)
    _identify(case)
    holder: dict[str, Case] = {}
    call_count = {"n": 0}

    class _ReviewSupersededClient(ScriptedLlmClient):
        def complete(
            self,
            model: str,
            messages: Sequence[dict[str, str]],
            *,
            json_schema: dict[str, Any] | None = None,
        ) -> str:
            call_count["n"] += 1
            if call_count["n"] == 2:  # LLM2 em voo: a revisão é substituída
                CaseEvent.objects.create(
                    case_id=case.case_id,
                    event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE,
                    actor_type=ActorType.SYSTEM,
                    actor=None,
                    actor_role="system",
                    payload={
                        "classification": {ANGIO_TYPE: "not_detected"},
                        "source_fingerprint": source_fingerprint_for_case(case),
                        "anonymized_text_digest": anonymized_text_digest_for_case(case),
                        "pseudonym_map_digest": pseudonym_map_digest_for_case(case),
                    },
                )
                # O original só entra na fila de agendamento durante a LLM2.
                holder["prior"] = _scheduling_prior(nir_user)
            return super().complete(model, messages, json_schema=json_schema)

    fake = _ReviewSupersededClient(
        [
            json.dumps(_artifact(["nefrostomia"])),  # LLM1 confirmada (sem candidato ainda)
            json.dumps(_llm2_response([ANGIO_TYPE])),  # LLM2 estagnada (descartada)
            json.dumps(_artifact([ANGIO_TYPE])),  # reconciliação HD-001 que casa
        ]
    )
    _run(case, fake)
    case.refresh_from_db()
    assert _models(fake) == [FAKE_MODEL_LLM1, FAKE_MODEL_LLM2, FAKE_MODEL_LLM1]
    assert case.status == CaseStatus.CLEANED
    link = _sched_link(case)
    assert link.prior_case_id == holder["prior"].pk
    assert case.lock_token is None
    assert case.clinical_data_disposed_at is None
    assert CaseEventType.CASE_LLM2_COMPLETED.value not in _event_types(case)
    assert (
        holder["prior"].events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
    )


# ── R1.4: SQL trace, deriva ×3 no pipeline ──────────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_pipeline_post_llm1_atomic_mutex_precedes_every_row_lock(
    owner_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scheduling_prior(owner_user)
    incoming = _extracting_incoming(owner=owner_user, number="orc-1")
    fake = ScriptedLlmClient([json.dumps(_artifact([ANGIO_TYPE]))])
    from django.db.backends.utils import CursorWrapper

    seen: list[str] = []
    real_execute = CursorWrapper.execute
    real_hook: Any = getattr(orchestrator_module, "archive_scheduling_repeat_after_llm1")

    def _execute(self: Any, sql: Any, params: Any = None) -> Any:
        seen.append(str(sql))
        return real_execute(self, sql, params)

    def _scoped_hook(*args: Any, **kwargs: Any) -> Any:
        seen.clear()  # captura desde a ENTRADA do atomic pós-LLM1 (nunca no mutex)
        return real_hook(*args, **kwargs)

    monkeypatch.setattr(CursorWrapper, "execute", _execute)
    monkeypatch.setattr(orchestrator_module, "archive_scheduling_repeat_after_llm1", _scoped_hook)
    _run(incoming, fake)
    advisory = next(i for i, sql in enumerate(seen) if "pg_advisory_xact_lock" in sql)
    row_lock = next(i for i, sql in enumerate(seen) if "FOR UPDATE" in sql.upper())
    assert advisory < row_lock
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED


@NEW_MODE
@pytest.mark.django_db
def test_persistent_drift_fails_guarded_with_prior_intact_and_zero_llm_repeat(
    owner_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = _scheduling_prior(owner_user)
    prior_before = _snapshot(prior)
    prior_events = _event_types(prior)
    incoming = _extracting_incoming(owner=owner_user, number="orc-1")
    checks = {"n": 0}

    def _always_drift(**kwargs: Any) -> None:
        checks["n"] += 1
        raise reappearance_module._DriftDetectedError("drift injetado")

    monkeypatch.setattr(reappearance_module, "_check_drift", _always_drift)
    fake = ScriptedLlmClient([json.dumps(_artifact([ANGIO_TYPE]))])
    _run(incoming, fake)
    incoming.refresh_from_db()
    assert checks["n"] == 3
    assert incoming.status == CaseStatus.FAILED
    assert _models(fake) == [FAKE_MODEL_LLM1]  # zero repetição de LLM
    assert incoming.lock_token is None  # só a própria lease foi liberada
    failed = incoming.events.get(event_type=CaseEventType.CASE_STATUS_FAILED)
    assert failed.payload.get("reason") == "reappearance_drift"
    assert not CaseReappearance.objects.exists()
    assert Schedule.objects.count() == 0
    assert _snapshot(prior) == prior_before
    assert _event_types(prior) == prior_events


# ── R2.1: adiamento por lease de worker alheio + retry sem LLM ──────────────


@NEW_MODE
@pytest.mark.django_db
def test_other_worker_lease_on_original_defers_then_retry_archives_without_llm(
    owner_user: User,
) -> None:
    prior = _scheduling_prior(owner_user)
    claim_case_lock(prior, user=None, context="worker_pdf", role=SYSTEM_ROLE)
    prior_before = _snapshot(prior)
    incoming = _extracting_incoming(owner=owner_user, number="orc-1")
    fake = ScriptedLlmClient(
        [json.dumps(_artifact([ANGIO_TYPE])), json.dumps(_llm2_response([ANGIO_TYPE]))]
    )
    _run(incoming, fake)
    incoming.refresh_from_db()
    # Adiado: pendente + agenda durável; o incoming segue o fluxo normal ao médico.
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    link = _sched_link(incoming)
    assert link.state == ReappearanceState.PENDING
    assert Schedule.objects.filter(cluster="llm", schedule_type=Schedule.ONCE).count() == 1
    assert _snapshot(prior) == prior_before  # lease alheia jamais tomada
    # Lease do worker alheio termina → retry aplica o MESMO vínculo, sem LLM.
    Case.objects.filter(pk=prior.pk).update(
        locked_by=None,
        locked_at=None,
        locked_until=None,
        lock_token=None,
        lock_context="",
        lock_role="",
    )
    CaseReappearance.objects.filter(pk=link.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )

    def _forbidden() -> object:
        raise AssertionError("retry nunca chama LLM")

    calls_before = len(fake.calls)
    with override_settings(LLM_CLIENT_FACTORY=_forbidden):
        reappearance_module.handle_reappearance_retry(link.pk, link.revision_digest, 1)
    assert len(fake.calls) == calls_before
    applied = CaseReappearance.objects.get(pk=link.pk)
    assert applied.state == ReappearanceState.APPLIED
    incoming.refresh_from_db()
    assert incoming.status == CaseStatus.CLEANED
    assert incoming.clinical_data_disposed_at is None


@pytest.mark.django_db
def test_orchestrator_exposes_post_llm1_hook() -> None:
    assert hasattr(orchestrator_module, "archive_scheduling_repeat_after_llm1")
    assert uuid.UUID(int=0)  # import uuid usado


@NEW_MODE
@pytest.mark.django_db
def test_llm_summarizing_resumption_with_stale_gate_does_not_archive_prematurely(
    nir_user: User,
) -> None:
    """Fix 2: retomada em LLM_SUMMARIZING com gate obsoleto usa reconciled=False."""
    from apps.cases.procedures import set_declared_procedures
    from apps.pipeline.llm1_service import run_llm1_extraction

    prior = _scheduling_prior(nir_user, declared=(OTHER_TYPE,))
    prior_before = _snapshot(prior)
    case = _confirmed_case_ready(nir_user)  # confirmado = {art_perif}
    _identify(case)
    # Extração prévia válida para o caso marcado antes de entrar em LLM_SUMMARIZING.
    with override_settings(
        LLM_CLIENT_FACTORY=lambda: ScriptedLlmClient([json.dumps(_artifact(["nefrostomia"]))]),
        LLM1_MODEL=FAKE_MODEL_LLM1,
    ):
        run_llm1_extraction(case, user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    # Declaração muda após confirmação → gate obsoleto; caso entra em LLM_SUMMARIZING.
    set_declared_procedures(case, (OTHER_TYPE,), user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_SUMMARIZING

    # Com reconciled=False, NÃO arquiva na entrada de LLM_SUMMARIZING sem LLM1;
    # segue para LLM2 sem arquivamento prematuro.
    fake = ScriptedLlmClient([json.dumps(_llm2_response([OTHER_TYPE]))])
    _run(case, fake)
    case.refresh_from_db()
    assert len(fake.calls) == 1
    assert _models(fake) == [FAKE_MODEL_LLM2]
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.filter(
        incoming_case=case, resolution_kind=ResolutionKind.SCHEDULING_REPEAT
    ).exists()
    assert _snapshot(prior) == prior_before
