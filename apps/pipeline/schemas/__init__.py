"""Schemas do pipeline LLM por tipo (change llm-pipeline-per-type, slice 002,
design D2).

Contrato puro (sem LLM/DB): base comum + blocos específicos + composição
**união** ``build_llm1_schema(types)`` (uma chamada LLM1 por caso, com os
blocos apenas dos tipos declarados), schema da resposta LLM2 e a normalização
``oneOf→anyOf`` para o ``response_format`` strict.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.pipeline.schemas.adapters import normalize_schema_for_response_format
from apps.pipeline.schemas.base import (
    Llm1AspectClaim,
    Llm1CaseBase,
    Llm1ClinicalClaim,
    Llm1Comorbidity,
    Llm1Contraindication,
    Llm1EvidenceSpan,
    Llm1Exames,
    Llm1LabResult,
    Llm1Medication,
    Llm1Pedido,
    Llm1Status,
    Llm1TimelineEntry,
    ProcedimentoSolicitado,
    StrictModel,
)
from apps.pipeline.schemas.blocks import (
    PROCEDURE_SPECIFIC_BLOCKS,
    AngioFavBlock,
    FiltroCavaBlock,
    PermicathBlock,
)
from apps.pipeline.schemas.llm2 import Llm2ProcedureRecommendation, Llm2Response

__all__ = [
    "AngioFavBlock",
    "FiltroCavaBlock",
    "Llm1AspectClaim",
    "Llm1CaseBase",
    "Llm1ClinicalClaim",
    "Llm1Comorbidity",
    "Llm1Contraindication",
    "Llm1EvidenceSpan",
    "Llm1Exames",
    "Llm1LabResult",
    "Llm1Medication",
    "Llm1Pedido",
    "Llm1Status",
    "Llm1TimelineEntry",
    "Llm2ProcedureRecommendation",
    "Llm2Response",
    "PermicathBlock",
    "ProcedimentoSolicitado",
    "PROCEDURE_SPECIFIC_BLOCKS",
    "StrictModel",
    "build_llm1_schema",
    "normalize_schema_for_response_format",
]

_CATALOG_TYPES: tuple[str, ...] = tuple(profile.procedure_type for profile in PROCEDURE_PROFILES)
_CATALOG_SET = frozenset(_CATALOG_TYPES)

# Nome estável do modelo da união (não vira chave de contrato; o schema de
# cada chamada é derivado dos tipos declarados).
_LLM1_UNION_NAME = "Llm1CaseArtifact"


def build_llm1_schema(types: Sequence[str]) -> type[StrictModel]:
    """Modelo LLM1 da união para os tipos declarados (R4).

    Base comum + um campo por tipo com bloco específico (nested). Tipos fora
    do catálogo, duplicatas ou lista vazia → ``ValueError``. Os blocos
    clínicos profundos vêm dos tipos declarados; o campo
    ``pedido.procedimentos_solicitados`` continua aceitando qualquer um dos
    13 tipos (detecção livre no catálogo).
    """
    validated = _validate_declared_types(types)
    block_annotations: dict[str, Any] = {
        procedure_type: PROCEDURE_SPECIFIC_BLOCKS[procedure_type]
        for procedure_type in validated
        if procedure_type in PROCEDURE_SPECIFIC_BLOCKS
    }
    union = type(
        _LLM1_UNION_NAME,
        (Llm1CaseBase,),
        {"__annotations__": block_annotations},
    )
    return cast(type[StrictModel], union)


def _validate_declared_types(types: Sequence[str]) -> tuple[str, ...]:
    """Valida os tipos declarados: não-vazio, no catálogo e sem duplicatas."""
    if not types:
        raise ValueError("build_llm1_schema exige ao menos um tipo declarado")
    seen: list[str] = []
    for procedure_type in types:
        if procedure_type not in _CATALOG_SET:
            raise ValueError(f"tipo fora do catálogo: {procedure_type!r}")
        if procedure_type in seen:
            raise ValueError(f"duplicata de tipo na declaração: {procedure_type!r}")
        seen.append(procedure_type)
    return tuple(seen)
