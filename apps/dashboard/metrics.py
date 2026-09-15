"""Métricas gerenciais do painel (design D3, slice 003).

Serviços puros (sem ``request``) consumidos pela view ``dashboard:home``. As
fontes são **imutáveis** (lição do dashboard do ats-web; emenda P1 review): a
população é sempre os casos com ``created_at`` no período; o desfecho por caso
(``outcome``) é o ``payload["source"]`` do **ÚLTIMO** evento
``CASE_STATUS_FINAL_REPLY_POSTED`` do caso, considerado **apenas quando o
status atual é pós-final** (``FINAL_REPLY_POSTED``/``AWAITING_NIR_ACK``/
``CLEANING``/``CLEANED``) — caso cujo status voltou ao pipeline (reaberto por
intercorrência) conta como **em andamento**. Isso elimina a dupla contagem de
dois eventos finais com sources distintos e um ``em_andamento`` negativo.

A tabela por tipo conta **rows de procedimento** (``CaseProcedure``), não
casos: o somatório das linhas difere do total do resumo (esperado; anotado no
template). O tempo até a decisão médica é por caso:
``max(doctor_decided_at das rows decididas) − case.created_at``.

O change painel-lista-encerramento (slice 003, R3/D1) acrescenta o card de
ENCERRAMENTOS ADMINISTRATIVOS (eventos ``CASE_ADMINISTRATIVELY_CLOSED`` com
``timestamp`` na janela) e mantém ``em_andamento`` derivado da POPULAÇÃO por
conjuntos de ids — sem dupla subtração de quem já tem desfecho. A população do
período é exposta em ``cases_in_period`` e o desfecho por caso em
``outcome_by_case``: a MESMA régua alimenta a lista de casos do painel (view
``dashboard:home``), sem grafia paralela.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from django.db.models import (
    Avg,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    Max,
    Q,
    QuerySet,
)
from django.utils import timezone

from apps.cases.events import CaseEventType
from apps.cases.models import (
    Case,
    CaseEvent,
    CaseProcedure,
    CaseStatus,
    DoctorDisposition,
    SchedulingUnit,
)
from apps.cases.procedure_catalog import PROCEDURE_PROFILES

# Períodos aceitos pela view e resolvidos por ``_period_bounds`` (R1/R2).
VALID_PERIODS: tuple[str, ...] = ("hoje", "7d", "30d", "tudo")
DEFAULT_PERIOD = "hoje"

# Status pós-final: o caso recebeu a resposta final e não voltou ao pipeline.
_POST_FINAL_STATUSES: tuple[CaseStatus, ...] = (
    CaseStatus.FINAL_REPLY_POSTED,
    CaseStatus.AWAITING_NIR_ACK,
    CaseStatus.CLEANING,
    CaseStatus.CLEANED,
)

# Fontes do evento final (``payload["source"]``) por desfecho (R1).
_SCHEDULED_SOURCES: tuple[CaseStatus, ...] = (CaseStatus.SCHEDULING_CONFIRMED,)
_NEGATED_SOURCES: tuple[CaseStatus, ...] = (
    CaseStatus.DOCTOR_DENIED,
    CaseStatus.SCHEDULING_DENIED,
)

# Rows com decisão médica registrada — base do tempo médio até a decisão.
_DECIDED_DISPOSITIONS: tuple[DoctorDisposition, ...] = (
    DoctorDisposition.APPROVED,
    DoctorDisposition.DENIED,
)


def local_day_bounds(day: date | None = None) -> tuple[datetime, datetime]:
    """Bounds ``[00:00, +1d)`` do dia local (``TIME_ZONE`` do repo).

    ``day`` ausente usa a data local atual; o retorno é aware no fuso local
    (``timezone.localtime``). Espelha o helper homônimo do ats-web.
    """
    local_now = timezone.localtime(timezone.now())
    target = local_now.date() if day is None else day
    start = local_now.replace(
        year=target.year,
        month=target.month,
        day=target.day,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return start, start + timedelta(days=1)


def _period_bounds(period: str) -> tuple[datetime | None, datetime | None]:
    """Resolve ``(start, end)`` do período (R1).

    ``hoje`` = dia local ``[00:00, +1d)``; ``7d``/``30d`` = janela rolling de
    7/30 dias até agora; ``tudo`` = sem filtro temporal ``(None, None)``.
    Período ausente/inválido cai em ``hoje`` (default seguro).
    """
    if period == "tudo":
        return None, None
    if period == "7d":
        now = timezone.now()
        return now - timedelta(days=7), now
    if period == "30d":
        now = timezone.now()
        return now - timedelta(days=30), now
    return local_day_bounds()


def _window(start: datetime | None, end: datetime | None) -> QuerySet[Case]:
    """População do período: casos com ``created_at`` em ``[start, end)``."""
    cases = Case.objects.all()
    if start is None or end is None:
        return cases
    return cases.filter(created_at__gte=start, created_at__lt=end)


def cases_in_period(period: str) -> QuerySet[Case]:
    """População do período (``created_at`` na janela) — régua única das métricas
    e da lista de casos do painel (R1/D1).
    """
    start, end = _period_bounds(period)
    return _window(start, end)


def _post_final_population(population: QuerySet[Case]) -> QuerySet[Case]:
    """Casos da população cujo status atual é pós-final (desfecho atribuível)."""
    return population.filter(status__in=_POST_FINAL_STATUSES)


def outcome_by_case(population: QuerySet[Case]) -> dict[uuid.UUID, str]:
    """``payload["source"]`` do ÚLTIMO evento final por caso (só status pós-final).

    Fetch dirigido (uma consulta) com ``DISTINCT ON (case_id)`` ordenado por id
    desc: classifica o caso pelo desfecho do evento final MAIS RECENTE e só o
    inclui se o status atual é pós-final — base da regra imutável do D3 (caso
    reaberto tem o evento final anterior ignorado). Casos sem evento final com
    source textual ficam de fora (contam como em andamento). Consumido pelo
    resumo e pelos cards da lista (D1): uma única grafia do desfecho imutável.
    """
    rows = (
        CaseEvent.objects.filter(
            case__in=_post_final_population(population),
            event_type=CaseEventType.CASE_STATUS_FINAL_REPLY_POSTED,
        )
        .order_by("case_id", "-id")
        .distinct("case_id")
        .values_list("case_id", "payload")
    )
    outcome: dict[uuid.UUID, str] = {}
    for case_id, payload in rows:
        source = payload.get("source") if isinstance(payload, dict) else None
        if isinstance(source, str):
            outcome[case_id] = source
    return outcome


def _outcome_ids(outcome: dict[uuid.UUID, str], sources: tuple[CaseStatus, ...]) -> set[uuid.UUID]:
    """Ids dos casos cujo desfecho (último evento final) tem ``source`` no conjunto."""
    wanted = {str(source) for source in sources}
    return {case_id for case_id, source in outcome.items() if source in wanted}


def _administratively_closed_case_ids(
    start: datetime | None, end: datetime | None
) -> set[uuid.UUID]:
    """Ids dos casos com evento ``CASE_ADMINISTRATIVELY_CLOSED`` na janela (R3).

    O card conta EVENTOS do período (semântica do cenário "encerramentos
    administrativos contados no período"): o caso aparece na métrica mesmo
    quando foi criado fora da janela.
    """
    events = CaseEvent.objects.filter(event_type=CaseEventType.CASE_ADMINISTRATIVELY_CLOSED)
    if start is not None and end is not None:
        events = events.filter(timestamp__gte=start, timestamp__lt=end)
    return set(events.values_list("case_id", flat=True))


def compute_summary(period: str) -> dict[str, int]:
    """Resumo do período (R1/R3): total, agendados, negados, em andamento,
    encerrados e encerramentos administrativos.

    ``em_andamento`` é derivado da POPULAÇÃO (``created_at`` na janela) por
    conjuntos de ids: ``|population − agendados − negados − (admin_fechados ∩
    population sem desfecho)|``. Admin-fechado FORA da população não subtrai
    (total 0 com card 1 sem em andamento negativo) e admin-fechado COM desfecho
    já está em ``agendados``/``negados`` (sem dupla subtração). ``encerrados``
    segue contando os ``CLEANED`` da população — INCLUI os encerrados
    administrativamente; ``administratively_closed`` conta os eventos do
    período. Os conjuntos subtraídos são subconjuntos da população, então o
    saldo de ``em_andamento`` nunca é negativo por construção.
    """
    start, end = _period_bounds(period)
    population = _window(start, end)
    population_ids = set(population.values_list("case_id", flat=True))
    outcome = outcome_by_case(population)
    scheduled_ids = _outcome_ids(outcome, _SCHEDULED_SOURCES)
    negated_ids = _outcome_ids(outcome, _NEGATED_SOURCES)
    admin_closed_ids = _administratively_closed_case_ids(start, end)
    admin_without_outcome = (admin_closed_ids & population_ids) - scheduled_ids - negated_ids
    return {
        "total": len(population_ids),
        "agendados": len(scheduled_ids),
        "negados": len(negated_ids),
        "em_andamento": len(population_ids - scheduled_ids - negated_ids - admin_without_outcome),
        "encerrados": population.filter(status=CaseStatus.CLEANED).count(),
        "administratively_closed": len(admin_closed_ids),
    }


def compute_by_procedure_type(period: str) -> list[dict[str, Any]]:
    """Linhas por tipo de procedimento na ORDEM CANÔNICA do catálogo (R1).

    Cada linha traz ``procedure_type``/``label`` e as contagens de rows
    (``total``/``approved``/``denied``/``pending`` por ``doctor_disposition``).
    A tabela conta rows de procedimento — o somatório das linhas difere do
    total do resumo (esperado; notado no template).
    """
    start, end = _period_bounds(period)
    population = _window(start, end)
    aggregated = (
        CaseProcedure.objects.filter(case__in=population)
        .values("procedure_type")
        .annotate(
            total=Count("pk"),
            approved=Count("pk", filter=Q(doctor_disposition=DoctorDisposition.APPROVED)),
            denied=Count("pk", filter=Q(doctor_disposition=DoctorDisposition.DENIED)),
            pending=Count("pk", filter=Q(doctor_disposition=DoctorDisposition.PENDING)),
        )
    )
    counts_by_type: dict[str, Any] = {row["procedure_type"]: row for row in aggregated}
    rows: list[dict[str, Any]] = []
    for profile in PROCEDURE_PROFILES:
        counts = counts_by_type.get(profile.procedure_type)
        rows.append(
            {
                "procedure_type": profile.procedure_type,
                "label": profile.label,
                "total": counts["total"] if counts else 0,
                "approved": counts["approved"] if counts else 0,
                "denied": counts["denied"] if counts else 0,
                "pending": counts["pending"] if counts else 0,
            }
        )
    return rows


def compute_by_unit(period: str) -> dict[str, int]:
    """Casos com outcome agendado por ``scheduled_unit`` (R1): unidade 1 e unidade 2.

    Casos reabertos ao pipeline não entram (status fora do pós-final), mesmo
    que tenham tido ``scheduled_unit`` no passado.
    """
    start, end = _period_bounds(period)
    population = _window(start, end)
    outcome = outcome_by_case(population)
    scheduled = str(CaseStatus.SCHEDULING_CONFIRMED)
    scheduled_ids = [case_id for case_id, source in outcome.items() if source == scheduled]
    return {
        "unit_1": population.filter(
            pk__in=scheduled_ids, scheduled_unit=SchedulingUnit.UNIT_1
        ).count(),
        "unit_2": population.filter(
            pk__in=scheduled_ids, scheduled_unit=SchedulingUnit.UNIT_2
        ).count(),
    }


def compute_avg_time_to_decision(period: str) -> timedelta | None:
    """Média ``max(doctor_decided_at por caso) − created_at`` dos casos decididos (R1).

    Considera os casos da população com ≥ 1 row decidida (``doctor_disposition``
    ≠ pending) cujo ``doctor_decided_at`` (o maior da row) está no período.
    Sem casos → ``None``.
    """
    start, end = _period_bounds(period)
    population = _window(start, end)
    cases = population.annotate(
        decision_at=Max(
            "procedures__doctor_decided_at",
            filter=Q(procedures__doctor_disposition__in=_DECIDED_DISPOSITIONS),
        )
    ).filter(decision_at__isnull=False)
    if start is not None and end is not None:
        cases = cases.filter(decision_at__gte=start, decision_at__lt=end)
    elapsed = ExpressionWrapper(F("decision_at") - F("created_at"), output_field=DurationField())
    average: timedelta | None = cases.aggregate(avg=Avg(elapsed))["avg"]
    return average


def format_duration(value: timedelta | None) -> str:
    """Humaniza a duração para o template (R2): ``"3h 42m"``; ausente → ``"—"``."""
    if value is None:
        return "—"
    total_minutes = int(value.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"
