"""Testes das métricas do painel gerencial (slice 003, R1/R5).

Cobre ``apps/dashboard/metrics.py`` (serviços puros, sem request): resumo do
período com a regra de desfecho IMUTÁVEL (população por ``created_at``;
outcome = ``payload["source"]`` do ÚLTIMO evento ``CASE_STATUS_FINAL_REPLY_POSTED``,
contado apenas quando o status atual é pós-final — caso reaberto ao pipeline
conta como em andamento), contagem por tipo/unidade, tempo médio até decisão
médica e bounds de período. Os casos são levados aos estados pelas operações
públicas da FSM (mesmo padrão dos testes de closure/scheduler).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import (
    Case,
    CaseProcedure,
    CaseStatus,
    DoctorDisposition,
    SchedulingUnit,
)
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.dashboard.metrics import (
    _period_bounds,
    compute_avg_time_to_decision,
    compute_by_procedure_type,
    compute_by_unit,
    compute_summary,
    format_duration,
    local_day_bounds,
)

SYSTEM_ROLE = "system"

# Tipos representativos do catálogo nas fixtures.
ART_PERIF = "art_perif"
FLEBOGRAFIA = "flebografia"
NEFROSTOMIA = "nefrostomia"

EMPTY_SUMMARY = {
    "total": 0,
    "agendados": 0,
    "negados": 0,
    "em_andamento": 0,
    "encerrados": 0,
}


def _make_user(username: str) -> User:
    """Criador dos casos das fixtures (sem papel — métricas não dependem dele)."""
    return User.objects.create_user(username=username, password="senha-teste")


def _drive_to_awaiting_doctor(case: Case) -> Case:
    """Leva o caso do pipeline (NEW) até ``AWAITING_DOCTOR``."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    return case


def _final_by_doctor_denial(case: Case) -> Case:
    """Negativa médica até ``FINAL_REPLY_POSTED`` (source ``DOCTOR_DENIED``)."""
    _drive_to_awaiting_doctor(case)
    case.record_doctor_decision(accepted=False, user=None, role=SYSTEM_ROLE)
    case.post_final_reply(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


def _final_by_scheduling_confirmed(case: Case, unit: int) -> Case:
    """Agendamento confirmado até ``FINAL_REPLY_POSTED`` (source ``SCHEDULING_CONFIRMED``)."""
    _drive_to_awaiting_doctor(case)
    case.record_doctor_decision(accepted=True, user=None, role=SYSTEM_ROLE)
    case.request_scheduling(user=None, role=SYSTEM_ROLE)
    case.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    case.confirm_scheduling(user=None, role=SYSTEM_ROLE)
    case.scheduled_unit = unit
    case.save(update_fields=["scheduled_unit"])
    case.post_final_reply(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


def _final_by_scheduling_denied(case: Case) -> Case:
    """Agendamento negado até ``FINAL_REPLY_POSTED`` (source ``SCHEDULING_DENIED``)."""
    _drive_to_awaiting_doctor(case)
    case.record_doctor_decision(accepted=True, user=None, role=SYSTEM_ROLE)
    case.request_scheduling(user=None, role=SYSTEM_ROLE)
    case.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    case.deny_scheduling(user=None, role=SYSTEM_ROLE)
    case.post_final_reply(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


def _cleaned_case(case: Case, unit: int) -> Case:
    """Caso encerrado (``CLEANED``) pelo agendamento confirmado na unidade."""
    _final_by_scheduling_confirmed(case, unit)
    case.nir_acknowledge(user=None, role=SYSTEM_ROLE)
    case.start_cleaning(user=None, role=SYSTEM_ROLE)
    case.complete_cleaning(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.CLEANED
    return case


def _reopened_case(case: Case) -> Case:
    """Caso confirmado na unidade 1 e reaberto por intercorrência (volta ao pipeline)."""
    _final_by_scheduling_confirmed(case, SchedulingUnit.UNIT_1)
    case.reopen_scheduling(reason="vaga desmarcada pela unidade", user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_SCHEDULING
    return case


def _coherent_population() -> None:
    """Cria a população-alvo da R5: 2 agendados + 2 negados + 2 em andamento."""
    creator = _make_user("nir-coerente")
    _final_by_scheduling_confirmed(Case.objects.create(created_by=creator), SchedulingUnit.UNIT_1)
    _final_by_scheduling_confirmed(Case.objects.create(created_by=creator), SchedulingUnit.UNIT_2)
    _final_by_doctor_denial(Case.objects.create(created_by=creator))
    _final_by_scheduling_denied(Case.objects.create(created_by=creator))
    Case.objects.create(created_by=creator)  # NEW — em andamento
    _drive_to_awaiting_doctor(Case.objects.create(created_by=creator))  # em andamento


# ── R1: resumo do período ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_summary_counts() -> None:
    """R5: resumo coerente (2 agendados, 1 negado médico, 1 negado agendamento, 2 em andamento)."""
    _coherent_population()

    summary = compute_summary("hoje")

    assert summary == {
        "total": 6,
        "agendados": 2,
        "negados": 2,
        "em_andamento": 2,
        "encerrados": 0,
    }


@pytest.mark.django_db
def test_summary_counts_both_negation_sources() -> None:
    """R1: negados = DOCTOR_DENIED ∪ SCHEDULING_DENIED (mesma contagem do resumo)."""
    creator = _make_user("nir-negados")
    _final_by_doctor_denial(Case.objects.create(created_by=creator))
    _final_by_scheduling_denied(Case.objects.create(created_by=creator))
    _drive_to_awaiting_doctor(Case.objects.create(created_by=creator))

    summary = compute_summary("hoje")

    assert summary["total"] == 3
    assert summary["negados"] == 2
    assert summary["agendados"] == 0
    assert summary["em_andamento"] == 1


@pytest.mark.django_db
def test_summary_reopened_case_counts_as_in_progress() -> None:
    """R5/P1: caso reaberto por intercorrência conta como EM ANDAMENTO apesar do evento final anterior."""
    creator = _make_user("nir-reaberto")
    case = _reopened_case(Case.objects.create(created_by=creator))

    # Há um evento final anterior com source SCHEDULING_CONFIRMED na trilha —
    # o desfecho NÃO o usa porque o status atual voltou ao pipeline.
    final_sources = list(
        case.events.filter(event_type="CASE_STATUS_FINAL_REPLY_POSTED").values_list(
            "payload__source", flat=True
        )
    )
    assert final_sources == ["SCHEDULING_CONFIRMED"]

    summary = compute_summary("hoje")

    assert summary == {
        "total": 1,
        "agendados": 0,
        "negados": 0,
        "em_andamento": 1,
        "encerrados": 0,
    }


@pytest.mark.django_db
def test_summary_cleaned_case_kept_by_outcome() -> None:
    """R5: caso CLEANED continua contado pelo outcome (nada depende só do status FSM final)."""
    creator = _make_user("nir-limpo")
    _cleaned_case(Case.objects.create(created_by=creator), SchedulingUnit.UNIT_1)

    summary = compute_summary("hoje")

    assert summary == {
        "total": 1,
        "agendados": 1,
        "negados": 0,
        "em_andamento": 0,
        "encerrados": 1,
    }


@pytest.mark.django_db
def test_summary_period_boundaries() -> None:
    """R5: caso de ontem fica fora de ``hoje`` e dentro de ``7d``/``tudo``."""
    creator = _make_user("nir-periodo")
    case = _final_by_scheduling_confirmed(
        Case.objects.create(created_by=creator), SchedulingUnit.UNIT_1
    )
    Case.objects.filter(pk=case.pk).update(created_at=timezone.now() - timedelta(days=1))

    assert compute_summary("hoje") == EMPTY_SUMMARY
    assert compute_summary("7d")["total"] == 1
    assert compute_summary("7d")["agendados"] == 1
    assert compute_summary("tudo")["total"] == 1


# ── R1: por tipo de procedimento ───────────────────────────────────────────


@pytest.mark.django_db
def test_by_type_uses_catalog_order_and_partial_decisions() -> None:
    """R1/R5: linhas na ordem canônica do catálogo, com decisões parciais por ``doctor_disposition``."""
    creator = _make_user("nir-tipos")
    case = Case.objects.create(created_by=creator)
    now = timezone.now()
    CaseProcedure.objects.create(
        case=case,
        procedure_type=ART_PERIF,
        declared_by_nir=True,
        doctor_disposition=DoctorDisposition.APPROVED,
        doctor_decided_at=now,
    )
    CaseProcedure.objects.create(
        case=case,
        procedure_type=FLEBOGRAFIA,
        declared_by_nir=True,
        doctor_disposition=DoctorDisposition.DENIED,
        doctor_decided_at=now,
    )
    CaseProcedure.objects.create(case=case, procedure_type=NEFROSTOMIA, declared_by_nir=True)

    rows = compute_by_procedure_type("hoje")

    assert [row["procedure_type"] for row in rows] == [
        profile.procedure_type for profile in PROCEDURE_PROFILES
    ]
    by_type = {row["procedure_type"]: row for row in rows}
    assert by_type[ART_PERIF]["label"] == "Arteriografia periférica"
    assert (by_type[ART_PERIF]["total"], by_type[ART_PERIF]["approved"]) == (1, 1)
    assert (by_type[ART_PERIF]["denied"], by_type[ART_PERIF]["pending"]) == (0, 0)
    assert (by_type[FLEBOGRAFIA]["total"], by_type[FLEBOGRAFIA]["denied"]) == (1, 1)
    assert (by_type[NEFROSTOMIA]["total"], by_type[NEFROSTOMIA]["pending"]) == (1, 1)
    assert by_type["permicath"]["total"] == 0


@pytest.mark.django_db
def test_by_type_counts_rows_not_cases() -> None:
    """R1/P2: a tabela por tipo conta ROWS de procedimento — somatório ≠ total de casos (esperado)."""
    creator = _make_user("nir-rows")
    case = Case.objects.create(created_by=creator)
    CaseProcedure.objects.create(case=case, procedure_type=ART_PERIF, declared_by_nir=True)
    CaseProcedure.objects.create(case=case, procedure_type=FLEBOGRAFIA, declared_by_nir=True)

    summary = compute_summary("hoje")
    rows = compute_by_procedure_type("hoje")

    assert summary["total"] == 1
    assert sum(row["total"] for row in rows) == 2


# ── R1: por unidade ────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_by_unit_counts_scheduled_units() -> None:
    """R5: unidade 1 e unidade 2 contam os casos com outcome agendado por ``scheduled_unit``."""
    _coherent_population()

    assert compute_by_unit("hoje") == {"unit_1": 1, "unit_2": 1}


@pytest.mark.django_db
def test_by_unit_ignores_reopened_case() -> None:
    """R1: caso reaberto (``scheduled_unit`` limpo) não conta como agendado."""
    creator = _make_user("nir-unidade-reaberto")
    _reopened_case(Case.objects.create(created_by=creator))

    assert compute_by_unit("hoje") == {"unit_1": 0, "unit_2": 0}


# ── R1: tempo médio até decisão médica ─────────────────────────────────────


@pytest.mark.django_db
def test_avg_time_to_decision() -> None:
    """R1/R5: média de ``max(doctor_decided_at) − created_at`` dos casos com row decidida no período."""
    creator = _make_user("nir-tempo")
    case = Case.objects.create(created_by=creator)
    decision = timezone.now()
    Case.objects.filter(pk=case.pk).update(created_at=decision - timedelta(hours=3, minutes=30))
    CaseProcedure.objects.create(
        case=case,
        procedure_type=ART_PERIF,
        declared_by_nir=True,
        doctor_disposition=DoctorDisposition.APPROVED,
        doctor_decided_at=decision,
    )

    assert compute_avg_time_to_decision("7d") == timedelta(hours=3, minutes=30)


@pytest.mark.django_db
def test_avg_time_uses_latest_row_decision() -> None:
    """R1: usa o MAIOR ``doctor_decided_at`` entre as rows decididas do caso (a mais recente)."""
    creator = _make_user("nir-tempo-max")
    case = Case.objects.create(created_by=creator)
    first = timezone.now() - timedelta(hours=5)
    latest = timezone.now() - timedelta(hours=2)
    Case.objects.filter(pk=case.pk).update(created_at=latest - timedelta(hours=1))
    CaseProcedure.objects.create(
        case=case,
        procedure_type=ART_PERIF,
        declared_by_nir=True,
        doctor_disposition=DoctorDisposition.APPROVED,
        doctor_decided_at=first,
    )
    CaseProcedure.objects.create(
        case=case,
        procedure_type=FLEBOGRAFIA,
        declared_by_nir=True,
        doctor_disposition=DoctorDisposition.DENIED,
        doctor_decided_at=latest,
    )

    assert compute_avg_time_to_decision("7d") == timedelta(hours=1)


@pytest.mark.django_db
def test_avg_time_none_without_decisions() -> None:
    """R1: sem rows decididas no período → ``None``."""
    creator = _make_user("nir-sem-tempo")
    case = Case.objects.create(created_by=creator)
    CaseProcedure.objects.create(case=case, procedure_type=ART_PERIF, declared_by_nir=True)

    assert compute_avg_time_to_decision("hoje") is None


def test_format_duration_humanizes() -> None:
    """R2: duração humanizada ("3h 42m") e ausência como travessão ("—")."""
    assert format_duration(timedelta(hours=3, minutes=42)) == "3h 42m"
    assert format_duration(timedelta(minutes=42)) == "42m"
    assert format_duration(None) == "—"


# ── R1: bounds de período ──────────────────────────────────────────────────


def test_period_bounds_today_is_local_day() -> None:
    """R1: ``hoje`` = dia local [00:00, +1d)."""
    assert _period_bounds("hoje") == local_day_bounds()


def test_period_bounds_all_is_open() -> None:
    """R1: ``tudo`` = sem filtro temporal (None, None)."""
    assert _period_bounds("tudo") == (None, None)


def test_period_bounds_invalid_defaults_to_today() -> None:
    """R1/R2: período inválido cai em ``hoje`` (default seguro)."""
    assert _period_bounds("30d-medio") == local_day_bounds()


def test_period_bounds_windows_are_rolling() -> None:
    """R1: ``7d``/``30d`` são janelas rolling de 7/30 dias até agora."""
    now = timezone.now()
    start, end = _period_bounds("7d")
    assert end is not None and start is not None
    assert end - start == timedelta(days=7)
    assert (now - end).total_seconds() < 5

    start_30, end_30 = _period_bounds("30d")
    assert end_30 is not None and start_30 is not None
    assert end_30 - start_30 == timedelta(days=30)


@pytest.mark.django_db
def test_summary_two_final_events_last_source_wins() -> None:
    """R5/P1 (P2 review): caso PÓS-FINAL com DOIS eventos finais de sources
    distintos (confirmado → reaberto → negado) — o outcome é o do ÚLTIMO
    evento (``DISTINCT ON`` por ``-id``), sem dupla contagem."""
    creator = _make_user("nir-dois-finais")
    case = _reopened_case(Case.objects.create(created_by=creator))
    # Reaberto está em AWAITING_SCHEDULING: nega e publica a nova resposta.
    case.deny_scheduling(user=None, role=SYSTEM_ROLE)
    case.post_final_reply(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.FINAL_REPLY_POSTED

    final_sources = list(
        case.events.filter(event_type="CASE_STATUS_FINAL_REPLY_POSTED").values_list(
            "payload__source", flat=True
        )
    )
    assert final_sources == ["SCHEDULING_CONFIRMED", "SCHEDULING_DENIED"]

    summary = compute_summary("hoje")

    # Última resposta vence: negado 1× — nunca agendado+negado no mesmo caso.
    assert summary == {
        "total": 1,
        "agendados": 0,
        "negados": 1,
        "em_andamento": 0,
        "encerrados": 0,
    }
