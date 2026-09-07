"""Testes dos validadores e recognizers BR (slice 002, R2/R3/R5).

Puros e rápidos: validam funções/recognizers diretamente (``analyze``) sem
carregar o modelo spaCy — o carregamento vive nos testes do engine
(``test_engine.py``). Valores de CPF/CNS são gerados algoritmicamente nas
fixtures (nunca PII real) e o contrato central é: valor com checksum inválido
NUNCA é detectado.
"""

from __future__ import annotations

from apps.anonymization.recognizers import (
    CnsRecognizer,
    CpfRecognizer,
    CrmRecognizer,
    valid_cns,
    valid_cpf,
)


def _cpf_with_wrong_check_digit(valid: str) -> str:
    """CPF com o último dígito verificador trocado (checksum inválido)."""
    return valid[:-1] + str((int(valid[-1]) + 1) % 10)


# ── R2: validadores puros ─────────────────────────────────────────────────


def test_valid_cpf_generated(generated_cpf: str, generated_cpf_formatted: str) -> None:
    """R2: CPF gerado com checksum correto valida com e sem pontuação."""
    assert generated_cpf.isdigit() and len(generated_cpf) == 11
    assert valid_cpf(generated_cpf) is True
    assert valid_cpf(generated_cpf_formatted) is True


def test_invalid_cpf_rejected(generated_cpf: str) -> None:
    """R2: dígito verificador errado e sequências repetidas são rejeitados."""
    wrong = _cpf_with_wrong_check_digit(generated_cpf)
    assert valid_cpf(wrong) is False
    assert valid_cpf("111.111.111-11") is False
    assert valid_cpf("12345678901") is False  # 11 dígitos, checksum errado
    assert valid_cpf("12345") is False  # tamanho inválido
    assert valid_cpf("") is False


def test_valid_cns_generated(generated_cns: str) -> None:
    """R2: CNS gerado (soma ponderada 15..1 múltipla de 11) valida."""
    assert generated_cns.isdigit() and len(generated_cns) == 15
    assert valid_cns(generated_cns) is True


def test_invalid_cns_rejected(generated_cns: str) -> None:
    """R2: CNS com soma ponderada fora do múltiplo e repetidos são rejeitados."""
    wrong = generated_cns[:-1] + str((int(generated_cns[-1]) + 1) % 10)
    assert valid_cns(wrong) is False
    assert valid_cns("111111111111111") is False
    assert valid_cns("123456789012345") is False  # 15 dígitos, soma não múltipla
    assert valid_cns("12345") is False  # tamanho inválido
    assert valid_cns("") is False


# ── R3/R5: recognizers só emitem resultado com checksum válido ────────────


def test_cpf_recognizer_detects_valid_only(
    generated_cpf: str, generated_cpf_formatted: str
) -> None:
    """R3/R5: CPF válido (com e sem pontuação) detectado; inválido nunca."""
    recognizer = CpfRecognizer()

    text = f"Documento {generated_cpf_formatted} e também {generated_cpf} anexados."
    results = recognizer.analyze(text=text, entities=["BR_CPF"])
    matched = [text[result.start : result.end] for result in results]
    assert [result.entity_type for result in results] == ["BR_CPF", "BR_CPF"]
    assert generated_cpf_formatted in matched
    assert generated_cpf in matched

    wrong = _cpf_with_wrong_check_digit(generated_cpf)
    wrong_text = f"Documento {wrong} anexado."
    assert recognizer.analyze(text=wrong_text, entities=["BR_CPF"]) == []
    assert recognizer.analyze(text="Número 111.111.111-11 repetido.", entities=["BR_CPF"]) == []


def test_cpf_recognizer_ignores_adjacent_digits(generated_cpf: str) -> None:
    """R5: lookarounds — dígito vizinho contíguo não vira CPF."""
    recognizer = CpfRecognizer()

    assert recognizer.analyze(text=f"Código {generated_cpf}7", entities=["BR_CPF"]) == []
    assert recognizer.analyze(text=f"Código 9{generated_cpf}", entities=["BR_CPF"]) == []


def test_cns_recognizer_detects_valid_only(generated_cns: str) -> None:
    """R3/R5: CNS válido detectado; checksum errado e repetidos nunca."""
    recognizer = CnsRecognizer()

    text = f"Cartão {generated_cns} do paciente."
    results = recognizer.analyze(text=text, entities=["BR_CNS"])
    assert [result.entity_type for result in results] == ["BR_CNS"]
    assert [text[result.start : result.end] for result in results] == [generated_cns]

    wrong = generated_cns[:-1] + str((int(generated_cns[-1]) + 1) % 10)
    assert recognizer.analyze(text=f"Cartão {wrong} do paciente.", entities=["BR_CNS"]) == []
    assert recognizer.analyze(text="Cartão 111111111111111 repetido.", entities=["BR_CNS"]) == []


def test_cns_recognizer_ignores_adjacent_digits(generated_cns: str) -> None:
    """R5: lookarounds — CNS dentro de run de 16 dígitos não é detectado."""
    recognizer = CnsRecognizer()

    assert recognizer.analyze(text=f"Run {generated_cns}9", entities=["BR_CNS"]) == []


def test_crm_formats() -> None:
    """R5: CRM nos formatos com UF (CRM-UF n / CRM/UF n / n-UF) e sem UF."""
    text = (
        "Responsável: Dr. João, CRM-BA 12345. "
        "A Dra. Ana (CRM/SP 123456) assinou; CRM 987654 consta no laudo e "
        "o CRM 54321-RJ complementa."
    )
    results = CrmRecognizer().analyze(text=text, entities=["BR_CRM"])
    matched = [text[result.start : result.end] for result in results]

    assert "CRM-BA 12345" in matched
    assert "CRM/SP 123456" in matched
    assert "CRM 987654" in matched
    assert "CRM 54321-RJ" in matched
    assert [result.entity_type for result in results] == ["BR_CRM"] * 4


def test_crm_recognizer_ignores_embedded_word() -> None:
    """R5: lookbehind — CRM dentro de palavra maior (MCRM) não é detectado."""
    text = "A sigla MCRM 12345 não deve casar como CRM."
    results = CrmRecognizer().analyze(text=text, entities=["BR_CRM"])
    assert results == []
