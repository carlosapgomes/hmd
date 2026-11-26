"""Coluna «Nome completo» e busca por nome na lista de usuários do Django admin.

Cobre R1–R5 do slice 001 (change admin-lista-usuarios-nome-completo):

- R1: ``UserAdmin.list_display`` expõe «Nome completo» logo após ``username``,
  que permanece a primeira coluna e o link de edição;
- R2: a coluna exibe nome e sobrenome;
- R3: sem nome/sobrenome, a coluna exibe o ``username``;
- R4: acionar o cabeçalho da coluna ordena as linhas pelo primeiro nome;
- R5: ``UserAdmin.search_fields`` alcança nome e sobrenome (parcial, sem
  diferenciar maiúsculas), mantendo ``username`` e ``ad_upn``.

Molde: ``test_admin_professional_council.py`` (fixtures tipadas e asserção de
estrutura) e ``test_ad_upn.py::TestAdminRegistration`` (client autenticado
como superusuário na changelist). Os CPFs usados como ``username`` nos testes
de busca não contêm o nome — só a busca por nome/sobrenome os encontra.
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.accounts.admin import UserAdmin
from apps.accounts.models import User as UserModel

User = get_user_model()

ADMIN_USERNAME = "admin-lista-nome"
ADMIN_PASSWORD = "senha-admin-2026"
LOCAL_PASSWORD = "senha-local-forte-2026"
NAME_COLUMN = "full_name"
NAME_COLUMN_LABEL = "Nome completo"
CHANGELIST_NAME = "admin:accounts_user_changelist"
CHANGE_NAME = "admin:accounts_user_change"


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste (padrão dos testes do app)."""
    return Client()


@pytest.fixture
def admin_client(client: Client) -> Client:
    """Client autenticado como superusuário (superfície do Django admin)."""
    superuser = User.objects.create_superuser(
        username=ADMIN_USERNAME,
        email="admin@example.com",
        password=ADMIN_PASSWORD,
    )
    client.force_login(superuser)
    return client


def _create_user(username: str, *, first_name: str = "", last_name: str = "") -> UserModel:
    """Usuário comum com senha local (não aparece autenticado; só na lista)."""
    return User.objects.create_user(
        username=username,
        password=LOCAL_PASSWORD,
        first_name=first_name,
        last_name=last_name,
    )


def _changelist_body(client: Client, query: str | None = None) -> str:
    """HTML da lista de usuários, opcionalmente com query string da ordenação."""
    url = reverse(CHANGELIST_NAME)
    response = client.get(f"{url}{query}" if query else url)
    assert response.status_code == 200
    return response.content.decode()


def _row(body: str, username: str) -> str:
    """Linha da changelist do usuário (do ``<tr>`` ao ``</tr>``)."""
    for row in re.findall(r"<tr[^>]*>.*?</tr>", body, flags=re.DOTALL):
        # A primeira coluna (link de edição) é renderizada como <th>; as demais, <td>.
        if re.search(rf'class="field-username"[^>]*>.*?>{re.escape(username)}<', row):
            return str(row)
    raise AssertionError(f"linha de {username!r} ausente da lista")


def _name_cell(row: str) -> str:
    """Conteúdo da célula da coluna «Nome completo» numa linha da changelist."""
    match = re.search(rf'class="field-{NAME_COLUMN}"[^>]*>(.*?)</t[dh]>', row, flags=re.DOTALL)
    assert match is not None, f"coluna {NAME_COLUMN!r} ausente da linha"
    return str(match.group(1))


def _sort_link(body: str) -> str:
    """Query string de ordenação do cabeçalho «Nome completo» (link clicável)."""
    match = re.search(rf'<a href="(?P<href>\?o=[^"]+)"[^>]*>{NAME_COLUMN_LABEL}</a>', body)
    assert match is not None, "cabeçalho «Nome completo» não é um link de ordenação"
    return str(match.group("href"))


class TestUserAdminListStructure:
    """R1/R5: estrutura do ``UserAdmin`` (sem banco)."""

    def test_username_first_and_full_name_second(self) -> None:
        """R1: ``username`` é a primeira coluna; «Nome completo» vem logo depois."""
        assert UserAdmin.list_display[0] == "username"
        assert UserAdmin.list_display[1] == NAME_COLUMN

    def test_full_name_is_a_callable_column(self) -> None:
        """R1: a coluna é um método do admin (não um campo bruto do modelo)."""
        assert callable(getattr(UserAdmin, NAME_COLUMN))

    def test_search_fields_cover_names(self) -> None:
        """R5: busca alcança username, ad_upn, nome e sobrenome."""
        assert set(UserAdmin.search_fields) >= {"username", "ad_upn", "first_name", "last_name"}


@pytest.mark.django_db
class TestUserListNameColumn:
    """R1–R4: coluna «Nome completo» na changelist."""

    def test_header_is_sortable(self, admin_client: Client) -> None:
        """R4: o cabeçalho «Nome completo» é um link de ordenação."""
        assert _sort_link(_changelist_body(admin_client)).startswith("?o=")

    def test_shows_full_name_and_username_is_the_edit_link(self, admin_client: Client) -> None:
        """R1/R2: nome e sobrenome na coluna; ``username`` continua sendo o link."""
        user = _create_user("maria.ad", first_name="Maria", last_name="Silva")
        row = _row(_changelist_body(admin_client), "maria.ad")
        assert _name_cell(row) == "Maria Silva"
        change_url = reverse(CHANGE_NAME, args=[user.pk])
        assert f'<a href="{change_url}">maria.ad</a>' in row

    def test_falls_back_to_username_without_name(self, admin_client: Client) -> None:
        """R3: sem nome/sobrenome, a coluna mostra o ``username``."""
        _create_user("maria.ad", first_name="Maria", last_name="Silva")
        _create_user("sem.nome")
        row = _row(_changelist_body(admin_client), "sem.nome")
        assert _name_cell(row) == "sem.nome"

    def test_sorts_by_first_name(self, admin_client: Client) -> None:
        """R4: acionar o cabeçalho ordena as linhas pelo primeiro nome."""
        # Criados nesta ordem: a ordenação padrão (mais recente primeiro) põe
        # Maria antes de Carlos — a ordenação pela coluna inverte.
        _create_user("zzz.carlos", first_name="Carlos", last_name="Almeida")
        _create_user("aaa.maria", first_name="Maria", last_name="Barbosa")
        body = _changelist_body(admin_client)
        assert body.index("Maria Barbosa") < body.index("Carlos Almeida")
        sorted_body = _changelist_body(admin_client, _sort_link(body))
        assert sorted_body.index("Carlos Almeida") < sorted_body.index("Maria Barbosa")


@pytest.mark.django_db
class TestUserListSearch:
    """R5: busca por nome e sobrenome."""

    def test_search_by_last_name_fragment(self, admin_client: Client) -> None:
        """R5: «sil» encontra o usuário pelo sobrenome."""
        _create_user("222.333.444-55", first_name="Maria", last_name="Silva")
        assert "Maria Silva" in _changelist_body(admin_client, "?q=sil")

    def test_search_by_first_name_excludes_non_matching(self, admin_client: Client) -> None:
        """R5: «maria» encontra só quem tem o nome; sobrenome sem relação sai."""
        _create_user("222.333.444-55", first_name="Maria", last_name="Silva")
        _create_user("333.444.555-66", first_name="Carlos", last_name="Souza")
        body = _changelist_body(admin_client, "?q=maria")
        assert "Maria Silva" in body
        assert "Carlos Souza" not in body
