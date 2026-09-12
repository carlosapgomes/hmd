"""Testes dos endpoints de saúde e das settings de produção (slice 001, R3–R5).

Cobre:
- R3/R5: ``/healthz`` (liveness: anônimo, sem banco) e ``/readyz`` (readiness:
  ``SELECT 1`` no banco default; 503 quando o banco falha). Ambos públicos e
  isentos do ``IntranetGuardMiddleware`` (papel ``nir`` fora da intranet passa);
- R4/R5: settings de produção — cache default ``DatabaseCache`` na tabela
  ``hmd_cache`` (LocMem segue proibido), ``CSRF_TRUSTED_ORIGINS`` e
  ``SECURE_PROXY_SSL_HEADER`` lidos do ambiente.

O import de ``config.settings.prod`` usa ``importlib`` com envs dummy e remove o
módulo de ``sys.modules`` a cada teste (mesmo padrão de ``test_lockout.py``):
nenhuma secret real entra na suíte e as settings ativas (``config.settings.test``
já configuradas em ``django.conf``) não são afetadas.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

import pytest
from django.db import OperationalError
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User

PUBLIC_ORIGIN = "https://hmd.projetoshgrs.com"
EXTERNAL_IP = "203.0.113.10"
INTRANET_CIDR = "10.0.0.0/8"
GUARD_SETTINGS = {
    "INTRANET_IP_RANGE": INTRANET_CIDR,
    "INTRANET_RESTRICTED_ROLES": ["nir"],
}
PROD_SECRET_KEY = "chave-de-teste-de-producao"
PROD_DATABASE_URL = "postgres://build:build@localhost/build"
DATABASE_CACHE_BACKEND = "django.core.cache.backends.db.DatabaseCache"


class _BrokenConnection:
    """Conexão falsa cujo ``cursor()`` sempre falha (R3: 503 / liveness sem DB)."""

    def cursor(self) -> object:
        raise OperationalError("banco indisponível")


def _load_prod_settings(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Importa ``config.settings.prod`` com as envs mínimas (sem secret real)."""
    monkeypatch.setenv("DJANGO_SECRET_KEY", PROD_SECRET_KEY)
    monkeypatch.setenv("DATABASE_URL", PROD_DATABASE_URL)
    sys.modules.pop("config.settings.prod", None)
    return importlib.import_module("config.settings.prod")


class TestHealthz:
    """R3/R5: liveness pública e sem dependências."""

    def test_healthz_anonymous_returns_ok(self) -> None:
        """Anônimo recebe 200 JSON ``{"status": "ok"}`` (sem login)."""
        response = Client().get(reverse("healthz"))

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_healthz_does_not_touch_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Liveness nunca consulta o banco: conexão quebrada ainda responde 200."""
        url = reverse("healthz")
        monkeypatch.setattr("apps.accounts.views_health.connection", _BrokenConnection())

        response = Client().get(url)

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestReadyz:
    """R3/R5: readiness reflete o banco sem ler dados de negócio."""

    @pytest.mark.django_db
    def test_readyz_returns_ready_with_database(self) -> None:
        """Banco acessível → 200 JSON ``{"status": "ready"}``."""
        response = Client().get(reverse("readyz"))

        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    def test_readyz_returns_unavailable_when_cursor_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Banco inacessível → 503 JSON ``{"status": "unavailable"}``."""
        url = reverse("readyz")
        monkeypatch.setattr("apps.accounts.views_health.connection", _BrokenConnection())

        response = Client().get(url)

        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}


@pytest.mark.django_db
class TestHealthExemptFromGuard:
    """R3: health/ready são públicas — o IntranetGuard nunca responde 403 nelas."""

    @override_settings(**GUARD_SETTINGS)
    def test_nir_outside_intranet_reaches_health_endpoints(self, client: Client) -> None:
        """Papel ``nir`` ativo de IP externo acessa /healthz e /readyz sem 403."""
        user = User.objects.create_user(username="regulador.nir", password="senha-local-123")
        role, _ = Role.objects.get_or_create(name="nir")
        user.roles.add(role)
        client.force_login(user)
        session = client.session
        session["active_role"] = "nir"
        session.save()

        healthz = client.get(reverse("healthz"), REMOTE_ADDR=EXTERNAL_IP)
        readyz = client.get(reverse("readyz"), REMOTE_ADDR=EXTERNAL_IP)

        assert healthz.status_code == 200
        assert readyz.status_code == 200


class TestProdSettings:
    """R4/R5: cache, CSRF e header de proxy SSL da configuração de produção."""

    def test_prod_cache_is_database_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cache default de prod = ``DatabaseCache`` na tabela ``hmd_cache``.

        Duplicado propositalmente removido (P2 review F5): o invariante vive
        em ``test_lockout.py::TestProdCacheDefault`` (documenta R3b do
        change ad-kerberos); aqui só o essencial de runtime.
        """
        prod = _load_prod_settings(monkeypatch)

        assert prod.CACHES["default"]["BACKEND"] == DATABASE_CACHE_BACKEND

    def test_prod_csrf_trusted_origins_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sem env, a origem pública do piloto é a confiável (D3)."""
        monkeypatch.delenv("CSRF_TRUSTED_ORIGINS", raising=False)

        prod = _load_prod_settings(monkeypatch)

        assert prod.CSRF_TRUSTED_ORIGINS == [PUBLIC_ORIGIN]

    def test_prod_csrf_trusted_origins_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Env com várias origens: split por vírgula, com strip e sem vazios."""
        monkeypatch.setenv("CSRF_TRUSTED_ORIGINS", " https://a.example , https://b.example ,")

        prod = _load_prod_settings(monkeypatch)

        assert prod.CSRF_TRUSTED_ORIGINS == ["https://a.example", "https://b.example"]

    def test_prod_proxy_ssl_header_default_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sem env, o header de proxy SSL fica ligado (default do piloto)."""
        monkeypatch.delenv("PROXY_SSL_HEADER", raising=False)

        prod = _load_prod_settings(monkeypatch)

        assert prod.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")

    def test_prod_proxy_ssl_header_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``PROXY_SSL_HEADER=false`` desliga a leitura do header de proxy."""
        monkeypatch.setenv("PROXY_SSL_HEADER", "false")

        prod = _load_prod_settings(monkeypatch)

        assert prod.SECURE_PROXY_SSL_HEADER is None


@pytest.mark.django_db
def test_readyz_unavailable_when_cache_table_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2 review F2: cache compartilhado inutilizável (tabela hmd_cache
    ausente/erro) ⇒ 503 mesmo com o banco respondendo — o login depende do
    cache, o readyz não pode dizer pronto antes disso."""
    from django.core.cache import cache as real_cache

    def _boom(_key: str) -> None:
        raise RuntimeError("cache table missing")

    monkeypatch.setattr(real_cache, "get", _boom)

    client = Client()

    response = client.get(reverse("readyz"))

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
