"""Aplicação concorrente segura (S2 unidade B — R4.1–R4.3, AUD-01/07/14).

Chave de mutex versionada signed-64 SHA-256, revalidação pós-locks da chave
e do conjunto de participantes, no máximo TRÊS tentativas transacionais por
chamada e erro técnico explícito na deriva persistente — sem sucesso
silencioso, sem efeito parcial e sem repetir LLM. Matriz de corridas PG com
conexões separadas/barreiras para finalização×retry, dois incoming pelo
caminho final real, decisão médica×finalização e contenção de lease.

Idempotência de vínculo PENDING reentrante (AUD-04/R4.4) é da unidade C: a
corrida finalização×retry usa revisão obsoleta para não depender dela; o
limite é documentado no appendix da unidade.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connections, transaction
from django.test import override_settings
from django.utils import timezone
from django_q.models import Schedule

from apps.accounts.models import User
from apps.cases import reappearance as reappearance_module
from apps.cases.events import CaseEventType
from apps.cases.locks import assert_case_lock, claim_case_lock, release_case_lock
from apps.cases.models import (
    Case,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    DoctorDisposition,
    ReappearanceState,
    ResolutionKind,
)
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.pipeline import orchestrator as orchestrator_module
from apps.pipeline.orchestrator import process_case_pipeline

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)

SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
_NAME = "Maria da Silva"
_OTHER_NAME = "Joao Pereira"

_FAKE_MODEL_LLM1 = "modelo-llm1-teste"
_FAKE_MODEL_LLM2 = "modelo-llm2-teste"
_ANONYMIZED_TEXT = (
    "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente. "
    "Solicitada avaliacao hemodinamica."
)
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "JOSE MARIA DE SOUZA", "entity_type": "PERSON"},
    "<DATA_1>": {"value": "05/03/1972", "entity_type": "DATE_TIME"},
}


@pytest.fixture(autouse=True)
def _seeded_prompts() -> None:
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture(autouse=True)
def _pipeline_dispatch_paused() -> Iterator[None]:
    """Setup não dispara pipeline inline via on_commit (threads chamam direto)."""
    with override_settings(LLM_RUN_TASKS_INLINE=False):
        yield


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


def _identity(case: Case, *, name: str = _NAME, number: str = "123") -> Case:
    case.patient_name = name
    case.agency_record_number = number
    case.save(update_fields=["patient_name", "agency_record_number"])
    return case


def _make_prior(owner: User, *, days_ago: int = 5, name: str = _NAME) -> Case:
    prior = Case.objects.create(created_by=owner)
    _declared(prior, ANGIO_TYPE)
    _identity(prior, name=name)
    _awaiting(prior)
    Case.objects.filter(pk=prior.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    prior.refresh_from_db()
    return prior


def _make_incoming(owner: User) -> Case:
    incoming = Case.objects.create(created_by=owner)
    _declared(incoming, ANGIO_TYPE)
    _identity(incoming)
    _awaiting(incoming)
    return incoming


def _make_extracting(owner: User) -> Case:
    """Incoming em LLM_EXTRACTING com artefatos, pronto para o orquestrador real."""
    case = Case.objects.create(created_by=owner)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    _declared(case, ANGIO_TYPE)
    case.patient_name = _NAME
    case.agency_record_number = "123"
    case.anonymized_text = _ANONYMIZED_TEXT
    case.pseudonym_map = dict(_PSEUDONYMS)
    case.save()
    assert case.status == CaseStatus.LLM_EXTRACTING
    return case


class _ScriptedLlmClient:
    """Fake com roteiro compartilhado LLM1+LLM2 (só fronteira LLM)."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, list[dict[str, str]], dict[str, Any] | None]] = []

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append((model, list(messages), json_schema))
        if not self.responses:
            raise AssertionError("cliente fake sem respostas agendadas")
        return self.responses.pop(0)


def _artifact(procedure_types: list[str]) -> dict[str, object]:
    return {
        "pedido": {
            "procedimentos_solicitados": procedure_types,
            "evidence_spans": [
                {
                    "field_path": "pedido.procedimentos_solicitados",
                    "excerpt": "O relatorio solicita os procedimentos listados acima.",
                }
            ],
        },
        "contexto_clinico": "Paciente <PESSOA_1> com claudicacao intermitente ha tres meses.",
        "linha_do_tempo": [],
        "exames": None,
        "medicacoes": [],
        "comorbidades": [],
        "contraindicacoes": [],
        "trechos_nao_classificados": [],
    }


def _llm2_response(procedure_types: list[str]) -> dict[str, object]:
    return {
        "summary_text": "Sumario clinico em portugues, sem intercorrencias.",
        "procedures": [
            {
                "procedure_type": procedure_type,
                "suggestion": "aceitar",
                "motivos": ["sem contraindicoes identificadas"],
                "evidence_spans": [],
            }
            for procedure_type in procedure_types
        ],
    }


def _fake_pair() -> _ScriptedLlmClient:
    return _ScriptedLlmClient(
        [json.dumps(_artifact([ANGIO_TYPE])), json.dumps(_llm2_response([ANGIO_TYPE]))]
    )


def _run_pipeline(case: Case, fake: _ScriptedLlmClient) -> None:
    with override_settings(
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM1_MODEL=_FAKE_MODEL_LLM1,
        LLM2_MODEL=_FAKE_MODEL_LLM2,
    ):
        process_case_pipeline(case.case_id)


def _drift_error_type() -> type[Exception]:
    error = getattr(reappearance_module, "ReappearanceDriftError", None)
    assert isinstance(error, type) and issubclass(error, Exception), (
        "contrato B exige erro técnico explícito ReappearanceDriftError"
    )
    return error


def _install_name_flip(
    monkeypatch: pytest.MonkeyPatch, incoming_pk: uuid.UUID, *, max_flips: int
) -> dict[str, int]:
    """Injeta deriva de nome: renomeia o incoming antes do lock do grupo.

    Simula rename comprometido entre a leitura sem lock e o ``select_for_update``:
    a chave travada difere da adquirida. O rollback da tentativa desfaz o rename.
    """
    mutex_calls = {"count": 0}
    lock_calls = {"count": 0}
    real_acquire = reappearance_module.acquire_reappearance_mutex
    real_lock_group = reappearance_module._lock_group

    def _counting_acquire(key: int) -> None:
        mutex_calls["count"] += 1
        real_acquire(key)

    def _flipping_lock_group(case_ids: list[uuid.UUID]) -> dict[uuid.UUID, Case]:
        lock_calls["count"] += 1
        if lock_calls["count"] <= max_flips:
            Case.objects.filter(pk=incoming_pk).update(patient_name=_OTHER_NAME)
        return real_lock_group(case_ids)

    monkeypatch.setattr(reappearance_module, "acquire_reappearance_mutex", _counting_acquire)
    monkeypatch.setattr(reappearance_module, "_lock_group", _flipping_lock_group)
    return mutex_calls


# ── Chave de mutex (AUD-14) ────────────────────────────────────────────────


@pytest.mark.django_db
def test_mutex_key_is_versioned_signed64_sha256() -> None:
    """R4.1/AUD-14: chave = primeiros 8 bytes SHA-256 de v1:reappearance:<nome>."""
    expected = int.from_bytes(
        hashlib.sha256(b"v1:reappearance:MARIADASILVA").digest()[:8], "big", signed=True
    )
    key = reappearance_module._mutex_key(_NAME, "fallback-ignorado")
    assert isinstance(key, int)
    assert key == expected
    assert -(2**63) <= key < 2**63
    assert reappearance_module._mutex_key("MARIA  da silva", "outro") == expected


# ── Deriva em resolve (AUD-01): ordem, 3 tentativas, erro explícito ────────


@NEW_MODE
@pytest.mark.django_db
def test_resolve_name_drift_retries_and_applies_on_third_attempt(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.2: deriva nas tentativas 1–2 aborta sem efeito; a 3ª aplica limpo."""
    prior = _make_prior(nir_user)
    incoming = _make_incoming(nir_user)
    mutex_calls = _install_name_flip(monkeypatch, incoming.pk, max_flips=2)
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None
    assert link.state == ReappearanceState.APPLIED
    assert mutex_calls["count"] == 3
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.patient_name == _NAME  # rollback desfez os flips
    assert incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).count() == 1
    assert prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).count() == 1


@NEW_MODE
@pytest.mark.django_db
def test_resolve_persistent_drift_raises_after_exactly_three_attempts(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.2: deriva persistente → erro técnico explícito após 3 tentativas, sem efeito."""
    prior = _make_prior(nir_user)
    incoming = _make_incoming(nir_user)
    mutex_calls = _install_name_flip(monkeypatch, incoming.pk, max_flips=99)
    with pytest.raises(_drift_error_type()):
        reappearance_module.resolve_medical_replacement(incoming)
    assert mutex_calls["count"] == 3
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.patient_name == _NAME
    assert not CaseReappearance.objects.exists()
    assert not incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()


@NEW_MODE
@pytest.mark.django_db(transaction=True)
def test_resolve_member_drift_via_separate_connection_sees_new_member(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.2: novo plausível comprometido em conexão separada após a coleta.

    A 1ª tentativa aborta; a retomada recoletam o conjunto (2 plausíveis) e
    não aplica — nunca sob visão obsoleta do grupo.
    """
    prior = _make_prior(nir_user)
    incoming = _make_incoming(nir_user)
    owner_pk = nir_user.pk
    mutex_calls = {"count": 0}
    real_acquire = reappearance_module.acquire_reappearance_mutex
    real_lock_group = reappearance_module._lock_group
    lock_calls = {"count": 0}

    def _counting_acquire(key: int) -> None:
        mutex_calls["count"] += 1
        real_acquire(key)

    def _member_lock_group(case_ids: list[uuid.UUID]) -> dict[uuid.UUID, Case]:
        lock_calls["count"] += 1
        if lock_calls["count"] == 1:

            def _create_member() -> None:
                try:
                    owner = User.objects.get(pk=owner_pk)
                    member = Case.objects.create(created_by=owner)
                    _declared(member, ANGIO_TYPE)
                    _identity(member)
                    _awaiting(member)
                    Case.objects.filter(pk=member.pk).update(
                        created_at=timezone.now() - timedelta(days=2)
                    )
                finally:
                    connections.close_all()

            helper = threading.Thread(target=_create_member)
            helper.start()
            helper.join(timeout=60)
            assert not helper.is_alive()
        return real_lock_group(case_ids)

    monkeypatch.setattr(reappearance_module, "acquire_reappearance_mutex", _counting_acquire)
    monkeypatch.setattr(reappearance_module, "_lock_group", _member_lock_group)
    assert reappearance_module.resolve_medical_replacement(incoming) is None
    assert mutex_calls["count"] == 2
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()


# ── Deriva em retry (AUD-01): estado preservado + erro sinalizado ──────────


@NEW_MODE
@pytest.mark.django_db
def test_retry_persistent_drift_signals_error_and_preserves_pending(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.2: retry com deriva persistente sinaliza erro e preserva o vínculo."""
    prior = _make_prior(nir_user)
    incoming = _make_incoming(nir_user)
    doctor = User.objects.create_user(username="med-b-retry", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None and link.state == ReappearanceState.PENDING
    from apps.cases.locks import release_case_lock as _release

    prior.refresh_from_db()
    assert prior.lock_token is not None
    _release(prior, prior.lock_token)
    CaseReappearance.objects.filter(pk=link.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    link.refresh_from_db()
    attempts_before = link.attempt_count
    digest_before = link.revision_digest
    mutex_calls = _install_name_flip(monkeypatch, incoming.pk, max_flips=99)
    with pytest.raises(_drift_error_type()):
        reappearance_module.handle_reappearance_retry(
            str(link.pk), link.revision_digest, link.attempt_count
        )
    assert mutex_calls["count"] == 3
    link.refresh_from_db()
    assert link.state == ReappearanceState.PENDING
    assert link.attempt_count == attempts_before
    assert link.revision_digest == digest_before
    prior.refresh_from_db()
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()


# ── Deriva no atomic final real (AUD-01): FAILED limpo, sem LLM repetido ───


@NEW_MODE
@pytest.mark.django_db
def test_finalize_persistent_drift_fails_incoming_clean_via_real_orchestrator(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.2/D4: deriva persistente no final real → FAILED guardado, prior intacto.

    Pipeline usa o FAILED existente (incoming ainda em processamento), só a
    própria lease é liberada, LLM roda exatamente uma vez (2 chamadas) e
    nenhuma recuperação/reagendamento é criada para este erro.
    """
    prior = _make_prior(nir_user)
    incoming = _make_extracting(owner=nir_user)
    mutex_calls = _install_name_flip(monkeypatch, incoming.pk, max_flips=99)
    fake = _fake_pair()
    _run_pipeline(incoming, fake)
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.FAILED
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()
    assert len(fake.calls) == 2
    assert mutex_calls["count"] == 3
    assert incoming.lock_token is None  # só a própria lease foi liberada
    assert not Schedule.objects.filter(name__startswith="reappearance-retry:").exists()
    failed = incoming.events.filter(event_type=CaseEventType.CASE_STATUS_FAILED)
    failed_event = failed.first()
    assert failed.count() == 1 and failed_event is not None
    assert failed_event.payload.get("reason") == "reappearance_drift"


@NEW_MODE
@pytest.mark.django_db
def test_finalize_drift_on_third_attempt_completes_with_single_llm_pass(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.2: deriva nas tentativas 1–2; a 3ª conclui e substitui, LLM uma vez."""
    prior = _make_prior(nir_user)
    incoming = _make_extracting(owner=nir_user)
    mutex_calls = _install_name_flip(monkeypatch, incoming.pk, max_flips=2)
    fake = _fake_pair()
    _run_pipeline(incoming, fake)
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert prior.status == CaseStatus.CLEANED
    assert (
        CaseReappearance.objects.filter(
            incoming_case=incoming,
            prior_case=prior,
            state=ReappearanceState.APPLIED,
        ).count()
        == 1
    )
    assert len(fake.calls) == 2
    assert mutex_calls["count"] == 3


# ── Matriz de corridas PG (AUD-07) ─────────────────────────────────────────


def _run_named_threads(
    named_targets: list[tuple[str, Callable[[], None]]], *, timeout: float = 180.0
) -> tuple[list[BaseException], bool]:
    """Roda alvos com nomes estáveis (dispatch do fake LLM por nome de thread)."""
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(named_targets))

    def _wrap(target: Callable[[], None]) -> None:
        try:
            barrier.wait(timeout=60)
            target()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [
        threading.Thread(target=_wrap, args=(target,), name=name) for name, target in named_targets
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout)
    return errors, any(thread.is_alive() for thread in threads)


def _install_thread_llm_fakes(
    monkeypatch: pytest.MonkeyPatch, fakes: dict[str, _ScriptedLlmClient]
) -> None:
    """Fake LLM por nome de thread (override_settings não atravessa threads)."""

    def _client() -> _ScriptedLlmClient:
        return fakes[threading.current_thread().name]

    monkeypatch.setattr("apps.pipeline.llm1_service.get_llm_client", _client)
    monkeypatch.setattr("apps.pipeline.llm2_service.get_llm_client", _client)


@NEW_MODE
@pytest.mark.django_db(transaction=True)
def test_race_finalization_times_retry_stale_revision_no_deadlock(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.3: orquestrador real × retry (revisão obsoleta) — sem deadlock/erro.

    Revisão obsoleta evita depender da idempotência de PENDING reentrante
    (unidade C, AUD-04); a corrida prova ordenação de locks e preservação.
    """
    prior = _make_prior(nir_user)
    incoming = _make_extracting(owner=nir_user)
    incoming.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    assert incoming.status == CaseStatus.LLM_SUMMARIZING
    doctor = User.objects.create_user(username="med-b-final-retry", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = CaseReappearance.objects.create(
        incoming_case=incoming,
        prior_case=prior,
        canonical_case=incoming,
        revision_digest="v1:revisao-obsoleta",
        evidence_code="occurrence_number",
        state=ReappearanceState.PENDING,
        resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
        attempt_count=1,
        first_attempt_at=timezone.now() - timedelta(minutes=5),
        next_attempt_at=timezone.now() - timedelta(seconds=1),
        last_reason_code="lease_busy",
    )
    link_pk = link.pk
    incoming_pk = incoming.pk
    fake = _fake_pair()
    _install_thread_llm_fakes(monkeypatch, {"finalize": fake})

    def _finalize() -> None:
        process_case_pipeline(incoming_pk)

    def _retry() -> None:
        reappearance_module.handle_reappearance_retry(link_pk, "v1:revisao-obsoleta", 1)

    errors, deadlocked = _run_named_threads([("finalize", _finalize), ("retry", _retry)])
    assert not deadlocked, "deadlock finalization×retry"
    assert not errors
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.filter(state=ReappearanceState.APPLIED).exists()
    assert not prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()


@NEW_MODE
@pytest.mark.django_db(transaction=True)
def test_race_two_incomings_via_actual_final_atomic_no_deadlock(nir_user: User) -> None:
    """R4.3: dois incoming pelo atomic final real — cadeia única, sem deadlock.

    Usa ``finalize_summarization`` (o seam D4 FINAL que o orquestrador chama)
    em threads: dois orquestradores concorrentes esbarram no race pré-existente
    de ``refresh_from_db`` do django-fsm (flag protegida em nível de classe),
    fora do escopo B — documentado no appendix. Contagens LLM do caminho
    completo estão nos testes de orquestrador em thread única.
    """
    prior = _make_prior(nir_user)
    first = _make_extracting(owner=nir_user)
    second = _make_extracting(owner=nir_user)
    first.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    second.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    assert first.status == CaseStatus.LLM_SUMMARIZING
    assert second.status == CaseStatus.LLM_SUMMARIZING
    # Anterioridade estrita determinística (sem empate de created_at).
    Case.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(days=3))
    Case.objects.filter(pk=second.pk).update(created_at=timezone.now() - timedelta(days=1))
    first_token = claim_case_lock(first, user=None, context="worker_llm", role=SYSTEM_ROLE).token
    second_token = claim_case_lock(second, user=None, context="worker_llm", role=SYSTEM_ROLE).token

    def _finalize_one(case_pk: uuid.UUID, token: uuid.UUID) -> None:
        outcome = reappearance_module.finalize_summarization(
            case_pk=case_pk,
            lock_token=token,
            marked_execution=False,
            execution_digests=None,
            execution_authority=(None, None, None),
            execution_declared=(),
            user=None,
            role=SYSTEM_ROLE,
        )
        assert outcome.status == "completed"

    errors, deadlocked = _run_named_threads(
        [
            ("first", lambda: _finalize_one(first.pk, first_token)),
            ("second", lambda: _finalize_one(second.pk, second_token)),
        ]
    )
    assert not deadlocked
    assert not errors
    # Duas intercalações válidas (como resolve×resolve): 1 vínculo (um só
    # substitui o original) ou cadeia de 2 (o segundo substitui o primeiro).
    applied = CaseReappearance.objects.filter(
        state=ReappearanceState.APPLIED,
        resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
    )
    assert 1 <= applied.count() <= 2
    prior.refresh_from_db()
    first.refresh_from_db()
    second.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    representative = reappearance_module.current_representative(prior)
    representative.refresh_from_db()
    assert representative.status == CaseStatus.AWAITING_DOCTOR
    for case in (first, second):
        assert case.status in (CaseStatus.AWAITING_DOCTOR, CaseStatus.CLEANED)
        assert (
            CaseReappearance.objects.filter(
                incoming_case=case, state=ReappearanceState.APPLIED
            ).count()
            <= 1
        )
    for link in applied:
        assert (
            link.incoming_case.events.filter(
                event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED,
                payload__reappearance_id=str(link.pk),
            ).count()
            == 1
        )
        assert (
            link.prior_case.events.filter(
                event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED,
                payload__reappearance_id=str(link.pk),
            ).count()
            == 1
        )


@NEW_MODE
@pytest.mark.django_db(transaction=True)
def test_race_medical_decision_times_finalization_preserves_winner(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.3: decisão médica × finalização — exatamente um vencedor, sem deadlock."""
    prior = _make_prior(nir_user)
    incoming = _make_extracting(owner=nir_user)
    prior_pk, incoming_pk = prior.pk, incoming.pk
    doctor = User.objects.create_user(username="med-b-decide", password="x")
    fake = _fake_pair()
    _install_thread_llm_fakes(monkeypatch, {"finalize": fake})

    def _decide() -> None:
        case = Case.objects.get(pk=prior_pk)
        record_doctor_procedure_decisions(
            case, {ANGIO_TYPE: ("denied", "sem indicacao")}, user=doctor, role="doctor"
        )

    def _finalize() -> None:
        process_case_pipeline(incoming_pk)

    errors, deadlocked = _run_named_threads([("decide", _decide), ("finalize", _finalize)])
    assert not deadlocked
    from django_fsm import TransitionNotAllowed

    # Único erro limpo aceitável: decisão sobre caso já substituído (CLEANED).
    assert all(isinstance(exc, TransitionNotAllowed) for exc in errors), errors
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    decision_won = prior.status == CaseStatus.DOCTOR_DENIED
    substitution_won = prior.status == CaseStatus.CLEANED
    assert decision_won != substitution_won, "exatamente um vencedor"
    row = prior.procedures.get(procedure_type=ANGIO_TYPE)
    if decision_won:
        assert row.doctor_disposition == DoctorDisposition.DENIED
        assert not CaseReappearance.objects.exists()
    else:
        assert row.doctor_disposition == DoctorDisposition.PENDING
        assert (
            CaseReappearance.objects.filter(
                incoming_case=incoming,
                prior_case=prior,
                state=ReappearanceState.APPLIED,
            ).count()
            == 1
        )


@NEW_MODE
@pytest.mark.django_db(transaction=True)
def test_race_claim_lease_times_finalization_consistent_winner(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.3: claim de lease × finalização — defer ou aplica, nunca ambos/nada."""
    prior = _make_prior(nir_user)
    incoming = _make_extracting(owner=nir_user)
    prior_pk, incoming_pk = prior.pk, incoming.pk
    doctor = User.objects.create_user(username="med-b-claim", password="x")
    fake = _fake_pair()
    _install_thread_llm_fakes(monkeypatch, {"finalize": fake})

    def _claim() -> None:
        case = Case.objects.get(pk=prior_pk)
        claim_case_lock(case, user=doctor, context="doctor_decision", role="doctor")

    def _finalize() -> None:
        process_case_pipeline(incoming_pk)

    errors, deadlocked = _run_named_threads([("claim", _claim), ("finalize", _finalize)])
    assert not deadlocked
    assert not errors
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    deferred = CaseReappearance.objects.filter(
        incoming_case=incoming, state=ReappearanceState.PENDING
    ).exists()
    applied = CaseReappearance.objects.filter(
        incoming_case=incoming, state=ReappearanceState.APPLIED
    ).exists()
    assert deferred != applied, "exatamente um desfecho consistente"
    if deferred:
        assert prior.status == CaseStatus.AWAITING_DOCTOR
    else:
        assert prior.status == CaseStatus.CLEANED


@NEW_MODE
@pytest.mark.django_db(transaction=True)
def test_race_release_lease_times_finalization_consistent_winner(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.3: release da lease × finalização — aplica ou adia, consistentemente."""
    prior = _make_prior(nir_user)
    incoming = _make_extracting(owner=nir_user)
    prior_pk, incoming_pk = prior.pk, incoming.pk
    doctor = User.objects.create_user(username="med-b-release", password="x")
    lock = claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    token = lock.token
    fake = _fake_pair()
    _install_thread_llm_fakes(monkeypatch, {"finalize": fake})

    def _release() -> None:
        case = Case.objects.get(pk=prior_pk)
        release_case_lock(case, token)

    def _finalize() -> None:
        process_case_pipeline(incoming_pk)

    errors, deadlocked = _run_named_threads([("release", _release), ("finalize", _finalize)])
    assert not deadlocked
    assert not errors
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    deferred = CaseReappearance.objects.filter(
        incoming_case=incoming, state=ReappearanceState.PENDING
    ).exists()
    applied = CaseReappearance.objects.filter(
        incoming_case=incoming, state=ReappearanceState.APPLIED
    ).exists()
    assert deferred != applied
    if applied:
        assert prior.status == CaseStatus.CLEANED
    else:
        assert prior.status == CaseStatus.AWAITING_DOCTOR


@NEW_MODE
@pytest.mark.django_db(transaction=True)
def test_race_expired_lease_takeover_times_finalization(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4.3: lease expirada × claim concorrente × finalização — desfecho consistente."""
    prior = _make_prior(nir_user)
    incoming = _make_extracting(owner=nir_user)
    prior_pk, incoming_pk = prior.pk, incoming.pk
    old_doctor = User.objects.create_user(username="med-b-old", password="x")
    claim_case_lock(
        prior, user=old_doctor, context="doctor_decision", role="doctor", lease_seconds=-1
    )
    new_doctor = User.objects.create_user(username="med-b-new", password="x")
    fake = _fake_pair()
    _install_thread_llm_fakes(monkeypatch, {"finalize": fake})

    def _takeover() -> None:
        case = Case.objects.get(pk=prior_pk)
        claim_case_lock(case, user=new_doctor, context="doctor_decision", role="doctor")

    def _finalize() -> None:
        process_case_pipeline(incoming_pk)

    errors, deadlocked = _run_named_threads([("takeover", _takeover), ("finalize", _finalize)])
    assert not deadlocked
    assert not errors
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    deferred = CaseReappearance.objects.filter(
        incoming_case=incoming, state=ReappearanceState.PENDING
    ).exists()
    applied = CaseReappearance.objects.filter(
        incoming_case=incoming, state=ReappearanceState.APPLIED
    ).exists()
    assert deferred != applied
    if deferred:
        assert prior.status == CaseStatus.AWAITING_DOCTOR
        assert prior.locked_by_id == new_doctor.pk
    else:
        assert prior.status == CaseStatus.CLEANED


# ── Reparos B (readiness): retry aplica-e-retorna, FAILED guardado, R4.3 válido ──


def _make_pending_link_via_lease(nir_user: User) -> tuple[Case, Case, CaseReappearance]:
    """Prior/incoming com PENDING via lease viva, depois lease liberada p/ retry."""
    prior = _make_prior(nir_user)
    incoming = _make_incoming(nir_user)
    doctor = User.objects.create_user(username="med-b-repair-pending", password="x")
    claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")
    link = reappearance_module.resolve_medical_replacement(incoming)
    assert link is not None and link.state == ReappearanceState.PENDING
    prior.refresh_from_db()
    assert prior.lock_token is not None
    release_case_lock(prior, prior.lock_token)
    CaseReappearance.objects.filter(pk=link.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    link.refresh_from_db()
    return prior, incoming, link


@NEW_MODE
@pytest.mark.django_db
def test_retry_drift_then_valid_third_attempt_applies_once_and_returns(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reparo 1: deriva em 1–2 e sucesso na 3ª aplica 1× e RETORNA (sem erro pós-commit)."""
    prior, incoming, link = _make_pending_link_via_lease(nir_user)
    attempts_before = link.attempt_count
    mutex_calls = _install_name_flip(monkeypatch, incoming.pk, max_flips=2)
    # Não deve levantar ReappearanceDriftError após o commit da 3ª tentativa.
    reappearance_module.handle_reappearance_retry(
        str(link.pk), link.revision_digest, link.attempt_count
    )
    assert mutex_calls["count"] == 3
    link.refresh_from_db()
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert link.state == ReappearanceState.APPLIED
    assert link.attempt_count == attempts_before
    assert link.applied_at is not None
    assert link.canonical_case_id == incoming.pk
    assert link.prior_case_id == prior.pk
    assert prior.status == CaseStatus.CLEANED
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.patient_name == _NAME
    assert incoming.reappearance_wait_started_at == prior.created_at
    assert incoming.lock_token is None
    assert prior.lock_token is None
    assert (
        incoming.events.filter(
            event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED,
            payload__reappearance_id=str(link.pk),
        ).count()
        == 1
    )
    assert (
        prior.events.filter(
            event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED,
            payload__reappearance_id=str(link.pk),
        ).count()
        == 1
    )
    assert not Schedule.objects.filter(name__startswith=f"reappearance-retry:{link.pk}:").exists()


@NEW_MODE
@pytest.mark.django_db
def test_retry_normal_success_acquires_mutex_exactly_once(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reparo 1: sucesso normal não recomeça transação/mutex após aplicar."""
    prior, incoming, link = _make_pending_link_via_lease(nir_user)
    mutex_calls = _install_name_flip(monkeypatch, incoming.pk, max_flips=0)
    reappearance_module.handle_reappearance_retry(
        str(link.pk), link.revision_digest, link.attempt_count
    )
    assert mutex_calls["count"] == 1
    link.refresh_from_db()
    prior.refresh_from_db()
    assert link.state == ReappearanceState.APPLIED
    assert prior.status == CaseStatus.CLEANED
    assert incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).count() == 1
    assert prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).count() == 1


@NEW_MODE
@pytest.mark.django_db
def test_finalize_drift_ready_case_is_preserved_not_failed(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reparo 2: deriva persistente + caso já PRONTO (AWAITING) é preservado.

    Seam determinista: o final real (mutex/deriva ×3, LLM 1×) roda; entre o
    erro e o handler, outra execução conclui a sumarização (AWAITING). O
    handler NÃO pode chamar fail_processing (TransitionNotAllowed) nem FAILED.
    """
    prior = _make_prior(nir_user)
    incoming = _make_extracting(owner=nir_user)
    mutex_calls = _install_name_flip(monkeypatch, incoming.pk, max_flips=99)
    real_finalize = reappearance_module.finalize_summarization
    drift_error = _drift_error_type()

    def _finalize_then_ready(*args: Any, **kwargs: Any) -> Any:
        try:
            return real_finalize(*args, **kwargs)
        except drift_error:
            fresh = Case.objects.get(pk=kwargs["case_pk"])
            assert fresh.status == CaseStatus.LLM_SUMMARIZING
            fresh.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
            raise

    monkeypatch.setattr("apps.pipeline.orchestrator.finalize_summarization", _finalize_then_ready)
    fake = _fake_pair()
    _run_pipeline(incoming, fake)  # não deve propagar TransitionNotAllowed
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not incoming.events.filter(event_type=CaseEventType.CASE_STATUS_FAILED).exists()
    assert not CaseReappearance.objects.exists()
    assert len(fake.calls) == 2
    assert mutex_calls["count"] == 3
    assert incoming.lock_token is None  # finally liberou a PRÓPRIA lease
    assert not Schedule.objects.filter(name__startswith="reappearance-retry:").exists()


@NEW_MODE
@pytest.mark.django_db
def test_finalize_drift_other_lease_case_is_preserved_not_failed(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reparo 2: deriva persistente + lease de OUTRO ator é preservada.

    Seam determinista: após o final real falhar por deriva, a lease do worker
    expira e outro médico assume (claim real). Estado ainda em processamento,
    mas sem posse própria: FAILED sobrescreveria a execução alheia.
    """
    prior = _make_prior(nir_user)
    incoming = _make_extracting(owner=nir_user)
    mutex_calls = _install_name_flip(monkeypatch, incoming.pk, max_flips=99)
    real_finalize = reappearance_module.finalize_summarization
    drift_error = _drift_error_type()
    taken: dict[str, uuid.UUID] = {}
    doctor = User.objects.create_user(username="med-b-other-lease", password="x")

    def _finalize_then_takeover(*args: Any, **kwargs: Any) -> Any:
        try:
            return real_finalize(*args, **kwargs)
        except drift_error:
            case_pk = kwargs["case_pk"]
            Case.objects.filter(pk=case_pk).update(
                locked_until=timezone.now() - timedelta(seconds=1)
            )
            fresh = Case.objects.get(pk=case_pk)
            lock = claim_case_lock(fresh, user=doctor, context="doctor_decision", role="doctor")
            taken["token"] = lock.token
            raise

    monkeypatch.setattr(
        "apps.pipeline.orchestrator.finalize_summarization", _finalize_then_takeover
    )
    fake = _fake_pair()
    _run_pipeline(incoming, fake)  # não deve propagar nem falhar o caso alheio
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.LLM_SUMMARIZING
    assert incoming.lock_token == taken["token"]
    assert incoming.locked_by_id == doctor.pk
    assert not incoming.events.filter(event_type=CaseEventType.CASE_STATUS_FAILED).exists()
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()
    assert len(fake.calls) == 2
    assert mutex_calls["count"] == 3
    assert not Schedule.objects.filter(name__startswith="reappearance-retry:").exists()


@NEW_MODE
@pytest.mark.django_db(transaction=True)
def test_race_valid_finalization_times_due_retry_shared_series(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reparo 3/R4.3: finalização válida × retry VENCIDO válido (mesma série).

    Retry: revisão/tentativa vigentes, next_attempt no passado, PENDING real
    p/ o prior — chega a mutex+grupo (não é no-op precoce). Final: orquestrador
    real sobre incoming DISTINTO da mesma série (não depende do PENDING
    reentrante da unidade C). O primeiro a adquirir o mutex segura-o até o
    outro TENTAR adquirir (barreira pós-aquisição): contenção compartilhada
    provada por tentativa sobreposta, sem deadlock.
    """
    prior = _make_prior(nir_user)
    retry_incoming = _make_incoming(nir_user)
    Case.objects.filter(pk=retry_incoming.pk).update(created_at=timezone.now() - timedelta(days=2))
    retry_incoming.refresh_from_db()
    final_incoming = _make_extracting(owner=nir_user)
    final_incoming.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    Case.objects.filter(pk=final_incoming.pk).update(created_at=timezone.now() - timedelta(days=1))
    final_incoming.refresh_from_db()
    assert final_incoming.status == CaseStatus.LLM_SUMMARIZING
    digest = reappearance_module.revision_digest_for_pair(retry_incoming, prior)
    link = CaseReappearance.objects.create(
        incoming_case=retry_incoming,
        prior_case=prior,
        canonical_case=retry_incoming,
        revision_digest=digest,
        evidence_code="occurrence_number",
        state=ReappearanceState.PENDING,
        resolution_kind=ResolutionKind.MEDICAL_REPLACEMENT,
        attempt_count=1,
        first_attempt_at=timezone.now() - timedelta(minutes=5),
        next_attempt_at=timezone.now() - timedelta(seconds=1),
        last_reason_code="lease_busy",
    )
    link_pk, retry_pk, final_pk = link.pk, retry_incoming.pk, final_incoming.pk
    digest_arg = link.revision_digest
    fake = _fake_pair()
    _install_thread_llm_fakes(monkeypatch, {"finalize": fake})
    attempts: list[tuple[str, float]] = []
    acquired: list[tuple[str, float]] = []
    lock_group_calls: list[str] = []
    attempt_events = {"finalize": threading.Event(), "retry": threading.Event()}
    real_acquire = reappearance_module.acquire_reappearance_mutex
    real_lock_group = reappearance_module._lock_group

    def _contending_acquire(key: int) -> None:
        name = threading.current_thread().name
        attempts.append((name, time.monotonic()))
        attempt_events[name].set()
        real_acquire(key)
        peer = "retry" if name == "finalize" else "finalize"
        assert attempt_events[peer].wait(timeout=60), f"mutex sem contenção: {name} sem par"
        acquired.append((name, time.monotonic()))

    def _counting_lock_group(case_ids: list[uuid.UUID]) -> dict[uuid.UUID, Case]:
        lock_group_calls.append(threading.current_thread().name)
        return real_lock_group(case_ids)

    monkeypatch.setattr(reappearance_module, "acquire_reappearance_mutex", _contending_acquire)
    monkeypatch.setattr(reappearance_module, "_lock_group", _counting_lock_group)

    def _finalize() -> None:
        process_case_pipeline(final_pk)

    def _retry() -> None:
        reappearance_module.handle_reappearance_retry(link_pk, digest_arg, 1)

    errors, deadlocked = _run_named_threads([("finalize", _finalize), ("retry", _retry)])
    assert not deadlocked, "deadlock finalização×retry válido"
    assert not errors, errors
    # Retry chegou de fato a mutex+grupo (não no-op por revisão/tentativa):
    # sem deriva, cada caminho adquire o mutex exatamente 1×.
    assert {name for name, _ in attempts} == {"finalize", "retry"}
    assert {name for name, _ in acquired} == {"finalize", "retry"}
    assert len(attempts) == 2 and len(acquired) == 2, (attempts, acquired)
    assert set(lock_group_calls) == {"finalize", "retry"}
    # Contenção real: a 2ª tentativa iniciou antes de a 1ª aquisição concluir
    # (o 1º a adquirir segurou o mutex até o par tentar).
    attempts_sorted = sorted(attempts, key=lambda item: item[1])
    first_name = attempts_sorted[0][0]
    second_attempt_at = attempts_sorted[1][1]
    first_acquired_at = next(ts for name, ts in acquired if name == first_name)
    assert second_attempt_at < first_acquired_at, "tentativas de mutex não se sobrepuseram"
    link.refresh_from_db()
    prior.refresh_from_db()
    retry_incoming.refresh_from_db()
    final_incoming.refresh_from_db()
    # Retry válido aplicou exatamente o seu vínculo (qualquer interleaving).
    assert link.state == ReappearanceState.APPLIED
    assert link.attempt_count == 1
    assert prior.status == CaseStatus.CLEANED
    assert retry_incoming.reappearance_wait_started_at == prior.created_at
    assert retry_incoming.lock_token is None
    assert final_incoming.lock_token is None
    assert prior.lock_token is None
    # Representante final da cadeia está pronto; decisão nunca inventada.
    representative = reappearance_module.current_representative(prior)
    representative.refresh_from_db()
    assert representative.status == CaseStatus.AWAITING_DOCTOR
    assert (
        prior.procedures.get(procedure_type=ANGIO_TYPE).doctor_disposition
        == DoctorDisposition.PENDING
    )
    # Idempotência por vínculo: 1 evento correlacionado de cada lado por link.
    applied = list(
        CaseReappearance.objects.filter(state=ReappearanceState.APPLIED).order_by("applied_at")
    )
    assert 1 <= len(applied) <= 2
    assert all(item.pk != link.pk or item.incoming_case_id == retry_pk for item in applied)
    for item in applied:
        assert (
            item.incoming_case.events.filter(
                event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED,
                payload__reappearance_id=str(item.pk),
            ).count()
            == 1
        )
        assert (
            item.prior_case.events.filter(
                event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED,
                payload__reappearance_id=str(item.pk),
            ).count()
            == 1
        )
    for case in (retry_incoming, final_incoming):
        assert case.status in (CaseStatus.AWAITING_DOCTOR, CaseStatus.CLEANED)


# ── Guarda atômica do FAILED por deriva (B-guard, R4.2) ─────────────────────


@NEW_MODE
@pytest.mark.django_db(transaction=True)
def test_finalize_drift_check_to_write_race_serializes_winner(
    nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-guard: corrida check→write no handler de deriva é serializada.

    O concorrente (conexão PG separada) tenta concluir a sumarização
    exatamente no seam entre a checagem de estado/posse e o
    ``fail_processing``. Sem lock de linha cobrindo checagem+efeito, o
    concorrente vencedor (AWAITING) é sobrescrito pelo FAILED de uma
    instância obsoleta — estado/eventos incoerentes. Com a guarda sob
    ``select_for_update``, o concorrente bloqueia honestamente e o
    vencedor serializado é único e coerente: ou ele concluiu ANTES da
    guarda (AWAITING, sem FAILED) ou a guarda venceu (FAILED, sem
    AWAITING, concorrente rejeitado por estado). Nenhuma ordem correta
    produz FAILED+AWAITING nem repopula lease obsoleta.
    """
    prior = _make_prior(nir_user)
    incoming = _make_extracting(owner=nir_user)
    mutex_calls = _install_name_flip(monkeypatch, incoming.pk, max_flips=99)
    incoming_pk = incoming.pk
    competitor_attempt = threading.Event()
    competitor_done = threading.Event()
    competitor_outcome: dict[str, object] = {}
    spawned: dict[str, threading.Thread] = {}
    real_assert = assert_case_lock

    def _competitor_ready() -> None:
        try:
            competitor_attempt.set()
            with transaction.atomic():
                fresh = Case.objects.select_for_update().get(pk=incoming_pk)
                if fresh.status != CaseStatus.LLM_SUMMARIZING:
                    competitor_outcome["result"] = "rejected-state"
                    competitor_outcome["status_seen"] = str(fresh.status)
                else:
                    fresh.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
                    competitor_outcome["result"] = "completed"
        except BaseException as exc:  # noqa: BLE001
            competitor_outcome["result"] = "error"
            competitor_outcome["error"] = exc
        finally:
            competitor_done.set()
            connections.close_all()

    def _racing_assert(case: Case, token: uuid.UUID) -> None:
        real_assert(case, token)
        if "thread" in spawned:
            return
        thread = threading.Thread(target=_competitor_ready, name="competitor-ready")
        spawned["thread"] = thread
        thread.start()
        assert competitor_attempt.wait(timeout=60), "concorrente não tentou a linha"
        # Sem lock de linha, o concorrente conclui aqui; com a guarda
        # correta ele fica bloqueado no row lock — não esperar o commit
        # dele (deadlock honesto), só capturar se concluiu antes da escrita.
        competitor_outcome["completed_before_write"] = competitor_done.wait(timeout=5)

    monkeypatch.setattr(orchestrator_module, "assert_case_lock", _racing_assert)
    fake = _fake_pair()
    _run_pipeline(incoming, fake)
    thread = spawned.get("thread")
    assert thread is not None, "seam de corrida não foi exercido"
    thread.join(timeout=60)
    assert not thread.is_alive(), "concorrente travou após a guarda"
    incoming.refresh_from_db()
    prior.refresh_from_db()
    # Só o FAILED desta deriva conta: em transaction=True os signals de
    # setup disparam on_commit e deixam um FAILED histórico de anonimização.
    failed_count = incoming.events.filter(
        event_type=CaseEventType.CASE_STATUS_FAILED,
        payload__reason="reappearance_drift",
    ).count()
    awaiting_count = incoming.events.filter(
        event_type=CaseEventType.CASE_STATUS_AWAITING_DOCTOR
    ).count()
    # Invariante de serialização: nunca os dois desfechos na trilha.
    assert not (failed_count >= 1 and awaiting_count >= 1), (
        f"FAILED obsoleto sobrescreveu vencedor AWAITING: status={incoming.status}, "
        f"failed={failed_count}, awaiting={awaiting_count}, outcome={competitor_outcome}"
    )
    if competitor_outcome.get("completed_before_write"):
        assert competitor_outcome.get("result") == "completed"
        assert incoming.status == CaseStatus.AWAITING_DOCTOR
        assert failed_count == 0 and awaiting_count == 1
    else:
        assert competitor_outcome.get("result") == "rejected-state", competitor_outcome
        assert incoming.status == CaseStatus.FAILED
        assert failed_count == 1 and awaiting_count == 0
    assert incoming.lock_token is None  # finally liberou só a própria lease
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()
    assert len(fake.calls) == 2
    assert mutex_calls["count"] == 3
    assert not Schedule.objects.filter(name__startswith="reappearance-retry:").exists()
