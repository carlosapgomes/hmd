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


# ── R1/D1 (slice 002 sesab-header-extraction): âncora do cabeçalho real ────
#
# Layout real (validado no corpus do piloto): na extração linear do PyMuPDF o
# nome completo do paciente vem na linha de DEMOGRAFIA
# (``<NOME> - Idade: 79a. - Sexo: F - Raça/Cor: Parda``) e o rótulo ``Paciente:``
# aparece sozinho na linha SEGUINTE, bloco repetido a cada página. O pattern da
# linha de demografia é a fonte única importada de ``apps.intake.pdf_utils``.
_SESAB_HEADER_PAGE = (
    "RELATÓRIO DE OCORRÊNCIAS\n"
    "5040778\n"
    "ANTONIO CARLOS PEREIRA LIMA - Idade: 79a. - Sexo: F - Raça/Cor: Parda\n"
    "Paciente:\n"
    "Abertura:\n"
    "05/02/2025\n"
    "Código:\n"
    "09:12\n"
    "Governo do Estado da Bahia\n"
    "Secretaria da Saúde do Estado da Bahia\n"
    "Central Estadual de Regulação\n"
    "CNS:547762666886141\n"
    "Dias em tela:\n"
    "5\n"
    "Data Adm. Unid.:\n"
    "05/02/2025\n"
    "Dias Unid.:\n"
    "3\n"
    "Pág: 1\n"
    "09:30:00\n"
    "Data:\n"
    "Hora:\n"
    "Nome Social:\n"
    "Central. Reg.:\n"
    "CER - Central Estadual de Regulação\n"
)


def test_patient_name_from_demographics_line_anchor() -> None:
    """R1/D1: nome do layout real (demografia + ``Paciente:`` sozinho abaixo)."""
    assert extract_patient_name(_SESAB_HEADER_PAGE) == "ANTONIO CARLOS PEREIRA LIMA"


def test_patient_name_anchor_requires_following_label_line() -> None:
    """R1: demografia cuja linha seguinte NÃO é ``Paciente:`` → None."""
    text = "ANTONIO CARLOS PEREIRA LIMA - Idade: 79a. - Sexo: F - Raça/Cor: Parda\nAbertura:\n"

    assert extract_patient_name(text) is None


def test_patient_name_orphan_clinical_demographics_not_captured() -> None:
    """R1 adversarial: «Idade: 79a.» clínico órfão NÃO gera nome.

    Menção clínica de idade sem os outros marcadores na mesma linha, mesmo
    seguida de uma linha ``Paciente:``, nunca casa — a âncora exige a linha de
    demografia COMPLETA (Idade+Sexo+Raça/Cor na mesma linha, na ordem).
    """
    text = "Resumo Clínico: paciente em investigação. Idade: 79a. conforme anotação.\nPaciente:\n"

    assert extract_patient_name(text) is None


def test_patient_name_anchor_requires_race_marker_same_line() -> None:
    """R1 adversarial: Idade+Sexo sem Raça/Cor na mesma linha → None."""
    text = "ANTONIO CARLOS PEREIRA LIMA - Idade: 79a. - Sexo: F\nPaciente:\n"

    assert extract_patient_name(text) is None


def test_patient_name_anchor_single_word_prefix_ignored() -> None:
    """R1: prefixo de uma única palavra não é nome válido (≥ 2 palavras)."""
    text = "ADULTO - Idade: 79a. - Sexo: F - Raça/Cor: Parda\nPaciente:\n"

    assert extract_patient_name(text) is None


def test_patient_name_anchor_first_valid_across_pages() -> None:
    """R1: blocos repetidos por página — a primeira ocorrência válida vence."""
    text = _SESAB_HEADER_PAGE + _SESAB_HEADER_PAGE.replace(
        "ANTONIO CARLOS PEREIRA LIMA", "OUTRO NOME QUALQUER"
    )

    assert extract_patient_name(text) == "ANTONIO CARLOS PEREIRA LIMA"


def test_patient_name_anchor_precedes_same_line_label_layout() -> None:
    """R1: a âncora do cabeçalho é tentada ANTES dos rótulos mesma-linha."""
    text = "Paciente: MARIA DA SILVA\n" + _SESAB_HEADER_PAGE

    assert extract_patient_name(text) == "ANTONIO CARLOS PEREIRA LIMA"


# ── R2/D2 (slice 002): nome social — candidato PESSOA, sem fallback ────────


def test_social_name_same_line_captured() -> None:
    """R2/D2: valor na MESMA linha do rótulo é capturado (sem linkage)."""
    text = "Nome Social: TONI LIMA\nCentral. Reg.: CER - Central Estadual\n"

    result = run_deterministic_extraction(text)

    assert result.social_name == "TONI LIMA"
    assert result.patient_name is None


def test_social_name_accented_value_captured() -> None:
    """R2/D2: nome social acentuado é valor nominal válido."""
    result = run_deterministic_extraction("Nome Social: JOÃO DA CONCEIÇÃO\n")

    assert result.social_name == "JOÃO DA CONCEIÇÃO"


def test_social_name_label_alone_returns_none() -> None:
    """R2/D2: rótulo sozinho → None — SEM fallback de linha anterior.

    O texto clínico ANTES do rótulo não pode ser capturado como nome social.
    """
    text = (
        "Resumo Clínico: paciente sem registro de nome social informado.\n"
        "Nome Social:\n"
        "Central. Reg.: CER - Central Estadual de Regulação\n"
    )

    assert run_deterministic_extraction(text).social_name is None


def test_social_name_equal_to_civil_name_ignored() -> None:
    """R2/D2: valor igual ao nome civil (fold) não gera candidato próprio."""
    text = "Nome Social: ANTONIO CARLOS PEREIRA LIMA\nPaciente: ANTONIO CARLOS PEREIRA LIMA\n"

    assert run_deterministic_extraction(text).social_name is None


@pytest.mark.parametrize("value", ["N/A", "12345", "A", "-", "J. SILVA", "  "])
def test_social_name_non_nominal_value_ignored(value: str) -> None:
    """R2/D2: valor com dígito/símbolo ou palavra de 1 letra → None."""
    assert run_deterministic_extraction(f"Nome Social: {value}\n").social_name is None


def test_full_extraction_real_layout_composition() -> None:
    """R1/R3: composição no layout real — nome pelo cabeçalho, sem nascimento."""
    result = run_deterministic_extraction(_SESAB_HEADER_PAGE)

    assert result.patient_name == "ANTONIO CARLOS PEREIRA LIMA"
    assert result.social_name is None
    assert result.birth_date is None  # o relatório SESAB real não traz nascimento
    assert result.record_number == "5040778"
    assert result.cns_candidates == ("547762666886141",)


def test_patient_name_anchor_real_corpus_variant_without_colons() -> None:
    """R1: variante do corpus real — ``Sexo``/``Raça/Cor`` sem dois-pontos e
    gênero por extenso (o pattern da demografia vem do intake, fonte única)."""
    text = (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "5040778\n"
        "FULANO DE TAL SILVA - Idade: 79a. - Sexo Feminino - Raça/Cor PARDA\n"
        "Paciente:\n"
    )

    assert extract_patient_name(text) == "FULANO DE TAL SILVA"
