"""Tipos canônicos de eventos do caso (design D5).

``CaseEventType`` é o enum único consumido pela FSM, serviços, locks e
projeção de comunicações (D8): a trilha só grava tipos canônicos definidos
aqui, e cada change futuro estende o enum (procedimentos no 003, locks no
004, etc.) sem alterar o model. Transições FSM seguem o padrão
``CASE_STATUS_<ESTADO-ALVO>`` (R5) com payload ``{"source", "target", ...}``.
"""

from __future__ import annotations

from django.db import models


class CaseEventType(models.TextChoices):
    """Tipos de evento da trilha de auditoria (append-only)."""

    # Transições da FSM de 17 estados (design D4): CASE_STATUS_<target>.
    CASE_STATUS_PDF_EXTRACTING = "CASE_STATUS_PDF_EXTRACTING", "Extração de PDF em andamento"
    CASE_STATUS_ANONYMIZING = "CASE_STATUS_ANONYMIZING", "Anonimização em andamento"
    CASE_STATUS_LLM_EXTRACTING = "CASE_STATUS_LLM_EXTRACTING", "Extração via LLM em andamento"
    CASE_STATUS_LLM_SUMMARIZING = "CASE_STATUS_LLM_SUMMARIZING", "Sumarização via LLM em andamento"
    CASE_STATUS_AWAITING_DOCTOR = "CASE_STATUS_AWAITING_DOCTOR", "Caso aguardando decisão médica"
    CASE_STATUS_FAILED = "CASE_STATUS_FAILED", "Falha de processamento"
    CASE_STATUS_DOCTOR_DENIED = "CASE_STATUS_DOCTOR_DENIED", "Decisão médica de negação"
    CASE_STATUS_DOCTOR_ACCEPTED = "CASE_STATUS_DOCTOR_ACCEPTED", "Decisão médica de aceitação"
    CASE_STATUS_SCHEDULER_REQUESTED = "CASE_STATUS_SCHEDULER_REQUESTED", "Agendamento solicitado"
    CASE_STATUS_AWAITING_SCHEDULING = (
        "CASE_STATUS_AWAITING_SCHEDULING",
        "Aguardando confirmação de agendamento",
    )
    CASE_STATUS_SCHEDULING_CONFIRMED = "CASE_STATUS_SCHEDULING_CONFIRMED", "Agendamento confirmado"
    CASE_STATUS_SCHEDULING_DENIED = "CASE_STATUS_SCHEDULING_DENIED", "Agendamento negado"
    CASE_STATUS_FINAL_REPLY_POSTED = "CASE_STATUS_FINAL_REPLY_POSTED", "Resposta final publicada"
    CASE_STATUS_AWAITING_NIR_ACK = "CASE_STATUS_AWAITING_NIR_ACK", "Aguardando ciência do NIR"
    CASE_STATUS_CLEANING = "CASE_STATUS_CLEANING", "Limpeza de dados em andamento"
    CASE_STATUS_CLEANED = "CASE_STATUS_CLEANED", "Caso concluído"

    # Operações de procedimento por caso (slice 003): declaradas em payloads
    # enxutos pelos serviços de apps/cases/procedures.py.
    CASE_PROCEDURES_DECLARED = "CASE_PROCEDURES_DECLARED", "Procedimentos declarados pelo NIR"
    CASE_PROCEDURES_DETECTED = "CASE_PROCEDURES_DETECTED", "Detecção de procedimentos registrada"
    CASE_DOCTOR_DECISIONS_RECORDED = (
        "CASE_DOCTOR_DECISIONS_RECORDED",
        "Decisões médicas por procedimento registradas",
    )


def case_status_event_type(state: str) -> str:
    """Resolve o tipo canônico do evento de transição para o estado-alvo.

    Transições gravam ``CASE_STATUS_<ESTADO>`` (R5); ``state`` é o valor do
    ``CaseStatus`` alcançado. Estados sem tipo canônico são erro de código
    (fail-fast) — o contrato manda o conjunto ser completo.
    """
    canonical = f"CASE_STATUS_{state}"
    if canonical not in CaseEventType.values:
        raise ValueError(f"Estado sem tipo canônico de evento de transição: {state!r}")
    return canonical
