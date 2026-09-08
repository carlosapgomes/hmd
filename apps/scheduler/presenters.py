"""Presenter do detalhe do caso para o agendador (scheduler-multi-unit, D3/R3).

Serviço puro ``build_scheduler_case_detail_context(case)`` (sem request) que
monta o dict de contexto a partir dos artefatos persistidos — o template
apenas renderiza. Detalhe **limitado ao necessário** (decisão do dono
2026-09-08, design D3): identificação real, diagnóstico resumido = a
**primeira linha não-vazia** de ``summary_text`` re-identificada
EXCLUSIVAMENTE na renderização via ``reidentify_text`` (a mesma função do
presenter médico), decisões médicas por procedimento (disposição + motivo das
negativas) e dados de agendamento atuais. NENHUM conteúdo de
``structured_data``/``policy_result``/``suggested_action`` nem as demais
linhas do resumo saem daqui — o guard é o próprio presenter (assert de
ausência nos testes com artefatos populados).
"""

from __future__ import annotations

from datetime import datetime

from django.utils.timezone import localtime

from apps.anonymization.reidentify import reidentify_text
from apps.cases.models import Case, CaseStatus, DoctorDisposition, SchedulingUnit
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.cases.procedures import get_declared_procedure_types

# Perfil do catálogo por tipo (label/subtipo dos cards).
_PROFILE_BY_TYPE = {profile.procedure_type: profile for profile in PROCEDURE_PROFILES}
# Rótulo legível da disposição médica atual da row (DoctorDisposition).
_DISPOSITION_LABELS = dict(DoctorDisposition.choices)
# Rótulo legível da unidade de destino do agendamento (SchedulingUnit).
_UNIT_LABELS = dict(SchedulingUnit.choices)

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


def _first_non_empty_line(text: str) -> str:
    """Primeira linha não-vazia do resumo (diagnóstico resumido — D3)."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _format_datetime(value: datetime | None) -> str:
    """Data/hora exibível (fuso local da aplicação) ou vazio quando nula."""
    if value is None:
        return ""
    return localtime(value).strftime(_DATETIME_DISPLAY_FORMAT)


def build_scheduler_case_detail_context(case: Case) -> dict[str, object]:
    """Contexto limitado do detalhe para o agendador (puro, R3/D3).

    Identificação real (``patient_name``/``patient_birth_date``/
    ``agency_record_number``), diagnóstico resumido (primeira linha não-vazia
    de ``summary_text`` re-identificada só aqui, na renderização), decisões
    médicas por procedimento declarado (disposição + motivo/data das rows já
    decididas), dados de agendamento atuais (unidade/data/local/ator/reason)
    e a thread de comunicações. Nenhum artefato clínico (structured_data/
    policy_result/suggested_action) e nenhuma linha além da primeira do resumo.
    """
    summary_line = _first_non_empty_line(case.summary_text)
    # Re-identificação exclusiva na renderização — nunca persistida (D3).
    if summary_line:
        summary_line = reidentify_text(case, summary_line)

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
    unit_label = _UNIT_LABELS.get(case.scheduled_unit, "") if case.scheduled_unit else ""
    denial_reason = case.scheduling_denial_reason.strip()
    reopen_reason = case.scheduling_reopen_reason.strip()
    scheduled_at = _format_datetime(case.scheduled_datetime)
    decided_at = _format_datetime(case.scheduled_decided_at)
    scheduling = {
        "unit_label": unit_label,
        "scheduled_at": scheduled_at,
        "location": case.scheduled_location,
        "decided_by": scheduled_by.display_name if scheduled_by is not None else "",
        "decided_at": decided_at,
        "denial_reason": denial_reason,
        "reopen_reason": reopen_reason,
    }

    return {
        "case_id": str(case.case_id),
        "status": str(case.status),
        "status_label": case.get_status_display(),
        "identification": {
            "patient_name": case.patient_name,
            "agency_record_number": case.agency_record_number,
            "birth_date": case.patient_birth_date,
        },
        "summary_line": summary_line,
        "declared": declared,
        "scheduling": scheduling,
        "has_scheduling": bool(
            case.scheduled_unit or scheduled_at or denial_reason or reopen_reason or decided_at
        ),
        # Thread de comunicações (change 03) — mesmo padrão de render do detalhe
        # do intake; o contexto entrega as mensagens, o template renderiza.
        "communications": list(case.communication_messages.select_related("author")),
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
