"""Classificador conservador de identidade de reapresentação (slice 001, D1).

Serviço de domínio PURO (sem DB, sem I/O) compartilhado pelo pipeline
(prior-case, S1) e pelos fluxos de fila/conferência (S2/S5): decide, para um
PAR de casos, se há evidência forte, necessidade de conferência NIR ou
ausência de candidato — a partir de nome normalizado, regulação, CNS válido,
origem e idades, sempre com o MESMO conjunto efetivo declarado de
procedimentos.

Matriz D1 (nome igual + procedimento igual exigidos em todos os ramos):

| evidência                            | resultado            |
| regulação igual não vazia OU CNS válido igual | forte, se único e sem conflito |
| forte com idades informadas e diferença > 1   | conferência NIR      |
| nome + origem iguais; idades ausentes ou diff <= 1 | conferência NIR |
| somente nome + origem e diferença > 1          | sem candidato        |
| nome diferente, procedimento diferente ou sem segunda evidência | sem candidato |

CNS válidos diferentes — mesmo com regulação igual — impedem a automação e
geram conferência com conflito visível; regulações diferentes NÃO são
conflito por si sós (motivo da feature). Idade zero é informada (válida);
``None`` é ausente. Vazio nunca é igualdade. Mais de um candidato ativo
plausível impede a seleção automática — a unicidade é decidida pelo chamador
(prior-case/ fila), nunca por cadeia transitiva aqui.
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
import logging as _logging
import re
import unicodedata
import uuid as _uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
from typing import TYPE_CHECKING, Literal

from django.conf import settings as _settings
from django.db import IntegrityError as _IntegrityError
from django.db import connection as _connection
from django.db import transaction as _transaction
from django.db.models import Q as _Q
from django.utils import timezone as _timezone

from apps.cases.closure import close_case_as_reappearance as _close_original
from apps.cases.events import CaseEventType as _CaseEventType
from apps.cases.models import ActorType as _ActorType
from apps.cases.models import Case as _Case
from apps.cases.models import CaseEvent as _CaseEvent
from apps.cases.models import CaseReappearance as _CaseReappearance
from apps.cases.models import CaseStatus as _CaseStatus
from apps.cases.models import ReappearanceState as _ReappearanceState
from apps.cases.models import ResolutionKind as _ResolutionKind
from apps.cases.procedure_review import is_failed_reason as _is_failed_reason
from apps.cases.procedure_review import (
    latest_confirmation_validity as _latest_confirmation_validity,
)
from apps.cases.procedures import get_declared_procedure_types as _get_declared_procedure_types

if TYPE_CHECKING:
    from apps.accounts.models import User as _User

_logger = _logging.getLogger(__name__)


# Veredito do par: ``strong`` (automação permitida), ``needs_confirmation``
# (somente conferência NIR — sem efeito até S5) ou ``no_match``.
ReappearanceVerdict = Literal["strong", "needs_confirmation", "no_match"]

# Origens canônicas do match (compartilhadas com o pipeline e o presenter —
# D3): ``occurrence_number`` (regulação), ``patient_cns`` (CNS estrutural),
# ``nir_confirmed`` (conferência NIR vigente — consumida só em S5) e ``none``
# (evento sem match). ``name_birthdate_fallback`` existe apenas no modo legado
# e em eventos históricos.
PriorCaseOrigin = Literal[
    "occurrence_number",
    "patient_cns",
    "nir_confirmed",
    "none",
    "name_birthdate_fallback",
]

# Rótulos pt-BR por origem (fonte única consumida pelo presenter médico).
PRIOR_ORIGIN_LABELS: dict[str, str] = {
    "occurrence_number": "Nº de ocorrência",
    "patient_cns": "CNS",
    "nir_confirmed": "Confirmado pelo NIR",
    "name_birthdate_fallback": "Nome e nascimento",
}


@dataclass(frozen=True)
class IdentitySignals:
    """Sinais de identidade de um caso para o pareamento (valores brutos).

    ``patient_cns`` deve chegar validado-ou-vazio (o wrapper de anonimização
    só persiste dígitos de ``extract_patient_cns``); ``patient_age`` usa
    ``None`` para ausente (zero é idade informada válida).
    """

    name: str = ""
    record_number: str = ""
    patient_cns: str = ""
    origin_unit: str = ""
    patient_age: int | None = None
    procedures: tuple[str, ...] = ()


@dataclass(frozen=True)
class Classification:
    """Veredito do par com origem e conflito.

    ``origin`` é a origem do match SOMENTE quando ``verdict == "strong"``
    (``occurrence_number`` precede ``patient_cns``); em ``needs_confirmation``
    a origem só existe após a confirmação (``nir_confirmed``, S5) — aqui é
    ``None``. ``conflict`` marca CNS divergentes (visível à conferência).
    """

    verdict: ReappearanceVerdict
    origin: PriorCaseOrigin | None
    conflict: bool = False


def normalize_person_name(name: str) -> str:
    """Nome normalizado para o pareamento: sem acentos/maiúsculas/sem espaços.

    Implementação pura em Python (NFKD → ASCII + upper + remoção de
    whitespace) — canônica do linkage (``apps.pipeline.prior_case`` a
    reexporta como ``normalize_name``); sem distância aproximada.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", "", ascii_text).upper()


def normalize_origin_unit(value: str) -> str:
    """Origem normalizada: caixa/acentos/espaços repetidos (D1, sem aliases)."""
    decomposed = unicodedata.normalize("NFKD", value.strip())
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).upper().strip()


def effective_procedure_sets_equal(first: Iterable[str], second: Iterable[str]) -> bool:
    """Igualdade do conjunto EFETIVO declarado de tipos (D1).

    Compara como conjuntos (ordem irrelevante, sem duplicatas); tipo diferente
    nunca se funde — nem por CNS. Conjuntos vazios nunca são iguais aqui (sem
    procedimento não há o que parear).
    """
    first_set = set(first)
    second_set = set(second)
    if not first_set or not second_set:
        return False
    return first_set == second_set


def _valid_cns_digits(value: str) -> bool:
    """CNS válido (reuso de ``valid_cns`` sem dependência de import do módulo)."""
    from apps.anonymization.recognizers import valid_cns

    return valid_cns(value)


def _ages_compatible(first: int | None, second: int | None) -> bool:
    """Idades compatíveis: ausentes ou diferença de até um ano (D1).

    ``None`` é ausente (não impede); zero é informado. Diferença > 1 com ambas
    informadas exige conferência — nunca automação.
    """
    if first is None or second is None:
        return True
    return abs(first - second) <= 1


def classify_pair(current: IdentitySignals, prior: IdentitySignals) -> Classification:
    """Classifica o par (atual, prévio) pela matriz D1 (puro, sem DB)."""
    if not normalize_person_name(current.name) or not normalize_person_name(prior.name):
        return Classification(verdict="no_match", origin=None)
    if normalize_person_name(current.name) != normalize_person_name(prior.name):
        return Classification(verdict="no_match", origin=None)
    if not effective_procedure_sets_equal(current.procedures, prior.procedures):
        return Classification(verdict="no_match", origin=None)

    current_number = current.record_number.strip()
    prior_number = prior.record_number.strip()
    number_equal = bool(current_number) and current_number == prior_number

    current_cns = current.patient_cns.strip()
    prior_cns = prior.patient_cns.strip()
    current_cns_valid = bool(current_cns) and _valid_cns_digits(current_cns)
    prior_cns_valid = bool(prior_cns) and _valid_cns_digits(prior_cns)
    cns_equal = bool(current_cns_valid and prior_cns_valid and current_cns == prior_cns)
    # CNS válidos diferentes — mesmo com regulação igual — impedem a automação
    # e geram conferência com conflito visível (D1, linha 5).
    cns_conflict = bool(current_cns_valid and prior_cns_valid and current_cns != prior_cns)
    if cns_conflict:
        return Classification(verdict="needs_confirmation", origin=None, conflict=True)

    if number_equal or cns_equal:
        if not _ages_compatible(current.patient_age, prior.patient_age):
            return Classification(verdict="needs_confirmation", origin=None)
        origin: PriorCaseOrigin | None = "occurrence_number" if number_equal else "patient_cns"
        return Classification(verdict="strong", origin=origin)

    current_origin = normalize_origin_unit(current.origin_unit)
    prior_origin = normalize_origin_unit(prior.origin_unit)
    if current_origin and current_origin == prior_origin:
        if _ages_compatible(current.patient_age, prior.patient_age):
            return Classification(verdict="needs_confirmation", origin=None)
        return Classification(verdict="no_match", origin=None)

    return Classification(verdict="no_match", origin=None)


# ── Resolução de fila: substituição médica (slice 002, D2/D4/D6) ────────────

# Recuperação limitada do efeito pendente (D6): atraso inicial, base de
# duplicação, teto por tentativa, máximo de tentativas e janela máxima desde
# a primeira tentativa — o que atingir primeiro esgota. Cluster EXPLÍCITO
# ``llm`` (o default não é implantado) com o entry point do worker.
RETRY_MAX_ATTEMPTS = 24
RETRY_MAX_WINDOW_HOURS = 24
RETRY_BASE_DELAY_SECONDS = 60
RETRY_MAX_DELAY_SECONDS = 3600
RETRY_CLUSTER = "llm"
RETRY_FUNC = "apps.cases.reappearance_tasks.retry_reappearance_application"

# Motivos técnicos do adiamento/esgotamento (payloads e logs — sem PHI).
REASON_LEASE_BUSY = "lease_busy"
REASON_SCHEDULE_FAILED = "schedule_failed"
REASON_STATE_INELIGIBLE = "state_ineligible"
REASON_WINDOW_EXCEEDED = "window_exceeded"
REASON_RETRY_EXHAUSTED = "retry_exhausted"

# Kinds de resolução de FILA sob a unicidade parcial (S2+S3; o contexto
# histórico coexiste sem consumir a exclusividade).
_QUEUE_RESOLUTION_KINDS: tuple[str, str] = (
    _ResolutionKind.MEDICAL_REPLACEMENT.value,
    _ResolutionKind.SCHEDULING_REPEAT.value,
)

# Rótulos de evidência do vínculo aplicado (trilha/histórico — sem PII).
REAPPEARANCE_EVIDENCE_LABELS: dict[str, str] = {
    "occurrence_number": "Nº de ocorrência",
    "patient_cns": "CNS",
    "nir_confirmed": "Confirmado pelo NIR",
}

_DIGEST_VERSION = "v1:"


@dataclass(frozen=True)
class LockedSubstitution:
    """Candidato revalidado sob o grupo travado, pronto para aplicar."""

    prior: _Case
    evidence_code: str
    revision_digest: str


def is_reappearance_resolution_enabled() -> bool:
    """Resolução de fila ativa? (flag única da change — off preserva tudo aplicado)."""
    return bool(_settings.CASE_REAPPEARANCE_ENABLED)


def effective_wait_started_at(case: _Case) -> _datetime:
    """Marco efetivo da espera (função do módulo; o model expõe a property)."""
    return case.effective_wait_started_at


def compute_retry_delay_seconds(attempt: int) -> int:
    """Atraso da tentativa (60s duplicado, teto 3600s — D6)."""
    delay: int = RETRY_BASE_DELAY_SECONDS * (2 ** max(attempt - 1, 0))
    return min(delay, RETRY_MAX_DELAY_SECONDS)


def retry_schedule_name(
    reappearance_id: _uuid.UUID | str, attempt: int, revision_digest: str = ""
) -> str:
    """Nome determinístico do Schedule por vínculo+revisão+tentativa (dedupe).

    Inclui hash determinístico (16 hex) do digest versionado para que
    revisões distintas do mesmo vínculo não colidam nem dedupem entre si
    (D6/AUD-05). Sem revisão (legado), nome antigo por vínculo+tentativa.
    """
    if revision_digest:
        revision_hash = _hashlib.sha256(revision_digest.encode()).hexdigest()[:16]
        return f"reappearance-retry:{reappearance_id}:{revision_hash}:{attempt}"
    return f"reappearance-retry:{reappearance_id}:{attempt}"


def revision_digest_for_pair(incoming: _Case, prior: _Case) -> str:
    """Digest versionado do par (fontes/documentos, declaração, identidade).

    Só hashes hex e valores normalizados — nunca texto clínico, nome ou CNS
    em claro além do próprio hash (o digest identifica a revisão; a trilha
    nunca reconstrói PII a partir dele).
    """
    from apps.cases.procedure_review import source_fingerprint_for_case

    def _signals(case: _Case) -> dict[str, object]:
        return {
            "name": normalize_person_name(case.patient_name or ""),
            "number": (case.agency_record_number or "").strip(),
            "cns": (case.patient_cns or "").strip(),
            "origin": normalize_origin_unit(case.origin_unit or ""),
            "age": case.patient_age,
            "procedures": sorted(_get_declared_procedure_types(case)),
            "source": source_fingerprint_for_case(case),
        }

    canonical = _json.dumps(
        {"incoming": _signals(incoming), "prior": _signals(prior)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return _DIGEST_VERSION + _hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def acquire_reappearance_mutex(key: int) -> None:
    """Trava consultiva da série (D4 FINAL): primeira aquisição do fluxo final.

    ``pg_advisory_xact_lock`` pela chave signed-64 versionada do nome
    normalizado (sem PHI em logs). Chamar como PRIMEIRA operação dentro do
    atomic final, antes de qualquer ``select_for_update`` do fluxo.
    """
    with _connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
    _logger.debug("reappearance mutex adquirido para a série")


def _mutex_key(patient_name: str, fallback: str = "") -> int:
    """Chave signed-64 da série (D4/AUD-14): SHA-256 versionado do nome.

    Primeiros 8 bytes de ``SHA-256("v1:reappearance:<nome_normalizado>")``
    como inteiro signed de 64 bits — idêntica em todos os entrypoints, sem
    ``hash()`` de processo. Sem nome, a chave é a do nome vazio (conservadora:
    série única). ``fallback`` é legado ignorado (assinatura preservada).
    """
    del fallback
    normalized = normalize_person_name(patient_name or "")
    digest = _hashlib.sha256(f"v1:reappearance:{normalized}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class ReappearanceDriftError(Exception):
    """Deriva persistente de chave/conjunto após 3 tentativas (D4, erro técnico).

    Sinal explícito ao chamador — nunca sucesso ``stale``/``aborted`` silencioso.
    No pipeline, o orquestrador encaminha o incoming ainda em processamento ao
    FAILED guardado existente, sem repetir LLM nem agendar recuperação.
    """

    reason = "reappearance_drift"


class _DriftDetectedError(Exception):
    """Sinal interno de rollback: deriva detectada sob os locks (nova transação)."""


# Tentativas transacionais TOTAIS por chamada (D4): a primeira + 2 retomadas.
_FINAL_MAX_ATTEMPTS = 3


def _recorded_prior_ids(incoming_pk: _uuid.UUID) -> set[_uuid.UUID]:
    """Priors já registrados em vínculos do incoming (pendentes/aplicados, D4)."""
    return set(
        _CaseReappearance.objects.filter(
            incoming_case_id=incoming_pk,
            state__in=(_ReappearanceState.PENDING, _ReappearanceState.APPLIED),
        ).values_list("prior_case_id", flat=True)
    )


def _check_drift(
    *,
    acquired_key: int,
    locked_incoming: _Case,
    locked_ids: set[_uuid.UUID],
    fresh_plausible: list[_Case],
    recorded_ids: set[_uuid.UUID],
) -> None:
    """Revalida chave + conjunto de participantes APÓS os locks (D4/AUD-01).

    Compara a chave derivada do nome travado com a adquirida e recolecta os
    plausíveis frescos; qualquer diferença levanta ``_DriftDetectedError`` (rollback
    sem efeito parcial, retomada em nova transação, sem segundo mutex).
    """
    if _mutex_key(locked_incoming.patient_name or "") != acquired_key:
        raise _DriftDetectedError("mutex key drift")
    fresh_ids = {locked_incoming.pk} | {row.pk for row in fresh_plausible} | recorded_ids
    if fresh_ids != locked_ids:
        raise _DriftDetectedError("participant set drift")


def _identity_signals_for(case: _Case) -> IdentitySignals:
    """Sinais de identidade do caso para o classificador (S2)."""
    return IdentitySignals(
        name=case.patient_name or "",
        record_number=case.agency_record_number or "",
        patient_cns=case.patient_cns or "",
        origin_unit=case.origin_unit or "",
        patient_age=case.patient_age,
        procedures=tuple(sorted(_get_declared_procedure_types(case))),
    )


def _authority_allows_completion(case: _Case) -> bool:
    """Autoridade vigente permite a substituição? (S2: sem confirmação nova).

    Sem confirmação → automático ok. Válida → ok (S2 exige par forte de
    qualquer modo). Inválida por integridade/malformação → recusa (o caso não
    deveria ter completado). Gate (revisão/declaração íntegres) → automático.
    """
    _event, validity = _latest_confirmation_validity(case)
    if validity is None:
        return True
    if validity.valid:
        return True
    return not _is_failed_reason(validity.reason)


# Filas do pool de ambiguidade (D3): médica e agendamento — S2 só aplica na
# médica, mas o candidato de agendamento bloqueia a automação como plausível.
_QUEUE_STATUSES: tuple[str, str, str] = (
    _CaseStatus.AWAITING_DOCTOR.value,
    _CaseStatus.SCHEDULER_REQUESTED.value,
    _CaseStatus.AWAITING_SCHEDULING.value,
)


def _is_strictly_anterior(prior: _Case, incoming: _Case) -> bool:
    """Anterioridade estrita pela tupla ``(created_at, case_id)`` (D3).

    Mesma ordem UUID do PostgreSQL (bytes do UUID): em empate exato de
    criação, só é anterior quem tem ``case_id`` menor — nunca os dois casos
    são mutuamente anteriores.
    """
    if prior.created_at != incoming.created_at:
        return prior.created_at < incoming.created_at
    return prior.case_id < incoming.case_id


def _plausible_candidates(incoming: _Case, rows: list[_Case]) -> list[tuple[_Case, Classification]]:
    """Plausíveis (``strong`` + ``needs_confirmation``) entre as rows (D1/D3)."""
    incoming_signals = _identity_signals_for(incoming)
    if not normalize_person_name(incoming_signals.name):
        return []
    if not incoming_signals.procedures:
        return []
    plausible: list[tuple[_Case, Classification]] = []
    for prior in rows:
        if prior.pk == incoming.pk:
            continue
        classification = classify_pair(incoming_signals, _identity_signals_for(prior))
        if classification.verdict != "no_match":
            plausible.append((prior, classification))
    return plausible


def _single_strong_medical(incoming: _Case, rows: list[_Case]) -> _Case | None:
    """Único plausível, forte e ainda médico — ou ``None`` (R2.2).

    Um só plausível ``strong`` em ``AWAITING_DOCTOR`` permite a substituição;
    qualquer outro plausível (fraco, conflitante ou de agendamento) ou um
    único plausível não-forte impede a automação.
    """
    plausible = _plausible_candidates(incoming, rows)
    if len(plausible) != 1:
        return None
    prior, classification = plausible[0]
    if classification.verdict != "strong":
        return None
    if prior.status != _CaseStatus.AWAITING_DOCTOR:
        return None
    return prior


def _candidate_queryset(incoming: _Case) -> list[_Case]:
    """Plausíveis D3 anteriores ao incoming (descoberta conservadora, R2.2/R2.3).

    Pré-seleção DB limitada às três filas de ambiguidade, à anterioridade
    estrita ``(created_at, case_id)`` e à interseção de procedimento declarado
    — sem varrer históricos (``CLEANED``/processamento/``FAILED`` ficam fora).
    Nome/origem/regulação/CNS são refinados em Python com a normalização
    canônica, para não perder valores normalização-equivalentes (acentos,
    caixa, espaços) que nenhum filtro bruto de igualdade capturaria.
    """
    incoming_name = normalize_person_name(incoming.patient_name or "")
    if not incoming_name:
        return []
    declared = tuple(sorted(_get_declared_procedure_types(incoming)))
    if not declared:
        return []
    anterior = _Q(created_at__lt=incoming.created_at) | _Q(
        created_at=incoming.created_at, case_id__lt=incoming.case_id
    )
    rows = list(
        _Case.objects.filter(status__in=_QUEUE_STATUSES)
        .filter(anterior)
        .filter(procedures__procedure_type__in=declared)
        .exclude(pk=incoming.pk)
        .order_by("created_at", "case_id")
        .distinct()
    )
    incoming_signals = _identity_signals_for(incoming)
    plausible: list[_Case] = []
    for row in rows:
        if normalize_person_name(row.patient_name or "") != incoming_name:
            continue
        if classify_pair(incoming_signals, _identity_signals_for(row)).verdict == "no_match":
            continue
        plausible.append(row)
    return plausible


def find_medical_replacement_candidate(incoming: _Case) -> _Case | None:
    """Candidato à substituição médica — ``None`` se ausente ou ambíguo (R2.2).

    Flag off → ``None`` (sem candidatos novos). Só devolve o original quando
    há exatamente um plausível (forte ou fraco/conflitante, em qualquer fila
    D3), forte e ainda aguardando decisão médica. Fraco/conflito/ambíguo nunca
    casa automaticamente (conferência é S5).
    """
    if not is_reappearance_resolution_enabled():
        return None
    return _single_strong_medical(incoming, _candidate_queryset(incoming))


def _has_live_lease(case: _Case, now: _datetime) -> bool:
    """Lease viva (humana ou worker) — jamais tomada à força (D4)."""
    return case.locked_until is not None and case.locked_until > now


def _existing_applied_link(incoming: _Case) -> _CaseReappearance | None:
    """Resolução de fila já aplicada para o incoming (idempotência)."""
    return (
        _CaseReappearance.objects.filter(
            incoming_case=incoming,
            state=_ReappearanceState.APPLIED,
            resolution_kind__in=_QUEUE_RESOLUTION_KINDS,
        )
        .order_by("applied_at", "id")
        .first()
    )


def _lock_group(case_ids: list[_uuid.UUID]) -> dict[_uuid.UUID, _Case]:
    """Trava o grupo inteiro ordenado por ``case_id`` (D4 FINAL).

    Ordem total crescente — dois fluxos com membros comuns travam na mesma
    ordem e nunca entram em deadlock entre si. Quem descobre membro novo após
    a trava aborta e recoletta, nunca expande fora de ordem.
    """
    ordered = sorted(set(case_ids))
    rows = list(_Case.objects.select_for_update().filter(pk__in=ordered).order_by("case_id"))
    return {row.pk: row for row in rows}


def _validate_locked_pair(incoming: _Case, prior: _Case) -> tuple[str, str] | None:
    """Revalida o par sob o grupo travado → (evidence_code, revision) ou ``None``."""
    if incoming.status != _CaseStatus.AWAITING_DOCTOR:
        return None
    if prior.status != _CaseStatus.AWAITING_DOCTOR:
        return None
    if not _is_strictly_anterior(prior, incoming):
        return None
    incoming_signals = _identity_signals_for(incoming)
    classification = classify_pair(incoming_signals, _identity_signals_for(prior))
    if classification.verdict != "strong" or classification.origin is None:
        return None
    if not _authority_allows_completion(incoming):
        return None
    return classification.origin, revision_digest_for_pair(incoming, prior)


def _emit_correlated_events(
    *,
    link: _CaseReappearance,
    incoming: _Case,
    prior: _Case,
    actor: _User | None,
    role: str,
) -> None:
    """Dois eventos correlacionados (D6): aplicado no novo, superado no original."""
    actor_type = _ActorType.USER if actor is not None else _ActorType.SYSTEM
    payload_applied: dict[str, object] = {
        "reappearance_id": str(link.pk),
        "prior_case_id": str(prior.pk),
        "evidence_code": link.evidence_code,
        "resolution_kind": link.resolution_kind,
        "revision_digest": link.revision_digest,
    }
    payload_superseded: dict[str, object] = {
        "reappearance_id": str(link.pk),
        "incoming_case_id": str(incoming.pk),
        "evidence_code": link.evidence_code,
        "resolution_kind": link.resolution_kind,
        "revision_digest": link.revision_digest,
    }
    _CaseEvent.objects.create(
        case_id=incoming.case_id,
        event_type=_CaseEventType.CASE_REAPPEARANCE_APPLIED,
        actor_type=actor_type,
        actor=actor,
        actor_role=role or "",
        payload=payload_applied,
    )
    _CaseEvent.objects.create(
        case_id=prior.case_id,
        event_type=_CaseEventType.CASE_REAPPEARANCE_SUPERSEDED,
        actor_type=actor_type,
        actor=actor,
        actor_role=role or "",
        payload=payload_superseded,
    )


def _inherit_wait_marker(incoming: _Case, prior: _Case) -> None:
    """Novo herda o menor marco da cadeia (D4); criação intacta."""
    candidates = [effective_wait_started_at(prior), incoming.created_at]
    if incoming.reappearance_wait_started_at is not None:
        candidates.append(incoming.reappearance_wait_started_at)
    marker = min(candidates)
    if incoming.reappearance_wait_started_at != marker:
        incoming.reappearance_wait_started_at = marker
        incoming.save(update_fields=["reappearance_wait_started_at"])


_TERMINAL_REASON_CODES: tuple[str, str, str] = (
    REASON_RETRY_EXHAUSTED,
    REASON_WINDOW_EXCEEDED,
    REASON_STATE_INELIGIBLE,
)


def _find_pending_link(
    *, incoming: _Case, prior: _Case, revision_digest: str
) -> _CaseReappearance | None:
    """PENDING do mesmo par/revisão/kind, se existir (reuso R4.4)."""
    return (
        _CaseReappearance.objects.filter(
            incoming_case=incoming,
            prior_case=prior,
            revision_digest=revision_digest,
            state=_ReappearanceState.PENDING,
            resolution_kind=_ResolutionKind.MEDICAL_REPLACEMENT,
        )
        .order_by("created_at", "id")
        .first()
    )


def _durable_retry_schedule_exists(link: _CaseReappearance, attempt: int) -> bool:
    """Agenda durável da MESMA revisão/tentativa (dedupe honesto D6).

    Nome por vínculo+revisão+tentativa, cluster/func explícitos e args com
    revisão+tentativa — agenda de outra revisão nunca prova dedupe.
    """
    from django_q.models import Schedule

    candidates = Schedule.objects.filter(
        name=retry_schedule_name(link.pk, attempt, link.revision_digest),
        func=RETRY_FUNC,
        cluster=RETRY_CLUSTER,
    )
    for schedule in candidates:
        args_text = schedule.args or ""
        if link.revision_digest in args_text and str(attempt) in args_text:
            return True
    return False


def _apply_locked(
    *,
    incoming: _Case,
    prior: _Case,
    evidence_code: str,
    revision_digest: str,
    user: _User | None,
    role: str,
) -> _CaseReappearance:
    """Efeito aplicado sob o grupo travado (vínculo + encerramento + eventos).

    Reuso R4.4: PENDING do mesmo par/revisão/kind é aplicado (mesmo pk);
    já aplicado retorna sem novo efeito/evento.
    """
    applied = (
        _CaseReappearance.objects.filter(
            incoming_case=incoming,
            prior_case=prior,
            revision_digest=revision_digest,
            state=_ReappearanceState.APPLIED,
            resolution_kind=_ResolutionKind.MEDICAL_REPLACEMENT,
        )
        .order_by("applied_at", "id")
        .first()
    )
    if applied is not None:
        return applied
    now = _timezone.now()
    pending = _find_pending_link(incoming=incoming, prior=prior, revision_digest=revision_digest)
    if pending is not None:
        pending = _CaseReappearance.objects.select_for_update().get(pk=pending.pk)
        if pending.last_reason_code in _TERMINAL_REASON_CODES:
            # D2: terminal mantem pending so p/ auditoria/suporte — nunca e
            # autorizacao de aplicacao automatica. Retorna o proprio pendente
            # (sem encerrar, sem eventos/heranca/agenda e sem tocar orcamento).
            return pending
        pending.state = _ReappearanceState.APPLIED
        pending.applied_at = now
        pending.evidence_code = evidence_code
        pending.canonical_case = incoming
        pending.save(update_fields=["state", "applied_at", "evidence_code", "canonical_case"])
        link = pending
    else:
        link = _CaseReappearance.objects.create(
            incoming_case=incoming,
            prior_case=prior,
            canonical_case=incoming,
            revision_digest=revision_digest,
            evidence_code=evidence_code,
            state=_ReappearanceState.APPLIED,
            resolution_kind=_ResolutionKind.MEDICAL_REPLACEMENT,
            applied_at=now,
        )
    _close_original(prior, user=user, role=role)
    _emit_correlated_events(link=link, incoming=incoming, prior=prior, actor=user, role=role)
    _inherit_wait_marker(incoming, prior)
    _drop_pending_schedules(link.pk)
    return link


def _drop_pending_schedules(reappearance_id: _uuid.UUID) -> None:
    """Remove Schedules pendentes do vínculo (caso aplicado — sem redispatch)."""
    from django_q.models import Schedule

    Schedule.objects.filter(name__startswith=f"reappearance-retry:{reappearance_id}:").delete()


def schedule_next_reappearance_retry(link: _CaseReappearance) -> bool:
    """Agenda a próxima tentativa (D6): orçamento, backoff e dedupe.

    Incrementa ``attempt_count`` e persiste ``first/next_attempt_at`` +
    ``last_reason_code`` na transação do chamador; o Schedule ONCE (cluster
    ``llm``, nome determinístico por vínculo+tentativa) é criado na MESMA
    transação — o cluster só enxerga rows commitadas. Esgotado (24 tentativas
    ou 24h) ou name duplicado em corrida → ``False`` sem alegar sucesso (o
    esgotamento emite o evento técnico único). Falha de persistência → propaga
    (o chamador mantém a pendência observável sem anunciar conclusão).
    """
    from django_q.models import Schedule
    from django_q.tasks import schedule as q_schedule

    if link.last_reason_code in _TERMINAL_REASON_CODES:
        return False
    now = _timezone.now()
    if link.first_attempt_at is None:
        link.first_attempt_at = now
    window_exceeded = now - link.first_attempt_at >= _timedelta(hours=RETRY_MAX_WINDOW_HOURS)
    if link.attempt_count >= RETRY_MAX_ATTEMPTS or window_exceeded:
        _mark_retry_exhausted(
            link,
            reason=(REASON_WINDOW_EXCEEDED if window_exceeded else REASON_RETRY_EXHAUSTED),
        )
        return False
    link.attempt_count += 1
    link.next_attempt_at = now + _timedelta(seconds=compute_retry_delay_seconds(link.attempt_count))
    link.last_reason_code = REASON_LEASE_BUSY
    link.save(
        update_fields=[
            "attempt_count",
            "first_attempt_at",
            "next_attempt_at",
            "last_reason_code",
        ]
    )
    try:
        q_schedule(
            RETRY_FUNC,
            str(link.pk),
            link.revision_digest,
            int(link.attempt_count),
            schedule_type=Schedule.ONCE,
            next_run=link.next_attempt_at,
            name=retry_schedule_name(link.pk, link.attempt_count, link.revision_digest),
            cluster=RETRY_CLUSTER,
        )
    except _IntegrityError as exc:
        # Dedupe honesto (fix-2 R4/D6): só alega sucesso se a agenda durável
        # da MESMA revisão/tentativa já existe (nome+args). Integridade
        # alheia nunca vira sucesso — propaga p/ schedule_failed do chamador.
        try:
            durable = _durable_retry_schedule_exists(link, int(link.attempt_count))
        except Exception:
            raise exc from None
        if durable and "same name" in str(exc).lower():
            _logger.info(
                "reappearance retry dedupado vínculo=%s tentativa=%s",
                link.pk,
                link.attempt_count,
            )
            return True
        raise
    return True


def _schedule_retry_or_mark_failed(link: _CaseReappearance) -> bool:
    """Agenda o retry com falha observável e transacionalmente válida (fix-2 R4/D6).

    Envolve ``schedule_next_reappearance_retry`` em savepoint para que falha
    de agenda/banco (RuntimeError/DatabaseError do broker/INSERT) não
    envenene o atomic externo; persiste PENDING + ``schedule_failed`` +
    ``next_attempt_at`` com backoff do attempt vigente, sem Schedule falso,
    sem fechar casos. Dedupe honesto continua ``True`` via schedule_next;
    demais falhas nunca alegam sucesso. Partilhado pelo deferral inicial e
    pelo reagendamento vencido (mesma semântica/orçamento).
    """
    previous_reason = link.last_reason_code
    try:
        with _transaction.atomic():
            return schedule_next_reappearance_retry(link)
    except Exception:
        _logger.warning(
            "reappearance retry nao agendado vinculo=%s motivo=%s",
            link.pk,
            REASON_SCHEDULE_FAILED,
            exc_info=True,
        )
        now = _timezone.now()
        try:
            failed_attempt = int(link.attempt_count)
        except (TypeError, ValueError):
            failed_attempt = 0
        if failed_attempt < 1:
            failed_attempt = 1
        link.last_reason_code = REASON_SCHEDULE_FAILED
        link.next_attempt_at = now + _timedelta(seconds=compute_retry_delay_seconds(failed_attempt))
        link.save(
            update_fields=[
                "attempt_count",
                "first_attempt_at",
                "next_attempt_at",
                "last_reason_code",
            ]
        )
        # AUD-03 (unidade D): a falha vive na trilha do incoming — nunca só
        # no campo/log. Falhas consecutivas sem recuperação intermediária
        # não duplicam o evento (mesma ocorrência); se nem a auditoria
        # persiste, log sanitizado + retorno False (nunca sucesso falso).
        if previous_reason != REASON_SCHEDULE_FAILED:
            _record_schedule_failed_event(link, attempts=failed_attempt)
        return False


def _record_schedule_failed_event(link: _CaseReappearance, *, attempts: int) -> None:
    """Evento sistêmico de falha de agenda no incoming (AUD-03, sanitizado)."""
    payload: dict[str, object] = {
        "reappearance_id": str(link.pk),
        "attempts": attempts,
        "reason_code": REASON_SCHEDULE_FAILED,
    }
    if link.next_attempt_at is not None:
        payload["next_attempt_at"] = link.next_attempt_at.isoformat()
    try:
        with _transaction.atomic():
            _CaseEvent.objects.create(
                case_id=link.incoming_case_id,
                event_type=_CaseEventType.CASE_REAPPEARANCE_SCHEDULE_FAILED,
                actor_type=_ActorType.SYSTEM,
                actor=None,
                actor_role="system",
                payload=payload,
            )
    except Exception:
        _logger.warning(
            "reappearance recuperacao nao auditada vinculo=%s motivo=%s",
            link.pk,
            REASON_SCHEDULE_FAILED,
            exc_info=True,
        )


def _mark_retry_exhausted(link: _CaseReappearance, *, reason: str) -> None:
    """Esgotamento auditado único (D6): mantém a pendência, sem encerrar."""
    if link.last_reason_code in (
        REASON_RETRY_EXHAUSTED,
        REASON_WINDOW_EXCEEDED,
        REASON_STATE_INELIGIBLE,
    ):
        return
    link.last_reason_code = reason
    link.save(update_fields=["last_reason_code"])
    _CaseEvent.objects.create(
        case_id=link.incoming_case_id,
        event_type=_CaseEventType.CASE_REAPPEARANCE_RETRY_EXHAUSTED,
        actor_type=_ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={
            "reappearance_id": str(link.pk),
            "attempts": link.attempt_count,
            "reason_code": reason,
        },
    )


# ── Recuperação observável (unidade D, R4.10/R4.11) ───────────────────────

# Estado ATUAL da recuperação de um incoming (somente leitura — GET nunca
# escreve): ``scheduled`` (lease viva com agenda real), ``schedule_failed``
# (sem agenda durável) ou ``terminal`` (depende de suporte).
RecoveryStatusKind = Literal["scheduled", "schedule_failed", "terminal"]


@dataclass(frozen=True)
class RecoveryStatus:
    """Pendência atual de recuperação do incoming (sem PII)."""

    kind: RecoveryStatusKind
    reason_code: str
    attempt_count: int
    has_schedule: bool
    next_attempt_at: _datetime | None
    reappearance_id: _uuid.UUID


def recovery_status_for_case(case: _Case) -> RecoveryStatus | None:
    """Pendência atual de recuperação do incoming, ou ``None`` se regularizada.

    Puro/leitura: consulta o PENDING mais recente do incoming e a existência
    honesta de agenda durável da tentativa vigente. Sem PENDING (aplicado,
    inexistente) não há aviso — o histórico permanece só na trilha.
    """
    link = (
        _CaseReappearance.objects.filter(incoming_case_id=case.pk, state=_ReappearanceState.PENDING)
        .order_by("-created_at", "-id")
        .first()
    )
    if link is None:
        return None
    if link.last_reason_code in _TERMINAL_REASON_CODES:
        return RecoveryStatus(
            kind="terminal",
            reason_code=link.last_reason_code,
            attempt_count=int(link.attempt_count),
            has_schedule=False,
            next_attempt_at=link.next_attempt_at,
            reappearance_id=link.pk,
        )
    has_schedule = _durable_retry_schedule_exists(link, int(link.attempt_count))
    return RecoveryStatus(
        kind="scheduled" if has_schedule else "schedule_failed",
        reason_code=link.last_reason_code or REASON_SCHEDULE_FAILED,
        attempt_count=int(link.attempt_count),
        has_schedule=has_schedule,
        next_attempt_at=link.next_attempt_at,
        reappearance_id=link.pk,
    )


@dataclass(frozen=True)
class ReenqueueOutcome:
    """Desfecho da avaliação/execução do reenqueue (saída do comando)."""

    status: Literal["simulation", "scheduled", "noop", "refused"]
    reason_code: str
    attempt_count: int
    next_attempt_at: _datetime | None


def _reenqueue_refusal_reason(
    link: _CaseReappearance,
    incoming: _Case | None = None,
    prior: _Case | None = None,
) -> str | None:
    """Motivo de recusa do reenqueue, ou ``None`` se elegível (leitura)."""
    if not is_reappearance_resolution_enabled():
        return "flag_off"
    if link.state != _ReappearanceState.PENDING:
        return "not_pending"
    if link.last_reason_code in _TERMINAL_REASON_CODES:
        return link.last_reason_code
    now = _timezone.now()
    if link.attempt_count >= RETRY_MAX_ATTEMPTS:
        return REASON_RETRY_EXHAUSTED
    if link.first_attempt_at is not None and now - link.first_attempt_at >= _timedelta(
        hours=RETRY_MAX_WINDOW_HOURS
    ):
        return REASON_WINDOW_EXCEEDED
    if incoming is None:
        incoming = _Case.objects.get(pk=link.incoming_case_id)
    if prior is None:
        prior = _Case.objects.get(pk=link.prior_case_id)
    if revision_digest_for_pair(incoming, prior) != link.revision_digest:
        return REASON_STATE_INELIGIBLE
    return None


def evaluate_reenqueue(link: _CaseReappearance) -> ReenqueueOutcome:
    """Simulação somente-leitura do reenqueue (nunca escreve)."""
    refusal = _reenqueue_refusal_reason(link)
    if refusal is not None:
        return ReenqueueOutcome(
            status="simulation",
            reason_code=refusal,
            attempt_count=int(link.attempt_count),
            next_attempt_at=link.next_attempt_at,
        )
    if _durable_retry_schedule_exists(link, int(link.attempt_count)):
        return ReenqueueOutcome(
            status="simulation",
            reason_code="valid_schedule_exists",
            attempt_count=int(link.attempt_count),
            next_attempt_at=link.next_attempt_at,
        )
    return ReenqueueOutcome(
        status="simulation",
        reason_code="eligible",
        attempt_count=int(link.attempt_count),
        next_attempt_at=link.next_attempt_at,
    )


def apply_reenqueue(link_id: _uuid.UUID) -> ReenqueueOutcome:
    """Executa o reenqueue técnico: agenda SOMENTE o efeito pendente (D6).

    Mutex→locks ordenados→revalidação no MESMO atomic; reutiliza
    ``schedule_next_reappearance_retry`` (orçamento/backoff/dedupe) e emite
    o evento de reenqueue no incoming. Nunca resolve/aplica, nunca toma
    lease, nunca zera orçamento nem reativa terminal/stale/aplicado.
    Falha de persistência propaga (rollback integral — nunca AGENDADO falso).
    """
    stored = _CaseReappearance.objects.get(pk=link_id)
    key_source = _Case.objects.only("case_id", "patient_name").get(pk=stored.incoming_case_id)
    acquired_key = _mutex_key(key_source.patient_name or "")
    with _transaction.atomic():
        acquire_reappearance_mutex(acquired_key)
        group = _lock_group([stored.incoming_case_id, stored.prior_case_id])
        link = _CaseReappearance.objects.select_for_update().get(pk=stored.pk)
        locked_incoming = group[link.incoming_case_id]
        if _mutex_key(locked_incoming.patient_name or "") != acquired_key:
            return ReenqueueOutcome(
                status="refused",
                reason_code=REASON_STATE_INELIGIBLE,
                attempt_count=int(link.attempt_count),
                next_attempt_at=link.next_attempt_at,
            )
        refusal = _reenqueue_refusal_reason(link, locked_incoming, group[link.prior_case_id])
        if refusal is not None:
            return ReenqueueOutcome(
                status="refused",
                reason_code=refusal,
                attempt_count=int(link.attempt_count),
                next_attempt_at=link.next_attempt_at,
            )
        if _durable_retry_schedule_exists(link, int(link.attempt_count)):
            return ReenqueueOutcome(
                status="noop",
                reason_code="valid_schedule_exists",
                attempt_count=int(link.attempt_count),
                next_attempt_at=link.next_attempt_at,
            )
        scheduled = schedule_next_reappearance_retry(link)
        link.refresh_from_db()
        if not scheduled:
            if _durable_retry_schedule_exists(link, int(link.attempt_count)):
                return ReenqueueOutcome(
                    status="noop",
                    reason_code="valid_schedule_exists",
                    attempt_count=int(link.attempt_count),
                    next_attempt_at=link.next_attempt_at,
                )
            return ReenqueueOutcome(
                status="refused",
                reason_code=link.last_reason_code or REASON_SCHEDULE_FAILED,
                attempt_count=int(link.attempt_count),
                next_attempt_at=link.next_attempt_at,
            )
        if not _durable_retry_schedule_exists(link, int(link.attempt_count)):
            raise RuntimeError("reenqueue sem agenda durável — rollback honesto")
        payload: dict[str, object] = {
            "reappearance_id": str(link.pk),
            "attempts": int(link.attempt_count),
            "reason_code": REASON_LEASE_BUSY,
        }
        if link.next_attempt_at is not None:
            payload["next_attempt_at"] = link.next_attempt_at.isoformat()
        _CaseEvent.objects.create(
            case_id=link.incoming_case_id,
            event_type=_CaseEventType.CASE_REAPPEARANCE_REENQUEUED,
            actor_type=_ActorType.SYSTEM,
            actor=None,
            actor_role="system",
            payload=payload,
        )
        return ReenqueueOutcome(
            status="scheduled",
            reason_code="eligible",
            attempt_count=int(link.attempt_count),
            next_attempt_at=link.next_attempt_at,
        )


def _defer_locked(
    *,
    incoming: _Case,
    prior: _Case,
    evidence_code: str,
    revision_digest: str,
    reason_code: str,
) -> _CaseReappearance:
    """Adiamento auditável (D4/D6): vínculo pendente + retry, sem efeito clínico.

    Reuso R4.4: PENDING do mesmo par/revisão/kind é reutilizado sem novo
    INSERT, sem consumo de orçamento e sem agenda duplicada; só agenda se
    a pendência está sem agenda durável por falha anterior (schedule_failed).
    """
    existing = _find_pending_link(incoming=incoming, prior=prior, revision_digest=revision_digest)
    if existing is not None:
        existing = _CaseReappearance.objects.select_for_update().get(pk=existing.pk)
        if existing.last_reason_code in _TERMINAL_REASON_CODES:
            # D2: terminal nunca reagenda — devolve o pendente sem agenda/
            # orcamento novos (visivel p/ suporte, sem conversao silenciosa).
            return existing
        if (
            existing.last_reason_code == REASON_SCHEDULE_FAILED
            and not _durable_retry_schedule_exists(existing, int(existing.attempt_count))
            and existing.last_reason_code not in _TERMINAL_REASON_CODES
        ):
            _schedule_retry_or_mark_failed(existing)
        existing.refresh_from_db()
        return existing
    link = _CaseReappearance.objects.create(
        incoming_case=incoming,
        prior_case=prior,
        canonical_case=incoming,
        revision_digest=revision_digest,
        evidence_code=evidence_code,
        state=_ReappearanceState.PENDING,
        resolution_kind=_ResolutionKind.MEDICAL_REPLACEMENT,
    )
    scheduled = _schedule_retry_or_mark_failed(link)
    if not scheduled and link.last_reason_code == REASON_LEASE_BUSY:
        # Esgotado já na primeira programação (orçamento herdado inválido):
        # mantém pendente sem Schedule — visível para reenqueue técnico.
        link.refresh_from_db()
    link.refresh_from_db()
    return link


def resolve_medical_replacement(
    incoming: _Case,
    *,
    user: _User | None = None,
    role: str = "system",
) -> _CaseReappearance | None:
    """Resolve a substituição médica do incoming (S2, entrada de serviço).

    Mutex→grupo ordenado→revalidação no MESMO atomic: aplica (vínculo +
    encerramento do original + eventos + espera herdada), adia (pendente +
    retry) com lease viva, ou não faz nada (sem candidato, ambíguo, par
    fraco/conflitante, tipo distinto, original já fora da fila). Flag off →
    ``None`` sem efeitos. Nunca adquire mutex depois de row lock do fluxo e
    nunca toma lease viva.
    """
    if not is_reappearance_resolution_enabled():
        return None
    existing = _existing_applied_link(incoming)
    if existing is not None:
        return existing
    for _attempt in range(_FINAL_MAX_ATTEMPTS):
        # Leitura SEM lock da chave estável (D4 passo 1) — antes de qualquer atomic.
        key_source = _Case.objects.only(
            "case_id",
            "patient_name",
            "agency_record_number",
            "patient_cns",
            "origin_unit",
            "patient_age",
            "created_at",
        ).get(pk=incoming.pk)
        acquired_key = _mutex_key(key_source.patient_name or "")
        try:
            with _transaction.atomic():
                acquire_reappearance_mutex(acquired_key)
                # Coleta fresca após o mutex + trava do grupo ordenada (D4 2–4).
                collect_source = _Case.objects.only(
                    "case_id",
                    "patient_name",
                    "agency_record_number",
                    "patient_cns",
                    "origin_unit",
                    "patient_age",
                    "created_at",
                ).get(pk=incoming.pk)
                plausible = _candidate_queryset(collect_source)
                recorded = _recorded_prior_ids(incoming.pk)
                locked_ids = {incoming.pk} | {row.pk for row in plausible} | recorded
                group = _lock_group(sorted(locked_ids))
                locked_incoming = group[incoming.pk]
                if locked_incoming.status != _CaseStatus.AWAITING_DOCTOR:
                    return None
                existing_locked = _existing_applied_link(locked_incoming)
                if existing_locked is not None:
                    return existing_locked
                # Revalidação pós-locks (D4 5): chave + conjunto fresco.
                _check_drift(
                    acquired_key=acquired_key,
                    locked_incoming=locked_incoming,
                    locked_ids=locked_ids,
                    fresh_plausible=_candidate_queryset(locked_incoming),
                    recorded_ids=_recorded_prior_ids(incoming.pk),
                )
                prior = _single_strong_medical(
                    locked_incoming,
                    [row for row in group.values() if row.pk != locked_incoming.pk],
                )
                if prior is None:
                    return None
                validated = _validate_locked_pair(locked_incoming, prior)
                if validated is None:
                    return None
                evidence_code, revision_digest = validated
                if _has_live_lease(prior, _timezone.now()):
                    return _defer_locked(
                        incoming=locked_incoming,
                        prior=prior,
                        evidence_code=evidence_code,
                        revision_digest=revision_digest,
                        reason_code=REASON_LEASE_BUSY,
                    )
                return _apply_locked(
                    incoming=locked_incoming,
                    prior=prior,
                    evidence_code=evidence_code,
                    revision_digest=revision_digest,
                    user=user,
                    role=role,
                )
        except _DriftDetectedError:
            continue  # rollback sem efeito; nova transação desde a leitura sem lock
    raise ReappearanceDriftError(
        f"reappearance drift persistente após {_FINAL_MAX_ATTEMPTS} tentativas "
        f"(incoming={incoming.pk})"
    )


# ── Recuperação limitada do efeito pendente (D6) ────────────────────────────


def handle_reappearance_retry(
    reappearance_id: _uuid.UUID | str,
    revision_digest: str,
    attempt: int | str,
) -> None:
    """Executa uma tentativa de recuperação (entry point lógico do Schedule).

    Todos os guards antes de qualquer efeito ou consumo de orçamento: vínculo
    existente e pendente, flag ligada, revisão/tentativa vigentes e
    ``next_attempt_at`` alcançado. Job atrasado/duplicado/adiantado é no-op.
    Terminais (exhausted/window/state_ineligible) nunca retomam, mesmo
    após liberação da lease. Drift de revisão torna a tentativa inelegível
    sem retarget/reset; lease viva reagenda; caso contrário aplica.
    Nunca nova análise LLM.
    """
    try:
        normalized_id = (
            reappearance_id
            if isinstance(reappearance_id, _uuid.UUID)
            else _uuid.UUID(str(reappearance_id))
        )
    except (ValueError, AttributeError):
        _logger.warning("reappearance retry com id inválido — no-op")
        return
    if isinstance(attempt, bool):
        return
    try:
        attempt_number = int(attempt)
    except (TypeError, ValueError):
        _logger.warning("reappearance retry com tentativa inválida — no-op")
        return
    try:
        stored = _CaseReappearance.objects.get(pk=normalized_id)
    except _CaseReappearance.DoesNotExist:
        return
    if stored.state != _ReappearanceState.PENDING:
        return
    if stored.last_reason_code in _TERMINAL_REASON_CODES:
        return
    if not is_reappearance_resolution_enabled():
        return
    if not isinstance(revision_digest, str) or revision_digest != stored.revision_digest:
        return
    if attempt_number != stored.attempt_count:
        return
    now = _timezone.now()
    if stored.next_attempt_at is not None and now < stored.next_attempt_at:
        return
    for _attempt in range(_FINAL_MAX_ATTEMPTS):
        # Leitura SEM lock da chave estável — antes de qualquer atomic (D4).
        key_source = _Case.objects.only("case_id", "patient_name").get(pk=stored.incoming_case_id)
        acquired_key = _mutex_key(key_source.patient_name or "")
        try:
            with _transaction.atomic():
                acquire_reappearance_mutex(acquired_key)
                collect_source = _Case.objects.only(
                    "case_id",
                    "patient_name",
                    "agency_record_number",
                    "patient_cns",
                    "origin_unit",
                    "patient_age",
                    "created_at",
                ).get(pk=stored.incoming_case_id)
                plausible = _candidate_queryset(collect_source)
                recorded = {stored.prior_case_id} | _recorded_prior_ids(stored.incoming_case_id)
                locked_ids = {stored.incoming_case_id} | {row.pk for row in plausible} | recorded
                group = _lock_group(sorted(locked_ids))
                link = _CaseReappearance.objects.select_for_update().get(pk=stored.pk)
                if link.state != _ReappearanceState.PENDING:
                    return
                if link.last_reason_code in _TERMINAL_REASON_CODES:
                    return
                if link.revision_digest != stored.revision_digest:
                    return
                if link.attempt_count != stored.attempt_count:
                    return
                if link.next_attempt_at is not None and _timezone.now() < link.next_attempt_at:
                    return
                locked_incoming = group[link.incoming_case_id]
                # Revalidação pós-locks ANTES de qualquer mutação (D4/AUD-01).
                _check_drift(
                    acquired_key=acquired_key,
                    locked_incoming=locked_incoming,
                    locked_ids=locked_ids,
                    fresh_plausible=_candidate_queryset(locked_incoming),
                    recorded_ids={link.prior_case_id} | _recorded_prior_ids(link.incoming_case_id),
                )
                if link.first_attempt_at is not None and now - link.first_attempt_at >= _timedelta(
                    hours=RETRY_MAX_WINDOW_HOURS
                ):
                    _mark_retry_exhausted(link, reason=REASON_WINDOW_EXCEEDED)
                    return
                if link.attempt_count >= RETRY_MAX_ATTEMPTS:
                    _mark_retry_exhausted(link, reason=REASON_RETRY_EXHAUSTED)
                    return
                if locked_incoming.status == _CaseStatus.CLEANED:
                    _mark_retry_exhausted(link, reason=REASON_STATE_INELIGIBLE)
                    return
                prior = _single_strong_medical(
                    locked_incoming,
                    [row for row in group.values() if row.pk != locked_incoming.pk],
                )
                if prior is None:
                    _mark_retry_exhausted(link, reason=REASON_STATE_INELIGIBLE)
                    return
                if prior.pk != link.prior_case_id:
                    _mark_retry_exhausted(link, reason=REASON_STATE_INELIGIBLE)
                    return
                validated = _validate_locked_pair(locked_incoming, prior)
                if validated is None:
                    _mark_retry_exhausted(link, reason=REASON_STATE_INELIGIBLE)
                    return
                evidence_code, fresh_digest = validated
                if fresh_digest != link.revision_digest:
                    # AUD-05: revisão imutável — drift inelegível sem retarget,
                    # sem reset de attempt_count/first_attempt_at, sem aplicar.
                    _mark_retry_exhausted(link, reason=REASON_STATE_INELIGIBLE)
                    return
                if _has_live_lease(prior, _timezone.now()):
                    _schedule_retry_or_mark_failed(link)
                    return
                link.state = _ReappearanceState.APPLIED
                link.applied_at = _timezone.now()
                link.evidence_code = evidence_code
                link.revision_digest = fresh_digest
                link.canonical_case = locked_incoming
                link.save(
                    update_fields=[
                        "state",
                        "applied_at",
                        "evidence_code",
                        "revision_digest",
                        "canonical_case",
                    ]
                )
                _close_original(prior, user=None, role="system")
                _emit_correlated_events(
                    link=link,
                    incoming=locked_incoming,
                    prior=prior,
                    actor=None,
                    role="system",
                )
                _inherit_wait_marker(locked_incoming, prior)
                _drop_pending_schedules(link.pk)
                return  # aplicação commitada: nunca recomeçar transação/mutex
        except _DriftDetectedError:
            continue
    raise ReappearanceDriftError(
        f"reappearance drift persistente após {_FINAL_MAX_ATTEMPTS} tentativas "
        f"(vínculo={stored.pk})"
    )


# ── Atomic final do orquestrador (D4 FINAL) ──────────────────────────────────


@dataclass(frozen=True)
class FinalizationOutcome:
    """Desfecho do atomic final (completar + substituir + liberar lease)."""

    status: str  # "completed" | "failed" | "stale" | "aborted"
    failure_reason: str | None = None
    lease_released: bool = False


def finalize_summarization(
    *,
    case_pk: _uuid.UUID,
    lock_token: _uuid.UUID,
    marked_execution: bool,
    execution_digests: dict[str, str | None] | None,
    execution_authority: tuple[int | None, bool | None, str | None],
    execution_declared: tuple[str, ...],
    user: _User | None = None,
    role: str = "system",
) -> FinalizationOutcome:
    """Atomic final reestruturado (D4 FINAL): mutex→grupo→revalidação→efeitos.

    Mesma transação: trava consultiva da série, grupo ordenado por
    ``case_id``, revalidação de autoridade/digests/completude (lógica
    portada do orquestrador, sem mudança de semântica), conclusão da
    sumarização, tentativa de substituição médica (aplica, adia com retry ou
    completa sem efeito) e liberação da própria lease. Nenhuma chamada externa
    ou deleção física sob os locks; ``CLEANED`` na entrada aborta.
    """
    from django_fsm import TransitionNotAllowed as _TransitionNotAllowed

    from apps.cases.locks import CaseLockConflictError as _CaseLockConflictError
    from apps.cases.locks import release_case_lock as _release_case_lock
    from apps.cases.procedure_review import (
        REASON_DECLARATION_CHANGED as _REASON_DECLARATION_CHANGED,
    )
    from apps.cases.procedure_review import (
        is_gate_reason as _is_gate_reason,
    )
    from apps.cases.procedure_review import (
        latest_confirmation_validity as _fresh_validity_of,
    )
    from apps.cases.procedure_review import (
        record_review_invalidated as _record_invalidated,
    )

    def _execution_digest_failure(snapshot: dict[str, str | None], case: _Case) -> str | None:
        from apps.cases.procedure_review import (
            REASON_ANONYMIZATION_MISSING as _R_ANON_MISSING,
        )
        from apps.cases.procedure_review import (
            REASON_ANONYMIZED_MISMATCH as _R_ANON_MISMATCH,
        )
        from apps.cases.procedure_review import (
            REASON_MAP_MISMATCH as _R_MAP_MISMATCH,
        )
        from apps.cases.procedure_review import (
            REASON_SOURCE_CHANGED as _R_SOURCE_CHANGED,
        )
        from apps.cases.procedure_review import (
            REASON_SOURCE_UNAVAILABLE as _R_SOURCE_UNAVAILABLE,
        )
        from apps.cases.procedure_review import (
            anonymized_text_digest_for_case as _anon_digest,
        )
        from apps.cases.procedure_review import (
            pseudonym_map_digest_for_case as _map_digest,
        )
        from apps.cases.procedure_review import (
            source_fingerprint_for_case as _source_fingerprint,
        )

        current_source = _source_fingerprint(case)
        if snapshot["source_fingerprint"] is not None and current_source is None:
            return _R_SOURCE_UNAVAILABLE
        if current_source != snapshot["source_fingerprint"]:
            return _R_SOURCE_CHANGED
        if _anon_digest(case) != snapshot["anonymized_text_digest"]:
            if not (case.anonymized_text or ""):
                return _R_ANON_MISSING
            return _R_ANON_MISMATCH
        if _map_digest(case) != snapshot["pseudonym_map_digest"]:
            return _R_MAP_MISMATCH
        return None

    for _attempt in range(_FINAL_MAX_ATTEMPTS):
        # Leitura SEM lock da chave estável — antes de qualquer atomic (D4).
        key_source = _Case.objects.only("case_id", "patient_name").get(pk=case_pk)
        acquired_key = _mutex_key(key_source.patient_name or "")
        try:
            with _transaction.atomic():
                acquire_reappearance_mutex(acquired_key)
                # Passos 2–3: coleta fresca de plausíveis + trava do grupo ordenada.
                prefilter_source = _Case.objects.only(
                    "case_id",
                    "patient_name",
                    "agency_record_number",
                    "patient_cns",
                    "origin_unit",
                    "patient_age",
                    "created_at",
                ).get(pk=case_pk)
                plausible = (
                    _candidate_queryset(prefilter_source)
                    if is_reappearance_resolution_enabled()
                    else []
                )
                group = _lock_group(
                    sorted({case_pk} | {row.pk for row in plausible} | _recorded_prior_ids(case_pk))
                )
                current = group[case_pk]
                if current.status == _CaseStatus.CLEANED:
                    return FinalizationOutcome(status="aborted")
                # Revalidação pós-locks ANTES de qualquer mutação (D4/AUD-01):
                # chave do nome travado + conjunto fresco de participantes.
                _check_drift(
                    acquired_key=acquired_key,
                    locked_incoming=current,
                    locked_ids={case_pk}
                    | {row.pk for row in plausible}
                    | _recorded_prior_ids(case_pk),
                    fresh_plausible=(
                        _candidate_queryset(current) if is_reappearance_resolution_enabled() else []
                    ),
                    recorded_ids=_recorded_prior_ids(case_pk),
                )
                # Passo 4: revalidação (autoridade/digests/completude — semântica do
                # orquestrador preservada byte a byte).
                final_failure: str | None = None
                final_stale_gate = False
                if marked_execution and execution_digests is not None:
                    digest_failure = _execution_digest_failure(execution_digests, current)
                    _fresh_event, fresh_validity = _fresh_validity_of(current)
                    fresh_authority = (
                        _fresh_event.pk if _fresh_event is not None else None,
                        fresh_validity.valid if fresh_validity is not None else None,
                        fresh_validity.reason if fresh_validity is not None else None,
                    )
                    if digest_failure is not None:
                        final_failure = digest_failure
                    elif (
                        fresh_authority != execution_authority
                        and fresh_validity is not None
                        and not fresh_validity.valid
                    ):
                        if _is_gate_reason(fresh_validity.reason):
                            final_stale_gate = True
                        else:
                            final_failure = fresh_validity.reason
                    elif _get_declared_procedure_types(current) != execution_declared:
                        final_stale_gate = True
                    if final_failure is not None or final_stale_gate:
                        if _fresh_event is not None:
                            record_reason = final_failure
                            if record_reason is None:
                                record_reason = (
                                    fresh_validity.reason
                                    if fresh_validity is not None and not fresh_validity.valid
                                    else _REASON_DECLARATION_CHANGED
                                )
                            _record_invalidated(current, _fresh_event, record_reason)
                if final_failure is not None:
                    return FinalizationOutcome(status="failed", failure_reason=final_failure)
                if final_stale_gate:
                    return FinalizationOutcome(status="stale")
                # Passo 5: concluir sumarização, aplicar substituição e liberar a
                # própria lease no MESMO atomic.
                from apps.cases.procedure_review import (
                    has_procedure_review_marker as _has_marker,
                )
                from apps.cases.procedure_review import (
                    is_processing_complete as _is_complete,
                )

                if _has_marker(current) and not _is_complete(current):
                    from apps.pipeline.llm import LlmPipelineError as _LlmPipelineError

                    raise _LlmPipelineError(
                        "procedure_review_incomplete",
                        "caso marcado sem extração válida, resumo ou análise completa "
                        "do conjunto efetivo — sem encaminhamento.",
                    )
                try:
                    current.complete_llm_summarization(user=user, role=role)
                except _TransitionNotAllowed:
                    from apps.pipeline.llm import LlmPipelineError as _LlmPipelineError

                    raise _LlmPipelineError(
                        "procedure_review_incomplete",
                        "transição final recusada — sem encaminhamento.",
                    ) from None
                if (
                    is_reappearance_resolution_enabled()
                    and current.status == _CaseStatus.AWAITING_DOCTOR
                ):
                    prior = _single_strong_medical(
                        current, [row for row in group.values() if row.pk != current.pk]
                    )
                    if prior is not None:
                        validated = _validate_locked_pair(current, prior)
                        if validated is not None:
                            evidence_code, revision_digest = validated
                            if _has_live_lease(prior, _timezone.now()):
                                _defer_locked(
                                    incoming=current,
                                    prior=prior,
                                    evidence_code=evidence_code,
                                    revision_digest=revision_digest,
                                    reason_code=REASON_LEASE_BUSY,
                                )
                            else:
                                _apply_locked(
                                    incoming=current,
                                    prior=prior,
                                    evidence_code=evidence_code,
                                    revision_digest=revision_digest,
                                    user=user,
                                    role=role,
                                )
                released = False
                try:
                    _release_case_lock(current, lock_token)
                    released = True
                except _CaseLockConflictError:
                    _logger.warning(
                        "finalize_summarization: lease expirou no atomic final",
                        exc_info=True,
                    )
                return FinalizationOutcome(status="completed", lease_released=released)
        except _DriftDetectedError:
            continue
    raise ReappearanceDriftError(
        f"reappearance drift persistente após {_FINAL_MAX_ATTEMPTS} tentativas (incoming={case_pk})"
    )


# ── Cadeia, histórico e métricas ────────────────────────────────────────────


def current_representative(case: _Case) -> _Case:
    """Representante atual da cadeia (elo vigente; guard contra ciclo)."""
    seen = {case.pk}
    current = case
    while True:
        link = (
            _CaseReappearance.objects.filter(
                prior_case=current,
                state=_ReappearanceState.APPLIED,
                resolution_kind=_ResolutionKind.MEDICAL_REPLACEMENT,
            )
            .select_related("incoming_case")
            .order_by("applied_at", "id")
            .first()
        )
        if link is None:
            return current
        nxt = link.incoming_case
        if nxt.pk in seen:
            return current
        seen.add(nxt.pk)
        current = nxt


def historical_reappearance_links(case: _Case) -> dict[str, object]:
    """Elo imediato + representante atual para o detalhe histórico (R1)."""
    link = (
        _CaseReappearance.objects.filter(
            prior_case=case,
            state=_ReappearanceState.APPLIED,
            resolution_kind__in=_QUEUE_RESOLUTION_KINDS,
        )
        .select_related("incoming_case")
        .order_by("applied_at", "id")
        .first()
    )
    if link is None:
        return {"immediate": None, "representative": case, "evidence_label": ""}
    return {
        "immediate": link.incoming_case,
        "representative": current_representative(case),
        "evidence_code": link.evidence_code,
        "evidence_label": REAPPEARANCE_EVIDENCE_LABELS.get(link.evidence_code, ""),
    }


def technical_reappearance_case_ids() -> set[_uuid.UUID]:
    """Casos encerrados por reapresentação (evento canônico — R5/métricas)."""
    return set(
        _CaseEvent.objects.filter(
            event_type=_CaseEventType.CASE_REAPPEARANCE_SUPERSEDED
        ).values_list("case_id", flat=True)
    )
