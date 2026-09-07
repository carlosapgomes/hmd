"""Testes do gate de regulação adaptado ao domínio (slice 002, design D4/R4).

``evaluate_regulation_report`` é função pura: os textos de fixture são
sintéticos de hemodinâmica (nenhum vestígio EDA/colonoscopia) e longos o
bastante para os thresholds default (500 chars / 3 seções). Cobre:

- gate ok para o relatório padrão SESAB (header + sinais institucionais BA +
  seções operacionais + dígitos), com diagnósticos (listas casadas e
  ``text_length``) e resultado congelado;
- cada ``reason_code`` canônico de falha isolado: ``below_min_chars``,
  ``missing_header``, ``missing_institutional_signals``,
  ``insufficient_sections``.
"""

from __future__ import annotations

import dataclasses

import pytest

from apps.intake.regulation_gate import (
    REASON_BELOW_MIN_CHARS,
    GateResult,
    evaluate_regulation_report,
)

# Rótulos canônicos exibidos nas listas diagnósticas (design D4) — usados aqui
# como display names; a normalização (sem acentos, case-insensitive,
# whitespace colapsado) roda na função.
_HEADER = "RELATÓRIO DE OCORRÊNCIAS"
_INSTITUTIONAL_SIGNALS = [
    # Ordem canônica do design D4 (mesma da lista retornada no diagnóstico).
    "Central Estadual de Regulação",
    "Secretaria da Saúde do Estado",
    "Governo do Estado da Bahia",
]
_FULL_SECTIONS = [
    "Código: 123456",
    "Abertura: 01/02/2025",
    "Unid. Origem: Hospital Geral do Estado",
    "Motivo da Solicitação: cateterismo cardíaco diagnóstico",
    "Complemento da Solicitação: paciente encaminhado para avaliação hemodinâmica",
    "Resumo Clínico: paciente de 58 anos com dor torácica, em investigação",
    "Dias em tela: 3",
    "Data Adm. Unid.: 01/02/2025",
]

_PADDING = (
    "Linha de preenchimento para atingir o tamanho mínimo exigido pelo detector de relatório."
)


def _report_text(
    *,
    header: bool = True,
    institutional: bool = True,
    sections: list[str] | None = None,
) -> str:
    """Monta um relatório sintético longo o bastante (> threshold default de chars).

    Flags removem apenas a dimensão pedida para isolar cada falha do gate.
    """
    lines: list[str] = []
    if header:
        lines.append(_HEADER)
    if institutional:
        lines.extend(_INSTITUTIONAL_SIGNALS)
    for section in sections or _FULL_SECTIONS:
        lines.append(section)
    while len("\n".join(lines)) < 520:
        lines.append(_PADDING)
    return "\n".join(lines)


def test_standard_report_ok() -> None:
    """R4/R6: relatório padrão (header + sinais + seções + dígitos) passa o gate."""
    text = _report_text()

    result = evaluate_regulation_report(text)

    assert isinstance(result, GateResult)
    assert result.ok is True
    assert result.reason_code == "ok"
    # Diagnósticos: os rótulos canônicos casados alimentam o motivo ao NIR.
    assert result.matched_institutional_signals == _INSTITUTIONAL_SIGNALS
    assert "Código:" in result.matched_operational_sections
    assert "Motivo da Solicitação" in result.matched_operational_sections
    assert "Resumo Clínico" in result.matched_operational_sections
    assert result.text_length == len(text.strip())
    assert result.text_length >= 500


def test_each_failure_reason() -> None:
    """R4: cada reason_code canônico de falha, isolado (só um critério falha)."""
    cases = [
        ("below_min_chars", "texto curto demais para o gate avaliar."),
        ("missing_header", _report_text(header=False)),
        ("missing_institutional_signals", _report_text(institutional=False)),
        (
            "insufficient_sections",
            _report_text(sections=["Código: 123456", "Abertura: 01/02/2025"]),
        ),
    ]

    for expected_reason, text in cases:
        result = evaluate_regulation_report(text)
        assert result.ok is False
        assert result.reason_code == expected_reason


def test_header_match_tolerates_accents_and_case() -> None:
    """R4: normalização sem acentos/case-insensitive — header e sinais casam."""
    text = _report_text()
    text = text.replace(_HEADER, "relatorio de ocorrencias")
    text = text.replace("Secretaria da Saúde do Estado", "secretaria da saude do estado")

    result = evaluate_regulation_report(text)

    assert result.ok is True
    assert result.reason_code == "ok"


def test_empty_text_below_min_chars() -> None:
    """R4: PDF sem camada de texto ("" extraída) é retido por below_min_chars."""
    result = evaluate_regulation_report("   \n  \n  ")

    assert result.ok is False
    assert result.reason_code == REASON_BELOW_MIN_CHARS
    assert result.text_length == 0


def test_failure_result_carries_diagnostics() -> None:
    """R4: falha preserva os diagnósticos (listas casadas/text_length) para o NIR."""
    text = _report_text(header=False)

    result = evaluate_regulation_report(text)

    assert result.ok is False
    assert result.reason_code == "missing_header"
    assert len(result.matched_institutional_signals) >= 1
    assert len(result.matched_operational_sections) >= 3
    assert result.text_length == len(text.strip())


def test_result_is_frozen() -> None:
    """R4: GateResult é dataclass congelada — imutável após a avaliação."""
    result = evaluate_regulation_report(_report_text())

    assert dataclasses.is_dataclass(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(result, "ok", False)


def test_deterministic() -> None:
    """R4: mesma entrada → mesmo resultado (função pura, sem estado)."""
    text = _report_text()

    first = evaluate_regulation_report(text)
    second = evaluate_regulation_report(text)

    assert first == second
