"""Rótulos legíveis da trilha de eventos do painel (painel-ats-parity, slice 003).

Fonte canônica dos textos da trilha (R1/D3), no molde do dashboard do ats-web
(``EVENT_LABELS.get(t, t)``): o supervisor lê «Caso criado» em vez de
``CASE_CREATED``. O mapa cobre **todos** os valores de ``CaseEventType`` —
tipo novo sem rótulo quebra o teste anti-drift (``test_event_labels.py``) em
vez de aparecer cru no painel; tipos desconhecidos caem no fallback (valor
bruto). As categorias de badge (``EVENT_BADGE_CSS``) separam etapa do sistema,
ação de usuário e falha; ``payload`` com ``status=FAILED`` força o vermelho
(evento de erro é a exceção, não a regra).
"""

from __future__ import annotations

from collections.abc import Mapping

from apps.cases.events import CaseEventType

# Classes Bootstrap 5.3 do badge por categoria (R1): sistema (etapas
# automáticas), NIR (ações do operador), médico, agendador, atenção (retenção),
# conclusão e falha.
BADGE_SYSTEM = "text-bg-secondary"
BADGE_NIR = "text-bg-info"
BADGE_DOCTOR = "text-bg-primary"
BADGE_SCHEDULER = "text-bg-warning"
BADGE_ATTENTION = "text-bg-warning"
BADGE_CONCLUSION = "text-bg-success"
BADGE_FAILURE = "text-bg-danger"

# Valor de ``payload["status"]`` que marca o evento como falha (R1).
_FAILED_PAYLOAD_STATUS = "FAILED"

# Rótulos pt-BR por tipo de evento (R1/D3) — cobertura COMPLETA do enum.
EVENT_LABELS: dict[str, str] = {
    # Transições da FSM de 17 estados.
    CaseEventType.CASE_STATUS_PDF_EXTRACTING: "Extração de texto iniciada",
    CaseEventType.CASE_STATUS_ANONYMIZING: "Anonimização iniciada",
    CaseEventType.CASE_STATUS_LLM_EXTRACTING: "Análise automática (estrutura) iniciada",
    CaseEventType.CASE_STATUS_LLM_SUMMARIZING: "Análise automática (sugestão) iniciada",
    CaseEventType.CASE_STATUS_AWAITING_DOCTOR: "Caso encaminhado para avaliação médica",
    CaseEventType.CASE_STATUS_FAILED: "Falha no processamento",
    CaseEventType.CASE_STATUS_DOCTOR_DENIED: "Decisão médica registrada: negado",
    CaseEventType.CASE_STATUS_DOCTOR_ACCEPTED: "Decisão médica registrada: aceito",
    CaseEventType.CASE_STATUS_SCHEDULER_REQUESTED: "Caso encaminhado ao agendamento",
    CaseEventType.CASE_STATUS_AWAITING_SCHEDULING: "Aguardando confirmação do agendamento",
    CaseEventType.CASE_STATUS_SCHEDULING_CONFIRMED: "Agendamento confirmado",
    CaseEventType.CASE_STATUS_SCHEDULING_DENIED: "Agendamento negado",
    CaseEventType.CASE_STATUS_FINAL_REPLY_POSTED: "Resposta final publicada ao NIR",
    CaseEventType.CASE_STATUS_AWAITING_NIR_ACK: "Aguardando ciência do NIR",
    CaseEventType.CASE_STATUS_CLEANING: "Limpeza dos dados iniciada",
    CaseEventType.CASE_STATUS_CLEANED: "Caso concluído e dados minimizados",
    # Procedimentos por caso.
    CaseEventType.CASE_PROCEDURES_DECLARED: "Procedimentos declarados pelo NIR",
    CaseEventType.CASE_PROCEDURES_DETECTED: "Procedimentos detectados na análise automática",
    CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED: ("Decisões médicas por procedimento registradas"),
    # Locks/lease de mutação por caso.
    CaseEventType.CASE_LOCK_CLAIMED: "Caso reservado para processamento",
    CaseEventType.CASE_LOCK_RELEASED: "Reserva de processamento liberada",
    CaseEventType.CASE_LOCK_RENEWED: "Reserva de processamento renovada",
    CaseEventType.CASE_LOCK_EXPIRED: "Reserva de processamento expirada",
    # Extração e gate do intake.
    CaseEventType.CASE_EXTRACTION_COMPLETED: "Extração de texto do relatório concluída",
    CaseEventType.CASE_GATE_MANUAL_REVIEW: "Documento retido para revisão do NIR",
    CaseEventType.CASE_GATE_BYPASSED: "Revisão do NIR liberou o documento",
    # Anonimização e pipeline de análise.
    CaseEventType.CASE_ANONYMIZATION_COMPLETED: "Anonimização concluída",
    CaseEventType.CASE_LLM1_COMPLETED: "Análise automática (estrutura) concluída",
    CaseEventType.CASE_LLM2_COMPLETED: "Análise automática (sugestão) concluída",
    CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE: (
        "Divergência entre declarado e detectado retida para revisão"
    ),
    CaseEventType.CASE_POLICY_EVALUATED: "Política pré-operatória avaliada",
    CaseEventType.PRIOR_CASE_LOOKUP: "Consulta a casos anteriores registrada",
    # Reenvio corrigido.
    CaseEventType.CASE_MARKED_SUPERSEDED: "Caso substituído por reenvio corrigido",
    CaseEventType.CASE_CORRECTION_CREATED: "Reenvio corrigido criado",
    # Anexos clínicos.
    CaseEventType.CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED: "Anexo enviado a OCR externo",
    CaseEventType.CASE_ATTACHMENT_PROCESSED: "Anexo processado e verificado",
    CaseEventType.CASE_ATTACHMENT_FAILED: "Falha no processamento do anexo",
    # Encerramento administrativo do supervisor.
    CaseEventType.CASE_ADMINISTRATIVELY_CLOSED: "Caso encerrado administrativamente",
}

# Categoria do badge por tipo (R1) — cobertura COMPLETA do enum.
EVENT_BADGE_CSS: dict[str, str] = {
    CaseEventType.CASE_STATUS_PDF_EXTRACTING: BADGE_SYSTEM,
    CaseEventType.CASE_STATUS_ANONYMIZING: BADGE_SYSTEM,
    CaseEventType.CASE_STATUS_LLM_EXTRACTING: BADGE_SYSTEM,
    CaseEventType.CASE_STATUS_LLM_SUMMARIZING: BADGE_SYSTEM,
    CaseEventType.CASE_STATUS_AWAITING_DOCTOR: BADGE_SYSTEM,
    CaseEventType.CASE_STATUS_FAILED: BADGE_FAILURE,
    CaseEventType.CASE_STATUS_DOCTOR_DENIED: BADGE_DOCTOR,
    CaseEventType.CASE_STATUS_DOCTOR_ACCEPTED: BADGE_DOCTOR,
    CaseEventType.CASE_STATUS_SCHEDULER_REQUESTED: BADGE_SYSTEM,
    CaseEventType.CASE_STATUS_AWAITING_SCHEDULING: BADGE_SCHEDULER,
    CaseEventType.CASE_STATUS_SCHEDULING_CONFIRMED: BADGE_CONCLUSION,
    CaseEventType.CASE_STATUS_SCHEDULING_DENIED: BADGE_SCHEDULER,
    CaseEventType.CASE_STATUS_FINAL_REPLY_POSTED: BADGE_SYSTEM,
    CaseEventType.CASE_STATUS_AWAITING_NIR_ACK: BADGE_NIR,
    CaseEventType.CASE_STATUS_CLEANING: BADGE_SYSTEM,
    CaseEventType.CASE_STATUS_CLEANED: BADGE_CONCLUSION,
    CaseEventType.CASE_PROCEDURES_DECLARED: BADGE_NIR,
    CaseEventType.CASE_PROCEDURES_DETECTED: BADGE_SYSTEM,
    CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED: BADGE_DOCTOR,
    CaseEventType.CASE_LOCK_CLAIMED: BADGE_SYSTEM,
    CaseEventType.CASE_LOCK_RELEASED: BADGE_SYSTEM,
    CaseEventType.CASE_LOCK_RENEWED: BADGE_SYSTEM,
    CaseEventType.CASE_LOCK_EXPIRED: BADGE_SYSTEM,
    CaseEventType.CASE_EXTRACTION_COMPLETED: BADGE_SYSTEM,
    CaseEventType.CASE_GATE_MANUAL_REVIEW: BADGE_ATTENTION,
    CaseEventType.CASE_GATE_BYPASSED: BADGE_NIR,
    CaseEventType.CASE_ANONYMIZATION_COMPLETED: BADGE_SYSTEM,
    CaseEventType.CASE_LLM1_COMPLETED: BADGE_SYSTEM,
    CaseEventType.CASE_LLM2_COMPLETED: BADGE_SYSTEM,
    CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE: BADGE_ATTENTION,
    CaseEventType.CASE_POLICY_EVALUATED: BADGE_SYSTEM,
    CaseEventType.PRIOR_CASE_LOOKUP: BADGE_SYSTEM,
    CaseEventType.CASE_MARKED_SUPERSEDED: BADGE_NIR,
    CaseEventType.CASE_CORRECTION_CREATED: BADGE_NIR,
    CaseEventType.CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED: BADGE_SYSTEM,
    CaseEventType.CASE_ATTACHMENT_PROCESSED: BADGE_SYSTEM,
    CaseEventType.CASE_ATTACHMENT_FAILED: BADGE_FAILURE,
    CaseEventType.CASE_ADMINISTRATIVELY_CLOSED: BADGE_ATTENTION,
}


def event_label(event_type: str) -> str:
    """Rótulo legível do tipo; tipo fora do mapa devolve o valor bruto (R1)."""
    return EVENT_LABELS.get(event_type, event_type)


def event_badge_css(event_type: str, payload: Mapping[str, object] | None = None) -> str:
    """Classe do badge do evento (R1): falha (tipo ``*_FAILED`` ou payload com
    ``status=FAILED``) → danger; demais no mapa por categoria; desconhecido →
    badge de sistema (default seguro)."""
    if event_type.endswith("_FAILED") or _payload_failed(payload):
        return BADGE_FAILURE
    return EVENT_BADGE_CSS.get(event_type, BADGE_SYSTEM)


def _payload_failed(payload: Mapping[str, object] | None) -> bool:
    """Payload marca falha quando ``status`` normaliza para ``FAILED``."""
    if not payload:
        return False
    return str(payload.get("status", "")).upper() == _FAILED_PAYLOAD_STATUS
