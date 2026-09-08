"""Testes do admin local por design + anti-lockout no Django admin (slice 001).

Cobre R1–R6 do slice do change admin-local-identity:

- R1: ``LocalAccountBackend`` autentica o perfil administrativo (superusuário
  sem ``ad_upn``) **sem nenhuma flag de habilitação** (extinta — ADR-0009),
  em settings que simulam produção; hermético para os demais perfis e custo
  constante para usuário inexistente;
- R3: ``config.admin.HmdAdminSite`` estende o login do admin — bloqueado
  recusa cedo sem tocar autenticação; falha de credencial do perfil local
  conta no anti-lockout; sucesso zera os contadores; falhas de perfil AD na
  rota do admin NÃO contam;
- R4: ``HmdAdminConfig`` com ``default_site`` faz o ``admin.site`` global
  resolver para a subclass; superuser local loga no /admin end-to-end.

A suíte não toca a rede: o perfil AD do teste R3d usa a factory fake de
Kerberos (mesma técnica dos demais testes do app).
"""

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import authenticate
from django.http import HttpRequest
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from apps.accounts.backends import LocalAccountBackend
from apps.accounts.kerberos import KerberosAuthResult
from apps.accounts.models import User
from apps.accounts.ratelimit import is_login_locked, register_failed_login
from config.admin import HmdAdminSite

USERNAME = "admin.hmd"
AD_UPN = "12345678901@dominio-teste.local"
LOCAL_PASSWORD = "senha-local-123"
WRONG_PASSWORD = "senha-errada"


class FakeKerberosFactory:
    """Factory fake com registro de chamadas (upn, senha, dc, timeout)."""

    def __init__(self, script: list[KerberosAuthResult]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str, str, int]] = []

    def __call__(self, upn: str, password: str, dc: str, timeout: int) -> KerberosAuthResult:
        if not self.script:
            raise AssertionError("FakeKerberosFactory chamada sem script — chamada inesperada")
        self.calls.append((upn, password, dc, timeout))
        return self.script.pop(0)


def _create_local_superuser(*, username: str = USERNAME) -> User:
    """Superusuário local (sem ``ad_upn``, ativo) — o perfil do admin (D1)."""
    user = User.objects.create_user(username=username, password=LOCAL_PASSWORD)
    user.is_superuser = True
    user.is_staff = True
    user.save(update_fields=["is_superuser", "is_staff"])
    return user


def _create_ad_staff_user() -> User:
    """Usuário assistencial com ``ad_upn`` e ``is_staff`` (perfil AD no admin)."""
    user = User.objects.create_user(username=USERNAME, password=LOCAL_PASSWORD)
    user.is_staff = True
    user.ad_upn = AD_UPN
    user.save(update_fields=["is_staff", "ad_upn"])
    return user


def _locked_probe() -> HttpRequest:
    """Request de sonda com o mesmo REMOTE_ADDR do client (127.0.0.1)."""
    return RequestFactory().post("/admin/login/", {})


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste (padrão dos testes do app)."""
    return Client()


@pytest.mark.django_db
class TestLocalBackendByDesign:
    """R1: autenticação local por design — sem flag, hermético."""

    def test_local_admin_authenticates_without_flag(self) -> None:
        """Superuser sem ``ad_upn`` autentica com settings SEM a flag (prod)."""
        # As settings não definem nenhuma chave de habilitação de autenticação
        # local — o ambiente de teste simula produção (D2).
        assert not any(name.startswith("AD_ALLOW") for name in dir(settings))
        _create_local_superuser()

        result = authenticate(None, username=USERNAME, password=LOCAL_PASSWORD)

        assert result is not None
        assert result.username == USERNAME
        assert result.is_superuser

    def test_local_backend_hermetic_ad_user_denied(self) -> None:
        """Com ``ad_upn`` (mesmo superuser) a senha local é recusada."""
        user = _create_local_superuser()
        user.ad_upn = AD_UPN
        user.save(update_fields=["ad_upn"])

        result = LocalAccountBackend().authenticate(
            None, username=USERNAME, password=LOCAL_PASSWORD
        )

        assert result is None

    def test_local_backend_hermetic_common_user_denied(self) -> None:
        """Usuário comum sem ``ad_upn`` não usa autenticação local."""
        User.objects.create_user(username="comum.local", password=LOCAL_PASSWORD)

        result = authenticate(None, username="comum.local", password=LOCAL_PASSWORD)

        assert result is None

    def test_local_backend_hermetic_wrong_password_denied(self) -> None:
        """Senha errada no perfil local → None."""
        _create_local_superuser()

        result = LocalAccountBackend().authenticate(
            None, username=USERNAME, password=WRONG_PASSWORD
        )

        assert result is None

    def test_local_backend_nonexistent_user_hashes_dummy_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Custo constante: usuário inexistente hasheia senha descartável.

        O backend mantém a mitigação de enumeração por tempo de resposta do
        ModelBackend mesmo sem a flag (R1).
        """
        hashed: list[str] = []
        original = User.set_password

        def spy(self: User, raw_password: str) -> None:
            hashed.append(raw_password)
            original(self, raw_password)

        monkeypatch.setattr(User, "set_password", spy)

        result = LocalAccountBackend().authenticate(
            None, username="cpf.inexistente", password=LOCAL_PASSWORD
        )

        assert result is None
        assert hashed == [LOCAL_PASSWORD]


@pytest.mark.django_db
class TestHmdAdminSiteLogin:
    """R3: login do Django admin coberto pelo anti-lockout local."""

    def test_admin_login_locked_refuses_early(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bloqueado recusa cedo (200 + mensagem genérica) SEM autenticar."""
        _create_local_superuser()
        # Enche os contadores até o lockout do CPF via a API pública (D7).
        probe = _locked_probe()
        for _ in range(settings.LOGIN_ATTEMPTS_LIMIT):
            register_failed_login(probe, USERNAME)
        assert is_login_locked(probe, USERNAME)

        # Qualquer consulta de backend/autenticação no bloqueio é falha de teste.
        def _fail_if_authenticated(*args: object, **kwargs: object) -> None:
            raise AssertionError("autenticação não pode rodar com login bloqueado")

        import django.contrib.auth as auth_module

        monkeypatch.setattr(auth_module, "authenticate", _fail_if_authenticated)

        response = client.post(
            reverse("admin:login"), {"username": USERNAME, "password": WRONG_PASSWORD}
        )

        assert response.status_code == 200
        body = response.content.decode()
        assert "errornote" in body
        # Mensagem genérica — nada revela bloqueio/limiar.
        assert "bloque" not in body.lower()
        assert "lockout" not in body.lower()
        # A recusa não limpa nem incrementa: segue bloqueado.
        assert is_login_locked(probe, USERNAME)

    def test_admin_login_counts_local_failures(self, client: Client) -> None:
        """Falha de credencial do perfil local no /admin conta no anti-lockout."""
        _create_local_superuser()
        probe = _locked_probe()

        for _ in range(settings.LOGIN_ATTEMPTS_LIMIT):
            response = client.post(
                reverse("admin:login"), {"username": USERNAME, "password": WRONG_PASSWORD}
            )
            assert response.status_code == 200

        # Limiar atingido: bloqueado; a próxima tentativa é recusada cedo.
        assert is_login_locked(probe, USERNAME)
        response = client.post(
            reverse("admin:login"), {"username": USERNAME, "password": WRONG_PASSWORD}
        )
        assert response.status_code == 200
        assert is_login_locked(probe, USERNAME)

    def test_admin_login_without_password_not_counted(self, client: Client) -> None:
        """POST sem senha (form inválido) NÃO conta como falha de credencial."""
        _create_local_superuser()
        probe = _locked_probe()

        for _ in range(settings.LOGIN_ATTEMPTS_LIMIT + 1):
            response = client.post(reverse("admin:login"), {"username": USERNAME})
            assert response.status_code == 200

        assert is_login_locked(probe, USERNAME) is False

    def test_admin_login_success_clears_failures(self, client: Client) -> None:
        """Sucesso no /admin zera os contadores do CPF (R3c)."""
        _create_local_superuser()
        probe = _locked_probe()

        for _ in range(3):
            client.post(reverse("admin:login"), {"username": USERNAME, "password": WRONG_PASSWORD})

        response = client.post(
            reverse("admin:login"), {"username": USERNAME, "password": LOCAL_PASSWORD}
        )
        assert response.status_code == 302
        assert client.get(reverse("admin:index")).status_code == 200

        # Contadores zerados: 4 novas falhas ainda não bloqueiam (sem a limpeza
        # seriam 3+4=7 falhas — acima do limiar).
        for _ in range(settings.LOGIN_ATTEMPTS_LIMIT - 1):
            client.post(reverse("admin:login"), {"username": USERNAME, "password": WRONG_PASSWORD})
        assert is_login_locked(probe, USERNAME) is False

    def test_admin_login_ad_profile_failure_not_counted(self, client: Client) -> None:
        """Falha de perfil AD no /admin NÃO registra nos contadores locais."""
        _create_ad_staff_user()
        probe = _locked_probe()
        attempts = settings.LOGIN_ATTEMPTS_LIMIT + 1
        factory = FakeKerberosFactory(
            [KerberosAuthResult(False, code=24, reason="kdc_error")] * attempts
        )

        with override_settings(KERBEROS_CLIENT_FACTORY=factory):
            for _ in range(attempts):
                response = client.post(
                    reverse("admin:login"), {"username": USERNAME, "password": WRONG_PASSWORD}
                )
                assert response.status_code == 200

        # As tentativas chegaram ao Kerberos (perfil AD), mas não contaram.
        assert len(factory.calls) == attempts
        assert is_login_locked(probe, USERNAME) is False


@pytest.mark.django_db
class TestAdminWiring:
    """R4: HmdAdminConfig/default_site e integração mínima do /admin."""

    def test_admin_site_is_hmd_subclass(self) -> None:
        """``admin.site`` global resolve para ``HmdAdminSite`` (R4)."""
        assert getattr(apps.get_app_config("admin"), "default_site") == (
            "config.admin.HmdAdminSite"
        )
        _ = admin.site.urls  # força a resolução do lazy site do Django
        assert isinstance(admin.site._wrapped, HmdAdminSite)  # type: ignore[attr-defined]

    def test_superuser_local_logs_into_admin(self, client: Client) -> None:
        """Login do admin renderiza e o superuser local loga sem flag (R4/R6)."""
        _create_local_superuser()

        response = client.get(reverse("admin:login"))
        assert response.status_code == 200

        response = client.post(
            reverse("admin:login"),
            # O form real do admin envia o ``next`` oculto (aponta pro índice).
            {"username": USERNAME, "password": LOCAL_PASSWORD, "next": reverse("admin:index")},
        )
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("admin:index")
        assert client.get(reverse("admin:index")).status_code == 200


@pytest.mark.django_db
def test_admin_login_username_normalization_shares_counters(client: Client) -> None:
    """R3(b): username com espacos/caixa alta cai nos MESMOS contadores."""
    admin_user = _create_local_superuser()
    url = reverse("admin:login")
    for username in (admin_user.username, f" {admin_user.username.upper()} "):
        client.post(url, {"username": username, "password": WRONG_PASSWORD})

    # 2 falhas do mesmo CPF (normalizado) contam juntas: a 3a, correta,
    # ainda passa (abaixo do limiar) — sem contadores duplicados por variante.
    response = client.post(url, {"username": admin_user.username, "password": LOCAL_PASSWORD})
    assert response.status_code == 302
