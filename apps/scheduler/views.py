"""Views da fila, do detalhe e das ações do agendador (scheduler-multi-unit,
slice 003, R1–R7).

R1/R2: ``scheduler:queue`` em ``/scheduler/`` sob ``role_required("scheduler",
"admin")`` — abas ``aguardando`` (default = ``SCHEDULER_REQUESTED`` **ou**
``AWAITING_SCHEDULING`` — pedidos novos e casos reabertos por intercorrência)
e ``processados`` (= ``SCHEDULING_CONFIRMED|SCHEDULING_DENIED|
FINAL_REPLY_POSTED``), ordenada por tempo de tela (``days_on_screen`` desc,
desempate FIFO por ``created_at``), paginada (``Paginator``) e
**sem filtro por unidade** (fila completa, plano §4). Cards com identificação,
tipos declarados e unidade quando definida.

R3/R4: ``scheduler:case_detail`` renderiza o contexto do presenter puro
(identificação real + one-liner re-identificado + decisões médicas + dados de
agendamento + thread — sem artefatos clínicos) acrescido das ações por estado:
em ``SCHEDULER_REQUESTED``/``AWAITING_SCHEDULING`` os forms de confirmar
(unidade 1|2 + data/hora futura + local) e negar (motivo); em
``FINAL_REPLY_POSTED`` com unidade 1 o form de desmarcar (intercorrência);
unidade 2 exibe o banner "intercorrência desabilitada". Os POSTs
(``case_confirm``/``case_deny``/``case_reopen``) delegam aos serviços
transacionais do slice 001/002; erros de serviço/estado/concorrência
(``ValueError`` nomeado e ``TransitionNotAllowed``) viram mensagem + redirect
ao detalhe — nunca 500, nenhuma escrita parcial (R5/D5).

R7: ``scheduler:case_pdf`` serve os PDFs do relatório original por position
(padrão do doctor do change 07; decisão do supervisor — o HMD é multi-PDF via
``CaseDocument`` e não tem ``Case.pdf_file``): SOMENTE quando
``scheduled_by == request.user`` E status ∈ {``SCHEDULING_CONFIRMED``,
``SCHEDULING_DENIED``, ``FINAL_REPLY_POSTED``, ``AWAITING_NIR_ACK``} (404
fail-closed — inclusive caso reaberto por intercorrência, cujo
``scheduled_by`` foi limpo); links de PDF no detalhe apenas nessa condição.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import cast
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_fsm import TransitionNotAllowed

from apps.accounts.decorators import role_required
from apps.accounts.models import User
from apps.cases.models import Case, CaseDocument, CaseStatus
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.cases.units import unit_label

from .forms import (
    DENY_REASON_REQUIRED_MESSAGE,
    REOPEN_REASON_REQUIRED_MESSAGE,
    SchedulerConfirmForm,
    SchedulerDenyForm,
    SchedulerReopenForm,
)
from .presenters import build_scheduler_case_detail_context
from .services import (
    confirm_case_scheduling,
    deny_case_scheduling,
    reopen_scheduling_after_incident,
)

logger = logging.getLogger(__name__)

# Content-type dos documentos do relatório (mesmo padrão do doctor/intake).
PDF_CONTENT_TYPE = "application/pdf"

# Tamanho de página da fila do agendador (padrão do change 07).
QUEUE_PAGE_SIZE = 20

# Estados por aba (R2/D4): ``aguardando`` (default) = SCHEDULER_REQUESTED ou
# AWAITING_SCHEDULING; ``processados`` = SCHEDULING_CONFIRMED|
# SCHEDULING_DENIED|FINAL_REPLY_POSTED (os dois primeiros transitórios do
# encadeamento, incluídos inofensivos — mesmo critério da fila médica).
TAB_STATUSES: dict[str, tuple[CaseStatus, ...]] = {
    "aguardando": (CaseStatus.SCHEDULER_REQUESTED, CaseStatus.AWAITING_SCHEDULING),
    "processados": (
        CaseStatus.SCHEDULING_CONFIRMED,
        CaseStatus.SCHEDULING_DENIED,
        CaseStatus.FINAL_REPLY_POSTED,
    ),
}

# Estados pós-decisão do agendador cujo PDF o próprio agendador pode abrir (R7,
# semântica do ``_get_scheduler_processed_case_or_404`` do ats-web).
PDF_ALLOWED_STATUSES: frozenset[CaseStatus] = frozenset(
    {
        CaseStatus.SCHEDULING_CONFIRMED,
        CaseStatus.SCHEDULING_DENIED,
        CaseStatus.FINAL_REPLY_POSTED,
        CaseStatus.AWAITING_NIR_ACK,
    }
)

# Mensagens de sucesso/flash das ações (R4) e de corrida concorrente (R5/D5).
CONFIRM_SUCCESS_MESSAGE = "Agendamento confirmado."
DENY_SUCCESS_MESSAGE = "Agendamento negado."
REOPEN_SUCCESS_MESSAGE = "Agendamento desmarcado — o caso retornou à fila de agendamento."
RACE_MESSAGE = "Estado do caso mudou — ação não registrada. Atualize a página e tente novamente."

# Perfil do catálogo por tipo (ordem/rotulo/subtipo dos cards da fila).
_PROFILE_BY_TYPE = {profile.procedure_type: profile for profile in PROCEDURE_PROFILES}
_CATALOG_INDEX: dict[str, int] = {
    profile.procedure_type: index for index, profile in enumerate(PROCEDURE_PROFILES)
}


def _require_user(request: HttpRequest) -> User:
    """Usuário autenticado de uma view já protegida por ``@role_required``."""
    user = request.user
    if not isinstance(user, User):
        raise PermissionDenied
    return user


def _case_detail_url(case: Case) -> str:
    """URL canônica do detalhe do caso (destino dos redirects/flash)."""
    return reverse("scheduler:case_detail", args=[case.case_id])


def _form_error_text(form: SchedulerConfirmForm) -> str:
    """Primeiro erro do form de confirmação como texto da mensagem.

    Os POSTs de ação não re-renderizam o form (o detalhe é a página canônica):
    a validação falha vira mensagem de erro + redirect ao detalhe — R5 (nunca
    500; nenhuma escrita).
    """
    errors: list[str] = []
    errors.extend(str(error) for error in form.non_field_errors())
    for field_errors in form.errors.values():
        errors.extend(str(error) for error in field_errors)
    return " ".join(errors) if errors else "Formulário inválido."


def _queue_item(case: Case) -> dict[str, object]:
    """Item de card da fila: identificação + tipos declarados + unidade.

    Os tipos saem das rows já pré-carregadas (``prefetch_related``),
    declaradas pelo NIR, na ordem canônica do catálogo; a unidade de destino
    aparece apenas quando já definida no agendamento (R2).
    """
    rows = [row for row in case.procedures.all() if row.declared_by_nir]
    rows.sort(key=lambda row: _CATALOG_INDEX[row.procedure_type])
    scheduled_unit = case.scheduled_unit
    unit_label_text = unit_label(scheduled_unit) if scheduled_unit else ""
    return {
        "case_id": str(case.case_id),
        "status_label": case.get_status_display(),
        "patient_name": case.patient_name or "—",
        "patient_age": case.patient_age,
        "days_on_screen": case.days_on_screen,
        "agency_record_number": case.agency_record_number or "—",
        "created_at": case.created_at,
        "unit_label": unit_label_text,
        "procedures": [
            {
                "label": _PROFILE_BY_TYPE[row.procedure_type].label,
                "subtype": _PROFILE_BY_TYPE[row.procedure_type].doctor_subtipo,
            }
            for row in rows
        ],
    }


# ── R1/R2: fila ───────────────────────────────────────────────────────────


@role_required("scheduler", "admin")
def queue(request: HttpRequest) -> HttpResponse:
    """Fila do agendador por estado (abas), ordenada por tempo de tela (R2/D4).

    ``days_on_screen`` desc nulls-last com desempate FIFO por ``created_at`` e
    ``case_id`` — mesma expressão da fila médica. Sem filtro por unidade (fila
    completa, plano §4). Aba desconhecida cai no default ``aguardando``.
    """
    _require_user(request)
    tab = request.GET.get("tab", "aguardando")
    if tab not in TAB_STATUSES:
        tab = "aguardando"

    cases = (
        Case.objects.filter(status__in=TAB_STATUSES[tab])
        .order_by(F("days_on_screen").desc(nulls_last=True), "created_at", "case_id")
        .prefetch_related("procedures")
    )
    page = Paginator(cases, QUEUE_PAGE_SIZE).get_page(request.GET.get("page", "1"))
    items = [_queue_item(case) for case in page.object_list]

    context: dict[str, object] = {
        "tab": tab,
        "page_obj": page,
        "items": items,
        "filter_qs": urlencode({"tab": tab}),
    }
    return render(request, "scheduler/queue.html", context)


# ── R3/R4: detalhe ────────────────────────────────────────────────────────


@role_required("scheduler", "admin")
def case_detail(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Detalhe limitado do caso para o agendador (R3/D3).

    Guard de papel ativo (scheduler/admin); caso inexistente → 404. Contexto
    do presenter puro (one-liner re-identificado exclusivamente na
    renderização) acrescido dos forms de ação por estado (confirmar/negar em
    SCHEDULER_REQUESTED/AWAITING_SCHEDULING; desmarcar em FINAL_REPLY_POSTED
    com unidade 1) e dos documentos PDF apenas quando a condição R7 vale
    (``scheduled_by == request.user`` E status pós-decisão).
    """
    user = _require_user(request)
    case = get_object_or_404(Case, case_id=case_id)
    context = build_scheduler_case_detail_context(case)
    if bool(context["can_decide_scheduling"]):
        context["confirm_form"] = SchedulerConfirmForm()
        context["deny_form"] = SchedulerDenyForm()
    if bool(context["can_reopen"]):
        context["reopen_form"] = SchedulerReopenForm()

    can_view_pdf = case.scheduled_by_id == user.pk and case.status in PDF_ALLOWED_STATUSES
    context["can_view_pdf"] = can_view_pdf
    if can_view_pdf:
        context["pdf_documents"] = list(case.documents.all())
    return render(request, "scheduler/case_detail.html", context)


# ── R4/R5: POSTs de ação (confirmar/negar/desmarcar) ──────────────────────


def _schedule_confirm_datetime(form: SchedulerConfirmForm) -> datetime:
    """Datetime aware da confirmação (form válido ⇒ sempre presente)."""
    aware = form.aware_scheduled_datetime()
    if aware is None:  # pragma: no cover — inalcançável com form válido
        raise AssertionError("datetime ausente em form de confirmação válido")
    return aware


@role_required("scheduler", "admin")
@require_POST
def case_confirm(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """POST de confirmação do agendamento (unidade 1|2 + data/hora + local).

    Form válido → ``confirm_case_scheduling`` (atomic + transições FSM até
    ``FINAL_REPLY_POSTED`` + resposta final ao NIR); sucesso → flash + redirect
    ao detalhe. Form inválido ou erro de serviço/estado/concorrência
    (``ValueError``/``TransitionNotAllowed``) → mensagem + redirect — nunca
    500, sem escrita parcial (R5/D5).
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(Case, case_id=case_id)
    detail_url = _case_detail_url(case)
    form = SchedulerConfirmForm(request.POST)
    if not form.is_valid():
        logger.warning("scheduler_confirm_invalid user=%s case=%s", user.pk, case.case_id)
        messages.error(request, _form_error_text(form))
        return redirect(detail_url)
    try:
        confirm_case_scheduling(
            case,
            unit=form.cleaned_data["unit"],
            scheduled_datetime=_schedule_confirm_datetime(form),
            scheduled_location=form.cleaned_data["scheduled_location"],
            user=user,
            role=active_role,
        )
    except ValueError as exc:
        logger.warning(
            "scheduler_confirm_rejected user=%s case=%s erro=%s", user.pk, case.case_id, exc
        )
        messages.error(request, str(exc))
        return redirect(detail_url)
    except TransitionNotAllowed:
        logger.warning(
            "scheduler_confirm_race user=%s case=%s status=%s", user.pk, case.case_id, case.status
        )
        messages.error(request, RACE_MESSAGE)
        return redirect(detail_url)
    messages.success(request, CONFIRM_SUCCESS_MESSAGE)
    return redirect(detail_url)


@role_required("scheduler", "admin")
@require_POST
def case_deny(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """POST de negação do agendamento com motivo obrigatório (R4/R5)."""
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(Case, case_id=case_id)
    detail_url = _case_detail_url(case)
    form = SchedulerDenyForm(request.POST)
    if not form.is_valid():
        logger.warning("scheduler_deny_invalid user=%s case=%s", user.pk, case.case_id)
        # Mensagem estável do motivo obrigatório (testada) — sem texto genérico
        # de "campo obrigatório" do Django.
        messages.error(request, DENY_REASON_REQUIRED_MESSAGE)
        return redirect(detail_url)
    try:
        deny_case_scheduling(
            case,
            reason=form.cleaned_data["reason"],
            user=user,
            role=active_role,
        )
    except ValueError as exc:
        logger.warning(
            "scheduler_deny_rejected user=%s case=%s erro=%s", user.pk, case.case_id, exc
        )
        messages.error(request, str(exc))
        return redirect(detail_url)
    except TransitionNotAllowed:
        logger.warning(
            "scheduler_deny_race user=%s case=%s status=%s", user.pk, case.case_id, case.status
        )
        messages.error(request, RACE_MESSAGE)
        return redirect(detail_url)
    messages.success(request, DENY_SUCCESS_MESSAGE)
    return redirect(detail_url)


@role_required("scheduler", "admin")
@require_POST
def case_reopen(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """POST de desmarcar por intercorrência (unidade 1 apenas — R4).

    Unidade 2 (ou estado fora da janela) → erro nomeado do serviço traduzido
    em mensagem + redirect (o banner "intercorrência desabilitada" já impede o
    POST pela UI; o serviço é a barreira do POST direto). Sucesso → o caso
    volta a ``AWAITING_SCHEDULING`` na aba aguardando.
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(Case, case_id=case_id)
    detail_url = _case_detail_url(case)
    form = SchedulerReopenForm(request.POST)
    if not form.is_valid():
        logger.warning("scheduler_reopen_invalid user=%s case=%s", user.pk, case.case_id)
        messages.error(request, REOPEN_REASON_REQUIRED_MESSAGE)
        return redirect(detail_url)
    try:
        reopen_scheduling_after_incident(
            case,
            reason=form.cleaned_data["reason"],
            user=user,
            role=active_role,
        )
    except ValueError as exc:
        logger.warning(
            "scheduler_reopen_rejected user=%s case=%s erro=%s", user.pk, case.case_id, exc
        )
        messages.error(request, str(exc))
        return redirect(detail_url)
    except TransitionNotAllowed:
        logger.warning(
            "scheduler_reopen_race user=%s case=%s status=%s", user.pk, case.case_id, case.status
        )
        messages.error(request, RACE_MESSAGE)
        return redirect(detail_url)
    messages.success(request, REOPEN_SUCCESS_MESSAGE)
    return redirect(detail_url)


# ── R7: PDF do caso processado pelo próprio agendador ─────────────────────


@role_required("scheduler", "admin")
def case_pdf(
    request: HttpRequest,
    case_id: uuid.UUID,
    position: int,
) -> HttpResponse:
    """Serve o PDF do documento do relatório na ``position`` (R7).

    Gate por caso (semântica do ``_get_scheduler_processed_case_or_404`` do
    ats-web) ANTES de resolver o documento: 404 fail-closed quando
    ``scheduled_by != request.user`` ou status fora do conjunto pós-decisão —
    inclusive caso reaberto por intercorrência (``scheduled_by`` limpo).
    Documento/position inexistente → 404. Entrega via ``FileResponse`` com
    content-type ``application/pdf``, nome original e ``Cache-Control:
    no-store``.
    """
    user = _require_user(request)
    case = get_object_or_404(
        Case.objects.only("case_id", "status", "scheduled_by_id"),
        case_id=case_id,
    )
    if case.scheduled_by_id != user.pk or case.status not in PDF_ALLOWED_STATUSES:
        # 404 fail-closed (nunca 403): não distingue existência/caso alheio.
        raise Http404("PDF indisponível para este caso.")
    document = get_object_or_404(
        CaseDocument.objects.only("pk", "case_id", "file", "original_filename"),
        case=case,
        position=position,
    )
    response = FileResponse(
        document.file.open("rb"),
        content_type=PDF_CONTENT_TYPE,
        filename=document.original_filename or "documento.pdf",
    )
    response["Cache-Control"] = "no-store"
    return cast(HttpResponse, response)
