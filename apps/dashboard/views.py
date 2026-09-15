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
métricas entra a LISTA de casos do período, com status, tipos declarados,
criação, resultado imutável e próximo passo. O encerramento administrativo tem
confirmação em página própria (``admin_close_confirm``) e POST
(``admin_close``) que delega ao serviço ``administratively_close_case``.

Slice 002 do change painel-ats-parity (R1/R2, D2/D5b) — paridade com o
dashboard do ats-web: a lista ganha a identificação do paciente (política
corrigida pelo dono: zero-PHI vale para o PERÍMETRO EXTERNO/LLM — a UI interna
de funcionários mostra o paciente) e filtros que compõem por AND
(``scope``/``status``/``procedure_type``/``q``/``date_from``/``date_to``).
Sem NENHUM filtro de lista explícito o default é hoje/hoje + escopo ``todos``
(molde ``_resolve_list_defaults`` do ats-web) — o ``period`` segue mandando
APENAS nas métricas. A ação de encerramento sai do card e passa a viver no
detalhe real do caso (``dashboard:case_detail``), que mostra a identificação
completa e os procedimentos declarados. Filtros SSR puros, busca só a partir
de 3 caracteres (nome do paciente OU nº de ocorrência OU prefixo do uid) e
paginação de 25 que preserva os filtros. A **seção de métricas** continua
zero-PHI (apenas contagens, tempos e labels).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import date
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

from .case_labels import CASE_NEXT_STEP_LABELS, CASE_RESULT_LABELS, PROCEDURE_TYPE_OPTIONS
from .event_labels import event_badge_css, event_label
from .metrics import (
    DEFAULT_PERIOD,
    VALID_PERIODS,
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

# Escopos da lista (R1/D2): ``todos`` é o default da paridade ats-web — casos
# de hoje em TODOS os estados, inclusive ``CLEANED``.
ACTIVE_SCOPE = "ativos"
DEFAULT_SCOPE = "todos"
CASE_SCOPES: tuple[tuple[str, str], ...] = (
    (ACTIVE_SCOPE, "Ativos"),
    (DEFAULT_SCOPE, "Todos (inclui encerrados)"),
)
_SCOPE_VALUES: frozenset[str] = frozenset(value for value, _ in CASE_SCOPES)
_PROCEDURE_TYPE_VALUES: frozenset[str] = frozenset(
    procedure_type for procedure_type, _ in PROCEDURE_TYPE_OPTIONS
)

# Paginação da lista (R1/D1): SSR puro, sem partial/HTMX.
CASE_LIST_PAGE_SIZE = 25

# Busca server-side (R1/D1): termos mais curtos que isto são ignorados.
MIN_SEARCH_LENGTH = 3

# Filtros preservados na paginação, nos links de período e no retorno
# pós-encerramento (R1/R2). A ORDEM define a da query string gerada.
_PRESERVED_FILTERS: tuple[str, ...] = (
    "period",
    "scope",
    "status",
    "procedure_type",
    "q",
    "date_from",
    "date_to",
)

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


def _parse_iso_date(value: str) -> str:
    """Data ISO (``YYYY-MM-DD``) válida ou ``""`` — inválida = ausente (R1/D2).

    O formulário manda ``type=date`` (sempre ISO), mas a rota é pública para o
    papel gerencial: valor torto vira ausente em vez de erro de banco.
    """
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except (AttributeError, TypeError, ValueError):
        return ""


def _valid_scope(value: str) -> str:
    """Escopo válido ou ``""`` (valor desconhecido é tratado como ausente)."""
    return value if value in _SCOPE_VALUES else ""


def _valid_status(value: str) -> str:
    """Status válido ou ``""`` (valor fora das choices é tratado como ausente)."""
    return value if value in CaseStatus.values else ""


def _valid_procedure_type(value: str) -> str:
    """Tipo de exame do catálogo ou ``""`` (fora do catálogo = ausente)."""
    return value if value in _PROCEDURE_TYPE_VALUES else ""


def _has_explicit_list_filters(params: Mapping[str, str]) -> bool:
    """Há algum filtro de LISTA com valor VÁLIDO na query? (R1/D2)

    Os filtros da lista são ``scope``, ``status``, ``procedure_type``, ``q``,
    ``date_from`` e ``date_to`` (o ``period`` é das MÉTRICAS e não conta aqui).

    Só valores válidos contam: escopo/status/tipo desconhecido cai fora e não
    derruba o default. O termo de busca conta como preenchido mesmo com menos de
    ``MIN_SEARCH_LENGTH`` (o usuário filtrou; a consulta é que ignora o termo) e
    datas inválidas não contam.
    """
    return bool(
        _valid_scope(params.get("scope", ""))
        or _valid_status(params.get("status", ""))
        or _valid_procedure_type(params.get("procedure_type", ""))
        or params.get("q", "").strip()
        or _parse_iso_date(params.get("date_from", ""))
        or _parse_iso_date(params.get("date_to", ""))
    )


def _resolve_filters(params: Mapping[str, str]) -> dict[str, str]:
    """Filtros validados da lista (R1/D2) — período das métricas + filtros da lista.

    O ``period`` é a régua das MÉTRICAS (ausente/inválido → ``hoje``) e segue
    independente da lista. Os filtros da lista são validados um a um (valor
    inválido vira ausente) e compõem por AND na consulta.

    **Default da paridade ats-web**: sem NENHUM filtro de lista explícito, a
    janela é hoje/hoje e o escopo é ``todos`` (recebidos hoje em todos os
    estados, inclusive ``CLEANED``); qualquer filtro explícito preserva o que
    veio — datas ausentes continuam ausentes. ``from > to`` normaliza por swap
    e data inválida é tratada como ausente.
    """
    period = params.get("period", DEFAULT_PERIOD)
    if period not in VALID_PERIODS:
        period = DEFAULT_PERIOD
    scope = _valid_scope(params.get("scope", ""))
    date_from = _parse_iso_date(params.get("date_from", ""))
    date_to = _parse_iso_date(params.get("date_to", ""))
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    if _has_explicit_list_filters(params):
        scope = scope or DEFAULT_SCOPE
    else:
        scope = DEFAULT_SCOPE
        date_from = date_to = timezone.localdate().isoformat()
    return {
        "period": period,
        "scope": scope,
        "status": _valid_status(params.get("status", "")),
        "procedure_type": _valid_procedure_type(params.get("procedure_type", "")),
        "q": params.get("q", ""),
        "date_from": date_from,
        "date_to": date_to,
    }


def _filter_params(filters: Mapping[str, str]) -> list[tuple[str, str]]:
    """Pares (nome, valor) dos filtros vigentes não vazios (R1/R2)."""
    return [(key, filters[key]) for key in _PRESERVED_FILTERS if filters.get(key)]


def _list_filter_params(filters: Mapping[str, str]) -> list[tuple[str, str]]:
    """Filtros da LISTA vigentes, SEM o ``period`` (R2/D2).

    Alimenta os links do seletor de período das métricas: trocar o período não
    descarta datas/tipo/status/busca da lista.
    """
    return [
        (key, filters[key]) for key in _PRESERVED_FILTERS if key != "period" and filters.get(key)
    ]


def _dashboard_url(filters: Mapping[str, str]) -> str:
    """URL do painel com os filtros vigentes (R1/R2) — paginação e retorno do POST."""
    query = urlencode(_filter_params(filters))
    base = reverse("dashboard:home")
    return f"{base}?{query}" if query else base


def _list_cases(filters: Mapping[str, str]) -> QuerySet[Case]:
    """Casos da lista (R1/D2): janela de datas, escopo, status, tipo e busca.

    Compoem por AND na ordem do formulário: janela ``date_from``/``date_to``
    sobre ``created_at`` (a régua PRÓPRIA da lista — o ``period`` manda só nas
    métricas), escopo ``ativos`` (≠ ``CLEANED``) quando escolhido, status, tipo
    DECLARADO pelo NIR (``distinct`` sobre as rows) e o termo de busca (3+
    caracteres) casando nome do paciente OU nº de ocorrência OU prefixo do uid
    (``istartswith`` — o backend Postgres emite o ``::text`` sozinho). As rows
    de procedimento vêm pré-carregadas (sem N+1 por card) e a ordem é
    ``created_at`` decrescente (D1).
    """
    cases = Case.objects.all()
    if filters["date_from"]:
        cases = cases.filter(created_at__date__gte=filters["date_from"])
    if filters["date_to"]:
        cases = cases.filter(created_at__date__lte=filters["date_to"])
    if filters["scope"] == ACTIVE_SCOPE:
        cases = cases.exclude(status=CaseStatus.CLEANED)
    if filters["status"]:
        cases = cases.filter(status=filters["status"])
    if filters["procedure_type"]:
        cases = cases.filter(
            procedures__declared_by_nir=True,
            procedures__procedure_type=filters["procedure_type"],
        ).distinct()
    term = filters["q"].strip()
    if len(term) >= MIN_SEARCH_LENGTH:
        cases = cases.filter(
            Q(patient_name__icontains=term)
            | Q(agency_record_number__icontains=term)
            | Q(case_id__istartswith=term)
        )
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
    """Card da lista (R2/D2): identificação do paciente + andamento do caso.

    A política de PHI foi corrigida pelo dono (2026-09-15): zero-PHI vale para o
    PERÍMETRO EXTERNO (LLM); a UI interna de funcionários mostra o paciente. O
    card traz nome (``—`` quando ausente), idade (``0`` é válida), unidade de
    origem, nº de ocorrência (ou uid curto), status + próximo passo, tipos
    declarados, data/hora de inserção e o resultado imutável. A ação de
    encerramento administrativo vive no DETALHE do caso (slice 002), não aqui.
    """
    declared_types = [row.procedure_type for row in case.procedures.all() if row.declared_by_nir]
    return {
        "case_id": case.case_id,
        "short_id": str(case.case_id)[:8],
        "patient_name": case.patient_name,
        "patient_age": case.patient_age,
        "origin_unit": case.origin_unit,
        "agency_record_number": case.agency_record_number,
        "status_label": case.get_status_display(),
        "procedure_labels": _procedure_labels(declared_types),
        "created_at": timezone.localtime(case.created_at),
        "result_label": _result_label(
            outcome=outcome, administratively_closed=administratively_closed
        ),
        "next_step_label": CASE_NEXT_STEP_LABELS[case.status],
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
        # Campos ocultos: só filtros com valor (vazios não viajam na query).
        "filters": {key: value for key, value in filters.items() if value},
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
        # Lista de casos (R1/R2) — cards com a identificação do paciente
        # (política corrigida: o zero-PHI é do perímetro externo).
        "cases": _case_cards(page.object_list),
        "page_obj": page,
        "filter_qs": urlencode(_filter_params(filters)),
        "list_qs": urlencode(_list_filter_params(filters)),
        "scope": filters["scope"],
        "status": filters["status"],
        "procedure_type": filters["procedure_type"],
        "procedure_type_options": PROCEDURE_TYPE_OPTIONS,
        "q": filters["q"],
        "date_from": filters["date_from"],
        "date_to": filters["date_to"],
        "scope_options": CASE_SCOPES,
        "status_options": CaseStatus.choices,
    }
    return render(request, "dashboard/home.html", context)


def _case_detail_context(case: Case, *, filters: Mapping[str, str]) -> dict[str, Any]:
    """Contexto do detalhe do caso no painel (R2/D2).

    Identificação completa (nome, idade, sexo, raça/cor, unidade de origem, nº de
    ocorrência ou uid curto, inserção e fase/status), procedimentos declarados, a
    TRILHA de eventos com rótulos legíveis (slice 003, R1/R2 — mapa
    ``event_labels``, ator e timestamp localizado) e o destino do encerramento
    administrativo (casos ≠ ``CLEANED``) com os filtros da lista preservados no
    retorno.
    """
    declared_types = [row.procedure_type for row in case.procedures.all() if row.declared_by_nir]
    return {
        "case_id": case.case_id,
        "short_id": str(case.case_id)[:8],
        "patient_name": case.patient_name,
        "patient_age": case.patient_age,
        "patient_gender": case.patient_gender,
        "patient_race": case.patient_race,
        "origin_unit": case.origin_unit,
        "agency_record_number": case.agency_record_number,
        "created_at": timezone.localtime(case.created_at),
        "status_label": case.get_status_display(),
        "next_step_label": CASE_NEXT_STEP_LABELS[case.status],
        "procedure_labels": _procedure_labels(declared_types),
        "can_close": case.status != CaseStatus.CLEANED,
        "events": [
            {
                "event_type": event.event_type,
                "label": event_label(event.event_type),
                "badge_css": event_badge_css(event.event_type, event.payload),
                "actor_display": event.actor.display_name if event.actor else "Sistema",
                "actor_role": event.actor_role,
                "timestamp": timezone.localtime(event.timestamp),
            }
            for event in case.events.select_related("actor")
        ],
        "filter_qs": urlencode(_filter_params(filters)),
        "back_url": _dashboard_url(filters),
    }


@role_required("manager", "admin")
def case_detail(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Detalhe do caso no painel (R2/D2) — exclusivo manager/admin.

    Ponto de entrada do botão [Detalhes] da lista: identificação completa do
    paciente, procedimentos declarados e a ação de encerramento administrativo
    (casos ≠ ``CLEANED``; o fluxo das rotas ``admin_close_confirm``/
    ``admin_close`` não muda). Caso inexistente → 404; papel ativo fora de
    manager/admin → 403 (``role_required``); anônimo → redirect ao login.
    """
    filters = _resolve_filters(request.GET)
    case = get_object_or_404(Case.objects.prefetch_related("procedures"), case_id=case_id)
    return render(
        request,
        "dashboard/case_detail.html",
        _case_detail_context(case, filters=filters),
    )


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
