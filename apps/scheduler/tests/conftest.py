"""Fixtures dos testes de views de ``apps.scheduler`` (scheduler-multi-unit, slice 003).

Mesmo padrão de ``apps/doctor/tests/conftest.py`` (change 07): login com papel
ativo explícito na sessão via ``client.force_login`` + ``session["active_role"]``
— as views leem o papel ativo da sessão (matriz D4: ``scheduler``/``admin``).
Usuários de papel único dispensariam o passo (o ``ActiveRoleMiddleware``
auto-define), mas as fixtures fixam o papel para cobrir todos os papéis ativos
da matriz e os cenários multi-role (composição ``scheduler+manager`` acessa a
fila sob o papel ativo ``scheduler``). O ``nir_user`` é o criador dos casos das
filas — a fila do agendador não usa ownership (é papel, não dono).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest
from django.test import Client

from apps.accounts.models import Role, User


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste."""
    return Client()


@pytest.fixture
def user_factory() -> Callable[..., User]:
    """Cria usuário com os papéis informados (papéis criados se não existirem)."""

    def _factory(username: str, role_names: Sequence[str] = ("scheduler",)) -> User:
        user = User.objects.create_user(username=username, password="senha-teste")
        for role_name in role_names:
            role, _ = Role.objects.get_or_create(name=role_name)
            user.roles.add(role)
        return user

    return _factory


@pytest.fixture
def nir_user(user_factory: Callable[..., User]) -> User:
    """NIR criador dos casos das filas (papel de criação, não de acesso)."""
    return user_factory("nir-fila-agendador", ("nir",))


@pytest.fixture
def login_user(client: Client) -> Callable[[User, str], None]:
    """Autentica o usuário no client com o papel ativo ``role`` na sessão."""

    def _login(user: User, role: str) -> None:
        client.force_login(user)
        session = client.session
        session["active_role"] = role
        session.save()

    return _login
