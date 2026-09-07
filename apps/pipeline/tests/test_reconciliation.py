"""Testes da reconciliação declarado × detectado (slice 004, R3/D5).

Cobre o contrato puro de ``reconcile_procedures``: classificação por tipo
(``match | missing_declaration | not_detected``) na ordem canônica do
catálogo, derivação da divergência e rejeição de tipos fora do catálogo.
"""

from __future__ import annotations

import pytest

from apps.pipeline.procedure_reconciliation import reconcile_procedures


def test_classification() -> None:
    """R3/R8: matriz declarado × detectado classificada por tipo.

    ``art_perif`` declarado e não detectado → ``not_detected``;
    ``cat_cardiaco`` em ambos → ``match``; ``nefrostomia`` detectado sem
    declaração → ``missing_declaration``.
    """
    result = reconcile_procedures(
        declared=("art_perif", "cat_cardiaco"),
        detected=("cat_cardiaco", "nefrostomia"),
    )

    assert result.match == ("cat_cardiaco",)
    assert result.missing_declaration == ("nefrostomia",)
    assert result.not_detected == ("art_perif",)
    assert result.has_divergence is True
    assert result.classification() == {
        "art_perif": "not_detected",
        "cat_cardiaco": "match",
        "nefrostomia": "missing_declaration",
    }


def test_full_match_no_divergence() -> None:
    """Declaração e detecção coincidem → sem divergência, tudo ``match``."""
    result = reconcile_procedures(("filtro_cava",), ("filtro_cava",))

    assert result.match == ("filtro_cava",)
    assert result.missing_declaration == ()
    assert result.not_detected == ()
    assert result.has_divergence is False


def test_canonical_order_independent_of_input_order() -> None:
    """Conjuntos de saída seguem a ordem canônica do catálogo (entrada fora de ordem)."""
    result = reconcile_procedures(
        declared=("nefrostomia", "art_perif"),
        detected=("art_perif", "flebografia", "nefrostomia"),
    )

    assert result.match == ("art_perif", "nefrostomia")
    assert result.missing_declaration == ("flebografia",)
    assert result.not_detected == ()
    assert result.classification() == {
        "art_perif": "match",
        "flebografia": "missing_declaration",
        "nefrostomia": "match",
    }


def test_out_of_catalog_rejected() -> None:
    """Tipo fora do catálogo é rejeitado nomeando o tipo (fail-fast)."""
    with pytest.raises(ValueError, match="fora do catálogo"):
        reconcile_procedures(("art_perif",), ("endoscopia",))
    with pytest.raises(ValueError, match="fora do catálogo"):
        reconcile_procedures(("art_perif", "inexistente"), ("art_perif",))
