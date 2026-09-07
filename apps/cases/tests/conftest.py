"""Fixtures de usuários/papéis dos testes de ``apps.cases`` (change 03, slice 002).

Papéis são rows de ``apps.accounts.Role`` (nome canônico dos papéis fixos do
HMD: ``nir``, ``doctor``, ``scheduler``); a trilha registra o papel ativo como
string via ``role`` (design D5 — ``actor_role`` é parâmetro explícito das
operações auditadas). Os usuários aqui carregam o papel como reforço do
cenário da spec; as transições recebem o papel explicitamente.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from apps.accounts.models import Role, User


@pytest.fixture
def user_factory() -> Callable[[str, str], User]:
    """Cria usuário com um papel (papel criado se não existir)."""

    def _factory(username: str, role: str) -> User:
        role_obj, _ = Role.objects.get_or_create(name=role)
        user = User.objects.create_user(username=username, password="senha-teste")
        user.roles.add(role_obj)
        return user

    return _factory


@pytest.fixture
def nir_user(user_factory: Callable[[str, str], User]) -> User:
    return user_factory("nir-teste", "nir")


@pytest.fixture
def doctor_user(user_factory: Callable[[str, str], User]) -> User:
    return user_factory("doctor-teste", "doctor")
