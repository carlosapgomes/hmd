"""Presenter do detalhe do caso para o agendador (scheduler-multi-unit, D3/R3;
paridade ats-web, slice 001 da change paridade-ats-filas-historico-intercorrencia).

Serviço puro ``build_scheduler_case_detail_context(case)`` (sem request) que
monta o dict de contexto a partir dos artefatos persistidos — o template
apenas renderiza. Detalhe **limitado ao necessário** (decisão do dono
2026-09-08, design D3; visibilidade in-app liberada pela decisão D1 da change
de paridade): identificação real, diagnóstico = o **sumário clínico COMPLETO**
(o ``summary_text`` INTEIRO) re-identificado
EXCLUSIVAMENTE na renderização via ``reidentify_text`` (a mesma função do
presenter médico), decisões médicas por procedimento (disposição + motivo das
negativas) e dados de agendamento atuais. NENHUM conteúdo de
``structured_data``/``policy_result``/``suggested_action`` sai daqui — o guard
é o próprio presenter (assert de ausência nos testes com artefatos populados).
"""

from __future__ import annotations

from datetime import datetime

from django.utils.timezone import localtime

from apps.anonymization.reidentify import reidentify_text
from apps.cases.event_trail import build_event_trail_context
from apps.cases.models import Case, CaseStatus, DoctorDisposition, SchedulingUnit
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.cases.procedures import get_declared_procedure_types
from apps.cases.units import unit_label

# Perfil do catálogo por tipo (label/subtipo dos cards).
_PROFILE_BY_TYPE = {profile.procedure_type: profile for profile in PROCEDURE_PROFILES}
# Rótulo legível da disposição médica atual da row (DoctorDisposition).
_DISPOSITION_LABELS = dict(DoctorDisposition.choices)

# Formato de exibição de data/hora (fuso local da aplicação).
_DATETIME_DISPLAY_FORMAT = "%d/%m/%Y %H:%M"


def _procedure_label(procedure_type: str) -> str:
    """Rótulo legível do procedimento (fallback: o próprio id do catálogo)."""
    profile = _PROFILE_BY_TYPE.get(procedure_type)
    return profile.label if profile is not None else procedure_type


def _procedure_subtype(procedure_type: str) -> str:
    """Subtipo do procedimento no catálogo (badge do card/detalhe)."""
    profile = _PROFILE_BY_TYPE.get(procedure_type)
    return profile.doctor_subtipo if profile is not None else ""


def _format_datetime(value: datetime | None) -> str:
    """Data/hora exibível (fuso local da aplicação) ou vazio quando nula."""
    if value is None:
        return ""
    return localtime(value).strftime(_DATETIME_DISPLAY_FORMAT)


def build_scheduler_case_detail_context(case: Case) -> dict[str, object]:
    """Contexto limitado do detalhe para o agendador (puro, R3/D3; slice 001:
    sumário completo).

        Identificação real (``patient_name``/``patient_birth_date``/
        ``agency_record_number``), diagnóstico (o ``summary_text`` INTEIRO
        re-identificado só aqui, na renderização — quebras preservadas 1:1 via
        ``linebreaksbr`` no template), decisões
        médicas por procedimento declarado (disposição + motivo/data das rows já
        decididas), dados de agendamento atuais (unidade/data/local/ator/reason)
        e a thread de comunicações. Nenhum artefato clínico (structured_data/
        policy_result/suggested_action).
        A trilha de eventos (``event_trail`` — a mesma do painel, ao final do
        detalhe, fora dos gates de PDF/texto) acompanha o contexto.
    """
    # Re-identificação exclusiva na renderização — nunca persistida (D3).
    # Paridade ats-web (slice 001, rodada 3): o summary_text INTEIRO, sem
    # qualquer strip — o template usa `linebreaksbr` (cada `\\n` vira um `<br>`,
    # sem colapso de múltiplas vazias nem perda das bordas). O placeholder de
    # vazio é decidido no template (`{% if summary_text.strip %}`).
    summary_text = reidentify_text(case, case.summary_text)

    declared: list[dict[str, object]] = []
    rows_by_type = {row.procedure_type: row for row in case.procedures.all()}
    for procedure_type in get_declared_procedure_types(case):
        row = rows_by_type.get(procedure_type)
        disposition = row.doctor_disposition if row is not None else DoctorDisposition.PENDING
        declared.append(
            {
                "procedure_type": procedure_type,
                "label": _procedure_label(procedure_type),
                "subtype": _procedure_subtype(procedure_type),
                "disposition": disposition,
                "disposition_label": _DISPOSITION_LABELS.get(disposition, disposition),
                "reason": row.doctor_reason.strip() if row is not None else "",
                "decided_at": _format_datetime(row.doctor_decided_at) if row is not None else "",
            }
        )

    scheduled_by = case.scheduled_by
    scheduled_unit = case.scheduled_unit
    unit_label_text = unit_label(scheduled_unit) if scheduled_unit else ""
    denial_reason = case.scheduling_denial_reason.strip()
    reopen_reason = case.scheduling_reopen_reason.strip()
    scheduled_at = _format_datetime(case.scheduled_datetime)
    decided_at = _format_datetime(case.scheduled_decided_at)
    scheduling = {
        "unit_label": unit_label_text,
        "scheduled_at": scheduled_at,
        "location": case.scheduled_location,
        "decided_by": scheduled_by.display_name if scheduled_by is not None else "",
        "decided_at": decided_at,
        "denial_reason": denial_reason,
        "reopen_reason": reopen_reason,
    }

    return {
        "case_id": str(case.case_id),
        # Referência humana (change case-detail-human-reference, D4): título e
        # breadcrumb; o uid segue como chave técnica das URLs.
        "case_reference": case.display_reference,
        "status": str(case.status),
        "status_label": case.get_status_display(),
        "identification": {
            "patient_name": case.patient_name,
            "agency_record_number": case.agency_record_number,
            "birth_date": case.patient_birth_date,
        },
        "summary_text": summary_text,
        "declared": declared,
        "scheduling": scheduling,
        "has_scheduling": bool(
            case.scheduled_unit or scheduled_at or denial_reason or reopen_reason or decided_at
        ),
        # Thread de comunicações (change 03) — mesmo padrão de render do detalhe
        # do intake; o contexto entrega as mensagens, o template renderiza.
        "communications": list(case.communication_messages.select_related("author")),
        # Trilha de eventos (trilha-eventos-todos-perfis, slice 003): o mesmo
        # builder do painel — histórico do caso, fora dos gates de PDF/texto.
        "event_trail": build_event_trail_context(case),
        # Flag de leitura por estado: a página detalhe exibe ações apenas em
        # SCHEDULER_REQUESTED/AWAITING_SCHEDULING (confirmar/negar).
        "can_decide_scheduling": case.status
        in (CaseStatus.SCHEDULER_REQUESTED, CaseStatus.AWAITING_SCHEDULING),
        # Intercorrência (desmarcar) só em FINAL_REPLY_POSTED com unidade 1;
        # unidade 2 exibe o banner "intercorrência desabilitada" (D4).
        "can_reopen": (
            case.status == CaseStatus.FINAL_REPLY_POSTED
            and case.scheduled_unit == SchedulingUnit.UNIT_1
        ),
        "incident_disabled": (
            case.status == CaseStatus.FINAL_REPLY_POSTED
            and case.scheduled_unit == SchedulingUnit.UNIT_2
        ),
    }
