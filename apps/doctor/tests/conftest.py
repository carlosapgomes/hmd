"""Fixtures dos testes de ``apps.doctor`` (doctor-queue-decision, slice 002).

Login com papel ativo explícito na sessão (padrão de ``apps.accounts/tests``
e do guard de intranet): ``client.force_login`` + ``session["active_role"]``
— as views leem o papel ativo da sessão (matriz D2). Usuários de papel único
dispensariam o passo (o ``ActiveRoleMiddleware`` auto-define), mas as
fixtures fixam o papel para cobrir todos os papéis ativos da matriz e os
cenários multi-role (composição: ``doctor+manager`` acessa a fila sob o papel
ativo ``doctor``). O ``nir_user`` é o criador dos casos das filas — a fila
médica não usa ownership (é papel, não dono).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest
from django.test import Client

from apps.accounts.models import DoctorSpecialty, Role, User


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste."""
    return Client()


@pytest.fixture
def user_factory() -> Callable[..., User]:
    """Cria usuário com os papéis informados (papéis criados se não existirem)."""

    def _factory(username: str, role_names: Sequence[str] = ("doctor",)) -> User:
        user = User.objects.create_user(username=username, password="senha-teste")
        for role_name in role_names:
            role, _ = Role.objects.get_or_create(name=role_name)
            user.roles.add(role)
        return user

    return _factory


@pytest.fixture
def nir_user(user_factory: Callable[..., User]) -> User:
    """NIR criador dos casos das filas (papel de criação, não de acesso)."""
    return user_factory("nir-fila", ("nir",))


@pytest.fixture
def login_user(client: Client) -> Callable[[User, str], None]:
    """Autentica o usuário no client com o papel ativo ``role`` na sessão."""

    def _login(user: User, role: str) -> None:
        client.force_login(user)
        session = client.session
        session["active_role"] = role
        session.save()

    return _login


@pytest.fixture
def assign_specialties() -> Callable[[User, Sequence[str]], None]:
    """Atribui ao usuário os subtipos do catálogo (seed da migration 0003)."""

    def _assign(user: User, names: Sequence[str]) -> None:
        user.specialties.set(DoctorSpecialty.objects.filter(name__in=names))

    return _assign
