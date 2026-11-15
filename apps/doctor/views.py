"""Views da fila, do detalhe e da decisão do caso médico (doctor-queue-decision).

Slice 002 (R2–R5, D2/D5): fila única em ``/doctor/`` para papel ativo
doctor/admin — abas por estado (``aguardando`` default = ``AWAITING_DOCTOR``;
``decididos`` = estados pós-decisão), ordenada por tempo de tela
(``days_on_screen`` desc, desempate FIFO por ``created_at``), filtro de
subtipo no querystring e paginação (``Paginator`` — primeira view paginada do
projeto). O access control combina o guard ``role_required`` (papel ativo)
com ``apps.doctor.access`` (fatia por subtipo — predicado único
fila×detalhe×decisão, D2). Os fatos do usuário (``DoctorAccess``) são
computados **1× por request** e reutilizados nos filtros SQL, nas contagens e
no re-check por caso da página.

Slice 003 (R3/R4, D3/D7): ``doctor:case_detail`` renderiza o contexto do
presenter puro re-identificado (read-only — o formulário/POST de decisão é o
slice 004) e ``doctor:case_pdf`` serve o PDF original por ``position`` via
``FileResponse`` (404 sem documento). Ambas reutilizam o MESMO guard do slice
002 (papel ativo + ``can_access_case``), sem duplicar o predicado.

Slice 004 (R1–R5, D4/D6): ``doctor:case_decide`` — GET renderiza o form
dinâmico por procedimento apenas em ``AWAITING_DOCTOR`` (já decidido →
redirect ao detalhe com mensagem); POST reforça o guard ANTES de qualquer
escrita, valida e delega ao serviço atômico do change 03
(``record_doctor_procedure_decisions``) → redirect ao detalhe com flash. A
submissão concorrente/estado inválido (``TransitionNotAllowed`` do serviço ou
estado fora de ``AWAITING_DOCTOR``) é traduzida em mensagem "já decidido por
outro médico" + redirect ao detalhe — nunca 500 (D4: sem lock de posse; o
segundo POST perde a corrida no atomic do serviço e é desfeito por inteiro).
O detalhe pós-decisão (R5) continua read-only — decisões por procedimento,
ator/data do evento e trilha vêm do presenter estendido.

case-work-lock, slice 003 (R1–R3, D3): ``doctor:lock_renew`` e
``doctor:lock_release`` são os endpoints JSON consumidos pelo
``static/js/work_lock.js`` (heartbeat com detecção de atividade e release
best-effort em navegação) e ``doctor:case_leave`` é o POST server-side do
«Sair sem decidir» — libera a reserva com flash informativo e volta à fila,
sem depender de JavaScript. Todos reutilizam o guard único do fluxo
(``_lock_endpoint_case``) e nunca levantam 500: token ausente/ilegível ou sem
posse vigente viram JSON/redirect idempotente.

Fechamento da negativa total (nir-result-closure, slice 001): a decisão de
negação de TODOS os procedimentos é seguida, no POST do ``case_decide``, da
publicação automática da resposta final ao NIR (``post_doctor_denial_reply``
— caso → ``FINAL_REPLY_POSTED`` com a resposta na thread). O serviço do
change 03 devolve ``None`` e trabalha sobre um ``locked`` re-buscado
(``select_for_update``), então o ``case`` em memória fica STALE: a view o
re-lê (``refresh_from_db``) e invoca o fechamento SOMENTE quando o status é
``DOCTOR_DENIED`` — decisão parcial (``SCHEDULER_REQUESTED``) segue o fluxo
existente sem fechamento. ``ValueError`` do fechamento → mensagem + redirect
(nunca 500; o caso permanece ``DOCTOR_DENIED``, visível e operável).
"""

from __future__ import annotations

import logging
import uuid
from typing import cast
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, QuerySet
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST
from django_fsm import TransitionNotAllowed

from apps.accounts.decorators import role_required
from apps.accounts.models import User
from apps.cases.closure import post_doctor_denial_reply
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
from apps.cases.procedure_catalog import PROCEDURE_PROFILES, VALID_DOCTOR_SUBTYPES
from apps.cases.procedures import get_declared_procedure_types, record_doctor_procedure_decisions

from .access import DoctorAccess, can_access_case
from .forms import DoctorDecisionForm
from .presenters import build_case_detail_context

logger = logging.getLogger(__name__)

# Content-type dos documentos do relatório (D7 — mesmo padrão do intake).
PDF_CONTENT_TYPE = "application/pdf"

# Tamanho de página da primeira view paginada do projeto (D5) — sem padrão
# anterior no HMD (my_cases não pagina).
QUEUE_PAGE_SIZE = 20

# Estados por aba (R3/D5): ``aguardando`` (default) = AWAITING_DOCTOR;
# ``decididos`` = DOCTOR_DENIED|DOCTOR_ACCEPTED|SCHEDULER_REQUESTED —
# DOCTOR_ACCEPTED é transitório no encadeamento do slice 003; incluí-lo no
# filtro é inofensivo e cobre casos de borda.
TAB_STATUSES: dict[str, tuple[CaseStatus, ...]] = {
    "aguardando": (CaseStatus.AWAITING_DOCTOR,),
    "decididos": (
        CaseStatus.DOCTOR_DENIED,
        CaseStatus.DOCTOR_ACCEPTED,
        CaseStatus.SCHEDULER_REQUESTED,
    ),
}

# Mensagens de estado da decisão (R2–R4, D4/D6) — texto verificado nos testes.
DECISION_RECORDED_MESSAGE = "Decisão registrada."
ALREADY_DECIDED_DETAIL_MESSAGE = (
    "Este caso já foi decidido — exibindo o detalhe em modo de leitura."
)
ALREADY_DECIDED_RACE_MESSAGE = "Caso já decidido por outro médico — sua decisão não foi registrada."
NO_DECLARED_PROCEDURES_MESSAGE = "Não há procedimentos declarados para este caso — nada a decidir."
# Fechamento da negativa total (nir-result-closure, slice 001): falha rara
# entre a decisão e a publicação da resposta final ao NIR — a decisão fica
# registrada e o caso permanece DOCTOR_DENIED, visível e operável via admin
# (design D1, risco residual aceito). Mensagem + redirect, nunca 500.
DENIAL_REPLY_ERROR_MESSAGE = (
    "Decisão registrada, mas a resposta final ao NIR não pôde ser publicada."
)

# Reserva do caso na página de trabalho (case-work-lock, D2): contexto/papel do
# lock da decisão médica (lease de 1h renovável — slice 003) e as duas
# mensagens de audiência: reserva ativa de outro médico e perda da reserva.
DOCTOR_LOCK_CONTEXT = "doctor_decision"
DOCTOR_LOCK_ROLE = "doctor"
LOCK_RESERVED_MESSAGE = (
    "Este caso está reservado por {holder}. Aguarde até que a reserva expire para acessá-lo."
)
LOCK_LOST_MESSAGE = (
    "Sua reserva para este caso expirou ou não é válida. Volte para a fila e tente novamente."
)
# Heartbeat e «sair sem decidir» (case-work-lock, slice 003, D3): o token da
# reserva chega no corpo dos endpoints JSON; a liberação explícita pelo botão
# é server-side (independe do JavaScript, que só a antecipa em navegação).
LOCK_TOKEN_MISSING_MESSAGE = "Token de reserva não fornecido."
CASE_LEAVE_MESSAGE = "Reserva liberada — o caso voltou a ficar disponível na fila."

# Perfil do catálogo por tipo (ordem/rotulo/subtipo dos cards da fila).
_PROFILE_BY_TYPE = {profile.procedure_type: profile for profile in PROCEDURE_PROFILES}
# Rótulo legível da disposição médica atual da row (DoctorDisposition) —
# OLI-003 D8: projetada das rows já pré-carregadas, sem consultar events.
_DISPOSITION_LABELS = dict(DoctorDisposition.choices)
# Ordem canônica de exibição dos tipos declarados (registro do catálogo).
_CATALOG_INDEX: dict[str, int] = {
    profile.procedure_type: index for index, profile in enumerate(PROCEDURE_PROFILES)
}
# Tipos do catálogo por subtipo (filtro de subtipo e contagem de pendentes).
_TYPES_BY_SUBTYPE: dict[str, frozenset[str]] = {
    subtype: frozenset(
        profile.procedure_type
        for profile in PROCEDURE_PROFILES
        if profile.doctor_subtipo == subtype
    )
    for subtype in VALID_DOCTOR_SUBTYPES
}


def _require_user(request: HttpRequest) -> User:
    """Usuário autenticado de uma view já protegida por ``@role_required``."""
    user = request.user
    if not isinstance(user, User):
        raise PermissionDenied
    return user


def _accessible_cases(
    *,
    statuses: tuple[CaseStatus, ...],
    access: DoctorAccess,
    subtype: str,
) -> QuerySet[Case]:
    """Casos da fila visíveis ao papel ativo (matriz D2) + filtro de subtipo.

    A base de acesso replica exatamente ``access.allowed_types``: sem
    restrição para admin/generalista; médico com subtipos S fica restrito aos
    casos com ≥1 tipo declarado de subtipo em S (mesma regra do predicado).
    """
    cases = Case.objects.filter(status__in=statuses)
    allowed_types = access.allowed_types
    if allowed_types is not None:
        cases = cases.filter(
            procedures__declared_by_nir=True,
            procedures__procedure_type__in=allowed_types,
        ).distinct()
    if subtype:
        cases = cases.filter(
            procedures__declared_by_nir=True,
            procedures__procedure_type__in=_TYPES_BY_SUBTYPE[subtype],
        ).distinct()
    return (
        cases.order_by(F("days_on_screen").desc(nulls_last=True), "created_at", "case_id")
        .select_related("locked_by")
        .prefetch_related("procedures")
    )


def _lock_token_from_request(request: HttpRequest) -> uuid.UUID | None:
    """Token de reserva do corpo do POST (ausente/ilegível → ``None``).

    Formato canônico ``uuid.UUID``: qualquer outro valor é tratado como "sem
    posse" e nunca vira erro (os endpoints do heartbeat respondem JSON e a
    página de trabalho reexibe o formulário — nunca 500, D3).
    """
    raw_token = request.POST.get("lock_token", "")
    try:
        return uuid.UUID(raw_token) if raw_token else None
    except (ValueError, AttributeError):
        return None


def _lock_endpoint_case(request: HttpRequest, case_id: uuid.UUID) -> Case:
    """Caso dos endpoints de reserva, com o guard único do fluxo (D3).

    Mesmo predicado das demais views do médico: papel ativo ``doctor``/
    ``admin`` (decorator ``role_required``) + ``can_access_case`` (matriz de
    subtipo). Caso inexistente → 404; sem acesso → 403.
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(Case, case_id=case_id)
    if not can_access_case(user, case, active_role=active_role):
        raise PermissionDenied
    return case


def _declared_types_of_case(case: Case) -> tuple[str, ...]:
    """Tipos declarados do caso a partir das rows pré-carregadas (P2).

    Os casos da página chegam com ``procedures`` pré-carregadas
    (``prefetch_related``): derivar os tipos declarados das rows evita a query
    por card que o predicado ``get_declared_procedure_types`` faria.
    """
    return tuple(row.procedure_type for row in case.procedures.all() if row.declared_by_nir)


def _pending_counts_by_subtype(
    access: DoctorAccess, subtype_options: tuple[str, ...]
) -> dict[str, int]:
    """Contagem de casos ``AWAITING_DOCTOR`` acessíveis por subtipo (R5/D5).

    Base = pendentes acessíveis ao papel ativo (mesma regra da fila); a
    contagem por subtipo vale sobre os subtipos do filtro do usuário. Uma
    query por subtipo (≤4), independente da página.
    """
    awaiting = Case.objects.filter(status=CaseStatus.AWAITING_DOCTOR)
    allowed_types = access.allowed_types
    if allowed_types is not None:
        awaiting = awaiting.filter(
            procedures__declared_by_nir=True,
            procedures__procedure_type__in=allowed_types,
        ).distinct()
    counts: dict[str, int] = {}
    for subtype in subtype_options:
        counts[subtype] = (
            awaiting.filter(
                procedures__declared_by_nir=True,
                procedures__procedure_type__in=_TYPES_BY_SUBTYPE[subtype],
            )
            .values("case_id")
            .distinct()
            .count()
        )
    return counts


def _queue_item(case: Case, *, user: User) -> dict[str, object]:
    """Item de card da fila: campos de exibição + tipos declarados por subtipo.

    Os tipos saem das rows já pré-carregadas (``prefetch_related``),
    declaradas pelo NIR, na ordem canônica do catálogo; cada tipo leva o label
    e o ``doctor_subtipo`` (badge no template). OLI-003 D8: cada tipo leva
    também a disposição (``doctor_disposition`` + rótulo) e o instante da
    decisão (``doctor_decided_at``) da própria row — sem consultar
    ``decision_event``/trilha/presenter por card, sem N+1. O estado de
    reserva (D2) entra em ``lock`` para o card escolher a ação
    (Avaliar/Continuar/Reservado).
    """
    rows = [row for row in case.procedures.all() if row.declared_by_nir]
    rows.sort(key=lambda row: _CATALOG_INDEX[row.procedure_type])
    return {
        "case_id": case.case_id,
        "status_label": case.get_status_display(),
        "patient_name": case.patient_name or "—",
        "patient_age": case.patient_age,
        "patient_gender": case.patient_gender or "",
        "origin_unit": case.origin_unit or "",
        "days_on_screen": case.days_on_screen,
        "agency_record_number": case.agency_record_number or "—",
        "created_at": case.created_at,
        "lock": compute_lock_display(case, user),
        "procedures": [
            {
                "label": _PROFILE_BY_TYPE[row.procedure_type].label,
                "subtype": _PROFILE_BY_TYPE[row.procedure_type].doctor_subtipo,
                "disposition": row.doctor_disposition,
                "disposition_label": _DISPOSITION_LABELS.get(
                    row.doctor_disposition, row.doctor_disposition
                ),
                "decided_at": row.doctor_decided_at,
            }
            for row in rows
        ],
        # Subtipos declarados (sem duplicar, ordem canônica) — o filtro
        # client-side de exame só refina dentro do escopo server (D3).
        "subtypes": list(
            dict.fromkeys(_PROFILE_BY_TYPE[row.procedure_type].doctor_subtipo for row in rows)
        ),
    }


def _build_queue_context(request: HttpRequest) -> dict[str, object]:
    """Contexto da fila médica (assembly único página + partial, D1/D9).

    Mesmos guards, filtro de subtipo server-side autoritativo, ordenação
    por tempo de tela e expiração lazy de leases antes da montagem —
    o partial executa o MESMO caminho da página cheia (D9: não é GET
    puro; o efeito colateral de expiração é preexistente).
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    # Fatos do usuário por papel ativo computados 1× por request (P2):
    # reutilizados no filtro SQL, nas contagens e no re-check por card.
    access = DoctorAccess(user, active_role=active_role)

    tab = request.GET.get("tab", "aguardando")
    if tab not in TAB_STATUSES:
        tab = "aguardando"
    subtype_options = access.filterable
    requested_subtype = request.GET.get("subtype", "")
    subtype = requested_subtype if requested_subtype in subtype_options else ""

    cases = _accessible_cases(statuses=TAB_STATUSES[tab], access=access, subtype=subtype)
    if tab == "aguardando":
        # Expiração LAZY das reservas vencidas (D2/R8) antes de montar a fila:
        # sem isso uma aba abandonada bloquearia o card de outro médico até a
        # varredura seguinte.
        expire_stale_locks_for_statuses(TAB_STATUSES["aguardando"])
    page = Paginator(cases, QUEUE_PAGE_SIZE).get_page(request.GET.get("page", "1"))
    # Predicado único re-aplicado por caso da página como invariante do filtro
    # SQL — tipos declarados derivados das rows já pré-carregadas, sem query
    # por card (P2).
    items = [
        _queue_item(case, user=user)
        for case in page.object_list
        if access.allows_declared_types(_declared_types_of_case(case))
    ]
    pending_counts = _pending_counts_by_subtype(access, subtype_options)
    # OLI-001 D6: opções do filtro server-side já projetadas — em
    # ``aguardando`` cada opção carrega a contagem de pendentes; em
    # ``decididos`` o template ignora a contagem.
    subtype_option_items = [
        {"value": option, "label": option, "pending_count": pending_counts[option]}
        for option in subtype_options
    ]

    filter_parts: list[tuple[str, str]] = [("tab", tab)]
    if subtype:
        filter_parts.append(("subtype", subtype))

    return {
        "tab": tab,
        "subtype": subtype,
        "subtype_options": subtype_options,
        "subtype_option_items": subtype_option_items,
        "pending_counts": pending_counts,
        "page_obj": page,
        "items": items,
        "filter_qs": urlencode(filter_parts),
    }


@role_required("doctor", "admin")
def queue(request: HttpRequest) -> HttpResponse:
    """Fila médica por estado (abas) com filtro de subtipo (R2–R5, D2/D5).

    Ordenada por tempo de tela (``days_on_screen`` desc nulls-last, desempate
    FIFO) em TODAS as abas — o tempo de tela persiste pós-decisão.
    """
    return render(request, "doctor/queue.html", _build_queue_context(request))


@role_required("doctor", "admin")
def queue_partial(request: HttpRequest) -> HttpResponse:
    """Fragmento da fila para o polling htmx de 20 s (ui-parity-queues D1/D9).

    Reutiliza o assembly da página cheia (``_build_queue_context``) e
    renderiza SÓ ``doctor/_queue_content.html`` — o mesmo fragmento que a
    página inclui no server-side. Mesmos guards (``role_required`` +
    matriz de subtipo dentro do assembly) e o mesmo efeito colateral de
    expiração lazy de leases (D9).
    """
    return render(request, "doctor/_queue_content.html", _build_queue_context(request))


@role_required("doctor", "admin")
def case_detail(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Detalhe read-only do caso para o médico (R3, D3).

    Guard do slice 002 (papel ativo doctor/admin + matriz D2 por subtipo via
    ``can_access_case`` — predicado único, nunca duplicado); caso inexistente
    → 404. Renderiza o contexto do presenter puro (re-identificação exclusiva
    na renderização) acrescido dos documentos para o card de PDFs. Aceita
    AWAITING_DOCTOR e estados pós-decisão; sem formulário neste slice (004).
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(Case, case_id=case_id)
    if not can_access_case(user, case, active_role=active_role):
        raise PermissionDenied
    context = build_case_detail_context(case)
    context["documents"] = list(case.documents.all())
    return render(request, "doctor/case_detail.html", context)


@role_required("doctor", "admin")
@xframe_options_sameorigin
def case_pdf(
    request: HttpRequest,
    case_id: uuid.UUID,
    position: int,
) -> HttpResponse:
    """Serve o PDF original do documento na ``position`` (R4, D7).

    Mesmo guard do detalhe (papel ativo + matriz D2). Documento/position
    inexistente → 404; entrega via ``FileResponse`` (streaming) com
    content-type ``application/pdf`` e o nome original do upload.
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(
        Case.objects.only("case_id", "status"),
        case_id=case_id,
    )
    if not can_access_case(user, case, active_role=active_role):
        raise PermissionDenied
    document = get_object_or_404(
        CaseDocument.objects.only("pk", "case_id", "file", "original_filename", "position"),
        case=case,
        position=position,
    )
    return cast(
        HttpResponse,
        FileResponse(
            document.file.open("rb"),
            content_type=PDF_CONTENT_TYPE,
            filename=document.original_filename or "documento.pdf",
        ),
    )


def _render_decision_page(
    request: HttpRequest,
    case: Case,
    *,
    form: DoctorDecisionForm,
    lock_token: str = "",
    lock_error: str = "",
) -> HttpResponse:
    """Página de trabalho (D1): cards de leitura + formulário no fim.

    GET válido e POST inválido/perdido renderizam a MESMA página — o token da
    reserva volta ao formulário (``lock_token``) e ``lock_error`` explica a
    perda da posse (D2 item 5) sem nunca perder as demais entradas digitadas.
    """
    context = build_case_detail_context(case)
    context["documents"] = list(case.documents.all())
    context["form"] = form
    context["decision_fields"] = form.procedure_fields()
    context["lock_token"] = lock_token
    context["lock_error"] = lock_error
    return render(request, "doctor/decide.html", context)


@role_required("doctor", "admin")
def case_decide(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Decide o caso por procedimento declarado (R1–R4, D4/D6).

    GET: presenter (slice 003) + ``DoctorDecisionForm`` dinâmico — apenas em
    ``AWAITING_DOCTOR``; caso já decidido → redirect ao detalhe com mensagem.
    GET válido (``AWAITING_DOCTOR``): RESERVA o caso (claim com contexto
    ``doctor_decision`` e a lease longa do contexto — case-work-lock D2) e
    renderiza a página de trabalho (cards de leitura + formulário no fim, D1).
    Reserva ativa de outro médico → aviso com o portador + redirect à fila,
    nunca 500.
    POST: guard completo ANTES de qualquer escrita; o ``lock_token`` da reserva
    é obrigatório — a posse é validada SOB ROW LOCK na MESMA transação da
    escrita (``select_for_update`` + ``assert_case_lock``), e o release da
    reserva acontece logo após o serviço atômico do change 03 (rows + evento +
    transição FSM). Para a negativa total, o fechamento automático (resposta
    final ao NIR na thread, D1) → redirect ao detalhe com flash "decisão
    registrada". Como o serviço devolve ``None`` e trabalha sobre um ``locked``
    re-buscado, o caso em memória fica STALE: a view o re-lê
    (``refresh_from_db``) e chama ``post_doctor_denial_reply`` somente se o
    status for ``DOCTOR_DENIED``; decisão parcial → ``SCHEDULER_REQUESTED`` sem
    fechamento. Sem reserva vigente (token ausente/inválido/vencido) ou form
    inválido → re-render da página sem NENHUMA escrita; caso fora de
    ``AWAITING_DOCTOR``, perda da corrida concorrente (``TransitionNotAllowed``
    do serviço) ou falha do fechamento (``ValueError``) → mensagem + redirect
    ao detalhe — HTTP redirect, nunca 500.
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(Case, case_id=case_id)
    # Predicado único de acesso (papel ativo + matriz D2 por subtipo),
    # reforçado no POST — nunca só no template (R3).
    if not can_access_case(user, case, active_role=active_role):
        raise PermissionDenied

    detail_url = reverse("doctor:case_detail", args=[case.case_id])

    if request.method == "POST":
        raw_token = request.POST.get("lock_token", "")
        token = _lock_token_from_request(request)
        if not get_declared_procedure_types(case):
            # Defensivo (produção não deve chegar aqui: o intake declara >=1
            # tipo na criação): sem procedimentos declarados não há decisão a
            # registrar — redirect com mensagem, nunca 500 (R4).
            logger.warning(
                "doctor_decide_no_declared_procedures user=%s case=%s",
                user.pk,
                case.case_id,
            )
            messages.error(request, NO_DECLARED_PROCEDURES_MESSAGE)
            return redirect(detail_url)
        if case.status != CaseStatus.AWAITING_DOCTOR:
            # R4: estado inválido já no início do POST — mensagem + redirect,
            # sem construir form nem tocar o serviço (sem qualquer escrita).
            logger.warning(
                "doctor_decide_wrong_state user=%s case=%s status=%s",
                user.pk,
                case.case_id,
                case.status,
            )
            messages.error(request, ALREADY_DECIDED_RACE_MESSAGE)
            return redirect(detail_url)
        form = DoctorDecisionForm(request.POST, case=case)
        if token is None or not form.is_valid():
            # R6/D2: sem reserva vigente (token ausente/ilegível) o POST não
            # tem posse — nada persiste. Com posse mas form inválido, o token
            # volta ao formulário para o reenvio após corrigir os erros.
            return _render_decision_page(
                request,
                case,
                form=form,
                lock_token=raw_token,
                lock_error=LOCK_LOST_MESSAGE if token is None else "",
            )

        race = False
        try:
            with transaction.atomic():
                # D2 item 5: posse validada SOB ROW LOCK na MESMA transação da
                # escrita (time-of-check == time-of-use) — expiração/tomada da
                # lease entre a validação e a persistência é impossível.
                locked = Case.objects.select_for_update().get(pk=case.pk)
                assert_case_lock(locked, token)
                try:
                    record_doctor_procedure_decisions(
                        locked,
                        form.decisions(),
                        user=user,
                        role=active_role,
                    )
                except TransitionNotAllowed:
                    # D4: submit concorrente perdeu a corrida — o savepoint do
                    # serviço desfaz tudo dele (sem escrita parcial); a reserva
                    # fica com o portador para o retorno à fila.
                    race = True
                else:
                    # R7/D2: decisão registrada → release determinístico da
                    # reserva na MESMA transação.
                    release_case_lock(locked, token)
        except CaseLockConflictError:
            # R6/D2: token divergente/expirado ou caso sem reserva — a linha
            # não foi tocada em nenhum ponto.
            logger.warning("doctor_decide_lock_lost user=%s case=%s", user.pk, case.case_id)
            return _render_decision_page(request, case, form=form, lock_error=LOCK_LOST_MESSAGE)

        if race:
            logger.warning(
                "doctor_decide_race user=%s case=%s status=%s",
                user.pk,
                case.case_id,
                case.status,
            )
            messages.error(request, ALREADY_DECIDED_RACE_MESSAGE)
            return redirect(detail_url)

        # Fechamento da negativa total (nir-result-closure): o serviço do
        # change 03 devolve None e trabalha sobre um ``locked`` re-buscado —
        # o ``case`` em memória fica stale (e com os campos de lock antigos);
        # re-lemos o caso e publicamos a resposta final SOMENTE quando todos os
        # procedimentos foram negados (DOCTOR_DENIED). Decisão parcial (≥1
        # aprovado, em SCHEDULER_REQUESTED) segue o fluxo existente.
        case.refresh_from_db()
        if case.status == CaseStatus.DOCTOR_DENIED:
            try:
                post_doctor_denial_reply(case, user=user, role=active_role)
            except ValueError:
                # D1 (risco residual aceito): falha rara entre a decisão e
                # o fechamento deixa o caso em DOCTOR_DENIED, visível e
                # operável via admin — mensagem + redirect, nunca 500.
                logger.warning(
                    "doctor_decide_closure_error user=%s case=%s status=%s",
                    user.pk,
                    case.case_id,
                    case.status,
                )
                messages.error(request, DENIAL_REPLY_ERROR_MESSAGE)
                return redirect(detail_url)
        messages.success(request, DECISION_RECORDED_MESSAGE)
        return redirect(detail_url)

    if case.status != CaseStatus.AWAITING_DOCTOR:
        # R2: GET de caso já decidido → detalhe read-only com mensagem.
        messages.info(request, ALREADY_DECIDED_DETAIL_MESSAGE)
        return redirect(detail_url)
    # R4/D2: abrir a página de trabalho RESERVA o caso (claim atômico com a
    # lease longa do contexto). Reserva ativa de OUTRO médico barra a abertura
    # com aviso identificando o portador + volta à fila, sem tocar o lock dele;
    # o mesmo médico reabrindo renova a reserva com token novo.
    try:
        lock = claim_case_lock(
            case,
            user=user,
            context=DOCTOR_LOCK_CONTEXT,
            role=DOCTOR_LOCK_ROLE,
        )
    except CaseLockConflictError:
        case.refresh_from_db()
        holder = case.locked_by.display_name if case.locked_by is not None else "outro médico"
        logger.warning("doctor_decide_reserved user=%s case=%s", user.pk, case.case_id)
        messages.warning(request, LOCK_RESERVED_MESSAGE.format(holder=holder))
        return redirect(reverse("doctor:queue"))

    form = DoctorDecisionForm(case=case)
    return _render_decision_page(request, case, form=form, lock_token=str(lock.token))


@role_required("doctor", "admin")
@require_POST
def lock_renew(request: HttpRequest, case_id: uuid.UUID) -> JsonResponse:
    """POST: renova a reserva do caso (heartbeat da página de trabalho, D3).

    Requer ``lock_token`` no corpo. Nunca 500: token ausente/ilegível ou sem
    posse vigente → JSON ``success=false`` com a mensagem ao usuário (o JS
    exibe o aviso e desabilita o submit; a reabertura pela fila refaz a
    reserva). Posse válida → lease renovada pela duração do CONTEXTO do lock
    vigente (1h no ``doctor_decision``), com ``locked_until`` devolvido em ISO
    e evento ``CASE_LOCK_RENEWED`` na trilha.
    """
    case = _lock_endpoint_case(request, case_id)
    token = _lock_token_from_request(request)
    if token is None:
        return JsonResponse({"success": False, "error": LOCK_TOKEN_MISSING_MESSAGE})
    try:
        lock = renew_case_lock(case, token)
    except CaseLockConflictError:
        logger.warning("doctor_lock_renew_lost case=%s", case.case_id)
        return JsonResponse({"success": False, "error": LOCK_LOST_MESSAGE})
    return JsonResponse({"success": True, "locked_until": lock.locked_until.isoformat()})


@role_required("doctor", "admin")
@require_POST
def lock_release(request: HttpRequest, case_id: uuid.UUID) -> JsonResponse:
    """POST: libera a reserva do caso (release best-effort do JS, D3).

    Requer ``lock_token`` no corpo. Idempotente por contrato: sem posse vigente
    (reserva já liberada pela decisão/saída, expirada ou token de outra aba)
    responde ``success=false`` — nada a liberar, nunca erro. Posse válida →
    ``CASE_LOCK_RELEASED`` na trilha.
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


@role_required("doctor", "admin")
@require_POST
def case_leave(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """POST: «Sair sem decidir» — libera a reserva na hora e volta à fila (R3/D3).

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
    return redirect(reverse("doctor:queue"))
