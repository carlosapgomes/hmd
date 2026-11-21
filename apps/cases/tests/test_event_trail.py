"""Trilha de eventos compartilhada (change trilha-eventos-todos-perfis, slice 001).

Fonte única do contexto da trilha (builder ``build_event_trail_context`` +
mapa de rótulos/badges), consumida pelo partial
``templates/cases/_event_trail.html`` em todas as superfícies de detalhe.
Os testes de rótulos/badges MIGRAM de
``apps/dashboard/tests/test_event_labels.py`` (removido neste slice): o
anti-drift itera o ENUM ``CaseEventType`` (nunca lista hardcoded) e exige
rótulo E badge para CADA valor — tipo novo sem entrada quebra a suíte em vez
de aparecer cru no painel.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from pytest_django.fixtures import DjangoAssertNumQueries

from apps.accounts.models import User
from apps.cases.event_trail import (
    EVENT_BADGE_CSS,
    EVENT_LABELS,
    build_event_trail_context,
    event_badge_css,
    event_label,
)
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent
from apps.cases.procedure_review import event_detail_lines

SYSTEM_ROLE = "system"


def _add_event(
    case: Case,
    event_type: str,
    *,
    actor: User | None = None,
    actor_role: str = SYSTEM_ROLE,
    payload: dict[str, object] | None = None,
) -> CaseEvent:
    """Evento de trilha append-only (ator de sistema por padrão)."""
    return CaseEvent.objects.create(
        case=case,
        event_type=event_type,
        actor_type=ActorType.USER if actor is not None else ActorType.SYSTEM,
        actor=actor,
        actor_role=actor_role,
        payload=payload if payload is not None else {},
    )


# ── R1: builder — ordem cronológica ascendente (paridade com o painel) ─────


@pytest.mark.django_db
def test_build_event_trail_context_returns_chronological_order(nir_user: User) -> None:
    """R1: o builder devolve os eventos na ordem cronológica ascendente
    (timestamp, id — a do queryset ``CaseEvent.Meta.ordering``), mesmo quando
    a ordem de inserção diverge dos timestamps."""
    case = Case.objects.create(created_by=nir_user)
    first_inserted = _add_event(case, CaseEventType.CASE_STATUS_PDF_EXTRACTING)
    second_inserted = _add_event(case, CaseEventType.CASE_STATUS_ANONYMIZING)
    now = timezone.now()
    CaseEvent.objects.filter(pk=first_inserted.pk).update(timestamp=now)
    CaseEvent.objects.filter(pk=second_inserted.pk).update(timestamp=now - timedelta(minutes=5))

    trail = build_event_trail_context(case)

    assert [item["event_type"] for item in trail] == [
        CaseEventType.CASE_STATUS_ANONYMIZING,
        CaseEventType.CASE_STATUS_PDF_EXTRACTING,
    ]


@pytest.mark.django_db
def test_build_event_trail_context_projects_label_badge_role_timestamp(
    nir_user: User,
) -> None:
    """R1: cada item projeta rótulo, badge (consciente do payload), papel e
    timestamp localizado — paridade com o contexto inline do painel."""
    case = Case.objects.create(created_by=nir_user)
    event = _add_event(
        case,
        CaseEventType.CASE_POLICY_EVALUATED,
        actor_role="nir",
        payload={"status": "FAILED"},
    )

    (item,) = build_event_trail_context(case)

    assert item["event_type"] == CaseEventType.CASE_POLICY_EVALUATED
    assert item["label"] == event_label(CaseEventType.CASE_POLICY_EVALUATED)
    assert item["badge_css"] == event_badge_css(
        CaseEventType.CASE_POLICY_EVALUATED, {"status": "FAILED"}
    )
    assert item["badge_css"] == "text-bg-danger"
    assert item["actor_role"] == "nir"
    assert item["timestamp"] == timezone.localtime(event.timestamp)
    assert item["details"] == event_detail_lines(event)


@pytest.mark.django_db
def test_build_event_trail_context_shows_system_without_actor(nir_user: User) -> None:
    """R1: evento sem ator exibe «Sistema» (fallback do painel)."""
    case = Case.objects.create(created_by=nir_user)
    _add_event(case, CaseEventType.CASE_STATUS_ANONYMIZING)

    (item,) = build_event_trail_context(case)

    assert item["actor_display"] == "Sistema"


@pytest.mark.django_db
def test_build_event_trail_context_shows_actor_display_name(nir_user: User) -> None:
    """R1: evento com ator exibe o ``display_name`` do usuário."""
    nir_user.first_name = "Ana"
    nir_user.last_name = "Nir"
    nir_user.save(update_fields=["first_name", "last_name"])
    case = Case.objects.create(created_by=nir_user)
    _add_event(case, CaseEventType.CASE_PROCEDURES_DECLARED, actor=nir_user, actor_role="nir")

    (item,) = build_event_trail_context(case)

    assert item["actor_display"] == "Ana Nir"


@pytest.mark.django_db
def test_build_event_trail_context_projects_review_details(nir_user: User) -> None:
    """R1: eventos de revisão projetam as linhas de detalhe (payload, sem PHI
    bruto); demais tipos projetam lista vazia."""
    case = Case.objects.create(created_by=nir_user)
    _add_event(case, CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED)
    _add_event(case, CaseEventType.CASE_STATUS_ANONYMIZING)

    trail = build_event_trail_context(case)

    assert trail[0]["details"] == ["Caso sob o contrato de revisão de procedimento do NIR."]
    assert trail[1]["details"] == []


@pytest.mark.django_db
def test_build_event_trail_context_uses_single_query(
    nir_user: User, django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    """R1: o builder resolve a trilha em UMA query (``select_related("actor")``,
    sem N+1 por evento)."""
    case = Case.objects.create(created_by=nir_user)
    _add_event(case, CaseEventType.CASE_STATUS_ANONYMIZING)
    _add_event(
        case,
        CaseEventType.CASE_PROCEDURES_DECLARED,
        actor=nir_user,
        actor_role="nir",
    )

    with django_assert_num_queries(1):
        trail = build_event_trail_context(case)

    assert len(trail) == 2


# ── R4: anti-drift migrado (ex-``test_event_labels.py`` do dashboard) ───────


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


def test_cleaned_label_is_truthful_in_both_modes() -> None:
    """ADR-0010/D9: ``CASE_STATUS_CLEANED`` é "Caso concluído" — verdadeiro
    na retenção e na minimização (sem afirmar minimização)."""
    assert event_label(CaseEventType.CASE_STATUS_CLEANED) == "Caso concluído"


def test_purged_retained_data_label_and_badge() -> None:
    """Slice 003 (retencao, R1): a purga retroativa exibe rótulo legível e
    badge de sistema."""
    assert event_label(CaseEventType.CASE_RETAINED_DATA_PURGED) == "Dados retidos purgados"
    assert event_badge_css(CaseEventType.CASE_RETAINED_DATA_PURGED) == "text-bg-secondary"


def test_purge_outcome_label_and_payload_aware_badge() -> None:
    """Slice 003 (retencao, R1, P2): o desfecho físico exibe rótulo distinto e
    badge CONSCIENTE DO PAYLOAD — perigo quando houve falha, neutro quando 0."""
    assert event_label(CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME) == (
        "Remoção dos arquivos da purga"
    )
    assert (
        event_badge_css(
            CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME,
            {"files_deleted": 0, "files_failed": 1},
        )
        == "text-bg-danger"
    )
    assert (
        event_badge_css(
            CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME,
            {"files_deleted": 1, "files_failed": 0},
        )
        == "text-bg-secondary"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"files_deleted": 0, "files_failed": 2}, "text-bg-danger"),
        ({"files_deleted": 2, "files_failed": 0}, "text-bg-secondary"),
        (None, "text-bg-secondary"),
        ({}, "text-bg-secondary"),
    ],
)
def test_purge_outcome_badge_is_payload_aware(
    payload: dict[str, object] | None, expected: str
) -> None:
    """P2: badge do outcome reflete ``files_failed`` — purga incompleta é
    visível em vermelho na trilha, desfecho completo fica neutro."""
    assert event_badge_css(CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME, payload) == expected


def test_cleaning_label_is_truthful_in_both_modes() -> None:
    """ADR-0010/D9 (P2-3): ``CASE_STATUS_CLEANING`` é neutro nos dois modos
    (sem afirmar limpeza que pode não ocorrer em retenção)."""
    assert event_label(CaseEventType.CASE_STATUS_CLEANING) == ("Disposição dos dados em andamento")


@pytest.mark.parametrize(
    "event_type",
    [CaseEventType.CASE_STATUS_FAILED, CaseEventType.CASE_ATTACHMENT_FAILED],
)
def test_failed_event_types_use_danger_badge(event_type: str) -> None:
    """R1: tipo ``*_FAILED`` marca o evento em vermelho."""
    assert event_badge_css(event_type) == "text-bg-danger"


def test_enqueue_failed_label_and_badge() -> None:
    """Slice 003 (confirm-nir): a falha de enqueue pós-confirmação tem
    rótulo legível e badge de falha (observável na trilha do painel)."""
    assert event_label(CaseEventType.CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED) == (
        "Falha ao enfileirar reprocessamento"
    )
    assert (
        event_badge_css(CaseEventType.CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED) == "text-bg-danger"
    )


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
