"""Transporte no card de identificação do detalhe médico (transport-badge, slice 003).

R1: linha rotulada «Transporte» no card de identificação do detalhe, junto da
demografia, com o valor canônico via ``get_transport_type_display()``.
R2: transporte ausente exibe «—», como os demais campos vazios do card, sem erro.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseProcedure, CaseStatus

DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"

ANGIO_TYPE = "art_perif"


def _login(client: Client, user: User, role: str) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _make_awaiting_case(created_by: User, *, transport_type: str | None) -> Case:
    """Caso em AWAITING_DOCTOR com o transporte informado (ou ausente)."""
    case = Case.objects.create(created_by=created_by)
    CaseProcedure.objects.create(
        case=case,
        procedure_type=ANGIO_TYPE,
        declared_by_nir=True,
    )
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    Case.objects.filter(pk=case.pk).update(transport_type=transport_type)
    case.refresh_from_db()
    return case


_IDENTIFICATION_CARD_START = "<!-- Identificação do paciente -->"
_IDENTIFICATION_CARD_END = "<!-- Procedimentos do caso -->"


def _identification_card(body: str) -> str:
    """Recorte do card de identificação (asserts escopados por linha)."""
    _, card = body.split(_IDENTIFICATION_CARD_START, 1)
    card, _ = card.split(_IDENTIFICATION_CARD_END, 1)
    return card


def _dd_value(card: str, label: str) -> str:
    """Valor do ``<dd>`` que segue o ``<dt>`` do rótulo DENTRO do card."""
    match = re.search(
        rf'<dt class="col-sm-3">{re.escape(label)}</dt>\s*<dd class="col-sm-9">(.*?)</dd>',
        card,
        flags=re.DOTALL,
    )
    assert match is not None, f"linha «{label}» ausente no card de identificação"
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _doctor_client(client: Client, user_factory: Callable[..., User], username: str) -> None:
    """Autentica um médico generalista (acesso total pela matriz D2)."""
    doctor = user_factory(username, (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)


@pytest.mark.django_db
def test_detail_identification_shows_transport_label(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: detalhe com ``transport_type=mobile_icu`` exibe «UTI Móvel» no card."""
    case = _make_awaiting_case(nir_user, transport_type="mobile_icu")
    _doctor_client(client, user_factory, "medico-transporte")

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    card = _identification_card(response.content.decode())
    assert _dd_value(card, "Transporte") == "UTI Móvel"


@pytest.mark.django_db
def test_detail_identification_shows_placeholder_when_transport_absent(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: sem transporte o card exibe «—», como os demais campos vazios."""
    case = _make_awaiting_case(nir_user, transport_type=None)
    _doctor_client(client, user_factory, "medico-transporte-vazio")

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    card = _identification_card(response.content.decode())
    assert _dd_value(card, "Transporte") == "—"


@pytest.mark.django_db
def test_detail_identification_shows_basic_transport_label(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: detalhe com ``transport_type=basic`` exibe «Ambulância Básica»."""
    case = _make_awaiting_case(nir_user, transport_type="basic")
    _doctor_client(client, user_factory, "medico-transporte-basic")

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    card = _identification_card(response.content.decode())
    assert _dd_value(card, "Transporte") == "Ambulância Básica"
