"""Reconciliação declarado × detectado (slice 004, R3/D5).

Função pura ``reconcile_procedures``: classifica cada tipo da união
(declarado ∪ detectado) como ``match`` (em ambos), ``missing_declaration``
(detectado sem declaração) ou ``not_detected`` (declarado sem detecção) —
na ordem canônica do catálogo, independente da ordem de entrada. Tipos fora
do catálogo são rejeitados nomeando o tipo (fail-fast, mesmo contrato do
catálogo do change 03).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from apps.cases.procedure_catalog import PROCEDURE_PROFILES

_CATALOG_ORDER: dict[str, int] = {
    profile.procedure_type: index for index, profile in enumerate(PROCEDURE_PROFILES)
}

_CLASS_MATCH = "match"
_CLASS_MISSING_DECLARATION = "missing_declaration"
_CLASS_NOT_DETECTED = "not_detected"


def _ordered(procedure_types: Iterable[str]) -> tuple[str, ...]:
    """Valida contra o catálogo e ordena na ordem canônica (sem duplicatas)."""
    seen: set[str] = set()
    for procedure_type in procedure_types:
        if procedure_type not in _CATALOG_ORDER:
            raise ValueError(f"procedimento fora do catálogo: {procedure_type!r}")
        seen.add(procedure_type)
    return tuple(sorted(seen, key=_CATALOG_ORDER.__getitem__))


@dataclass(frozen=True)
class ReconciliationResult:
    """Classificação da matriz declarado × detectado (R3).

    - ``match``: declarado E detectado;
    - ``missing_declaration``: detectado sem declaração (tipo novo no caso);
    - ``not_detected``: declarado sem detecção (ausente do artefato).

    Conjuntos sempre na ordem canônica do catálogo.
    """

    match: tuple[str, ...]
    missing_declaration: tuple[str, ...]
    not_detected: tuple[str, ...]

    @property
    def has_divergence(self) -> bool:
        """Qualquer ``missing_declaration``/``not_detected`` = divergência."""
        return bool(self.missing_declaration or self.not_detected)

    def classification(self) -> dict[str, str]:
        """Mapa ``tipo → classe`` sobre declarado ∪ detectado, ordem canônica.

        Usado no payload enxuto do evento ``CASE_GATE_PROCEDURE_DIVERGENCE``
        (classificação por tipo — nunca conteúdo clínico bruto).
        """
        match_set = set(self.match)
        missing_set = set(self.missing_declaration)
        union = sorted(
            match_set | missing_set | set(self.not_detected), key=_CATALOG_ORDER.__getitem__
        )
        classified: dict[str, str] = {}
        for procedure_type in union:
            if procedure_type in match_set:
                classified[procedure_type] = _CLASS_MATCH
            elif procedure_type in missing_set:
                classified[procedure_type] = _CLASS_MISSING_DECLARATION
            else:
                classified[procedure_type] = _CLASS_NOT_DETECTED
        return classified


def reconcile_procedures(
    declared: Iterable[str],
    detected: Iterable[str],
) -> ReconciliationResult:
    """Reconcilia os conjuntos declarado e detectado (pura, R3).

    Ordena cada conjunto na ordem canônica do catálogo e classifica a união
    por tipo. Tipo fora do catálogo (em qualquer conjunto) levanta
    ``ValueError`` nomeando o tipo.
    """
    declared_ordered = _ordered(declared)
    detected_ordered = _ordered(detected)
    declared_set = set(declared_ordered)
    detected_set = set(detected_ordered)

    matched = tuple(
        procedure_type for procedure_type in declared_ordered if procedure_type in detected_set
    )
    missing = tuple(
        procedure_type for procedure_type in detected_ordered if procedure_type not in declared_set
    )
    not_detected = tuple(
        procedure_type for procedure_type in declared_ordered if procedure_type not in detected_set
    )
    return ReconciliationResult(
        match=matched,
        missing_declaration=missing,
        not_detected=not_detected,
    )
