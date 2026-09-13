"""Testes do IntranetGuardMiddleware (slice 006, R1–R6).

Cobre:
- R1/R4: bloqueio pelo papel ativo fora de ``INTRANET_IP_RANGE`` — ``nir``
  externo bloqueado, demais papéis liberados (cenários 1 e 2 da spec);
- R2: IP real via ``TRUSTED_PROXY_HEADER`` com fallback para ``REMOTE_ADDR``;
- R3: paths isentos (login, logout, switch-role, static/media) nunca bloqueiam;
- R4 revisado (2026-09-13): multi-role com papel ativo ``nir`` acessa
  externamente sem trocar de papel (cenário 3 da spec); conjunto inteiramente
  restrito permanece bloqueado (cenário 4 da spec);
- R5: guard registrado após o ``ActiveRoleMiddleware`` no settings;
- R6: sem ``INTRANET_IP_RANGE`` (default de dev) não há bloqueio.

O change ``intranet-blocked-logout`` enriquece o ramo de bloqueio (R1–R3): a
resposta 403 renderiza ``accounts/intranet_blocked.html`` (mensagem + botão de
volta ao login), a sessão é encerrada no bloqueio e o link leva de fato ao
formulário de login (usuário já anônimo).
"""

import re
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
# Button-link da página de bloqueio (intranet-blocked-logout, R1/R3); tolera
# qualquer ordem/atributo extra na tag <a>.
LOGIN_LINK_PATTERN = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>\s*Voltar ao login\s*</a>')
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


def _login_link(body: str) -> str:
    """Href do botão "Voltar ao login" da página de bloqueio (R1/R3)."""
    match = LOGIN_LINK_PATTERN.search(body)
    assert match is not None, "página de bloqueio sem link de volta ao login"
    return match.group(1)


@pytest.mark.django_db
class TestIntranetGuard:
    """R1/R3/R4: guard restringe o papel ativo configurado à intranet."""

    @override_settings(**GUARD_SETTINGS)
    def test_nir_blocked_outside_range(self, client: Client) -> None:
        """Cenário 1 da spec: ``nir`` externo recebe resposta de bloqueio.

        A página de bloqueio (intranet-blocked-logout, R1) mantém o 403, a
        mensagem atual e o link para a tela de login.
        """
        _create_user(username=USERNAME, role_names=["nir"])
        _login_with_active_role(client, username=USERNAME, role="nir")

        response = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)

        body = response.content.decode()
        assert response.status_code == 403
        assert BLOCK_MESSAGE_FRAGMENT in body
        assert _login_link(body) == reverse("login")

    @override_settings(**GUARD_SETTINGS)
    def test_blocked_session_is_terminated(self, client: Client) -> None:
        """R2: o bloqueio encerra a sessão (cookie) do usuário exclusivamente restrito.

        Depois do 403 a sessão não guarda mais autenticação nem papel ativo e a
        requisição seguinte é anônima — o guard não bloqueia de novo, a view
        protegida redireciona ao login.
        """
        _create_user(username=USERNAME, role_names=["nir"])
        _login_with_active_role(client, username=USERNAME, role="nir")
        assert "_auth_user_id" in client.session

        blocked = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)
        assert blocked.status_code == 403
        # Cookie de sessão EXPIRADO na própria resposta (review P2: não basta a
        # sessão estar limpa — o SessionMiddleware derruba o cookie, max-age 0).
        session_cookie = blocked.cookies[settings.SESSION_COOKIE_NAME]
        assert session_cookie["max-age"] == 0

        session = client.session
        assert "_auth_user_id" not in session
        assert "active_role" not in session

        retry = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)
        assert retry.status_code == 302
        assert retry.headers["Location"].startswith(reverse("login"))

    @override_settings(**GUARD_SETTINGS)
    def test_blocked_page_login_link_works(self, client: Client) -> None:
        """R3: o botão da página de bloqueio exibe o formulário de login.

        O usuário já está anônimo quando a página é renderizada, então
        ``/login/`` responde 200 com o formulário em vez de redirecionar para a
        home bloqueada.
        """
        _create_user(username=USERNAME, role_names=["nir"])
        _login_with_active_role(client, username=USERNAME, role="nir")

        blocked = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)
        login_url = _login_link(blocked.content.decode())

        login_page = client.get(login_url, REMOTE_ADDR=EXTERNAL_IP)

        assert login_page.status_code == 200
        assert 'name="password"' in login_page.content.decode()

    @override_settings(**GUARD_SETTINGS)
    def test_doctor_allowed_outside_range(self, client: Client) -> None:
        """Cenário 2 da spec: papel não restrito acessa de qualquer rede."""
        _create_user(username="cardiologista.medica", role_names=["doctor"])
        _login_with_active_role(client, username="cardiologista.medica", role="doctor")

        response = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)

        assert response.status_code == 200

    @override_settings(**GUARD_SETTINGS)
    def test_multi_role_with_external_role_passes_with_active_nir(self, client: Client) -> None:
        """Cenário 3 da spec: papel fora do conjunto restrito libera o acesso.

        Multi-role ``nir``+``manager`` externo com ``nir`` ativo prossegue sem
        trocar de papel — o bloqueio externo só vale para conjuntos
        exclusivamente restritos.
        """
        _create_user(username="regulador.gerente", role_names=["nir", "manager"])
        _login_with_active_role(client, username="regulador.gerente", role="nir")

        response = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)

        assert response.status_code == 200
        assert client.session["active_role"] == "nir"

    @override_settings(
        INTRANET_IP_RANGE=INTRANET_CIDR,
        INTRANET_RESTRICTED_ROLES=["nir", "scheduler"],
    )
    def test_all_roles_restricted_stays_blocked(self, client: Client) -> None:
        """Cenário 4 da spec: conjunto todo restrito permanece bloqueado.

        Com ``INTRANET_RESTRICTED_ROLES`` multi-valor, o usuário ``nir`` +
        ``scheduler`` é bloqueado externamente com qualquer um dos dois papéis
        ativo — inclusive depois da troca via /switch-role/ (path isento) — e a
        sessão é encerrada a cada bloqueio (intranet-blocked-logout, R2/R4).
        """
        _create_user(username="regulador.agendador", role_names=["nir", "scheduler"])
        _login_with_active_role(client, username="regulador.agendador", role="nir")

        blocked_with_nir = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)
        assert blocked_with_nir.status_code == 403
        assert BLOCK_MESSAGE_FRAGMENT in blocked_with_nir.content.decode()
        assert "_auth_user_id" not in client.session

        # O bloqueio anterior derrubou a sessão: reautentica (nir) para trocar
        # de papel pelo path isento e exercitar o segundo papel restrito.
        _login_with_active_role(client, username="regulador.agendador", role="nir")
        switch = client.post(reverse("switch_role"), {"role": "scheduler"}, REMOTE_ADDR=EXTERNAL_IP)
        assert switch.status_code == 302
        assert client.session["active_role"] == "scheduler"

        blocked_with_scheduler = client.get(reverse("home"), REMOTE_ADDR=EXTERNAL_IP)
        assert blocked_with_scheduler.status_code == 403
        assert "_auth_user_id" not in client.session

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

        # O bloqueio encerra a sessão (intranet-blocked-logout, R2): reautentica
        # para isolar o segundo caso, que é sobre resolução de IP.
        _login_with_active_role(client, username=USERNAME, role="nir")

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
