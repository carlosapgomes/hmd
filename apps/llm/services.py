"""Montagem dos prompts de um caso (change llm-pipeline-per-type, slice 003, R3/R4).

``build_case_prompts`` é a única produtora dos ``ChatMessage`` do caso por
estágio: system neutro do estágio (``<stage>.system``) + user = concatenação
dos blocos ``proc.<type>.<stage>.user`` ativos na **ordem canônica do
catálogo** + seção de payload com os placeholders do estágio.

A montagem NUNCA vê conteúdo do caso: os placeholders ``{texto_anonimizado}``
/ ``{visao_estruturada}`` / ``{policy}`` / ``{prior_case}`` são substituídos
pelo chamador (serviços do pipeline — slices 004/006). ``prompt_usage``
devolve o payload enxuto de auditoria (names+versions dos prompts usados).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, TypedDict

from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.llm.models import PromptTemplate
from apps.llm.prompts_seed import system_prompt_name, user_prompt_name

Stage = Literal["llm1", "llm2"]


class ChatMessage(TypedDict):
    """Mensagem no formato da SDK OpenAI (role + content) para o cliente LLM."""

    role: Literal["system", "user"]
    content: str


# Posição de cada tipo no registro canônico do catálogo (R3: ordem canônica).
_CANONICAL_ORDER: dict[str, int] = {
    profile.procedure_type: index for index, profile in enumerate(PROCEDURE_PROFILES)
}

# Seção de payload anexada ao user, por estágio (placeholders; o chamador
# substitui — a montagem não conhece os dados do caso).
_LLM1_PAYLOAD = (
    "DADOS DO CASO — texto anonimizado do relatório (substituído no envio):\n{texto_anonimizado}"
)

_LLM2_PAYLOAD = (
    "DADOS DO CASO — seções substituídas no envio pelo chamador:\n"
    "- Texto anonimizado do relatório:\n"
    "{texto_anonimizado}\n"
    "- Visão estruturada (artefato LLM1 filtrado pelos procedimentos "
    "reconciliados):\n"
    "{visao_estruturada}\n"
    "- Resultados da política pré-operatória por procedimento:\n"
    "{policy}\n"
    "- Resumos de casos anteriores (prior-case):\n"
    "{prior_case}"
)

_PAYLOAD_BY_STAGE: dict[str, str] = {
    "llm1": _LLM1_PAYLOAD,
    "llm2": _LLM2_PAYLOAD,
}


def _order_canonically(types: Sequence[str]) -> list[str]:
    """Valida os tipos contra o catálogo e devolve na ordem canônica.

    Tipo fora do catálogo levanta ``KeyError`` nomeando o tipo (fail-fast do
    catálogo); os 13 códigos são únicos no registro, então a ordenação por
    posição é total e determinística.
    """
    for procedure_type in types:
        if procedure_type not in _CANONICAL_ORDER:
            raise KeyError(f"procedimento fora do catálogo: {procedure_type!r}")
    return sorted(types, key=_CANONICAL_ORDER.__getitem__)


def _assemble_user_content(blocks: Sequence[str], stage: Stage) -> str:
    """Concatena os blocos dos tipos + payload do estágio (placeholders)."""
    parts = list(blocks)
    parts.append(_PAYLOAD_BY_STAGE[stage])
    return "\n\n".join(parts)


def build_case_prompts(
    stage: Stage,
    types: Sequence[str],
    *,
    extra_context: str = "",
) -> list[ChatMessage]:
    """Monta system neutro + user composto para o caso (R3).

    ``types`` são os procedimentos do caso (declarados/reconciliados — o
    chamador decide); a ordem de exibição é sempre a canônica do catálogo.
    Tipo sem prompt ativo (ou system sem ativo) falha explicitamente
    nomeando o prompt esperado.
    """
    ordered = _order_canonically(types)
    system = PromptTemplate.get_active_prompt(system_prompt_name(stage))
    blocks = [
        PromptTemplate.get_active_prompt(user_prompt_name(procedure_type, stage)).content
        for procedure_type in ordered
    ]
    user_content = _assemble_user_content(blocks, stage)
    if extra_context:
        user_content = f"{user_content}\n\n{extra_context}"
    return [
        {"role": "system", "content": system.content},
        {"role": "user", "content": user_content},
    ]


def prompt_usage(stage: Stage, types: Sequence[str]) -> dict[str, int]:
    """Payload enxuto de auditoria (R4): nomes e versões dos prompts ativos.

    ``{"<name>": <version>, ...}`` — system do estágio + users dos tipos na
    ordem canônica. Sem conteúdo: apenas identificadores versionados, para o
    payload de eventos do pipeline.
    """
    ordered = _order_canonically(types)
    names = [system_prompt_name(stage)]
    names.extend(user_prompt_name(procedure_type, stage) for procedure_type in ordered)
    return {name: PromptTemplate.get_active_prompt(name).version for name in names}
