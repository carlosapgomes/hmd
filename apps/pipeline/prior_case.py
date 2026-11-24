"""Prior-case: casos prévios do mesmo paciente por procedimento (slice 005,
R4–R6, design D7; identidade nova no slice 001 de reapresentacao-casos-filas).

``lookup_prior_case_context(case, procedure_type) -> PriorCaseSummary | None``:
candidatas = outros casos com ``CaseProcedure`` do MESMO tipo e decisão
semântica registrada (``doctor_disposition != pending`` com
``doctor_decided_at`` — campos semânticos, não status FSM). Chave primária:
``agency_record_number`` igual (não-vazio) e decisão no intervalo fechado
``[created_at - PRIOR_CASE_WINDOW_DAYS, created_at]`` (default 7). **Fallback**
(apenas quando a primária não casa): nome normalizado
(``normalize_name``: sem acentos/maiúsculas/sem espaços — NFKD puro, sem ORM)
+ ``patient_birth_date`` iguais (não-nulos) e intervalo FECHADO
``decisão_prévia <= case.created_at <= decisão_prévia + FALLBACK_WINDOW_DAYS``
(default 15) — decisão "futura" (posterior ao ``created_at`` do caso atual)
nunca casa (correção do review). Prioridade: nº > fallback; escolhe a decisão
mais recente do pool vencedor; ``prior_denial_count`` conta as negações do
pool. O motivo é texto livre do médico e pode conter PII → o resumo que
alimenta o LLM2 usa o motivo **anonimizado pelo núcleo do change 05**
(``anonymize_text``); o original só vai à UI do médico (07). Sem match →
``None``. Até o change 07 não há decisões no banco — lookup vazio em produção
inicial (esperado; coberto com fixtures).

Modo novo (``CASE_REAPPEARANCE_ENABLED=True``, default dev/test): o lookup usa
SOMENTE a janela única ``CASE_REAPPEARANCE_WINDOW_DAYS`` (default 15) e a
identidade de ``apps.cases.reappearance`` — nome normalizado exigido +
conjunto efetivo declarado igual + (regulação igual OU CNS válido igual), sem
nascimento; idades como evidência auxiliar; conflito de CNS ou par fraco
(nome+origem) nunca vira contexto factual antes da confirmação NIR (S5).
Origens: ``occurrence_number | patient_cns | nir_confirmed | none``
(``nir_confirmed`` reservado à S5; ``name_birthdate_fallback`` só no legado e
em eventos históricos).

``record_prior_case_lookups(case, *, user, role)`` grava por procedimento
declarado o evento ``PRIOR_CASE_LOOKUP`` com a origem do modo vigente e
resumo enxuto (sem PII além do que já está no caso — o motivo anonimizado nem
entra no evento; quem consome é o resumo retornado ao orquestrador 006).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Func, Q, TextField

from apps.anonymization.services import anonymize_text
from apps.cases.events import CaseEventType
from apps.cases.models import (
    ActorType,
    Case,
    CaseEvent,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    DoctorDisposition,
    ReappearanceState,
    ResolutionKind,
)
from apps.cases.procedures import get_declared_procedure_types
from apps.cases.reappearance import (
    IdentitySignals,
    PriorCaseOrigin,
    classify_pair,
    normalize_person_name,
    revision_digest_for_pair,
)

if TYPE_CHECKING:
    from apps.accounts.models import User

logger = logging.getLogger(__name__)

# Normalização de nome do linkage (paridade com o classificador de S1 —
# alias do ``normalize_person_name`` canônico de ``apps.cases.reappearance``).
normalize_name = normalize_person_name


def is_reappearance_enabled() -> bool:
    """Identidade nova ativa? (S1, R5 — default true; prod força false)."""
    return bool(settings.CASE_REAPPEARANCE_ENABLED)


def reappearance_window_days() -> int:
    """Janela única do modo novo, em dias (S1, R5 — default 15)."""
    return int(settings.CASE_REAPPEARANCE_WINDOW_DAYS)


@dataclass(frozen=True)
class PriorCaseSummary:
    """Resumo de um caso prévio decidido, seguro para o LLM2 (D7).

    ``reason_anonymized`` é o motivo do médico passado pelo núcleo
    ``anonymize_text`` (nunca o texto cru); ``decided_at`` em ISO 8601;
    ``decision`` usa o vocabulário semântico de ``DoctorDisposition``.
    """

    prior_case_id: str
    procedure_type: str
    decided_at: str
    decision: str
    reason_anonymized: str
    prior_denial_count: int
    origin: PriorCaseOrigin
    confirmed_after_summarization: bool = False


def _decided_rows(case: Case, procedure_type: str) -> list[CaseProcedure]:
    """Rows decididas do tipo em OUTROS casos (semântica, não FSM)."""
    return list(
        CaseProcedure.objects.filter(
            procedure_type=procedure_type,
            doctor_disposition__in=(
                DoctorDisposition.APPROVED.value,
                DoctorDisposition.DENIED.value,
            ),
            doctor_decided_at__isnull=False,
        )
        .exclude(case=case)
        .select_related("case")
        .order_by("doctor_decided_at")
    )


def _number_rows(case: Case, rows: list[CaseProcedure], window_days: int) -> list[CaseProcedure]:
    """Pool primário: mesmo ``agency_record_number`` (não-vazio) na janela de dias."""
    current_number = case.agency_record_number.strip()
    if not current_number:
        return []
    window_start = case.created_at - timedelta(days=window_days)
    matched: list[CaseProcedure] = []
    for row in rows:
        other = row.case
        if not other.agency_record_number.strip():
            continue
        if other.agency_record_number.strip() != current_number:
            continue
        assert row.doctor_decided_at is not None
        if window_start <= row.doctor_decided_at <= case.created_at:
            matched.append(row)
    return matched


def _fallback_rows(case: Case, rows: list[CaseProcedure], window_days: int) -> list[CaseProcedure]:
    """Pool de fallback: nome normalizado + nascimento iguais na janela de dias."""
    current_name = case.patient_name.strip()
    if not current_name or case.patient_birth_date is None:
        return []
    normalized_current = normalize_name(current_name)
    window_start = case.created_at - timedelta(days=window_days)
    matched: list[CaseProcedure] = []
    for row in rows:
        other = row.case
        if not other.patient_name.strip() or other.patient_birth_date is None:
            continue
        if other.patient_birth_date != case.patient_birth_date:
            continue
        if normalize_name(other.patient_name) != normalized_current:
            continue
        assert row.doctor_decided_at is not None
        if window_start <= row.doctor_decided_at <= case.created_at:
            matched.append(row)
    return matched


def _anonymized_reason(
    reason: str,
    seed_map: Mapping[str, Mapping[str, str]] | None = None,
) -> str:
    """Motivo anonimizado pelo núcleo do change 05; vazio → 'não informado'.

    ``seed_map`` é o ``pseudonym_map`` do caso ANTERIOR (D3): valores conhecidos
    do caso (o paciente) reutilizam o token do caso mesmo citados sem rótulo
    SESAB no motivo — a semeadura fecha o residual de nome de paciente em texto
    livre do médico.
    """
    if not reason or not reason.strip():
        return "não informado"
    return anonymize_text(reason.strip(), seed_map=seed_map).anonymized_text


def _build_summary(
    row: CaseProcedure,
    *,
    pool: list[CaseProcedure],
    origin: PriorCaseOrigin,
    confirmed_after_summarization: bool = False,
) -> PriorCaseSummary:
    """Resumo do prévio mais recente do pool vencedor (data/decisão/motivo)."""
    case = row.case
    assert row.doctor_decided_at is not None
    denial_count = sum(
        1 for candidate in pool if candidate.doctor_disposition == DoctorDisposition.DENIED
    )
    return PriorCaseSummary(
        prior_case_id=str(case.case_id),
        procedure_type=row.procedure_type,
        decided_at=row.doctor_decided_at.isoformat(),
        decision=str(row.doctor_disposition),
        reason_anonymized=_anonymized_reason(row.doctor_reason, seed_map=case.pseudonym_map),
        prior_denial_count=denial_count,
        origin=origin,
        confirmed_after_summarization=confirmed_after_summarization,
    )


def _identity_signals(case: Case, declared: frozenset[str]) -> IdentitySignals:
    """Sinais de identidade do caso para o classificador de S1 (R2)."""
    return IdentitySignals(
        name=case.patient_name or "",
        record_number=case.agency_record_number or "",
        patient_cns=case.patient_cns or "",
        origin_unit=case.origin_unit or "",
        patient_age=case.patient_age,
        procedures=tuple(sorted(declared)),
    )


class _StripWhitespace(Func):
    """Normalização de identificador no banco (fix-2 P1-03).

    Espelha ``str.strip`` nos dois lados da comparação (o classificador
    ``classify_pair`` normaliza ambos os valores em Python): remove os runs
    de whitespace inicial/final — espaços, tabulação e quebras de linha —
    via ``REGEXP_REPLACE`` do PostgreSQL. Só os identificadores fortes
    (regulação/CNS) usam a expressão, sempre combinada com a janela
    fechada; sem virada de índice, migração ou campo novo.
    """

    function = "REGEXP_REPLACE"
    template = "%(function)s(%(expressions)s, '^\\s+|\\s+$', '', 'g')"
    output_field = TextField()


def _is_confirmed_after_summarization(
    case: Case,
    selected_prior_id: UUID,
    confirmed_at: datetime | None,
) -> bool:
    """Confirmação NIR ocorreu após a sumarização sem novo lookup consumido? (D7)."""
    llm2_event = (
        CaseEvent.objects.filter(case=case, event_type=CaseEventType.CASE_LLM2_COMPLETED)
        .order_by("-timestamp", "-id")
        .first()
    )
    has_summary = bool((case.summary_text or "").strip())
    if llm2_event is None and not has_summary:
        return False
    lookup_qs = CaseEvent.objects.filter(
        case=case,
        event_type=CaseEventType.PRIOR_CASE_LOOKUP,
        payload__origin="nir_confirmed",
        payload__prior_case_id=str(selected_prior_id),
    )
    if llm2_event is not None:
        has_consumed_lookup = lookup_qs.filter(timestamp__lte=llm2_event.timestamp).exists()
        return not has_consumed_lookup
    return not lookup_qs.exists()


def _valid_confirmed_links_by_prior(case: Case) -> dict[UUID, CaseReappearance]:
    """Vínculos NIR confirmados vigentes do caso, indexados por ``prior_case_id``."""
    links = list(
        CaseReappearance.objects.filter(
            incoming_case=case,
            confirmed_by__isnull=False,
            state__in=(ReappearanceState.CONFIRMED, ReappearanceState.APPLIED),
        )
        .select_related("prior_case")
        .order_by("-confirmed_at", "-id")
    )
    if not links:
        return {}
    independent_pairs = set(
        CaseReappearance.objects.filter(
            incoming_case=case,
            state=ReappearanceState.INDEPENDENT,
        ).values_list("prior_case_id", "revision_digest")
    )
    valid: dict[UUID, CaseReappearance] = {}
    for link in links:
        if link.prior_case_id in valid:
            continue
        if (link.prior_case_id, link.revision_digest) in independent_pairs:
            continue
        if revision_digest_for_pair(case, link.prior_case) != link.revision_digest:
            continue
        valid[link.prior_case_id] = link
    return valid


def _lookup_new_mode(case: Case, procedure_type: str) -> PriorCaseSummary | None:
    """Lookup do modo novo (S1/S5, R3): janela única + identidade ampliada + NIR confirmado."""
    window_days = reappearance_window_days()
    window_start = case.created_at - timedelta(days=window_days)
    window_end = case.created_at
    current_number = case.agency_record_number.strip()
    current_cns = (case.patient_cns or "").strip()

    confirmed_links = _valid_confirmed_links_by_prior(case)
    if not current_number and not current_cns and not confirmed_links:
        return None

    base_qs = (
        CaseProcedure.objects.filter(
            procedure_type=procedure_type,
            doctor_disposition__in=(
                DoctorDisposition.APPROVED.value,
                DoctorDisposition.DENIED.value,
            ),
            doctor_decided_at__isnull=False,
            doctor_decided_at__gte=window_start,
            doctor_decided_at__lte=window_end,
        )
        .exclude(case=case)
        .select_related("case")
    )

    row_ids: set[int] = set()
    if current_number or current_cns:
        identity_filter = Q()
        if current_number:
            identity_filter |= Q(_norm_number=current_number)
        if current_cns:
            identity_filter |= Q(_norm_cns=current_cns)
        row_ids.update(
            base_qs.annotate(
                _norm_number=_StripWhitespace("case__agency_record_number"),
                _norm_cns=_StripWhitespace("case__patient_cns"),
            )
            .filter(identity_filter)
            .values_list("pk", flat=True)
        )

    if confirmed_links:
        row_ids.update(
            base_qs.filter(case_id__in=list(confirmed_links)).values_list("pk", flat=True)
        )

    if not row_ids:
        return None

    rows = list(
        CaseProcedure.objects.filter(pk__in=row_ids)
        .select_related("case")
        .order_by("doctor_decided_at", "id")
    )

    candidate_ids = {row.case_id for row in rows} | {case.pk}
    declared: dict[UUID, set[str]] = {}
    for case_id, procedure in CaseProcedure.objects.filter(
        case_id__in=candidate_ids, declared_by_nir=True
    ).values_list("case_id", "procedure_type"):
        declared.setdefault(case_id, set()).add(procedure)
    declared_sets: dict[UUID, frozenset[str]] = {
        case_id: frozenset(types) for case_id, types in declared.items()
    }
    current_signals = _identity_signals(case, declared_sets.get(case.pk, frozenset()))

    valid_pool: list[tuple[CaseProcedure, PriorCaseOrigin, CaseReappearance | None]] = []
    for row in rows:
        assert row.doctor_decided_at is not None
        prior_signals = _identity_signals(row.case, declared_sets.get(row.case_id, frozenset()))
        classification = classify_pair(current_signals, prior_signals)
        if classification.verdict == "strong" and classification.origin is not None:
            valid_pool.append((row, classification.origin, None))
        elif row.case_id in confirmed_links and classification.verdict != "no_match":
            valid_pool.append((row, "nir_confirmed", confirmed_links[row.case_id]))

    if not valid_pool:
        return None
    valid_pool.sort(key=lambda item: (item[0].doctor_decided_at or window_start, item[0].pk))
    selected_row, selected_origin, selected_link = valid_pool[-1]
    pool = [row for row, _, _ in valid_pool]
    confirmed_after = False
    if selected_origin == "nir_confirmed" and selected_link is not None:
        confirmed_after = _is_confirmed_after_summarization(
            case, selected_row.case_id, selected_link.confirmed_at
        )
    return _build_summary(
        selected_row,
        pool=pool,
        origin=selected_origin,
        confirmed_after_summarization=confirmed_after,
    )


def _lookup_applied_prior_context_when_flag_off(
    case: Case, procedure_type: str
) -> PriorCaseSummary | None:
    """Preserva visibilidade de leitura de ``PRIOR_CONTEXT`` já aplicado com flag off."""
    window_days = reappearance_window_days()
    window_start = case.created_at - timedelta(days=window_days)
    applied_links = list(
        CaseReappearance.objects.filter(
            incoming_case=case,
            state=ReappearanceState.APPLIED,
            resolution_kind=ResolutionKind.PRIOR_CONTEXT,
            confirmed_by__isnull=False,
        )
        .select_related("prior_case")
        .order_by("-confirmed_at", "-id")
    )
    if not applied_links:
        return None
    link_by_prior = {link.prior_case_id: link for link in applied_links}
    rows = list(
        CaseProcedure.objects.filter(
            case_id__in=list(link_by_prior),
            procedure_type=procedure_type,
            doctor_disposition__in=(
                DoctorDisposition.APPROVED.value,
                DoctorDisposition.DENIED.value,
            ),
            doctor_decided_at__isnull=False,
            doctor_decided_at__gte=window_start,
            doctor_decided_at__lte=case.created_at,
        )
        .select_related("case")
        .order_by("doctor_decided_at", "id")
    )
    if not rows:
        return None
    selected_row = rows[-1]
    selected_link = link_by_prior[selected_row.case_id]
    confirmed_after = _is_confirmed_after_summarization(
        case, selected_row.case_id, selected_link.confirmed_at
    )
    return _build_summary(
        selected_row,
        pool=rows,
        origin="nir_confirmed",
        confirmed_after_summarization=confirmed_after,
    )


def lookup_prior_case_context(case: Case, procedure_type: str) -> PriorCaseSummary | None:
    """Busca o caso prévio mais relevante do mesmo procedimento (R4).

    Modo novo (flag ligada): janela única de 15 dias + identidade ampliada
    (nome + regulação/CNS, sem nascimento) + confirmação NIR vigente.
    Modo legado (flag desligada): preserva leitura de ``PRIOR_CONTEXT`` já
    aplicado; caso contrário, match por nº de ocorrência (7 dias) > fallback
    por nome+nascimento (15 dias).
    """
    if is_reappearance_enabled():
        return _lookup_new_mode(case, procedure_type)
    applied_summary = _lookup_applied_prior_context_when_flag_off(case, procedure_type)
    if applied_summary is not None:
        return applied_summary
    rows = _decided_rows(case, procedure_type)
    number_matches = _number_rows(case, rows, int(settings.PRIOR_CASE_WINDOW_DAYS))
    if number_matches:
        return _build_summary(number_matches[-1], pool=number_matches, origin="occurrence_number")
    fallback_matches = _fallback_rows(case, rows, int(settings.PRIOR_CASE_FALLBACK_WINDOW_DAYS))
    if not fallback_matches:
        return None
    return _build_summary(
        fallback_matches[-1], pool=fallback_matches, origin="name_birthdate_fallback"
    )


def record_prior_case_lookups(
    case: Case,
    *,
    user: User | None = None,
    role: str = "system",
) -> None:
    """Registra os lookups por procedimento declarado com origem (R5).

    Grava um evento ``PRIOR_CASE_LOOKUP`` por procedimento declarado com a
    origem do match no modo vigente (novo:
    ``occurrence_number | patient_cns | none``; legado:
    ``occurrence_number | name_birthdate_fallback | none``) e resumo enxuto —
    sem o motivo e sem identificadores (o resumo anonimizado é consumido pelo
    orquestrador 006 pelo retorno de ``lookup_prior_case_context``, não pelo
    evento). Sem procedimentos declarados → ``ValueError``.
    """
    declared_types = get_declared_procedure_types(case)
    if not declared_types:
        raise ValueError("caso sem procedimentos declarados — o lookup exige a declaração")

    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        # Gate do slice 002 (painel-lista-encerramento): o passo pode retornar
        # DEPOIS do encerramento administrativo (worker com lease expirada) —
        # não grava eventos ``PRIOR_CASE_LOOKUP`` no caso minimizado.
        if locked.status == CaseStatus.CLEANED:
            logger.info(
                "record_prior_case_lookups: caso %s encerrado administrativamente — sem persistir",
                case.pk,
            )
            return
        actor_type = ActorType.USER if user is not None else ActorType.SYSTEM
        for procedure_type in declared_types:
            summary = lookup_prior_case_context(locked, procedure_type)
            payload: dict[str, object] = {"procedure_type": procedure_type}
            if summary is None:
                payload["origin"] = "none"
                payload["matched"] = False
            else:
                payload["origin"] = summary.origin
                payload["matched"] = True
                payload["prior_case_id"] = summary.prior_case_id
                payload["decision"] = summary.decision
                payload["decided_at"] = summary.decided_at
                payload["prior_denial_count"] = summary.prior_denial_count
            CaseEvent.objects.create(
                case=locked,
                event_type=CaseEventType.PRIOR_CASE_LOOKUP,
                actor_type=actor_type,
                actor=user,
                actor_role=role or "",
                payload=payload,
            )
