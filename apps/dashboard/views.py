"""Views do painel gerencial (change dashboard-notifications-pwa, slice 003, D3).

View ``dashboard:home`` (login-required, **sem** role_required — o painel é
transversal e zero-PHI; D3): resolve o período de ``GET ?period=`` contra o
conjunto aceito (ausente/inválido → ``hoje``) e monta o contexto com as
métricas do período + o tempo médio humanizado. Toda a lógica de negócio vive
em ``apps/dashboard/metrics.py`` (serviços puros) — a view apenas orquestra.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.cases.models import SchedulingUnit

from .metrics import (
    DEFAULT_PERIOD,
    VALID_PERIODS,
    compute_avg_time_to_decision,
    compute_by_procedure_type,
    compute_by_unit,
    compute_summary,
    format_duration,
)

# Rótulos do seletor de período (R2/R3).
_PERIOD_LABELS: dict[str, str] = {
    "hoje": "Hoje",
    "7d": "7 dias",
    "30d": "30 dias",
    "tudo": "Tudo",
}


@login_required
def home(request: HttpRequest) -> HttpResponse:
    """Painel gerencial do período selecionado (R2).

    GET ``?period=hoje|7d|30d|tudo`` — valor ausente/inválido resolve para
    ``hoje``. O contexto expõe as métricas puras e o tempo médio humanizado
    (``—`` quando não há decisões no período); nenhum dado de paciente.
    """
    period = request.GET.get("period", DEFAULT_PERIOD)
    if period not in VALID_PERIODS:
        period = DEFAULT_PERIOD
    context = {
        "period": period,
        "period_options": [
            {"value": value, "label": _PERIOD_LABELS[value]} for value in VALID_PERIODS
        ],
        "summary": compute_summary(period),
        "by_type": compute_by_procedure_type(period),
        "by_unit": compute_by_unit(period),
        # Labels de unidade derivados do modelo (P2 review: sem rótulo
        # hardcoded — renomear em SchedulingUnit.choices reflete no painel).
        "unit_labels": {
            "unit_1": SchedulingUnit.UNIT_1.label,
            "unit_2": SchedulingUnit.UNIT_2.label,
        },
        "avg_time": format_duration(compute_avg_time_to_decision(period)),
    }
    return render(request, "dashboard/home.html", context)
