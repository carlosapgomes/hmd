"""LLM2: sumarização com policy determinística prevalecendo (slice 006, R1, D8).

Serviço ``run_llm2_summarization`` (R1): tipos = **declarados** (reconciliados —
bypass preserva declarado, D5); monta a **visão estruturada** = cópia efêmera
do ``structured_data`` filtrada pelos tipos (padrão ``_build_llm2_structured_data_view``
do ats-web) + o ``policy_result`` persistido (slice 005) + os resumos de
prior-case (motivo pré-anonimizado pelo slice 005); aplica o **assert de tokens
RECURSIVO** sobre o payload serializado (nenhum valor real do ``pseudonym_map``
do caso E dos mapas dos casos prévios); monta os prompts ``llm2`` (slice 003,
placeholders via ``str.replace`` — nunca ``.format``); faz **uma chamada** com
``response_format`` strict (schema Llm2, slice 002), language guard pt-BR e
**retry corretivo único** (regime do 004); falha esgotada → levanta
``LlmPipelineError("llm2_<motivo>")`` — **o serviço nunca transiciona**: o
orquestrador é o único dono do ``fail_processing`` (D4/D8).

Sucesso → **reconciliação final**: por procedimento, ``policy.recomendacao ==
recusar`` ⇒ sugestão final ``recusar`` com os motivos da policy (o LLM não
suaviza); agregado do caso (consultivo) = **qualquer procedimento com sugestão
final recusar ⇒ agregado recusa com os motivos somados** (definição HMD; o
``strictest_global_support`` do ats-web referia-se apenas ao suporte anestésico
e não se aplica — correção do review). Persiste ``Case.summary_text`` +
``Case.suggested_action`` + evento enxuto ``CASE_LLM2_COMPLETED``.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from django.conf import settings
from django.db import transaction
from pydantic import ValidationError as PydanticValidationError

from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent
from apps.cases.procedures import get_declared_procedure_types
from apps.llm.services import build_case_prompts, prompt_usage
from apps.pipeline.json_parser import LlmJsonParseError, decode_llm_json_object
from apps.pipeline.llm import LlmClient, LlmPipelineError, get_llm_client
from apps.pipeline.prior_case import PriorCaseSummary, lookup_prior_case_context
from apps.pipeline.ptbr_language_guard import collect_forbidden_terms
from apps.pipeline.schemas import normalize_schema_for_response_format
from apps.pipeline.schemas.blocks import PROCEDURE_SPECIFIC_BLOCKS
from apps.pipeline.schemas.llm2 import Llm2Response

if TYPE_CHECKING:
    from apps.accounts.models import User

# Motivo de falha das guardas (código da instrução corretiva do retry).
GuardReason = Literal["schema", "language"]

# Instruções corretivas do retry único (tipadas por falha). A resposta
# inválida anterior nunca é reenviada — só a instrução entra no user (D4/D8).
_RETRY_INSTRUCTION_BY_REASON: dict[GuardReason, str] = {
    "schema": (
        "Instrução de correção: a resposta anterior não seguiu o JSON schema "
        "fornecido. Retorne APENAS um objeto JSON válido que satisfaça "
        "exatamente o schema (sem campos extras, com todos os campos "
        "obrigatórios e os tipos corretos), produzindo exatamente um item por "
        "procedimento da lista fornecida — sem omitir, duplicar ou adicionar. "
        "Sem markdown."
    ),
    "language": (
        "Instrução de correção: a resposta anterior continha palavras em "
        "inglês nos campos narrativos. Reescreva TODO o texto narrativo em "
        "português brasileiro (pt-BR); não use palavras em inglês."
    ),
}

_FAILURE_MESSAGE_BY_REASON: dict[GuardReason, str] = {
    "schema": "resposta do LLM2 inválida após o retry corretivo (schema/conjunto).",
    "language": "resposta do LLM2 em outro idioma após o retry corretivo (pt-BR).",
}

_NO_PRIOR_CASE_TEXT = "Nenhum caso anterior localizado."


@dataclass(frozen=True)
class Llm2SummarizationResult:
    """Desfecho enxuto da sumarização para o caller (orquestrador 006)."""

    suggestions: dict[str, str]
    aggregate: str
    aggregate_reasons: list[str]
    retry_used: bool = False


# ── Guardas ────────────────────────────────────────────────────────────────


def _narrative_texts(artifact: dict[str, Any]) -> list[str]:
    """Campos textuais narrativos da resposta (strings com espaço)."""
    texts: list[str] = []

    def _collect(value: object) -> None:
        if isinstance(value, dict):
            for child in value.values():
                _collect(child)
        elif isinstance(value, list):
            for child in value:
                _collect(child)
        elif isinstance(value, str) and any(character.isspace() for character in value):
            texts.append(value)

    _collect(artifact)
    return texts


def _response_covers_declared(validated: Llm2Response, declared: Sequence[str]) -> bool:
    """Igualdade exata de conjuntos: um item por tipo declarado (D8)."""
    return sorted(item.procedure_type for item in validated.procedures) == sorted(declared)


def _decode_and_validate(
    raw_response: str,
    declared: Sequence[str],
) -> tuple[Llm2Response | None, GuardReason | None]:
    """Parse tolerante + validação strict + igualdade de conjunto + language guard."""
    try:
        decoded = decode_llm_json_object(raw_response)
    except LlmJsonParseError:
        return None, "schema"
    try:
        validated = Llm2Response.model_validate(decoded)
    except PydanticValidationError:
        return None, "schema"
    if not _response_covers_declared(validated, declared):
        return None, "schema"
    artifact = validated.model_dump(mode="json")
    if collect_forbidden_terms(texts=_narrative_texts(artifact)):
        return None, "language"
    return validated, None


def _call_with_corrective_retry(
    client: LlmClient,
    messages: Sequence[dict[str, str]],
    json_schema: dict[str, Any],
    declared: Sequence[str],
) -> tuple[Llm2Response, bool]:
    """Uma chamada + no máximo 1 retry corretivo tipado (regime do 004)."""
    raw_response = client.complete(settings.LLM2_MODEL, list(messages), json_schema=json_schema)
    validated, failure = _decode_and_validate(raw_response, declared)
    if validated is not None:
        return validated, False
    assert failure is not None
    corrected = _append_instruction(messages, _RETRY_INSTRUCTION_BY_REASON[failure])
    retry_response = client.complete(settings.LLM2_MODEL, corrected, json_schema=json_schema)
    validated, failure = _decode_and_validate(retry_response, declared)
    if validated is not None:
        return validated, True
    assert failure is not None
    raise LlmPipelineError(f"llm2_{failure}", _FAILURE_MESSAGE_BY_REASON[failure])


def _append_instruction(
    messages: Sequence[dict[str, str]],
    instruction: str,
) -> list[dict[str, str]]:
    """Anexa a instrução corretiva ao fim do user (nada da resposta anterior)."""
    appended: list[dict[str, str]] = []
    for message in messages:
        if message["role"] == "user":
            appended.append({"role": "user", "content": f"{message['content']}\n\n{instruction}"})
        else:
            appended.append({"role": "system", "content": message["content"]})
    return appended


# ── Montagem do payload (só tokens) ────────────────────────────────────────


def _build_structured_view(
    structured_data: dict[str, Any],
    declared: Sequence[str],
) -> dict[str, Any]:
    """Cópia efêmera do artefato LLM1 filtrada pelos tipos reconciliados.

    Reaproveita os itens originais (cópia profunda — nunca muta o artefato
    persistido): mantém a base comum e restringe a lista de procedimentos e os
    blocos específicos ao conjunto declarado (padrão ats-web).
    """
    types = set(declared)
    view = copy.deepcopy(structured_data)
    for block_key in PROCEDURE_SPECIFIC_BLOCKS:
        if block_key not in types and block_key in view:
            del view[block_key]
    pedido = view.get("pedido")
    if isinstance(pedido, dict):
        raw = pedido.get("procedimentos_solicitados")
        if isinstance(raw, list):
            pedido["procedimentos_solicitados"] = [
                procedure_type for procedure_type in raw if procedure_type in types
            ]
    return view


def _prior_item(summary: PriorCaseSummary) -> dict[str, object]:
    """Item de prior-case para o prompt (resumo enxuto, motivo já anonimizado)."""
    return {
        "procedure_type": summary.procedure_type,
        "decided_at": summary.decided_at,
        "decision": summary.decision,
        "reason_anonymized": summary.reason_anonymized,
        "prior_denial_count": summary.prior_denial_count,
        "origin": summary.origin,
    }


def _lookup_prior_context(case: Case, declared: Sequence[str]) -> list[PriorCaseSummary]:
    """Resumos de prior-case por tipo declarado (ordem canônica da declaração)."""
    summaries: list[PriorCaseSummary] = []
    for procedure_type in declared:
        summary = lookup_prior_case_context(case, procedure_type)
        if summary is not None:
            summaries.append(summary)
    return summaries


def _render_messages(
    case: Case,
    declared: Sequence[str],
    view: dict[str, Any],
    prior_summaries: Sequence[PriorCaseSummary],
) -> list[dict[str, str]]:
    """Monta system+user do caso e substitui os placeholders via str.replace."""
    rendered: list[dict[str, str]] = []
    visao_json = json.dumps(view, ensure_ascii=False, sort_keys=True)
    policy_json = json.dumps(case.policy_result, ensure_ascii=False, sort_keys=True)
    if prior_summaries:
        prior_json = json.dumps(
            [_prior_item(summary) for summary in prior_summaries],
            ensure_ascii=False,
            sort_keys=True,
        )
    else:
        prior_json = _NO_PRIOR_CASE_TEXT
    for message in build_case_prompts("llm2", declared):
        if message["role"] == "user":
            content = message["content"]
            content = content.replace("{texto_anonimizado}", case.anonymized_text)
            content = content.replace("{visao_estruturada}", visao_json)
            content = content.replace("{policy}", policy_json)
            content = content.replace("{prior_case}", prior_json)
            rendered.append({"role": "user", "content": content})
        else:
            rendered.append({"role": "system", "content": message["content"]})
    return rendered


def _prior_cases_for_assert(
    prior_summaries: Sequence[PriorCaseSummary],
) -> list[Case]:
    """Casos prévios casados (para a varredura de valores reais dos mapas)."""
    if not prior_summaries:
        return []
    prior_ids = [summary.prior_case_id for summary in prior_summaries]
    return list(Case.objects.filter(case_id__in=prior_ids))


def _assert_tokens_only(
    case: Case,
    serialized: str,
    prior_cases: Sequence[Case],
) -> None:
    """Assert de sanidade recursivo (R1/D8): nenhum valor real do mapa no payload.

    Varre o payload serializado contra os valores reais do ``pseudonym_map`` do
    caso E dos casos prévios (o motivo do prior-case entra pré-anonimizado pelo
    slice 005 — o assert é o backstop). Vazamento →
    ``LlmPipelineError("llm2_token_leak")`` sem persistir (fail-closed); a
    mensagem nunca carrega o valor vazado.
    """
    leaked_count = 0
    sources = [case, *prior_cases]
    for source in sources:
        for entry in source.pseudonym_map.values():
            if isinstance(entry, dict) and isinstance(entry.get("value"), str):
                real_value = entry["value"]
                if real_value and real_value in serialized:
                    leaked_count += 1
    if leaked_count:
        raise LlmPipelineError(
            "llm2_token_leak",
            "payload da sumarização contém valor(es) real(is) de pseudonym_map "
            "(PII) — resposta recusada e nada persistido.",
        )


# ── Reconciliação final (policy prevalece; agregado estrito) ───────────────


def _final_suggestions(
    validated: Llm2Response,
    policy_result: dict[str, dict[str, Any]],
    declared: Sequence[str],
) -> dict[str, dict[str, object]]:
    """Sugestão final por procedimento: policy recusa ⇒ recusa (o LLM não suaviza)."""
    llm_by_type = {item.procedure_type: item for item in validated.procedures}
    final: dict[str, dict[str, object]] = {}
    for procedure_type in declared:
        item = llm_by_type[procedure_type]
        policy = policy_result.get(procedure_type, {})
        if policy.get("recommendation") == "recomenda_recusar":
            motivos = policy.get("refusal_reasons")
            final[procedure_type] = {
                "suggestion": "recusar",
                "motivos": list(motivos) if isinstance(motivos, list) else [],
            }
        else:
            final[procedure_type] = {
                "suggestion": item.suggestion,
                "motivos": list(item.motivos),
            }
    return final


def _aggregate(final_suggestions: dict[str, dict[str, object]]) -> dict[str, object]:
    """Agregado do caso (consultivo): qualquer recusa ⇒ recusa com motivos somados."""
    refused_reasons: list[str] = []
    for entry in final_suggestions.values():
        if entry["suggestion"] == "recusar":
            motivos = entry["motivos"]
            if isinstance(motivos, list):
                refused_reasons.extend(str(reason) for reason in motivos)
    if refused_reasons:
        return {"suggestion": "recusar", "motivos": refused_reasons}
    return {"suggestion": "aceitar", "motivos": []}


def _record_event(
    case: Case,
    *,
    event_type: str,
    payload: dict[str, object],
    user: User | None,
    role: str | None,
) -> None:
    """Grava evento não-transicional da etapa com ator/papel (D5)."""
    CaseEvent.objects.create(
        case=case,
        event_type=event_type,
        actor_type=ActorType.USER if user is not None else ActorType.SYSTEM,
        actor=user,
        actor_role=role or "",
        payload=payload,
    )


# ── Serviço ────────────────────────────────────────────────────────────────


def run_llm2_summarization(
    case: Case,
    *,
    user: User | None = None,
    role: str = "system",
) -> Llm2SummarizationResult:
    """Executa a sumarização LLM2 ponta a ponta para o caso (R1 — sem transições).

    Raises:
        LlmPipelineError: guardas esgotadas (``llm2_schema``/``llm2_language``),
            vazamento de token (``llm2_token_leak``), ausência de declarações
            (``llm2_no_declared_types``) ou de resultado de policy
            (``llm2_no_policy_result``); LlmError de transporte propaga. Quem
            decide o ``fail_processing`` é o orquestrador (006), nunca aqui.
    """
    declared_types = get_declared_procedure_types(case)
    if not declared_types:
        raise LlmPipelineError(
            "llm2_no_declared_types",
            "caso sem procedimentos declarados — a sumarização exige a declaração.",
        )
    stored = case.structured_data
    artifact = stored if isinstance(stored, dict) else {}
    if not case.policy_result:
        raise LlmPipelineError(
            "llm2_no_policy_result",
            "caso sem resultado de policy persistido — a sumarização exige a avaliação.",
        )
    policy_result: dict[str, dict[str, Any]] = {}
    for procedure_type, raw in case.policy_result.items():
        if isinstance(raw, dict):
            policy_result[str(procedure_type)] = {str(k): v for k, v in raw.items()}

    view = _build_structured_view(artifact, declared_types)
    prior_summaries = _lookup_prior_context(case, declared_types)
    prior_cases = _prior_cases_for_assert(prior_summaries)
    prompt_versions = prompt_usage("llm2", declared_types)
    messages = _render_messages(case, declared_types, view, prior_summaries)
    json_schema = {
        "name": Llm2Response.__name__,
        "schema": normalize_schema_for_response_format(Llm2Response),
    }

    # Assert de sanidade pré-call: o payload serializado (tudo que o LLM2 vê)
    # não contém valor real dos mapas do caso/dos casos prévios.
    serialized_payload = json.dumps(
        [message["content"] for message in messages],
        ensure_ascii=False,
        sort_keys=True,
    )
    _assert_tokens_only(case, serialized_payload, prior_cases)

    client = get_llm_client()
    validated, retry_used = _call_with_corrective_retry(
        client, messages, json_schema, declared_types
    )

    # Assert pós-call: a resposta validada também não vaza valor real.
    response_json = json.dumps(
        validated.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    _assert_tokens_only(case, response_json, prior_cases)

    final_suggestions = _final_suggestions(validated, policy_result, declared_types)
    aggregate = _aggregate(final_suggestions)
    suggested_action: dict[str, object] = {
        "procedures": final_suggestions,
        "aggregate": aggregate,
    }

    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        locked.summary_text = validated.summary_text
        locked.suggested_action = suggested_action
        locked.save(update_fields=["summary_text", "suggested_action"])
        suggestions_payload = {
            procedure_type: str(entry["suggestion"])
            for procedure_type, entry in final_suggestions.items()
        }
        _record_event(
            locked,
            event_type=CaseEventType.CASE_LLM2_COMPLETED,
            payload={
                "prompts": prompt_versions,
                "suggestions": suggestions_payload,
                "aggregate": aggregate["suggestion"],
                "retry_used": retry_used,
            },
            user=user,
            role=role,
        )

    raw_motivos = aggregate.get("motivos")
    aggregate_reasons = (
        [str(reason) for reason in raw_motivos] if isinstance(raw_motivos, list) else []
    )
    return Llm2SummarizationResult(
        suggestions=suggestions_payload,
        aggregate=str(aggregate["suggestion"]),
        aggregate_reasons=aggregate_reasons,
        retry_used=retry_used,
    )
