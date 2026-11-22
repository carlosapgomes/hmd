"""Testes da extração pura do tipo de transporte (change transport-badge, slice 001, R1).

O label ``TRANSPORTE:`` é um campo estrutural do bloco «RELATÓRIO MÉDICO» do
relatório de regulação: a extração é linha-âncorada com whitespace HORIZONTAL
(o valor precisa estar na MESMA linha do rótulo), tolerante a acento/caixa,
com domínio fechado (``basic``/``mobile_icu``) e última ocorrência vencendo.
Fixtures SINTÉTICAS com o layout real do corpus — os PDFs reais contêm PHI e
JAMAIS entram no repo.
"""

from __future__ import annotations

from apps.intake.pdf_utils import extract_transport_type


def test_basic_full_form() -> None:
    """Label básico popula o caso (cenário «Label básico popula o caso»)."""
    text = "\n".join(
        [
            "RELATÓRIO DE OCORRÊNCIAS",
            "TRANSPORTE: Ambulância Básica",
            "Resumo Clínico: paciente estável",
        ]
    )

    assert extract_transport_type(text) == "basic"


def test_mobile_icu_full_form() -> None:
    """UTI Móvel popula o caso (cenário «UTI Móvel popula o caso»)."""
    text = "\n".join(
        [
            "RELATÓRIO DE OCORRÊNCIAS",
            "TRANSPORTE: UTI Móvel",
            "Resumo Clínico: paciente grave",
        ]
    )

    assert extract_transport_type(text) == "mobile_icu"


def test_abbreviated_form_normalized() -> None:
    """Forma abreviada é normalizada (cenário «Forma abreviada é normalizada»)."""
    text = "\n".join(
        [
            "RELATÓRIO DE OCORRÊNCIAS",
            "TRANSPORTE: BÁSICA",
            "Resumo Clínico: paciente estável",
        ]
    )

    assert extract_transport_type(text) == "basic"


def test_case_and_accent_insensitive() -> None:
    """Casamento tolera acento/caixa (contrato da spec: «tolerante a acento/caixa»)."""
    text = "\n".join(
        [
            "RELATÓRIO DE OCORRÊNCIAS",
            "transporte: uti movel",
            "Resumo Clínico: paciente grave",
        ]
    )

    assert extract_transport_type(text) == "mobile_icu"


def test_surrounding_whitespace_tolerated() -> None:
    """Whitespace horizontal ao redor do rótulo/valor não quebra a captura."""
    text = "  TRANSPORTE  :   Ambulância Básica  "

    assert extract_transport_type(text) == "basic"


def test_unknown_value_returns_none() -> None:
    """Valor fora do domínio não inventa badge (cenário «Valor fora do domínio»)."""
    text = "\n".join(
        [
            "RELATÓRIO DE OCORRÊNCIAS",
            "TRANSPORTE: Aéreo",
            "Resumo Clínico: paciente estável",
        ]
    )

    assert extract_transport_type(text) is None


def test_absent_label_returns_none() -> None:
    """Label ausente deixa campo vazio (cenário «Label ausente deixa campo vazio»)."""
    text = "\n".join(
        [
            "RELATÓRIO DE OCORRÊNCIAS",
            "Resumo Clínico: paciente estável sem menção de transporte",
        ]
    )

    assert extract_transport_type(text) is None


def test_empty_value_returns_none() -> None:
    """Rótulo sem valor na linha deixa campo vazio (sem valor inventado)."""
    text = "\n".join(
        [
            "RELATÓRIO DE OCORRÊNCIAS",
            "TRANSPORTE:",
            "Resumo Clínico: paciente estável",
        ]
    )

    assert extract_transport_type(text) is None


def test_label_alone_with_value_next_line_not_captured() -> None:
    """Rótulo sozinho com valor na linha seguinte NÃO captura (design D1).

    O whitespace do regex é HORIZONTAL: ``\\s`` consumiria a quebra de linha
    e capturaria o valor da linha seguinte, violando o contrato
    «valor na mesma linha».
    """
    text = "\n".join(
        [
            "RELATÓRIO DE OCORRÊNCIAS",
            "TRANSPORTE:",
            "Ambulância Básica",
            "Resumo Clínico: paciente estável",
        ]
    )

    assert extract_transport_type(text) is None


def test_multiple_occurrences_last_wins() -> None:
    """Múltiplas ocorrências elegem a última (cenário dedicado da spec)."""
    text = "\n".join(
        [
            "RELATÓRIO DE OCORRÊNCIAS",
            "TRANSPORTE: Ambulância Básica",
            "Evolução: quadro estável",
            "TRANSPORTE: Ambulância Básica",
            "Evolução: piora do quadro",
            "TRANSPORTE: UTI Móvel",
        ]
    )

    assert extract_transport_type(text) == "mobile_icu"


def test_earlier_citation_does_not_override_current() -> None:
    """Citação de relatório anterior não sobrepõe a evolução atual (cenário dedicado).

    Citações de solicitação anterior vêm ANTES dos blocos de evolução no texto,
    então a última ocorrência já é a evolução atual por construção.
    """
    text = "\n".join(
        [
            "Complemento da Solicitação: solicitação anterior com",
            "TRANSPORTE: UTI Móvel",
            "Resumo Clínico: evolução atual do quadro",
            "TRANSPORTE: Ambulância Básica",
        ]
    )

    assert extract_transport_type(text) == "basic"


def test_narrative_mention_without_colon_not_captured() -> None:
    """Menção narrativa sem o formato de rótulo não é capturada (cenário dedicado)."""
    text = "\n".join(
        [
            "Complemento da Solicitação: Transporte em ambulância avançada",
            "para unidade de referência",
            "Resumo Clínico: paciente estável",
        ]
    )

    assert extract_transport_type(text) is None
