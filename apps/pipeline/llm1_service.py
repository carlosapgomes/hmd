"""LLM1: extração estruturada com guardas + reconciliação + gate de divergência.

Serviço ``run_llm1_extraction`` (slice 004, R2/R5, design D4/D5): monta o
schema união dos procedimentos **declarados**, monta os prompts do caso
(placeholders substituídos por ``str.replace`` — decisão registrada no slice
003), chama o cliente LLM via factory injetável com ``response_format`` strict
e aplica as guardas em ordem:

1. **schema strict**: parse tolerante (``json_parser``) → validação pydantic
   estrita contra o modelo da união;
2. **language guard pt-BR**: lista canônica versionada de marcadores de outro
   idioma nos campos narrativos (comportamento real do ats-web);
3. **retry corretivo único tipado** (schema/idioma) — a resposta inválida
   anterior NÃO é reenviada; apenas a instrução corretiva entra no user;
4. esgotado → ``LlmPipelineError("llm1_<motivo>")`` tipado. **O serviço nunca
   transiciona**: o orquestrador é o único dono do ``fail_processing`` (D4).

Sucesso: persiste ``Case.structured_data`` (assert de sanidade: nenhum valor
do ``pseudonym_map`` no artefato serializado — só tokens) e grava o evento
enxuto ``CASE_LLM1_COMPLETED``. Em seguida a detecção (tipos em
``pedido.procedimentos_solicitados`` — qualquer tipo do catálogo) é
reconciliada com o declarado e registrada via ``record_detected_procedures``
(UPSERT, R4). Divergência (``missing_declaration``/``not_detected``) → retém:
``manual_review_required=True`` + ``procedure_divergence`` + evento
``CASE_GATE_PROCEDURE_DIVERGENCE`` (classificação por tipo), permanecendo em
``LLM_EXTRACTING``; sem divergência o caller avança com
``complete_llm_extraction``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from django.conf import settings
from django.db import transaction
from pydantic import ValidationError as PydanticValidationError

from apps.cases.events import CaseEventType
from apps.cases.models import (
    PROCEDURE_DIVERGENCE_REASON,
    ActorType,
    Case,
    CaseEvent,
    CaseStatus,
    DetectionStatus,
)
from apps.cases.procedures import (
    get_declared_procedure_types,
    record_detected_procedures,
)
from apps.llm.services import build_case_prompts, prompt_usage
from apps.pipeline.json_parser import LlmJsonParseError, decode_llm_json_object
from apps.pipeline.llm import LlmClient, LlmPipelineError, get_llm_client
from apps.pipeline.procedure_reconciliation import (
    ReconciliationResult,
    reconcile_procedures,
)
from apps.pipeline.ptbr_language_guard import collect_forbidden_terms
from apps.pipeline.schemas import build_llm1_schema, normalize_schema_for_response_format
from apps.pipeline.schemas.base import StrictModel

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from apps.accounts.models import User

# Motivo de falha das guardas (código da instrução corretiva do retry).
GuardReason = Literal["schema", "language"]

# Instruções corretivas do retry único (tipadas por falha). A resposta
# inválida anterior nunca é reenviada — só a instrução entra no user (D4).
_RETRY_INSTRUCTION_BY_REASON: dict[GuardReason, str] = {
    "schema": (
        "Instrução de correção: a resposta anterior não seguiu o JSON schema "
        "fornecido. Retorne APENAS um objeto JSON válido que satisfaça "
        "exatamente o schema (sem campos extras, com todos os campos "
        "obrigatórios e os tipos corretos, sem markdown)."
    ),
    "language": (
        "Instrução de correção: a resposta anterior continha palavras em "
        "inglês nos campos narrativos. Reescreva TODO o texto narrativo em "
        "português brasileiro (pt-BR); não use palavras em inglês."
    ),
}

_FAILURE_MESSAGE_BY_REASON: dict[GuardReason, str] = {
    "schema": "resposta do LLM1 inválida após o retry corretivo (schema).",
    "language": "resposta do LLM1 em outro idioma após o retry corretivo (pt-BR).",
}


@dataclass(frozen=True)
class Llm1ExtractionResult:
    """Desfecho enxuto da extração para o caller (orquestrador 006).

    ``has_divergence`` deriva da reconciliação; persistência/eventos já estão
    no ``Case`` quando o resultado retorna (divergência retém sem transição).
    """

    declared_types: tuple[str, ...]
    detected_types: tuple[str, ...]
    reconciliation: ReconciliationResult
    retry_used: bool = False

    @property
    def has_divergence(self) -> bool:
        return self.reconciliation.has_divergence


# ── Guardas ────────────────────────────────────────────────────────────────


def _narrative_texts(artifact: dict[str, Any]) -> list[str]:
    """Campos textuais narrativos do artefato (leaf strings com espaço).

    Exclui códigos/enums (sem espaços: ``procedure_type``, ``status``,
    ``field_path``); coleta contexto clínico, descrições, ``detail`` dos
    blocos, ``excerpt``/``trechos_nao_classificados`` — os textos que o modelo
    redige e onde o language guard procura marcadores de outro idioma.
    """
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


def _decode_and_validate(
    raw_response: str,
    schema_model: type[StrictModel],
) -> tuple[StrictModel | None, GuardReason | None]:
    """Parse tolerante + validação strict + language guard (ordem das guardas).

    Devolve ``(modelo_validado, None)`` no sucesso ou ``(None, motivo)`` — o
    motivo é o da guarda que falhou (``schema`` para parse/validação,
    ``language`` para marcadores de outro idioma).
    """
    try:
        decoded = decode_llm_json_object(raw_response)
    except LlmJsonParseError:
        return None, "schema"
    try:
        validated = schema_model.model_validate(decoded)
    except PydanticValidationError:
        return None, "schema"
    artifact = validated.model_dump(mode="json")
    if collect_forbidden_terms(texts=_narrative_texts(artifact)):
        return None, "language"
    return validated, None


def _call_with_corrective_retry(
    client: LlmClient,
    messages: Sequence[dict[str, str]],
    json_schema: dict[str, Any],
    schema_model: type[StrictModel],
) -> tuple[StrictModel, bool]:
    """Uma chamada + no máximo 1 retry corretivo tipado (D4/R2).

    Erros de transporte (``LlmError``) propagam sem retry — só resposta
    inválida (schema/idioma) aciona o retry corretivo único. Esgotado →
    ``LlmPipelineError("llm1_<motivo>")`` (fail-closed; o orquestrador é o
    dono do ``fail_processing``).
    """
    raw_response = client.complete(settings.LLM1_MODEL, list(messages), json_schema=json_schema)
    validated, failure = _decode_and_validate(raw_response, schema_model)
    if validated is not None:
        return validated, False
    assert failure is not None
    corrected = _append_instruction(messages, _RETRY_INSTRUCTION_BY_REASON[failure])
    retry_response = client.complete(settings.LLM1_MODEL, corrected, json_schema=json_schema)
    validated, failure = _decode_and_validate(retry_response, schema_model)
    if validated is not None:
        return validated, True
    assert failure is not None
    raise LlmPipelineError(f"llm1_{failure}", _FAILURE_MESSAGE_BY_REASON[failure])


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


def _render_messages(case: Case, declared: tuple[str, ...]) -> list[dict[str, str]]:
    """Monta system+user do caso e substitui ``{texto_anonimizado}`` via str.replace."""
    rendered: list[dict[str, str]] = []
    for message in build_case_prompts("llm1", declared):
        if message["role"] == "user":
            content = message["content"].replace("{texto_anonimizado}", case.anonymized_text)
            rendered.append({"role": "user", "content": content})
        else:
            rendered.append({"role": "system", "content": message["content"]})
    return rendered


def _assert_tokens_only(case: Case, artifact: dict[str, Any]) -> None:
    """Assert de sanidade (R2/D4): nenhum valor real do mapa no artefato.

    Só valores (PII real) são varridos — os tokens (<PESSOA_1>, ...) DEVEM
    aparecer. Vazamento → ``LlmPipelineError("llm1_token_leak")`` sem
    persistir (fail-closed); a mensagem nunca carrega o valor vazado.
    """
    serialized = json.dumps(artifact, ensure_ascii=False, sort_keys=True)
    leaked_count = 0
    leaked_entity_types: list[str] = []
    leaked_lengths: list[int] = []
    for entry in case.pseudonym_map.values():
        if isinstance(entry, dict) and isinstance(entry.get("value"), str):
            real_value = entry["value"]
            if real_value and real_value in serialized:
                leaked_count += 1
                leaked_entity_types.append(str(entry.get("entity_type", "?")))
                leaked_lengths.append(len(real_value))
    if leaked_count:
        # Diagnóstico privacy-safe (fase 2): tipo e comprimento dos valores
        # que bateram — NUNCA o valor em si — para distinguir coincidência de
        # valor curto (ex.: DATA "2026") de re-identificação por inferência.
        logger.error(
            "llm1_token_leak: caso %s — %d valor(es) do pseudonym_map no "
            "artefato; entity_types=%s; comprimentos=%s (valores omitidos)",
            case.case_id,
            leaked_count,
            sorted(leaked_entity_types),
            sorted(leaked_lengths),
        )
        raise LlmPipelineError(
            "llm1_token_leak",
            "artefato da extração contém valor(es) real(is) do pseudonym_map "
            "(PII) — resposta recusada e nada persistido.",
        )


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


def _count_payload(artifact: dict[str, Any]) -> dict[str, int]:
    """Contagens enxutas das listas do artefato para o payload do evento."""
    counts: dict[str, int] = {}
    pedido = artifact.get("pedido")
    if isinstance(pedido, dict):
        procedures = pedido.get("procedimentos_solicitados")
        if isinstance(procedures, list):
            counts["procedimentos_solicitados"] = len(procedures)
    for key in (
        "linha_do_tempo",
        "medicacoes",
        "comorbidades",
        "contraindicacoes",
        "trechos_nao_classificados",
    ):
        value = artifact.get(key)
        if isinstance(value, list):
            counts[key] = len(value)
    return counts


# ── Serviço ────────────────────────────────────────────────────────────────


def run_llm1_extraction(
    case: Case,
    *,
    user: User | None = None,
    role: str = "system",
) -> Llm1ExtractionResult:
    """Executa LLM1 ponta a ponta para o caso (R2/R5 — sem transições).

    Raises:
        LlmPipelineError: guardas esgotadas (``llm1_schema``/``llm1_language``/
            ``llm1_token_leak``) ou caso sem declarações; LlmError de
            transporte propaga. Quem decide o ``fail_processing`` é o
            orquestrador (006), nunca este serviço.
    """
    declared_types = get_declared_procedure_types(case)
    if not declared_types:
        raise LlmPipelineError(
            "llm1_no_declared_types",
            "caso sem procedimentos declarados — a extração exige a declaração.",
        )

    schema_model = build_llm1_schema(declared_types)
    prompt_versions = prompt_usage("llm1", declared_types)
    messages = _render_messages(case, declared_types)
    json_schema = {
        "name": schema_model.__name__,
        "schema": normalize_schema_for_response_format(schema_model),
    }
    client = get_llm_client()
    validated, retry_used = _call_with_corrective_retry(client, messages, json_schema, schema_model)
    artifact = validated.model_dump(mode="json")
    _assert_tokens_only(case, artifact)

    with transaction.atomic():
        current = Case.objects.select_for_update().get(pk=case.pk)
        # Gate do slice 002 (painel-lista-encerramento): a chamada LLM é longa e
        # pode retornar DEPOIS do encerramento administrativo (worker com lease
        # expirada). Re-lê DENTRO do atomic e, em CLEANED, pula TODAS as
        # escritas (``structured_data``, ``manual_review_*``, eventos) devolvendo
        # o resultado computado sem persistir.
        if current.status == CaseStatus.CLEANED:
            logger.info(
                "run_llm1_extraction: caso %s encerrado administrativamente — sem persistir",
                case.pk,
            )
            reconciliation = _reconcile_artifact(declared_types, artifact)
            return Llm1ExtractionResult(
                declared_types=declared_types,
                detected_types=_detected_types(reconciliation),
                reconciliation=reconciliation,
                retry_used=retry_used,
            )
        case.structured_data = artifact
        case.save(update_fields=["structured_data"])
        _record_event(
            case,
            event_type=CaseEventType.CASE_LLM1_COMPLETED,
            payload={
                "prompts": prompt_versions,
                "declared_types": list(declared_types),
                "counts": _count_payload(artifact),
                "retry_used": retry_used,
            },
            user=user,
            role=role,
        )
        reconciliation = _record_detection(case, declared_types, artifact, user=user, role=role)
        if reconciliation.has_divergence:
            case.manual_review_required = True
            case.manual_review_reason = PROCEDURE_DIVERGENCE_REASON
            case.save(update_fields=["manual_review_required", "manual_review_reason"])
            _record_event(
                case,
                event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE,
                payload={"classification": reconciliation.classification()},
                user=user,
                role=role,
            )
        detected_types = _detected_types(reconciliation)

    return Llm1ExtractionResult(
        declared_types=declared_types,
        detected_types=detected_types,
        reconciliation=reconciliation,
        retry_used=retry_used,
    )


def _reconcile_artifact(
    declared_types: tuple[str, ...], artifact: dict[str, Any]
) -> ReconciliationResult:
    """Reconcilia declarado × detectado a partir do artefato (puro, sem persistir)."""
    pedido = artifact.get("pedido")
    detected_raw: list[object] = []
    if isinstance(pedido, dict):
        procedures = pedido.get("procedimentos_solicitados")
        if isinstance(procedures, list):
            detected_raw = procedures
    return reconcile_procedures(declared_types, (str(t) for t in detected_raw))


def _detected_types(reconciliation: ReconciliationResult) -> tuple[str, ...]:
    """Tipos detectados (≠ ``not_detected``) na ordem canônica da reconciliação."""
    return tuple(
        procedure_type
        for procedure_type, classification in reconciliation.classification().items()
        if classification != "not_detected"
    )


def _record_detection(
    case: Case,
    declared_types: tuple[str, ...],
    artifact: dict[str, Any],
    *,
    user: User | None,
    role: str | None,
) -> ReconciliationResult:
    """Reconcilia declarado × detectado e grava a detecção (UPSERT, R4)."""
    reconciliation = _reconcile_artifact(declared_types, artifact)

    detection_map: dict[str, str] = {}
    detected_set = set(reconciliation.match) | set(reconciliation.missing_declaration)
    for procedure_type in declared_types:
        detection_map[procedure_type] = (
            DetectionStatus.DETECTED.value
            if procedure_type in detected_set
            else DetectionStatus.NOT_DETECTED.value
        )
    for procedure_type in reconciliation.missing_declaration:
        detection_map[procedure_type] = DetectionStatus.DETECTED.value
    record_detected_procedures(case, detection_map, user=user, role=role)
    return reconciliation
