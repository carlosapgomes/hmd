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
(identificação real + sumário completo re-identificado + decisões médicas + dados de
agendamento + thread — sem artefatos clínicos) acrescido das ações por estado:
em ``SCHEDULER_REQUESTED``/``AWAITING_SCHEDULING`` os forms de confirmar
(unidade 1|2 + data/hora futura + local) e negar (motivo); em
``FINAL_REPLY_POSTED`` com unidade 1 o form de desmarcar (intercorrência);
unidade 2 exibe o banner "intercorrência desabilitada". Os POSTs
(``case_confirm``/``case_deny``/``case_reopen``) delegam aos serviços
transacionais do slice 001/002; erros de serviço/estado/concorrência
(``ValueError`` nomeado e ``TransitionNotAllowed``) viram mensagem + redirect
ao detalhe — nunca 500, nenhuma escrita parcial (R5/D5).

case-work-lock, slice 004 (R1–R8, D4): a página de trabalho do agendador É o
detalhe de caso agendável (não há GET dedicado de confirmação/negação como no
ATS): o GET RESERVA o caso (claim com contexto ``scheduler_confirm`` e papel
``scheduler``), os forms carregam o ``lock_token``, os POSTs de
confirmação/negação exigem a posse (validada sob row lock na MESMA transação da
escrita, com release determinístico ao concluir), a fila mostra o estado de
reserva (🔒 Reservado/▶ Continuar/ação livre) e o heartbeat/«Sair sem decidir»
vivem nos endpoints ``scheduler:lock_renew``/``lock_release``/``case_leave``.
Casos processados abrem read-only, sem claim.

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
from collections import Counter
from datetime import datetime
from typing import cast
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST
from django_fsm import TransitionNotAllowed

from apps.accounts.decorators import role_required
from apps.accounts.models import User
from apps.cases.day_bounds import local_day_bounds
from apps.cases.locks import (
    CaseLockConflictError,
    assert_case_lock,
    claim_case_lock,
    compute_lock_display,
    expire_stale_locks_for_statuses,
    release_case_lock,
    renew_case_lock,
)
from apps.cases.models import Case, CaseDocument, CaseStatus, DoctorDisposition
from apps.cases.navigation import resolve_safe_next_url
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
# AWAITING_SCHEDULING (única aba por estado — slice 005). ``processados``
# NÃO é mais um conjunto de estados: é a janela do dia corrente por
# decisor (``scheduling_decided_by`` + ``scheduling_decided_at`` do slice
# 004, confirmação OU negação — montada inline em ``_build_queue_context``),
# qualquer status atual; o tuple abaixo segue como referência dos estados
# pós-decisão e valida o ``?tab=`` (desconhecido cai em ``aguardando``).
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

# Reserva do caso na página de trabalho do agendador (case-work-lock, slice
# 004, D4): contexto/papel do lock (lease default de 300s — tarefa curta, sem
# override por contexto), as duas mensagens de audiência (reserva ativa de
# outro agendador e perda da reserva) e o flash do «Sair sem decidir».
SCHEDULER_LOCK_CONTEXT = "scheduler_confirm"
SCHEDULER_LOCK_ROLE = "scheduler"
LOCK_RESERVED_MESSAGE = (
    "Este caso está reservado por {holder}. Aguarde até que a reserva expire para acessá-lo."
)
LOCK_LOST_MESSAGE = (
    "Sua reserva para este caso expirou ou não é válida. Volte para a fila e tente novamente."
)
LOCK_TOKEN_MISSING_MESSAGE = "Token de reserva não fornecido."
CASE_LEAVE_MESSAGE = "Reserva liberada — o caso voltou a ficar disponível na fila."

# Perfil do catálogo por tipo (ordem/rotulo/subtipo dos cards da fila).
_PROFILE_BY_TYPE = {profile.procedure_type: profile for profile in PROCEDURE_PROFILES}
_CATALOG_INDEX: dict[str, int] = {
    profile.procedure_type: index for index, profile in enumerate(PROCEDURE_PROFILES)
}
# Rótulo legível da disposição médica atual da row (DoctorDisposition) —
# OLI-002 D8: projeção das rows já pré-carregadas, sem query por card.
_DISPOSITION_LABELS = dict(DoctorDisposition.choices)


def _require_user(request: HttpRequest) -> User:
    """Usuário autenticado de uma view já protegida por ``@role_required``."""
    user = request.user
    if not isinstance(user, User):
        raise PermissionDenied
    return user


def _case_detail_url(case: Case) -> str:
    """URL canônica do detalhe do caso (destino dos redirects/flash)."""
    return reverse("scheduler:case_detail", args=[case.case_id])


def _lock_token_from_request(request: HttpRequest) -> uuid.UUID | None:
    """Token de reserva do corpo do POST (ausente/ilegível → ``None``).

    Formato canônico ``uuid.UUID``: qualquer outro valor é tratado como "sem
    posse" e nunca vira erro (os endpoints do heartbeat respondem JSON e os
    POSTs de ação respondem mensagem + redirect — nunca 500, D4).
    """
    raw_token = request.POST.get("lock_token", "")
    try:
        return uuid.UUID(raw_token) if raw_token else None
    except (ValueError, AttributeError):
        return None


def _lock_endpoint_case(request: HttpRequest, case_id: uuid.UUID) -> Case:
    """Caso dos endpoints de reserva/«sair sem decidir» (R7/R8, D4).

    O guard do agendador é por papel ativo (decorator ``role_required``) — sem
    matriz por caso (fila completa). Caso inexistente → 404.
    """
    _require_user(request)
    return get_object_or_404(Case, case_id=case_id)


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


def _queue_item(case: Case, *, user: User) -> dict[str, object]:
    """Item de card da fila: identificação + tipos declarados + unidade + reserva.

    Os tipos saem das rows já pré-carregadas (``prefetch_related``),
    declaradas pelo NIR, na ordem canônica do catálogo; a unidade de destino
    aparece apenas quando já definida no agendamento (R2). O estado de reserva
    (``compute_lock_display``) alimenta as três variantes de ação do card
    aguardando: livre (ação), própria (▶ Continuar) e de outro (🔒 Reservado).
    OLI-002 D8: disposição por procedimento projetada das mesmas rows, sem
    consultar events por card.
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
        "patient_gender": case.patient_gender or "",
        "origin_unit": case.origin_unit or "",
        "days_on_screen": case.days_on_screen,
        "agency_record_number": case.agency_record_number or "—",
        "created_at": case.created_at,
        "unit_label": unit_label_text,
        "reopen_reason": case.scheduling_reopen_reason.strip(),
        "lock": compute_lock_display(case, user),
        "procedures": [
            {
                "label": _PROFILE_BY_TYPE[row.procedure_type].label,
                "subtype": _PROFILE_BY_TYPE[row.procedure_type].doctor_subtipo,
                "disposition_label": _DISPOSITION_LABELS.get(
                    row.doctor_disposition, row.doctor_disposition
                ),
            }
            for row in rows
        ],
    }


# ── R1/R2: fila ───────────────────────────────────────────────────────────


def _build_queue_context(request: HttpRequest) -> dict[str, object]:
    """Contexto da fila do agendador (assembly único página + partial, D1/D9).

    Mesmos guards, ordenação por tempo de tela e expiração lazy de leases
    antes da montagem — o partial executa o MESMO caminho da página cheia
    (D9: não é GET puro; o efeito colateral de expiração é preexistente).
    OLI-002 D7: ``procedure_type`` server-side opcional SÓ em ``aguardando``,
    validado contra o catálogo (inválido = vazio), aplicado antes da
    ordenação/paginação e preservado em ``filter_qs``/form/paginação/partial.
    """
    user = _require_user(request)
    tab = request.GET.get("tab", "aguardando")
    if tab not in TAB_STATUSES:
        tab = "aguardando"
    requested_procedure_type = request.GET.get("procedure_type", "")
    procedure_type = (
        requested_procedure_type
        if tab == "aguardando" and requested_procedure_type in _PROFILE_BY_TYPE
        else ""
    )

    # R6/D4: expiração lazy das reservas vencidas antes de montar a fila — uma
    # lease vencida não pode continuar bloqueando o card.
    expire_stale_locks_for_statuses(TAB_STATUSES["aguardando"])

    if tab == "processados":
        # Slice 005 (R2/D5): «Processados Hoje» — decisões de agendamento
        # (confirmação OU negação) do dia corrente NO FUSO LOCAL pelo próprio
        # agendador, qualquer status atual; caso de dia anterior (ou decisor
        # nulo do backfill fail-closed) fora das abas — consultável no
        # Histórico do slice 004. Ordenação pela data/hora da decisão.
        start, end = local_day_bounds()
        cases = (
            Case.objects.filter(
                scheduling_decided_by=user,
                scheduling_decided_at__gte=start,
                scheduling_decided_at__lt=end,
            )
            .order_by("-scheduling_decided_at", "created_at", "case_id")
            .select_related("locked_by")
            .prefetch_related("procedures")
        )
    else:
        cases = Case.objects.filter(status__in=TAB_STATUSES[tab])
        if procedure_type:
            cases = cases.filter(
                procedures__declared_by_nir=True,
                procedures__procedure_type=procedure_type,
            ).distinct()
        cases = (
            cases.order_by(F("days_on_screen").desc(nulls_last=True), "created_at", "case_id")
            .select_related("locked_by")
            .prefetch_related("procedures")
        )
    page = Paginator(cases, QUEUE_PAGE_SIZE).get_page(request.GET.get("page", "1"))
    items = [_queue_item(case, user=user) for case in page.object_list]

    filter_parts: list[tuple[str, str]] = [("tab", tab)]
    if procedure_type:
        filter_parts.append(("procedure_type", procedure_type))

    return {
        "tab": tab,
        "procedure_type": procedure_type,
        "procedure_type_options": [
            {"value": profile.procedure_type, "label": profile.label}
            for profile in PROCEDURE_PROFILES
        ],
        "page_obj": page,
        "items": items,
        "filter_qs": urlencode(filter_parts),
    }


@role_required("scheduler", "admin")
def queue(request: HttpRequest) -> HttpResponse:
    """Fila do agendador por aba (R2/D4; slice 005).

    Pendentes ordenadas por tempo de tela (``days_on_screen`` desc
    nulls-last, desempate FIFO por ``created_at`` e ``case_id`` — mesma
    expressão da fila médica); «Processados Hoje» pela data/hora da decisão
    do próprio agendador no dia corrente (mais recente primeiro). Sem filtro
    por unidade (fila completa, plano §4). Aba desconhecida cai no default
    ``aguardando``.
    """
    return render(request, "scheduler/queue.html", _build_queue_context(request))


@role_required("scheduler", "admin")
def queue_partial(request: HttpRequest) -> HttpResponse:
    """Fragmento da fila para o polling htmx de 20 s (ui-parity-queues D1/D9).

    Reutiliza o assembly da página cheia (``_build_queue_context``) e
    renderiza SÓ ``scheduler/_queue_content.html`` — o mesmo fragmento que a
    página inclui no server-side. Mesmos guards (``role_required``) e o mesmo
    efeito colateral de expiração lazy de leases (D9).
    """
    return render(request, "scheduler/_queue_content.html", _build_queue_context(request))


# ── Histórico do agendador (slice 004, D5/D6) ─────────────────────────────

# Cap da busca histórica (D6): no máximo 50 resultados por consulta, os mais
# novos primeiro (``order_by("-created_at")`` antes do slice) — mesmo padrão
# da busca NIR de encerrados do slice 003.
_HISTORICAL_SEARCH_CAP = 50


def _stripped_record(case: Case) -> str:
    """Nº de ocorrência sem bordas — chave da duplicidade (padrão NIR, D4)."""
    return case.agency_record_number.strip()


@role_required("scheduler", "admin")
def historical_search(request: HttpRequest) -> HttpResponse:
    """Busca histórica do agendador por nº de ocorrência OU nome (R2, D1/D6).

    Universo = ``scheduling_decided_at`` preenchido (confirmações E negações,
    reabertos, ``CLEANED`` e issue ativa, qualquer decisor; negativa médica
    encerrada nunca teve decisão de agendamento — fora por construção). O termo
    casa ``agency_record_number__icontains`` OU ``patient_name__icontains``;
    cap 50 (``-created_at`` antes do slice); sem critério → estado vazio.
    Leitura pura — nenhuma escrita. Cada resultado traz a referência humana
    (nº + sufixo curto quando duplicado), o status legível e os dados do
    agendamento anterior, com link ao detalhe em escopo histórico.
    """
    query = request.GET.get("q", "").strip()
    results: list[dict[str, object]] = []
    if query:
        matches = list(
            Case.objects.filter(scheduling_decided_at__isnull=False)
            .filter(Q(agency_record_number__icontains=query) | Q(patient_name__icontains=query))
            .select_related("scheduling_decided_by")
            .order_by("-created_at")[:_HISTORICAL_SEARCH_CAP]
        )
        # Nºs repetidos NOS RESULTADOS (mesma regra de «meus casos», D4):
        # contagem sobre a lista já carregada; nº em branco nunca é chave.
        repeated = Counter(record for record in map(_stripped_record, matches) if record)
        for case in matches:
            decided_by = case.scheduling_decided_by
            scheduled_unit = case.scheduled_unit
            denial_reason = case.scheduling_denial_reason.strip()
            reopen_reason = case.scheduling_reopen_reason.strip()
            results.append(
                {
                    "case": case,
                    "status_label": case.get_status_display(),
                    "patient_name": case.patient_name,
                    "agency_record_number": case.agency_record_number or "—",
                    # Sufixo só para nº duplicado E preenchido; senão "".
                    "reference_suffix": (
                        f" · {case.short_id}" if repeated[_stripped_record(case)] > 1 else ""
                    ),
                    "decided_by": decided_by.display_name if decided_by is not None else "",
                    "decided_at": case.scheduling_decided_at,
                    "unit_label": unit_label(scheduled_unit) if scheduled_unit else "",
                    # Dados do agendamento anterior (R3): data/hora e local
                    # disponíveis; ausentes → "—" no template.
                    "scheduled_datetime": case.scheduled_datetime,
                    "scheduled_location": case.scheduled_location.strip(),
                    "denial_reason": denial_reason,
                    "reopen_reason": reopen_reason,
                    # Reaberto por intercorrência = motivo persistido +
                    # agendamento corrente limpo (após re-confirmação o motivo
                    # persiste mas o agendamento volta a existir → confirmado).
                    "is_reopened": bool(reopen_reason) and case.scheduled_datetime is None,
                }
            )
    return render(
        request,
        "scheduler/historical_search.html",
        {"query": query, "results": results},
    )


@role_required("scheduler", "admin")
def historical_case_detail(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Detalhe do caso em escopo histórico: leitura, sem ações (R3/R4).

    Guarda de escopo histórico (fail-closed): só caso com
    ``scheduling_decided_at`` preenchido — fora disso → 404 sem vazar conteúdo.
    Reutiliza o presenter do detalhe vigente (mesmo conteúdo) com as flags de
    ação forçadas a ``False`` (sem claim de reserva, sem forms de
    confirmação/negação/desmarcação, sem banner de intercorrência) e o gate de
    PDF/texto preservado (``scheduled_by == request.user`` E status em
    ``PDF_ALLOWED_STATUSES`` — ``CLEANED`` segue fora, paridade ats-web).
    """
    user = _require_user(request)
    case = get_object_or_404(
        Case.objects.select_related("scheduling_decided_by"),
        case_id=case_id,
        scheduling_decided_at__isnull=False,
    )
    context = build_scheduler_case_detail_context(case)
    # Escopo histórico é leitura: nenhuma ação de agendamento, nenhuma reserva.
    context["lock_token"] = ""
    context["can_decide_scheduling"] = False
    context["can_reopen"] = False
    context["incident_disabled"] = False
    context["is_historical"] = True

    can_view_pdf = case.scheduled_by_id == user.pk and case.status in PDF_ALLOWED_STATUSES
    context["can_view_pdf"] = can_view_pdf
    if can_view_pdf:
        context["case"] = case
        documents: list[CaseDocument] = list(case.documents.all())
        context["pdf_items"] = _build_pdf_items(case, documents)
        context["detail_url"] = reverse("scheduler:historical_case_detail", args=[case.case_id])
    return render(request, "scheduler/case_detail.html", context)


# ── R3/R4: detalhe ────────────────────────────────────────────────────────


@role_required("scheduler", "admin")
def case_detail(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Detalhe limitado do caso para o agendador (R3/D3).

    Guard de papel ativo (scheduler/admin); caso inexistente → 404. Contexto
    do presenter puro (sumário completo re-identificado exclusivamente na
    renderização) acrescido dos forms de ação por estado (confirmar/negar em
    SCHEDULER_REQUESTED/AWAITING_SCHEDULING; desmarcar em FINAL_REPLY_POSTED
    com unidade 1) e dos documentos PDF apenas quando a condição R7 vale
    (``scheduled_by == request.user`` E status pós-decisão).

    R1–R3/D4 (case-work-lock, slice 004): caso agendável RESERVA o caso no
    GET (claim ``scheduler_confirm``) e exporta o token nos forms; reserva de
    outro agendador → aviso + redirect à fila; caso processado sem claim.
    """
    user = _require_user(request)
    case = get_object_or_404(Case, case_id=case_id)
    # R1/R3/D4: abrir a página de trabalho (detalhe de caso AGENDÁVEL) RESERVA
    # o caso — claim atômico com o contexto do agendador. Reserva ativa de
    # OUTRO agendador barra a abertura (aviso com o portador + volta à fila),
    # sem tocar o lock dele; o mesmo agendador reabrindo renova com token novo.
    # Caso processado abre read-only, SEM claim.
    lock_token = ""
    if case.status in TAB_STATUSES["aguardando"]:
        try:
            lock = claim_case_lock(
                case,
                user=user,
                context=SCHEDULER_LOCK_CONTEXT,
                role=SCHEDULER_LOCK_ROLE,
            )
        except CaseLockConflictError:
            case.refresh_from_db()
            holder = (
                case.locked_by.display_name if case.locked_by is not None else "outro agendador"
            )
            logger.warning("scheduler_detail_reserved user=%s case=%s", user.pk, case.case_id)
            messages.warning(request, LOCK_RESERVED_MESSAGE.format(holder=holder))
            return redirect(reverse("scheduler:queue"))
        lock_token = str(lock.token)

    context = build_scheduler_case_detail_context(case)
    context["lock_token"] = lock_token
    if bool(context["can_decide_scheduling"]):
        context["confirm_form"] = SchedulerConfirmForm()
        context["deny_form"] = SchedulerDenyForm()
    if bool(context["can_reopen"]):
        context["reopen_form"] = SchedulerReopenForm()

    can_view_pdf = case.scheduled_by_id == user.pk and case.status in PDF_ALLOWED_STATUSES
    context["can_view_pdf"] = can_view_pdf
    if can_view_pdf:
        # Cards do relatório (slice PVP-004, R2/D1): a view monta pdf_items
        # (position/filename/size/serve/viewer) com reverse; o template itera.
        context["case"] = case
        documents: list[CaseDocument] = list(case.documents.all())
        context["pdf_items"] = _build_pdf_items(case, documents)
        context["detail_url"] = reverse("scheduler:case_detail", args=[case.case_id])
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

    Form válido COM posse da reserva → ``confirm_case_scheduling`` (atomic +
    transições FSM até ``FINAL_REPLY_POSTED`` + resposta final ao NIR); sucesso
    → flash + redirect ao detalhe. A posse é validada SOB ROW LOCK na MESMA
    transação da escrita (``select_for_update`` + ``assert_case_lock``, D4 =
    espelho do D2 item 5 do médico) e a reserva é liberada na mesma transação —
    sem janela de expiração/tomada entre validação e persistência. Sem posse
    (token ausente/ilegível) o POST não escreve NADA; token divergente/vencido
    → ``CaseLockConflictError`` do assert (rollback, nada escrito). Form
    inválido ou erro de serviço/estado/concorrência (``ValueError``/
    ``TransitionNotAllowed``) → mensagem + redirect — nunca 500 (R4/R5/D5).
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(Case, case_id=case_id)
    detail_url = _case_detail_url(case)
    token = _lock_token_from_request(request)
    if token is None:
        logger.warning("scheduler_confirm_no_lock user=%s case=%s", user.pk, case.case_id)
        messages.error(request, LOCK_LOST_MESSAGE)
        return redirect(detail_url)
    form = SchedulerConfirmForm(request.POST)
    if not form.is_valid():
        logger.warning("scheduler_confirm_invalid user=%s case=%s", user.pk, case.case_id)
        messages.error(request, _form_error_text(form))
        return redirect(detail_url)
    try:
        with transaction.atomic():
            # D4: posse validada SOB ROW LOCK na MESMA transação da escrita
            # (time-of-check == time-of-use); o atomic do serviço vira savepoint.
            locked = Case.objects.select_for_update().get(pk=case.pk)
            assert_case_lock(locked, token)
            confirm_case_scheduling(
                locked,
                unit=form.cleaned_data["unit"],
                scheduled_datetime=_schedule_confirm_datetime(form),
                scheduled_location=form.cleaned_data["scheduled_location"],
                user=user,
                role=active_role,
            )
            # Release determinístico na MESMA transação (espelho do médico).
            release_case_lock(locked, token)
    except CaseLockConflictError:
        logger.warning("scheduler_confirm_lock_lost user=%s case=%s", user.pk, case.case_id)
        messages.error(request, LOCK_LOST_MESSAGE)
        return redirect(detail_url)
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
    """POST de negação do agendamento com motivo obrigatório (R4/R5).

    Mesma regra de posse da confirmação (D4): sem ``lock_token`` nada é
    escrito; a posse é validada sob row lock na mesma transação da escrita e a
    reserva é liberada ao concluir.
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(Case, case_id=case_id)
    detail_url = _case_detail_url(case)
    token = _lock_token_from_request(request)
    if token is None:
        logger.warning("scheduler_deny_no_lock user=%s case=%s", user.pk, case.case_id)
        messages.error(request, LOCK_LOST_MESSAGE)
        return redirect(detail_url)
    form = SchedulerDenyForm(request.POST)
    if not form.is_valid():
        logger.warning("scheduler_deny_invalid user=%s case=%s", user.pk, case.case_id)
        # Mensagem estável do motivo obrigatório (testada) — sem texto genérico
        # de "campo obrigatório" do Django.
        messages.error(request, DENY_REASON_REQUIRED_MESSAGE)
        return redirect(detail_url)
    try:
        with transaction.atomic():
            locked = Case.objects.select_for_update().get(pk=case.pk)
            assert_case_lock(locked, token)
            deny_case_scheduling(
                locked,
                reason=form.cleaned_data["reason"],
                user=user,
                role=active_role,
            )
            release_case_lock(locked, token)
    except CaseLockConflictError:
        logger.warning("scheduler_deny_lock_lost user=%s case=%s", user.pk, case.case_id)
        messages.error(request, LOCK_LOST_MESSAGE)
        return redirect(detail_url)
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


# ── case-work-lock D4: heartbeat e «Sair sem decidir» (R7/R8) ─────────────


@role_required("scheduler", "admin")
@require_POST
def lock_renew(request: HttpRequest, case_id: uuid.UUID) -> JsonResponse:
    """POST: renova a reserva do caso (heartbeat da página de trabalho, R7/D4).

    Requer ``lock_token`` no corpo. Nunca 500: token ausente/ilegível ou sem
    posse vigente → JSON ``success=false`` (o JS exibe o aviso e desabilita o
    submit; reabrir pela fila refaz a reserva). Posse válida → lease renovada
    (300s do contexto do agendador), com ``locked_until`` em ISO e evento
    ``CASE_LOCK_RENEWED`` na trilha.
    """
    case = _lock_endpoint_case(request, case_id)
    token = _lock_token_from_request(request)
    if token is None:
        return JsonResponse({"success": False, "error": LOCK_TOKEN_MISSING_MESSAGE})
    try:
        lock = renew_case_lock(case, token)
    except CaseLockConflictError:
        logger.warning("scheduler_lock_renew_lost case=%s", case.case_id)
        return JsonResponse({"success": False, "error": LOCK_LOST_MESSAGE})
    return JsonResponse({"success": True, "locked_until": lock.locked_until.isoformat()})


@role_required("scheduler", "admin")
@require_POST
def lock_release(request: HttpRequest, case_id: uuid.UUID) -> JsonResponse:
    """POST: libera a reserva do caso (release best-effort do JS, R7/D4).

    Idempotente por contrato: sem posse vigente (reserva já liberada pela
    ação/saída, expirada ou token de outra aba) responde ``success=false`` —
    nada a liberar, nunca erro.
    """
    case = _lock_endpoint_case(request, case_id)
    token = _lock_token_from_request(request)
    if token is None:
        return JsonResponse({"success": False, "error": LOCK_TOKEN_MISSING_MESSAGE})
    try:
        release_case_lock(case, token)
    except CaseLockConflictError:
        return JsonResponse({"success": False})
    return JsonResponse({"success": True})


@role_required("scheduler", "admin")
@require_POST
def case_leave(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """POST: «Sair sem decidir» — libera a reserva e volta à fila (R8/D4).

    A liberação é server-side (não depende do JavaScript do heartbeat): com
    posse válida o ``release_case_lock`` devolve o caso à fila imediatamente e
    a página da fila mostra o flash informativo. Sem token ou sem posse (caso
    já decidido, reserva expirada, token de outra aba) → apenas o redirect,
    idempotente e sem erro.
    """
    case = _lock_endpoint_case(request, case_id)
    token = _lock_token_from_request(request)
    if token is not None:
        try:
            release_case_lock(case, token)
        except CaseLockConflictError:
            pass
        else:
            messages.info(request, CASE_LEAVE_MESSAGE)
    return redirect(reverse("scheduler:queue"))


def _build_pdf_items(case: Case, documents: list[CaseDocument]) -> list[dict[str, object]]:
    """Itens do card do PDF original (slice PVP-004, R2/D1).

    A view monta ``pdf_items`` (position/filename/size/serve/viewer) com
    ``reverse`` — o template apenas itera, sem lógica de URL (mesma regra do
    slice PVP-003 no médico).
    """
    return [
        {
            "position": document.position,
            "filename": document.original_filename,
            "size": document.size_bytes,
            "serve_url": reverse("scheduler:case_pdf", args=[case.case_id, document.position]),
            "viewer_url": reverse("scheduler:pdf_viewer", args=[case.case_id, document.position]),
        }
        for document in documents
    ]


@role_required("scheduler", "admin")
def pdf_viewer(
    request: HttpRequest,
    case_id: uuid.UUID,
    position: int,
) -> HttpResponse:
    """Viewer mobile do PDF original do caso processado pelo próprio agendador.

    Mesmo gate fail-closed de ``case_pdf``: papel ativo scheduler/admin +
    ``scheduled_by == request.user`` + status em ``PDF_ALLOWED_STATUSES``
    (fora → 404 fail-closed, sem distinguir). Position inexistente ou
    documento sem arquivo → 404 (lição P1-4 do PVP-002). O ``?next=`` passa
    por ``resolve_safe_next_url`` (fallback ao detalhe do agendador); o
    template ``pdf_viewer/mobile_pdf_viewer.html`` recebe título, URLs do
    PDF e volta.
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
        CaseDocument.objects.only("pk", "case_id", "file"),
        case=case,
        position=position,
    )
    if not document.file:
        raise Http404
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    serve_url = reverse("scheduler:case_pdf", args=[case.case_id, position])
    back_url = resolve_safe_next_url(request, detail_url)
    return render(
        request,
        "pdf_viewer/mobile_pdf_viewer.html",
        {
            "viewer_title": "PDF Original",
            "pdf_url": serve_url,
            "fallback_pdf_url": serve_url,
            "back_url": back_url,
        },
    )


# ── R7: PDF do caso processado pelo próprio agendador ─────────────────────


@role_required("scheduler", "admin")
@xframe_options_sameorigin
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
