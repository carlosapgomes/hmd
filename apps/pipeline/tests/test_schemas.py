"""Testes dos schemas do pipeline LLM por tipo (slice 002, R1–R7).

Cobre o contrato **puro** (sem LLM/DB) de ``apps/pipeline/schemas``:

- R1/R2: ``StrictModel`` rejeita campos extras; ``status`` tri-state
  (``confirmado|nao_informado|incerto``); todo campo clínico informado exige
  ``evidence_spans`` (validator); ``Llm1EvidenceSpan`` (field_path + excerpt);
- R3: blocos específicos apenas dos 3 tipos nomeados no plano (angio_fav,
  filtro_cava, permicath) — os demais 10 tipos usam somente a base comum;
- R4: ``build_llm1_schema(types)`` compõe base + blocos dos tipos declarados;
  ``pedido.procedimentos_solicitados`` aceita QUALQUER tipo do catálogo (13),
  tipo fora do catálogo/duplicata → erro;
- R5: ``Llm2Response`` (summary_text + sugestão por procedimento) valida a
  saída da sumarização;
- R6: ``normalize_schema_for_response_format`` — oneOf→anyOf, required com
  todos os campos e ``additionalProperties: false`` em todos os níveis;
- R7: round-trip de payloads JSON válidos de exemplo (valores clínicos
  fictícios — sem PII real).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, get_args

import pytest
from pydantic import Field, ValidationError

from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.pipeline.schemas import build_llm1_schema
from apps.pipeline.schemas.adapters import normalize_schema_for_response_format
from apps.pipeline.schemas.base import (
    Llm1AspectClaim,
    Llm1CaseBase,
    Llm1ClinicalClaim,
    Llm1EvidenceSpan,
    Llm1Pedido,
    Llm1Status,
    StrictModel,
)
from apps.pipeline.schemas.blocks import PROCEDURE_SPECIFIC_BLOCKS
from apps.pipeline.schemas.llm2 import Llm2ProcedureRecommendation, Llm2Response

CATALOG_TYPES: tuple[str, ...] = tuple(p.procedure_type for p in PROCEDURE_PROFILES)

SPECIFIC_ASPECT_FIELDS: dict[str, tuple[str, ...]] = {
    "angio_fav": ("access_state",),
    "filtro_cava": ("tep", "anticoagulation_contraindication"),
    "permicath": ("active_infection",),
}


def _span(
    field_path: str = "pedido.procedimentos_solicitados", excerpt: str = "trecho do documento"
) -> dict[str, str]:
    return {"field_path": field_path, "excerpt": excerpt}


def _claim_payload(status: str = "confirmado") -> dict[str, Any]:
    return {"status": status, "evidence_spans": [_span("medicacoes", "medicação citada")]}


def _case_base_payload() -> dict[str, Any]:
    """Payload mínimo válido da base comum (sem os blocos específicos)."""
    return {
        "pedido": {
            "procedimentos_solicitados": ["angio_fav", "permicath"],
            "evidence_spans": [
                {
                    "field_path": "pedido.procedimentos_solicitados",
                    "excerpt": "solicito angioplastia de fístula",
                }
            ],
        },
        "contexto_clinico": "Paciente em hemodiálise, fístula arteriovenosa em membro superior esquerdo.",
        "linha_do_tempo": [
            {
                "description": "confecção da fístula em membro superior esquerdo",
                "status": "confirmado",
                "evidence_spans": [_span("linha_do_tempo", "relatório de cirurgia vascular")],
            }
        ],
        "exames": {
            "platelets": {
                "value": 180000,
                "unit": "mm3",
                "status": "confirmado",
                "evidence_spans": [_span("exames.platelets", "plaquetas 180 mil")],
            },
            "inr": None,
            "hemoglobin": None,
            "creatinine": None,
            "glucose": None,
            "potassium": None,
            "systolic_blood_pressure": None,
        },
        "medicacoes": [
            {"name": "Marevan", "drug_class": "anticoagulante", **_claim_payload()},
        ],
        "comorbidades": [
            {"name": "Doença renal crônica", **_claim_payload("incerto")},
        ],
        "contraindicacoes": [],
        "trechos_nao_classificados": [],
    }


class TestStrictModel:
    """R1/R7: modelo estrito rejeita campos extras em qualquer nível."""

    def test_strict_extra_forbidden(self) -> None:
        payload: dict[str, Any] = {**_case_base_payload(), "campo_inventado": "x"}
        with pytest.raises(ValidationError):
            Llm1CaseBase.model_validate(payload)

    def test_nested_model_extra_forbidden(self) -> None:
        payload: dict[str, Any] = {
            **_case_base_payload(),
            "pedido": {
                **_case_base_payload()["pedido"],
                "procedimentos_extra": ["angio_fav"],
            },
        }
        with pytest.raises(ValidationError):
            Llm1CaseBase.model_validate(payload)


class TestStatusEnum:
    """R1: status tri-state compartilhado; valor inválido é rejeitado."""

    def test_status_enum(self) -> None:
        for status in ("confirmado", "incerto"):
            claim = Llm1ClinicalClaim(
                status=status,
                evidence_spans=[
                    Llm1EvidenceSpan(field_path="medicacoes", excerpt="medicação citada")
                ],
            )
            assert claim.status == status
        uninformed = Llm1ClinicalClaim(status="nao_informado", evidence_spans=[])
        assert uninformed.status == "nao_informado"

        with pytest.raises(ValidationError):
            Llm1ClinicalClaim.model_validate({"status": "talvez", "evidence_spans": []})

    def test_status_type_is_shared_literal(self) -> None:
        assert get_args(Llm1Status) == ("confirmado", "nao_informado", "incerto")


class TestEvidenceRequirement:
    """R2: campo clínico preenchido sem evidence → erro de validação."""

    def test_clinical_field_requires_evidence(self) -> None:
        payload: dict[str, Any] = {
            **_case_base_payload(),
            "medicacoes": [
                {"name": "Marevan", "drug_class": "anticoagulante", "status": "confirmado"}
            ],
        }
        with pytest.raises(ValidationError, match="evidence"):
            Llm1CaseBase.model_validate(payload)

    def test_lab_result_informed_without_evidence_rejected(self) -> None:
        payload: dict[str, Any] = {
            **_case_base_payload(),
            "exames": {
                "platelets": {"value": 180000, "unit": "mm3", "status": "confirmado"},
                "inr": None,
                "hemoglobin": None,
                "creatinine": None,
                "glucose": None,
                "potassium": None,
                "systolic_blood_pressure": None,
            },
        }
        with pytest.raises(ValidationError, match="evidence"):
            Llm1CaseBase.model_validate(payload)

    def test_evidence_span_rejects_blank_texts(self) -> None:
        with pytest.raises(ValidationError):
            Llm1EvidenceSpan(field_path="  ", excerpt="trecho")
        with pytest.raises(ValidationError):
            Llm1EvidenceSpan(field_path="pedido", excerpt="   ")

    def test_nao_informado_does_not_require_evidence(self) -> None:
        payload: dict[str, Any] = {
            **_case_base_payload(),
            "medicacoes": [{"name": "Marevan", "status": "nao_informado"}],
        }
        parsed = Llm1CaseBase.model_validate(payload)
        assert parsed.medicacoes[0].status == "nao_informado"


class TestPedidoCatalogLiteral:
    """R4: detecção livre no catálogo — o schema do pedido aceita os 13 tipos."""

    def test_schema_enum_covers_full_catalog(self) -> None:
        schema = Llm1Pedido.model_json_schema()
        enum = schema["properties"]["procedimentos_solicitados"]["items"]["enum"]
        assert sorted(enum) == sorted(CATALOG_TYPES)
        assert len(enum) == 13

    def test_accepts_any_catalog_type(self) -> None:
        pedido = Llm1Pedido.model_validate(
            {"procedimentos_solicitados": ["art_cerebral"], "evidence_spans": [_span()]}
        )
        assert pedido.procedimentos_solicitados == ["art_cerebral"]

    def test_unknown_type_rejected_in_pedido(self) -> None:
        with pytest.raises(ValidationError):
            Llm1Pedido.model_validate(
                {
                    "procedimentos_solicitados": ["procedimento_inventado"],
                    "evidence_spans": [_span()],
                }
            )


class TestSpecificBlocksPerType:
    """R3: blocos específicos só para os 3 tipos nomeados no plano."""

    @pytest.mark.parametrize("procedure_type", CATALOG_TYPES)
    def test_specific_blocks_per_type(self, procedure_type: str) -> None:
        model = build_llm1_schema([procedure_type])
        base_fields = set(Llm1CaseBase.model_fields)
        extra_fields = set(model.model_fields) - base_fields

        if procedure_type in PROCEDURE_SPECIFIC_BLOCKS:
            assert extra_fields == {procedure_type}
            block_annotation = model.model_fields[procedure_type].annotation
            assert block_annotation is not None
            assert block_annotation is PROCEDURE_SPECIFIC_BLOCKS[procedure_type]
            expected_aspects = SPECIFIC_ASPECT_FIELDS[procedure_type]
            assert expected_aspects
            for aspect in expected_aspects:
                assert aspect in block_annotation.model_fields
        else:
            # Os demais 10 tipos usam somente a base comum — nada inventado.
            assert extra_fields == set()

    def test_block_aspects_are_clinical_claims(self) -> None:
        from apps.pipeline.schemas.blocks import AngioFavBlock, FiltroCavaBlock, PermicathBlock

        for block in (AngioFavBlock, FiltroCavaBlock, PermicathBlock):
            for field_name in block.model_fields:
                aspect_annotation = block.model_fields[field_name].annotation
                assert aspect_annotation is not None
                assert issubclass(aspect_annotation, Llm1AspectClaim)
                assert "status" in aspect_annotation.model_fields
                assert "evidence_spans" in aspect_annotation.model_fields


class TestUnionSchema:
    """R4: união de base + blocos dos tipos declarados."""

    def test_union_single(self) -> None:
        model = build_llm1_schema(["angio_fav"])
        assert issubclass(model, Llm1CaseBase)
        fields = set(model.model_fields)
        assert {"pedido", "contexto_clinico", "exames", "angio_fav"} <= fields
        assert "permicath" not in fields

    def test_union_three_types(self) -> None:
        model = build_llm1_schema(["angio_fav", "permicath", "filtro_cava"])
        fields = set(model.model_fields)
        assert {"angio_fav", "permicath", "filtro_cava"} <= fields
        # Fields de cada um dos 3 blocos presentes no modelo da união.
        angio_annotation = model.model_fields["angio_fav"].annotation
        assert angio_annotation is not None
        assert angio_annotation.model_fields["access_state"]
        filtro_annotation = model.model_fields["filtro_cava"].annotation
        assert filtro_annotation is not None
        assert filtro_annotation.model_fields["tep"]
        assert filtro_annotation.model_fields["anticoagulation_contraindication"]
        permicath_annotation = model.model_fields["permicath"].annotation
        assert permicath_annotation is not None
        assert permicath_annotation.model_fields["active_infection"]

    def test_union_unknown_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="cat[áa]logo"):
            build_llm1_schema(["art_perif", "nao_existe"])

    def test_duplicate_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicata"):
            build_llm1_schema(["angio_fav", "angio_fav"])

    def test_empty_types_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_llm1_schema([])


class TestLlm2Response:
    """R5: schema da sumarização (LLM2)."""

    def _valid_payload(self) -> dict[str, Any]:
        return {
            "summary_text": "Caso sem contraindicações para o procedimento solicitado.",
            "procedures": [
                {
                    "procedure_type": "angio_fav",
                    "suggestion": "aceitar",
                    "motivos": ["parâmetros dentro da faixa"],
                    "evidence_spans": [_span()],
                }
            ],
        }

    def test_llm2_response_valid(self) -> None:
        response = Llm2Response.model_validate(self._valid_payload())
        assert response.summary_text.startswith("Caso")
        assert response.procedures[0].procedure_type == "angio_fav"
        assert isinstance(response.procedures[0], Llm2ProcedureRecommendation)

    def test_llm2_rejects_invalid_suggestion(self) -> None:
        payload = self._valid_payload()
        payload["procedures"][0]["suggestion"] = "talvez"
        with pytest.raises(ValidationError):
            Llm2Response.model_validate(payload)

    def test_llm2_rejects_duplicate_procedure_type(self) -> None:
        payload = self._valid_payload()
        duplicate = dict(payload["procedures"][0])
        payload["procedures"].append(duplicate)
        with pytest.raises(ValidationError, match="duplicata"):
            Llm2Response.model_validate(payload)

    def test_llm2_rejects_empty_procedures(self) -> None:
        payload = self._valid_payload()
        payload["procedures"] = []
        with pytest.raises(ValidationError):
            Llm2Response.model_validate(payload)


# ── Modelos sintéticos para exercitar a normalização oneOf→anyOf ──────────


class _VariantEda(StrictModel):
    """Variante sintética de procedimento (teste apenas)."""

    procedure_type: Literal["eda"] = "eda"
    urgency: str | None = None


class _VariantColonoscopy(StrictModel):
    """Variante sintética de procedimento (teste apenas)."""

    procedure_type: Literal["colonoscopy"] = "colonoscopy"
    urgency: str | None = None


class _UnionHolder(StrictModel):
    """Modelo sintético com união discriminada (gera ``oneOf`` no JSON Schema)."""

    items: list[Annotated[_VariantEda | _VariantColonoscopy, Field(discriminator="procedure_type")]]


def _assert_object_nodes_strict(node: Any) -> None:
    """Todo nó objeto do schema (normalizado) está pronto para o modo strict."""
    if isinstance(node, dict):
        if node.get("type") == "object" and isinstance(node.get("properties"), dict):
            assert node.get("additionalProperties") is False
            assert set(node["required"]) == set(node["properties"].keys())
        for value in node.values():
            _assert_object_nodes_strict(value)
    elif isinstance(node, list):
        for item in node:
            _assert_object_nodes_strict(item)


def _assert_absent(node: Any, key: str) -> None:
    if isinstance(node, dict):
        assert key not in node
        for value in node.values():
            _assert_absent(value, key)
    elif isinstance(node, list):
        for item in node:
            _assert_absent(item, key)


class TestAdapterNormalization:
    """R6: normalização para ``response_format`` (modo strict)."""

    def test_oneof_to_anyof(self) -> None:
        raw_schema = _UnionHolder.model_json_schema()
        assert "oneOf" in raw_schema["properties"]["items"]["items"]

        normalized = normalize_schema_for_response_format(_UnionHolder)

        items = normalized["properties"]["items"]["items"]
        assert "oneOf" not in items
        assert "discriminator" not in items
        assert "anyOf" in items
        assert len(items["anyOf"]) == 2

    def test_oneof_to_anyof_idempotent(self) -> None:
        once = normalize_schema_for_response_format(_UnionHolder)
        twice = normalize_schema_for_response_format(_UnionHolder)

        assert once == twice

    def test_original_schema_not_mutated(self) -> None:
        raw_schema = _UnionHolder.model_json_schema()
        normalize_schema_for_response_format(_UnionHolder)
        assert "oneOf" in raw_schema["properties"]["items"]["items"]

    def test_required_and_additional_properties_strict(self) -> None:
        model = build_llm1_schema(["angio_fav", "filtro_cava"])
        normalized = normalize_schema_for_response_format(model)
        # União sem oneOf no resultado e estrita em todos os níveis.
        _assert_absent(normalized, "oneOf")
        _assert_object_nodes_strict(normalized)


class TestRoundTrip:
    """R7: payload JSON válido de exemplo parseia e volta intacto."""

    def test_valid_llm1_payload_round_trip(self) -> None:
        model = build_llm1_schema(["angio_fav"])
        payload: dict[str, Any] = {
            **_case_base_payload(),
            "angio_fav": {
                "access_state": {
                    "detail": "fístula madura, com bom fluxo ao exame físico",
                    **_claim_payload(),
                }
            },
        }

        parsed: Any = model.model_validate(payload)
        reparsed: Any = model.model_validate_json(parsed.model_dump_json())

        assert reparsed.pedido.procedimentos_solicitados == ["angio_fav", "permicath"]
        assert reparsed.exames is not None
        assert reparsed.exames.platelets is not None
        assert reparsed.exames.platelets.value == 180000
        access = reparsed.angio_fav.access_state
        assert access.status == "confirmado"
        assert access.detail is not None
        assert access.evidence_spans

    def test_invalid_block_aspect_rejected(self) -> None:
        model = build_llm1_schema(["permicath"])
        payload: dict[str, Any] = {
            **_case_base_payload(),
            "permicath": {
                "active_infection": {"status": "confirmado", "evidence_spans": []},
            },
        }
        with pytest.raises(ValidationError, match="evidence"):
            model.model_validate(payload)
