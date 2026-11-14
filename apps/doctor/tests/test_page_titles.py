"""Títulos de página da fila/detalhe/decisão médica (slice 003, R2).

Toda página listada renderiza exatamente um ``h1.page-title`` com o nome da
visão, distinto do ``<title>`` (que leva a marca). RED: ``h1.page-title``
ausente nos HTMLs antes da fundação CSS.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseProcedure, CaseStatus

SYSTEM_ROLE = "system"
_PAGE_TITLE_RE = re.compile(r'<h1[^>]*class="[^"]*page-title[^"]*"[^>]*>(.*?)</h1>', re.DOTALL)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


def _awaiting_doctor_case(created_by: User) -> Case:
    """Caso em ``AWAITING_DOCTOR`` com 1 tipo declarado (setup das filas)."""
    case = Case.objects.create(created_by=created_by)
    CaseProcedure.objects.create(case=case, procedure_type="art_perif", declared_by_nir=True)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    case.agency_record_number = "90001"
    case.save(update_fields=["agency_record_number"])
    case.refresh_from_db()
    return case


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
def test_doctor_queue_page_title(
    client: Client,
    user_factory: Callable[..., User],
    nir_user: User,
    login_user: Callable[[User, str], None],
) -> None:
    """Fila médica tem ``h1.page-title`` com o nome da visão."""
    doctor = user_factory("medico-titulo-fila", ("doctor",))
    _awaiting_doctor_case(nir_user)
    login_user(doctor, "doctor")
    response = client.get(reverse("doctor:queue"))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Fila médica")


@pytest.mark.django_db
def test_doctor_case_detail_page_title(
    client: Client,
    user_factory: Callable[..., User],
    nir_user: User,
    login_user: Callable[[User, str], None],
) -> None:
    """Detalhe médico tem ``h1.page-title`` com o nome da visão."""
    doctor = user_factory("medico-titulo-detalhe", ("doctor",))
    case = _awaiting_doctor_case(nir_user)
    login_user(doctor, "doctor")
    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Caso")


@pytest.mark.django_db
def test_doctor_decide_page_title(
    client: Client,
    user_factory: Callable[..., User],
    nir_user: User,
    login_user: Callable[[User, str], None],
) -> None:
    """Página de decisão tem ``h1.page-title`` com o nome da visão."""
    doctor = user_factory("medico-titulo-decidir", ("doctor",))
    case = _awaiting_doctor_case(nir_user)
    login_user(doctor, "doctor")
    response = client.get(reverse("doctor:case_decide", args=[case.case_id]))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "decisão médica")
