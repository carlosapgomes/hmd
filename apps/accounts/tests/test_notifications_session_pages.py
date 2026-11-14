"""Páginas de sessão no padrão (ui-parity-foundation, slice 005, R1–R3).

Cobre, em TDD (RED antes da implementação):

- R1: login com toggle de senha (``toggle-password`` + ``input-group`` +
  ``password_toggle.js``), aviso de acesso transitório mantido e SEM
  link de reset/esqueci-senha (ADR-0003 — sem e-mails, permanente);
- R2: leitura individual de notificação (POST mínimo que marca só ela
  como lida e volta à lista, sem abrir o caso), guard de destinatário
  (alheia = 404 sem marcar), lista com referência do caso, formulário
  individual por item não lido, marcar-todas, voltar e polling do sino;
- R3: perfil e manual no padrão (``h1.page-title``, cards, voltar no
  perfil, manual imprimível com seções por papel).
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import NotificationType, Role, User, UserNotification
from apps.cases.models import Case

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
PASSWORD = "senha-teste"
CASE_REFERENCE = "12345"
TRANSIENT_NOTICE = "Acesso local transitório"


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
    case = Case.objects.create(created_by=creator)
    Case.objects.filter(pk=case.pk).update(agency_record_number=CASE_REFERENCE)
    case.refresh_from_db()
    return case


def _create_notification(recipient: User, case: Case) -> UserNotification:
    return UserNotification.objects.create(
        recipient=recipient,
        case=case,
        notification_type=NotificationType.FINAL_REPLY_POSTED,
        title="Resposta final disponível",
        body_preview="Agendamento confirmado",
    )


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def nir_user() -> User:
    return _create_user("nir-sessao", NIR_ROLE)


@pytest.fixture
def doctor_user() -> User:
    return _create_user("doctor-sessao", DOCTOR_ROLE)


@pytest.fixture
def case(nir_user: User) -> Case:
    return _create_case(nir_user)


@pytest.mark.django_db
class TestLoginPasswordToggle:
    """R1: login no padrão — toggle funcional, aviso transitório, sem reset."""

    def test_login_has_password_toggle(self, client: Client) -> None:
        body = client.get(reverse("login")).content.decode()

        assert "toggle-password" in body
        assert "input-group" in body
        assert "password_toggle.js" in body
        assert 'data-target="id_password"' in body
        assert 'aria-label="Mostrar senha"' in body

    def test_login_keeps_transient_notice(self, client: Client) -> None:
        body = client.get(reverse("login")).content.decode()

        assert TRANSIENT_NOTICE in body

    def test_login_has_no_reset_link(self, client: Client) -> None:
        body = client.get(reverse("login")).content.decode()

        assert "Esqueci" not in body
        assert "password_reset" not in body
        assert "reset" not in body.lower()


@pytest.mark.django_db
class TestNotificationIndividualRead:
    """R2: leitura individual (POST mínimo, sem abrir o caso)."""

    def test_read_marks_only_that_notification(
        self, client: Client, nir_user: User, case: Case
    ) -> None:
        target = _create_notification(nir_user, case)
        other = _create_notification(nir_user, case)
        _login(client, nir_user, NIR_ROLE)

        response = client.post(reverse("notifications_read", args=[target.notification_id]))

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("notifications")
        target.refresh_from_db()
        other.refresh_from_db()
        assert target.read_at is not None
        assert other.read_at is None

    def test_read_foreign_404_without_marking(
        self, client: Client, nir_user: User, doctor_user: User, case: Case
    ) -> None:
        foreign = _create_notification(nir_user, case)
        _login(client, doctor_user, DOCTOR_ROLE)

        response = client.post(reverse("notifications_read", args=[foreign.notification_id]))

        assert response.status_code == 404
        foreign.refresh_from_db()
        assert foreign.read_at is None

    def test_read_requires_post(self, client: Client, nir_user: User, case: Case) -> None:
        notification = _create_notification(nir_user, case)
        _login(client, nir_user, NIR_ROLE)

        response = client.get(reverse("notifications_read", args=[notification.notification_id]))

        assert response.status_code == 405
        notification.refresh_from_db()
        assert notification.read_at is None

    def test_read_anonymous_redirected_to_login(
        self, client: Client, nir_user: User, case: Case
    ) -> None:
        notification = _create_notification(nir_user, case)

        response = client.post(reverse("notifications_read", args=[notification.notification_id]))

        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("login"))
        notification.refresh_from_db()
        assert notification.read_at is None


@pytest.mark.django_db
class TestNotificationListPattern:
    """R2: lista no padrão — referência do caso, leitura individual, voltar."""

    def test_list_shows_case_reference(self, client: Client, nir_user: User, case: Case) -> None:
        _create_notification(nir_user, case)
        _login(client, nir_user, NIR_ROLE)

        body = client.get(reverse("notifications")).content.decode()

        assert CASE_REFERENCE in body

    def test_list_has_individual_read_form_for_unread(
        self, client: Client, nir_user: User, case: Case
    ) -> None:
        notification = _create_notification(nir_user, case)
        _login(client, nir_user, NIR_ROLE)

        body = client.get(reverse("notifications")).content.decode()

        assert f"/notifications/{notification.notification_id}/read/" in body
        assert "Marcar como lida" in body

    def test_list_has_back_link_and_polling(self, client: Client, nir_user: User) -> None:
        _login(client, nir_user, NIR_ROLE)

        body = client.get(reverse("notifications")).content.decode()

        assert "← Voltar" in body
        assert reverse("home") in body
        assert 'data-unread-count-url="/notifications/unread-count/"' in body


@pytest.mark.django_db
class TestProfileAndManualPattern:
    """R3: perfil e manual no padrão (page-title, cards)."""

    def test_profile_has_back_link(self, client: Client, nir_user: User) -> None:
        _login(client, nir_user, NIR_ROLE)

        body = client.get(reverse("profile")).content.decode()

        assert "← Voltar" in body
        assert reverse("home") in body

    def test_manual_sections_in_cards(self, client: Client, nir_user: User) -> None:
        _login(client, nir_user, NIR_ROLE)

        body = client.get(reverse("manual")).content.decode()

        assert body.count('class="card') >= 4
        assert "@media print" in body
