"""Views do painel gerencial (change dashboard-notifications-pwa, slice 003, D3).

View ``dashboard:home`` (login-required + role_required manager/admin):
resolve o período de ``GET ?period=`` contra o conjunto aceito
(ausente/inválido → ``hoje``) e monta o contexto com as métricas do período +
o tempo médio humanizado. Toda a lógica de negócio vive em
``apps/dashboard/metrics.py`` (serviços puros) — a view apenas orquestra.

Gate por papel ativo (painel-gerencial-e-home, slice 001, R1/D1): o painel
DEIXOU de ser transversal — é exclusivo dos papéis ``manager``/``admin``, na
ROTA (papel ativo fora → 403), não só no link da navbar; a composição
``@login_required`` + ``@role_required(...)`` é a mesma das filas
doctor/scheduler. Anônimo segue redirecionado ao login. A condição do link da
navbar (``templates/base.html``) é a MESMA desta rota — UI e rota não divergem.
O conteúdo continua zero-PHI.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.accounts.decorators import role_required
from apps.cases.models import SchedulingUnit
from apps.cases.units import unit_label

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


@role_required("manager", "admin")
def home(request: HttpRequest) -> HttpResponse:
    """Painel gerencial do período selecionado (R2) — exclusivo manager/admin (R1).

    Papel ativo fora de ``manager``/``admin`` → 403 (``role_required``);
    anônimo → redirect ao login (``login_required``).

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
        # Rótulos de unidade da fonte única (change unit-labels-env, D2/D4):
        # derivados de ``settings.UNIT_LABELS`` — renomear um rótulo não exige
        # editar este arquivo nem o template do painel.
        "unit_labels": {
            "unit_1": unit_label(SchedulingUnit.UNIT_1),
            "unit_2": unit_label(SchedulingUnit.UNIT_2),
        },
        "avg_time": format_duration(compute_avg_time_to_decision(period)),
    }
    return render(request, "dashboard/home.html", context)
