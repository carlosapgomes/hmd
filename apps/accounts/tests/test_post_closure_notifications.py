"""Fan-out da reabertura pós-encerramento (slice 002, R4 — spec `notifications` MODIFIED).

Cobre a extensão do marco de reabertura (``CASE_STATUS_AWAITING_SCHEDULING``
com ``reason`` — pré-ciência E pós-encerramento): criador + fan-out dos
agendadores ativos (preview fixo novo), idempotência por
``unique(recipient,event)`` para ambos os destinatários e falha que nunca
bloqueia a transição.
"""

from __future__ import annotations

import pytest

import apps.accounts.notifications as notifications
from apps.accounts.models import Role, User, UserNotification
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent

SCHEDULER_ROLE = "scheduler"
NIR_ROLE = "nir"

REOPEN_TITLE = "Caso reaberto por intercorrência"
REOPEN_CREATOR_PREVIEW = "Reconfirme os dados do caso"
REOPEN_SCHEDULER_PREVIEW = "Caso reaberto aguardando novo agendamento"

REOPEN_REASON = "vaga desmarcada pela unidade de origem — nova data solicitada"


def _create_user(
    username: str,
    *roles: str,
    account_status: str = "active",
    is_active: bool = True,
) -> User:
    user = User.objects.create_user(
        username=username,
        password="senha-teste",
        account_status=account_status,
        is_active=is_active,
    )
    for role_name in roles:
        role, _created = Role.objects.get_or_create(name=role_name)
        user.roles.add(role)
    return user


def _create_reopen_event(case: Case) -> CaseEvent:
    """Evento-marco de reabertura (retorno à fila com motivo) — dispara o signal."""
    return CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_STATUS_AWAITING_SCHEDULING,
        actor_type=ActorType.USER,
        actor=None,
        actor_role="",
        payload={
            "source": "CLEANED",
            "target": "AWAITING_SCHEDULING",
            "reason": REOPEN_REASON,
        },
    )


@pytest.fixture
def creator() -> User:
    """Criador (NIR) do caso — destinatário do marco de reabertura."""
    return _create_user("nir-criador-reabertura", NIR_ROLE)


@pytest.fixture
def case(creator: User) -> Case:
    return Case.objects.create(created_by=creator)


@pytest.fixture
def active_schedulers() -> list[User]:
    """Dois agendadores ativos — um deles com papel extra (fan-out sem duplicata)."""
    return [
        _create_user("agendador-reab-1", SCHEDULER_ROLE),
        _create_user("agendador-reab-2", SCHEDULER_ROLE, NIR_ROLE),
    ]


@pytest.fixture
def inactive_scheduler() -> User:
    """Agendador com login desabilitado — fora do fan-out."""
    return _create_user("agendador-reab-inativo", SCHEDULER_ROLE, is_active=False)


@pytest.fixture
def blocked_scheduler() -> User:
    """Agendador com conta bloqueada — fora do fan-out."""
    return _create_user("agendador-reab-bloqueado", SCHEDULER_ROLE, account_status="blocked")


@pytest.mark.django_db
class TestReopenFanoutRecipients:
    """R4: o marco de reabertura notifica o criador E os agendadores ativos."""

    def test_reopen_notifies_creator_and_active_schedulers(
        self,
        creator: User,
        case: Case,
        active_schedulers: list[User],
        inactive_scheduler: User,
        blocked_scheduler: User,
    ) -> None:
        event = _create_reopen_event(case)

        created = notifications.create_milestone_notifications(event)

        by_recipient = {notification.recipient_id: notification for notification in created}
        assert set(by_recipient) == {creator.pk} | {scheduler.pk for scheduler in active_schedulers}
        assert len(created) == 1 + len(active_schedulers)
        # Sem duplicata para o agendador com papel extra (fan-out ``distinct()``).
        assert inactive_scheduler.notifications.count() == 0
        assert blocked_scheduler.notifications.count() == 0

    def test_reopen_creator_keeps_title_and_preview(
        self, creator: User, case: Case, active_schedulers: list[User]
    ) -> None:
        """O criador mantém título e preview vigentes (comportamento preservado)."""
        del active_schedulers
        event = _create_reopen_event(case)

        created = notifications.create_milestone_notifications(event)

        creator_notification = next(
            notification for notification in created if notification.recipient_id == creator.pk
        )
        assert creator_notification.title == REOPEN_TITLE
        assert creator_notification.body_preview == REOPEN_CREATOR_PREVIEW
        assert creator_notification.notification_type == "scheduling_reopened"
        assert REOPEN_REASON not in creator_notification.body_preview

    def test_reopen_schedulers_share_title_with_new_preview(
        self, creator: User, case: Case, active_schedulers: list[User]
    ) -> None:
        """D8/copy do dono: mesmo título do criador, preview fixo novo — sem PHI."""
        del creator
        event = _create_reopen_event(case)

        created = notifications.create_milestone_notifications(event)

        scheduler_notifications = [
            notification
            for notification in created
            if notification.recipient_id != case.created_by_id
        ]
        assert len(scheduler_notifications) == len(active_schedulers)
        for notification in scheduler_notifications:
            assert notification.title == REOPEN_TITLE
            assert notification.body_preview == REOPEN_SCHEDULER_PREVIEW
            assert notification.notification_type == "scheduling_reopened"
            assert notification.case_id == case.pk
            assert notification.event_id == event.pk
            assert notification.read_at is None
            assert REOPEN_REASON not in notification.body_preview


@pytest.mark.django_db
class TestReopenIdempotency:
    """R4: reprocessar o marco não duplica — nem criador, nem agendadores."""

    def test_idempotent_double_call_fanout(
        self,
        creator: User,
        case: Case,
        active_schedulers: list[User],
    ) -> None:
        del creator, active_schedulers
        event = _create_reopen_event(case)

        first = notifications.create_milestone_notifications(event)
        second = notifications.create_milestone_notifications(event)

        assert sorted(notification.pk for notification in first) == sorted(
            notification.pk for notification in second
        )
        assert UserNotification.objects.filter(event=event).count() == len(first)


@pytest.mark.django_db
class TestReopenViaSignal:
    """R4: o signal cria o fan-out no marco e falha jamais bloqueia."""

    def test_signal_creates_creator_and_scheduler_notifications(
        self,
        creator: User,
        case: Case,
        active_schedulers: list[User],
    ) -> None:
        event = _create_reopen_event(case)

        stored = UserNotification.objects.filter(event=event)

        assert {notification.recipient_id for notification in stored} == {creator.pk} | {
            scheduler.pk for scheduler in active_schedulers
        }
        previews = {notification.recipient_id: notification.body_preview for notification in stored}
        assert previews[creator.pk] == REOPEN_CREATOR_PREVIEW
        for scheduler in active_schedulers:
            assert previews[scheduler.pk] == REOPEN_SCHEDULER_PREVIEW

    def test_signal_failure_does_not_block_reopen(
        self,
        creator: User,
        case: Case,
        active_schedulers: list[User],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Serviço que explode → evento persistido, falha em log, sem notificação."""
        import logging

        del active_schedulers

        def _explode(event: CaseEvent) -> list[UserNotification]:
            raise RuntimeError("fan-out indisponível")

        monkeypatch.setattr(notifications, "create_milestone_notifications", _explode)

        with caplog.at_level(logging.ERROR, logger="apps.accounts.signals"):
            event = _create_reopen_event(case)

        assert CaseEvent.objects.filter(pk=event.pk).exists()
        assert creator.notifications.count() == 0
        assert "fan-out indisponível" in caplog.text
