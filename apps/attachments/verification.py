"""Verificação LLM de patient-match dos anexos (change attachment-processing-ocr,
slice 003, design D4/R2–R4/R6).

``verify_attachment(case, attachment)`` encerra o pipeline do anexo: o LLM de
texto recebe EXCLUSIVAMENTE tokens — o texto anonimizado do anexo
(``anonymized_text``, produzido pela semeadura de R1 no espaço de tokens do
caso) + o token do paciente do caso (``patient_token``, reverse lookup em
``case.pseudonym_map``) — e devolve o resultado ``{patient_match, summary,
evidence}`` sob o schema strict do slice. O prompt é o seed versionado
``ATTACHMENT_VERIFICATION`` (29º em ``apps/llm/prompts_seed.py``; NÃO usa o
mecanismo por-perfil de ``build_case_prompts``): o conteúdo concentra papel +
regras + instruções e os placeholders ``{{attachment_text}}``/``{{patient_token}}``,
montado como mensagem única de usuário.

Resultado e evento no MESMO ``transaction.atomic``: persistência de
``patient_match``/``verification_summary``/``verification_evidence`` +
``status=processed`` + ``processed_at`` + ``CASE_ATTACHMENT_PROCESSED`` (payload
match+método). Falha de LLM/parse/validação → ``failed`` + ``failed_reason`` +
``CASE_ATTACHMENT_FAILED`` (padrão do tasks.py), SEM efeito no caso. Sem
paciente/token no caso → verificação em modo sem-comparação: o LLM ainda avalia
o documento, mas o ``patient_match`` persistido é ``unknown`` (R2 — o prompt
instrui a saída; o serviço garante o invariante).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.attachments.models import AttachmentStatus, CaseAttachment, PatientMatch
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent
from apps.llm.models import ActivePromptNotFoundError, PromptTemplate
from apps.llm.prompts_seed import ATTACHMENT_VERIFICATION
from apps.pipeline.json_parser import LlmJsonParseError, decode_llm_json_object
from apps.pipeline.llm import LlmError, get_llm_client

logger = logging.getLogger(__name__)

SYSTEM_ROLE = "system"

# Frase do campo ``{{patient_token}}`` quando o caso não tem paciente/token:
# o prompt instrui unknown e o serviço garante (modo sem-comparação, R2).
_NO_TOKEN_FIELD = (
    "não informado (paciente do caso desconhecido — sem comparação de "
    "identidade; patient_match=unknown)"
)

# Schema strict da verificação (D4/R3): o envelope ``{name, schema}`` é o que o
# cliente LLM recebe (ele envolve com ``strict: true`` no response_format).
# Patient_match é enum de 3 estados — a comparação é instrução de prompt; o
# schema obriga o formato JSON da resposta.
ATTACHMENT_VERIFICATION_JSON_SCHEMA: dict[str, Any] = {
    "name": "AttachmentVerificationResult",
    "schema": {
        "type": "object",
        "properties": {
            "patient_match": {
                "type": "string",
                "enum": ["match", "mismatch", "unknown"],
            },
            "summary": {"type": "string"},
            "evidence": {"type": "string"},
        },
        "required": ["patient_match", "summary", "evidence"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class VerificationOutcome:
    """Resultado decodificado/validado da verificação (R4)."""

    patient_match: str
    summary: str
    evidence: str


class AttachmentVerificationError(ValueError):
    """Resposta LLM fora do contrato (schema/parse/validação) — vira ``failed``."""


def patient_token(case: Case) -> str | None:
    """Token do paciente do caso no ``pseudonym_map`` (reverse lookup, R2).

    Procura a entrada cujo valor real é igual a ``case.patient_name``
    (case-insensitive). Sem ``patient_name`` ou sem token correspondente →
    ``None`` (verificação segue sem comparação).
    """
    patient = (case.patient_name or "").strip()
    if not patient:
        return None
    pseudonym_map = case.pseudonym_map
    if not isinstance(pseudonym_map, dict):
        return None
    for token, entry in pseudonym_map.items():
        if not isinstance(token, str) or not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if isinstance(value, str) and value.strip().casefold() == patient.casefold():
            return token
    return None


def verify_attachment(case: Case, attachment: CaseAttachment) -> None:
    """Verifica o anexo anonimizado contra o paciente do caso (R4, sem efeito no caso).

    LLM de texto (``settings.LLM1_MODEL``) vê apenas o texto anonimizado do
    anexo + o token do paciente; o resultado é persistido na row + evento
    ``CASE_ATTACHMENT_PROCESSED`` (processed). Falha tipada (LLM/parse/schema)
    → ``failed`` + ``failed_reason`` + ``CASE_ATTACHMENT_FAILED``. Sem
    paciente/token → ``patient_match=unknown`` garantido (modo sem-comparação,
    R2). Qualquer outra exceção propaga — o worker (tasks.py) é a rede de
    segurança fail-closed por anexo.
    """
    token = patient_token(case)
    try:
        outcome = _request_and_decode(attachment, token)
    except (
        LlmError,
        LlmJsonParseError,
        AttachmentVerificationError,
        ActivePromptNotFoundError,
    ) as exc:
        _mark_failed(attachment, exc)
        return
    if token is None:
        # Modo sem-comparação: mesmo que o modelo responda match/mismatch, sem
        # token do caso não há comparação possível (R2) — o documento segue
        # avaliado (summary/evidence), a identidade fica unknown.
        outcome = VerificationOutcome(
            patient_match=PatientMatch.UNKNOWN,
            summary=outcome.summary,
            evidence=outcome.evidence,
        )
    _persist_processed(attachment, outcome)


def _request_and_decode(attachment: CaseAttachment, token: str | None) -> VerificationOutcome:
    """Monta a mensagem do seed, chama o LLM e decodifica/valida a resposta.

    Falhas tipadas propagam (LlmError de transporte, LlmJsonParseError do
    parser tolerante, AttachmentVerificationError de resposta fora do
    contrato, ActivePromptNotFoundError de prompt não seedado) — o chamador
    decide o destino (``failed``).
    """
    template = PromptTemplate.get_active_prompt(ATTACHMENT_VERIFICATION).content
    user_content = template.replace("{{attachment_text}}", attachment.anonymized_text).replace(
        "{{patient_token}}", token if token is not None else _NO_TOKEN_FIELD
    )
    messages: list[dict[str, str]] = [{"role": "user", "content": user_content}]
    client = get_llm_client()
    raw_response = client.complete(
        settings.LLM1_MODEL,
        messages,
        json_schema=ATTACHMENT_VERIFICATION_JSON_SCHEMA,
    )
    return _decode_outcome(raw_response)


def _decode_outcome(raw_response: str) -> VerificationOutcome:
    """Parse tolerante (json_parser) + validação do contrato do slice (R4).

    Rejeita patient_match fora do enum e summary/evidence não-string — resposta
    fora do contrato → ``AttachmentVerificationError`` (vira ``failed``).
    """
    decoded = decode_llm_json_object(raw_response)
    patient_match = decoded.get("patient_match")
    summary = decoded.get("summary")
    evidence = decoded.get("evidence")
    if not isinstance(patient_match, str) or patient_match not in PatientMatch.values:
        raise AttachmentVerificationError(
            "resposta da verificação sem patient_match válido (match|mismatch|unknown)"
        )
    if not isinstance(summary, str) or not isinstance(evidence, str):
        raise AttachmentVerificationError("resposta da verificação sem summary/evidence textuais")
    return VerificationOutcome(
        patient_match=patient_match,
        summary=summary.strip(),
        evidence=evidence.strip(),
    )


def _persist_processed(attachment: CaseAttachment, outcome: VerificationOutcome) -> None:
    """Persiste o resultado + fecha o anexo + evento, num único atomic (R4)."""
    with transaction.atomic():
        current = _reload(attachment)
        if current is None:
            return
        current.patient_match = outcome.patient_match
        current.verification_summary = outcome.summary
        current.verification_evidence = outcome.evidence
        current.status = AttachmentStatus.PROCESSED
        current.processed_at = timezone.now()
        current.save(
            update_fields=[
                "patient_match",
                "verification_summary",
                "verification_evidence",
                "status",
                "processed_at",
            ]
        )
        CaseEvent.objects.create(
            case=current.case,
            event_type=CaseEventType.CASE_ATTACHMENT_PROCESSED,
            actor_type=ActorType.SYSTEM,
            actor=None,
            actor_role=SYSTEM_ROLE,
            payload={
                "patient_match": outcome.patient_match,
                "method": current.extraction_method,
            },
        )


def _mark_failed(attachment: CaseAttachment, exc: Exception) -> None:
    """Marca ``failed`` + motivo + evento de falha (fail-closed por anexo)."""
    reason = f"falha na verificação do anexo: {exc}"
    with transaction.atomic():
        current = _reload(attachment)
        if current is None:
            return
        current.status = AttachmentStatus.FAILED
        current.failed_reason = reason
        current.save(update_fields=["status", "failed_reason"])
        CaseEvent.objects.create(
            case=current.case,
            event_type=CaseEventType.CASE_ATTACHMENT_FAILED,
            actor_type=ActorType.SYSTEM,
            actor=None,
            actor_role=SYSTEM_ROLE,
            payload={
                "filename": current.original_filename,
                "reason": reason,
            },
        )


def _reload(attachment: CaseAttachment) -> CaseAttachment | None:
    """Re-lê a row (corrida com a ciência do NIR): removida → no-op silencioso."""
    try:
        return CaseAttachment.objects.select_related("case").get(pk=attachment.pk)
    except CaseAttachment.DoesNotExist:
        return None
