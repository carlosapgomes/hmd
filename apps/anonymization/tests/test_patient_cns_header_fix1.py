"""P1-01 RED: CNS extraído somente do cabeçalho SESAB (fix-1).

O extrator vigente aceita qualquer linha própria ``CNS:`` do texto, sem
pertinência ao cabeçalho: narrativa em linha própria vira identidade e
contamina (taint) um CNS legítimo do cabeçalho. O contrato D1 exige
pertinência ao cabeçalho identificado pelo extrator.

Estes testes falham contra o comportamento vigente (RED de comportamento) e
passam após restringir ao contexto estrutural do cabeçalho.
"""

from __future__ import annotations

from apps.anonymization.deterministic import extract_patient_cns

CNS_A = "207139200176053"
CNS_B = "605932160152243"
CNS_INVALID = "207139200176054"


def _header(cns_line: str) -> str:
    """Cabeçalho SESAB sintético D1 (fix-3: quadro fechado revision002)."""
    return (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "Código: 33345\n"
        "Paciente:\n"
        "MARIA DA SILVA - Idade: 58 a. - Sexo: F - Raça/Cor: Parda\n"
        "Central Estadual de Regulação\n"
        f"{cns_line}\n"
        "Dias em tela: 3\n"
        "Nome Social:\n"
        "Resumo Clínico: acompanhamento ambulatorial de rotina.\n"
    )


def test_narrative_own_line_cns_without_header_is_empty() -> None:
    """P1-01: linha própria ``CNS:`` na narrativa, sem cabeçalho, não identifica."""
    text = (
        "Resumo Clínico: paciente evoluiu bem, sem intercorrências.\n"
        f"CNS: {CNS_A}\n"
        "Alta programada para amanhã.\n"
    )
    assert extract_patient_cns(text) is None


def test_blank_header_then_narrative_cns_stays_empty() -> None:
    """P1-01: cabeçalho com CNS em branco + linha narrativa posterior não vira identidade."""
    text = _header("CNS:") + f"CNS: {CNS_A}\n" + "Evolução estável.\n"
    assert extract_patient_cns(text) is None


def test_header_cns_not_tainted_by_later_narrative_cns() -> None:
    """P1-01: CNS legítimo do cabeçalho não é contaminado por CNS narrativo distinto."""
    text = _header(f"CNS: {CNS_A}") + f"CNS: {CNS_B}\n" + "Evolução estável.\n"
    assert extract_patient_cns(text) == CNS_A


def test_letters_mixed_into_cns_value_rejected() -> None:
    """P1-01: valor com letras coladas aos dígitos não tem a gramática de separadores."""
    assert extract_patient_cns(_header(f"CNS: X{CNS_A}")) is None
    assert extract_patient_cns(_header(f"CNS: {CNS_A}X")) is None


def test_header_cns_still_accepted() -> None:
    """Controle: CNS válido do cabeçalho continua identidade (sem regressão)."""
    assert extract_patient_cns(_header(f"CNS: {CNS_A}")) == CNS_A
    assert extract_patient_cns(_header(f"CNS:{CNS_A}")) == CNS_A
    assert extract_patient_cns(_header(f"CNS: {CNS_INVALID}")) is None
