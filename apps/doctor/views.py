"""Views da fila e do detalhe do caso médico (doctor-queue-decision, slices 002/003).

Slice 002 (R2–R5, D2/D5): fila única em ``/doctor/`` para papel ativo
doctor/admin — abas por estado (``aguardando`` default = ``AWAITING_DOCTOR``;
``decididos`` = estados pós-decisão), FIFO por ``created_at``, filtro de
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
"""

from __future__ import annotations

import uuid
from typing import cast
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.accounts.decorators import role_required
from apps.accounts.models import User
from apps.cases.models import Case, CaseDocument, CaseStatus
from apps.cases.procedure_catalog import PROCEDURE_PROFILES, VALID_DOCTOR_SUBTYPES

from .access import DoctorAccess, can_access_case
from .presenters import build_case_detail_context

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

# Perfil do catálogo por tipo (ordem/rotulo/subtipo dos cards da fila).
_PROFILE_BY_TYPE = {profile.procedure_type: profile for profile in PROCEDURE_PROFILES}
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
    return cases.order_by("created_at", "case_id").prefetch_related("procedures")


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


def _queue_item(case: Case) -> dict[str, object]:
    """Item de card da fila: campos de exibição + tipos declarados por subtipo.

    Os tipos saem das rows já pré-carregadas (``prefetch_related``),
    declaradas pelo NIR, na ordem canônica do catálogo; cada tipo leva o label
    e o ``doctor_subtipo`` (badge no template).
    """
    rows = [row for row in case.procedures.all() if row.declared_by_nir]
    rows.sort(key=lambda row: _CATALOG_INDEX[row.procedure_type])
    return {
        "case_id": case.case_id,
        "status_label": case.get_status_display(),
        "patient_name": case.patient_name or "—",
        "agency_record_number": case.agency_record_number or "—",
        "created_at": case.created_at,
        "procedures": [
            {
                "label": _PROFILE_BY_TYPE[row.procedure_type].label,
                "subtype": _PROFILE_BY_TYPE[row.procedure_type].doctor_subtipo,
            }
            for row in rows
        ],
    }


@role_required("doctor", "admin")
def queue(request: HttpRequest) -> HttpResponse:
    """Fila médica por estado (abas) com filtro de subtipo (R2–R5, D2/D5)."""
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
    page = Paginator(cases, QUEUE_PAGE_SIZE).get_page(request.GET.get("page", "1"))
    # Predicado único re-aplicado por caso da página como invariante do filtro
    # SQL — tipos declarados derivados das rows já pré-carregadas, sem query
    # por card (P2).
    items = [
        _queue_item(case)
        for case in page.object_list
        if access.allows_declared_types(_declared_types_of_case(case))
    ]
    pending_counts = _pending_counts_by_subtype(access, subtype_options)

    filter_parts: list[tuple[str, str]] = [("tab", tab)]
    if subtype:
        filter_parts.append(("subtype", subtype))

    context = {
        "tab": tab,
        "subtype": subtype,
        "subtype_options": subtype_options,
        "pending_counts": pending_counts,
        "page_obj": page,
        "items": items,
        "filter_qs": urlencode(filter_parts),
    }
    return render(request, "doctor/queue.html", context)


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
