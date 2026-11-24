"""Bloqueio das rotas de troca de senha do Django admin (slice 002, R1–R6).

Cobre o contrato «Sem rotas de troca de senha no aplicativo» para a
superfície herdada do Django admin:

- R1: ``admin:password_change``/``admin:password_change_done`` não existem —
  404 (GET e POST), POST não altera a senha do superusuário e ``reverse``
  levanta ``NoReverseMatch``;
- R2: ``admin:auth_user_password_change`` não existe — GET e POST na rota
  por usuário caem no padrão de compatibilidade do ``ModelAdmin`` (302
  para a página de edição, que redireciona 302 ao índice do admin com o
  aviso de objeto inexistente): com ``follow=True`` o status final é
  200 no índice (``/admin/``) e nenhuma senha é gravada (usuário AD
  segue sem senha utilizável; usuário local mantém ``check_password``
  da senha original);
- R3: páginas do admin (índice, changelist, edição e criação de usuário)
  renderizam 200 para o superusuário local (que tem senha utilizável),
  sem ``password_change`` no HTML e sem ``NoReverseMatch``;
- R4: o formulário de edição não exibe o botão ``../password/`` nem o
  texto de ajuda padrão, exibe o resumo do hash e o texto exato da TI;
- R5 (anti-drift): ``templates/admin/base.html`` do projeto é o original
  do Django 5.2.17 menos exatamente o bloco de três linhas do link;
- R6 (varredura): nenhum padrão do resolver contém ``password`` em
  caminho ou nome.
"""

from pathlib import Path
from typing import Any

import django
import pytest
from django.http import HttpRequest, HttpResponse
from django.test import Client
from django.urls import NoReverseMatch, URLPattern, URLResolver, get_resolver, reverse
from django.urls.resolvers import RoutePattern

from apps.accounts.models import User

ADMIN_USERNAME = "admin-seed"
ADMIN_PASSWORD = "senha-admin-2026"
NEW_PASSWORD = "senha-nova-2026"
LOCAL_PASSWORD = "senha-local-forte-2026"
AD_UPN = "12345678901@dominio-teste.local"

HELP_TEXT = "Gerida fora do aplicativo — a troca de senha é feita pessoalmente na TI do hospital."

BLOCO_LINK_TROCA_SENHA = (
    "                {% if user.has_usable_password %}\n"
    "                <a href=\"{% url 'admin:password_change' %}\">"
    "{% translate 'Change password' %}</a> /\n"
    "                {% endif %}\n"
)


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste (padrão dos testes do app)."""
    return Client()


@pytest.fixture
def admin_user() -> User:
    """Superusuário local (sem ``ad_upn``) — tem senha utilizável."""
    return User.objects.create_superuser(
        username=ADMIN_USERNAME,
        email="admin@example.com",
        password=ADMIN_PASSWORD,
    )


@pytest.fixture
def admin_client(client: Client, admin_user: User) -> Client:
    """Client autenticado como superusuário (superfície do Django admin)."""
    client.force_login(admin_user)
    return client


def _walk_patterns(
    patterns: list[URLPattern | URLResolver], prefix: str = ""
) -> list[tuple[str, str | None]]:
    """Achata recursivamente os padrões do resolver (caminhos + nomes).

    O caminho de cada folha acumula os prefixos dos ``URLResolver``
    ancestrais, para que um prefixo contendo ``password`` (ex.: uma
    inclusão ``path("password/", include(...))``) não passe despercebido.
    """
    found: list[tuple[str, str | None]] = []
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            found.extend(_walk_patterns(pattern.url_patterns, prefix + str(pattern.pattern)))
        else:
            found.append((prefix + str(pattern.pattern), pattern.name))
    return found


@pytest.mark.django_db
class TestAdminSelfServicePasswordRoutesRemoved:
    """R1: rotas de autoatendimento do admin não existem."""

    def test_password_change_get_returns_404(self, admin_client: Client) -> None:
        assert admin_client.get("/admin/password_change/").status_code == 404

    def test_password_change_done_get_returns_404(self, admin_client: Client) -> None:
        assert admin_client.get("/admin/password_change/done/").status_code == 404

    def test_password_change_post_returns_404_and_does_not_change_password(
        self, admin_client: Client, admin_user: User
    ) -> None:
        response = admin_client.post(
            "/admin/password_change/",
            {
                "old_password": ADMIN_PASSWORD,
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
        )
        assert response.status_code == 404
        admin_user.refresh_from_db()
        assert admin_user.check_password(ADMIN_PASSWORD)
        assert not admin_user.check_password(NEW_PASSWORD)

    def test_password_change_done_post_returns_404(self, admin_client: Client) -> None:
        assert admin_client.post("/admin/password_change/done/").status_code == 404

    def test_reverse_password_change_raises_no_reverse_match(self) -> None:
        with pytest.raises(NoReverseMatch):
            reverse("admin:password_change")

    def test_reverse_password_change_done_raises_no_reverse_match(self) -> None:
        with pytest.raises(NoReverseMatch):
            reverse("admin:password_change_done")


@pytest.mark.django_db
class TestAdminUserPasswordRouteRemoved:
    """R2: rota de senha por usuário no admin não existe; nada é gravado."""

    def _user_password_url(self, user: User) -> str:
        return f"/admin/accounts/user/{user.pk}/password/"

    def _assert_redirects_to_admin_index(self, response: Any, user: User) -> None:
        # Comportamento nativo do Django (decisão do dono, D3): o padrão de
        # compatibilidade <path:object_id>/ do ModelAdmin responde 302 para
        # a página de edição (object_id="<id>/password", inexistente), que
        # redireciona 302 ao índice do admin com o aviso de objeto
        # inexistente — sem página de senha e sem escrita em nenhum passo.
        assert response.status_code == 200
        assert response.redirect_chain == [
            (f"/admin/accounts/user/{user.pk}/password/change/", 302),
            ("/admin/", 302),
        ]
        assert response.redirect_chain[-1][0] == "/admin/"
        body = response.content.decode()
        assert "password1" not in body
        assert "new_password" not in body

    def test_user_password_get_redirects_to_admin_index(self, admin_client: Client) -> None:
        user = User.objects.create_user(username="maria.local", password=LOCAL_PASSWORD)
        response = admin_client.get(self._user_password_url(user), follow=True)
        self._assert_redirects_to_admin_index(response, user)

    def test_user_password_post_redirects_to_admin_index_and_local_password_unchanged(
        self, admin_client: Client
    ) -> None:
        user = User.objects.create_user(username="maria.local", password=LOCAL_PASSWORD)
        response = admin_client.post(
            self._user_password_url(user),
            {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
            follow=True,
        )
        self._assert_redirects_to_admin_index(response, user)
        user.refresh_from_db()
        assert user.check_password(LOCAL_PASSWORD)
        assert not user.check_password(NEW_PASSWORD)

    def test_user_password_post_redirects_to_admin_index_and_ad_user_stays_without_usable_password(
        self, admin_client: Client
    ) -> None:
        user = User.objects.create_user(username="maria.ad", password=LOCAL_PASSWORD, ad_upn=AD_UPN)
        user.set_unusable_password()
        user.save(update_fields=["password"])
        assert user.has_usable_password() is False

        response = admin_client.post(
            self._user_password_url(user),
            {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
            follow=True,
        )
        self._assert_redirects_to_admin_index(response, user)
        user.refresh_from_db()
        assert user.has_usable_password() is False
        assert not user.check_password(NEW_PASSWORD)

    def test_reverse_auth_user_password_change_raises_no_reverse_match(
        self, admin_user: User
    ) -> None:
        with pytest.raises(NoReverseMatch):
            reverse("admin:auth_user_password_change", args=[admin_user.pk])


@pytest.mark.django_db
class TestAdminPagesRenderWithoutPasswordLink:
    """R3: páginas do admin renderizam sem ``password_change`` no HTML."""

    def _assert_ok_without_password_change(self, response: Any, body: str) -> None:
        assert response.status_code == 200
        assert "password_change" not in body

    def test_admin_index_renders(self, admin_client: Client) -> None:
        response = admin_client.get(reverse("admin:index"))
        self._assert_ok_without_password_change(response, response.content.decode())

    def test_admin_user_changelist_renders(self, admin_client: Client) -> None:
        response = admin_client.get(reverse("admin:accounts_user_changelist"))
        self._assert_ok_without_password_change(response, response.content.decode())

    def test_admin_user_change_form_renders(self, admin_client: Client, admin_user: User) -> None:
        response = admin_client.get(reverse("admin:accounts_user_change", args=[admin_user.pk]))
        self._assert_ok_without_password_change(response, response.content.decode())

    def test_admin_user_add_form_renders(self, admin_client: Client) -> None:
        response = admin_client.get(reverse("admin:accounts_user_add"))
        self._assert_ok_without_password_change(response, response.content.decode())


@pytest.mark.django_db
class TestUserChangeFormWithoutPasswordButton:
    """R4: formulário de edição sem botão, com texto neutro e resumo do hash."""

    def test_change_form_has_no_password_button_and_shows_neutral_help_text(
        self, admin_client: Client, admin_user: User
    ) -> None:
        response = admin_client.get(reverse("admin:accounts_user_change", args=[admin_user.pk]))
        assert response.status_code == 200
        body = response.content.decode()
        assert "../password/" not in body
        assert "Raw passwords are not stored" not in body
        assert "Enable password-based authentication for this user" not in body
        assert HELP_TEXT in body
        # Resumo do hash preservado (algoritmo do hasher da suíte: MD5).
        assert "md5" in body

    def test_change_form_unusable_password_shows_neutral_help_text(
        self, admin_client: Client
    ) -> None:
        user = User.objects.create_user(username="maria.ad", password=LOCAL_PASSWORD, ad_upn=AD_UPN)
        user.set_unusable_password()
        user.save(update_fields=["password"])
        assert user.has_usable_password() is False
        response = admin_client.get(reverse("admin:accounts_user_change", args=[user.pk]))
        assert response.status_code == 200
        body = response.content.decode()
        assert HELP_TEXT in body
        assert (
            "Enable password-based authentication for this user by setting a password" not in body
        )
        assert "Habilite a autenticação baseada em senha" not in body
        assert "../password/" not in body


@pytest.mark.django_db
class TestAdminBaseTemplateAntiDrift:
    """R5: override é o original do Django menos o bloco do link."""

    def test_project_base_template_matches_django_original_minus_link_block(self) -> None:
        original_path = Path(django.__file__).parent / "contrib/admin/templates/admin/base.html"
        project_path = Path(__file__).resolve().parents[3] / "templates/admin/base.html"
        original = original_path.read_text(encoding="utf-8")
        project = project_path.read_text(encoding="utf-8")
        assert BLOCO_LINK_TROCA_SENHA in original
        assert original.replace(BLOCO_LINK_TROCA_SENHA, "") == project


def _dummy_view(request: HttpRequest) -> HttpResponse:
    return HttpResponse()


class TestResolverWalkAccumulatesPrefixes:
    """R6 (guarda): o helper acumula prefixos de ``URLResolver``."""

    def test_walk_includes_resolver_prefix_containing_password(self) -> None:
        leaf = URLPattern(RoutePattern("acao/"), _dummy_view, name="acao")
        inner = URLResolver(RoutePattern("sub/"), [leaf])
        outer = URLResolver(RoutePattern("password/"), [inner])
        found = _walk_patterns([outer])
        assert found == [("password/sub/acao/", "acao")]
        assert any("password" in path for path, _name in found)


class TestResolverHasNoPasswordPattern:
    """R6: varredura recursiva — nenhum caminho ou nome contém ``password``."""

    def test_no_resolver_pattern_mentions_password(self) -> None:
        found = _walk_patterns(get_resolver().url_patterns)
        offenders = [
            (path, name) for path, name in found if "password" in path or "password" in (name or "")
        ]
        assert offenders == []
