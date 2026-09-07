"""Schemas da base comum do artefato LLM1 (change llm-pipeline-per-type, slice
002, design D2 — R1/R2).

Contrato puro (sem LLM/DB): ``StrictModel`` (pydantic v2, ``extra="forbid"``),
``Llm1EvidenceSpan`` (field_path + excerpt), ``status`` tri-state
(``confirmado|nao_informado|incerto``) e a base comum ``Llm1CaseBase`` com os
campos clínicos (pedido/contexto clínico/linha do tempo/exames/medicações/
comorbidades/contraindicações/``trechos_nao_classificados``). Proveniência:
todo campo clínico **informado** (status != ``nao_informado``) exige
``evidence_spans`` não vazia (validator) — nunca completar ausência.

``pedido.procedimentos_solicitados`` usa ``ProcedimentoSolicitado``: Literal
**gerada a partir do catálogo** (``procedure_catalog.PROCEDURE_PROFILES``), não
hardcoded — a detecção do LLM1 fica livre sobre os 13 tipos (correção do
review D2). Nomes de medicamentos são texto (não-PII no change 05).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from apps.cases.procedure_catalog import PROCEDURE_PROFILES

# Limites do contrato (padrão ats-web, R1).
MAX_EVIDENCE_FIELD_PATH_LENGTH = 120
MAX_EVIDENCE_EXCERPT_LENGTH = 200
MAX_CLINICAL_TEXT_LENGTH = 300
MAX_TIMELINE_TEXT_LENGTH = 400
MAX_CONTEXT_TEXT_LENGTH = 2000

# Texto obrigatório, sem espaços apenas (strip antes de validar o min_length).
ShortNonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]
LongNonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CLINICAL_TEXT_LENGTH),
]
TimelineNonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TIMELINE_TEXT_LENGTH),
]
ExcerptNonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_EVIDENCE_EXCERPT_LENGTH),
]
EvidencePathText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_EVIDENCE_FIELD_PATH_LENGTH
    ),
]


class StrictModel(BaseModel):
    """Modelo estrito pydantic v2: qualquer campo fora do contrato é rejeitado (R1)."""

    model_config = ConfigDict(extra="forbid")


# Status tri-state compartilhado (R1): nunca completar informação ausente.
Llm1Status = Literal["confirmado", "nao_informado", "incerto"]


# Literal dos 13 tipos gerada a partir do catálogo (R4). ``Any`` apenas para o
# checker (o valor em runtime é a ``Literal`` completa — a restrição de enum
# vale na validação e no JSON Schema do pedido).
_CATALOG_PROCEDURE_TYPES: tuple[str, ...] = tuple(
    profile.procedure_type for profile in PROCEDURE_PROFILES
)
ProcedimentoSolicitado: Any = Literal[_CATALOG_PROCEDURE_TYPES]


class Llm1EvidenceSpan(StrictModel):
    """Proveniência de um campo extraído: trecho citado do documento (R1).

    ``field_path`` localiza o campo sustentado (ex.: ``exames.platelets``) e
    ``excerpt`` é o trecho anonimizado citado. Texto só de espaços é rejeitado.
    """

    field_path: EvidencePathText
    excerpt: ExcerptNonBlankText


class Llm1ClinicalClaim(StrictModel):
    """Afirmação clínica com proveniência obrigatória (R1/R2).

    - ``status="confirmado"``: informação presente no documento;
    - ``status="incerto"``: indício sem confirmação;
    - ``status="nao_informado"``: ausente do documento (sem evidência exigida).

    Campo clínico **informado** sem ``evidence_spans`` é erro de validação.
    """

    status: Llm1Status
    evidence_spans: list[Llm1EvidenceSpan] = Field(default_factory=list)

    @model_validator(mode="after")
    def _evidence_required_when_informed(self) -> Self:
        if self.status != "nao_informado" and not self.evidence_spans:
            raise ValueError(
                "campo clínico informado (status != 'nao_informado') exige evidence_spans não vazia"
            )
        return self


class Llm1AspectClaim(Llm1ClinicalClaim):
    """Aspecto clínico específico de um tipo (usado pelos blocos R3).

    ``detail`` (opcional) descreve, em tokens, o que o documento diz sobre o
    aspecto — nunca substitui a evidência.
    """

    detail: str | None = Field(default=None, max_length=MAX_CLINICAL_TEXT_LENGTH)


class Llm1Comorbidity(Llm1ClinicalClaim):
    """Comorbidade descrita no documento."""

    name: ShortNonBlankText


class Llm1Medication(Llm1ClinicalClaim):
    """Medicação descrita no documento (nomes de fármacos não são PII).

    ``drug_class`` marca anticoagulante/antiagregante **quando houver**
    (requisitos gerais da policy, change 005); demais medicações ficam sem
    classe.
    """

    name: ShortNonBlankText
    drug_class: Literal["anticoagulante", "antiagregante"] | None = None


class Llm1Contraindication(Llm1ClinicalClaim):
    """Contraindicação descrita no documento."""

    description: LongNonBlankText


class Llm1TimelineEntry(Llm1ClinicalClaim):
    """Evento da linha do tempo do caso (ordem cronológica da fonte)."""

    description: TimelineNonBlankText


class Llm1LabResult(Llm1ClinicalClaim):
    """Resultado objetivo de exame: valor + unidade + proveniência (R2).

    ``status != nao_informado`` exige ``value`` preenchido (nunca completar
    ausência com valor inventado); ``nao_informado`` não pode trazer valor.
    """

    value: float | None = None
    unit: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _value_matches_status(self) -> Self:
        if self.status == "nao_informado":
            if self.value is not None:
                raise ValueError("status 'nao_informado' não pode trazer value")
        elif self.value is None:
            raise ValueError("exame informado (confirmado/incerto) exige value numérico")
        return self


class Llm1Exames(StrictModel):
    """Exames com resultados objetivos — chaves determinísticas por exame.

    As chaves espelham os critérios numéricos das seções S1–S8 do catálogo
    (Plt/INR/Hb/Cr/PAS/glicemia/K), dando caminho estável para a tabela de
    thresholds da policy (change 005). Exame ausente do documento = ``None``.
    """

    platelets: Llm1LabResult | None = None
    inr: Llm1LabResult | None = None
    hemoglobin: Llm1LabResult | None = None
    creatinine: Llm1LabResult | None = None
    glucose: Llm1LabResult | None = None
    potassium: Llm1LabResult | None = None
    systolic_blood_pressure: Llm1LabResult | None = None


class Llm1Pedido(StrictModel):
    """Pedido: procedimentos como tokens/labels (R2/R4).

    ``procedimentos_solicitados`` aceita **qualquer** tipo do catálogo (13) —
    a detecção não é restringida aos tipos declarados (correção do review D2).
    Lista preenchida exige evidência (detecção com proveniência).
    """

    procedimentos_solicitados: list[ProcedimentoSolicitado] = Field(default_factory=list)
    evidence_spans: list[Llm1EvidenceSpan] = Field(default_factory=list)

    @model_validator(mode="after")
    def _detection_requires_evidence(self) -> Self:
        if self.procedimentos_solicitados and not self.evidence_spans:
            raise ValueError("procedimentos_solicitados preenchido exige evidence_spans não vazia")
        return self


class Llm1CaseBase(StrictModel):
    """Base comum do artefato LLM1 — campos clínicos com evidência (R2).

    Um único conjunto por caso; blocos específicos por tipo entram na
    composição união (``build_llm1_schema``) como campos adicionais desta
    base.
    """

    pedido: Llm1Pedido
    contexto_clinico: str | None = Field(default=None, max_length=MAX_CONTEXT_TEXT_LENGTH)
    linha_do_tempo: list[Llm1TimelineEntry] = Field(default_factory=list)
    exames: Llm1Exames | None = None
    medicacoes: list[Llm1Medication] = Field(default_factory=list)
    comorbidades: list[Llm1Comorbidity] = Field(default_factory=list)
    contraindicacoes: list[Llm1Contraindication] = Field(default_factory=list)
    trechos_nao_classificados: list[str] = Field(default_factory=list)
