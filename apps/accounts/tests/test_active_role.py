"""Testes do papel ativo em sessão e switch-role (slice 005, R1–R6).

Cobre:
- R1: ``ActiveRoleMiddleware`` — auto-set para usuário com 1 papel,
  redirect para /switch-role/ com N papéis e logout com mensagem com 0 papéis;
- R2: ``switch_role`` lista os papéis no GET e valida pertencimento no POST;
- R3: ``role_context`` expõe ``active_role``/``user_roles``/``has_multiple_roles``;
- R4: ``role_required`` nega HTTP 403 com papel ativo errado (divergência
  deliberada vs ats-web) e redireciona anônimo ao login;
- R5: navbar exibe o papel ativo e o link de troca para multi-role.
"""

import sys
import types
from collections.abc import Callable, Sequence

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import path, reverse

from apps.accounts.context_processors import role_context
from apps.accounts.decorators import role_required
from apps.accounts.models import Role, User
from apps.accounts.urls import urlpatterns as accounts_urlpatterns

USERNAME = "maria.hemodinamica"
PASSWORD = "senha-local-123"
ZERO_ROLES_MESSAGE = "não possui papéis atribuídos"
# URLconf sintética para exercitar o decorator via HTTP completo (403 real).
TEST_URLCONF = "hmd_test_active_role_urls"


def _create_user(*, username: str, role_names: Sequence[str]) -> User:
    """Cria usuário com os papéis informados (senha fixa ``PASSWORD``)."""
    user = User.objects.create_user(username=username, password=PASSWORD)
    for name in role_names:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


def _install_role_test_urlconf(
    protected_view: Callable[..., HttpResponse],
) -> None:
    """Registra um urlconf sintético com uma única view protegida."""
    module = types.ModuleType(TEST_URLCONF)
    setattr(module, "urlpatterns", [path("protegida/", protected_view)])
    sys.modules[TEST_URLCONF] = module


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste."""
    return Client()


@pytest.mark.django_db
class TestActiveRoleMiddleware:
    """R1: auto-set, exigência de seleção e logout para zero papéis."""

    def test_single_role_auto_set(self, client: Client) -> None:
        """Usuário com 1 papel ganha papel ativo sem tela de seleção."""
        _create_user(username=USERNAME, role_names=["doctor"])
        client.force_login(_user := User.objects.get(username=USERNAME))

        # follow=True: a home agora despacha por papel ativo (change
        # painel-gerencial-e-home, slice 002) — a página final (fila médica)
        # segue sendo 200.
        response = client.get(reverse("home"), follow=True)

        assert response.status_code == 200
        assert client.session["active_role"] == "doctor"

    def test_multi_role_requires_selection(self, client: Client) -> None:
        """Multi-role sem papel ativo é redirecionado para /switch-role/."""
        _create_user(username=USERNAME, role_names=["doctor", "manager"])
        client.force_login(User.objects.get(username=USERNAME))

        response = client.get(reverse("home"))

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("switch_role")

        # A tela de seleção lista os papéis disponíveis do usuário: botão com
        # rótulo (badge/botão-scoped, não a chave crua) e chave no wire do form.
        page = client.get(reverse("switch_role"))
        assert page.status_code == 200
        body = page.content.decode()
        assert ">médico</button>" in body
        assert ">supervisor</button>" in body
        assert 'value="doctor"' in body
        assert 'value="manager"' in body

    def test_zero_roles_logs_out(self, client: Client) -> None:
        """Usuário sem papéis é deslogado com mensagem explicativa."""
        _create_user(username=USERNAME, role_names=[])
        client.force_login(User.objects.get(username=USERNAME))

        response = client.get(reverse("home"))

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("login")
        assert "_auth_user_id" not in client.session

        # A mensagem explicativa aparece na tela de login.
        login_page = client.get(reverse("login"))
        assert login_page.status_code == 200
        assert ZERO_ROLES_MESSAGE in login_page.content.decode()

    def test_revoked_active_role_is_no_longer_authorized(self, client: Client) -> None:
        """Papel ativo revogado não autoriza a próxima requisição (R6).

        O middleware roda antes do ``role_required`` na fase de request e
        descarta o papel revogado; com 2 papéis restantes o usuário é levado
        à re-seleção em vez de seguir autorizado como o papel antigo.
        """

        def _protected_view(request: HttpRequest) -> HttpResponse:
            return HttpResponse("ok")

        # URLconf com a view protegida + URLs reais de conta (o middleware
        # redireciona para /switch-role/ e precisa resolvê-lo).
        module = types.ModuleType(TEST_URLCONF)
        setattr(
            module,
            "urlpatterns",
            [
                *accounts_urlpatterns,
                path("protegida/", role_required("doctor")(_protected_view)),
            ],
        )
        sys.modules[TEST_URLCONF] = module

        _create_user(username=USERNAME, role_names=["doctor", "manager", "nurse"])
        user = User.objects.get(username=USERNAME)
        client.force_login(user)
        session = client.session
        session["active_role"] = "doctor"
        session.save()

        # Revoga o papel ativo; a próxima requisição não pode autorizar como
        # doctor (o decorator responderia 200 se a sessão ainda valesse).
        user.roles.remove(Role.objects.get(name="doctor"))

        with override_settings(ROOT_URLCONF=TEST_URLCONF):
            response = client.get("/protegida/")

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("switch_role")
        assert "active_role" not in client.session

        # A tela de re-seleção oferece apenas os papéis remanescentes.
        page = client.get(reverse("switch_role"))
        assert page.status_code == 200
        body = page.content.decode()
        assert ">supervisor</button>" in body
        assert 'value="manager"' in body
        assert "nurse" in body
        assert "doctor" not in body

    def test_revoking_last_role_logs_out(self, client: Client) -> None:
        """Revogar o único papel desloga o usuário — não evita o logout (R6)."""
        _create_user(username=USERNAME, role_names=["doctor"])
        user = User.objects.get(username=USERNAME)
        client.force_login(user)
        session = client.session
        session["active_role"] = "doctor"
        session.save()

        user.roles.remove(Role.objects.get(name="doctor"))

        response = client.get(reverse("home"))

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("login")
        assert "_auth_user_id" not in client.session

        # A mensagem explicativa aparece na tela de login.
        login_page = client.get(reverse("login"))
        assert login_page.status_code == 200
        assert ZERO_ROLES_MESSAGE in login_page.content.decode()


@pytest.mark.django_db
class TestSwitchRoleView:
    """R2: troca de papel grava na sessão e rejeita papel estranho."""

    def test_switch_role_updates_session(self, client: Client) -> None:
        """POST válido grava o papel na sessão e leva à home."""
        _create_user(username=USERNAME, role_names=["doctor", "manager"])
        client.force_login(User.objects.get(username=USERNAME))

        response = client.post(reverse("switch_role"), {"role": "manager"})

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("home")
        assert client.session["active_role"] == "manager"

        # A página final do papel trocado (painel) renderiza com o papel ativo
        # na navbar; follow=True porque a home despacha por papel (slice 002).
        home = client.get(reverse("home"), follow=True)
        assert home.status_code == 200
        # Badge-scoped: a página é a do dispatcher com ativo manager; a chave
        # crua apareceria de graça no comentário HTML de base.html.
        assert 'title="Papel ativo">supervisor<' in home.content.decode()

    def test_switch_role_rejects_foreign_role(self, client: Client) -> None:
        """Papel não atribuído ao usuário é rejeitado sem gravar na sessão."""
        _create_user(username=USERNAME, role_names=["doctor"])
        client.force_login(User.objects.get(username=USERNAME))

        response = client.post(reverse("switch_role"), {"role": "manager"})

        assert response.status_code == 200
        assert "active_role" not in client.session
        assert "não atribuído" in response.content.decode()

    def test_switch_role_requires_login(self, client: Client) -> None:
        """Usuário anônimo é redirecionado ao login (R4)."""
        response = client.get(reverse("switch_role"))

        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
class TestRoleContext:
    """R3: context processor expõe papel ativo, papéis e flag multi-role."""

    def test_role_context_in_template(self) -> None:
        _create_user(username=USERNAME, role_names=["doctor", "manager"])
        user = User.objects.get(username=USERNAME)

        request = RequestFactory().get("/")
        request.session = {"active_role": "doctor"}  # type: ignore[assignment]
        request.user = user

        context = role_context(request)

        assert context["active_role"] == "doctor"
        assert context["user_roles"] == ["doctor", "manager"]
        assert context["has_multiple_roles"] is True

    def test_role_context_anonymous_is_empty(self) -> None:
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        context = role_context(request)

        assert context["active_role"] == ""
        assert context["user_roles"] == []
        assert context["has_multiple_roles"] is False


@pytest.mark.django_db
class TestRoleRequired:
    """R4: decorator nega 403 e redireciona anônimo ao login."""

    def test_role_required_forbids_wrong_active_role(self, client: Client) -> None:
        """Papel ativo errado recebe HTTP 403 mesmo possuindo o papel (R4)."""

        def _protected_view(request: HttpRequest) -> HttpResponse:
            return HttpResponse("ok")

        _install_role_test_urlconf(role_required("manager")(_protected_view))

        _create_user(username=USERNAME, role_names=["doctor", "manager"])
        client.force_login(User.objects.get(username=USERNAME))
        session = client.session
        session["active_role"] = "doctor"
        session.save()

        with override_settings(ROOT_URLCONF=TEST_URLCONF):
            response = client.get("/protegida/")

        assert response.status_code == 403

    def test_role_required_allows_matching_active_role(self, client: Client) -> None:
        """Papel ativo correspondente ao exigido prossegue normalmente."""

        def _protected_view(request: HttpRequest) -> HttpResponse:
            return HttpResponse("ok")

        _install_role_test_urlconf(role_required("manager")(_protected_view))

        _create_user(username=USERNAME, role_names=["manager"])
        client.force_login(User.objects.get(username=USERNAME))
        session = client.session
        session["active_role"] = "manager"
        session.save()

        with override_settings(ROOT_URLCONF=TEST_URLCONF):
            response = client.get("/protegida/")

        assert response.status_code == 200

    def test_role_required_redirects_anonymous_to_login(self, client: Client) -> None:
        """Usuário anônimo é redirecionado ao login (R4)."""

        def _protected_view(request: HttpRequest) -> HttpResponse:
            return HttpResponse("ok")

        _install_role_test_urlconf(role_required("manager")(_protected_view))

        with override_settings(ROOT_URLCONF=TEST_URLCONF):
            response = client.get("/protegida/")

        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
class TestNavbarActiveRole:
    """R5: navbar exibe papel ativo e link de troca para multi-role."""

    def test_navbar_shows_active_role(self, client: Client) -> None:
        _create_user(username=USERNAME, role_names=["doctor", "manager"])
        client.force_login(User.objects.get(username=USERNAME))

        # Seleciona um papel e confere a navbar da home.
        response = client.post(reverse("switch_role"), {"role": "manager"})
        assert response.status_code == 302

        home = client.get(reverse("home"), follow=True)
        assert home.status_code == 200
        body = home.content.decode()
        assert "Papel ativo" in body
        assert 'title="Papel ativo">supervisor<' in body
        assert "Trocar papel" in body
        assert reverse("switch_role") in body
