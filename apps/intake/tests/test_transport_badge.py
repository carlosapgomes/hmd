"""Badge de transporte nos cards de «meus casos» (transport-badge, slice 002, R2/R5).

R2: card com ``mobile_icu`` exibe «UTI Móvel» em ênfase; card com ``basic``
exibe «Ambulância Básica» neutra; card sem transporte não exibe badge e a
linha de metadados segue íntegra. R5 (paridade): rótulo canônico do
``TransportType`` e classe do mapeamento único ``TRANSPORT_BADGE_CLASSES`` —
o mesmo HTML exato afirmado nas filas médica e do agendador.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, TransportType
from apps.intake.services import create_case_with_documents

NIR_ROLE = "nir"


@pytest.fixture(autouse=True)
def _creation_without_processing() -> Iterator[None]:
    """Criação sem processamento: estados controlados pelo teste."""

    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


ICU_BADGE = '<span class="badge rounded-pill text-bg-warning">🚑 UTI Móvel</span>'
BASIC_BADGE = '<span class="badge rounded-pill text-bg-info">🚑 Ambulância Básica</span>'


def _create_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    *,
    record_number: str = "5040778",
    patient_name: str = "Paciente Transporte",
    transport_type: str | None = None,
) -> Case:
    """Cria um caso NEW com 1 PDF e 1 tipo declarado, ajustando exibição."""
    case = create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(),
        procedure_type="art_perif",
    )
    Case.objects.filter(pk=case.pk).update(
        agency_record_number=record_number,
        patient_name=patient_name,
        transport_type=transport_type,
    )
    case.refresh_from_db()
    return case


def _card_slice(body: str, case_id: object) -> str:
    """Fatia o HTML do card do caso pelo marcador ``data-my-cases-card``."""
    chunks = body.split("data-my-cases-card")
    for chunk in chunks:
        if str(case_id) in chunk:
            return chunk
    raise AssertionError(f"card do caso {case_id} ausente no corpo")


@pytest.mark.django_db
def test_my_cases_card_with_mobile_icu_shows_emphasis_badge(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/R5: ``mobile_icu`` → badge «UTI Móvel» em ênfase (``text-bg-warning``)."""
    case = _create_case(nir_user, pdf_factory, transport_type=TransportType.MOBILE_ICU.value)

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    card = _card_slice(body, case.case_id)
    assert ICU_BADGE in card
    assert BASIC_BADGE not in card


@pytest.mark.django_db
def test_my_cases_card_with_basic_shows_neutral_badge(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/R5: ``basic`` → badge «Ambulância Básica» neutro (``text-bg-info``)."""
    case = _create_case(nir_user, pdf_factory, transport_type=TransportType.BASIC.value)

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    card = _card_slice(body, case.case_id)
    assert BASIC_BADGE in card
    assert ICU_BADGE not in card


@pytest.mark.django_db
def test_my_cases_card_without_transport_shows_no_badge(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: sem transporte → nenhum badge; a linha de metadados segue íntegra."""
    case = _create_case(nir_user, pdf_factory, transport_type=None)

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    card = _card_slice(body, case.case_id)
    assert "🚑" not in card
    assert "UTI Móvel" not in card
    assert "Ambulância Básica" not in card
    assert "Nº de ocorrência:" in card
    assert "5040778" in card
