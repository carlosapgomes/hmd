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

    # Locks/lease de exclusividade de mutação por caso (slice 004, D6): claim,
    # liberação, renovação e expiração de leases em apps/cases/locks.py.
    CASE_LOCK_CLAIMED = "CASE_LOCK_CLAIMED", "Exclusividade de mutação reivindicada"
    CASE_LOCK_RELEASED = "CASE_LOCK_RELEASED", "Exclusividade de mutação liberada"
    CASE_LOCK_RENEWED = "CASE_LOCK_RENEWED", "Lease de exclusividade renovada"
    CASE_LOCK_EXPIRED = "CASE_LOCK_EXPIRED", "Lease de exclusividade expirada"

    # Extração e gate do intake (change intake-nir-upload, slice 003, D4/D5):
    # eventos não-transicionais gravados pela task do worker e pelas ações de
    # revisão do gate (o bypass é o slice 005).
    CASE_EXTRACTION_COMPLETED = (
        "CASE_EXTRACTION_COMPLETED",
        "Extração de PDF concluída",
    )
    CASE_GATE_MANUAL_REVIEW = (
        "CASE_GATE_MANUAL_REVIEW",
        "Documento retido pelo gate para revisão manual",
    )
    CASE_GATE_BYPASSED = (
        "CASE_GATE_BYPASSED",
        "Gate de regulação dispensado na revisão do NIR",
    )

    # Anonimização (change presidio-anonymization, slice 003, design D6):
    # evento não-transicional gravado pelo serviço
    # ``apps/anonymization/services.py::anonymize_case_text`` ao persistir os
    # artefatos no caso (payload enxuto: contagens + versões). A transição FSM
    # de conclusão é da task do slice 004.
    CASE_ANONYMIZATION_COMPLETED = (
        "CASE_ANONYMIZATION_COMPLETED",
        "Anonimização concluída",
    )

    # Pipeline LLM (change llm-pipeline-per-type, D10): TODOS os eventos do
    # change nascem AQUI (slice 004 é o dono; os slices 005/006 apenas usam).
    # Gravados pelos serviços do pipeline (llm1/llm2/policy/prior-case) e pelo
    # gate de divergência com payloads enxutos (names/versions/contagens/
    # classificação — nunca conteúdo clínico bruto). ``CASE_GATE_BYPASSED``
    # (do gate do intake) é reutilizado pela liberação de divergência.
    CASE_LLM1_COMPLETED = "CASE_LLM1_COMPLETED", "Extração LLM1 concluída"
    CASE_LLM2_COMPLETED = "CASE_LLM2_COMPLETED", "Sumarização LLM2 concluída"
    CASE_GATE_PROCEDURE_DIVERGENCE = (
        "CASE_GATE_PROCEDURE_DIVERGENCE",
        "Divergência declarado×detectado retida para revisão do NIR",
    )
    CASE_POLICY_EVALUATED = "CASE_POLICY_EVALUATED", "Policy pré-operatória avaliada"
    PRIOR_CASE_LOOKUP = "PRIOR_CASE_LOOKUP", "Consulta a casos anteriores registrada"

    # Reenvio corrigido de caso encerrado (change nir-result-closure, slice
    # 004, design D4): eventos aditivos gravados pelo serviço
    # ``apps/intake/services.py::create_corrected_resubmission`` — a
    # supersedição no ORIGINAL (payload com o id do novo caso) e a correção no
    # NOVO caso (payload com id do original + motivo).
    CASE_MARKED_SUPERSEDED = (
        "CASE_MARKED_SUPERSEDED",
        "Caso substituído por reenvio corrigido",
    )
    CASE_CORRECTION_CREATED = (
        "CASE_CORRECTION_CREATED",
        "Reenvio corrigido criado",
    )

    # Anexos clínicos (change attachment-processing-ocr, design D1/D3): os
    # três canônicos do anexo nascem AQUI — ``…EXTERNAL_OCR_DISPATCHED``
    # (auditoria de PII enviada a OCR externo: payload filename+método,
    # gravado ANTES do envio), ``…FAILED`` (motivo da falha de uma etapa) e
    # ``…PROCESSED`` (definido neste change; gravado apenas pelo slice 003,
    # que fecha o anexo como ``processed`` com o resultado da verificação).
    CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED = (
        "CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED",
        "Anexo enviado a OCR externo",
    )
    CASE_ATTACHMENT_PROCESSED = (
        "CASE_ATTACHMENT_PROCESSED",
        "Anexo processado e verificado",
    )
    CASE_ATTACHMENT_FAILED = (
        "CASE_ATTACHMENT_FAILED",
        "Falha no processamento do anexo",
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
