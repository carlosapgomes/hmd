"""P1-04: matriz parametrizada do classificador compartilhado (fix-1).

Cobre D1 sem tocar banco: normalização de nome (acentos/whitespace), nome
obrigatório não-vazio, CNS inválido/vazio, precedência forte da regulação no
par, conflito de CNS, conjuntos efetivos iguais/independência de ordem e
multisset legado versus tipo distinto, e idades zero/None/igual/±1/±2 no par
forte e no par só-origem. O comportamento já está correto — estes testes
passam imediatamente (sem RED fabricado).
"""

from __future__ import annotations

import pytest

from apps.cases.reappearance import IdentitySignals, classify_pair

_CNS_A = "207139200176053"
_CNS_B = "605932160152243"
_CNS_INVALID = "207139200176054"


def _signals(
    *,
    name: str = "Maria da Silva",
    number: str = "",
    cns: str = "",
    origin: str = "",
    age: int | None = None,
    procedures: tuple[str, ...] = ("art_perif",),
) -> IdentitySignals:
    """Sinais de identidade com defaults do par canônico."""
    return IdentitySignals(
        name=name,
        record_number=number,
        patient_cns=cns,
        origin_unit=origin,
        patient_age=age,
        procedures=procedures,
    )


@pytest.mark.parametrize(
    ("current_name", "prior_name"),
    [
        ("José María  da Silva", "JOSE MARIA DA SILVA"),
        ("  Maria da Silva ", "MARIA   DA   SILVA"),
        ("Antônio Carlos de Souza", "ANTONIO CARLOS DE SOUZA"),
    ],
)
def test_name_normalization_accents_whitespace(current_name: str, prior_name: str) -> None:
    """D1: nome normalizado (acentos/caixa/espaços) com segunda evidência → forte."""
    current = _signals(name=current_name, number="123", cns="")
    prior = _signals(name=prior_name, number="123", cns="")
    result = classify_pair(current, prior)
    assert result.verdict == "strong"
    assert result.origin == "occurrence_number"


@pytest.mark.parametrize("empty_name", ["", "   "])
def test_empty_name_never_matches(empty_name: str) -> None:
    """D1: nome vazio nunca é igualdade — mesmo com regulação/CNS iguais."""
    current = _signals(name=empty_name, number="123", cns=_CNS_A)
    prior = _signals(name="Maria da Silva", number="123", cns=_CNS_A)
    assert classify_pair(current, prior).verdict == "no_match"
    assert classify_pair(prior, current).verdict == "no_match"


def test_invalid_or_empty_cns_is_not_evidence() -> None:
    """D1: CNS inválido/vazio não é segunda evidência; número igual ainda vale."""
    current = _signals(name="Maria da Silva", number="123", cns=_CNS_INVALID)
    prior = _signals(name="Maria da Silva", number="123", cns=_CNS_INVALID)
    result = classify_pair(current, prior)
    assert result.verdict == "strong"
    assert result.origin == "occurrence_number"

    current = _signals(name="Maria da Silva", number="999", cns=_CNS_INVALID)
    prior = _signals(name="Maria da Silva", number="888", cns=_CNS_INVALID)
    assert classify_pair(current, prior).verdict == "no_match"

    current = _signals(name="Maria da Silva", number="", cns="")
    prior = _signals(name="Maria da Silva", number="", cns="")
    assert classify_pair(current, prior).verdict == "no_match"


def test_strong_precedence_number_over_cns_within_pair() -> None:
    """D3: número e CNS iguais no MESMO par → origem ``occurrence_number``."""
    current = _signals(name="Maria da Silva", number="123", cns=_CNS_A)
    prior = _signals(name="Maria da Silva", number="123", cns=_CNS_A)
    result = classify_pair(current, prior)
    assert result.verdict == "strong"
    assert result.origin == "occurrence_number"


def test_distinct_valid_cns_conflicts_even_with_number() -> None:
    """D1: CNS válidos distintos, mesmo com regulação igual → conferência com conflito."""
    current = _signals(name="Maria da Silva", number="123", cns=_CNS_A)
    prior = _signals(name="Maria da Silva", number="123", cns=_CNS_B)
    result = classify_pair(current, prior)
    assert result.verdict == "needs_confirmation"
    assert result.origin is None
    assert result.conflict is True


def test_effective_sets_equal_order_independent() -> None:
    """D1: conjunto efetivo igual independe de ordem; vazio nunca casa."""
    current = _signals(
        name="Maria da Silva", number="123", procedures=("art_perif", "cat_cardiaco")
    )
    prior = _signals(name="Maria da Silva", number="123", procedures=("cat_cardiaco", "art_perif"))
    assert classify_pair(current, prior).verdict == "strong"

    current = _signals(name="Maria da Silva", number="123", procedures=())
    prior = _signals(name="Maria da Silva", number="123", procedures=())
    assert classify_pair(current, prior).verdict == "no_match"


def test_legacy_multiset_subset_and_different_type() -> None:
    """D1: legado multisset só casa com conjunto exatamente igual; tipo distinto nunca."""
    current = _signals(
        name="Maria da Silva", number="123", procedures=("art_perif", "cat_cardiaco")
    )
    prior = _signals(name="Maria da Silva", number="123", procedures=("art_perif",))
    assert classify_pair(current, prior).verdict == "no_match"

    current = _signals(name="Maria da Silva", number="123", procedures=("art_perif",))
    prior = _signals(name="Maria da Silva", number="123", procedures=("permicath",))
    assert classify_pair(current, prior).verdict == "no_match"


@pytest.mark.parametrize(
    ("current_age", "prior_age", "expected"),
    [
        (0, 0, "strong"),
        (0, 1, "strong"),
        (1, 0, "strong"),
        (None, None, "strong"),
        (None, 45, "strong"),
        (45, None, "strong"),
        (45, 45, "strong"),
        (45, 46, "strong"),
        (46, 45, "strong"),
        (45, 47, "needs_confirmation"),
        (47, 45, "needs_confirmation"),
        (0, 2, "needs_confirmation"),
    ],
)
def test_strong_pair_ages(current_age: int | None, prior_age: int | None, expected: str) -> None:
    """D1: par forte — zero é informada, None é ausente, tolerância ±1."""
    current = _signals(name="Maria da Silva", number="123", age=current_age)
    prior = _signals(name="Maria da Silva", number="123", age=prior_age)
    assert classify_pair(current, prior).verdict == expected


@pytest.mark.parametrize(
    ("current_age", "prior_age", "expected"),
    [
        (None, None, "needs_confirmation"),
        (None, 45, "needs_confirmation"),
        (45, None, "needs_confirmation"),
        (45, 45, "needs_confirmation"),
        (45, 46, "needs_confirmation"),
        (0, 0, "needs_confirmation"),
        (0, 1, "needs_confirmation"),
        (45, 47, "no_match"),
        (0, 2, "no_match"),
    ],
)
def test_origin_only_pair_ages(
    current_age: int | None, prior_age: int | None, expected: str
) -> None:
    """D1: par só-origem — idades ausentes/diff ≤1 → conferência; diff >1 → sem candidato."""
    current = _signals(name="Maria da Silva", origin="Hospital Geral", age=current_age)
    prior = _signals(name="Maria da Silva", origin="Hospital Geral", age=prior_age)
    result = classify_pair(current, prior)
    assert result.verdict == expected
    assert result.origin is None
