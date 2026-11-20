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
from apps.cases.procedure_review import (
    REASON_ANONYMIZATION_MISSING,
    REASON_ANONYMIZED_MISMATCH,
    REASON_MAP_MISMATCH,
    REASON_SOURCE_CHANGED,
    REASON_SOURCE_UNAVAILABLE,
    anonymized_text_digest_for_case,
    has_procedure_review_marker,
    is_gate_reason,
    latest_confirmation_validity,
    latest_review_event,
    pseudonym_map_digest_for_case,
    record_review_invalidated,
    source_fingerprint_for_case,
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

    ``has_divergence`` deriva da reconciliação, SUPRIMIDA quando há confirmação
    humana vigente (D4: a mesma divergência não recria retenção, mas a
    detecção real permanece nas rows/artefato); persistência/eventos já estão
    no ``Case`` quando o resultado retorna (divergência retém sem transição).
    """

    declared_types: tuple[str, ...]
    detected_types: tuple[str, ...]
    reconciliation: ReconciliationResult
    retry_used: bool = False
    human_confirmation: bool = False

    @property
    def has_divergence(self) -> bool:
        if self.human_confirmation:
            return False
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


# ── Autoridade humana (confirm-nir 001, D3/D4) ─────────────────────────────


def _capture_digests(case: Case) -> dict[str, str | None]:
    """Snapshot dos três digests vigentes (só hashes, nunca textos/valores)."""
    return {
        "source_fingerprint": source_fingerprint_for_case(case),
        "anonymized_text_digest": anonymized_text_digest_for_case(case),
        "pseudonym_map_digest": pseudonym_map_digest_for_case(case),
    }


def _pre_digest_failure(pre_digests: dict[str, str | None], case: Case) -> str | None:
    """Motivo de integridade se os digests vigentes divergem do snapshot pré-dispatch."""
    current_source = source_fingerprint_for_case(case)
    if pre_digests["source_fingerprint"] is not None and current_source is None:
        return REASON_SOURCE_UNAVAILABLE
    if current_source != pre_digests["source_fingerprint"]:
        return REASON_SOURCE_CHANGED
    current_anonymized = anonymized_text_digest_for_case(case)
    if current_anonymized != pre_digests["anonymized_text_digest"]:
        if not (case.anonymized_text or ""):
            return REASON_ANONYMIZATION_MISSING
        return REASON_ANONYMIZED_MISMATCH
    if pseudonym_map_digest_for_case(case) != pre_digests["pseudonym_map_digest"]:
        return REASON_MAP_MISMATCH
    return None


def _check_authority_before_dispatch(
    case: Case,
) -> tuple[bool, CaseEvent | None]:
    """Validação pré-dispatch: devolve ``(human_confirmation, evento)``.

    Sem confirmação → ``(False, None)`` (automático). Válida → ``(True, ...)``.
    Gate (revisão/declaração íntegres) → audita INVALIDATED uma vez e devolve
    ``(False, ...)`` (automático segue). Integridade/malformação → audita e
    levanta ``LlmPipelineError(motivo)`` SEM dispatch (o orquestrador vira
    FAILED). ``CLEANED`` é decidido pelo chamador antes desta checagem.
    """
    confirmation_event, validity = latest_confirmation_validity(case)
    if confirmation_event is None or validity is None:
        return False, None
    if validity.valid:
        return True, confirmation_event
    record_review_invalidated(case, confirmation_event, validity.reason)
    if is_gate_reason(validity.reason):
        return False, confirmation_event
    raise LlmPipelineError(
        validity.reason,
        "confirmação de procedimento inválida (integridade) — sem dispatch.",
    )


def _validate_usable_inputs(case: Case) -> None:
    """Entrada utilizável para dispatch marcado (D3, fix-2 P1-4).

    Fonte indisponível ou cópia anonimizada vazia falha fechado ANTES de
    qualquer dispatch externo — inclusive na primeira extração marcada, sem
    confirmação (onde ``_check_authority_before_dispatch`` retorna normal).
    """
    if source_fingerprint_for_case(case) is None:
        raise LlmPipelineError(
            REASON_SOURCE_UNAVAILABLE,
            "fonte do caso indisponível — sem dispatch.",
        )
    if not (case.anonymized_text or ""):
        raise LlmPipelineError(
            REASON_ANONYMIZATION_MISSING,
            "cópia anonimizada ausente — sem dispatch.",
        )


def _review_already_recorded(
    case: Case,
    classification: dict[str, str],
    pre_digests: dict[str, str | None] | None,
) -> bool:
    """Revisão equivalente já é a mais recente? (HD-001, fix-2 P1-3).

    Equivalência = mesma classificação e mesmos três digests da entrada.
    Revisão genuinamente alterada (classificação ou digests diferentes)
    não é suprimida.
    """
    latest = latest_review_event(case)
    if latest is None:
        return False
    payload = latest.payload
    if not isinstance(payload, dict):
        return False
    if payload.get("classification") != classification:
        return False
    if pre_digests is not None:
        for key in ("source_fingerprint", "anonymized_text_digest", "pseudonym_map_digest"):
            if payload.get(key) != pre_digests[key]:
                return False
    return True


def _record_applied(
    case: Case,
    *,
    confirmation_event: CaseEvent,
    automatic: tuple[str, ...],
    effective: tuple[str, ...],
    user: User | None,
    role: str | None,
) -> None:
    """Registra APPLIED (D4) idempotente por confirmação + resultado."""
    automatic_list = list(automatic)
    effective_list = list(effective)
    existing = CaseEvent.objects.filter(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED,
        payload__confirmation_event_id=confirmation_event.pk,
    )
    for prior in existing:
        prior_payload = prior.payload
        if (
            isinstance(prior_payload, dict)
            and prior_payload.get("automatic_procedures") == automatic_list
            and prior_payload.get("effective_procedures") == effective_list
        ):
            return
    _record_event(
        case,
        event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED,
        payload={
            "confirmation_event_id": confirmation_event.pk,
            "automatic_procedures": automatic_list,
            "effective_procedures": effective_list,
        },
        user=user,
        role=role,
    )


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
    while True:
        case.refresh_from_db()
        declared_types = get_declared_procedure_types(case)
        if not declared_types:
            raise LlmPipelineError(
                "llm1_no_declared_types",
                "caso sem procedimentos declarados — a extração exige a declaração.",
            )
        marked = has_procedure_review_marker(case)
        # Validação pré-dispatch (D3): CLEANED decide antes de qualquer invalidação
        # (D3/D6) — caso encerrado aborta sem INVALIDATED/FAILED.
        human_confirmation = False
        confirmation_event: CaseEvent | None = None
        if case.status != CaseStatus.CLEANED:
            human_confirmation, confirmation_event = _check_authority_before_dispatch(case)
            if marked:
                # Fix-2 P1-4: a primeira extração marcada (sem confirmação) não
                # passava por validação de integridade — fonte indisponível ou
                # cópia vazia falham fechado antes de qualquer dispatch.
                _validate_usable_inputs(case)
        # Snapshot pré-dispatch (fix-1 P1-3): a revisão/LLM1_COMPLETED registram
        # os digests da entrada que produziu o artefato, nunca os de uma fonte
        # trocada em voo.
        pre_digests: dict[str, str | None] | None = _capture_digests(case) if marked else None

        schema_model = build_llm1_schema(declared_types)
        prompt_versions = prompt_usage("llm1", declared_types)
        messages = _render_messages(case, declared_types)
        json_schema = {
            "name": schema_model.__name__,
            "schema": normalize_schema_for_response_format(schema_model),
        }
        client = get_llm_client()
        validated, retry_used = _call_with_corrective_retry(
            client, messages, json_schema, schema_model
        )
        artifact = validated.model_dump(mode="json")
        _assert_tokens_only(case, artifact)

        # Revalidação pós-chamada longa ainda ANTES de persistir (D3): releitura
        # do estado primeiro (CLEANED aborta sem INVALIDATED/FAILED); depois a
        # autoridade — integridade divergente não persiste o resultado em voo.
        case.refresh_from_db()
        if case.status != CaseStatus.CLEANED:
            post_human, post_event = _check_authority_before_dispatch(case)
            human_confirmation = post_human
            confirmation_event = post_event

        pending_failure: str | None = None
        obsolete_execution = False
        reconciliation = _reconcile_artifact(declared_types, artifact)
        detected_types = _detected_types(reconciliation)
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
                    human_confirmation=human_confirmation,
                )
            # Revalidação DENTRO do lock de persistência (fix-1 P1-3): fecha a
            # janela check-to-write. Integridade vs snapshot pré-dispatch ou vs
            # autoridade vigente → FAILED sem persistir o artefato em voo. O
            # INVALIDATED (se houver confirmação) é gravado neste atomic e a
            # falha é levantada após o commit, para não desfazê-lo.
            if marked and pre_digests is not None:
                pre_failure = _pre_digest_failure(pre_digests, current)
                if pre_failure is not None:
                    pending_failure = pre_failure
                    _event, _validity = latest_confirmation_validity(current)
                    if _event is not None:
                        record_review_invalidated(current, _event, pre_failure)
            if pending_failure is None and marked:
                _event, _validity = latest_confirmation_validity(current)
                if _event is not None and _validity is not None and not _validity.valid:
                    record_review_invalidated(current, _event, _validity.reason)
                    if not is_gate_reason(_validity.reason):
                        pending_failure = _validity.reason
                    else:
                        human_confirmation = False
                        confirmation_event = _event
                elif _event is not None and _validity is not None and _validity.valid:
                    human_confirmation = True
                    confirmation_event = _event
            # Fix-2 P1-1: a declaração vigente travada difere da declaração da
            # execução — o artefato em voo é obsoleto e é DESCARTADO (nada de
            # artefato/detecção/revisão/APPLIED desta execução persiste); o
            # laço refaz extração/reconciliação automáticas de fato para a
            # declaração/autoridade vigentes. Autoridade humana invalidada não
            # revive: o laço revalida a mais recente (já INVALIDATED acima).
            if pending_failure is None and marked:
                fresh_declared = get_declared_procedure_types(current)
                if fresh_declared != declared_types:
                    obsolete_execution = True
            if pending_failure is None and not obsolete_execution:
                case.structured_data = artifact
                case.save(update_fields=["structured_data"])
                llm1_payload: dict[str, object] = {
                    "prompts": prompt_versions,
                    "declared_types": list(declared_types),
                    "counts": _count_payload(artifact),
                    "retry_used": retry_used,
                }
                if marked and pre_digests is not None:
                    # D3: os três digests (nunca textos/valores) vão no evento de
                    # conclusão e, havendo retenção, na revisão — a confirmação os
                    # compara contra o snapshot do servidor (pré-dispatch).
                    llm1_payload["source_fingerprint"] = pre_digests["source_fingerprint"]
                    llm1_payload["anonymized_text_digest"] = pre_digests["anonymized_text_digest"]
                    llm1_payload["pseudonym_map_digest"] = pre_digests["pseudonym_map_digest"]
                _record_event(
                    case,
                    event_type=CaseEventType.CASE_LLM1_COMPLETED,
                    payload=llm1_payload,
                    user=user,
                    role=role,
                )
                reconciliation = _record_detection(
                    case, declared_types, artifact, user=user, role=role
                )
                detected_types = _detected_types(reconciliation)
                if human_confirmation and confirmation_event is not None:
                    _record_applied(
                        case,
                        confirmation_event=confirmation_event,
                        automatic=detected_types,
                        effective=tuple(declared_types),
                        user=user,
                        role=role,
                    )
                elif reconciliation.has_divergence:
                    case.manual_review_required = True
                    case.manual_review_reason = PROCEDURE_DIVERGENCE_REASON
                    case.save(update_fields=["manual_review_required", "manual_review_reason"])
                    # Fix-2 P1-3 (HD-001): a revisão automática equivalente à
                    # mais recente (mesma classificação + mesmos digests) não
                    # é duplicada; revisão genuinamente alterada é registrada.
                    already_recorded = marked and _review_already_recorded(
                        case, reconciliation.classification(), pre_digests
                    )
                    if not already_recorded:
                        divergence_payload: dict[str, object] = {
                            "classification": reconciliation.classification()
                        }
                        if marked and pre_digests is not None:
                            divergence_payload["source_fingerprint"] = pre_digests[
                                "source_fingerprint"
                            ]
                            divergence_payload["anonymized_text_digest"] = pre_digests[
                                "anonymized_text_digest"
                            ]
                            divergence_payload["pseudonym_map_digest"] = pre_digests[
                                "pseudonym_map_digest"
                            ]
                        _record_event(
                            case,
                            event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE,
                            payload=divergence_payload,
                            user=user,
                            role=role,
                        )

        if pending_failure is not None:
            raise LlmPipelineError(
                pending_failure,
                "confirmação de procedimento inválida (integridade) — sem persistir.",
            )
        if obsolete_execution:
            continue
        return Llm1ExtractionResult(
            declared_types=declared_types,
            detected_types=detected_types,
            reconciliation=reconciliation,
            retry_used=retry_used,
            human_confirmation=human_confirmation,
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
