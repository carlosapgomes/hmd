"""Fixtures do app de anonimização (slice 002): CPF/CNS válidos gerados.

Valores fabricados algoritmicamente (checksum correto, dígitos não repetidos)
com ``random.Random`` de seed fixa — determinístico e sem PII real (R5/R6 da
matriz do slice). Nenhuma fixture toca banco ou modelo spaCy.
"""

from __future__ import annotations

import random

import pytest


def _cpf_check_digit(digits: list[int], check_position: int) -> int:
    """Dígito verificador de CPF na posição 9/10 (pesos decrescentes, 10 → 0).

    Espelho do algoritmo de ``apps.anonymization.recognizers.valid_cpf``: cada
    verificador usa os pesos ``check_position..1`` sobre os dígitos anteriores
    e ``(soma * 10) % 11`` com ``10 → 0``.
    """
    total = sum(digits[index] * (check_position + 1 - index) for index in range(check_position))
    expected = (total * 10) % 11
    return 0 if expected == 10 else expected


def _generate_valid_cpf(seed: int) -> str:
    """CPF de 11 dígitos com checksum correto (sem sequências repetidas)."""
    rng = random.Random(seed)
    base = [rng.randrange(10) for _ in range(9)]
    while len(set(base)) == 1:
        base = [rng.randrange(10) for _ in range(9)]
    first = _cpf_check_digit(base, 9)
    second = _cpf_check_digit(base + [first], 10)
    return "".join(str(digit) for digit in base + [first, second])


def _generate_valid_cns(seed: int) -> str:
    """CNS de 15 dígitos cuja soma ponderada 15..1 é múltipla de 11."""
    rng = random.Random(seed)
    while True:
        digits = [rng.randrange(10) for _ in range(15)]
        if len(set(digits)) == 1:
            continue
        weighted_sum = sum(digit * weight for digit, weight in zip(digits, range(15, 0, -1)))
        if weighted_sum % 11 == 0:
            return "".join(str(digit) for digit in digits)


@pytest.fixture
def generated_cpf() -> str:
    """CPF válido (11 dígitos, checksum correto) para os testes do slice."""
    return _generate_valid_cpf(seed=101)


@pytest.fixture
def generated_cpf_formatted() -> str:
    """O mesmo CPF do fixture ``generated_cpf`` pontuado (000.000.000-00)."""
    digits = _generate_valid_cpf(seed=101)
    return f"{digits[0:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:11]}"


@pytest.fixture
def generated_cns() -> str:
    """CNS válido (15 dígitos, soma ponderada múltipla de 11)."""
    return _generate_valid_cns(seed=202)
