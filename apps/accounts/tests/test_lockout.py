"""Testes do anti-lockout local via cache (change ad-kerberos, slice 004).

Cobre R1–R5 do slice (design D7):

- R1: contadores por CPF **normalizado** (strip/lower — variações de
  formatação compartilham contador) e por IP+CPF no cache do Django; o IP vem
  do helper confiável do guard (``_get_client_ip``/``TRUSTED_PROXY_HEADER``),
  nunca de header arbitrário;
- R2: bloqueio checado ANTES de qualquer backend/KDC — a tentativa no limiar
  excedido não aciona a factory fake; sucesso zera contadores;
- R3/R3b: limiar/janela/duração nas settings; ``config.settings.prod`` usa
  cache compartilhado entre workers por default (``DatabaseCache`` na tabela
  ``hmd_cache`` — slice 001 do change pilot-deployment-v0-1-1/R4);
- R4: bloqueio temporário expira (cache zerado simula o TTL) sem tocar
  ``account_status`` nem persistir em banco;
- R5: IP+CPF independente do contador por CPF; spoof de header não-confiável
  não troca a chave de IP.

Nenhum teste toca a rede: a factory ``KERBEROS_CLIENT_FACTORY`` é sempre
substituída por fakes via ``override_settings`` (mesma técnica dos slices 002
e 003). O cache LocMem é zerado entre testes pelo conftest do diretório.
"""

import importlib
from collections.abc import Sequence

import pytest
from django.core.cache import cache
from django.http import HttpRequest
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from apps.accounts.kerberos import KerberosAuthResult
from apps.accounts.models import Role, User
from apps.accounts.ratelimit import clear_login_failures, is_login_locked, register_failed_login
from apps.accounts.views import INVALID_CREDENTIALS_MESSAGE, SERVICE_UNAVAILABLE_MESSAGE

CPF = "12345678901"
AD_UPN = "12345678901@dominio-teste.local"
AD_PASSWORD = "senha-ad-correta"
LOCAL_PASSWORD = "senha-local-123"
DC_ONE = "10.0.0.19"
DC_TWO = "10.0.0.21"
LIMIT = 5
# IPs de teste: ``10.0.0.5`` é o REMOTE_ADDR (confiável por padrão);
# ``203.0.113.9``/``198.51.100.7`` são valores de documentação (TEST-NET) para
# headers de proxy falsificados ou trusted.
CLIENT_IP = "10.0.0.5"
SPOOF_IP = "203.0.113.9"
TRUSTED_CF_IP = "198.51.100.7"
# Header confiável explícito nos testes de IP (isolar do .env local).
TRUSTED_HEADER = "HTTP_CF_CONNECTING_IP"


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
    ad_upn: str | None = AD_UPN,
    password: str = LOCAL_PASSWORD,
    is_superuser: bool = False,
    account_status: str = "active",
    role_names: Sequence[str] = ("doctor",),
) -> User:
    """Cria usuário de teste (default: usuário AD ativo com papel)."""
    user = User.objects.create_user(username=username, password=password)
    user.is_superuser = is_superuser
    user.ad_upn = ad_upn
    user.account_status = account_status
    user.save(update_fields=["is_superuser", "ad_upn", "account_status"])
    for name in role_names:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


def _make_request(
    *, remote_addr: str = CLIENT_IP, cf_ip: str | None = None, spoof: str | None = None
) -> HttpRequest:
    """Request de POST /login/ com o cenário META desejado.

    ``cf_ip`` popula o header **confiável** (``HTTP_CF_CONNECTING_IP``);
    ``spoof`` popula um header arbitrário (``HTTP_X_FORWARDED_FOR``) que o
    helper NÃO deve considerar como origem.
    """
    request = RequestFactory().post("/login/", {"username": CPF, "password": "x"})
    request.META["REMOTE_ADDR"] = remote_addr
    if cf_ip is not None:
        request.META[TRUSTED_HEADER] = cf_ip
    if spoof is not None:
        request.META["HTTP_X_FORWARDED_FOR"] = spoof
    return request


def _fail_attempts(client: Client, count: int, password: str = "senha-errada") -> None:
    """``count`` POSTs de login malsucedidos (senha errada)."""
    for _ in range(count):
        response = client.post(reverse("login"), {"username": CPF, "password": password})
        assert response.status_code == 200


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste (padrão dos testes do app)."""
    return Client()


class TestRatelimitCounters:
    """R1/R5 unitários: contadores, normalização de CPF e IP confiável."""

    @pytest.mark.django_db
    def test_counters_by_normalized_cpf_and_trusted_ip(self) -> None:
        """R1: contadores por CPF normalizado e por IP do header confiável."""
        with override_settings(TRUSTED_PROXY_HEADER=TRUSTED_HEADER):
            request = _make_request(cf_ip=TRUSTED_CF_IP)
            for _ in range(LIMIT):
                register_failed_login(request, CPF)

            # Mesmo CPF + mesmo IP confiável: bloqueado.
            assert is_login_locked(_make_request(cf_ip=TRUSTED_CF_IP), CPF) is True

    def test_below_limit_is_not_locked(self) -> None:
        """Dentro do limiar não bloqueia; atinge o limiar e bloqueia."""
        request = _make_request()
        for _ in range(LIMIT - 1):
            register_failed_login(request, CPF)
        assert is_login_locked(_make_request(), CPF) is False

        register_failed_login(request, CPF)
        assert is_login_locked(_make_request(), CPF) is True

    @pytest.mark.django_db
    def test_cpf_variations_count_toward_same_counter(self) -> None:
        """Variações de CPF (espaços/caixa) contam para o mesmo contador."""
        request = _make_request()
        variations = [f"  {CPF}  ", CPF.upper(), f" {CPF}"]
        for _ in range(LIMIT):
            register_failed_login(request, variations[_ % len(variations)])

        # O CPF normalizado está bloqueado; outro CPF não é afetado.
        assert is_login_locked(_make_request(), CPF) is True
        assert is_login_locked(_make_request(), "99999999999") is False

    def test_cpf_lowercase_variations_share_counter(self) -> None:
        """Normalização inclui lower: caixa alta/baixa compartilham o contador."""
        request = _make_request()
        for token in ("  UsEr-Ad.1  ", "user-ad.1", " USER-AD.1 "):
            register_failed_login(request, token)
        for _ in range(LIMIT - 3):
            register_failed_login(request, "user-ad.1")
        assert is_login_locked(_make_request(), "user-ad.1") is True

    @pytest.mark.django_db
    def test_ip_cpf_entries_independent_of_cpf_counter(self) -> None:
        """R5: IP+CPF tem entrada própria; o escopo CPF vale entre IPs."""
        request_a = _make_request(remote_addr="10.0.0.5")
        for _ in range(LIMIT):
            register_failed_login(request_a, CPF)

        # Par (ip B, CPF X) nunca tentado → livre mesmo com CPF X bloqueado em A.
        request_b = _make_request(remote_addr="10.0.0.9")
        assert is_login_locked(request_b, CPF) is True  # escopo CPF (X) atravessa IPs.
        # Outro CPF a partir do mesmo IP A → livre (entrada IP+CPF independente).
        assert is_login_locked(request_a, "99999999999") is False

    @pytest.mark.django_db
    def test_untrusted_header_spoof_does_not_change_key(self) -> None:
        """R5: spoof de header não-confiável não muda a chave de IP."""
        with override_settings(TRUSTED_PROXY_HEADER=TRUSTED_HEADER):
            # 5 falhas do mesmo REMOTE_ADDR, 3 delas com X-Forwarded-For
            # falsificado (valores distintos): se o XFF fosse tratado como IP
            # de origem, essas falhas iriam para chaves diferentes e o par
            # (10.0.0.5, CPF) nunca atingiria o limiar.
            for _ in range(3):
                register_failed_login(_make_request(remote_addr=CLIENT_IP, spoof=SPOOF_IP), CPF)
            for _ in range(LIMIT - 3):
                register_failed_login(_make_request(remote_addr=CLIENT_IP, spoof="192.0.2.7"), CPF)
            assert is_login_locked(_make_request(remote_addr=CLIENT_IP), CPF) is True

            # Sucesso a partir de outro IP zera o escopo CPF e o par do novo
            # IP, mas o par (IP antigo, CPF) permanece bloqueado — prova que
            # as falhas ficaram registradas sob o REMOTE_ADDR confiável.
            clear_login_failures(_make_request(remote_addr="10.0.0.9"), CPF)
            assert is_login_locked(_make_request(remote_addr="10.0.0.9"), CPF) is False
            assert is_login_locked(_make_request(remote_addr=CLIENT_IP), CPF) is True

    @pytest.mark.django_db
    def test_clear_login_failures_resets_scopes(self) -> None:
        """clear_login_failures zera contadores e lockouts (sucesso zera)."""
        request = _make_request()
        for _ in range(LIMIT):
            register_failed_login(request, CPF)
        assert is_login_locked(request, CPF) is True

        clear_login_failures(request, CPF)
        assert is_login_locked(request, CPF) is False
        # Nova falha conta de 1: ainda abaixo do limiar.
        register_failed_login(request, CPF)
        assert is_login_locked(request, CPF) is False


@pytest.mark.django_db
class TestLoginLockoutFlow:
    """R2/R4: integração no fluxo de login (recusa pré-KDC, zera, expira)."""

    def test_locked_attempt_skips_kerberos(self, client: Client) -> None:
        """R2: limiar atingido → próxima tentativa negada sem acionar o KDC."""
        _create_user(ad_upn=AD_UPN)
        factory = FakeKerberosFactory(
            [KerberosAuthResult(False, code=24, reason="kdc_error") for _ in range(LIMIT)]
        )

        with override_settings(
            AD_DCS=[DC_ONE],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            _fail_attempts(client, LIMIT)
            assert len(factory.calls) == LIMIT

            # 6ª tentativa (senha correta): recusada com mensagem genérica e
            # SEM acionar a factory (a proteção corre antes do backend/KDC).
            blocked = client.post(reverse("login"), {"username": CPF, "password": AD_PASSWORD})

        assert blocked.status_code == 200
        body = blocked.content.decode()
        assert INVALID_CREDENTIALS_MESSAGE in body
        assert "bloquead" not in body and "temporariamente" not in body
        assert len(factory.calls) == LIMIT
        # Nenhuma sessão foi criada.
        assert client.get(reverse("home")).status_code == 302

    def test_below_limit_does_not_block_success(self, client: Client) -> None:
        """Dentro do limiar, o login bem-sucedido continua autorizado."""
        _create_user(ad_upn=AD_UPN)
        factory = FakeKerberosFactory(
            [KerberosAuthResult(False, code=24, reason="kdc_error") for _ in range(LIMIT - 1)]
            + [KerberosAuthResult(ok=True)]
        )

        with override_settings(
            AD_DCS=[DC_ONE],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            _fail_attempts(client, LIMIT - 1)
            response = client.post(reverse("login"), {"username": CPF, "password": AD_PASSWORD})

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("home")

    def test_success_resets_counters(self, client: Client) -> None:
        """R2: sucesso zera — nova sequência de falhas volta a contar de 1."""
        user = _create_user(ad_upn=AD_UPN)
        results = [KerberosAuthResult(False, code=24, reason="kdc_error")] * (LIMIT - 1)
        script = results + [KerberosAuthResult(ok=True)] + results + [KerberosAuthResult(ok=True)]
        factory = FakeKerberosFactory(script)

        with override_settings(
            AD_DCS=[DC_ONE],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            # 4 falhas + sucesso (zera)…
            _fail_attempts(client, LIMIT - 1)
            first = client.post(reverse("login"), {"username": CPF, "password": AD_PASSWORD})
            assert first.status_code == 302
            assert int(client.session["_auth_user_id"]) == user.pk

            # …nova sequência de 4 falhas + sucesso: se não tivesse zerado, o
            # contador acumulado (8 >= 5) teria bloqueado esta tentativa.
            client.post(reverse("logout"))
            _fail_attempts(client, LIMIT - 1)
            second = client.post(reverse("login"), {"username": CPF, "password": AD_PASSWORD})

        assert second.status_code == 302
        assert second.headers["Location"] == reverse("home")
        assert len(factory.calls) == 2 * LIMIT

    def test_lockout_expires_without_touching_account_status(self, client: Client) -> None:
        """R4: bloqueio é do cache (TTL), expira e não altera account_status."""
        user = _create_user(ad_upn=AD_UPN)
        factory = FakeKerberosFactory(
            [KerberosAuthResult(False, code=24, reason="kdc_error") for _ in range(LIMIT)]
            + [KerberosAuthResult(ok=True)]
        )

        with override_settings(
            AD_DCS=[DC_ONE],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            _fail_attempts(client, LIMIT)
            blocked = client.post(reverse("login"), {"username": CPF, "password": AD_PASSWORD})
            assert blocked.status_code == 200
            assert len(factory.calls) == LIMIT
            # Conta permanece ativa no banco: o bloqueio não é DB/account_status.
            user.refresh_from_db()
            assert user.account_status == "active"

            # Expiração simulada (cache zerado = TTLs vencidos).
            cache.clear()
            allowed = client.post(reverse("login"), {"username": CPF, "password": AD_PASSWORD})

        assert allowed.status_code == 302
        assert allowed.headers["Location"] == reverse("home")
        user.refresh_from_db()
        assert user.account_status == "active"

    def test_service_unavailable_failures_do_not_lock(self, client: Client) -> None:
        """R2 emendado: falhas por indisponibilidade NÃO contam para o limiar.

        N tentativas acima do limiar em que o serviço AD está fora (KDCs
        inalcançáveis → ``request.kerberos_unavailable`` setada pelo backend)
        não chegaram ao AD: não podem contar para o lockout do AD nem para o
        limite local. Quando o serviço volta, o login com a senha correta
        deve passar — se a indisponibilidade contasse, o contador local teria
        bloqueado a tentativa (RED).
        """
        _create_user(ad_upn=AD_UPN)
        # Uma tentativa de outage = 2 DCs falhos (failover esgota →
        # ``all_kdcs_unreachable`` → marca ``request.kerberos_unavailable``).
        outage_attempt = [
            KerberosAuthResult(False, reason="kdc_unreachable"),
            KerberosAuthResult(False, reason="kdc_timeout"),
        ]
        factory = FakeKerberosFactory(outage_attempt * (LIMIT + 1) + [KerberosAuthResult(ok=True)])

        with override_settings(
            AD_DCS=[DC_ONE, DC_TWO],
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            for _ in range(LIMIT + 1):
                unavailable = client.post(
                    reverse("login"), {"username": CPF, "password": AD_PASSWORD}
                )
                assert unavailable.status_code == 200
                # Serviço fora é comunicado como indisponível — nunca como
                # credenciais inválidas nem bloqueio local.
                assert SERVICE_UNAVAILABLE_MESSAGE in unavailable.content.decode()

            # Serviço de volta: credenciais corretas autenticam sem bloqueio
            # local residual (as falhas de indisponibilidade não contaram).
            recovered = client.post(reverse("login"), {"username": CPF, "password": AD_PASSWORD})

        assert recovered.status_code == 302
        assert recovered.headers["Location"] == reverse("home")


class TestProdCacheDefault:
    """R3b/R4: produção usa cache compartilhado entre workers por default.

    Guard anti-LocMem permanece em prod.py como self-check do deploy; o default
    é DatabaseCache (R4 do slice 001).
    """

    def test_prod_cache_default_is_database_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Importar ``config.settings.prod`` resolve o cache para ``hmd_cache``."""
        monkeypatch.setenv("DJANGO_SECRET_KEY", "chave-de-teste-de-producao")
        monkeypatch.setenv("DATABASE_URL", "postgres://build:build@localhost/build")
        prod = importlib.reload(importlib.import_module("config.settings.prod"))

        assert prod.CACHES["default"]["BACKEND"] == (
            "django.core.cache.backends.database.DatabaseCache"
        )
        assert prod.CACHES["default"]["LOCATION"] == "hmd_cache"
