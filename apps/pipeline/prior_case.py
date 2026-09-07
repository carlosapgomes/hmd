"""Prior-case: casos prévios do mesmo paciente por procedimento (slice 005,
R4–R6, design D7).

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

``record_prior_case_lookups(case, *, user, role)`` grava por procedimento
declarado o evento ``PRIOR_CASE_LOOKUP`` com origem
``occurrence_number | name_birthdate_fallback | none`` e resumo enxuto (sem
PII além do que já está no caso — o motivo anonimizado nem entra no evento;
quem consome é o resumo retornado ao orquestrador 006).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from django.conf import settings
from django.db import transaction

from apps.anonymization.services import anonymize_text
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent, CaseProcedure, DoctorDisposition
from apps.cases.procedures import get_declared_procedure_types

if TYPE_CHECKING:
    from apps.accounts.models import User

# Origens canônicas do match (design D7 / R5).
PriorCaseOrigin = Literal["occurrence_number", "name_birthdate_fallback"]


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


def normalize_name(name: str) -> str:
    """Nome normalizado para o fallback: sem acentos, maiúsculas, sem espaços.

    Implementação pura em Python (NFKD → ASCII + upper + remoção de
    whitespace) — mesmo resultado do ``unaccent``/``upper`` do Postgres para
    os nomes do linkage, sem depender de ORM (R4).
    """
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", "", ascii_text).upper()


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


def _anonymized_reason(reason: str) -> str:
    """Motivo anonimizado pelo núcleo do change 05; vazio → 'não informado'."""
    if not reason or not reason.strip():
        return "não informado"
    return anonymize_text(reason.strip()).anonymized_text


def _build_summary(
    row: CaseProcedure,
    *,
    pool: list[CaseProcedure],
    origin: PriorCaseOrigin,
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
        reason_anonymized=_anonymized_reason(row.doctor_reason),
        prior_denial_count=denial_count,
        origin=origin,
    )


def lookup_prior_case_context(case: Case, procedure_type: str) -> PriorCaseSummary | None:
    """Busca o caso prévio mais relevante do mesmo procedimento (R4).

    Prioridade: match por nº de ocorrência (7 dias) > fallback por
    nome+nascimento (15 dias); dentro do pool vencedor, decisão mais recente.
    Exclui o próprio caso e casos sem decisão. Sem candidatos → ``None``.
    """
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
    origem do match (``occurrence_number | name_birthdate_fallback | none``)
    e resumo enxuto — sem o motivo (o resumo anonimizado é consumido pelo
    orquestrador 006 pelo retorno de ``lookup_prior_case_context``, não pelo
    evento). Sem procedimentos declarados → ``ValueError``.
    """
    declared_types = get_declared_procedure_types(case)
    if not declared_types:
        raise ValueError("caso sem procedimentos declarados — o lookup exige a declaração")

    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
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
