"""Views do intake do NIR (change intake-nir-upload, slices 001 e 004).

Slice 001 (R4/R5): ``intake_home`` é a tela de envio do relatório (multi-PDF +
declaração de tipos): GET renderiza o form; POST valida tudo via
``apps.intake.services.create_case_with_documents`` — erro de validação
re-renderiza o form com resumo nomeando arquivo/tipo; sucesso redireciona ao
detalhe do caso criado (slice 004, R5).

Slice 004 (R1–R3): ``my_cases`` lista os casos do criador (sem filtros —
change 11); ``case_detail`` exibe documentos/trilha/comunicações apenas do
caso do próprio NIR (alheio → 404, sem vazamento — divergência deliberada do
ats-web, onde o detalhe é visível a qualquer NIR operacional; design D6/D9);
``serve_document`` entrega o PDF por id interno (sem path traversal) com
content-type ``application/pdf`` e filename original. Ações do gate
(liberar/reenviar) são o slice 005.
"""

import logging
import uuid
from typing import Any, cast

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.decorators import role_required
from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseDocument
from apps.cases.procedure_catalog import PROCEDURE_PROFILES

from .forms import IntakeUploadForm
from .services import PDF_CONTENT_TYPE, create_case_with_documents

logger = logging.getLogger(__name__)

# Label legível por tipo canônico de evento (trilha do detalhe); o tipo é a
# chave de fallback quando o evento ainda não tem entrada canônica (R2).
_EVENT_TYPE_LABELS: dict[str, str] = {
    event_type: label for event_type, label in CaseEventType.choices
}
# Labels do catálogo por tipo, na ordem canônica do registro (R1/R2).
_PROCEDURE_LABELS_BY_TYPE: dict[str, str] = {
    profile.procedure_type: profile.label for profile in PROCEDURE_PROFILES
}


def _require_user(request: HttpRequest) -> User:
    """Usuário autenticado de uma view já protegida por ``@role_required``."""
    user = request.user
    if not isinstance(user, User):
        raise PermissionDenied
    return user


def _procedure_labels(declared_types: list[str]) -> list[str]:
    """Labels legíveis dos tipos declarados, na ordem do catálogo (R1/R2)."""
    declared = set(declared_types)
    return [
        label
        for procedure_type, label in _PROCEDURE_LABELS_BY_TYPE.items()
        if procedure_type in declared
    ]


def _declared_types_from_rows(case: Case) -> list[str]:
    """Tipos declarados do caso a partir das rows já carregadas do prefetch.

    Equivale ao leitor de ``apps.cases.procedures`` aplicado sobre o manager
    ``procedures`` pré-carregado (sem query extra por caso); a ordem de
    exibição é resolvida por ``_procedure_labels``.
    """
    return [row.procedure_type for row in case.procedures.all() if row.declared_by_nir]


def _summarize_payload(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Resumo exibível do payload do evento (R2): pares chave → valor enxuto.

    Valores aninhados viram sua representação textual truncada — a trilha do
    detalhe nunca precisa do payload bruto completo.
    """

    def _render(value: object) -> str:
        text = str(value)
        return text if len(text) <= 160 else f"{text[:157]}..."

    return [(key, _render(value)) for key, value in payload.items()]


@role_required("nir")
def intake_home(request: HttpRequest) -> HttpResponse:
    """Home do intake NIR: form de envio do relatório com tipos declarados."""
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    form = IntakeUploadForm()

    if request.method == "POST":
        form = IntakeUploadForm(request.POST, request.FILES)
        if form.is_valid():
            documents = form.cleaned_data["documents"]
            procedure_types = form.cleaned_data["procedure_types"]
            try:
                case = create_case_with_documents(
                    user=user,
                    role=active_role,
                    files=documents,
                    procedure_types=procedure_types,
                )
            except ValueError as exc:
                logger.warning("intake_upload_rejected user=%s motivo=%s", user.pk, exc)
                form.add_error(None, str(exc))
            else:
                messages.success(
                    request,
                    f"Caso {case.case_id} criado com sucesso.",
                )
                # R5 (slice 004): o POST do formulário leva ao detalhe do caso.
                return redirect(reverse("intake:case_detail", args=[case.case_id]))

    return render(request, "intake/home.html", {"form": form})


@role_required("nir")
def my_cases(request: HttpRequest) -> HttpResponse:
    """Lista "meus casos": apenas os criados pelo NIR logado (R1, sem filtros).

    Ordenação ``created_at desc``; cada item leva o caso, o label legível do
    status, os tipos declarados (labels do catálogo) — badges de
    retenção/FAILED são decididos no template pelos campos
    ``manual_review_required``/``status``.
    """
    user = _require_user(request)
    cases = (
        Case.objects.filter(created_by=user).prefetch_related("procedures").order_by("-created_at")
    )
    items = []
    for case in cases:
        items.append(
            {
                "case": case,
                "status_label": case.get_status_display(),
                "procedure_labels": _procedure_labels(_declared_types_from_rows(case)),
                "agency_record_number": case.agency_record_number or "—",
            }
        )
    return render(request, "intake/my_cases.html", {"items": items})


@role_required("nir")
def case_detail(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Detalhe do caso do próprio NIR: docs, trilha e comunicações (R2).

    O queryset filtra ``created_by=user`` — caso alheio (ou inexistente)
    responde 404 sem vazar informação (D6/D9). Exibe status/timestamps, tipos
    declarados, flag de retenção com motivo, documentos com link de abertura
    (``serve_document``), trilha de eventos (tipo, ator+papel, timestamp,
    payload resumido) e a thread de comunicações do change 03.
    """
    user = _require_user(request)
    case = get_object_or_404(
        Case.objects.select_related("created_by").prefetch_related("procedures"),
        case_id=case_id,
        created_by=user,
    )
    documents = list(case.documents.all())
    events = list(case.events.select_related("actor"))
    communications = list(case.communication_messages.select_related("author"))

    enriched_events = []
    for event in events:
        enriched_events.append(
            {
                "event_type": event.event_type,
                "label": _EVENT_TYPE_LABELS.get(event.event_type, event.event_type),
                "actor_display": event.actor.display_name if event.actor else "Sistema",
                "actor_role": event.actor_role,
                "timestamp": event.timestamp,
                "payload_summary": _summarize_payload(event.payload),
            }
        )

    context = {
        "case": case,
        "status_label": case.get_status_display(),
        "documents": documents,
        "procedure_labels": _procedure_labels(_declared_types_from_rows(case)),
        "events": enriched_events,
        "communications": communications,
    }
    return render(request, "intake/case_detail.html", context)


@role_required("nir")
def serve_document(
    request: HttpRequest,
    case_id: uuid.UUID,
    document_id: int,
) -> HttpResponse:
    """Serve o PDF do documento do caso do próprio NIR (R3).

    Resolve caso e documento por ids internos (``case_id`` UUID + pk do
    ``CaseDocument``) — o nome/path armazenado jamais vem do cliente (sem path
    traversal). Caso alheio, documento de outro caso ou inexistente → 404.
    Entrega via ``FileResponse`` com content-type ``application/pdf`` e
    filename original do upload.
    """
    user = _require_user(request)
    case = get_object_or_404(
        Case.objects.only("case_id", "created_by"),
        case_id=case_id,
        created_by=user,
    )
    document = get_object_or_404(
        CaseDocument.objects.only("pk", "case_id", "file", "original_filename"),
        case=case,
        pk=document_id,
    )
    return cast(
        HttpResponse,
        FileResponse(
            document.file.open("rb"),
            content_type=PDF_CONTENT_TYPE,
            filename=document.original_filename or "documento.pdf",
        ),
    )
