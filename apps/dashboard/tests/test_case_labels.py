"""Testes dos rótulos de card do painel (painel-lista-encerramento, slice 003).

Cobre ``apps/dashboard/case_labels.py`` — a fonte canônica do "próximo passo"
por estado da FSM (R1/D1) e do rótulo do resultado imutável por origem do
evento final (D1: desfecho do ÚLTIMO ``CASE_STATUS_FINAL_REPLY_POSTED``). O
invariante central é a COBERTURA COMPLETA: nenhum dos 17 estados fica sem
rótulo e o template nunca inventa texto inline (o teste bidirecional falha se
sobrar ou faltar entrada).
"""

from __future__ import annotations

from apps.cases.models import CaseStatus
from apps.dashboard.case_labels import CASE_NEXT_STEP_LABELS, CASE_RESULT_LABELS


def test_next_step_labels_cover_all_fsm_states() -> None:
    """R1: o mapa do "próximo passo" cobre EXATAMENTE os 17 estados da FSM."""
    assert set(CASE_NEXT_STEP_LABELS) == set(CaseStatus.values)
    assert all(label.strip() for label in CASE_NEXT_STEP_LABELS.values())


def test_result_labels_cover_final_outcome_sources() -> None:
    """R1: o mapa do resultado cobre as 3 origens de desfecho do evento final."""
    assert set(CASE_RESULT_LABELS) == {
        str(CaseStatus.DOCTOR_DENIED),
        str(CaseStatus.SCHEDULING_CONFIRMED),
        str(CaseStatus.SCHEDULING_DENIED),
    }
    assert all(label.strip() for label in CASE_RESULT_LABELS.values())
