"""Views do intake do NIR (change intake-nir-upload, slices 001 e 004).

Slice 001 (R4/R5): ``intake_home`` é a tela de envio do relatório (multi-PDF +
declaração de tipos): GET renderiza o form; POST valida tudo via
``apps.intake.services.create_case_with_documents`` — erro de validação
re-renderiza o form com resumo nomeando arquivo/tipo; sucesso redireciona ao
detalhe do caso criado (slice 004, R5).

Slice 002 do change intake-batch-semantics (R1–R4/D4): ``intake_home`` envia o
lote pelo serviço ``submit_report_batch`` (1 PDF = 1 caso, tipo único em radio
validado pelo form antes do serviço) e despacha o contrato redirect ×
resultado — 1 caso com ZERO erros segue redirecionando ao detalhe do caso;
lote e/ou erros renderizam a página de resultado com a contagem de casos
criados e os erros por arquivo (substituindo a ponte provisória que
redirecionava o lote a ``my_cases`` e re-renderizava o alerta enganoso).

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

Slice 003 do nir-result-closure (R1–R4): ``case_detail`` ganha a seção de
resultado quando o caso passou da decisão médica (decisões por procedimento
com motivo real, dados de agendamento quando houver e resposta final em
destaque na thread); ``case_ack`` (POST novo) confirma o recebimento no estado
``FINAL_REPLY_POSTED`` do próprio criador via ``acknowledge_case_receipt``;
``my_cases`` ganha as abas **ativos** (default, tudo exceto ``CLEANED``) e
**encerrados** (apenas ``CLEANED``), com ``?tab=`` e fallback seguro.

"""

import logging
import uuid
from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.http import require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import User
from apps.cases.closure import acknowledge_case_receipt
from apps.cases.events import CaseEventType
from apps.cases.locks import CaseLockConflictError
from apps.cases.models import Case, CaseDocument, CaseStatus, DoctorDisposition
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.cases.units import unit_label

from .forms import CorrectedResubmissionForm, IntakeUploadForm
from .services import (
    INTAKE_DISABLED_MESSAGE,
    PDF_CONTENT_TYPE,
    CaseNotRetainedError,
    IntakeValidationError,
    create_corrected_resubmission,
    release_retained_case,
    resubmit_case_documents,
    submit_report_batch,
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

# ── Resultado/ciência/encerrados (nir-result-closure, slice 003) ──────────

# Disposições que configuram decisão médica registrada (R1): a seção de
# resultado existe quando há rows decididas (approved/denied).
_DECIDED_DISPOSITIONS = frozenset({DoctorDisposition.APPROVED, DoctorDisposition.DENIED})
# Rótulo legível por disposição (dados de ``DoctorDisposition``, mesmas choices
# dos demais apresentadores).
_DISPOSITION_LABELS: dict[str, str] = dict(DoctorDisposition.choices)
# Estados em que a resposta final do fechamento/agendamento já foi publicada
# na thread (R1): o destaque é a última comunicação autoral.
_FINAL_REPLY_STATES = frozenset(
    {
        CaseStatus.FINAL_REPLY_POSTED,
        CaseStatus.AWAITING_NIR_ACK,
        CaseStatus.CLEANING,
        CaseStatus.CLEANED,
    }
)
# Formato de exibição da data/hora do agendamento (fuso local da aplicação).
_SCHEDULED_AT_FORMAT = "%d/%m/%Y %H:%M"

# Abas de "meus casos" (R3): ``active`` é a default; ``closed`` lista apenas
# casos ``CLEANED``. Valor desconhecido de ``?tab=`` cai na default (seguro).
_MY_CASES_TABS = frozenset({"active", "closed"})
_DEFAULT_MY_CASES_TAB = "active"


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


def _procedure_decisions(case: Case) -> list[dict[str, str]]:
    """Decisões médicas por procedimento do resultado (R1, D3).

    Itens das rows com disposição registrada (``approved``/``denied``), na
    ordem canônica do catálogo — label legível + disposição + motivo real. As
    rows ``CaseProcedure`` com decisões/motivos são preservadas pela limpeza
    do ack (D2), então o resultado permanece exibível no caso ``CLEANED``.
    """
    rows_by_type = {row.procedure_type: row for row in case.procedures.all()}
    decisions: list[dict[str, str]] = []
    for procedure_type, label in _PROCEDURE_LABELS_BY_TYPE.items():
        row = rows_by_type.get(procedure_type)
        if row is None or row.doctor_disposition not in _DECIDED_DISPOSITIONS:
            continue
        disposition = row.doctor_disposition
        decisions.append(
            {
                "label": label,
                "disposition": disposition,
                "disposition_label": _DISPOSITION_LABELS.get(disposition, disposition),
                "reason": row.doctor_reason,
            }
        )
    return decisions


def _scheduling_context(case: Case) -> dict[str, object]:
    """Dados de agendamento do resultado (R1, D3): unidade/data/local.

    Expõe o que existe nos campos ``scheduled_*`` decididos pelo agendador
    (confirmação: unidade + data/hora local + local; negação: motivo) — o
    template decide a apresentação por ``has_scheduling``.
    """
    scheduled_at = ""
    if case.scheduled_datetime is not None:
        scheduled_at = timezone.localtime(case.scheduled_datetime).strftime(_SCHEDULED_AT_FORMAT)
    unit = case.scheduled_unit
    unit_label_text = unit_label(unit) if unit is not None else ""
    denial_reason = case.scheduling_denial_reason
    return {
        "unit_label": unit_label_text,
        "scheduled_at": scheduled_at,
        "location": case.scheduled_location,
        "denial_reason": denial_reason,
        "has_scheduling": bool(case.scheduled_unit is not None or scheduled_at or denial_reason),
    }


@role_required("nir")
def intake_home(request: HttpRequest) -> HttpResponse:
    """Home do intake NIR: envio em lote, tipo único e resultado do envio.

    GET renderiza o form (1–N PDFs, tipo único em radio, anexos com a regra do
    relatório único). POST com o form válido chama ``submit_report_batch``
    (contrato de lote do slice 001: ``(cases, errors)`` com falhas parciais por
    arquivo — tipo/lote inválidos já viram erro de FORMULÁRIO em R1, sem
    chegar ao serviço) e despacha o contrato redirect × resultado (R4/D4):

    - exatamente 1 caso criado e ZERO erros → redirect ao detalhe do caso
      (comportamento preservado desde o slice 004 do change original);
    - lote (N>1) e/ou erros → página de resultado com "N casos criados" (link
      Meus casos) + um item por arquivo rejeitado (nome + motivo).
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    form = IntakeUploadForm()

    if request.method == "POST":
        # Intake desligado (D7, slice 002 do pilot-deployment): informa sem
        # efeito — o guard do serviço também bloqueia, mas aqui a resposta é
        # amigável (flash + redirect) em vez de erro de validação.
        if not settings.INTAKE_ENABLED:
            messages.error(request, INTAKE_DISABLED_MESSAGE)
            return redirect(reverse("intake:home"))
        form = IntakeUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                cases, errors = submit_report_batch(
                    user=user,
                    role=active_role,
                    files=form.cleaned_data["documents"],
                    procedure_type=form.cleaned_data["procedure_type"],
                    attachments=form.cleaned_data["attachments"],
                )
            except IntakeValidationError as exc:
                # Único erro levantado pelo serviço fora do contrato de lote:
                # o guard fail-closed do intake (D1).
                logger.warning("intake_upload_rejected user=%s motivo=%s", user.pk, exc)
                form.add_error(None, str(exc))
            else:
                if not errors and len(cases) == 1:
                    messages.success(request, f"Caso {cases[0].case_id} criado com sucesso.")
                    return redirect(reverse("intake:case_detail", args=[cases[0].case_id]))
                return render(
                    request,
                    "intake/home.html",
                    {
                        "form": IntakeUploadForm(),
                        "result": {
                            "cases": cases,
                            "errors": errors,
                            "created_count": len(cases),
                        },
                    },
                )

    return render(request, "intake/home.html", {"form": form})


@role_required("nir")
def my_cases(request: HttpRequest) -> HttpResponse:
    """Lista "meus casos" com abas ativos/encerrados (R3, escopo por criador).

    A aba **ativos** (default de ``?tab=``, com fallback seguro para valores
    desconhecidos) lista os casos do criador em tudo exceto ``CLEANED``; a
    aba **encerrados** lista apenas os ``CLEANED`` — mesmo formato de items,
    badges e ordenação (``created_at desc``). ``?tab=`` desconhecido/ausente
    nunca vaza nem quebra: cai em ativos.
    """
    user = _require_user(request)
    requested_tab = request.GET.get("tab", _DEFAULT_MY_CASES_TAB)
    current_tab = requested_tab if requested_tab in _MY_CASES_TABS else _DEFAULT_MY_CASES_TAB

    own_cases = Case.objects.filter(created_by=user)
    active_cases = own_cases.exclude(status=CaseStatus.CLEANED)
    closed_cases = own_cases.filter(status=CaseStatus.CLEANED)
    selected_cases = closed_cases if current_tab == "closed" else active_cases

    items = []
    for case in selected_cases.prefetch_related("procedures").order_by("-created_at"):
        items.append(
            {
                "case": case,
                "status_label": case.get_status_display(),
                "procedure_labels": _procedure_labels(_declared_types_from_rows(case)),
                "agency_record_number": case.agency_record_number or "—",
            }
        )
    return render(
        request,
        "intake/my_cases.html",
        {
            "items": items,
            "current_tab": current_tab,
            "active_count": active_cases.count(),
            "closed_count": closed_cases.count(),
        },
    )


@role_required("nir")
def case_detail(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Detalhe do caso do próprio NIR: docs, trilha, comunicações e resultado.

    O queryset filtra ``created_by=user`` — caso alheio (ou inexistente)
    responde 404 sem vazar informação (D6/D9). Exibe status/timestamps, tipos
    declarados, flag de retenção com motivo, documentos com link de abertura
    (``serve_document``), trilha de eventos (tipo, ator+papel, timestamp,
    payload resumido) e a thread de comunicações do change 03. Quando o caso
    passou da decisão médica (slice 003, R1/D3), ganha também a seção de
    resultado: decisões por procedimento (label + disposição + motivo real),
    dados de agendamento (unidade/data/local quando houver) e a resposta final
    em destaque na thread — além do botão de ciência apenas no estado
    ``FINAL_REPLY_POSTED`` (``can_ack``), escopado ao criador pelo próprio
    queryset.
    """
    user = _require_user(request)
    case = get_object_or_404(
        Case.objects.select_related("created_by").prefetch_related("procedures"),
        case_id=case_id,
        created_by=user,
    )
    documents = list(case.documents.all())
    attachments = list(case.attachments.all())
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

    procedure_decisions = _procedure_decisions(case)
    # Resposta final em destaque (R1): a última comunicação autoral da thread
    # quando o fechamento/agendamento já publicou a resposta final. Mensagens
    # ``system`` (sem autor) nunca são a resposta final.
    final_reply = None
    if case.status in _FINAL_REPLY_STATES:
        final_reply = next((m for m in reversed(communications) if m.author is not None), None)

    # Reenvios corrigidos deste caso (D4/R4): os novos casos que corrigem o
    # original (reverso ``corrected_by``), ordenados por criação.
    corrections = list(case.corrected_by.order_by("created_at", "pk"))

    context = {
        "case": case,
        "status_label": case.get_status_display(),
        "documents": documents,
        "attachments": attachments,
        "procedure_labels": _procedure_labels(_declared_types_from_rows(case)),
        "events": enriched_events,
        "communications": communications,
        "has_outcome": bool(procedure_decisions),
        "procedure_decisions": procedure_decisions,
        "scheduling": _scheduling_context(case),
        "final_reply": final_reply,
        "can_ack": case.status == CaseStatus.FINAL_REPLY_POSTED,
        "can_resubmit": case.status == CaseStatus.CLEANED,
        "corrections": corrections,
    }
    return render(request, "intake/case_detail.html", context)


@role_required("nir")
@require_POST
def case_ack(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Confirma o recebimento da resposta final e encerra o caso (R2/D1).

    POST escopado ao caso do próprio NIR — caso alheio/inexistente → 404
    (``created_by`` no queryset; sem vazar informação). Delega ao serviço
    transacional ``acknowledge_case_receipt`` com o papel ativo da sessão:
    sucesso → flash + redirect ao detalhe (caso ``CLEANED``); estado fora de
    ``FINAL_REPLY_POSTED`` → mensagem de erro + redirect ao detalhe, nunca 500.
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(
        Case.objects.only("pk", "case_id", "created_by_id"),
        case_id=case_id,
        created_by=user,
    )
    detail_url = reverse("intake:case_detail", args=[case.case_id])
    try:
        acknowledge_case_receipt(case=case, user=user, role=active_role)
    except ValueError as exc:
        logger.warning("case_ack_rejected user=%s case=%s motivo=%s", user.pk, case.case_id, exc)
        messages.error(request, str(exc))
        return redirect(detail_url)
    logger.info("case_ack_ok user=%s case=%s", user.pk, case.case_id)
    messages.success(
        request,
        f"Recebimento confirmado — o caso {case.case_id} foi encerrado e os "
        "dados sensíveis foram removidos.",
    )
    return redirect(detail_url)


@role_required("nir")
def case_resubmit(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Form de reenvio corrigido de um caso encerrado do próprio NIR (R4/D4).

    GET/POST escopados ao caso ``CLEANED`` do próprio criador: caso alheio ou
    inexistente → 404 (``created_by`` no queryset, sem vazar informação); caso
    do criador fora de ``CLEANED`` → mensagem + redirect ao detalhe, nunca
    500. O form reúne arquivos + tipos declarados do catálogo + motivo; o POST
    delega ao serviço transacional ``create_corrected_resubmission`` — sucesso
    → redirect ao detalhe do NOVO caso com flash; erro de validação (motivo,
    arquivo ou tipo) re-renderiza o form com o resumo nomeando o problema.
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(
        Case.objects.only(
            "pk",
            "case_id",
            "created_by",
            "status",
            "agency_record_number",
        ),
        case_id=case_id,
        created_by=user,
    )
    detail_url = reverse("intake:case_detail", args=[case.case_id])
    # Intake desligado (D7; P2 review: checado ANTES do estado, para a
    # mensagem de "desabilitado" prevalecer sobre a de estado em qualquer
    # caso) — bloqueado sem efeito.
    if not settings.INTAKE_ENABLED:
        messages.error(request, INTAKE_DISABLED_MESSAGE)
        return redirect(detail_url)
    if case.status != CaseStatus.CLEANED:
        messages.error(
            request,
            "Reenvio corrigido disponível apenas para casos encerrados (CLEANED).",
        )
        return redirect(detail_url)

    form = CorrectedResubmissionForm()
    if request.method == "POST":
        form = CorrectedResubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            documents = form.cleaned_data["documents"]
            attachments = form.cleaned_data["attachments"]
            procedure_type = form.cleaned_data["procedure_type"]
            correction_reason = form.cleaned_data["correction_reason"]
            # O reenvio corrigido aceita exatamente 1 PDF (serviço, D5); o
            # campo de arquivos do form ainda é múltiplo até o slice 003.
            if len(documents) != 1:
                form.add_error(None, "O reenvio corrigido aceita exatamente 1 PDF do relatório.")
            else:
                try:
                    new_case = create_corrected_resubmission(
                        original_case=case,
                        user=user,
                        role=active_role,
                        file=documents[0],
                        procedure_type=procedure_type,
                        correction_reason=correction_reason,
                        attachments=attachments,
                    )
                except ValueError as exc:
                    logger.warning(
                        "case_resubmit_rejected user=%s case=%s motivo=%s",
                        user.pk,
                        case.case_id,
                        exc,
                    )
                    form.add_error(None, str(exc))
                else:
                    messages.success(
                        request,
                        f"Reenvio corrigido criado — acompanhe o novo caso {new_case.case_id}.",
                    )
                    return redirect(reverse("intake:case_detail", args=[new_case.case_id]))

    return render(
        request,
        "intake/corrected_resubmission.html",
        {"form": form, "case": case},
    )


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
    # Intake desligado (D7): bloqueia o reenvio do gate antes do serviço — a
    # resposta volta ao detalhe (302), nunca 4xx/500 nem efeito colateral.
    if not settings.INTAKE_ENABLED:
        messages.error(request, INTAKE_DISABLED_MESSAGE)
        return redirect(reverse("intake:case_detail", args=[case.case_id]))
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
