"""Rótulos de card do painel gerencial (painel-lista-encerramento, slice 003).

Fonte canônica dos textos do card da lista (D1): o "próximo passo" operacional
por estado da FSM (``CASE_NEXT_STEP_LABELS`` — cobertura COMPLETA dos 17
estados, pinada em ``tests/test_case_labels.py``) e o resultado imutável por
origem do evento de resposta final (``CASE_RESULT_LABELS``). O template do
painel nunca inventa texto inline: qualquer estado novo na FSM quebra o teste
de cobertura em vez de aparecer sem rótulo.
"""

from __future__ import annotations

from apps.cases.models import CaseStatus
from apps.cases.procedure_catalog import PROCEDURE_PROFILES

# Próximo passo operacional por estado (R1/D1), no estilo do ats-web
# ("Pendente: <papel>"): o card responde "quem age agora" mesmo quando o caso
# está parado numa etapa automática do pipeline.
CASE_NEXT_STEP_LABELS: dict[str, str] = {
    CaseStatus.NEW: "Aguardando início do processamento",
    CaseStatus.PDF_EXTRACTING: "Processando: extração de PDF",
    CaseStatus.ANONYMIZING: "Processando: anonimização",
    CaseStatus.LLM_EXTRACTING: "Processando: extração via LLM",
    CaseStatus.LLM_SUMMARIZING: "Processando: sumarização via LLM",
    CaseStatus.AWAITING_DOCTOR: "Pendente: médico",
    CaseStatus.DOCTOR_DENIED: "Publicando resposta final ao NIR",
    CaseStatus.DOCTOR_ACCEPTED: "Encaminhando ao agendamento",
    CaseStatus.SCHEDULER_REQUESTED: "Pendente: agendador",
    CaseStatus.AWAITING_SCHEDULING: "Pendente: agendador (confirmar agendamento)",
    CaseStatus.SCHEDULING_CONFIRMED: "Pendente: NIR (resposta final)",
    CaseStatus.SCHEDULING_DENIED: "Pendente: NIR (resposta final)",
    CaseStatus.FAILED: "Pendente: suporte",
    CaseStatus.FINAL_REPLY_POSTED: "Pendente: NIR (ciência do recebimento)",
    CaseStatus.AWAITING_NIR_ACK: "Aguardando limpeza dos dados",
    CaseStatus.CLEANING: "Processando: limpeza dos dados",
    CaseStatus.CLEANED: "Caso concluído",
}

# Resultado por ``payload["source"]`` do ÚLTIMO evento de resposta final (D1) —
# as 3 origens de desfecho imutável de ``apps/dashboard/metrics.py``.
CASE_RESULT_LABELS: dict[str, str] = {
    CaseStatus.DOCTOR_DENIED: "Negado pelo médico",
    CaseStatus.SCHEDULING_CONFIRMED: "Agendado",
    CaseStatus.SCHEDULING_DENIED: "Agendamento negado",
}

# Opções do filtro «Tipo de exame» da lista (change painel-ats-parity, slice 002,
# R1/D2): o par (valor, label) na ORDEM CANÔNICA do catálogo — o dropdown do
# painel e o filtro da consulta bebem desta fonte única, sem lista paralela.
PROCEDURE_TYPE_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (profile.procedure_type, profile.label) for profile in PROCEDURE_PROFILES
)
