"""Testes do login via backends Kerberos/local (change ad-kerberos, slice 003).

Cobre R1–R4/R7 do slice:

- R1: ``KerberosBackend`` — username normalizado (strip/lower), usuário
  inexistente/sem ``ad_upn`` não consulta o KDC, ``account_status`` checado
  ANTES do AS-REQ (conta bloqueada não aciona a factory), sucesso devolve o
  usuário, falha de autenticação devolve ``None`` sem failover;
- R1/D4: indisponibilidade (``all_kdcs_unreachable``) marca
  ``request.kerberos_unavailable`` e a view mostra "serviço indisponível";
- R2: ``LocalAccountBackend`` hermético — usuário AD nunca usa senha local;
  admin local por design é apenas superusuário sem ``ad_upn`` (sem flag —
  ADR-0009); usuário comum sem ``ad_upn`` é recusado sempre;
- R4/R7: mensagens de login distinguem credenciais inválidas de serviço
  indisponível (nenhuma mensagem revela código KDC/status interno);
- integração do contrato completo de login (rate-limit fora — slice 004 →
  backends em ordem → sessão → mensagens).

Nenhum teste toca a rede: a factory ``KERBEROS_CLIENT_FACTORY`` é sempre
substituída por fakes via ``override_settings`` (mesma técnica do slice 002).
"""

import logging
from collections.abc import Sequence

import pytest
from django.contrib.auth import authenticate
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from apps.accounts.backends import KerberosBackend, LocalAccountBackend
from apps.accounts.kerberos import KerberosAuthResult
from apps.accounts.models import Role, User
from apps.accounts.views import INVALID_CREDENTIALS_MESSAGE, SERVICE_UNAVAILABLE_MESSAGE

CPF = "12345678901"
AD_UPN = "12345678901@dominio-teste.local"
AD_PASSWORD = "senha-ad-correta"
LOCAL_PASSWORD = "senha-local-123"
DC_ONE = "10.0.0.19"
DC_TWO = "10.0.0.21"


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


def _create_user(
    *,
    username: str = CPF,
    ad_upn: str | None = None,
    password: str = LOCAL_PASSWORD,
    is_superuser: bool = False,
    account_status: str = "active",
    role_names: Sequence[str] = ("doctor",),
) -> User:
    """Cria usuário com ``ad_upn``/superuser/status/papéis opcionais."""
    user = User.objects.create_user(username=username, password=password)
    user.is_superuser = is_superuser
    user.ad_upn = ad_upn
    user.account_status = account_status
    user.save(update_fields=["is_superuser", "ad_upn", "account_status"])
    for name in role_names:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste (padrão dos testes do app)."""
    return Client()


@pytest.mark.django_db
class TestKerberosBackend:
    """R1: regras do ``KerberosBackend`` — nenhuma chamada de KDC indevida."""

    def test_nonexistent_user_skips_kdc(self) -> None:
        factory = FakeKerberosFactory([KerberosAuthResult(ok=True)])

        with override_settings(KERBEROS_CLIENT_FACTORY=factory):
            result = KerberosBackend().authenticate(
                None, username="cpf-inexistente", password=AD_PASSWORD
            )

        assert result is None
        assert factory.calls == []

    def test_user_without_ad_upn_skips_kdc(self) -> None:
        """Usuário existe mas é local (sem ``ad_upn``) → None, sem KDC."""
        _create_user(username=CPF, ad_upn=None)
        factory = FakeKerberosFactory([KerberosAuthResult(ok=True)])

        with override_settings(KERBEROS_CLIENT_FACTORY=factory):
            result = KerberosBackend().authenticate(None, username=CPF, password=AD_PASSWORD)

        assert result is None
        assert factory.calls == []

    def test_user_with_empty_ad_upn_skips_kdc(self) -> None:
        """``ad_upn=""`` (blank persistido via ORM) = sem UPN → None, sem KDC.

        ``CharField(null=True, blank=True)`` permite string vazia quando o
        usuário é salvo sem ``full_clean`` — a checagem é blank-aware (P1).
        """
        _create_user(username=CPF, ad_upn="")
        factory = FakeKerberosFactory([KerberosAuthResult(ok=True)])

        with override_settings(KERBEROS_CLIENT_FACTORY=factory):
            result = KerberosBackend().authenticate(None, username=CPF, password=AD_PASSWORD)

        assert result is None
        assert factory.calls == []

    def test_username_normalized_strip_lower_before_lookup(self) -> None:
        """CPF com espaços/caixa alta resolve o mesmo usuário (R1/D3)."""
        _create_user(username=CPF, ad_upn=AD_UPN)
        factory = FakeKerberosFactory([KerberosAuthResult(ok=True)])

        with override_settings(
            AD_DCS=[DC_ONE],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            user = KerberosBackend().authenticate(
                None, username=f"  {CPF.upper()}  ", password=AD_PASSWORD
            )

        assert user is not None
        assert user.username == CPF
        assert factory.calls[0][0] == AD_UPN

    def test_success_returns_user_with_full_upn(self) -> None:
        """Sucesso → usuário devolvido; a factory recebe o UPN completo."""
        _create_user(ad_upn=AD_UPN)
        factory = FakeKerberosFactory([KerberosAuthResult(ok=True)])

        with override_settings(
            AD_DCS=[DC_ONE],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            user = KerberosBackend().authenticate(None, username=CPF, password=AD_PASSWORD)

        assert user is not None
        assert user.ad_upn == AD_UPN
        assert factory.calls == [(AD_UPN, AD_PASSWORD, DC_ONE, 3)]

    def test_blocked_account_skips_kdc_unit(self) -> None:
        """Status checado ANTES do AS-REQ: conta bloqueada não aciona a factory."""
        _create_user(ad_upn=AD_UPN, account_status="blocked")
        factory = FakeKerberosFactory([KerberosAuthResult(ok=True)])

        with override_settings(KERBEROS_CLIENT_FACTORY=factory):
            result = KerberosBackend().authenticate(None, username=CPF, password=AD_PASSWORD)

        assert result is None
        assert factory.calls == []

    def test_all_dcs_unreachable_marks_request_kerberos_unavailable(self) -> None:
        """Contrato request-scoped (D4): marca o atributo na request."""
        _create_user(ad_upn=AD_UPN)
        factory = FakeKerberosFactory(
            [
                KerberosAuthResult(False, reason="kdc_unreachable"),
                KerberosAuthResult(False, reason="kdc_timeout"),
            ]
        )
        request = RequestFactory().post("/login/")

        with override_settings(
            AD_DCS=[DC_ONE, DC_TWO],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            result = KerberosBackend().authenticate(request, username=CPF, password=AD_PASSWORD)

        assert result is None
        assert request.kerberos_unavailable is True  # type: ignore[attr-defined]

    def test_auth_error_does_not_mark_service_unavailable(self) -> None:
        """Código KDC de autenticação (24) é credencial inválida, não outage."""
        _create_user(ad_upn=AD_UPN)
        factory = FakeKerberosFactory([KerberosAuthResult(False, code=24, reason="kdc_error")])
        request = RequestFactory().post("/login/")

        with override_settings(
            AD_DCS=[DC_ONE, DC_TWO],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            result = KerberosBackend().authenticate(request, username=CPF, password=AD_PASSWORD)

        assert result is None
        assert len(factory.calls) == 1
        assert getattr(request, "kerberos_unavailable", False) is False

    def test_failure_log_has_no_cpf(self, caplog: pytest.LogCaptureFixture) -> None:
        """Falha loga code/reason sem o username/CPF (R1/D5 — sem PII)."""
        _create_user(ad_upn=AD_UPN)
        factory = FakeKerberosFactory([KerberosAuthResult(False, code=24, reason="kdc_error")])

        with caplog.at_level(logging.WARNING, logger="apps.accounts.backends"):
            with override_settings(KERBEROS_CLIENT_FACTORY=factory, AD_DCS=["dc-teste-1"]):
                result = KerberosBackend().authenticate(None, username=CPF, password=AD_PASSWORD)

        assert result is None
        assert caplog.records
        log_text = caplog.text
        assert CPF not in log_text
        assert AD_UPN not in log_text
        assert "code=24" in log_text
        assert "reason=kdc_error" in log_text

    def test_get_user_returns_only_active_accounts(self) -> None:
        """``get_user`` rejeita contas não-ativas (paridade com o backend)."""
        active = _create_user(username="ad.ativo", ad_upn="ad.ativo@dominio-teste.local")
        _create_user(
            username="ad.bloqueado",
            ad_upn="ad.bloqueado@dominio-teste.local",
            account_status="blocked",
        )

        assert KerberosBackend().get_user(active.pk) == active
        assert KerberosBackend().get_user(User.objects.get(username="ad.bloqueado").pk) is None


@pytest.mark.django_db
class TestLocalAccountBackend:
    """R2: regra hermética por ``ad_upn`` + admin local superuser-only.

    Contrato novo (change admin-local-identity, D1/D2): sem flag de
    habilitação — o superusuário sem ``ad_upn`` autentica por design e os
    demais perfis são recusados sempre.
    """

    def test_ad_user_cannot_use_local_password(self) -> None:
        """Usuário AD com senha local correta no banco → recusado pelo local."""
        _create_user(ad_upn=AD_UPN, password=LOCAL_PASSWORD)

        result = LocalAccountBackend().authenticate(None, username=CPF, password=LOCAL_PASSWORD)

        assert result is None

    def test_superuser_with_ad_upn_cannot_use_local_password(self) -> None:
        """Mesmo superusuário, com ``ad_upn`` → AD manda; senha local recusada."""
        _create_user(username="admin.ad", ad_upn="admin.ad@dominio-teste.local", is_superuser=True)

        result = LocalAccountBackend().authenticate(
            None, username="admin.ad", password=LOCAL_PASSWORD
        )

        assert result is None

    def test_superuser_without_ad_upn_authenticates_by_design(self) -> None:
        """Admin local: superusuário sem ``ad_upn`` autentica sem flag (R1).

        Sem flag de habilitação (extinta — D2): as settings de teste simulam
        produção.
        """
        _create_user(username="admin.break", is_superuser=True, password=LOCAL_PASSWORD)

        result = authenticate(None, username="admin.break", password=LOCAL_PASSWORD)

        assert result is not None

    def test_empty_ad_upn_superuser_eligible_for_local_auth(self) -> None:
        """``ad_upn=""`` (blank persistido via ORM) = sem UPN: o superusuário
        continua elegível à autenticação local (checagem blank-aware — P1)."""
        _create_user(username="admin.vazio", ad_upn="", is_superuser=True, password=LOCAL_PASSWORD)

        result = LocalAccountBackend().authenticate(
            None, username="admin.vazio", password=LOCAL_PASSWORD
        )

        assert result is not None

    def test_common_user_without_ad_upn_denied(self) -> None:
        """Usuário comum sem ``ad_upn`` é recusado mesmo com senha correta."""
        _create_user(username="comum.local", password=LOCAL_PASSWORD)

        result = authenticate(None, username="comum.local", password=LOCAL_PASSWORD)

        assert result is None

    def test_blocked_local_admin_denied(self) -> None:
        """``user_can_authenticate`` segue valendo para o admin local."""
        _create_user(username="admin.block", is_superuser=True, account_status="blocked")

        result = authenticate(None, username="admin.block", password=LOCAL_PASSWORD)

        assert result is None


@pytest.mark.django_db
class TestADLoginFlow:
    """R1/R4/R7: login AD ponta a ponta via view (factory fake)."""

    def test_ad_user_login_ok(self, client: Client) -> None:
        """Login AD válido cria sessão e redireciona para a home."""
        user = _create_user(ad_upn=AD_UPN)
        factory = FakeKerberosFactory([KerberosAuthResult(ok=True)])

        with override_settings(
            AD_DCS=[DC_ONE, DC_TWO],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            response = client.post(reverse("login"), {"username": CPF, "password": AD_PASSWORD})

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("home")
        assert int(client.session["_auth_user_id"]) == user.pk
        assert factory.calls[0][0] == AD_UPN
        # Sessão válida: a área de trabalho do papel renderiza (papel ativo
        # definido); follow=True porque a home despacha por papel ativo (change
        # painel-gerencial-e-home, slice 002).
        assert client.get(reverse("home"), follow=True).status_code == 200

    def test_wrong_password_denied_no_failover(self, client: Client) -> None:
        """Senha errada (24) → mensagem genérica de credenciais; sem 2º DC."""
        _create_user(ad_upn=AD_UPN)
        factory = FakeKerberosFactory([KerberosAuthResult(False, code=24, reason="kdc_error")])

        with override_settings(
            AD_DCS=[DC_ONE, DC_TWO],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            response = client.post(reverse("login"), {"username": CPF, "password": "senha-errada"})

        assert response.status_code == 200
        body = response.content.decode()
        assert INVALID_CREDENTIALS_MESSAGE in body
        assert SERVICE_UNAVAILABLE_MESSAGE not in body
        assert len(factory.calls) == 1
        # Nenhuma sessão foi criada.
        assert client.get(reverse("home")).status_code == 302

    def test_blocked_account_skips_kdc(self, client: Client) -> None:
        """Conta ``blocked`` com senha AD válida → negada sem acionar a factory."""
        _create_user(ad_upn=AD_UPN, account_status="blocked")
        factory = FakeKerberosFactory([KerberosAuthResult(ok=True)])

        with override_settings(KERBEROS_CLIENT_FACTORY=factory):
            response = client.post(reverse("login"), {"username": CPF, "password": AD_PASSWORD})

        assert response.status_code == 200
        assert INVALID_CREDENTIALS_MESSAGE in response.content.decode()
        assert factory.calls == []
        assert client.get(reverse("home")).status_code == 302

    def test_unknown_username_generic_message(self, client: Client) -> None:
        """CPF inexistente → mensagem genérica de credenciais (sem vazar nada)."""
        factory = FakeKerberosFactory([])

        with override_settings(KERBEROS_CLIENT_FACTORY=factory):
            response = client.post(
                reverse("login"), {"username": "99999999999", "password": AD_PASSWORD}
            )

        assert response.status_code == 200
        body = response.content.decode()
        assert INVALID_CREDENTIALS_MESSAGE in body
        assert SERVICE_UNAVAILABLE_MESSAGE not in body

    def test_all_dcs_down_shows_service_unavailable(self, client: Client) -> None:
        """Todos os DCs inalcançáveis → "serviço indisponível", distinto."""
        _create_user(ad_upn=AD_UPN)
        factory = FakeKerberosFactory(
            [
                KerberosAuthResult(False, reason="kdc_unreachable"),
                KerberosAuthResult(False, reason="kdc_timeout"),
            ]
        )

        with override_settings(
            AD_DCS=[DC_ONE, DC_TWO],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            response = client.post(reverse("login"), {"username": CPF, "password": AD_PASSWORD})

        assert response.status_code == 200
        body = response.content.decode()
        assert SERVICE_UNAVAILABLE_MESSAGE in body
        assert INVALID_CREDENTIALS_MESSAGE not in body
        assert len(factory.calls) == 2

    def test_full_login_contract(self, client: Client) -> None:
        """Contrato completo: backends em ordem → falha genérica → sessão AD.

        Rate-limit está fora (slice 004): nada entre o POST e os backends.
        A ordem fixa (Kerberos primeiro, local só break-glass) faz o usuário
        AD autenticar via Kerberos com o UPN completo do ``ad_upn``.
        """
        user = _create_user(ad_upn=AD_UPN)
        factory = FakeKerberosFactory(
            [
                KerberosAuthResult(False, code=24, reason="kdc_error"),
                KerberosAuthResult(ok=True),
            ]
        )

        with override_settings(
            AD_DCS=[DC_ONE, DC_TWO],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            # 1º POST: senha errada → credenciais inválidas, sem sessão.
            failed = client.post(reverse("login"), {"username": CPF, "password": "errada"})
            assert failed.status_code == 200
            assert INVALID_CREDENTIALS_MESSAGE in failed.content.decode()

            # 2º POST: senha correta → sessão do usuário AD criada.
            ok_response = client.post(reverse("login"), {"username": CPF, "password": AD_PASSWORD})

        assert ok_response.status_code == 302
        assert ok_response.headers["Location"] == reverse("home")
        assert int(client.session["_auth_user_id"]) == user.pk
        assert factory.calls[0][0] == AD_UPN
        assert factory.calls[1][0] == AD_UPN
