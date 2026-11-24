"""Autoridade e integridade da revisão humana de procedimento (D1–D3/D6).

``CaseEvent`` é a autoridade durável — nenhum JSON mutável no ``Case`` é
segunda autoridade (precedente ats-web ``procedure_review.py``, adaptado ao
HMD). Este módulo concentra: digests versionados da fonte/texto anonimizado/
mapa (D3), marcador de contrato (D1), revisão/confirmação mais recentes,
validação de vigência com precedência de integridade sobre revisão/declaração,
invalidação uma-vez e definitiva, detecção original da revisão e o predicado
estrutural de completude (D6).

Nenhum import de pipeline/LLM/anonymization (D6: ``cases`` não depende dos
consumidores); o predicado de completude é estrutural — a validação detalhada
de schema continua no pipeline. Fingerprints/digests nunca persistem textos
nem valores do mapa (só hashes versionados ``v1:``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.cases.procedures import get_declared_procedure_types
from apps.cases.units import unit_label

# Versão do payload de autoridade e dos digests versionados (D3).
PROCEDURE_REVIEW_VERSION = 1
_DIGEST_PREFIX = "v1:"
REVIEW_ROLE = "nir"
REVIEW_LOCK_CONTEXT = "nir_procedure_review"

# Motivos de invalidação (D3). Integridade/malformação → FAILED operacional no
# worker (sem dispatch); revisão/declaração com integridade preservada → gate
# automático. Motivo desconhecido deve ser tratado como falha (fail-closed):
# só os dois motivos de gate são permissivos.
REASON_OK = "ok"
REASON_PAYLOAD_INVALID = "payload_invalid"
REASON_ACTOR_INVALID = "actor_invalid"
REASON_SOURCE_CHANGED = "source_changed"
REASON_SOURCE_UNAVAILABLE = "source_unavailable"
REASON_ANONYMIZED_MISMATCH = "anonymized_mismatch"
REASON_ANONYMIZATION_MISSING = "anonymization_missing"
REASON_MAP_MISMATCH = "pseudonym_map_mismatch"
REASON_REVIEW_SUPERSEDED = "review_superseded"
REASON_DECLARATION_CHANGED = "declaration_changed"

FAILED_REASONS = frozenset(
    {
        REASON_PAYLOAD_INVALID,
        REASON_ACTOR_INVALID,
        REASON_SOURCE_CHANGED,
        REASON_SOURCE_UNAVAILABLE,
        REASON_ANONYMIZED_MISMATCH,
        REASON_ANONYMIZATION_MISSING,
        REASON_MAP_MISMATCH,
    }
)
GATE_REASONS = frozenset({REASON_REVIEW_SUPERSEDED, REASON_DECLARATION_CHANGED})

_CATALOG_ORDER: dict[str, int] = {
    profile.procedure_type: index for index, profile in enumerate(PROCEDURE_PROFILES)
}
_CATALOG_TYPES = frozenset(_CATALOG_ORDER)
_POLICY_RECOMMENDATIONS = frozenset({"recomenda_aceitar", "recomenda_recusar"})
_LLM_SUGGESTIONS = frozenset({"aceitar", "recusar"})

# Classes de detecção que contam como "detectado" na revisão original (D3:
# somente ``match`` e ``missing_declaration``; ``not_detected`` não).
_DETECTED_CLASSES = frozenset({"match", "missing_declaration"})


@dataclass(frozen=True)
class ConfirmationValidity:
    """Resultado da validação de vigência de uma confirmação (puro, sem writes)."""

    valid: bool
    reason: str


def is_gate_reason(reason: str) -> bool:
    """Motivo permite o fallback automático (só revisão/declaração íntegres)."""
    return reason in GATE_REASONS


def is_failed_reason(reason: str) -> bool:
    """Motivo exige FAILED operacional (integridade/malformação — fail-closed)."""
    return reason in FAILED_REASONS or reason not in GATE_REASONS


# ── Digests versionados (D3) ───────────────────────────────────────────────


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_anonymized_text_digest(anonymized_text: str) -> str:
    """Digest versionado do texto anonimizado (SHA-256 UTF-8)."""
    return _DIGEST_PREFIX + _sha256_hex((anonymized_text or "").encode("utf-8"))


def compute_pseudonym_map_digest(pseudonym_map: Mapping[str, object]) -> str:
    """Digest versionado do mapa canônico (chaves ordenadas, separadores fixos).

    Serialização JSON canônica sem normalizar valores — qualquer troca de token,
    valor ou tipo-código altera o digest.
    """
    canonical = json.dumps(
        dict(pseudonym_map), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return _DIGEST_PREFIX + _sha256_hex(canonical.encode("utf-8"))


def compute_source_fingerprint(
    document_hashes: Iterable[tuple[int, int, str]],
    extracted_text: str,
) -> str:
    """Fingerprint versionado da fonte principal (D3, serialização não ambígua).

    ``document_hashes`` é ``(id, posição, hex SHA-256 dos bytes)`` na ordem
    das posições; ``extracted_text`` entra pelo hex do UTF-8. Nome de arquivo e
    anexos NÃO entram (a análise é do documento principal); logs dos bytes/
    textos nunca são emitidos.
    """
    lines = ["v1"]
    for document_id, position, hex_digest in sorted(document_hashes, key=lambda entry: entry[1]):
        lines.append(f"{document_id}:{position}:{hex_digest}")
    lines.append(f"text:{_sha256_hex((extracted_text or '').encode('utf-8'))}")
    combined = "\n".join(lines) + "\n"
    return _DIGEST_PREFIX + _sha256_hex(combined.encode("utf-8"))


def anonymized_text_digest_for_case(case: Case) -> str:
    """Digest vigente do texto anonimizado do caso."""
    return compute_anonymized_text_digest(case.anonymized_text or "")


def pseudonym_map_digest_for_case(case: Case) -> str:
    """Digest vigente do mapa de pseudônimos do caso."""
    raw = case.pseudonym_map
    if isinstance(raw, dict):
        return compute_pseudonym_map_digest(raw)
    return compute_pseudonym_map_digest({})


def source_fingerprint_for_case(case: Case) -> str | None:
    """Fingerprint vigente da fonte principal; ``None`` se indisponível (D3).

    Documentos principais em ordem de posição (ids/posições + hash dos bytes
    em chunks) + ``extracted_text``. Sem documento, arquivo sem nome ou erro de
    leitura = indisponível (nunca hash de vazio como autorização).
    """
    documents = list(case.documents.order_by("position"))
    if not documents:
        return None
    document_hashes: list[tuple[int, int, str]] = []
    for document in documents:
        if not document.file.name:
            return None
        try:
            hasher = hashlib.sha256()
            document.file.open("rb")
            try:
                while True:
                    chunk = document.file.read(65536)
                    if not chunk:
                        break
                    hasher.update(chunk)
            finally:
                document.file.close()
        except Exception:
            return None
        document_hashes.append((document.pk, document.position, hasher.hexdigest()))
    return compute_source_fingerprint(document_hashes, case.extracted_text or "")


# ── Marcador e eventos mais recentes ───────────────────────────────────────


def has_procedure_review_marker(case: Case) -> bool:
    """Caso sob o contrato novo? (D1: ENABLED com ``version=1`` é a marca.)

    Curto-circuita no primeiro marcador válido — e sem marcador não há
    confirmação nova, gate novo nem completude nova (casamento histórico).
    """
    events = CaseEvent.objects.filter(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED
    )
    for event in events:
        payload = event.payload
        if isinstance(payload, dict) and payload.get("version") == PROCEDURE_REVIEW_VERSION:
            return True
    return False


def latest_review_event(case: Case) -> CaseEvent | None:
    """Revisão (divergência) mais recente por (timestamp, id)."""
    return (
        CaseEvent.objects.filter(case=case, event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE)
        .order_by("-timestamp", "-id")
        .first()
    )


def latest_confirmation_event(case: Case) -> CaseEvent | None:
    """Confirmação humana mais recente por (timestamp, id)."""
    return (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED
        )
        .order_by("-timestamp", "-id")
        .first()
    )


def invalidated_reason_for(case: Case, confirmation_event: CaseEvent) -> str | None:
    """Motivo da invalidação correlacionada já registrada, se houver."""
    invalidated = (
        CaseEvent.objects.filter(
            case=case,
            event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED,
            payload__confirmation_event_id=confirmation_event.pk,
        )
        .order_by("id")
        .first()
    )
    if invalidated is None:
        return None
    payload = invalidated.payload
    if isinstance(payload, dict):
        reason = payload.get("reason")
        return str(reason) if isinstance(reason, str) and reason else None
    return None


def record_review_invalidated(case: Case, confirmation_event: CaseEvent, reason: str) -> CaseEvent:
    """Emite ``INVALIDATED`` uma vez por confirmação (idempotente, D3).

    Re-execuções do worker não entopem a auditoria com invalidações repetidas
    da mesma confirmação/motivo — o primeiro registro é definitivo.
    """
    existing = (
        CaseEvent.objects.filter(
            case=case,
            event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED,
            payload__confirmation_event_id=confirmation_event.pk,
        )
        .order_by("id")
        .first()
    )
    if existing is not None:
        return existing
    return CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={"confirmation_event_id": confirmation_event.pk, "reason": reason},
    )


# ── Validação de autoridade ────────────────────────────────────────────────


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_confirmation(case: Case, event: CaseEvent) -> ConfirmationValidity:
    """Valida a vigência de UMA confirmação contra o caso (D3, pura).

    Ordem fixa (precedência): payload/versão/seleção → ator/papel → integridade
    vigente (fonte, texto anonimizado, mapa) → invalidação já registrada
    (definitiva quando íntegra, nunca revalida) → revisão/declaração. Problema
    de integridade tem precedência mesmo sobre invalidação de gate histórica
    e mesmo se revisão/declaração também mudou. Não escolhe confirmação
    antiga: o chamador passa a mais recente.
    """
    payload = event.payload
    if not isinstance(payload, dict):
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    if payload.get("version") != PROCEDURE_REVIEW_VERSION:
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    review_event_id = payload.get("review_event_id")
    if not _is_int(review_event_id):
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    previous = payload.get("previous_declared_procedures")
    confirmed = payload.get("confirmed_procedures")
    if not isinstance(previous, list) or not previous:
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    if not isinstance(confirmed, list) or len(confirmed) != 1:
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    if any(not isinstance(item, str) or item not in _CATALOG_TYPES for item in previous):
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    if any(not isinstance(item, str) or item not in _CATALOG_TYPES for item in confirmed):
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    selection_changed = payload.get("selection_changed")
    if not isinstance(selection_changed, bool):
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    if selection_changed != (list(previous) != list(confirmed)):
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    justification = payload.get("justification")
    if not isinstance(justification, str):
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    stripped = justification.strip()
    if not stripped or len(stripped) > 500 or "\x00" in justification:
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)

    if event.actor_id is None or event.actor_role != REVIEW_ROLE:
        return ConfirmationValidity(valid=False, reason=REASON_ACTOR_INVALID)
    payload_role = payload.get("actor_role")
    if payload_role is not None and payload_role != REVIEW_ROLE:
        return ConfirmationValidity(valid=False, reason=REASON_ACTOR_INVALID)

    # Integridade vigente tem precedência sobre o fallback de gate de uma
    # invalidação histórica (fix-1 P1-5/P1-6): se fonte/texto/mapa falham
    # agora, o desfecho é FAILED, nunca o motivo permissivo registrado.
    # A invalidação histórica continua definitiva quando a integridade passa.
    source_current = source_fingerprint_for_case(case)
    if source_current is None:
        return ConfirmationValidity(valid=False, reason=REASON_SOURCE_UNAVAILABLE)
    payload_source = payload.get("source_fingerprint")
    if not isinstance(payload_source, str) or not payload_source:
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    if source_current != payload_source:
        return ConfirmationValidity(valid=False, reason=REASON_SOURCE_CHANGED)

    if not (case.anonymized_text or ""):
        return ConfirmationValidity(valid=False, reason=REASON_ANONYMIZATION_MISSING)
    payload_anonymized = payload.get("anonymized_text_digest")
    if not isinstance(payload_anonymized, str) or not payload_anonymized:
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    if anonymized_text_digest_for_case(case) != payload_anonymized:
        return ConfirmationValidity(valid=False, reason=REASON_ANONYMIZED_MISMATCH)

    payload_map = payload.get("pseudonym_map_digest")
    if not isinstance(payload_map, str) or not payload_map:
        return ConfirmationValidity(valid=False, reason=REASON_PAYLOAD_INVALID)
    if pseudonym_map_digest_for_case(case) != payload_map:
        return ConfirmationValidity(valid=False, reason=REASON_MAP_MISMATCH)

    recorded = invalidated_reason_for(case, event)
    if recorded is not None:
        return ConfirmationValidity(valid=False, reason=recorded)

    review = latest_review_event(case)
    if review is None or review.pk != review_event_id:
        return ConfirmationValidity(valid=False, reason=REASON_REVIEW_SUPERSEDED)
    declared = list(get_declared_procedure_types(case))
    if declared != list(confirmed):
        return ConfirmationValidity(valid=False, reason=REASON_DECLARATION_CHANGED)
    return ConfirmationValidity(valid=True, reason=REASON_OK)


def latest_confirmation_validity(
    case: Case,
) -> tuple[CaseEvent | None, ConfirmationValidity | None]:
    """(confirmação mais recente, validade) — ``(None, None)`` = caminho automático."""
    event = latest_confirmation_event(case)
    if event is None:
        return None, None
    return event, validate_confirmation(case, event)


def get_valid_confirmation(case: Case) -> tuple[tuple[str, ...] | None, CaseEvent | None]:
    """``(efetivo, evento)`` só quando a confirmação vigente é válida (sem writes).

    Sem confirmação ou com a mais recente inválida, devolve ``(None, evento)``
    sem tentar confirmação antiga — quem trata INVALIDATED/FAILED/gate é o
    chamador (worker), nunca este leitor.
    """
    event, validity = latest_confirmation_validity(case)
    if event is None or validity is None or not validity.valid:
        return None, event
    payload = event.payload
    assert isinstance(payload, dict)
    confirmed = payload.get("confirmed_procedures")
    assert isinstance(confirmed, list)
    ordered = tuple(sorted((str(item) for item in confirmed), key=_CATALOG_ORDER.__getitem__))
    return ordered, event


# ── Aviso médico + auditoria legível (slice 004, D8/R1–R2) ─────────────────

_PROFILE_LABEL_BY_TYPE: dict[str, str] = {
    profile.procedure_type: profile.label for profile in PROCEDURE_PROFILES
}


def _type_label(procedure_type: str) -> str:
    """Rótulo canônico do tipo (fallback: o próprio código)."""
    return _PROFILE_LABEL_BY_TYPE.get(procedure_type, procedure_type)


def _ordered_labels(procedure_types: Iterable[str]) -> str:
    """Rótulos na ordem canônica do catálogo, unidos por " + "."""
    ordered = sorted(
        (str(item) for item in procedure_types),
        key=lambda code: _CATALOG_ORDER.get(code, len(_CATALOG_ORDER)),
    )
    return " + ".join(_type_label(code) for code in ordered)


def _sanitized_procedure_list(value: object) -> tuple[str, ...]:
    """Lista de tipos válidos do catálogo em ordem canônica (D3)."""
    if not isinstance(value, list):
        return ()
    detected = [str(item) for item in value if isinstance(item, str) and item in _CATALOG_TYPES]
    return tuple(sorted(detected, key=_CATALOG_ORDER.__getitem__))


def build_doctor_confirmation_notice(case: Case) -> dict[str, object] | None:
    """Projeção determinística do aviso médico (D8/R1–R2, puro, sem writes).

    Fonte: ``APPLIED`` mais recente + ``CONFIRMED`` referenciada (nunca o
    último CONFIRMED solto nem metadata mutável). Sem aplicação/confirmação
    localizável, sem ator ou com referência quebrada → ``None`` (nenhum aviso
    fictício em automático/legado). Detecção original vem da revisão
    referenciada (só classes match/missing_declaration); detecção nova vem
    exatamente de ``APPLIED.automatic_procedures`` (ordem canônica,
    admitindo ``[]``) — nunca uniões/conflitos. Textos livres seguem crus no
    dict; o template escapa (nunca ``mark_safe``).
    """
    applied = (
        CaseEvent.objects.filter(case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED)
        .order_by("-timestamp", "-id")
        .first()
    )
    if applied is None or not isinstance(applied.payload, dict):
        return None
    confirmation_id = applied.payload.get("confirmation_event_id")
    if not _is_int(confirmation_id):
        return None
    assert isinstance(confirmation_id, int)
    confirmation = CaseEvent.objects.filter(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED,
        pk=confirmation_id,
    ).first()
    if confirmation is None or not isinstance(confirmation.payload, dict):
        return None
    if confirmation.actor_id is None:
        return None
    payload = confirmation.payload
    confirmed = _sanitized_procedure_list(payload.get("confirmed_procedures"))
    if len(confirmed) != 1:
        return None
    previous = _sanitized_procedure_list(payload.get("previous_declared_procedures"))
    if not previous:
        return None
    selection_changed = payload.get("selection_changed")
    if not isinstance(selection_changed, bool):
        return None
    review_event_id = payload.get("review_event_id")
    if not _is_int(review_event_id):
        return None
    assert isinstance(review_event_id, int)
    review = CaseEvent.objects.filter(case=case, pk=review_event_id).first()
    if review is None or review.event_type != CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE:
        return None
    original = original_detected_types(review)
    new_detected = _sanitized_procedure_list(applied.payload.get("automatic_procedures"))
    justification = payload.get("justification")
    justification_text = str(justification).strip() if isinstance(justification, str) else ""
    actor = confirmation.actor
    author_display = ""
    if actor is not None:
        display = getattr(actor, "display_name", "")
        author_display = str(display).strip() if display else ""
        if not author_display:
            author_display = getattr(actor, "username", "") or ""
    if not author_display:
        return None
    from django.utils.timezone import localtime as _localtime

    timestamp = confirmation.timestamp
    return {
        "confirmed_procedures": confirmed,
        "confirmed_label": _ordered_labels(confirmed),
        "previous_declared_procedures": previous,
        "previous_label": _ordered_labels(previous),
        "selection_changed": selection_changed,
        "original_detected": original,
        "original_label": _ordered_labels(original),
        "new_detected": new_detected,
        "new_label": _ordered_labels(new_detected),
        "show_new_detection": tuple(new_detected) != tuple(original),
        "author_display": author_display,
        "author_role": confirmation.actor_role or "",
        "confirmed_at": _localtime(timestamp).strftime("%d/%m/%Y %H:%M") if timestamp else "",
        "justification": justification_text,
    }


_INVALIDATED_REASON_LABELS: dict[str, str] = {
    REASON_SOURCE_CHANGED: "a fonte principal mudou após a revisão",
    REASON_SOURCE_UNAVAILABLE: "a fonte principal ficou indisponível",
    REASON_ANONYMIZED_MISMATCH: "o texto anonimizado mudou após a revisão",
    REASON_ANONYMIZATION_MISSING: "o texto anonimizado ficou indisponível",
    REASON_MAP_MISMATCH: "o mapa de pseudônimos mudou após a revisão",
    REASON_REVIEW_SUPERSEDED: "há revisão mais recente",
    REASON_DECLARATION_CHANGED: "a seleção declarada mudou após a confirmação",
    REASON_PAYLOAD_INVALID: "o registro da confirmação está inválido",
    REASON_ACTOR_INVALID: "a autoria da confirmação está inválida",
}


_REAPPEARANCE_EVIDENCE_LABELS: dict[str, str] = {
    "occurrence_number": "Nº de ocorrência",
    "patient_cns": "CNS",
    "nir_confirmed": "Confirmado pelo NIR",
    "origin_unit": "Mesma unidade de origem",
    "age_divergence": "Divergência de idade",
    "cns_conflict": "Conflito de CNS",
    "ambiguous_strong": "Múltiplos candidatos na fila",
}

_REAPPEARANCE_RESOLUTION_LABELS: dict[str, str] = {
    "medical_replacement": "Substituição médica",
    "scheduling_repeat": "Repetição no agendamento",
    "prior_context": "Contexto de caso anterior",
}


def event_detail_lines(event: CaseEvent) -> list[str]:
    """Linhas legíveis da trilha do painel para os eventos da revisão (D8).

    Projetadas só do payload (sem consultar o Case mutável); ``[]`` para
    qualquer outro tipo — leitores passam todos os eventos por aqui sem
    ramificar por tipo na view. Os eventos da intercorrência pós-encerramento
    (slice 002, D3/D4) também projetam linhas: motivo + snapshot
    administrativo (unidade/data-hora/local — sem PHI).
    """
    payload = event.payload if isinstance(event.payload, dict) else {}
    event_type = event.event_type
    if event_type == CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED:
        previous = _sanitized_procedure_list(payload.get("previous_declared_procedures"))
        confirmed = _sanitized_procedure_list(payload.get("confirmed_procedures"))
        if not previous or not confirmed:
            return []
        lines: list[str] = []
        if payload.get("selection_changed"):
            lines.append(
                f"Seleção alterada: {_ordered_labels(previous)} → {_ordered_labels(confirmed)}."
            )
        else:
            lines.append(f"Seleção mantida: {_ordered_labels(confirmed)}.")
        justification = payload.get("justification")
        if isinstance(justification, str) and justification.strip():
            lines.append(f"Justificativa: {justification.strip()}")
        return lines
    if event_type == CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED:
        effective = _sanitized_procedure_list(payload.get("effective_procedures"))
        automatic = _sanitized_procedure_list(payload.get("automatic_procedures"))
        if not effective:
            return []
        lines = [f"Conjunto em análise: {_ordered_labels(effective)}."]
        if automatic:
            lines.append(f"Detecção automática registrada: {_ordered_labels(automatic)}.")
        else:
            lines.append("Nenhum procedimento detectado na nova extração.")
        return lines
    if event_type == CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED:
        reason = payload.get("reason")
        label = _INVALIDATED_REASON_LABELS.get(str(reason), str(reason or "motivo técnico"))
        return [f"Confirmação desconsiderada: {label}. A análise segue as regras automáticas."]
    if event_type == CaseEventType.CASE_POST_CLOSURE_ISSUE_OPENED:
        return _post_closure_opened_detail_lines(payload)
    if event_type == CaseEventType.CASE_POST_CLOSURE_ISSUE_CLOSED:
        return ["Intercorrência pós-encerramento encerrada."]
    if event_type == CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED:
        return ["Caso sob o contrato de revisão de procedimento do NIR."]
    if event_type == CaseEventType.CASE_PROCEDURE_REPROCESSING_REQUESTED:
        return ["Reprocessamento do mesmo caso solicitado após confirmação."]
    if event_type == CaseEventType.CASE_REAPPEARANCE_SCHEDULE_FAILED:
        return [
            "Falha ao agendar a recuperação automática; a pendência segue "
            "sem agenda e depende de suporte técnico."
        ]
    if event_type == CaseEventType.CASE_REAPPEARANCE_RETRY_EXHAUSTED:
        reason = payload.get("reason_code")
        labels = {
            "retry_exhausted": "limite de tentativas atingido",
            "window_exceeded": "janela de 24 horas atingida",
            "state_ineligible": "par/revisão deixou de ser elegível",
        }
        label = labels.get(str(reason), "limite técnico atingido")
        return [
            f"Recuperação automática esgotada ({label}); a pendência "
            "permanece auditada e depende de suporte técnico."
        ]
    if event_type == CaseEventType.CASE_REAPPEARANCE_REENQUEUED:
        return ["Recuperação técnica reenfileirada; nova tentativa agendada."]
    if event_type == CaseEventType.CASE_REAPPEARANCE_CONFIRMED:
        lines_conf: list[str] = ["Vínculo de reapresentação confirmado pelo NIR."]
        evidence_code = payload.get("evidence_code")
        if isinstance(evidence_code, str) and evidence_code in _REAPPEARANCE_EVIDENCE_LABELS:
            lines_conf.append(f"Critério: {_REAPPEARANCE_EVIDENCE_LABELS[evidence_code]}.")
        resolution_kind = payload.get("resolution_kind")
        if isinstance(resolution_kind, str) and resolution_kind in _REAPPEARANCE_RESOLUTION_LABELS:
            lines_conf.append(f"Resolução: {_REAPPEARANCE_RESOLUTION_LABELS[resolution_kind]}.")
        return lines_conf
    if event_type == CaseEventType.CASE_REAPPEARANCE_INDEPENDENT:
        lines_indep: list[str] = ["Par marcado como independente para esta revisão."]
        evidence_code = payload.get("evidence_code")
        if isinstance(evidence_code, str) and evidence_code in _REAPPEARANCE_EVIDENCE_LABELS:
            lines_indep.append(
                f"Critério avaliado: {_REAPPEARANCE_EVIDENCE_LABELS[evidence_code]}."
            )
        return lines_indep
    if event_type == CaseEventType.CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED:
        scheduled = payload.get("recovery_scheduled") is True
        if scheduled:
            return ["Falha ao enfileirar o reprocessamento; recuperação programada."]
        return ["Falha ao enfileirar o reprocessamento; processamento pendente de suporte."]
    return []


def _post_closure_opened_detail_lines(payload: Mapping[str, object]) -> list[str]:
    """Linhas do evento de abertura pós-encerramento (slice 002, D3/D4).

    Motivo de texto livre + snapshot administrativo do agendamento anterior
    (unidade/data-hora/local — o precedente vigente do desmarcar). Sem PHI:
    nada de nome, registro ou dado clínico; o fuso de exibição é o local da
    aplicação, como as respostas ao NIR.
    """
    lines: list[str] = []
    reason = payload.get("reason")
    if isinstance(reason, str) and reason.strip():
        lines.append(f"Motivo: {reason.strip()}.")
    snapshot = payload.get("scheduling_snapshot")
    if isinstance(snapshot, Mapping):
        parts: list[str] = []
        unit = snapshot.get("scheduled_unit")
        if isinstance(unit, int):
            parts.append(unit_label(unit) or f"Unidade {unit}")
        scheduled_at = snapshot.get("scheduled_datetime")
        if isinstance(scheduled_at, str) and scheduled_at:
            try:
                parsed = datetime.fromisoformat(scheduled_at)
            except ValueError:
                parsed = None
            if parsed is not None and timezone.is_aware(parsed):
                parts.append(timezone.localtime(parsed).strftime("%d/%m/%Y %H:%M"))
        location = snapshot.get("scheduled_location")
        if isinstance(location, str) and location.strip():
            parts.append(location.strip())
        if parts:
            lines.append(f"Agendamento anterior: {' · '.join(parts)}.")
    return lines


# ── Detecção original e conjunto efetivo ───────────────────────────────────


def original_detected_types(review_event: CaseEvent) -> tuple[str, ...]:
    """Detecção automática original (D3/D8): revisão referenciada, ordem canônica.

    Considera só classes ``match`` e ``missing_declaration`` do payload
    ``classification`` (nunca ``not_detected``); admissão de ``[]`` quando nada
    foi detectado.
    """
    payload = review_event.payload
    if not isinstance(payload, dict):
        return ()
    classification = payload.get("classification")
    if not isinstance(classification, dict):
        return ()
    detected = [
        str(procedure_type)
        for procedure_type, kind in classification.items()
        if isinstance(procedure_type, str)
        and isinstance(kind, str)
        and kind in _DETECTED_CLASSES
        and procedure_type in _CATALOG_TYPES
    ]
    return tuple(sorted(detected, key=_CATALOG_ORDER.__getitem__))


def effective_procedure_types(case: Case) -> tuple[str, ...]:
    """Conjunto efetivo da análise (confirmação válida ou declaração corrente)."""
    effective, _event = get_valid_confirmation(case)
    if effective is not None:
        return effective
    return get_declared_procedure_types(case)


# ── Completude estrutural (D6) ─────────────────────────────────────────────


def is_processing_complete(case: Case) -> bool:
    """Há extração válida, resumo útil e análise completa do conjunto efetivo?

    Predicado estrutural puro (sem LLM/schema detalhado — validação fina segue
    no pipeline): sem retenção pendente, ``structured_data`` com ``pedido``,
    ``summary_text.strip()`` não vazio, policy e sugestões cobrindo EXATAMENTE
    o conjunto efetivo e agregado consistente. Derivados limpos na confirmação
    (D4) impedem que artefatos do perfil anterior satisfaçam a guarda.
    """
    if case.manual_review_required:
        return False
    effective = effective_procedure_types(case)
    if not effective:
        return False
    structured = case.structured_data
    if not isinstance(structured, dict) or not structured:
        return False
    pedido = structured.get("pedido")
    if not isinstance(pedido, dict):
        return False
    if not (case.summary_text or "").strip():
        return False
    policy_result = case.policy_result
    if not isinstance(policy_result, dict):
        return False
    if set(policy_result) != set(effective):
        return False
    for entry in policy_result.values():
        if not isinstance(entry, dict):
            return False
        if entry.get("recommendation") not in _POLICY_RECOMMENDATIONS:
            return False
    suggested = case.suggested_action
    if not isinstance(suggested, dict):
        return False
    procedures = suggested.get("procedures")
    if not isinstance(procedures, dict):
        return False
    if set(procedures) != set(effective):
        return False
    for entry in procedures.values():
        if not isinstance(entry, dict):
            return False
        if entry.get("suggestion") not in _LLM_SUGGESTIONS:
            return False
    aggregate = suggested.get("aggregate")
    if not isinstance(aggregate, dict):
        return False
    aggregate_suggestion = aggregate.get("suggestion")
    if aggregate_suggestion not in _LLM_SUGGESTIONS:
        return False
    any_refused = any(
        isinstance(entry, dict) and entry.get("suggestion") == "recusar"
        for entry in procedures.values()
    )
    expected = "recusar" if any_refused else "aceitar"
    aggregate_matches: bool = aggregate_suggestion == expected
    return aggregate_matches
