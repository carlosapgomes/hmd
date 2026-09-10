"""Testes do serviço e do signal de notificações por marcos (slice 001, R2–R5).

Cobre o conjunto FECHADO de marcos: resposta final publicada → criador (título
fixo + preview por ``payload["source"]`` com fallback), caso pronto para
agendamento → fan-out dos usuários com papel ``scheduler`` ativos, reabertura
por intercorrência (``AWAITING_SCHEDULING`` com ``reason`` no payload) →
criador com preview fixo; entrada NORMAL em ``AWAITING_SCHEDULING`` (sem
``reason``) e eventos fora do conjunto não notificam. Idempotência por
(``recipient``, ``event``) e fail-safe do signal (serviço que explode não
impede a gravação do evento).
"""

from __future__ import annotations

import logging

import pytest
from pytest_django.fixtures import DjangoAssertNumQueries

import apps.accounts.notifications as notifications
from apps.accounts.models import Role, User, UserNotification
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent

SCHEDULER_ROLE = "scheduler"
NIR_ROLE = "nir"

FINAL_REPLY_TITLE = "Resposta final disponível"
FINAL_REPLY_FALLBACK_PREVIEW = "Resposta final disponível para o caso"
SCHEDULER_REQUESTED_TITLE = "Caso pronto para agendamento"
REOPEN_TITLE = "Caso reaberto por intercorrência"
REOPEN_PREVIEW = "Reconfirme os dados do caso"

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


def _create_event(case: Case, event_type: str, payload: dict[str, object]) -> CaseEvent:
    """Grava o evento direto na trilha — o signal de notificações observa o save."""
    return CaseEvent.objects.create(
        case=case,
        event_type=event_type,
        actor_type=ActorType.USER,
        actor=None,
        actor_role="",
        payload=payload,
    )


@pytest.fixture
def creator() -> User:
    """Criador (NIR) do caso — destinatário dos marcos de resposta final e reabertura."""
    return _create_user("nir-criador", NIR_ROLE)


@pytest.fixture
def case(creator: User) -> Case:
    return Case.objects.create(created_by=creator)


@pytest.fixture
def active_schedulers() -> list[User]:
    """Dois agendadores ativos — um deles com papel extra (fan-out sem duplicata)."""
    return [
        _create_user("agendador-1", SCHEDULER_ROLE),
        _create_user("agendador-2", SCHEDULER_ROLE, NIR_ROLE),
    ]


@pytest.fixture
def inactive_scheduler() -> User:
    """Agendador com login desabilitado — fora do fan-out."""
    return _create_user("agendador-inativo", SCHEDULER_ROLE, is_active=False)


@pytest.fixture
def blocked_scheduler() -> User:
    """Agendador com conta bloqueada — fora do fan-out."""
    return _create_user("agendador-bloqueado", SCHEDULER_ROLE, account_status="blocked")


@pytest.mark.django_db
class TestFinalReplyNotifications:
    """R2: resposta final publicada notifica o criador com preview do tipo de resposta."""

    @pytest.mark.parametrize(
        ("source", "expected_preview"),
        [
            ("DOCTOR_DENIED", "Negativa médica"),
            ("SCHEDULING_CONFIRMED", "Agendamento confirmado"),
            ("SCHEDULING_DENIED", "Negativa de agendamento"),
        ],
    )
    def test_final_reply_notifies_creator_with_source_preview(
        self,
        creator: User,
        case: Case,
        source: str,
        expected_preview: str,
    ) -> None:
        event = _create_event(
            case,
            CaseEventType.CASE_STATUS_FINAL_REPLY_POSTED,
            {"source": source, "target": "FINAL_REPLY_POSTED"},
        )

        created = notifications.create_milestone_notifications(event)

        assert [notification.recipient_id for notification in created] == [creator.pk]
        notification = created[0]
        assert notification.title == FINAL_REPLY_TITLE
        assert notification.body_preview == expected_preview
        assert notification.notification_type == "final_reply_posted"
        assert notification.case_id == case.pk
        assert notification.event_id == event.pk
        assert notification.read_at is None

    @pytest.mark.parametrize(
        "payload",
        [
            {"source": "UNEXPECTED_SOURCE", "target": "FINAL_REPLY_POSTED"},
            {"target": "FINAL_REPLY_POSTED"},
        ],
    )
    def test_final_reply_fallback_preview(
        self,
        creator: User,
        case: Case,
        payload: dict[str, object],
    ) -> None:
        """Source inesperado/ausente cai no preview genérico (sem PHI)."""
        event = _create_event(case, CaseEventType.CASE_STATUS_FINAL_REPLY_POSTED, payload)

        created = notifications.create_milestone_notifications(event)

        assert len(created) == 1
        assert created[0].recipient_id == creator.pk
        assert created[0].body_preview == FINAL_REPLY_FALLBACK_PREVIEW


@pytest.mark.django_db
class TestSchedulerFanout:
    """R2: caso pronto para agendamento notifica todos os schedulers ativos."""

    def test_scheduler_requested_fanout_to_active_schedulers_only(
        self,
        creator: User,
        case: Case,
        active_schedulers: list[User],
        inactive_scheduler: User,
        blocked_scheduler: User,
    ) -> None:
        event = _create_event(
            case,
            CaseEventType.CASE_STATUS_SCHEDULER_REQUESTED,
            {"source": "DOCTOR_ACCEPTED", "target": "SCHEDULER_REQUESTED"},
        )

        created = notifications.create_milestone_notifications(event)

        assert {notification.recipient_id for notification in created} == {
            scheduler.pk for scheduler in active_schedulers
        }
        # Sem duplicata para o agendador com papel extra (fan-out ``distinct()``).
        assert len(created) == len(active_schedulers)
        for notification in created:
            assert notification.title == SCHEDULER_REQUESTED_TITLE
            assert notification.notification_type == "scheduler_requested"
        notified = set(
            User.objects.filter(notifications__event=event).values_list("username", flat=True)
        )
        assert notified == {scheduler.username for scheduler in active_schedulers}
        assert creator.notifications.count() == 0
        assert inactive_scheduler.notifications.count() == 0
        assert blocked_scheduler.notifications.count() == 0


@pytest.mark.django_db
class TestSchedulingReopenedNotification:
    """R2: reabertura por intercorrência notifica o criador (sem o motivo)."""

    def test_reopen_with_reason_notifies_creator(self, creator: User, case: Case) -> None:
        event = _create_event(
            case,
            CaseEventType.CASE_STATUS_AWAITING_SCHEDULING,
            {
                "source": "FINAL_REPLY_POSTED",
                "target": "AWAITING_SCHEDULING",
                "reason": REOPEN_REASON,
            },
        )

        created = notifications.create_milestone_notifications(event)

        assert len(created) == 1
        notification = created[0]
        assert notification.recipient_id == creator.pk
        assert notification.title == REOPEN_TITLE
        assert notification.body_preview == REOPEN_PREVIEW
        assert notification.notification_type == "scheduling_reopened"
        assert REOPEN_REASON not in notification.body_preview

    def test_plain_awaiting_scheduling_without_reason_notifies_nobody(
        self, creator: User, case: Case
    ) -> None:
        """Entrada normal na fila pós-decisão parcial (sem ``reason``) não notifica."""
        event = _create_event(
            case,
            CaseEventType.CASE_STATUS_AWAITING_SCHEDULING,
            {"source": "SCHEDULER_REQUESTED", "target": "AWAITING_SCHEDULING"},
        )

        assert notifications.create_milestone_notifications(event) == []
        assert not UserNotification.objects.exists()
        assert creator.notifications.count() == 0


@pytest.mark.django_db
class TestClosedMilestoneSet:
    """R2: eventos fora do conjunto fechado devolvem ``[]`` sem tocar o banco."""

    @pytest.mark.parametrize(
        "event_type",
        [
            CaseEventType.CASE_STATUS_CLEANED,
            CaseEventType.CASE_STATUS_DOCTOR_DENIED,
            CaseEventType.CASE_STATUS_AWAITING_NIR_ACK,
            CaseEventType.CASE_PROCEDURES_DECLARED,
        ],
    )
    def test_event_outside_milestones_creates_nothing(
        self,
        case: Case,
        event_type: str,
        django_assert_num_queries: DjangoAssertNumQueries,
    ) -> None:
        event = _create_event(case, event_type, {"source": "X", "target": "Y"})

        with django_assert_num_queries(0):
            assert notifications.create_milestone_notifications(event) == []
        assert not UserNotification.objects.exists()


@pytest.mark.django_db
class TestIdempotency:
    """R3: ``get_or_create`` por (recipient, event) — reprocessar não duplica."""

    def test_idempotent_double_call(self, creator: User, case: Case) -> None:
        event = _create_event(
            case,
            CaseEventType.CASE_STATUS_FINAL_REPLY_POSTED,
            {"source": "DOCTOR_DENIED", "target": "FINAL_REPLY_POSTED"},
        )

        first = notifications.create_milestone_notifications(event)
        second = notifications.create_milestone_notifications(event)

        assert [notification.pk for notification in first] == [
            notification.pk for notification in second
        ]
        assert UserNotification.objects.filter(event=event).count() == 1
        assert creator.notifications.count() == 1

    def test_idempotent_double_call_fanout(self, case: Case, active_schedulers: list[User]) -> None:
        event = _create_event(
            case,
            CaseEventType.CASE_STATUS_SCHEDULER_REQUESTED,
            {"source": "DOCTOR_ACCEPTED", "target": "SCHEDULER_REQUESTED"},
        )

        first = notifications.create_milestone_notifications(event)
        second = notifications.create_milestone_notifications(event)

        assert sorted(notification.pk for notification in first) == sorted(
            notification.pk for notification in second
        )
        assert UserNotification.objects.filter(event=event).count() == len(active_schedulers)


@pytest.mark.django_db
class TestNotificationSignal:
    """R4: signal ``CaseEvent.post_save`` cria no marco e nunca bloqueia o evento."""

    def test_signal_creates_notifications_on_event_create(self, creator: User, case: Case) -> None:
        event = _create_event(
            case,
            CaseEventType.CASE_STATUS_FINAL_REPLY_POSTED,
            {"source": "SCHEDULING_CONFIRMED", "target": "FINAL_REPLY_POSTED"},
        )

        notification = UserNotification.objects.get(event=event)
        assert notification.recipient_id == creator.pk
        assert notification.title == FINAL_REPLY_TITLE
        assert notification.body_preview == "Agendamento confirmado"

    def test_signal_ignores_event_update(self, creator: User, case: Case) -> None:
        event = _create_event(
            case,
            CaseEventType.CASE_STATUS_FINAL_REPLY_POSTED,
            {"source": "DOCTOR_DENIED", "target": "FINAL_REPLY_POSTED"},
        )
        UserNotification.objects.all().delete()

        event.payload = {"source": "DOCTOR_DENIED", "target": "FINAL_REPLY_POSTED", "n": 1}
        event.save()

        assert not UserNotification.objects.exists()

    def test_signal_failure_does_not_block_event(
        self,
        creator: User,
        case: Case,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Serviço que explode → evento persistido e falha registrada em log."""

        def _explode(event: CaseEvent) -> list[UserNotification]:
            raise RuntimeError("notificação indisponível")

        monkeypatch.setattr(notifications, "create_milestone_notifications", _explode)

        with caplog.at_level(logging.ERROR, logger="apps.accounts.signals"):
            event = _create_event(
                case,
                CaseEventType.CASE_STATUS_FINAL_REPLY_POSTED,
                {"source": "DOCTOR_DENIED", "target": "FINAL_REPLY_POSTED"},
            )

        assert CaseEvent.objects.filter(pk=event.pk).exists()
        assert creator.notifications.count() == 0
        assert "notificação indisponível" in caplog.text
