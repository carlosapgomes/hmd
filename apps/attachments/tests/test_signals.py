"""Testes do trigger de processamento de anexos (R4).

Receiver de ``CaseEvent.post_save``: o evento ``CASE_ANONYMIZATION_COMPLETED``
(created=True) dispara o enqueue de ``process_case_attachments`` via
``transaction.on_commit`` — rollback não enfileira; tipos não relacionados
ignorados; a flag ``ATTACHMENTS_RUN_TASKS_INLINE`` é respeitada (inline executa
a task sincronamente; ``False`` enfileira via django-q2 no cluster
``attachments``). Rodam com transações REAIS (``transaction=True``) porque o
enqueue via ``on_commit`` só executa no commit (precedente:
``apps/pipeline/tests/test_signals.py``).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from django.db import transaction
from django.test import override_settings

from apps.accounts.models import User
from apps.attachments import signals as attachment_signals
from apps.attachments.tasks import enqueue_case_attachments
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent

SYSTEM_ROLE = "system"


@pytest.fixture
def owner_user(user_factory: Callable[[str], User]) -> User:
    return user_factory("signals")


def _new_case(owner: User) -> Case:
    return Case.objects.create(created_by=owner)


def _anonymization_completed_event(case: Case) -> CaseEvent:
    """Evento canônico da anonimização (o que o receiver consome)."""
    return CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_ANONYMIZATION_COMPLETED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role=SYSTEM_ROLE,
        payload={},
    )


def _record_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Substitui o enqueue do worker por um recorder (decisão do signal)."""
    calls: list[uuid.UUID] = []

    def _record(case_id: uuid.UUID) -> None:
        calls.append(case_id)

    monkeypatch.setattr("apps.attachments.tasks.enqueue_case_attachments", _record)
    return calls


def test_receivers_are_connected() -> None:
    """R4: o módulo expõe o receiver e há listeners no ``CaseEvent.post_save``
    (o ``AppsConfig.ready`` registra via import)."""
    from django.db.models.signals import post_save

    assert callable(attachment_signals.enqueue_attachments_after_anonymization)
    assert post_save.has_listeners(CaseEvent)


@pytest.mark.django_db(transaction=True)
def test_signal_enqueues_after_anonymization(
    monkeypatch: pytest.MonkeyPatch,
    owner_user: User,
) -> None:
    """R4: ``CASE_ANONYMIZATION_COMPLETED`` (created) → enqueue único do
    processamento de anexos via on_commit."""
    calls = _record_enqueue(monkeypatch)
    case = _new_case(owner_user)

    _anonymization_completed_event(case)

    assert calls == [case.case_id]


@pytest.mark.django_db(transaction=True)
def test_signal_ignores_update_and_unrelated_events(
    monkeypatch: pytest.MonkeyPatch,
    owner_user: User,
) -> None:
    """R4: re-save (created=False) e tipos não relacionados não enfileiram."""
    calls = _record_enqueue(monkeypatch)
    case = _new_case(owner_user)
    event = _anonymization_completed_event(case)
    assert calls == [case.case_id]

    event.save()  # created=False → sem novo enqueue
    CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_LLM1_COMPLETED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role=SYSTEM_ROLE,
        payload={},
    )

    assert calls == [case.case_id]


@pytest.mark.django_db(transaction=True)
def test_no_enqueue_on_rollback(
    monkeypatch: pytest.MonkeyPatch,
    owner_user: User,
) -> None:
    """R4: transação que rollback NÃO enfileira (on_commit só roda no commit)."""
    calls = _record_enqueue(monkeypatch)
    case = _new_case(owner_user)
    events_before = case.events.count()

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            _anonymization_completed_event(case)
            raise RuntimeError("boom no meio da transação")

    assert calls == []
    assert case.events.count() == events_before

    # Fronteira de commit real depois do rollback: nenhum callback vazou.
    _new_case(owner_user)
    assert calls == []


@pytest.mark.django_db(transaction=True)
def test_signal_inline_false_enqueues_async_task(
    monkeypatch: pytest.MonkeyPatch,
    owner_user: User,
) -> None:
    """R4/R6: com ``ATTACHMENTS_RUN_TASKS_INLINE=False`` o signal enfileira no
    django-q2 (cluster ``attachments``) em vez de rodar inline."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _async_task(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr("apps.attachments.tasks.async_task", _async_task)
    case = _new_case(owner_user)

    with override_settings(ATTACHMENTS_RUN_TASKS_INLINE=False):
        _anonymization_completed_event(case)

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "apps.attachments.tasks.process_case_attachments"
    assert args[1] == case.case_id
    assert kwargs["q_options"]["cluster"] == "attachments"


def test_enqueue_inline_runs_synchronously(monkeypatch: pytest.MonkeyPatch) -> None:
    """R4/R6: ``ATTACHMENTS_RUN_TASKS_INLINE=True`` executa a task no próprio
    processo (padrão dos workers existentes)."""
    calls: list[uuid.UUID] = []
    case_id = uuid.uuid4()
    monkeypatch.setattr(
        "apps.attachments.tasks.process_case_attachments",
        lambda cid: calls.append(cid),
    )

    with override_settings(ATTACHMENTS_RUN_TASKS_INLINE=True):
        enqueue_case_attachments(case_id)

    assert calls == [case_id]


def test_enqueue_async_uses_attachments_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4/R6: fora de inline o enqueue aponta para o cluster ``attachments``
    com task_name rastreável."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _async_task(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr("apps.attachments.tasks.async_task", _async_task)
    case_id = uuid.uuid4()

    with override_settings(ATTACHMENTS_RUN_TASKS_INLINE=False):
        enqueue_case_attachments(case_id)

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "apps.attachments.tasks.process_case_attachments"
    assert args[1] == case_id
    q_options = kwargs["q_options"]
    assert q_options["cluster"] == "attachments"
    assert q_options["task_name"] == f"attachments:{case_id}"
