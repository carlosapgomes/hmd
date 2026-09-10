"""Testes das views/templates de notificação in-app (change 11, slice 002, R1–R5).

Cobre a superfície de UI do D2:

- R1: ``UserNotification.objects.visible_for_list()`` — janela de retenção
  (``NOTIFICATION_READ_RETENTION_HOURS``); não lidas nunca somem, lidas fora da
  janela saem da lista mas continuam no banco;
- R2: context processor ``notification_unread_count`` — contagem de não lidas do
  autenticado (0 para anônimo);
- R3: views de rota GLOBAL — lista escopada ao dono, abrir (POST, marca lida +
  redirect por papel), marcar-todas (POST), endpoint JSON de contagem; nada de
  notificação alheia (404 sem marcar);
- R4: sino com badge em ``base.html`` e lista em
  ``templates/accounts/notifications.html`` (indicador de não lida, form POST
  de abrir, botão marcar-todas, estado vazio);
- R5: tudo isso, mais login exigido em todas as rotas.

O redirect por papel é resolvido por ``resolve_notification_redirect_url``
(nir→``intake:case_detail``, doctor→``doctor:case_detail``,
scheduler→``scheduler:case_detail``, sem papel/admin→``home``); as rotas de
destino fazem o guard real, então aqui só se confere a URL do ``Location``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import NotificationType, Role, User, UserNotification
from apps.cases.models import Case

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SCHEDULER_ROLE = "scheduler"

PASSWORD = "senha-teste"
UNREAD_NOTIFS = 3
STALE_READ_HOURS = 49
FRESH_READ_HOURS = 1

OWN_TITLE = "Minha notificação"
FOREIGN_TITLE = "Notificação alheia"


def _create_user(username: str, *roles: str) -> User:
    """Cria usuário com os papéis informados (senha fixa)."""
    user = User.objects.create_user(username=username, password=PASSWORD)
    for name in roles:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


def _login(client: Client, user: User, role: str | None = None) -> None:
    """Autentica o client; grava ``active_role`` explícito quando informado."""
    client.force_login(user)
    if role is not None:
        session = client.session
        session["active_role"] = role
        session.save()


def _create_case(creator: User) -> Case:
    return Case.objects.create(created_by=creator)


def _create_notification(
    *,
    recipient: User,
    case: Case,
    title: str = "Resposta final disponível",
    body_preview: str = "Agendamento confirmado",
) -> UserNotification:
    return UserNotification.objects.create(
        recipient=recipient,
        case=case,
        notification_type=NotificationType.FINAL_REPLY_POSTED,
        title=title,
        body_preview=body_preview,
    )


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def nir_user() -> User:
    return _create_user("nir-notif", NIR_ROLE)


@pytest.fixture
def doctor_user() -> User:
    return _create_user("doctor-notif", DOCTOR_ROLE)


@pytest.fixture
def case(nir_user: User) -> Case:
    return _create_case(nir_user)


@pytest.mark.django_db
class TestVisibleForListWindow:
    """R1: janela de retenção da lista (nada é apagado)."""

    def test_visible_for_list_retention(self, nir_user: User, case: Case) -> None:
        unread_old = _create_notification(recipient=nir_user, case=case)
        read_recent = _create_notification(recipient=nir_user, case=case)
        read_stale = _create_notification(recipient=nir_user, case=case)
        now = timezone.now()
        # Não lida antiga continua visível — a janela só esconde LEITURAS antigas.
        UserNotification.objects.filter(pk=unread_old.pk).update(created_at=now - timedelta(days=5))
        UserNotification.objects.filter(pk=read_recent.pk).update(
            read_at=now - timedelta(hours=FRESH_READ_HOURS)
        )
        UserNotification.objects.filter(pk=read_stale.pk).update(
            read_at=now - timedelta(hours=STALE_READ_HOURS)
        )

        visible = set(UserNotification.objects.visible_for_list().values_list("pk", flat=True))

        assert visible == {unread_old.pk, read_recent.pk}
        # A lida antiga saiu da lista, mas segue no banco (nada é apagado).
        assert UserNotification.objects.filter(pk=read_stale.pk).exists()

    def test_visible_for_list_respects_setting(self, nir_user: User, case: Case) -> None:
        notification = _create_notification(recipient=nir_user, case=case)
        UserNotification.objects.filter(pk=notification.pk).update(
            read_at=timezone.now() - timedelta(hours=2)
        )

        with override_settings(NOTIFICATION_READ_RETENTION_HOURS=1):
            visible = UserNotification.objects.visible_for_list()

        assert not visible.filter(pk=notification.pk).exists()

    def test_list_hides_stale_read_but_keeps_in_db(
        self, client: Client, nir_user: User, case: Case
    ) -> None:
        stale = _create_notification(recipient=nir_user, case=case, title=OWN_TITLE)
        UserNotification.objects.filter(pk=stale.pk).update(
            read_at=timezone.now() - timedelta(hours=STALE_READ_HOURS)
        )
        _login(client, nir_user, NIR_ROLE)

        response = client.get(reverse("notifications"))

        assert response.status_code == 200
        assert OWN_TITLE not in response.content.decode()
        assert UserNotification.objects.filter(pk=stale.pk).exists()


@pytest.mark.django_db
class TestNotificationContextProcessor:
    """R2: contexto global com a contagem de não lidas (0 para anônimo)."""

    def test_context_processor_count(self, nir_user: User, case: Case) -> None:
        from apps.accounts.context_processors import notification_unread_count

        _create_notification(recipient=nir_user, case=case)
        _create_notification(recipient=nir_user, case=case)

        request = RequestFactory().get("/")
        request.user = nir_user

        assert notification_unread_count(request) == {"notification_unread_count": 2}

    def test_context_processor_anonymous_zero(self) -> None:
        from apps.accounts.context_processors import notification_unread_count

        request = RequestFactory().get("/")
        request.user = AnonymousUser()

        assert notification_unread_count(request) == {"notification_unread_count": 0}


@pytest.mark.django_db
class TestNavbarBell:
    """R4: sino com badge em toda página autenticada (base.html)."""

    def test_navbar_bell_renders_count(self, client: Client, nir_user: User, case: Case) -> None:
        for _ in range(UNREAD_NOTIFS):
            _create_notification(recipient=nir_user, case=case)
        _login(client, nir_user, NIR_ROLE)

        body = client.get(reverse("home")).content.decode()

        assert reverse("notifications") in body
        assert f"Notificações: {UNREAD_NOTIFS} não lidas" in body

        # Marcar todas zera o badge na navegação seguinte (SSR, sem polling).
        client.post(reverse("notifications_mark_all_read"))
        body = client.get(reverse("home")).content.decode()

        assert "Notificações: 0 não lidas" in body


@pytest.mark.django_db
class TestNotificationList:
    """R3/R4: lista escopada ao dono, com estado vazio amigável."""

    def test_list_shows_only_own_notifications(
        self, client: Client, nir_user: User, doctor_user: User, case: Case
    ) -> None:
        _create_notification(recipient=nir_user, case=case, title=OWN_TITLE)
        _create_notification(recipient=doctor_user, case=case, title=FOREIGN_TITLE)
        _login(client, nir_user, NIR_ROLE)

        response = client.get(reverse("notifications"))
        body = response.content.decode()

        assert response.status_code == 200
        assert OWN_TITLE in body
        assert FOREIGN_TITLE not in body

    def test_list_renders_empty_state(self, client: Client, nir_user: User) -> None:
        _login(client, nir_user, NIR_ROLE)

        response = client.get(reverse("notifications"))

        assert response.status_code == 200
        assert "Nenhuma notificação" in response.content.decode()


@pytest.mark.django_db
class TestNotificationOpen:
    """R3: abrir (POST) marca lida e redireciona pelo papel ativo."""

    def test_open_marks_read_redirects_nir(
        self, client: Client, nir_user: User, case: Case
    ) -> None:
        notification = _create_notification(recipient=nir_user, case=case)
        _login(client, nir_user, NIR_ROLE)

        response = client.post(reverse("notifications_open", args=[notification.notification_id]))

        assert response.status_code == 302
        assert response.headers["Location"] == reverse(
            "intake:case_detail", kwargs={"case_id": case.pk}
        )
        notification.refresh_from_db()
        assert notification.read_at is not None

    def test_open_doctor_role_redirect(
        self, client: Client, nir_user: User, doctor_user: User, case: Case
    ) -> None:
        """NIR e doctor no MESMO caso recebem a rota da própria visão."""
        _create_notification(recipient=nir_user, case=case)
        doctor_notification = _create_notification(recipient=doctor_user, case=case)
        _login(client, doctor_user, DOCTOR_ROLE)

        response = client.post(
            reverse("notifications_open", args=[doctor_notification.notification_id])
        )

        assert response.status_code == 302
        assert response.headers["Location"] == reverse(
            "doctor:case_detail", kwargs={"case_id": case.pk}
        )
        doctor_notification.refresh_from_db()
        assert doctor_notification.read_at is not None

    def test_open_scheduler_role_redirect(self, client: Client, case: Case) -> None:
        scheduler = _create_user("scheduler-notif", SCHEDULER_ROLE)
        notification = _create_notification(recipient=scheduler, case=case)
        _login(client, scheduler, SCHEDULER_ROLE)

        response = client.post(reverse("notifications_open", args=[notification.notification_id]))

        assert response.headers["Location"] == reverse(
            "scheduler:case_detail", kwargs={"case_id": case.pk}
        )

    def test_open_other_role_falls_back_to_home(self, client: Client, case: Case) -> None:
        manager = _create_user("manager-notif", "manager")
        notification = _create_notification(recipient=manager, case=case)
        _login(client, manager, "manager")

        response = client.post(reverse("notifications_open", args=[notification.notification_id]))

        assert response.headers["Location"] == reverse("home")

    def test_open_foreign_404_without_marking(
        self, client: Client, nir_user: User, doctor_user: User, case: Case
    ) -> None:
        foreign = _create_notification(recipient=nir_user, case=case)
        _login(client, doctor_user, DOCTOR_ROLE)

        response = client.post(reverse("notifications_open", args=[foreign.notification_id]))

        assert response.status_code == 404
        foreign.refresh_from_db()
        assert foreign.read_at is None

    def test_open_requires_post(self, client: Client, nir_user: User, case: Case) -> None:
        notification = _create_notification(recipient=nir_user, case=case)
        _login(client, nir_user, NIR_ROLE)

        response = client.get(reverse("notifications_open", args=[notification.notification_id]))

        assert response.status_code == 405
        notification.refresh_from_db()
        assert notification.read_at is None


@pytest.mark.django_db
class TestMarkAllRead:
    """R3: marcar-todas (POST) zera as não lidas — só do próprio usuário."""

    def test_mark_all_read(self, client: Client, nir_user: User, case: Case) -> None:
        first = _create_notification(recipient=nir_user, case=case)
        second = _create_notification(recipient=nir_user, case=case)
        _login(client, nir_user, NIR_ROLE)

        response = client.post(reverse("notifications_mark_all_read"))

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("notifications")
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.read_at is not None
        assert second.read_at is not None
        assert not UserNotification.objects.filter(
            recipient=nir_user, read_at__isnull=True
        ).exists()

    def test_mark_all_read_does_not_touch_foreign(
        self, client: Client, nir_user: User, doctor_user: User, case: Case
    ) -> None:
        foreign = _create_notification(recipient=doctor_user, case=case)
        _login(client, nir_user, NIR_ROLE)

        client.post(reverse("notifications_mark_all_read"))

        foreign.refresh_from_db()
        assert foreign.read_at is None


@pytest.mark.django_db
class TestUnreadCountEndpoint:
    """R3: endpoint JSON com a contagem do autenticado (sem PHI)."""

    def test_unread_count_json(self, client: Client, nir_user: User, case: Case) -> None:
        _create_notification(recipient=nir_user, case=case)
        _create_notification(recipient=nir_user, case=case)
        _login(client, nir_user, NIR_ROLE)

        response = client.get(reverse("notifications_unread_count"))

        assert response.status_code == 200
        assert response.json() == {"unread_count": 2}


@pytest.mark.django_db
class TestNotificationRoutesRequireLogin:
    """R5: todas as rotas de notificação exigem login."""

    @pytest.mark.parametrize(
        "route_name",
        ["notifications", "notifications_mark_all_read", "notifications_unread_count"],
    )
    def test_anonymous_redirected_to_login(self, client: Client, route_name: str) -> None:
        method = client.post if route_name == "notifications_mark_all_read" else client.get

        response = method(reverse(route_name))

        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("login"))

    def test_anonymous_open_redirected_to_login(
        self, client: Client, nir_user: User, case: Case
    ) -> None:
        notification = _create_notification(recipient=nir_user, case=case)

        response = client.post(reverse("notifications_open", args=[notification.notification_id]))

        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("login"))
        notification.refresh_from_db()
        assert notification.read_at is None
