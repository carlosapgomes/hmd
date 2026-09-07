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
content-type ``application/pdf`` e filename original.

Slice 005 (R1–R3, D6/D7): ``gate_release``/``gate_resubmit`` fecham o ciclo do
gate no detalhe de um caso retido — liberar avança a ``ANONYMIZING`` com
``CASE_GATE_BYPASSED``; reenviar substitui os PDFs e reprocessa. Ambas são
POST restritos a caso retido do próprio criador: caso alheio → 404 (escopo
por criador); estado fora da retenção → 400; lock ativo do worker → 409.

"""

import logging
import uuid
from typing import Any, cast

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import escape
from django.views.decorators.http import require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.locks import CaseLockConflictError
from apps.cases.models import Case, CaseDocument, CaseStatus
from apps.cases.procedure_catalog import PROCEDURE_PROFILES

from .forms import IntakeUploadForm
from .services import (
    PDF_CONTENT_TYPE,
    CaseNotRetainedError,
    IntakeValidationError,
    create_case_with_documents,
    release_retained_case,
    resubmit_case_documents,
)

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


# ── Ações de revisão do gate NIR (slice 005, D6/D7) ────────────────────────


def _gate_action_error(message: str, case_id: uuid.UUID, *, status: int) -> HttpResponse:
    """Resposta 4xx das ações do gate com a mensagem e o vínculo de volta ao caso.

    POST de ação sobre caso fora da retenção (400) ou sob lock ativo (409) não
    redireciona (sem efeito colateral — R3); o corpo é uma página mínima de erro
    sem depender de template novo fora do escopo do slice.
    """
    detail_url = reverse("intake:case_detail", args=[case_id])
    body = (
        '<!doctype html><html lang="pt-br"><head><meta charset="utf-8">'
        "<title>Revisão do gate</title></head><body>"
        f"<p>{escape(message)}</p>"
        f'<p><a href="{escape(detail_url)}">Voltar ao caso</a></p>'
        "</body></html>"
    )
    return HttpResponse(body, status=status)


@role_required("nir")
@require_POST
def gate_release(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Libera caso retido: despacho pela (estado, razão) da retenção (R7).

    POST escopado ao caso do próprio NIR (alheio/inexistente → 404). O efeito
    roda no serviço transacional ``release_retained_case``: retenção de
    FORMATO avança a ANONYMIZING (comportamento atual) e retenção por
    divergência (``LLM_EXTRACTING`` + ``procedure_divergence``) avança a
    LLM_SUMMARIZING via bypass — ambos com ``CASE_GATE_BYPASSED`` e flags
    zeradas. Sucesso → redirect ao detalhe com mensagem do caminho; caso fora
    das retenções → 400; lock ativo (worker reprocessando) → 409.
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(
        Case.objects.only("pk", "case_id", "created_by_id"),
        case_id=case_id,
        created_by=user,
    )
    try:
        target = release_retained_case(case=case, user=user, role=active_role)
    except CaseNotRetainedError as exc:
        logger.warning("gate_release_rejected user=%s case=%s motivo=%s", user.pk, case_id, exc)
        return _gate_action_error(str(exc), case_id, status=400)
    except CaseLockConflictError as exc:
        logger.warning("gate_release_locked user=%s case=%s motivo=%s", user.pk, case_id, exc)
        return _gate_action_error(str(exc), case_id, status=409)
    if target == CaseStatus.LLM_SUMMARIZING:
        messages.success(
            request,
            f"Caso {case.case_id} liberado — a divergência foi dispensada e o caso "
            "avançou para a sumarização.",
        )
    else:
        messages.success(
            request,
            f"Caso {case.case_id} liberado — o gate foi dispensado e o caso avançou para anonimização.",
        )
    return redirect(reverse("intake:case_detail", args=[case.case_id]))


@role_required("nir")
@require_POST
def gate_resubmit(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Reenvia documentos de um caso retido e reprocessa do zero (R2).

    POST escopado ao caso do próprio NIR (alheio/inexistente → 404). Recebe os
    novos PDFs no campo ``documents`` e delega ao serviço transacional
    ``resubmit_case_documents`` (mesma validação do slice 001, substituição de
    documentos, zeragem de flag/texto/nº e reenfileiramento). Sucesso → redirect
    ao detalhe com mensagem; arquivo inválido/caso fora da retenção → 400;
    lock ativo (worker reprocessando) → 409.
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(
        Case.objects.only("pk", "case_id", "created_by_id"),
        case_id=case_id,
        created_by=user,
    )
    documents = request.FILES.getlist("documents")
    try:
        resubmit_case_documents(
            case=case,
            user=user,
            role=active_role,
            files=documents,
        )
    except IntakeValidationError as exc:
        logger.warning("gate_resubmit_invalid user=%s case=%s motivo=%s", user.pk, case_id, exc)
        return _gate_action_error(str(exc), case_id, status=400)
    except CaseNotRetainedError as exc:
        logger.warning("gate_resubmit_rejected user=%s case=%s motivo=%s", user.pk, case_id, exc)
        return _gate_action_error(str(exc), case_id, status=400)
    except CaseLockConflictError as exc:
        logger.warning("gate_resubmit_locked user=%s case=%s motivo=%s", user.pk, case_id, exc)
        return _gate_action_error(str(exc), case_id, status=409)
    messages.success(
        request,
        f"Caso {case.case_id} reenviado — documentos substituídos e reprocessamento iniciado.",
    )
    return redirect(reverse("intake:case_detail", args=[case.case_id]))
