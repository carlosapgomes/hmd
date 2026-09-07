"""Schema da resposta LLM2 (sumarização) — change llm-pipeline-per-type, slice
002, design D2 — R5.

``Llm2Response`` valida a saída da sumarização: ``summary_text`` + uma
sugestão ``aceitar|recusar`` por procedimento com motivos e evidência.
A policy determinística (change 005) prevalece sobre a sugestão do LLM na
reconciliação final (D8) — o schema valida apenas o contrato de saída.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from apps.pipeline.schemas.base import (
    Llm1EvidenceSpan,
    ProcedimentoSolicitado,
    StrictModel,
)

MAX_SUMMARY_TEXT_LENGTH = 4000

Llm2Suggestion = Literal["aceitar", "recusar"]


class Llm2ProcedureRecommendation(StrictModel):
    """Sugestão para exatamente um procedimento (R5)."""

    procedure_type: ProcedimentoSolicitado
    suggestion: Llm2Suggestion
    motivos: list[str] = Field(default_factory=list)
    evidence_spans: list[Llm1EvidenceSpan] = Field(default_factory=list)


class Llm2Response(StrictModel):
    """Resposta da sumarização — sem duplicatas de procedimento (R5)."""

    summary_text: str = Field(min_length=1, max_length=MAX_SUMMARY_TEXT_LENGTH)
    procedures: list[Llm2ProcedureRecommendation] = Field(min_length=1)

    @model_validator(mode="after")
    def _reject_duplicate_procedure_types(self) -> Self:
        seen: set[str] = set()
        for recommendation in self.procedures:
            if recommendation.procedure_type in seen:
                raise ValueError(
                    f"procedures contém duplicata do tipo: {recommendation.procedure_type!r}"
                )
            seen.add(recommendation.procedure_type)
        return self
