"""Testes de ``apps.anonymization.deterministic`` (slice 001, change
presidio-anonymization; design D2 / requirement "Pré-extração determinística").

Cobre a matriz R1–R7 do slice: funções puras de pré-extração sobre o texto do
relatório SESAB — nº de ocorrência (reuso do padrão do intake), nome do
paciente, data de nascimento e candidatos CPF/CNS por regex (sem checksum —
isso é do slice 002). Nenhum teste toca banco/modelo/IO.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

import apps.anonymization.deterministic as deterministic
from apps.anonymization.deterministic import (
    DeterministicExtraction,
    extract_birth_date,
    extract_cns_candidates,
    extract_cpf_candidates,
    extract_patient_name,
    run_deterministic_extraction,
)
from apps.intake.pdf_utils import extract_agency_record_number

# Relatório sintético cobrindo todos os campos da composição (R6/R7).
_SYNTHETIC_REPORT = (
    "RELATÓRIO DE OCORRÊNCIAS\n"
    "Código: 33345\n"
    "Abertura: 01/02/2025\n"
    "Unid. Origem: Hospital Geral do Estado\n"
    "Paciente: MARIA DA SILVA SOUZA\n"
    "Data de Nascimento: 12/03/1960\n"
    "CPF: 123.456.789-09\n"
    "CNS: 123456789012345\n"
    "Motivo da Solicitação: cateterismo cardíaco diagnóstico\n"
    "Complemento da Solicitação: paciente encaminhado para avaliação\n"
    "Resumo Clínico: dor torácica em investigação\n"
    "Dias em tela: 3\n"
    "Data Adm. Unid.: 01/02/2025\n"
)


# ── R1/R6: composição completa ────────────────────────────────────────────


def test_full_extraction_synthetic_report() -> None:
    """R1/R6: composição de todos os extratores num relatório sintético."""
    result = run_deterministic_extraction(_SYNTHETIC_REPORT)

    assert result == DeterministicExtraction(
        record_number="33345",
        patient_name="MARIA DA SILVA SOUZA",
        birth_date=date(1960, 3, 12),
        cpf_candidates=("12345678909",),
        cns_candidates=("123456789012345",),
    )
    # R1: dataclass congelada — nenhum campo é mutável após a criação.
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(result, "record_number", "99999")


def test_full_extraction_without_fields() -> None:
    """R6: texto sem nenhum campo identificável → extração vazia."""
    text = "Laudo avulso sem rótulos do relatório SESAB."

    result = run_deterministic_extraction(text)

    assert result == DeterministicExtraction()


# ── R2: nº de ocorrência delegado ao intake ───────────────────────────────


def test_record_number_delegates_to_intake(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2: delega ao padrão do ``pdf_utils`` (import real, sem duplicação)."""
    text = "RELATÓRIO DE OCORRÊNCIAS\nCódigo: 33345\nPaciente: João Maria"
    received: list[str] = []

    def _spy(raw: str) -> str | None:
        received.append(raw)
        return extract_agency_record_number(raw)

    monkeypatch.setattr(deterministic, "extract_agency_record_number", _spy)

    assert deterministic.extract_record_number(text) == "33345"
    assert received == [text]
    # Paridade com o padrão original do intake (mesmo comportamento).
    assert deterministic.extract_record_number("CODIGO : 54321") == "54321"
    assert extract_agency_record_number("codigo: 54321") == "54321"
    assert deterministic.extract_record_number("Sem padrões do relatório") is None


# ── R3: nome do paciente ──────────────────────────────────────────────────


def test_patient_name_patterns() -> None:
    """R3: rótulos "Paciente:"/"Nome do Paciente:"/"Nome:" (case tolerante)."""
    assert (
        extract_patient_name("Paciente: MARIA DA SILVA\nData de Nascimento: 12/03/1960")
        == "MARIA DA SILVA"
    )
    assert (
        extract_patient_name("nome do paciente: João Maria de Souza\nNascimento: 10/05/1944")
        == "João Maria de Souza"
    )
    assert extract_patient_name("NOME : ANA PEREIRA\nMotivo da Solicitação: cateterismo") == (
        "ANA PEREIRA"
    )


def test_patient_name_value_until_next_field() -> None:
    """R3: valor vai até a quebra do campo seguinte (nome quebrado em linhas)."""
    assert (
        extract_patient_name(
            "Paciente:\nMARIA DA SILVA\nSOUZA SANTOS\nData de Nascimento: 12/03/1960"
        )
        == "MARIA DA SILVA SOUZA SANTOS"
    )


def test_patient_name_absent() -> None:
    """R3: nenhum rótulo de nome → None (prosa com a palavra não é campo)."""
    text = (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "Código: 33345\n"
        "Motivo da Solicitação: cateterismo cardíaco\n"
        "Resumo Clínico: o nome do paciente não consta como campo no documento"
    )
    assert extract_patient_name(text) is None


# ── R4: data de nascimento ────────────────────────────────────────────────


def test_birth_date_valid_and_invalid() -> None:
    """R4: dd/mm/aaaa e dd-mm-aaaa; datas inexistentes (32/13) rejeitadas."""
    assert extract_birth_date("Data de Nascimento: 12/03/1960") == date(1960, 3, 12)
    assert extract_birth_date("data de nascimento : 01/01/2000") == date(2000, 1, 1)
    assert extract_birth_date("Nascimento: 15-08-1955\nMotivo da Solicitação: cateterismo") == (
        date(1955, 8, 15)
    )
    assert extract_birth_date("NASCIMENTO: 02/02/2024") == date(2024, 2, 2)
    assert extract_birth_date("Nascimento: 32/13/1960") is None
    assert extract_birth_date("Nascimento: 29/02/2023") is None  # ano não bissexto
    assert extract_birth_date("Nascimento: 31/04/1960") is None  # abril tem 30 dias


def test_birth_date_absent() -> None:
    """R4: sem rótulo de nascimento ou sem data no padrão → None."""
    assert (
        extract_birth_date(
            "RELATÓRIO DE OCORRÊNCIAS\n"
            "Paciente: Maria\n"
            "Abertura: 01/02/2025\n"
            "Motivo da Solicitação: cateterismo"
        )
        is None
    )
    assert extract_birth_date("Nascimento: não informado") is None
    assert extract_birth_date("Data de Nascimento: 12-03-60") is None  # ano com 2 dígitos


# ── R5: candidatos CPF/CNS (regex, sem checksum) ──────────────────────────


def test_cpf_canditates_with_without_punctuation() -> None:
    """R5: candidatos CPF de 11 dígitos, com/sem pontuação, normalizados e dedup."""
    text = (
        "CPF com pontuação: 123.456.789-09\n"
        "CPF sem pontuação repetido: 12345678909\n"
        "Outro CPF: 987.654.321-00\n"
        "CPF com espaços: 111 222 333 44\n"
        "Telefone: (71) 3333-4455 não é CPF\n"
    )

    assert extract_cpf_candidates(text) == ("12345678909", "98765432100", "11122233344")
    assert extract_cpf_candidates("Sem documento numérico") == ()


def test_cns_candidates() -> None:
    """R5: candidatos CNS de 15 dígitos, com/sem separadores; CPF não vira CNS."""
    text = (
        "Primeiro CNS: 123456789012345\n"
        "CNS repetido com espaços: 123 456 789 012 345\n"
        "Outro CNS: 210345678901234\n"
        "CPF isolado não é CNS: 11122233344\n"
    )

    assert extract_cns_candidates(text) == ("123456789012345", "210345678901234")
    assert extract_cns_candidates("Sem documento numérico") == ()


def test_spaced_cns_digits_do_not_leak_as_cpf() -> None:
    """R5 adversário: CNS de 15 dígitos espaçados dígito a dígito.

    O run completo normaliza para 15 dígitos → não vira candidato CPF parcial
    (11 primeiros dígitos); segue válido como CNS.
    """
    text = "CNS: 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5"

    assert extract_cpf_candidates(text) == ()
    assert extract_cns_candidates(text) == ("123456789012345",)


def test_spaced_16_digit_sequence_yields_no_candidate() -> None:
    """R5 adversário: sequência espaçada de 16 dígitos → nenhum candidato.

    Nem CPF parcial (11) nem CNS parcial (15): o run normaliza para 16 dígitos,
    fora dos tamanhos exatos aceitos por qualquer um dos extratores.
    """
    text = "Número longo: 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6"

    assert extract_cpf_candidates(text) == ()
    assert extract_cns_candidates(text) == ()


def test_contiguous_12_digit_sequence_yields_no_candidate() -> None:
    """R5 adversário: 12 dígitos contíguos (CPF seguido de dígito extra)."""
    text = "Documento: 123456789012"

    assert extract_cpf_candidates(text) == ()
    assert extract_cns_candidates(text) == ()
