"""Testes da extração pura dos metadados do cabeçalho SESAB (change
sesab-header-extraction, slice 001, R1/D3).

O cabeçalho padrão SESAB repete-se por página e, na extração linear do
PyMuPDF, rótulos e valores desalinham: a demografia vem numa linha
``<NOME> - Idade: 79a. - Sexo: F - Raça/Cor: Parda`` ANTES do rótulo
``Paciente:`` sozinho; ``Dias em tela:`` é rótulo sozinho com o valor na
linha imediatamente seguinte. A âncora anti-falso-positivo exige os TRÊS
marcadores de demografia NA MESMA LINHA, na ordem (menção clínica órfã de
idade não casa); a raça é restrita à enumeração IBGE + "Não informado".
"""

from __future__ import annotations

import dataclasses

import pytest

from apps.intake.pdf_utils import SESAB_FIELD_LABELS, HeaderMetadata, extract_header_metadata

# Linha de demografia canônica do cabeçalho real (nome + idade + sexo + raça).
_DEMOGRAPHICS_LINE = "FULANO DE TAL SILVA - Idade: 79a. - Sexo: F - Raça/Cor: Parda"


def _header_lines(*, days_value: str = "5") -> list[str]:
    """Bloco de cabeçalho do layout real (rótulos e valores desalinhados)."""
    return [
        "RELATÓRIO DE OCORRÊNCIAS",
        "5040778",
        _DEMOGRAPHICS_LINE,
        "Paciente:",
        "Abertura:",
        "01/02/2026",
        "Código:",
        "10:00",
        "Governo do Estado da Bahia",
        "Secretaria da Saúde do Estado",
        "Central Estadual de Regulação",
        "CNS:123456789012345",
        "Dias em tela:",
        days_value,
        "Data Adm. Unid.:",
        "01/02/2026",
        "Dias Unid.:",
        "3",
    ]


# ── R1: linha de demografia canônica ───────────────────────────────────────


def test_extracts_demographics_from_canonical_header_line() -> None:
    """R1/D3: idade/sexo/raça vêm da linha de demografia do cabeçalho real."""
    metadata = extract_header_metadata("\n".join(_header_lines()))

    assert metadata.age == 79
    assert metadata.gender == "F"
    assert metadata.race == "Parda"


def test_days_on_screen_multiline_form() -> None:
    """R1/D3: rótulo sozinho com o valor na linha seguinte (layout real)."""
    metadata = extract_header_metadata("\n".join(_header_lines(days_value="5")))

    assert metadata.days_on_screen == 5


def test_days_on_screen_inline_form() -> None:
    """R1/D3: a forma mesma-linha (`Dias em tela: N`) também casa."""
    text = "\n".join([_DEMOGRAPHICS_LINE, "Dias em tela: 12", "Resumo Clínico: dor torácica"])

    metadata = extract_header_metadata(text)

    assert metadata.days_on_screen == 12


def test_days_on_screen_max_across_pages() -> None:
    """R1/D3: maior valor entre todas as ocorrências/páginas (multilinha)."""
    text = "\n".join(
        [
            *_header_lines(days_value="5"),
            *_header_lines(days_value="6"),
        ]
    )

    metadata = extract_header_metadata(text)

    assert metadata.days_on_screen == 6


def test_days_on_screen_max_across_inline_forms() -> None:
    """R1/D3: maior valor também na forma mesma-linha repetida."""
    text = "\n".join(["Dias em tela: 3", "Dias em tela: 11"])

    metadata = extract_header_metadata(text)

    assert metadata.days_on_screen == 11


def test_days_on_screen_multiple_inline_same_line() -> None:
    """R1/D3 (P2 da review): TODAS as ocorrências inline da MESMA linha contam.

    Regressão do ``.search()`` primeira-ocorrência: uma linha com dois
    marcadores inline precisa entregar o MAIOR valor, não o primeiro.
    """
    text = "Dias em tela: 3 — histórico: Dias em tela: 11"

    metadata = extract_header_metadata(text)

    assert metadata.days_on_screen == 11


def test_days_on_screen_label_without_integer_next_line_not_captured() -> None:
    """R1/D3: rótulo sozinho cuja linha seguinte NÃO é um inteiro puro → None."""
    text = "\n".join(["Dias em tela:", "Mais informações clínicas"])

    metadata = extract_header_metadata(text)

    assert metadata.days_on_screen is None


def test_days_on_screen_absent_is_none() -> None:
    """R1/D3: sem o marcador no texto → None (sem fallback inventado)."""
    metadata = extract_header_metadata("Resumo Clínico: paciente com dor torácica")

    assert metadata.days_on_screen is None


# ── R1: adversariais anti-falso-positivo ───────────────────────────────────


def test_orphan_clinical_age_mention_is_ignored() -> None:
    """R1/D3 adversarial: idade em texto clínico sem sexo/raça na mesma linha."""
    text = "\n".join(["História clínica: Idade: 79a.", "Paciente:", "Resumo Clínico: X"])

    metadata = extract_header_metadata(text)

    assert metadata.age is None
    assert metadata.gender is None
    assert metadata.race is None


def test_demographics_markers_must_share_the_same_line() -> None:
    """R1/D3 adversarial: marcadores em linhas SEPARADAS não são cabeçalho."""
    text = "\n".join(["Idade: 79a.", "Sexo: F", "Raça/Cor: Parda"])

    metadata = extract_header_metadata(text)

    assert metadata.age is None
    assert metadata.gender is None
    assert metadata.race is None


def test_demographics_markers_out_of_order_not_captured() -> None:
    """R1/D3: a ordem dos marcadores na linha é contrato (Idade→Sexo→Raça/Cor)."""
    text = "FULANO DE TAL - Sexo: F - Idade: 79a. - Raça/Cor: Parda"

    metadata = extract_header_metadata(text)

    assert metadata.age is None
    assert metadata.gender is None
    assert metadata.race is None


@pytest.mark.parametrize(
    ("race_value", "expected"),
    [
        ("Branca", "Branca"),
        ("Preta", "Preta"),
        ("Parda", "Parda"),
        ("Amarela", "Amarela"),
        ("Indígena", "Indígena"),
        ("Não informado", "Não informado"),
        ("indígena", "indígena"),
    ],
)
def test_race_enum_accented_and_composite_values(race_value: str, expected: str) -> None:
    """R1/D3: enumeração IBGE + "Não informado" (case/acentuação tolerantes)."""
    text = f"FULANO DE TAL - Idade: 79a. - Sexo: F - Raça/Cor: {race_value}"

    metadata = extract_header_metadata(text)

    assert metadata.race == expected
    assert metadata.age == 79


def test_race_outside_enum_not_captured() -> None:
    """R1/D3 adversarial: valor fora do enum não faz a linha ser cabeçalho."""
    text = "FULANO DE TAL - Idade: 79a. - Sexo: F - Raça/Cor: Desconhecida"

    metadata = extract_header_metadata(text)

    assert metadata.age is None
    assert metadata.gender is None
    assert metadata.race is None


@pytest.mark.parametrize(
    "gender_value",
    ["Fem", "Masc"],
)
def test_gender_abbreviations_outside_domain(gender_value: str) -> None:
    """P1 da review do slice 002: abreviações (``Fem``/``Masc``) estão FORA do
    domínio — a linha não é cabeçalho (fonte única: a âncora do nome também
    não ativa)."""
    text = f"FULANO DE TAL - Idade: 79a. - Sexo: {gender_value} - Raça/Cor: Parda"

    metadata = extract_header_metadata(text)

    assert metadata.age is None
    assert metadata.gender is None
    assert metadata.race is None


@pytest.mark.parametrize(
    "race_value",
    ["Branco", "Preto", "Amarelo", "Não informada"],
)
def test_race_masculine_and_feminine_absence_outside_domain(race_value: str) -> None:
    """P1 da review do slice 002: formas masculinas e a ausência no feminino
    (``Não informada``) estão FORA do domínio IBGE especificado."""
    text = f"FULANO DE TAL - Idade: 79a. - Sexo: F - Raça/Cor: {race_value}"

    metadata = extract_header_metadata(text)

    assert metadata.age is None
    assert metadata.gender is None
    assert metadata.race is None


def test_male_gender_captured() -> None:
    """R1/D3: sexo M também é valor canônico do cabeçalho."""
    text = "FULANO DE TAL - Idade: 40a. - Sexo: M - Raça/Cor: Branca"

    metadata = extract_header_metadata(text)

    assert metadata.gender == "M"
    assert metadata.age == 40


def test_absent_header_returns_nones() -> None:
    """R1/D3: texto sem cabeçalho → todos os campos None (não é erro)."""
    metadata = extract_header_metadata("texto clínico qualquer sem cabeçalho")

    assert metadata == HeaderMetadata(age=None, gender=None, race=None, days_on_screen=None)


def test_header_metadata_is_frozen() -> None:
    """R1: ``HeaderMetadata`` é dataclass congelável (resultado imutável)."""
    metadata = HeaderMetadata(age=79, gender="F", race="Parda", days_on_screen=5)

    with pytest.raises(dataclasses.FrozenInstanceError):
        metadata.age = 80  # type: ignore[misc]


# ── R1 (calibração do corpus real): variante sem dois-pontos e gênero por
# extenso — ``<NOME> - Idade: 79a. - Sexo Feminino - Raça/Cor Parda`` ──────


def test_extracts_demographics_from_real_corpus_variant() -> None:
    """R1: layout do corpus real — ``Sexo``/``Raça/Cor`` sem dois-pontos e
    gênero por extenso (o pattern é a fonte única compartilhada com a
    anonymization)."""
    text = (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "5040778\n"
        "FULANO DE TAL SILVA - Idade: 79a. - Sexo Feminino - Raça/Cor Parda\n"
        "Paciente:\n"
    )

    metadata = extract_header_metadata(text)

    assert metadata.age == 79
    assert metadata.gender == "F"
    assert metadata.race == "Parda"


def test_extracts_demographics_real_variant_masculino() -> None:
    """R1: ``Sexo Masculino`` (por extenso) normaliza para ``M``."""
    text = "FULANO DE TAL SILVA - Idade: 42a. - Sexo Masculino - Raça/Cor Branca\n"

    metadata = extract_header_metadata(text)

    assert metadata.gender == "M"
    assert metadata.race == "Branca"


# ── R1/D1 (change painel-ats-parity, slice 001): unidade de origem ─────────
#
# Layout real do corpus: ``Unid. Origem:`` sozinho numa linha e o nome da
# unidade (~7 palavras institucionais, sigla + hífen) na linha SEGUINTE —
# mesma forma multilinha de ``Dias em tela``. O valor só é aceito quando é
# plausível: não-vazio E não inicia rótulo de campo do cabeçalho SESAB
# (catálogo canônico ``SESAB_FIELD_LABELS``, fonte única em ``pdf_utils``).

_ORIGIN_UNIT_VALUE = "HELN - HOSPITAL ESTADUAL DO LESTE NORTE"


def test_origin_unit_multiline_form() -> None:
    """R1: rótulo ``Unid. Origem:`` sozinho + valor na linha seguinte."""
    text = "\n".join(["RELATÓRIO DE OCORRÊNCIAS", "Unid. Origem:", _ORIGIN_UNIT_VALUE])

    metadata = extract_header_metadata(text)

    assert metadata.origin_unit == _ORIGIN_UNIT_VALUE


def test_origin_unit_same_line_form() -> None:
    """R1: forma mesma-linha ``Unid. Origem: <valor>``."""
    text = f"Unid. Origem: {_ORIGIN_UNIT_VALUE}"

    metadata = extract_header_metadata(text)

    assert metadata.origin_unit == _ORIGIN_UNIT_VALUE


def test_origin_unit_unidade_de_origem_variant() -> None:
    """R1: variante ``Unidade de Origem:`` (rótulo sozinho + valor na seguinte)."""
    text = "\n".join(["Unidade de Origem:", _ORIGIN_UNIT_VALUE])

    metadata = extract_header_metadata(text)

    assert metadata.origin_unit == _ORIGIN_UNIT_VALUE


def test_origin_unit_first_occurrence_wins() -> None:
    """R1: primeira ocorrência válida vence (cabeçalho repetido por página)."""
    text = "\n".join(
        [
            "Unid. Origem:",
            _ORIGIN_UNIT_VALUE,
            "Unid. Origem:",
            "OUTRA UNIDADE QUALQUER",
        ]
    )

    metadata = extract_header_metadata(text)

    assert metadata.origin_unit == _ORIGIN_UNIT_VALUE


def test_origin_unit_label_alone_without_plausible_value_is_none() -> None:
    """R1 adversarial: rótulo sozinho + linha vazia → None (sem valor inventado)."""
    text = "\n".join(["Unid. Origem:", "", "Resumo Clínico: quadro estável"])

    metadata = extract_header_metadata(text)

    assert metadata.origin_unit is None


def test_origin_unit_label_at_end_of_text_is_none() -> None:
    """R1 adversarial: rótulo sozinho no fim do texto → None."""
    metadata = extract_header_metadata("RELATÓRIO DE OCORRÊNCIAS\nUnid. Origem:")

    assert metadata.origin_unit is None


@pytest.mark.parametrize("label", SESAB_FIELD_LABELS)
def test_origin_unit_adversarial_each_sesab_label_next_line_is_none(label: str) -> None:
    """R1 adversarial PARAMETRIZADO (anti-drift): CADA rótulo do catálogo
    canônico na linha seguinte a ``Unid. Origem:`` NÃO vira unidade — iterar
    a lista faz um rótulo novo esquecido quebrar o teste.

    Para os PRÓPRIOS rótulos de unidade o candidato vai sem par
    ``: valor`` na mesma linha — ali uma segunda ocorrência do rótulo
    legitimamente fornece o valor da linha seguinte (o contrato testado é
    o bloqueio, não a ausência de novas ocorrências).
    """
    candidate = label if label in ("unid. origem", "unidade de origem") else f"{label}: valor"
    text = f"Unid. Origem:\n{candidate}\n"

    metadata = extract_header_metadata(text)

    assert metadata.origin_unit is None


def test_origin_unit_truncated_to_128_chars() -> None:
    """R1: valor truncado a 128 chars (defesa de tamanho do campo)."""
    long_value = "UNIDADE " * 40

    metadata = extract_header_metadata("\n".join(["Unid. Origem:", long_value]))

    assert metadata.origin_unit is not None
    assert len(metadata.origin_unit) == 128
    assert metadata.origin_unit == long_value.strip()[:128]


def test_origin_unit_absent_is_none_and_other_metadata_preserved() -> None:
    """R1: cabeçalho real sem ``Unid. Origem:`` → None (não-vacuoso: os demais
    metadados seguem extraídos)."""
    metadata = extract_header_metadata("\n".join(_header_lines()))

    assert metadata.origin_unit is None
    assert metadata.age == 79
    assert metadata.days_on_screen == 5


# ── R1: catálogo canônico compartilhado (pdf_utils ↔ deterministic) ────────


def test_sesab_field_labels_covers_header_demographics() -> None:
    """R1: o catálogo canônico contém os rótulos do cabeçalho que faltavam na
    lista legada (fold: minúsculo/sem acento/colapsado)."""
    for label in (
        "paciente",
        "nome social",
        "sexo",
        "idade",
        "raca/cor",
        "data adm. unid.",
        "dias unid.",
        "dias em tela",
        "abertura",
        "codigo",
        "unid. origem",
        "unidade de origem",
    ):
        assert label in SESAB_FIELD_LABELS


@pytest.mark.parametrize("label", SESAB_FIELD_LABELS)
def test_deterministic_field_break_uses_canonical_label_list(label: str) -> None:
    """R1: o ``deterministic.py`` compila a quebra de campo da MESMA lista
    canônica — a captura do valor de um rótulo para no rótulo seguinte."""
    from apps.anonymization.deterministic import extract_patient_name

    text = f"Nome: MARIA DA SILVA\n{label}: outro campo\n"

    assert extract_patient_name(text) == "MARIA DA SILVA"
