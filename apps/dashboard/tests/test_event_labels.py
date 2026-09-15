"""Testes do mapa de rótulos legíveis da trilha (painel-ats-parity, slice 003, R1/D3).

Anti-drift: o teste itera o ENUM ``CaseEventType`` (nunca uma lista hardcoded
duplicada) e exige rótulo E badge para CADA valor — tipo novo sem entrada
quebra a suíte em vez de aparecer cru no painel. O fallback (tipo fora do
mapa → valor bruto) é o contrato do molde ats-web ``EVENT_LABELS.get(t, t)``.
"""

from __future__ import annotations

import pytest

from apps.cases.events import CaseEventType
from apps.dashboard.event_labels import (
    EVENT_BADGE_CSS,
    EVENT_LABELS,
    event_badge_css,
    event_label,
)


def test_event_labels_cover_every_case_event_type() -> None:
    """Gate 1: cobertura de 100% do enum (anti-drift, iterando o enum real)."""
    missing = [value for value in CaseEventType.values if value not in EVENT_LABELS]

    assert missing == []


def test_event_labels_are_legible_not_raw_values() -> None:
    """R1: cada rótulo é texto pt-BR legível — nunca o valor bruto do tipo."""
    for value in CaseEventType.values:
        label = EVENT_LABELS[value]
        assert label.strip()
        assert label != value


def test_event_badge_css_cover_every_case_event_type() -> None:
    """R1: todo tipo tem categoria de badge (nenhum evento cai no default)."""
    missing = [value for value in CaseEventType.values if value not in EVENT_BADGE_CSS]

    assert missing == []


def test_event_label_falls_back_to_raw_value() -> None:
    """R1/D3: tipo fora do mapa exibe o valor bruto (fallback do molde)."""
    assert event_label("LEGACY_UNKNOWN_EVENT") == "LEGACY_UNKNOWN_EVENT"


def test_known_event_label_is_the_mapped_one() -> None:
    """R1: tipo do mapa exibe o rótulo legível."""
    assert event_label(CaseEventType.CASE_ADMINISTRATIVELY_CLOSED) == (
        "Caso encerrado administrativamente"
    )


@pytest.mark.parametrize(
    "event_type",
    [CaseEventType.CASE_STATUS_FAILED, CaseEventType.CASE_ATTACHMENT_FAILED],
)
def test_failed_event_types_use_danger_badge(event_type: str) -> None:
    """R1: tipo ``*_FAILED`` marca o evento em vermelho."""
    assert event_badge_css(event_type) == "text-bg-danger"


def test_failed_payload_uses_danger_badge() -> None:
    """R1: payload com ``status=FAILED`` marca o evento em vermelho mesmo em
    tipo fora do padrão ``*_FAILED``."""
    assert (
        event_badge_css(CaseEventType.CASE_POLICY_EVALUATED, {"status": "FAILED"})
        == "text-bg-danger"
    )


def test_non_failed_payload_keeps_mapped_badge() -> None:
    """R1 (não-vacuidade do teste acima): payload sem falha mantém o badge do
    mapa — o vermelho vem do status, não do tipo."""
    mapped = EVENT_BADGE_CSS[CaseEventType.CASE_POLICY_EVALUATED]

    assert mapped != "text-bg-danger"
    assert event_badge_css(CaseEventType.CASE_POLICY_EVALUATED, {"status": "completed"}) == mapped


def test_unknown_event_type_defaults_to_system_badge() -> None:
    """R1: tipo fora do mapa cai no badge de sistema (default seguro)."""
    assert event_badge_css("LEGACY_UNKNOWN_EVENT") == "text-bg-secondary"


def test_user_and_system_events_have_distinct_badges() -> None:
    """R1: a categoria separa ação de usuário (NIR) de etapa do sistema."""
    assert event_badge_css(CaseEventType.CASE_PROCEDURES_DECLARED) != event_badge_css(
        CaseEventType.CASE_STATUS_ANONYMIZING
    )
