"""Fix-3 RED: gramatica textual fechada D1 (revision002).

Substitui proximidade/reinicio por demografia: somente titulo inteiro abre
candidato; termino valido soh Nome Social; Paciente/Codigo antes da
assinatura; slot imediato Central -> CNS -> Dias; validacao do bloco antes de
formato/checksum/taint. Positivos no quadro D1 completo; negativos para cada
marcador ausente/fora de ordem e para o repro do review3.
"""

from __future__ import annotations

from apps.anonymization.deterministic import extract_patient_cns

CNS_A = "207139200176053"
CNS_B = "605932160152243"
CNS_INVALID = "207139200176054"


def _canonical(
    cns_line: str = f"CNS: {CNS_A}",
    *,
    title: str = "RELATÓRIO DE OCORRÊNCIAS",
    nome_social: str | None = "Nome Social:",
    signature: str | None = "Central Estadual de Regulação",
    dias_label: str = "Dias em tela:",
    paciente_label: str = "Paciente:",
    codigo_label: str = "Código:",
    extra_between: tuple[str, ...] = (),
) -> str:
    """Quadro canonico D1 na ordem textual observada (31 paginas)."""
    lines = [
        title,
        "5040778",
        "ANTONIO CARLOS PEREIRA LIMA - Idade: 79a. - Sexo: F - Raça/Cor: Parda",
        paciente_label,
        "Abertura:",
        "05/02/2025",
        codigo_label,
        "09:12",
        "Governo do Estado da Bahia",
        "Secretaria da Saúde do Estado da Bahia",
    ]
    if signature is not None:
        lines.append(signature)
    lines.extend(extra_between)
    lines.append(cns_line)
    lines.append(dias_label)
    lines.append("5")
    if nome_social is not None:
        lines.append(nome_social)
    lines.append("Resumo Clínico: acompanhamento ambulatorial de rotina.")
    return "\n".join(lines) + "\n"


# ── Positivos D1 ──────────────────────────────────────────────────────────


def test_canonical_frame_identifies() -> None:
    assert extract_patient_cns(_canonical()) == CNS_A


def test_canonical_without_space_identifies() -> None:
    assert extract_patient_cns(_canonical(f"CNS:{CNS_A}")) == CNS_A


def test_canonical_case_accent_normalized_identifies() -> None:
    text = _canonical(
        f"cns: {CNS_A}",
        title="relatorio de ocorrencias",
        signature="central estadual de regulacao",
        dias_label="dias em tela:",
        nome_social="nome social:",
    )
    assert extract_patient_cns(text) == CNS_A


def test_canonical_blank_lines_allowed() -> None:
    text = (
        _canonical()
        .replace(
            "Central Estadual de Regulação\n",
            "Central Estadual de Regulação\n\n",
        )
        .replace("Dias em tela:\n", "Dias em tela:\n\n")
    )
    assert extract_patient_cns(text) == CNS_A


def test_repeated_identical_pages_deduplicate() -> None:
    assert extract_patient_cns(_canonical() + _canonical()) == CNS_A


def test_empty_cns_slot_is_empty_identity() -> None:
    assert extract_patient_cns(_canonical("CNS:")) is None


# ── Repro review3 + demografia isolada ─────────────────────────────────────


def test_review3_narrative_demographics_then_cns_stays_empty() -> None:
    text = (
        "Resumo Clínico: paciente evoluiu bem, sem intercorrências.\n"
        "MARIA DA SILVA - Idade: 58 a. - Sexo: F - Raça/Cor: Parda\n"
        f"CNS: {CNS_A}\n"
    )
    assert extract_patient_cns(text) is None


def test_demographic_line_does_not_open_block() -> None:
    text = (
        "MARIA DA SILVA - Idade: 58 a. - Sexo: F - Raça/Cor: Parda\n"
        "Paciente:\n"
        "Código: 33345\n"
        "Central Estadual de Regulação\n"
        f"CNS: {CNS_A}\n"
        "Dias em tela:\n"
        "Nome Social:\n"
    )
    assert extract_patient_cns(text) is None


def test_fields_without_title_stay_empty() -> None:
    text = (
        "Paciente:\n"
        "Código: 33345\n"
        "Central Estadual de Regulação\n"
        f"CNS: {CNS_A}\n"
        "Dias em tela:\n"
        "Nome Social:\n"
    )
    assert extract_patient_cns(text) is None


# ── Marcadores ausentes / fora de ordem ───────────────────────────────────


def test_missing_signature_stays_empty() -> None:
    assert extract_patient_cns(_canonical(signature=None)) is None


def test_missing_nome_social_stays_empty() -> None:
    text = _canonical()
    text = text.replace("Nome Social:\n", "")
    assert extract_patient_cns(text) is None


def test_nome_social_after_clinical_section_stays_empty() -> None:
    lines = [
        "RELATÓRIO DE OCORRÊNCIAS",
        "Paciente:",
        "Código: 33345",
        "Central Estadual de Regulação",
        f"CNS: {CNS_A}",
        "Dias em tela:",
        "Resumo Clínico: quadro estável.",
        "Nome Social:",
    ]
    assert extract_patient_cns("\n".join(lines) + "\n") is None


def test_next_title_before_nome_social_invalidates_first() -> None:
    first = _canonical().replace("Nome Social:\n", "")
    assert extract_patient_cns(first + _canonical()) == CNS_A


def test_clinical_section_before_nome_social_invalidates() -> None:
    # Slot existe mas ha narrativa antes do termino -> candidato ignorado.
    assert extract_patient_cns(_canonical()) is not None  # controle
    lines = [
        "RELATÓRIO DE OCORRÊNCIAS",
        "Paciente:",
        "Código: 33345",
        "Central Estadual de Regulação",
        f"CNS: {CNS_A}",
        "Dias em tela:",
        "Motivo da Solicitação: dor torácica.",
        "Nome Social:",
    ]
    assert extract_patient_cns("\n".join(lines) + "\n") is None


def test_wrong_order_cns_before_signature_stays_empty() -> None:
    lines = [
        "RELATÓRIO DE OCORRÊNCIAS",
        "Paciente:",
        "Código: 33345",
        f"CNS: {CNS_A}",
        "Central Estadual de Regulação",
        "Dias em tela:",
        "Nome Social:",
    ]
    assert extract_patient_cns("\n".join(lines) + "\n") is None


def test_intervening_line_between_signature_and_cns_stays_empty() -> None:
    assert (
        extract_patient_cns(
            _canonical(extra_between=("Observação administrativa: ver prontuário.",))
        )
        is None
    )


def test_intervening_line_between_cns_and_dias_stays_empty() -> None:
    text = _canonical().replace(
        f"CNS: {CNS_A}\nDias em tela:",
        f"CNS: {CNS_A}\nObservação: aguardar\nDias em tela:",
    )
    assert extract_patient_cns(text) is None


def test_cns_value_from_next_line_is_empty() -> None:
    text = _canonical("CNS:").replace("CNS:\nDias em tela:", f"CNS:\n{CNS_A}\nDias em tela:")
    assert extract_patient_cns(text) is None


def test_cns_outside_slot_ignored() -> None:
    # CNS antes da assinatura (fora do slot) + slot vazio -> vazio.
    lines = [
        "RELATÓRIO DE OCORRÊNCIAS",
        f"CNS: {CNS_A}",
        "Paciente:",
        "Código: 33345",
        "Central Estadual de Regulação",
        "CNS:",
        "Dias em tela:",
        "Nome Social:",
    ]
    assert extract_patient_cns("\n".join(lines) + "\n") is None


def test_extra_slot_same_cns_is_ambiguity() -> None:
    lines = [
        "RELATÓRIO DE OCORRÊNCIAS",
        "Paciente:",
        "Código: 33345",
        "Central Estadual de Regulação",
        f"CNS: {CNS_A}",
        "Dias em tela:",
        "Central Estadual de Regulação",
        f"CNS: {CNS_A}",
        "Dias em tela:",
        "Nome Social:",
    ]
    assert extract_patient_cns("\n".join(lines) + "\n") is None


def test_distinct_valid_blocks_conflict_empty() -> None:
    other = _canonical(f"CNS: {CNS_B}")
    assert extract_patient_cns(_canonical() + other) is None


def test_invalid_nonempty_in_valid_slot_empties() -> None:
    assert extract_patient_cns(_canonical(f"CNS: {CNS_INVALID}")) is None


def test_narrative_invalid_does_not_taint_real_block() -> None:
    text = _canonical() + f"CNS: {CNS_INVALID}\nEvolução estável.\n"
    assert extract_patient_cns(text) == CNS_A


def test_narrative_valid_after_block_does_not_ambiguate() -> None:
    text = _canonical() + f"CNS: {CNS_B}\nEvolução estável.\n"
    assert extract_patient_cns(text) == CNS_A
