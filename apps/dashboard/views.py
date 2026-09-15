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

Slice 003 do change painel-lista-encerramento (R1/R2, D1/D3): abaixo das
métricas entra a LISTA de casos do período — cards identificados pelo nº de
ocorrência (dado do CASO) ou uid curto, com status, tipos declarados, criação,
resultado imutável e próximo passo, e a ação de encerramento administrativo
apenas fora de ``CLEANED``. Filtros SSR puros (``scope``/``status``/``q``),
busca só a partir de 3 caracteres (``icontains`` no nº de ocorrência ou
``istartswith`` no uid) e paginação de 25 que preserva os filtros. O
encerramento tem confirmação em página própria (``admin_close_confirm``) e
POST (``admin_close``) que delega ao serviço ``administratively_close_case``.
A **seção de métricas** continua zero-PHI; o invariante próprio da lista é a
ausência de NOME e de DATA DE NASCIMENTO do paciente (o nº de ocorrência
identifica o caso).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import User
from apps.cases.closure import ADMINISTRATIVE_CLOSURE_REASONS, administratively_close_case
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseEvent, CaseStatus, SchedulingUnit
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.cases.units import unit_label

from .case_labels import CASE_NEXT_STEP_LABELS, CASE_RESULT_LABELS
from .metrics import (
    DEFAULT_PERIOD,
    VALID_PERIODS,
    cases_in_period,
    compute_avg_time_to_decision,
    compute_by_procedure_type,
    compute_by_unit,
    compute_summary,
    format_duration,
    outcome_by_case,
)

# Rótulos do seletor de período (R2/R3).
_PERIOD_LABELS: dict[str, str] = {
    "hoje": "Hoje",
    "7d": "7 dias",
    "30d": "30 dias",
    "tudo": "Tudo",
}

# Escopos da lista (R1/D1): ``ativos`` é o default (= tudo exceto CLEANED).
CASE_SCOPES: tuple[tuple[str, str], ...] = (
    ("ativos", "Ativos"),
    ("todos", "Todos (inclui encerrados)"),
)
DEFAULT_SCOPE = "ativos"

# Paginação da lista (R1/D1): SSR puro, sem partial/HTMX.
CASE_LIST_PAGE_SIZE = 25

# Busca server-side (R1/D1): termos mais curtos que isto são ignorados.
MIN_SEARCH_LENGTH = 3

# Filtros preservados na paginação e no retorno pós-encerramento (R1/R2).
_PRESERVED_FILTERS: tuple[str, ...] = ("period", "scope", "status", "q")

# Resultado do card quando não há desfecho atribuível (mesmo travessão das métricas).
RESULT_NONE_LABEL = "—"
# Resultado do caso encerrado administrativamente (R1/D1): o motivo fica na
# trilha e na notificação do criador, não no card do painel.
ADMINISTRATIVELY_CLOSED_RESULT = "Encerrado administrativamente"

# Flash do POST de encerramento (R2) — texto fixo, sem motivo nem dado de paciente.
CLOSE_SUCCESS_MESSAGE = "Caso encerrado administrativamente e removido das filas operacionais."


def _require_user(request: HttpRequest) -> User:
    """Usuário autenticado de uma view já protegida por ``@role_required``."""
    user = request.user
    if not isinstance(user, User):
        raise PermissionDenied
    return user


def _resolve_filters(params: Mapping[str, str]) -> dict[str, str]:
    """Filtros validados da lista (R1): período/escopo/status com default seguro.

    Valor ausente ou fora do conjunto aceito nunca quebra nem vaza: cai no
    default (``hoje``/``ativos``/sem status). O termo de busca é preservado como
    veio — a decisão de filtrar (``MIN_SEARCH_LENGTH``) é da consulta.
    """
    period = params.get("period", DEFAULT_PERIOD)
    if period not in VALID_PERIODS:
        period = DEFAULT_PERIOD
    scope = params.get("scope", DEFAULT_SCOPE)
    if scope not in {value for value, _ in CASE_SCOPES}:
        scope = DEFAULT_SCOPE
    status = params.get("status", "")
    if status not in CaseStatus.values:
        status = ""
    return {"period": period, "scope": scope, "status": status, "q": params.get("q", "")}


def _filter_params(filters: Mapping[str, str]) -> list[tuple[str, str]]:
    """Pares (nome, valor) dos filtros vigentes não vazios (R1/R2)."""
    return [(key, filters[key]) for key in _PRESERVED_FILTERS if filters.get(key)]


def _dashboard_url(filters: Mapping[str, str]) -> str:
    """URL do painel com os filtros vigentes (R1/R2) — paginação e retorno do POST."""
    query = urlencode(_filter_params(filters))
    base = reverse("dashboard:home")
    return f"{base}?{query}" if query else base


def _list_cases(filters: Mapping[str, str]) -> QuerySet[Case]:
    """Casos da lista (R1): janela do período, escopo, status e busca (3+ chars).

    A busca casa o nº de ocorrência (``icontains``) OU o prefixo do uid
    (``istartswith`` — o backend Postgres emite o ``::text`` sozinho); as rows
    de procedimento vêm pré-carregadas (sem N+1 por card) e a ordem é
    ``created_at`` decrescente (D1).
    """
    cases = cases_in_period(filters["period"])
    if filters["scope"] == DEFAULT_SCOPE:
        cases = cases.exclude(status=CaseStatus.CLEANED)
    if filters["status"]:
        cases = cases.filter(status=filters["status"])
    term = filters["q"].strip()
    if len(term) >= MIN_SEARCH_LENGTH:
        cases = cases.filter(Q(agency_record_number__icontains=term) | Q(case_id__istartswith=term))
    return cases.order_by("-created_at").prefetch_related("procedures")


def _procedure_labels(declared_types: list[str]) -> list[str]:
    """Labels legíveis dos tipos declarados, na ordem canônica do catálogo."""
    declared = set(declared_types)
    return [profile.label for profile in PROCEDURE_PROFILES if profile.procedure_type in declared]


def _result_label(*, outcome: str, administratively_closed: bool) -> str:
    """Resultado imutável do card (D1): encerramento administrativo > desfecho do
    último evento final (fonte única em ``metrics.outcome_by_case``) > ``—``.

    Caso reaberto ao pipeline não tem desfecho atribuível (``outcome`` vazio) e
    aparece sem resultado — o status corrente e o próximo passo mandam no card.
    """
    if administratively_closed:
        return ADMINISTRATIVELY_CLOSED_RESULT
    return CASE_RESULT_LABELS.get(outcome, RESULT_NONE_LABEL)


def _case_card(case: Case, *, outcome: str, administratively_closed: bool) -> dict[str, Any]:
    """Card D1 da lista: identifica o CASO (nº de ocorrência ou uid curto) e seu
    andamento — status, tipos declarados, criação, resultado e próximo passo.

    NUNCA carrega nome nem data de nascimento do paciente (invariante próprio da
    lista): os campos vêm só do caso e das rows de procedimento.
    """
    declared_types = [row.procedure_type for row in case.procedures.all() if row.declared_by_nir]
    return {
        "case_id": case.case_id,
        "short_id": str(case.case_id)[:8],
        "agency_record_number": case.agency_record_number,
        "status_label": case.get_status_display(),
        "procedure_labels": _procedure_labels(declared_types),
        "created_at": timezone.localtime(case.created_at),
        "result_label": _result_label(
            outcome=outcome, administratively_closed=administratively_closed
        ),
        "next_step_label": CASE_NEXT_STEP_LABELS[case.status],
        "can_close": case.status != CaseStatus.CLEANED,
    }


def _case_cards(cases: Iterable[Case]) -> list[dict[str, Any]]:
    """Cards da página (D1) com duas consultas dirigidas — sem N+1 por card.

    O desfecho vem do fetch único de ``metrics.outcome_by_case`` e o
    encerramento administrativo de um ``IN`` sobre os eventos da página.
    """
    case_list = list(cases)
    case_ids = [case.case_id for case in case_list]
    outcomes = outcome_by_case(Case.objects.filter(pk__in=case_ids))
    administratively_closed = set(
        CaseEvent.objects.filter(
            case_id__in=case_ids,
            event_type=CaseEventType.CASE_ADMINISTRATIVELY_CLOSED,
        ).values_list("case_id", flat=True)
    )
    return [
        _case_card(
            case,
            outcome=outcomes.get(case.case_id, ""),
            administratively_closed=case.case_id in administratively_closed,
        )
        for case in case_list
    ]


def _close_context(
    case: Case,
    *,
    filters: Mapping[str, str],
    reason_code: str = "",
    reason_text: str = "",
) -> dict[str, Any]:
    """Contexto do formulário de encerramento (GET e re-render do POST, R2/D3)."""
    return {
        "case": case,
        "agency_record_number": case.agency_record_number,
        "short_id": str(case.case_id)[:8],
        "status_label": case.get_status_display(),
        "reason_choices": list(ADMINISTRATIVE_CLOSURE_REASONS.items()),
        "submitted_reason_code": reason_code,
        "submitted_reason_text": reason_text,
        "filters": filters,
        "back_url": _dashboard_url(filters),
    }


@role_required("manager", "admin")
def home(request: HttpRequest) -> HttpResponse:
    """Painel gerencial do período selecionado (R2) — exclusivo manager/admin (R1).

    Papel ativo fora de ``manager``/``admin`` → 403 (``role_required``);
    anônimo → redirect ao login (``login_required``).

    GET ``?period=hoje|7d|30d|tudo`` — valor ausente/inválido resolve para
    ``hoje``. O contexto expõe as métricas puras e o tempo médio humanizado
    (``—`` quando não há decisões no período) e a lista de casos do mesmo
    período (R1) com os filtros ``scope``/``status``/``q`` e 25 cards por página.
    """
    filters = _resolve_filters(request.GET)
    period = filters["period"]
    cases = _list_cases(filters)
    page = Paginator(cases, CASE_LIST_PAGE_SIZE).get_page(request.GET.get("page", "1"))
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
        # Lista de casos do período (R1) — cards sem dados de paciente.
        "cases": _case_cards(page.object_list),
        "page_obj": page,
        "filter_qs": urlencode(_filter_params(filters)),
        "scope": filters["scope"],
        "status": filters["status"],
        "q": filters["q"],
        "scope_options": CASE_SCOPES,
        "status_options": CaseStatus.choices,
    }
    return render(request, "dashboard/home.html", context)


@role_required("manager", "admin")
def admin_close_confirm(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Confirmação do encerramento administrativo (R2/D3) — página própria.

    Identifica o CASO, oferece o select do catálogo fixo de motivos e a textarea
    OBRIGATÓRIA, e leva os filtros vigentes como campos ocultos (retorno
    preservado após o POST). Caso inexistente → 404; papel ativo fora de
    manager/admin → 403 (``role_required``).
    """
    filters = _resolve_filters(request.GET)
    case = get_object_or_404(Case, case_id=case_id)
    return render(
        request,
        "dashboard/admin_close_confirm.html",
        _close_context(case, filters=filters),
    )


@role_required("manager", "admin")
@require_POST
def admin_close(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """POST do encerramento administrativo (R2/D3).

    Delega ao serviço ``administratively_close_case`` (validações fail-closed,
    recusa por lease de worker viva, minimização e transição na FSM). Sucesso →
    ``messages.success`` + redirect ao painel PRESERVANDO os filtros; ``ValueError``
    (motivo fora do catálogo, texto vazio, caso já encerrado, lease viva) →
    ``messages.error`` + re-render da confirmação com o caso intacto — nunca 500.
    Caso inexistente → 404; papel ativo fora de manager/admin → 403.
    """
    user = _require_user(request)
    case = get_object_or_404(Case, case_id=case_id)
    filters = _resolve_filters(request.POST)
    reason_code = request.POST.get("reason_code", "")
    reason_text = request.POST.get("reason_text", "")
    try:
        administratively_close_case(
            case=case,
            user=user,
            active_role=request.session.get("active_role", ""),
            reason_code=reason_code,
            reason_text=reason_text,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return render(
            request,
            "dashboard/admin_close_confirm.html",
            _close_context(case, filters=filters, reason_code=reason_code, reason_text=reason_text),
        )
    messages.success(request, CLOSE_SUCCESS_MESSAGE)
    return redirect(_dashboard_url(filters))
