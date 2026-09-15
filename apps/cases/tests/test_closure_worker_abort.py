"""Intercalação do encerramento administrativo × workers em voo (slice 002, R1–R2).

Testes de INTERCALAÇÃO do change ``painel-lista-encerramento``: um worker que
está num passo longo com a lease EXPIRADA retorna depois do encerramento
administrativo (o slice 001 criou a janela) e NÃO pode repovoar dados clínicos,
eventos nem estado. O padrão de cada teste é sempre o mesmo:

1. o worker entra no passo (a instância em memória é pré-encerramento);
2. no SEAM nomeado (fora do atomic de escrita) a lease é EXPIRADA no banco e o
   encerramento roda (``administratively_close_case``);
3. o passo prossegue e o gate in-atomic (a releitura do fix) aborta sem
   persistir — ``extracted_text``/``anonymized_text``/``structured_data``/
   ``status``/``lock`` seguem limpos.

Seam FORA do atomic (senão o re-read do fix roda antes da intercalação) e lease
já expirada ANTES de intercalar (senão a recusa de lease viva do serviço aborta
a intercalação em vez do write). Os helpers de fixture são importados dos
módulos de teste existentes de cada app (PDFs, engine stub, cliente LLM fake).

Nota sobre o seam da anonimização: o trecho entre o start e o atomic não tem
colaborador patchável; o seam é a própria função de gate
(``_is_administratively_closed``), que o teste embrulha para intercalar e então
delegar à implementação real (o gate exercitado é o de produção).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.anonymization.tasks import _is_administratively_closed, process_case_anonymization
from apps.anonymization.tests.test_tasks import (
    _case_in_anonymizing,
    _create_user,
    _stub_engine,
)
from apps.cases.closure import administratively_close_case
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseDocument, CaseProcedure, CaseStatus
from apps.intake.regulation_gate import GateResult, evaluate_regulation_report
from apps.intake.tasks import process_case_documents
from apps.intake.tests.test_tasks import (
    _OFF_PATTERN_LINES,
    _create_case_with_pdf,
    _pdf_bytes,
    _standard_report_lines,
)
from apps.pipeline.llm2_service import Llm2SummarizationResult, run_llm2_summarization
from apps.pipeline.orchestrator import process_case_pipeline
from apps.pipeline.policy import evaluate_case_policies
from apps.pipeline.prior_case import record_prior_case_lookups
from apps.pipeline.tests.test_llm2 import _llm2_response, _make_llm_summarizing_case
from apps.pipeline.tests.test_orchestrator import (
    FAKE_MODEL_LLM1,
    FAKE_MODEL_LLM2,
    _artifact,
    _make_llm_extracting_case,
)

MANAGER_ROLE = "manager"
SYSTEM_ROLE = "system"

# Motivo canônico do worker travado (lease expirada) — o caso de uso do slice.
REASON_CODE = "stuck_lock"
REASON_TEXT = "worker travado no passo longo — encerrado administrativamente"

_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.fixture(autouse=True)
def _memory_storage() -> Iterator[None]:
    """Storage default em memória (uploads nunca tocam o MEDIA_ROOT)."""
    with override_settings(STORAGES=_TEST_STORAGES):
        yield


@pytest.fixture(autouse=True)
def _seeded_prompts() -> None:
    """Seed idempotente dos prompts (os passos LLM montam prompts ativos)."""
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture
def manager_user(user_factory: Callable[[str, str], User]) -> User:
    """Supervisor do painel que encerra o caso durante o passo do worker."""
    return user_factory("manager-abort", MANAGER_ROLE)


def _event_types(case: Case) -> list[str]:
    return list(case.events.values_list("event_type", flat=True))


def _expire_worker_lease(case_id: uuid.UUID) -> None:
    """Envelhece a lease do worker no banco (libera o encerramento)."""
    Case.objects.filter(pk=case_id).update(locked_until=timezone.now() - timedelta(seconds=1))


def _close(case_id: uuid.UUID, *, user: User) -> None:
    """Encerra o caso administrativamente (passo de intercalação dos testes)."""
    administratively_close_case(
        case=Case.objects.get(pk=case_id),
        user=user,
        active_role=MANAGER_ROLE,
        reason_code=REASON_CODE,
        reason_text=REASON_TEXT,
    )


def _interleave_and_close(case_id: uuid.UUID, user: User) -> None:
    """Expira a lease do worker e encerra o caso (passo comum das intercalações)."""
    _expire_worker_lease(case_id)
    _close(case_id, user=user)


class _InterleavingLlmClient:
    """Cliente LLM fake que intercala uma callback durante a chamada."""

    def __init__(self, response: str, on_call: Callable[[], None]) -> None:
        self._response = response
        self._on_call = on_call
        self.models: list[str] = []

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        del messages, json_schema
        self.models.append(model)
        self._on_call()
        return self._response


@pytest.fixture
def owner_user() -> User:
    """Dono (criador) dos casos dos passos do pipeline."""
    return User.objects.create_user(username="dono-abort", password="senha-teste")


# ── intake: gate in-atomic do ``_extract_and_decide`` ──────────────────────


@pytest.mark.django_db
def test_intake_aborts_after_interleaved_close(
    nir_user: User,
    manager_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: o passo longo do intake (extração) retorna após o encerramento e NÃO
    grava ``extracted_text``/eventos nem ressuscita o status.

    Seam em ``evaluate_regulation_report`` (~206-216, ANTES do atomic ~218).
    Sem o gate in-atomic o caso voltaria a ANONYMIZING (``complete_pdf_extraction``
    full save) com texto gravado.
    """
    case = _create_case_with_pdf(nir_user, _pdf_bytes(_standard_report_lines()))

    def interleaving_evaluate(text: str) -> GateResult:
        _interleave_and_close(case.case_id, manager_user)
        return evaluate_regulation_report(text)

    monkeypatch.setattr("apps.intake.tasks.evaluate_regulation_report", interleaving_evaluate)

    process_case_documents(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert case.extracted_text == ""
    assert case.manual_review_required is False
    events = _event_types(case)
    assert CaseEventType.CASE_EXTRACTION_COMPLETED.value not in events
    assert CaseEventType.CASE_GATE_MANUAL_REVIEW.value not in events


@pytest.mark.django_db
def test_intake_retention_aborts_after_interleaved_close(
    nir_user: User,
    manager_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2 (retenção): relatório OFF-pattern intercalado com o encerramento NÃO
    grava o evento de retenção nem o texto — o gate é a única proteção deste
    sub-caminho (sem ele, o atomic de retenção commita via ``return False``
    do release). Sem o gate, ``CASE_GATE_MANUAL_REVIEW`` apareceria na trilha
    de um caso CLEANED e ``extracted_text`` seria reposto."""
    case = _create_case_with_pdf(nir_user, _pdf_bytes(_OFF_PATTERN_LINES))

    def interleaving_evaluate(text: str) -> GateResult:
        _interleave_and_close(case.case_id, manager_user)
        return evaluate_regulation_report(text)

    monkeypatch.setattr("apps.intake.tasks.evaluate_regulation_report", interleaving_evaluate)

    process_case_documents(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert case.extracted_text == ""
    assert case.manual_review_required is False
    events = _event_types(case)
    assert CaseEventType.CASE_GATE_MANUAL_REVIEW.value not in events
    assert CaseEventType.CASE_EXTRACTION_COMPLETED.value not in events


@pytest.mark.django_db
def test_intake_error_handler_skips_fail_processing_when_closed(
    nir_user: User,
    manager_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: erro de extração DEPOIS do encerramento não propaga nem chama
    ``fail_processing`` (o ``except`` relê o caso e no-opa em CLEANED).

    Seam em ``_extract_document_text`` (fora do atomic): intercala o
    encerramento e então levanta — sem o fix, ``fail_processing`` em CLEANED
    seria ``TransitionNotAllowed``.
    """
    case = _create_case_with_pdf(nir_user, _pdf_bytes(_standard_report_lines()))

    def exploding_extract(document: CaseDocument) -> str:
        del document
        _interleave_and_close(case.case_id, manager_user)
        raise RuntimeError("falha de extração simulada")

    monkeypatch.setattr("apps.intake.tasks._extract_document_text", exploding_extract)

    process_case_documents(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert CaseEventType.CASE_STATUS_FAILED.value not in _event_types(case)


# ── anonymization: gate antes do atomic de escrita ─────────────────────────


@pytest.mark.django_db
def test_anonymization_aborts_after_interleaved_close(
    manager_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: o passo de anonimização retorna após o encerramento e NÃO persiste
    artefatos nem a transição (o ``save()`` full do serviço ressuscitaria
    status/campos/lock).

    Seam imediatamente antes do atomic (APÓS ``start_anonymization`` e o
    porteiro de texto vazio): embrulha o gate e delega à implementação real.
    """
    user = _create_user("abort-anon")
    case = _case_in_anonymizing(user)
    _stub_engine(monkeypatch)
    real_gate = _is_administratively_closed

    def interleaving_gate(target: Case) -> bool:
        _interleave_and_close(target.case_id, manager_user)
        return real_gate(target)

    monkeypatch.setattr("apps.anonymization.tasks._is_administratively_closed", interleaving_gate)

    process_case_anonymization(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert case.anonymized_text == ""
    assert case.pseudonym_map == {}
    assert CaseEventType.CASE_ANONYMIZATION_COMPLETED.value not in _event_types(case)


# ── pipeline: gates de LLM1, LLM2, policy e prior-case ─────────────────────


@pytest.mark.django_db
def test_pipeline_llm1_aborts_after_interleaved_close(
    owner_user: User,
    manager_user: User,
) -> None:
    """R2: o LLM1 intercalado durante a chamada não persiste ``structured_data``
    nem eventos e o orquestrador aborta antes da divergência/
    ``complete_llm_extraction`` (status permanece CLEANED).

    Seam no stub do cliente LLM (padrão dos testes com clientes fake).
    """
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif",))
    artifact = _artifact(["art_perif"])
    client = _InterleavingLlmClient(
        json.dumps(artifact), lambda: _interleave_and_close(case.case_id, manager_user)
    )

    with override_settings(
        LLM_CLIENT_FACTORY=lambda: client,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    ):
        process_case_pipeline(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert case.structured_data == {}
    events = _event_types(case)
    assert CaseEventType.CASE_LLM1_COMPLETED.value not in events
    assert CaseEventType.CASE_STATUS_LLM_SUMMARIZING.value not in events
    assert client.models == [FAKE_MODEL_LLM1]


@pytest.mark.django_db
def test_pipeline_llm2_returns_sentinel_when_closed_during_call(
    owner_user: User,
    manager_user: User,
) -> None:
    """R2: o passo de LLM2 retorna o sentinel tipado sem persistir quando o caso
    é encerrado durante a chamada (``summary_text``/``suggested_action`` limpos).

    Seam no stub do cliente LLM; chamada direta ao serviço.
    """
    case = _make_llm_summarizing_case(owner=owner_user)
    client = _InterleavingLlmClient(
        json.dumps(_llm2_response()),
        lambda: _close(case.case_id, user=manager_user),
    )

    with override_settings(
        LLM_CLIENT_FACTORY=lambda: client,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    ):
        result = run_llm2_summarization(case, user=None, role=SYSTEM_ROLE)

    assert result == Llm2SummarizationResult(
        suggestions={},
        aggregate="",
        aggregate_reasons=[],
        retry_used=False,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert case.summary_text == ""
    assert case.suggested_action == {}
    assert CaseEventType.CASE_LLM2_COMPLETED.value not in _event_types(case)


@pytest.mark.django_db
def test_pipeline_policy_skips_persistence_when_closed(
    owner_user: User,
    manager_user: User,
) -> None:
    """R2: com o caso já CLEANED, ``evaluate_case_policies`` retorna ``{}`` sem
    persistir ``policy_result`` nem evento."""
    case = _cleaned_case_with_declared(owner_user, manager_user, ("art_perif",))
    events_before = case.events.count()

    result = evaluate_case_policies(case, user=None, role=SYSTEM_ROLE)

    assert result == {}
    case.refresh_from_db()
    assert case.policy_result == {}
    assert case.events.count() == events_before
    assert CaseEventType.CASE_POLICY_EVALUATED.value not in _event_types(case)


@pytest.mark.django_db
def test_pipeline_prior_case_returns_none_when_closed(
    owner_user: User,
    manager_user: User,
) -> None:
    """R2: com o caso já CLEANED, ``record_prior_case_lookups`` no-opa (sem
    eventos ``PRIOR_CASE_LOOKUP``)."""
    case = _cleaned_case_with_declared(owner_user, manager_user, ("art_perif",))
    events_before = case.events.count()

    record_prior_case_lookups(case, user=None, role=SYSTEM_ROLE)

    case.refresh_from_db()
    assert case.events.count() == events_before
    assert CaseEventType.PRIOR_CASE_LOOKUP.value not in _event_types(case)


def _cleaned_case_with_declared(
    owner: User,
    manager: User,
    declared: Sequence[str],
) -> Case:
    """Caso encerrado administrativamente com rows de procedimento declaradas.

    A minimização do encerramento preserva as rows ``CaseProcedure`` (linkage)
    — os passos do pipeline dependem da declaração para rodar até o gate.
    """
    case = Case.objects.create(created_by=owner)
    for procedure_type in declared:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    _close(case.case_id, user=manager)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    return case
