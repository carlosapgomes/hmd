"""Testes da referência humana do caso (change case-detail-human-reference, slice 001, R1–R4).

Fonte única da regra de apresentação do caso nas superfícies visíveis (design
D1): nº de ocorrência quando presente, fallback ``uid <8 chars>`` (padrão
vigente do painel) e desambiguação ``nº · <8 chars>`` para os pontos onde a
duplicidade de nº é estrutural (original × reenvio corrigido — D3).
``short_id`` é o prefixo de 8 caracteres do ``case_id`` (UUID) usado pelos
dois formatos.
"""

from __future__ import annotations

import uuid

import pytest
from pytest_django.fixtures import DjangoAssertNumQueries

from apps.cases.models import Case

pytestmark = pytest.mark.django_db

# Uid fixo dos cenários: os formatos pinam os 8 primeiros caracteres.
CASE_ID = uuid.UUID("a1b2c3d4-1111-2222-3333-444455556666")
SHORT_ID = "a1b2c3d4"


def _case(agency_record_number: str = "") -> Case:
    """Caso em memória — as properties são puras (R4), não tocam o banco."""
    return Case(case_id=CASE_ID, agency_record_number=agency_record_number)


def test_short_id_is_first_eight_characters_of_uid() -> None:
    assert _case().short_id == SHORT_ID


def test_display_reference_uses_agency_record_number_when_present() -> None:
    assert _case("2024-1234").display_reference == "2024-1234"


def test_display_reference_strips_surrounding_whitespace() -> None:
    assert _case("  2024-1234  ").display_reference == "2024-1234"


@pytest.mark.parametrize("agency_record_number", ["", "   "])
def test_display_reference_falls_back_to_uid_when_absent(agency_record_number: str) -> None:
    assert _case(agency_record_number).display_reference == f"uid {SHORT_ID}"


def test_disambiguated_reference_appends_short_id_to_number() -> None:
    assert _case("  2024-1234 ").disambiguated_reference == f"2024-1234 · {SHORT_ID}"


@pytest.mark.parametrize("agency_record_number", ["", "   "])
def test_disambiguated_reference_falls_back_to_uid_when_absent(
    agency_record_number: str,
) -> None:
    assert _case(agency_record_number).disambiguated_reference == f"uid {SHORT_ID}"


def test_references_are_pure_and_do_not_query_database(
    django_assert_num_queries: DjangoAssertNumQueries,
) -> None:
    case = _case("2024-1234")

    with django_assert_num_queries(0):
        assert case.short_id == SHORT_ID
        assert case.display_reference == "2024-1234"
        assert case.disambiguated_reference == f"2024-1234 · {SHORT_ID}"
