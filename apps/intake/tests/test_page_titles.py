"""Títulos de página do intake NIR (ui-parity-foundation, slice 003, R2).

Toda página listada renderiza exatamente um ``h1.page-title`` com o nome da
visão, distinto do ``<title>`` (que leva a marca). RED: ``h1.page-title``
ausente nos HTMLs antes da fundação CSS.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseStatus

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
_PAGE_TITLE_RE = re.compile(r'<h1[^>]*class="[^"]*page-title[^"]*"[^>]*>(.*?)</h1>', re.DOTALL)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


@pytest.fixture(autouse=True)
def _no_inline_processing() -> Iterator[None]:
    """Criação sem processamento automático (padrão dos testes do intake)."""
    with override_settings(
        INTAKE_RUN_TASKS_INLINE=False,
        ANONYMIZATION_RUN_TASKS_INLINE=False,
    ):
        yield


def _assert_page_title(body: str, expected: str) -> None:
    """Asserta exatamente um ``h1.page-title`` com o nome da visão."""
    titles = _PAGE_TITLE_RE.findall(body)
    assert len(titles) == 1, f"esperado 1 h1.page-title, encontrados {len(titles)}"
    text = re.sub(r"<[^>]+>", "", titles[0]).strip()
    assert expected in text, f"título {text!r} não contém {expected!r}"
    title_match = _TITLE_RE.search(body)
    assert title_match is not None
    assert text != title_match.group(1).strip()


def _drive_to_cleaned(case: Case, *, nir: User, doctor: User) -> None:
    """Dirige o caso pelas operações FSM até ``CLEANED`` (setup do reenvio)."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    case.record_doctor_decision(accepted=False, user=doctor, role=DOCTOR_ROLE)
    case.post_final_reply(user=doctor, role=DOCTOR_ROLE)
    case.nir_acknowledge(user=nir, role=NIR_ROLE)
    case.start_cleaning(user=nir, role=NIR_ROLE)
    case.complete_cleaning(user=nir, role=NIR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED


@pytest.mark.django_db
def test_intake_home_page_title(client: Client, nir_user: User) -> None:
    """Envio de relatório tem ``h1.page-title`` com o nome da visão."""
    client.force_login(nir_user)
    response = client.get(reverse("intake:home"))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Envio de relatório de regulação")


@pytest.mark.django_db
def test_intake_my_cases_page_title(client: Client, nir_user: User) -> None:
    """Meus casos tem ``h1.page-title`` com o nome da visão (OLI-004 R1)."""
    client.force_login(nir_user)
    response = client.get(reverse("intake:my_cases"))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Todos os seus encaminhamentos")


@pytest.mark.django_db
def test_intake_case_detail_page_title(client: Client, nir_user: User) -> None:
    """Detalhe do caso do NIR tem ``h1.page-title`` com o nome da visão."""
    case = Case.objects.create(created_by=nir_user)
    case.agency_record_number = "81001"
    case.save(update_fields=["agency_record_number"])
    case.refresh_from_db()
    client.force_login(nir_user)
    response = client.get(reverse("intake:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Caso")


@pytest.mark.django_db
def test_intake_corrected_resubmission_page_title(
    client: Client, nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    """Reenvio corrigido tem ``h1.page-title`` com o nome da visão."""
    doctor = user_factory("medico-titulo-reenvio", "doctor")
    nir = nir_user
    case = Case.objects.create(created_by=nir)
    _drive_to_cleaned(case, nir=nir, doctor=doctor)
    client.force_login(nir)
    response = client.get(reverse("intake:case_resubmit", args=[case.case_id]))
    assert response.status_code == 200
    _assert_page_title(response.content.decode(), "Reenviar corrigido")
