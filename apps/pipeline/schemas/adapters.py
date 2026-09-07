"""Normalização do JSON Schema para o ``response_format`` strict do endpoint
(change llm-pipeline-per-type, slice 002, design D2 — R6).

Herança completa do adapter do ats-web: o modo strict exige, em **todo** nó
objeto, ``additionalProperties: false`` e ``required`` listando **todos** os
campos; união só via ``anyOf`` — todo nó ``oneOf`` é reescrito para
``anyOf`` (preservando ordem e ``$ref``) e a chave ``discriminator`` é
removida. A validação local (pydantic) segue usando o discriminator nos
próprios modelos; apenas a **cópia** enviada à API é reescrita (idempotente,
sem mutar o schema original).
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel


def normalize_schema_for_response_format(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema do modelo normalizado para o modo strict (R6).

    Aplica ``oneOf→anyOf`` + ``required`` de todos os campos +
    ``additionalProperties: false`` recursivamente sobre uma cópia do schema
    de ``model.model_json_schema()`` — o schema original não é alterado.
    """
    normalized = copy.deepcopy(model.model_json_schema())
    _normalize_schema_node(normalized)
    return normalized


def _normalize_schema_node(node: object) -> None:
    """Reescreve um nó do schema in-place (mutação antes da recursão)."""
    if isinstance(node, dict):
        if "oneOf" in node:
            node["anyOf"] = node.pop("oneOf")
        node.pop("discriminator", None)

        node_type = node.get("type")
        properties = node.get("properties")
        if node_type == "object" and isinstance(properties, dict):
            node["required"] = [str(name) for name in properties.keys()]
            node["additionalProperties"] = False

        for value in node.values():
            _normalize_schema_node(value)
        return

    if isinstance(node, list):
        for item in node:
            _normalize_schema_node(item)
