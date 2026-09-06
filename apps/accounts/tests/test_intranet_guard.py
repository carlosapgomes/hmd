"""Testes do IntranetGuardMiddleware (slice 006, R1–R6).

Cobre:
- R1/R4: bloqueio pelo papel ativo fora de ``INTRANET_IP_RANGE`` — ``nir``
  externo bloqueado, demais papéis liberados (cenários 1 e 2 da spec);
- R2: IP real via ``TRUSTED_PROXY_HEADER`` com fallback para ``REMOTE_ADDR``;
- R3: paths isentos (login, logout, switch-role, static/media) nunca bloqueiam;
- R4: multi-role bloqueado com ``nir`` ativo é liberado ao trocar para
  ``manager`` via /switch-role/ (cenário 3 da spec, rota de fuga);
- R5: guard registrado após o ``ActiveRoleMiddleware`` no settings;
- R6: sem ``INTRANET_IP_RANGE`` (default de dev) não há bloqueio.
"""

from collections.abc import Sequence

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User

USERNAME = "regulador.nir"
PASSWORD = "senha-local-123"
EXTERNAL_IP = "203.0.113.10"
INTRANET_IP = "10.20.30.40"
INTRANET_CIDR = "10.0.0.0/8"
BLOCK_MESSAGE_FRAGMENT = "rede interna"
MIDDLEWARE_GUARD = "apps.accounts.middleware.IntranetGuardMiddleware"
MIDDLEWARE_ACTIVE_ROLE = "apps.accounts.middleware.ActiveRoleMiddleware"
GUARD_SETTINGS = {
    "INTRANET_IP_RANGE": INTRANET_CIDR,
    "INTRANET_RESTRICTED_ROLES": ["nir"],
}


def _create_user(*, username: str, role_names: Sequence[str]) -> User:
    """Cria usuário com os papéis informados (senha fixa ``PASSWORD``)."""
    user = User.objects.create_user(username=username, password=PASSWORD)
    for name in role_names:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


def _login_with_active_role(client: Client, *, username: str, role: str) -> None:
    """Autentica o usuário e fixa o papel ativo ``role`` na sessão."""
    client.force_login(User.objects.get(username=username))
    session = client.session
    session["active_role"] = role
    session.save()


@pytest.mark.django_db
class TestIntranetGuard:
    """R1/R3/R4: guard restringe o papel ativo configurado à intranet."""

    @override_settings(**GUARD_SETTINGS)
    def test_nir_blocked_outside_range(self, client: Client) -> None:
        """Cenário 1 da spec: ``nir`` externo recebe resposta de bloqueio."""
        _create_user(username=USERNAME, role_names=["nir"])
        _login_with_active_role(client, username=USERNAME, role="nir")

        response = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)

        assert response.status_code == 403
        assert BLOCK_MESSAGE_FRAGMENT in response.content.decode()

    @override_settings(**GUARD_SETTINGS)
    def test_doctor_allowed_outside_range(self, client: Client) -> None:
        """Cenário 2 da spec: papel não restrito acessa de qualquer rede."""
        _create_user(username="cardiologista.medica", role_names=["doctor"])
        _login_with_active_role(client, username="cardiologista.medica", role="doctor")

        response = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)

        assert response.status_code == 200

    @override_settings(**GUARD_SETTINGS)
    def test_multi_role_blocked_on_nir_and_released_after_switch(self, client: Client) -> None:
        """Cenário 3 da spec: bloqueio segue o papel ativo, sem bypass.

        Multi-role ``nir``+``manager`` externo é bloqueado com ``nir`` ativo; a
        troca para ``manager`` via /switch-role/ (path isento) reabilita o
        acesso — a restrição nunca considera o conjunto de papéis.
        """
        _create_user(username="regulador.gerente", role_names=["nir", "manager"])
        _login_with_active_role(client, username="regulador.gerente", role="nir")

        blocked = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)
        assert blocked.status_code == 403

        switch = client.post(reverse("switch_role"), {"role": "manager"}, REMOTE_ADDR=EXTERNAL_IP)
        assert switch.status_code == 302
        assert client.session["active_role"] == "manager"

        allowed = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)
        assert allowed.status_code == 200

    @override_settings(**GUARD_SETTINGS)
    def test_exempt_paths_never_blocked(self, client: Client) -> None:
        """R3: login/logout/switch-role e static/media nunca bloqueiam."""
        _create_user(username=USERNAME, role_names=["nir"])
        _login_with_active_role(client, username=USERNAME, role="nir")

        # /login/ autenticado redireciona para a home (302) — nunca 403.
        login_page = client.get(reverse("login"), REMOTE_ADDR=EXTERNAL_IP)
        assert login_page.status_code != 403
        # Tela de seleção de papel continua acessível (rota de fuga).
        switch_page = client.get(reverse("switch_role"), REMOTE_ADDR=EXTERNAL_IP)
        assert switch_page.status_code == 200
        # Static/media são isentos mesmo autenticado como papel restrito.
        static = client.get("/static/css/app.css", REMOTE_ADDR=EXTERNAL_IP)
        assert static.status_code != 403
        media = client.get("/media/inexistente.png", REMOTE_ADDR=EXTERNAL_IP)
        assert media.status_code != 403
        # Logout via POST segue o fluxo normal (302 para o login).
        logout_response = client.post(reverse("logout"), REMOTE_ADDR=EXTERNAL_IP)
        assert logout_response.status_code == 302


@pytest.mark.django_db
class TestClientIpResolution:
    """R2: origem lida do header confiável, com fallback para REMOTE_ADDR."""

    @override_settings(**GUARD_SETTINGS, TRUSTED_PROXY_HEADER="HTTP_CF_CONNECTING_IP")
    def test_trusted_proxy_header_used(self, client: Client) -> None:
        """Header confiável com IP da intranet libera com REMOTE_ADDR externo."""
        _create_user(username=USERNAME, role_names=["nir"])
        _login_with_active_role(client, username=USERNAME, role="nir")

        response = client.get(
            reverse("home"),
            HTTP_CF_CONNECTING_IP=INTRANET_IP,
            REMOTE_ADDR=EXTERNAL_IP,
        )

        assert response.status_code == 200

    @override_settings(**GUARD_SETTINGS, TRUSTED_PROXY_HEADER="HTTP_CF_CONNECTING_IP")
    def test_empty_proxy_header_falls_back_to_remote_addr(self, client: Client) -> None:
        """Header confiável vazio cai no fallback REMOTE_ADDR."""
        _create_user(username=USERNAME, role_names=["nir"])
        _login_with_active_role(client, username=USERNAME, role="nir")

        # Header presente mas vazio → REMOTE_ADDR decide (fora da faixa).
        blocked = client.get(
            reverse("home"),
            HTTP_CF_CONNECTING_IP="",
            REMOTE_ADDR=EXTERNAL_IP,
        )
        assert blocked.status_code == 403

        # Sem header algum → REMOTE_ADDR dentro da faixa libera.
        allowed = client.get(reverse("home"), REMOTE_ADDR=INTRANET_IP)
        assert allowed.status_code == 200


@pytest.mark.django_db
class TestIntranetGuardDefaults:
    """R6: default de dev (sem faixa configurada) nunca bloqueia."""

    @override_settings(INTRANET_RESTRICTED_ROLES=["nir"], INTRANET_IP_RANGE="")
    def test_no_range_configured_does_not_block(self, client: Client) -> None:
        """Sem ``INTRANET_IP_RANGE`` o papel restrito não é bloqueado (dev)."""
        _create_user(username=USERNAME, role_names=["nir"])
        _login_with_active_role(client, username=USERNAME, role="nir")

        response = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)

        assert response.status_code == 200


class TestMiddlewareOrdering:
    """R5: guard roda após o ActiveRoleMiddleware (precisa do papel ativo)."""

    def test_guard_registered_after_active_role_middleware(self) -> None:
        middleware = settings.MIDDLEWARE
        assert middleware.index(MIDDLEWARE_GUARD) > middleware.index(MIDDLEWARE_ACTIVE_ROLE)
