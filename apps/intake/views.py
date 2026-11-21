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
from collections import Counter
from typing import cast
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import User
from apps.attachments.models import CaseAttachment
from apps.cases.closure import ADMINISTRATIVE_CLOSURE_REASONS, acknowledge_case_receipt
from apps.cases.event_trail import build_event_trail_context
from apps.cases.events import CaseEventType
from apps.cases.locks import CaseLockConflictError
from apps.cases.models import (
    PROCEDURE_DIVERGENCE_REASON,
    Case,
    CaseDocument,
    CaseStatus,
    DetectionStatus,
    DoctorDisposition,
)
from apps.cases.navigation import resolve_safe_next_url
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.cases.procedure_review import (
    has_procedure_review_marker,
    latest_review_event,
    source_fingerprint_for_case,
)
from apps.cases.procedures import get_declared_procedure_types
from apps.cases.units import unit_label
from apps.pipeline.tasks import ProcedureReviewEnqueueAfterCommitError

from .forms import CorrectedResubmissionForm, IntakeUploadForm, ProcedureReviewForm
from .services import (
    INTAKE_DISABLED_MESSAGE,
    PDF_CONTENT_TYPE,
    CaseNotRetainedError,
    IntakeValidationError,
    confirm_case_procedure_review,
    create_corrected_resubmission,
    release_retained_case,
    resubmit_case_documents,
    submit_report_batch,
)

logger = logging.getLogger(__name__)

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

# Filtros server-side de "meus casos" (ui-parity-queues slice 003, R2/D4):
# ``q`` casa o nº de ocorrência com mínimo de 3 caracteres (espelho do
# painel — ``MIN_SEARCH_LENGTH`` de ``apps.dashboard.views``); mais curtos
# são ignorados (nunca zeram a lista por erro). ``status`` aceita apenas
# valores do enum ``CaseStatus`` (desconhecido é ignorado, como ``?tab=``);
# ``procedure_type`` aceita apenas tipos do catálogo de 13 perfis (os mesmos
# valores do ``IntakeUploadForm.procedure_type``). Composição por AND, dentro
# do escopo por criador, antes dos sufixos de desambiguação (D4).
_MY_CASES_MIN_SEARCH_LENGTH = 3
_MY_CASES_STATUS_VALUES = frozenset(status.value for status in CaseStatus)
_MY_CASES_PROCEDURE_TYPE_VALUES = frozenset(_PROCEDURE_LABELS_BY_TYPE)
# Opções do filtro de tipo: (valor, rótulo) na ordem canônica do catálogo.
_MY_CASES_PROCEDURE_TYPE_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    _PROCEDURE_LABELS_BY_TYPE.items()
)

# Resultado do encerramento administrativo no card de "meus casos" (R3/D4):
# prefixo fixo + rótulo legível do catálogo de motivos de ``apps/cases/closure.py``
# (fonte única). O texto livre do motivo vem do supervisor autor do encerramento e
# é exibido ao CRIADOR (dono do caso, D4) — sem PHI no fluxo.
_ADMIN_CLOSED_RESULT_PREFIX = "Encerrado administrativamente"


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


def _procedure_rows(case: Case) -> list[dict[str, object]]:
    """Rows de procedimento do caso: origem + detecção (D1/D2 do change
    ``detected-procedures-visible``).

    Uma row por ``CaseProcedure`` do prefetch (sem query extra): ``label`` do
    catálogo (fallback ao tipo bruto — defensivo), ``is_declared`` (origem NIR)
    e ``detection`` ∈ {``detected``, ``not_detected``, ``None``} — ``None``
    enquanto o pipeline não reconciliou (``pending``), quando a UI exibe apenas
    a origem. Ordem canônica do catálogo; tipo fora do catálogo ao fim.
    """
    rows_by_type = {row.procedure_type: row for row in case.procedures.all()}
    ordered_types = [
        procedure_type
        for procedure_type in _PROCEDURE_LABELS_BY_TYPE
        if procedure_type in rows_by_type
    ]
    ordered_types.extend(sorted(set(rows_by_type) - set(_PROCEDURE_LABELS_BY_TYPE)))
    return [
        {
            "label": _PROCEDURE_LABELS_BY_TYPE.get(procedure_type, procedure_type),
            "is_declared": rows_by_type[procedure_type].declared_by_nir,
            "detection": (
                rows_by_type[procedure_type].detection_status
                if rows_by_type[procedure_type].detection_status != DetectionStatus.PENDING
                else None
            ),
        }
        for procedure_type in ordered_types
    ]


def _administrative_closure_result(case: Case) -> tuple[str, str] | None:
    """Resultado e texto do encerramento administrativo do caso (R3/D4).

    Lê o evento ``CASE_ADMINISTRATIVELY_CLOSED`` MAIS RECENTE das rows já
    pré-carregadas (``prefetch_related("events")`` — sem N+1): o rótulo legível
    vem do catálogo de motivos de ``apps/cases/closure.py``. Sem evento
    administrativo → ``None`` (o caso encerrado pela ciência do NIR segue sem
    resultado adicional); vale apenas para ``CLEANED``, checado no chamador.
    """
    admin_events = [
        event
        for event in case.events.all()
        if event.event_type == CaseEventType.CASE_ADMINISTRATIVELY_CLOSED
    ]
    if not admin_events:
        return None
    event = max(admin_events, key=lambda item: item.pk)
    payload = event.payload if isinstance(event.payload, dict) else {}
    reason_code = payload.get("reason_code", "")
    reason_label = ADMINISTRATIVE_CLOSURE_REASONS.get(reason_code, reason_code)
    reason_text = payload.get("reason_text", "")
    return f"{_ADMIN_CLOSED_RESULT_PREFIX} — {reason_label}", str(reason_text)


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


def _stripped_record(case: Case) -> str:
    """Nº de ocorrência sem espaços nas bordas — chave da duplicidade (R1/D3).

    Mesma normalização de ``Case.display_reference``; vazio = sem nº.
    """
    return case.agency_record_number.strip()


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
                    messages.success(
                        request, f"Caso {cases[0].display_reference} criado com sucesso."
                    )
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


def _build_my_cases_context(request: HttpRequest) -> dict[str, object]:
    """Contexto de "meus casos" (assembly único página + partial, D1).

    Escopo por criador em qualquer combinação (R2): os filtros server-side
    ``q``/``status``/``procedure_type`` (AND) aplicam-se DENTRO do queryset do
    criador e ANTES do cálculo dos sufixos de desambiguação (D4 — como as
    abas). Sem paginação nesta view (D2: a lista segue materializada).
    """
    user = _require_user(request)
    requested_tab = request.GET.get("tab", _DEFAULT_MY_CASES_TAB)
    current_tab = requested_tab if requested_tab in _MY_CASES_TABS else _DEFAULT_MY_CASES_TAB

    # Filtros preservados na URL do partial e nas pills (D4): só entram os
    # vigentes (``q`` curto é ecoado no form, mas não filtra — a consulta
    # é que o ignora).
    search = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")
    if status_filter not in _MY_CASES_STATUS_VALUES:
        status_filter = ""
    procedure_type_filter = request.GET.get("procedure_type", "")
    if procedure_type_filter not in _MY_CASES_PROCEDURE_TYPE_VALUES:
        procedure_type_filter = ""
    filter_params: list[tuple[str, str]] = []
    if search:
        filter_params.append(("q", search))
    if status_filter:
        filter_params.append(("status", status_filter))
    if procedure_type_filter:
        filter_params.append(("procedure_type", procedure_type_filter))
    filter_qs = urlencode(filter_params)
    partial_params = [("tab", current_tab), *filter_params]
    partial_url = f"{reverse('intake:my_cases_partial')}?{urlencode(partial_params)}"

    own_cases = Case.objects.filter(created_by=user)
    # Contadores das pills (D5/D6): totais do criador por aba, sem os filtros
    # de busca (as pills navegam entre abas, não entre filtros).
    active_cases = own_cases.exclude(status=CaseStatus.CLEANED)
    closed_cases = own_cases.filter(status=CaseStatus.CLEANED)
    selected_cases = closed_cases if current_tab == "closed" else active_cases

    filtered_cases = selected_cases
    if len(search) >= _MY_CASES_MIN_SEARCH_LENGTH:
        filtered_cases = filtered_cases.filter(agency_record_number__icontains=search)
    if status_filter:
        filtered_cases = filtered_cases.filter(status=status_filter)
    if procedure_type_filter:
        filtered_cases = filtered_cases.filter(
            procedures__declared_by_nir=True,
            procedures__procedure_type=procedure_type_filter,
        ).distinct()

    page_cases = list(
        filtered_cases.prefetch_related("procedures", "events").order_by("-created_at")
    )
    # Nºs repetidos NA PÁGINA (R1/D3, slice 004): contagem sobre a lista já
    # carregada (sem query extra); nºs em branco nunca são chave (a linha fica
    # ``—`` e o fallback ``uid <8 chars>`` não se aplica a esta linha rotulada).
    repeated_records = Counter(record for record in map(_stripped_record, page_cases) if record)

    items = []
    for case in page_cases:
        closure_result = (
            _administrative_closure_result(case) if case.status == CaseStatus.CLEANED else None
        )
        items.append(
            {
                "case": case,
                "status_label": case.get_status_display(),
                "patient_name": case.patient_name,
                "patient_age": case.patient_age,
                "patient_gender": case.patient_gender or "",
                "origin_unit": case.origin_unit or "",
                "procedure_labels": _procedure_labels(_declared_types_from_rows(case)),
                "agency_record_number": case.agency_record_number or "—",
                # Sufixo só para nº duplicado E preenchido; senão "" (card
                # idêntico ao de antes — o template apenas renderiza).
                "reference_suffix": (
                    f" · {case.short_id}" if repeated_records[_stripped_record(case)] > 1 else ""
                ),
                "admin_closure_result": closure_result[0] if closure_result else "",
                "admin_closure_reason": closure_result[1] if closure_result else "",
            }
        )
    return {
        "items": items,
        "current_tab": current_tab,
        "active_count": active_cases.count(),
        "closed_count": closed_cases.count(),
        # Filtros vigentes (form ecoa ``q``/``status``/``procedure_type``).
        "search": search,
        "status_filter": status_filter,
        "procedure_type_filter": procedure_type_filter,
        "status_options": CaseStatus.choices,
        "procedure_type_options": _MY_CASES_PROCEDURE_TYPE_OPTIONS,
        "filter_qs": filter_qs,
        "partial_url": partial_url,
    }


@role_required("nir")
def my_cases(request: HttpRequest) -> HttpResponse:
    """Lista "meus casos" com abas ativos/encerrados (R3, escopo por criador).

    A aba **ativos** (default de ``?tab=``, com fallback seguro para valores
    desconhecidos) lista os casos do criador em tudo exceto ``CLEANED``; a
    aba **encerrados** lista apenas os ``CLEANED`` — mesmo formato de items,
    badges e ordenação (``created_at desc``). ``?tab=`` desconhecido/ausente
    nunca vaza nem quebra: cai em ativos.

    Slice 003 do painel-lista-encerramento (R3/D4): o queryset pré-carrega
    ``events`` (além de ``procedures``) e o caso ``CLEANED`` com evento
    administrativo recebe o resultado "Encerrado administrativamente — {motivo}"
    na aba de encerrados — só para o CRIADOR (o escopo por criador já o garante).

    Slice 004 do case-detail-human-reference (R1–R3/D3): a linha «Nº de
    ocorrência» dos cards do nº REPETIDO na página recebe o sufixo curto de
    desambiguação (``N · <8 chars>``, o reenvio corrigido cria um caso novo com
    o nº do original); nº único ou ausente mantém o texto de antes.

    Slice 003 do ui-parity-queues (R1–R3/D1/D4): cards no padrão das filas,
    filtros server-side (``q``/``status``/``procedure_type`` por AND) e o
    fragmento compartilhado ``intake/_my_cases_content.html`` dentro do
    container htmx com auto-refresh de 20 s (página == partial).
    """
    return render(request, "intake/my_cases.html", _build_my_cases_context(request))


@role_required("nir")
def my_cases_partial(request: HttpRequest) -> HttpResponse:
    """Fragmento de "meus casos" para o auto-refresh htmx de 20 s (R3/D1).

    Reutiliza o assembly da página cheia (``_build_my_cases_context``) e
    renderiza SÓ ``intake/_my_cases_content.html`` — o mesmo fragmento que a
    página inclui no server-side. Mesmos guards (``role_required``) e o mesmo
    escopo por criador com os mesmos filtros/aba da URL.
    """
    return render(request, "intake/_my_cases_content.html", _build_my_cases_context(request))


def _assemble_case_detail_context(
    case: Case,
    *,
    review_form: ProcedureReviewForm | None = None,
) -> dict[str, object]:
    """Monta o contexto do detalhe (página cheia e re-render 400 da revisão).

    Confirmação NIR (slice 002, R3/D2): caso marcado retido por divergência
    exibe o card com o select único dos 13 tipos (atual pré-selecionado e
    confirmável, inclusive não detectado), justificativa, leitura explícita
    e snapshots de revisão/fonte — sem upload; caso sem marca mantém o
    bypass legado. O form do re-render de erro chega pronto (tentativa
    preservada, checkbox nunca marcado); no GET um form inicial é montado.
    """
    documents = list(case.documents.all())
    attachments = list(case.attachments.all())
    communications = list(case.communication_messages.select_related("author"))

    procedure_decisions = _procedure_decisions(case)
    procedure_rows = _procedure_rows(case)
    final_reply = None
    if case.status in _FINAL_REPLY_STATES:
        final_reply = next((m for m in reversed(communications) if m.author is not None), None)

    corrections = list(case.corrected_by.order_by("created_at", "pk"))

    detail_path = reverse("intake:case_detail", args=[case.case_id])
    pdf_items = [
        {
            "position": document.position,
            "filename": document.original_filename,
            "size": document.size_bytes,
            "serve_url": reverse("intake:serve_document", args=[case.case_id, document.pk]),
            "viewer_url": reverse("intake:pdf_viewer", args=[case.case_id, document.pk]),
        }
        for document in documents
    ]
    attachment_items = [
        {
            "attachment": attachment,
            "position": position,
            "is_pdf": attachment.content_type == "application/pdf",
            "serve_url": reverse("intake:serve_attachment", args=[case.case_id, attachment.pk]),
            "viewer_url": reverse(
                "intake:attachment_pdf_viewer", args=[case.case_id, attachment.pk]
            ),
        }
        for position, attachment in enumerate(attachments, start=1)
    ]

    marked = has_procedure_review_marker(case)
    divergence_retained = (
        case.status == CaseStatus.LLM_EXTRACTING
        and case.manual_review_required
        and case.manual_review_reason == PROCEDURE_DIVERGENCE_REASON
    )
    show_procedure_review = marked and divergence_retained
    show_legacy_divergence = divergence_retained and not marked
    form = review_form
    if show_procedure_review:
        review = latest_review_event(case)
        fingerprint = source_fingerprint_for_case(case)
        if form is None:
            initial: dict[str, object] = {"source_fingerprint": fingerprint or ""}
            if review is not None:
                initial["review_event_id"] = review.pk
            declared = get_declared_procedure_types(case)
            if len(declared) == 1:
                initial["procedure_type"] = declared[0]
            form = ProcedureReviewForm(initial=initial)

    return {
        "case": case,
        "status_label": case.get_status_display(),
        "documents": documents,
        "pdf_items": pdf_items,
        "detail_url": detail_path,
        "attachments": attachments,
        "attachment_items": attachment_items,
        "communications": communications,
        # Trilha de eventos (trilha-eventos-todos-perfis, slice 002, R1):
        # a mesma trilha do painel, em card collapsible ao final do detalhe.
        "event_trail": build_event_trail_context(case),
        "procedure_rows": procedure_rows,
        "declared_procedure_rows": [row for row in procedure_rows if row["is_declared"]],
        "detected_extra_procedure_rows": [
            row
            for row in procedure_rows
            if not row["is_declared"] and row["detection"] == DetectionStatus.DETECTED
        ],
        "has_outcome": bool(procedure_decisions),
        "procedure_decisions": procedure_decisions,
        "scheduling": _scheduling_context(case),
        "final_reply": final_reply,
        "can_ack": case.status == CaseStatus.FINAL_REPLY_POSTED,
        "can_resubmit": case.status == CaseStatus.CLEANED,
        "corrections": corrections,
        "show_procedure_review": show_procedure_review,
        "show_legacy_divergence": show_legacy_divergence,
        "procedure_review_form": form,
    }


@role_required("nir")
def case_detail(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Detalhe do caso do próprio NIR: docs, comunicações, resultado e trilha.

    O queryset filtra ``created_by=user`` — caso alheio (ou inexistente)
    responde 404 sem vazar informação (D6/D9). Exibe status/timestamps, tipos
    declarados, flag de retenção com motivo, documentos com link de abertura
    (``serve_document``), a thread de comunicações do change 03 e a trilha de
    eventos (slice 002 do trilha-eventos-todos-perfis: a mesma trilha do
    painel, em card collapsible fechado ao final) — falha aparece também como
    badge de erro. Quando o caso
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
    context = _assemble_case_detail_context(case)
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
        Case.objects.only("pk", "case_id", "created_by_id", "agency_record_number"),
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
        f"Recebimento confirmado — o caso {case.display_reference} foi encerrado e os "
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
            # O reenvio corrigido aceita exatamente 1 PDF (serviço, D5); o campo
            # coleta a lista (o hint do form anuncia o único arquivo, slice 003)
            # e o cliente que mandar 0/>1 recebe aqui o erro nomeado.
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
                        f"Reenvio corrigido criado — acompanhe o novo caso {new_case.disambiguated_reference}.",
                    )
                    return redirect(reverse("intake:case_detail", args=[new_case.case_id]))

    return render(
        request,
        "intake/corrected_resubmission.html",
        {"form": form, "case": case},
    )


@role_required("nir")
def pdf_viewer(
    request: HttpRequest,
    case_id: uuid.UUID,
    document_id: int,
) -> HttpResponse:
    """Viewer mobile do PDF original do caso do próprio NIR (slice PVP-002, R1).

    Mesmo guard fail-closed de ``serve_document``: queryset por criador —
    caso alheio, documento de outro caso ou inexistente → 404. O ``?next=``
    passa por ``resolve_safe_next_url`` (fallback ao detalhe); o template
    ``pdf_viewer/mobile_pdf_viewer.html`` recebe título, URLs do PDF e volta.
    """
    user = _require_user(request)
    case = get_object_or_404(
        Case.objects.only("case_id", "created_by"),
        case_id=case_id,
        created_by=user,
    )
    document = get_object_or_404(
        CaseDocument.objects.only("pk", "case_id", "file"),
        case=case,
        pk=document_id,
    )
    if not document.file:
        raise Http404
    detail_url = reverse("intake:case_detail", args=[case.case_id])
    serve_url = reverse("intake:serve_document", args=[case.case_id, document_id])
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


@role_required("nir")
def attachment_pdf_viewer(
    request: HttpRequest,
    case_id: uuid.UUID,
    attachment_id: int,
) -> HttpResponse:
    """Viewer mobile do anexo PDF do caso do próprio NIR (slice PVP-006, R2).

    Mesmo guard fail-closed de ``serve_attachment``: queryset por criador —
    caso alheio, anexo de outro caso ou inexistente → 404. SOMENTE para
    ``application/pdf`` (D3): imagens e outros tipos usam link direto +
    ``<img loading=lazy>`` (sem viewer dedicado) → 404. Anexo sem arquivo →
    404 fail-closed (lição P1-4). O ``?next=`` passa por
    ``resolve_safe_next_url`` (fallback ao detalhe); o template
    ``pdf_viewer/mobile_pdf_viewer.html`` recebe título, URLs do PDF e volta.
    """
    user = _require_user(request)
    case = get_object_or_404(
        Case.objects.only("case_id", "created_by"),
        case_id=case_id,
        created_by=user,
    )
    attachment = get_object_or_404(
        CaseAttachment.objects.only("pk", "case_id", "file", "content_type"),
        case=case,
        pk=attachment_id,
    )
    if attachment.content_type != "application/pdf" or not attachment.file:
        raise Http404
    detail_url = reverse("intake:case_detail", args=[case.case_id])
    serve_url = reverse("intake:serve_attachment", args=[case.case_id, attachment_id])
    back_url = resolve_safe_next_url(request, detail_url)
    return render(
        request,
        "pdf_viewer/mobile_pdf_viewer.html",
        {
            "viewer_title": "Anexo",
            "pdf_url": serve_url,
            "fallback_pdf_url": serve_url,
            "back_url": back_url,
        },
    )


@role_required("nir")
@xframe_options_sameorigin
def serve_attachment(
    request: HttpRequest,
    case_id: uuid.UUID,
    attachment_id: int,
) -> HttpResponse:
    """Serve o arquivo do anexo do caso do próprio NIR (slice PVP-006, R1).

    Mesmo guard fail-closed de ``serve_document`` (queryset por criador —
    caso alheio, anexo de outro caso ou inexistente → 404). Content-type
    ORIGINAL do upload e filename original; anexo sem arquivo → 404
    fail-closed (lição P1-4). O arquivo servido é o ORIGINAL (R4 — bytes
    intactos, mesma política dos serves de documento).
    """
    user = _require_user(request)
    case = get_object_or_404(
        Case.objects.only("case_id", "created_by"),
        case_id=case_id,
        created_by=user,
    )
    attachment = get_object_or_404(
        CaseAttachment.objects.only("pk", "case_id", "file", "original_filename", "content_type"),
        case=case,
        pk=attachment_id,
    )
    if not attachment.file:
        raise Http404
    return cast(
        HttpResponse,
        FileResponse(
            attachment.file.open("rb"),
            content_type=attachment.content_type or "application/octet-stream",
            filename=attachment.original_filename or "anexo",
        ),
    )


@role_required("nir")
@xframe_options_sameorigin
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
        Case.objects.only("pk", "case_id", "created_by_id", "agency_record_number"),
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
            f"Caso {case.display_reference} liberado — a divergência foi dispensada e o caso "
            "avançou para a sumarização.",
        )
    else:
        messages.success(
            request,
            f"Caso {case.display_reference} liberado — o gate foi dispensado e o caso avançou para anonimização.",
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
        Case.objects.only("pk", "case_id", "created_by_id", "agency_record_number"),
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
        f"Caso {case.display_reference} reenviado — documentos substituídos e reprocessamento iniciado.",
    )
    return redirect(reverse("intake:case_detail", args=[case.case_id]))


def _procedure_review_support_error(case_id: uuid.UUID, case_reference: str) -> HttpResponse:
    """Detalhe de erro 503 do reprocessamento pendente de suporte (R3/D7).

    Sem formulário nem botão de repetição: a confirmação está registrada e o
    recovery não foi programado — só a equipe de suporte resolve, informada
    pelo número do caso. Corpo mínimo, sem PHI e sem sucesso falso.
    """
    detail_url = reverse("intake:case_detail", args=[case_id])
    body = (
        '<!doctype html><html lang="pt-br"><head><meta charset="utf-8">'
        "<title>Processamento pendente de suporte</title></head><body>"
        f"<p>Confirmação do caso {escape(case_reference)} registrada; o "
        "processamento está pendente de suporte — informe o número do caso "
        "à equipe técnica. Não é necessário reenviar o relatório.</p>"
        f'<p><a href="{escape(detail_url)}">Voltar ao caso</a></p>'
        "</body></html>"
    )
    return HttpResponse(body, status=503)


@role_required("nir")
@require_POST
def procedure_review_confirm(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    """Confirma o procedimento do caso e solicita o reprocessamento (R3/D2).

    POST escopado ao caso do próprio NIR (alheio/inexistente → 404, sem
    vazar informação). A cardinalidade é validada via ``getlist`` (um único
    valor — o ChoiceField sozinho tomaria o último de um campo repetido) e
    o restante pelo ``ProcedureReviewForm``; a defesa de mutação continua no
    serviço ``confirm_case_procedure_review``. Sucesso → redirect ao detalhe
    com flash na referência humana; form inválido ou pré-condição obsoleta
    (revisão/fonte) → re-render do detalhe com HTTP 400, tentativa preservada
    e escapada, checkbox nunca marcado; lock ativo de outro ator → 409 com
    mensagem genérica (sem PHI/técnica); papel sem vínculo NIR → 403.
    Falha de dispatch pós-commit → a confirmação permanece e o outcome do
    recovery vira 302-aviso (programado) ou 503-suporte (sem recovery).
    """
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    case = get_object_or_404(
        Case.objects.only("pk", "case_id", "created_by_id", "agency_record_number"),
        case_id=case_id,
        created_by=user,
    )
    detail_url = reverse("intake:case_detail", args=[case.case_id])
    selected = request.POST.getlist("procedure_type")
    form = ProcedureReviewForm(request.POST)
    valid = form.is_valid()
    if len(selected) != 1:
        form.add_error("procedure_type", "Selecione exatamente um procedimento do catálogo.")
        valid = False
    if valid:
        try:
            confirm_case_procedure_review(
                case=case,
                user=user,
                role=active_role,
                procedure_type=form.cleaned_data["procedure_type"],
                review_acknowledged=form.cleaned_data["review_acknowledged"],
                review_justification=form.cleaned_data["review_justification"],
                review_event_id=form.cleaned_data["review_event_id"],
                source_fingerprint=form.cleaned_data["source_fingerprint"],
            )
        except ProcedureReviewEnqueueAfterCommitError as exc:
            # A confirmação já commitou; só o dispatch falhou (D7). Recovery
            # programado → redirect 302 com aviso; sem recovery → detalhe de
            # erro 503 com orientação de suporte (sem formulário/botão, sem
            # PHI, sem sucesso falso). Nenhuma outra exceção é convertida.
            if exc.recovery_scheduled:
                logger.warning(
                    "procedure_review_recovery_scheduled user=%s case=%s confirmacao=%s",
                    user.pk,
                    case_id,
                    exc.confirmation_event_id,
                )
                messages.warning(
                    request,
                    f"Caso {case.display_reference} confirmado — a recuperação do "
                    "processamento foi programada.",
                )
                return redirect(detail_url)
            logger.warning(
                "procedure_review_support_needed user=%s case=%s confirmacao=%s motivo=%s",
                user.pk,
                case_id,
                exc.confirmation_event_id,
                exc.reason_code,
            )
            messages.error(
                request,
                f"Caso {case.display_reference} confirmado, mas o processamento está "
                "pendente de suporte — informe o número do caso à equipe técnica.",
            )
            return _procedure_review_support_error(case.case_id, case.display_reference)
        except Http404:
            raise
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except CaseLockConflictError as exc:
            logger.warning(
                "procedure_review_locked user=%s case=%s motivo=%s", user.pk, case_id, exc
            )
            return _gate_action_error(
                "Este caso está sob processamento no momento — aguarde e tente novamente.",
                case_id,
                status=409,
            )
        except ValueError as exc:
            logger.warning(
                "procedure_review_rejected user=%s case=%s motivo=%s", user.pk, case_id, exc
            )
            form.add_error(None, str(exc))
            valid = False
        else:
            messages.success(
                request,
                f"Caso {case.display_reference} confirmado — o reprocessamento "
                "do procedimento foi iniciado.",
            )
            return redirect(detail_url)
    # Re-render do detalhe com a tentativa preservada e escapada, sem
    # consentimento automático: o checkbox nunca volta marcado após erro.
    display_data = request.POST.copy()
    if "review_acknowledged" in display_data:
        del display_data["review_acknowledged"]
    display_form = ProcedureReviewForm(display_data)
    display_form.is_valid()
    if len(selected) != 1:
        display_form.add_error(
            "procedure_type", "Selecione exatamente um procedimento do catálogo."
        )
    for message in form.non_field_errors():
        display_form.add_error(None, message)
    full_case = get_object_or_404(
        Case.objects.select_related("created_by").prefetch_related("procedures"),
        case_id=case_id,
        created_by=user,
    )
    context = _assemble_case_detail_context(full_case, review_form=display_form)
    return render(request, "intake/case_detail.html", context, status=400)
