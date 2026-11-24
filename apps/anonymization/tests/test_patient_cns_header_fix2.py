"""Fix-2 P1-01a/P1-01b RED: pertinência seccional do CNS + taint só no cabeçalho.

P1-01a: ``_is_header_cns_line`` aceitava âncora em qualquer lado da linha
``CNS:`` sem respeitar fronteiras de seção narrativa anteriores — um CNS
narrativo seguido de campo solto (``Nome:``) virava identidade, e um cabeçalho
real repetido após narrativa era ignorado. A pertinência agora é seccional:
fronteira narrativa (``resumo clinico``/``motivo da solicitacao``/
``complemento da solicitacao``) encerra o bloco estrutural; só um reinício
forte (título ``RELATÓRIO DE OCORRÊNCIAS`` ou linha de demografia, ambos
presentes em todas as fixtures SESAB e repetidos por página) reabre o
cabeçalho. ``Nome:`` solto após o Resumo NÃO reabre.

P1-01b: a contaminação por valor malformado/checksum inválido ocorria ANTES
do teste de pertinência — narrativa inválida apagava um CNS legítimo do
cabeçalho (o probe do parent mostra ``None`` em vez do CNS válido). A
pertinência é estabelecida PRIMEIRO; narrativa nunca participa (nem como
identidade, nem como contaminação); só valor inválido DE CABEÇALHO invalida.

Estes testes falham contra o comportamento vigente (RED de comportamento) e
passam após a correção. Controles de comportamento preservado incluídos.
"""

from __future__ import annotations

from apps.anonymization.deterministic import extract_patient_cns

CNS_A = "207139200176053"
CNS_B = "605932160152243"
CNS_INVALID = "207139200176054"


def _real_header(cns_line: str) -> str:
    """Cabeçalho SESAB no layout real D1 (fix-3: com Nome Social)."""
    return (
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
        f"{cns_line}\n"
        "Dias em tela:\n"
        "5\n"
        "Nome Social:\n"
        "Resumo Clínico: acompanhamento ambulatorial de rotina.\n"
    )


# ── P1-01a: pertinência seccional ──────────────────────────────────────────


def test_narrative_cns_before_later_name_field_stays_empty() -> None:
    """P1-01a: CNS narrativo + ``Nome:`` posterior NÃO vira identidade."""
    text = (
        "Resumo Clínico: paciente evoluiu bem, sem intercorrências.\n"
        f"CNS: {CNS_A}\n"
        "Nome: MARIA DA SILVA\n"
    )
    assert extract_patient_cns(text) is None


def test_narrative_cns_before_later_code_field_stays_empty() -> None:
    """P1-01a: CNS narrativo + ``Código:`` posterior NÃO vira identidade."""
    text = (
        f"Resumo Clínico: paciente evoluiu bem, sem intercorrências.\nCNS: {CNS_A}\nCódigo: 99999\n"
    )
    assert extract_patient_cns(text) is None


def test_blank_header_then_narrative_then_repeated_header_uses_only_real() -> None:
    """P1-01a: cabeçalho em branco + narrativa + cabeçalho real repetido.

    Só o CNS do bloco estrutural repetido participa (o narrativo distinto
    é ignorado, não ambigua nem contamina).
    """
    text = (
        _real_header("CNS:")
        + f"CNS: {CNS_B}\n"
        + "Evolução estável do quadro.\n"
        + _real_header(f"CNS: {CNS_A}")
    )
    assert extract_patient_cns(text) == CNS_A


def test_blank_header_then_invalid_narrative_then_repeated_header() -> None:
    """P1-01a/01b: narrativa com checksum inválido entre cabeçalhos não apaga o real."""
    text = (
        _real_header("CNS:")
        + f"CNS: {CNS_INVALID}\n"
        + "Evolução estável do quadro.\n"
        + _real_header(f"CNS: {CNS_A}")
    )
    assert extract_patient_cns(text) == CNS_A


# ── P1-01b: pertinência antes da validação ──────────────────────────────────


def test_valid_header_not_tainted_by_invalid_checksum_narrative_after() -> None:
    """P1-01b: cabeçalho válido + narrativa com checksum inválido → mantém o CNS."""
    text = _real_header(f"CNS: {CNS_A}") + f"CNS: {CNS_INVALID}\n"
    assert extract_patient_cns(text) == CNS_A


def test_valid_header_not_tainted_by_malformed_narrative_after() -> None:
    """P1-01b: cabeçalho válido + narrativa malformada (letras) → mantém o CNS."""
    text = _real_header(f"CNS: {CNS_A}") + f"CNS: X{CNS_A}\n"
    assert extract_patient_cns(text) == CNS_A


def test_invalid_narrative_before_valid_header_is_ignored() -> None:
    """P1-01b: narrativa inválida ANTES do cabeçalho válido não apaga nem vira identidade."""
    text = f"CNS: {CNS_INVALID}\nResumo Clínico: quadro estável.\n" + _real_header(f"CNS: {CNS_A}")
    assert extract_patient_cns(text) == CNS_A


# ── Controles: comportamento preservado ─────────────────────────────────────


def test_repeated_identical_real_headers_deduplicate() -> None:
    """Controle: páginas repetidas do layout real com o mesmo CNS → um valor."""
    assert (
        extract_patient_cns(_real_header(f"CNS: {CNS_A}") + _real_header(f"CNS:{CNS_A}")) == CNS_A
    )


def test_invalid_header_value_still_rejects_identity() -> None:
    """Controle: valor inválido EM CABEÇALHO continua invalidando a identidade."""
    assert extract_patient_cns(_real_header(f"CNS: {CNS_INVALID}")) is None


def test_distinct_valid_header_values_still_empty() -> None:
    """Controle: dois CNS válidos distintos de CABEÇALHO → identidade vazia."""
    text = _real_header(f"CNS: {CNS_A}") + _real_header(f"CNS: {CNS_B}")
    assert extract_patient_cns(text) is None
