"""Testes dos signals de enqueue do pipeline LLM (slice 006, R5, design D9).

Contrato final (correção do review): (a) ``CASE_STATUS_LLM_EXTRACTING`` com
``source == "ANONYMIZING"`` → enqueue (entrada real via ``complete_anonymization``;
a self-transition ``start_llm_extraction`` tem ``source == LLM_EXTRACTING`` e
NÃO re-dispara); (b) ``CASE_STATUS_LLM_SUMMARIZING`` com
``source == "LLM_EXTRACTING"`` **E ``actor_type == "user"``** → enqueue de
retomada (bypass do NIR — o avanço natural do orquestrador tem ator system e
NÃO re-enfileira). Ambos via ``transaction.on_commit`` (rollback não enfileira);
conflito de lock em async = no-op idempotente (coberto pelo orquestrador).

Rodam com transações REAIS (transaction=True): o enqueue via ``on_commit`` só
executa no commit. O enqueue da anonimização é substituído por recorder para a
cadeia não depender do engine spaCy.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import transaction

from apps.accounts.models import User
from apps.cases.models import Case, CaseStatus
from apps.pipeline import signals as pipeline_signals

SYSTEM_ROLE = "system"


@pytest.fixture
def owner_user() -> User:
    """Dono (criador) dos casos dos testes."""
    return User.objects.create_user(username="dono-signal", password="senha-teste")


def _recorder(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Substitui o enqueue do pipeline por um recorder (decisão do signal)."""
    calls: list[uuid.UUID] = []

    def _record(case_id: uuid.UUID) -> None:
        calls.append(case_id)

    monkeypatch.setattr("apps.pipeline.tasks.enqueue_case_pipeline", _record)
    return calls


def _silence_anonymization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita a cadeia real de anonimização (sem engine spaCy na suíte)."""

    def _noop(case_id: uuid.UUID) -> None:
        del case_id

    monkeypatch.setattr("apps.anonymization.tasks.enqueue_case_anonymization", _noop)


def _case_in_anonymizing(user: User) -> Case:
    """Caso dirigido pelas transições reais até ANONYMIZING."""
    case = Case.objects.create(created_by=user)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.ANONYMIZING
    return case


# ── R5a: entrada real em LLM_EXTRACTING enfileira ──────────────────────────


@pytest.mark.django_db(transaction=True)
def test_entry_enqueues(monkeypatch: pytest.MonkeyPatch, owner_user: User) -> None:
    """R5a: ``complete_anonymization`` (source ANONYMIZING) → enqueue único do
    pipeline via on_commit."""
    _silence_anonymization(monkeypatch)
    calls = _recorder(monkeypatch)
    case = _case_in_anonymizing(owner_user)

    case.complete_anonymization(user=None, role=SYSTEM_ROLE)

    assert calls == [case.case_id]


@pytest.mark.django_db(transaction=True)
def test_entry_self_transition_does_not_reenqueue(
    monkeypatch: pytest.MonkeyPatch, owner_user: User
) -> None:
    """R5a: a self-transition ``start_llm_extraction`` (source LLM_EXTRACTING)
    NÃO re-enfileira (guarda de source; sem o filtro, inline recursaria)."""
    _silence_anonymization(monkeypatch)
    calls = _recorder(monkeypatch)
    case = _case_in_anonymizing(owner_user)

    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    assert calls == [case.case_id]

    case.start_llm_extraction(user=None, role=SYSTEM_ROLE)
    assert calls == [case.case_id]


@pytest.mark.django_db(transaction=True)
def test_entry_rollback_does_not_enqueue(monkeypatch: pytest.MonkeyPatch, owner_user: User) -> None:
    """R5a: transação que rollback NÃO enfileira (on_commit só roda no commit)."""
    _silence_anonymization(monkeypatch)
    calls = _recorder(monkeypatch)
    case = _case_in_anonymizing(owner_user)
    events_before = case.events.count()

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            case.complete_anonymization(user=None, role=SYSTEM_ROLE)
            raise RuntimeError("boom no meio da transação")

    assert calls == []
    case.refresh_from_db()
    assert case.status == CaseStatus.ANONYMIZING
    assert case.events.count() == events_before

    # Fronteira de commit real depois do rollback: nenhum callback vazou.
    Case.objects.create(created_by=owner_user)
    assert calls == []


# ── R5b: retomada pós-bypass (ator user) enfileira; avanço natural não ─────


@pytest.mark.django_db(transaction=True)
def test_bypass_user_actor_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    """R5b: ``bypass_pipeline_divergence`` (source LLM_EXTRACTING, ator user —
    liberação do NIR) → enqueue de retomada."""
    _silence_anonymization(monkeypatch)
    calls = _recorder(monkeypatch)
    nir = User.objects.create_user(username="nir-signal", password="senha-teste")
    owner = User.objects.create_user(username="dono-bypass", password="senha-teste")
    case = _case_in_anonymizing(owner)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.manual_review_required = True
    case.manual_review_reason = "procedure_divergence"
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    assert calls == [case.case_id]

    case.bypass_pipeline_divergence(user=nir, role="nir")

    assert calls == [case.case_id, case.case_id]


@pytest.mark.django_db(transaction=True)
def test_natural_advance_system_actor_no_enqueue(
    monkeypatch: pytest.MonkeyPatch, owner_user: User
) -> None:
    """R5b: o avanço natural do orquestrador (``complete_llm_extraction`` com
    ator system) NÃO re-enfileira — a task segue em execução própria."""
    _silence_anonymization(monkeypatch)
    calls = _recorder(monkeypatch)
    case = _case_in_anonymizing(owner_user)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    assert calls == [case.case_id]

    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)

    assert calls == [case.case_id]
    assert case.status == CaseStatus.LLM_SUMMARIZING


def test_receivers_are_connected() -> None:
    """R5: o módulo de signals expõe os dois receivers e há listeners no
    ``CaseEvent.post_save`` (o AppsConfig.ready registra via import)."""
    from django.db.models.signals import post_save

    from apps.cases.models import CaseEvent

    assert callable(pipeline_signals.enqueue_case_pipeline_on_entry)
    assert callable(pipeline_signals.enqueue_case_pipeline_resume_after_bypass)
    assert post_save.has_listeners(CaseEvent)
