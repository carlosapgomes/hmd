"""Testes do model ``UserNotification`` (change dashboard-notifications-pwa, slice 001, R1).

Cobre o contrato de D1: campos exatos (pk UUID, FKs CASCADE com ``event``
anulável, ``notification_type`` do conjunto dos 3 marcos, ``title``/``body_preview``
limitados, ``created_at`` automático, ``read_at`` nulo), idempotência estrutural
(``UniqueConstraint(recipient, event)``), índices de consulta e ordenação
``-created_at``.

Os eventos usados aqui são de tipos FORA do conjunto de marcos: a trilha não
dispara notificação e os testes ficam focados no model.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError, models
from django.utils import timezone

from apps.accounts.models import NotificationType, User, UserNotification
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent

NON_MILESTONE_EVENT_TYPE = CaseEventType.CASE_STATUS_CLEANED


@pytest.fixture
def creator() -> User:
    """Criador (NIR) do caso — destinatário notificável."""
    return User.objects.create_user(username="nir-criador", password="senha-teste")


@pytest.fixture
def case(creator: User) -> Case:
    return Case.objects.create(created_by=creator)


def _create_non_milestone_event(case: Case) -> CaseEvent:
    return CaseEvent.objects.create(
        case=case,
        event_type=NON_MILESTONE_EVENT_TYPE,
        actor_type=ActorType.SYSTEM,
        payload={},
    )


def _create_notification(
    *,
    case: Case,
    recipient: User,
    event: CaseEvent | None = None,
) -> UserNotification:
    return UserNotification.objects.create(
        recipient=recipient,
        case=case,
        event=event,
        notification_type=NotificationType.FINAL_REPLY_POSTED,
        title="Resposta final disponível",
        body_preview="Negativa médica",
    )


@pytest.mark.django_db
class TestUserNotificationFields:
    """R1: campos e defaults exatos."""

    def test_defaults_and_recipient_relation(self, creator: User, case: Case) -> None:
        notification = _create_notification(case=case, recipient=creator)

        assert isinstance(notification.notification_id, uuid.UUID)
        assert notification.read_at is None
        assert notification.created_at is not None
        assert list(creator.notifications.all()) == [notification]
        assert list(case.notifications.all()) == [notification]

    def test_notification_type_choices_are_the_three_milestones(self) -> None:
        assert [choice for choice, _label in NotificationType.choices] == [
            "final_reply_posted",
            "scheduler_requested",
            "scheduling_reopened",
        ]
        field = UserNotification._meta.get_field("notification_type")
        assert field.choices == NotificationType.choices

    def test_title_and_body_preview_limits(self) -> None:
        assert UserNotification._meta.get_field("title").max_length == 160
        assert UserNotification._meta.get_field("body_preview").max_length == 240

    def test_created_at_is_auto_now_add(self, creator: User, case: Case) -> None:
        field = UserNotification._meta.get_field("created_at")
        assert isinstance(field, models.DateTimeField)
        assert field.auto_now_add is True

    def test_event_is_nullable(self, creator: User, case: Case) -> None:
        notification = _create_notification(case=case, recipient=creator)
        assert notification.event is None
        assert UserNotification._meta.get_field("event").null is True


@pytest.mark.django_db
class TestUserNotificationForeignKeys:
    """R1: FKs CASCADE (recorte de dados do caso remove as notificações)."""

    def test_recipient_and_case_use_cascade(self) -> None:
        for field_name in ("recipient", "case", "event"):
            field = UserNotification._meta.get_field(field_name)
            assert isinstance(field, models.ForeignKey)
            assert field.remote_field is not None
            assert field.remote_field.on_delete is models.CASCADE


@pytest.mark.django_db
class TestUserNotificationConstraints:
    """R1/R3: idempotência estrutural e índices de consulta."""

    def test_unique_recipient_event(self, creator: User, case: Case) -> None:
        event = _create_non_milestone_event(case)
        _create_notification(case=case, recipient=creator, event=event)

        with pytest.raises(IntegrityError):
            _create_notification(case=case, recipient=creator, event=event)

    def test_unique_constraint_declared_on_recipient_and_event(self) -> None:
        unique_constraints = [
            constraint
            for constraint in UserNotification._meta.constraints
            if isinstance(constraint, models.UniqueConstraint)
        ]
        assert [constraint.name for constraint in unique_constraints] == [
            "uniq_notif_recipient_event"
        ]
        assert [tuple(constraint.fields) for constraint in unique_constraints] == [
            ("recipient", "event")
        ]

    def test_indexes_declared(self) -> None:
        assert [tuple(index.fields) for index in UserNotification._meta.indexes] == [
            ("recipient", "read_at", "created_at"),
            ("case", "created_at"),
        ]

    def test_nullable_event_does_not_block_multiple_notifications(
        self, creator: User, case: Case
    ) -> None:
        """``event`` nulo (reserva) não é afetado pela unicidade por evento."""
        _create_notification(case=case, recipient=creator)
        _create_notification(case=case, recipient=creator)
        assert UserNotification.objects.count() == 2


@pytest.mark.django_db
class TestUserNotificationOrdering:
    """R1: ``ordering = ["-created_at"]`` (mais recente primeiro)."""

    def test_ordering_newest_first(self, creator: User, case: Case) -> None:
        older = _create_notification(case=case, recipient=creator)
        newer = _create_notification(case=case, recipient=creator)
        UserNotification.objects.filter(pk=older.pk).update(
            created_at=timezone.now() - timedelta(hours=1)
        )

        assert list(UserNotification.objects.values_list("pk", flat=True)) == [
            newer.pk,
            older.pk,
        ]
