"""Títulos de página do painel gerencial (ui-parity-foundation, slice 003, R2).

Toda página listada renderiza exatamente um ``h1.page-title`` com o nome da
visão, distinto do ``<title>`` (que leva a marca). RED: ``h1.page-title``
ausente nos HTMLs antes da fundação CSS.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.cases.models import Case

_PAGE_TITLE_RE = re.compile(r'<h1[^>]*class="[^"]*page-title[^"]*"[^>]*>(.*?)</h1>', re.DOTALL)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


def _make_user(username: str, role_name: str = "manager") -> User:
    """Usuário com um papel (o middleware resolve o papel ativo)."""
    role, _ = Role.objects.get_or_create(name=role_name)
    user = User.objects.create_user(username=username, password="senha-teste")
    user.roles.add(role)
    return user


def _assert_page_title(body: str, expected: str) -> None:
    """Asserta exatamente um ``h1.page-title`` com o nome da visão."""
    titles = _PAGE_TITLE_RE.findall(body)
    assert len(titles) == 1, f"esperado 1 h1.page-title, encontrados {len(titles)}"
    text = re.sub(r"<[^>]+>", "", titles[0]).strip()
    assert expected in text, f"título {text!r} não contém {expected!r}"
    title_match = _TITLE_RE.search(body)
    assert title_match is not None
    assert text != title_match.group(1).strip()


@pytest.mark.django_db
def test_dashboard_home_page_title(client: Client) -> None:
    """Painel gerencial tem ``h1.page-title`` com o nome da visão."""
    client.force_login(_make_user("gestor-titulo-home"))
    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Painel gerencial")


@pytest.mark.django_db
def test_dashboard_case_detail_page_title(client: Client) -> None:
    """Detalhe do caso no painel tem ``h1.page-title`` com o nome da visão."""
    creator = _make_user("gestor-titulo-detalhe")
    case = Case.objects.create(created_by=creator)
    client.force_login(creator)
    response = client.get(reverse("dashboard:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Detalhes do caso")


@pytest.mark.django_db
def test_dashboard_admin_close_confirm_page_title(client: Client) -> None:
    """Confirmação de encerramento tem ``h1.page-title`` com o nome da visão."""
    creator = _make_user("gestor-titulo-encerrar")
    case = Case.objects.create(created_by=creator)
    client.force_login(creator)
    response = client.get(reverse("dashboard:admin_close_confirm", args=[case.case_id]))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Encerrar caso administrativamente")
