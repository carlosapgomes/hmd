"""Histórico do agendador + carimbos de decisão de agendamento (slice 004).

Cobre os 6 cenários do delta ``scheduling`` («Histórico do agendador»: busca
por nome de caso antigo encerrado, busca por nº de outro decisor, negativa
médica encerrada fora do histórico, estado vazio sem critério, detalhe
histórico sem ações com gate de PDF, 403 não-agendador) + R1 (carimbos
``scheduling_decided_at``/``scheduling_decided_by`` na confirmação E na
negação, backfill por eventos, fail-closed de ator nulo, reabertura que
preserva o carimbo, re-decisão que atualiza).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from importlib import import_module
from typing import Any
from uuid import uuid4

import pytest
from django.apps.registry import Apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.closure import (
    POST_CLOSURE_ISSUE_STATUS_OPENED,
    acknowledge_case_receipt,
    open_post_closure_issue,
)
from apps.cases.events import CaseEventType
from apps.cases.models import (
    Case,
    CaseDocument,
    CaseEvent,
    CaseProcedure,
    CaseStatus,
    SchedulingUnit,
)
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.scheduler.services import (
    confirm_case_scheduling,
    deny_case_scheduling,
    reopen_scheduling_after_incident,
)

SCHEDULER_ROLE = "scheduler"
ADMIN_ROLE = "admin"
NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"

SEARCH_URL = "scheduler:historical_search"
DETAIL_URL = "scheduler:historical_case_detail"

CARDIO_TYPE = "cat_cardiaco"
ANGIO_TYPE = "art_perif"

REOPEN_REASON = "vaga desmarcada pela unidade de origem — nova data solicitada"


def _visible_text(body: str) -> str:
    """Corpo sem atributos de rota — o uid segue como chave técnica no href."""
    return re.sub(r'href="[^"]*"', "", body)


def _create_case_with_declared(created_by: User, procedure_types: Sequence[str]) -> Case:
    """Cria um caso com as rows declaradas (sem transições de status)."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )
    return case


def _run_processing(case: Case) -> None:
    """Dirige o caso pelo pipeline até ``AWAITING_DOCTOR``."""
    for operation in (
        "start_pdf_extraction",
        "complete_pdf_extraction",
        "complete_anonymization",
        "complete_llm_extraction",
        "complete_llm_summarization",
    ):
        getattr(case, operation)(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _awaiting_scheduling_case(
    created_by: User,
    decided_by: User,
    procedure_types: Sequence[str],
) -> Case:
    """Caso em ``AWAITING_SCHEDULING`` (decisão médica com ≥1 aprovado)."""
    case = _create_case_with_declared(created_by, procedure_types)
    _run_processing(case)
    record_doctor_procedure_decisions(
        case,
        {procedure_type: ("approved", "") for procedure_type in procedure_types},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    case.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_SCHEDULING
    return case


def _future_datetime(**kwargs: int) -> datetime:
    """Data/hora aware futura (UTC) a partir de agora."""
    return timezone.now() + timedelta(**kwargs)


def _confirm(
    case: Case,
    scheduler: User,
    *,
    unit: int = SchedulingUnit.UNIT_1,
    location: str = "HGRS — Hemodinâmica",
) -> None:
    """Confirma o agendamento do caso via serviço transacional."""
    confirm_case_scheduling(
        case,
        unit=unit,
        scheduled_datetime=_future_datetime(days=2),
        scheduled_location=location,
        user=scheduler,
        role=SCHEDULER_ROLE,
    )


def _decided_attrs(case: Case) -> tuple[Any, Any]:
    """Par de carimbos de decisão de agendamento (atributo dinâmico pré-migration)."""
    return (
        getattr(case, "scheduling_decided_at"),
        getattr(case, "scheduling_decided_by_id"),
    )


# ── R1: confirmação nova carimba o par ────────────────────────────────────


@pytest.mark.django_db
def test_confirm_stamps_decided_pair(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """A confirmação grava ``scheduling_decided_at`` + ``by`` no mesmo atomic."""
    doctor = user_factory("medico-confirma-carimbo", ("doctor",))
    scheduler = user_factory("agendador-confirma-carimbo", ("scheduler",))
    case = _awaiting_scheduling_case(nir_user, doctor, [CARDIO_TYPE])

    _confirm(case, scheduler)

    fresh = Case.objects.get(pk=case.pk)
    decided_at, decided_by_id = _decided_attrs(fresh)
    assert decided_at is not None
    assert decided_by_id == scheduler.pk
    assert abs((timezone.now() - decided_at).total_seconds()) < 120


# ── R1: negação nova carimba o par (hoje não carimba nada) ─────────────────


@pytest.mark.django_db
def test_deny_stamps_decided_pair(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """A negação passa a carimbar decisor+timestamp (antes: só o motivo)."""
    doctor = user_factory("medico-nega-carimbo", ("doctor",))
    scheduler = user_factory("agendador-nega-carimbo", ("scheduler",))
    case = _awaiting_scheduling_case(nir_user, doctor, [CARDIO_TYPE])

    deny_case_scheduling(case, reason="sem vaga na unidade", user=scheduler, role=SCHEDULER_ROLE)

    fresh = Case.objects.get(pk=case.pk)
    decided_at, decided_by_id = _decided_attrs(fresh)
    assert decided_at is not None
    assert decided_by_id == scheduler.pk
    assert fresh.scheduling_denial_reason == "sem vaga na unidade"


# ── R1: re-decisão atualiza o carimbo ─────────────────────────────────────


@pytest.mark.django_db
def test_redecision_updates_decided_pair(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """Reaberto pré-ciência e re-confirmado por outro agendador: o carimbo
    acompanha a ÚLTIMA decisão (ator + instante novos)."""
    doctor = user_factory("medico-redecide", ("doctor",))
    first = user_factory("agendador-primeiro", ("scheduler",))
    second = user_factory("agendador-segundo", ("scheduler",))
    case = _awaiting_scheduling_case(nir_user, doctor, [CARDIO_TYPE])
    _confirm(case, first)
    reopen_scheduling_after_incident(case, reason=REOPEN_REASON, user=first, role=SCHEDULER_ROLE)

    _confirm(Case.objects.get(pk=case.pk), second)

    fresh = Case.objects.get(pk=case.pk)
    decided_at, decided_by_id = _decided_attrs(fresh)
    assert decided_by_id == second.pk
    assert decided_at is not None
    assert abs((timezone.now() - decided_at).total_seconds()) < 120


# ── R1: reabertura NÃO zera o carimbo ─────────────────────────────────────


@pytest.mark.django_db
def test_reopen_preserves_decided_stamps(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """A intercorrência limpa os 5 campos de agendamento corrente mas mantém
    ``scheduling_decided_*`` (fato histórico «passou por decisão»)."""
    doctor = user_factory("medico-reabre", ("doctor",))
    scheduler = user_factory("agendador-reabre", ("scheduler",))
    case = _awaiting_scheduling_case(nir_user, doctor, [CARDIO_TYPE])
    _confirm(case, scheduler)
    before = Case.objects.get(pk=case.pk)
    before_at, before_by = _decided_attrs(before)
    assert before_at is not None

    reopen_scheduling_after_incident(
        case, reason=REOPEN_REASON, user=scheduler, role=SCHEDULER_ROLE
    )

    fresh = Case.objects.get(pk=case.pk)
    assert fresh.status == CaseStatus.AWAITING_SCHEDULING
    # Os 5 campos do agendamento corrente são limpos normalmente.
    assert fresh.scheduled_unit is None
    assert fresh.scheduled_datetime is None
    assert fresh.scheduled_location == ""
    assert fresh.scheduled_by_id is None
    assert fresh.scheduled_decided_at is None
    # ... mas o carimbo histórico permanece.
    after_at, after_by = _decided_attrs(fresh)
    assert after_at == before_at
    assert after_by == before_by


# ── R1: backfill por eventos (legado aparece no histórico) ────────────────


def _backfill(apps: Apps) -> None:
    """Executa a função de backfill da migration 0013."""
    migration = import_module("apps.cases.migrations.0013_scheduling_decided_stamps")
    migration.backfill_scheduling_decided(apps, None)


@pytest.mark.django_db
def test_backfill_covers_legacy_denied_case(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """Legado negado (só evento, sem carimbo): o backfill preenche o par pelo
    ÚLTIMO evento CONFIRMED|DENIED — o caso entra no universo do histórico."""
    from django.apps import apps as django_apps

    scheduler = user_factory("agendador-legado", ("scheduler",))
    case = Case.objects.create(
        created_by=nir_user,
        status=CaseStatus.FINAL_REPLY_POSTED,
        agency_record_number="5040701",
        patient_name="Legado Negado",
        scheduling_denial_reason="sem vaga",
    )
    CaseEvent.objects.filter(case=case).delete()
    denied_event = CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_STATUS_SCHEDULING_DENIED,
        actor_type="user",
        actor=scheduler,
        actor_role=SCHEDULER_ROLE,
        payload={"source": "AWAITING_SCHEDULING", "target": "SCHEDULING_DENIED"},
    )

    _backfill(django_apps)

    fresh = Case.objects.get(pk=case.pk)
    decided_at, decided_by_id = _decided_attrs(fresh)
    assert decided_at == denied_event.timestamp
    assert decided_by_id == scheduler.pk
    # Universo do histórico: carimbo preenchido.
    assert Case.objects.filter(scheduling_decided_at__isnull=False).filter(pk=fresh.pk).exists()


@pytest.mark.django_db
def test_backfill_uses_latest_decision_event(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """Com confirmação E negação na trilha, o backfill usa o ÚLTIMO evento."""
    from django.apps import apps as django_apps

    first = user_factory("agendador-antigo", ("scheduler",))
    last = user_factory("agendador-ultimo", ("scheduler",))
    case = Case.objects.create(
        created_by=nir_user,
        status=CaseStatus.FINAL_REPLY_POSTED,
        agency_record_number="5040702",
        patient_name="Legado Duplo",
    )
    CaseEvent.objects.filter(case=case).delete()
    CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_STATUS_SCHEDULING_CONFIRMED,
        actor_type="user",
        actor=first,
        actor_role=SCHEDULER_ROLE,
        payload={"source": "AWAITING_SCHEDULING", "target": "SCHEDULING_CONFIRMED"},
    )
    latest = CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_STATUS_SCHEDULING_DENIED,
        actor_type="user",
        actor=last,
        actor_role=SCHEDULER_ROLE,
        payload={"source": "AWAITING_SCHEDULING", "target": "SCHEDULING_DENIED"},
    )

    _backfill(django_apps)

    fresh = Case.objects.get(pk=case.pk)
    decided_at, decided_by_id = _decided_attrs(fresh)
    assert decided_at == latest.timestamp
    assert decided_by_id == last.pk


@pytest.mark.django_db
def test_backfill_fail_closed_null_actor(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """Evento de decisão sem ator (nulo): fail-closed — carimbos seguem nulos,
    o caso fica fora do universo do histórico."""
    from django.apps import apps as django_apps

    case = Case.objects.create(
        created_by=nir_user,
        status=CaseStatus.FINAL_REPLY_POSTED,
        agency_record_number="5040703",
        patient_name="Legado Sem Ator",
    )
    CaseEvent.objects.filter(case=case).delete()
    CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_STATUS_SCHEDULING_CONFIRMED,
        actor_type="system",
        actor=None,
        actor_role="",
        payload={"source": "AWAITING_SCHEDULING", "target": "SCHEDULING_CONFIRMED"},
    )

    _backfill(django_apps)

    fresh = Case.objects.get(pk=case.pk)
    decided_at, decided_by_id = _decided_attrs(fresh)
    assert decided_at is None
    assert decided_by_id is None


@pytest.mark.django_db
def test_backfill_without_decision_event_stays_null(nir_user: User) -> None:
    """Sem evento de decisão de agendamento: carimbos seguem nulos."""
    from django.apps import apps as django_apps

    case = Case.objects.create(
        created_by=nir_user,
        status=CaseStatus.CLEANED,
        agency_record_number="5040704",
        patient_name="Sem Decisão",
    )
    CaseEvent.objects.filter(case=case).delete()

    _backfill(django_apps)

    fresh = Case.objects.get(pk=case.pk)
    decided_at, decided_by_id = _decided_attrs(fresh)
    assert decided_at is None
    assert decided_by_id is None


# ── R2: universo da busca ─────────────────────────────────────────────────


def _decided_case(
    created_by: User,
    decided_by: User,
    *,
    record_number: str,
    patient_name: str,
    status: str = CaseStatus.CLEANED,
) -> Case:
    """Caso com carimbo de decisão preenchido (legado encerrado ou ativo)."""
    case = Case.objects.create(
        created_by=created_by,
        status=status,
        agency_record_number=record_number,
        patient_name=patient_name,
        scheduling_decided_at=timezone.now() - timedelta(days=9),
        scheduling_decided_by=decided_by,
    )
    return case


@pytest.mark.django_db
def test_search_by_name_finds_old_cleaned_case(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Busca por nome encontra caso CLEANED decidido há dias, com referência
    humana, status legível e dados do agendamento anterior — sem o uid."""
    other_scheduler = user_factory("agendador-outro-nome", ("scheduler",))
    scheduler = user_factory("agendador-busca-nome", ("scheduler",))
    case = _decided_case(
        nir_user,
        other_scheduler,
        record_number="5040711",
        patient_name="Zuleica Histórico Antiga",
        status=CaseStatus.CLEANED,
    )
    scheduled_at = timezone.now() - timedelta(days=8, hours=2)
    scheduled_location = "HGRS — Hemodinâmica"
    Case.objects.filter(pk=case.pk).update(
        scheduled_datetime=scheduled_at,
        scheduled_location=scheduled_location,
    )

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse(SEARCH_URL), {"q": "zuleica"})

    assert response.status_code == 200
    assert response.context is not None
    assert [item["case"].pk for item in response.context["results"]] == [case.pk]
    item = response.context["results"][0]
    assert item["scheduled_datetime"] == scheduled_at
    assert item["scheduled_location"] == scheduled_location
    body = response.content.decode()
    assert "Zuleica Histórico Antiga" in body
    assert "5040711" in body
    assert timezone.localtime(scheduled_at).strftime("%d/%m/%Y %H:%M") in body
    assert scheduled_location in body
    assert str(case.case_id) not in _visible_text(body)
    assert reverse(DETAIL_URL, args=[case.case_id]) in body


@pytest.mark.django_db
def test_search_by_number_finds_other_decisors_case(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Busca pelo nº encontra casos decididos por OUTRO agendador."""
    other_scheduler = user_factory("agendador-outro-numero", ("scheduler",))
    scheduler = user_factory("agendador-busca-numero", ("scheduler",))
    by_number = _decided_case(
        nir_user, other_scheduler, record_number="7770031", patient_name="Nome Comum A"
    )
    _decided_case(nir_user, other_scheduler, record_number="7770032", patient_name="Nome Comum B")

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse(SEARCH_URL), {"q": "7770031"})

    assert [item["case"].pk for item in response.context["results"]] == [by_number.pk]


@pytest.mark.django_db
def test_search_matches_either_number_or_name(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """O termo casa nº de ocorrência OU nome do paciente."""
    other_scheduler = user_factory("agendador-ou", ("scheduler",))
    scheduler = user_factory("agendador-busca-ou", ("scheduler",))
    by_number = _decided_case(
        nir_user, other_scheduler, record_number="7770041", patient_name="Nome Comum A"
    )
    by_name = _decided_case(
        nir_user, other_scheduler, record_number="7770042", patient_name="Termo Raro B"
    )

    login_user(scheduler, SCHEDULER_ROLE)
    by_number_response = client.get(reverse(SEARCH_URL), {"q": "7770041"})
    by_name_response = client.get(reverse(SEARCH_URL), {"q": "raro"})

    assert [item["case"].pk for item in by_number_response.context["results"]] == [by_number.pk]
    assert [item["case"].pk for item in by_name_response.context["results"]] == [by_name.pk]


@pytest.mark.django_db
def test_medical_denial_cleaned_not_in_history(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Negativa médica encerrada (nunca passou por decisão de agendamento):
    fora do histórico pelo nº E pelo nome."""
    scheduler = user_factory("agendador-busca-negmed", ("scheduler",))
    Case.objects.create(
        created_by=nir_user,
        status=CaseStatus.CLEANED,
        agency_record_number="5040721",
        patient_name="Negativa Medica Unica",
    )

    login_user(scheduler, SCHEDULER_ROLE)
    by_number = client.get(reverse(SEARCH_URL), {"q": "5040721"})
    by_name = client.get(reverse(SEARCH_URL), {"q": "negativa medica unica"})

    assert by_number.context["results"] == []
    assert by_name.context["results"] == []


@pytest.mark.django_db
def test_empty_state_without_criterion(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Sem termo: nenhum resultado e estado vazio orientando o uso da busca."""
    scheduler = user_factory("agendador-estado-vazio", ("scheduler",))

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse(SEARCH_URL))

    assert response.status_code == 200
    assert response.context["results"] == []
    assert response.context["query"] == ""
    assert "Informe um nº de ocorrência ou nome do paciente para buscar." in (
        response.content.decode()
    )


@pytest.mark.django_db
def test_issue_active_case_in_history_universe(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Caso com intercorrência ativa (fora de CLEANED) segue no histórico."""
    other_scheduler = user_factory("agendador-issue", ("scheduler",))
    scheduler = user_factory("agendador-busca-issue", ("scheduler",))
    case = _decided_case(
        nir_user,
        other_scheduler,
        record_number="8880051",
        patient_name="Paciente Reaberto Vivo",
        status=CaseStatus.AWAITING_SCHEDULING,
    )
    Case.objects.filter(pk=case.pk).update(
        post_schedule_issue_status=POST_CLOSURE_ISSUE_STATUS_OPENED
    )

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse(SEARCH_URL), {"q": "8880051"})

    assert [item["case"].pk for item in response.context["results"]] == [case.pk]


@pytest.mark.django_db
def test_reopened_case_in_history_with_neutral_badge(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Confirmado → reaberto via serviço: segue no histórico com badge neutro
    de reabertura (nunca «confirmado»), carimbo preservado e motivo de
    intercorrência visível."""
    doctor = user_factory("medico-reaberto-hist", ("doctor",))
    scheduler = user_factory("agendador-reaberto-hist", ("scheduler",))
    case = _awaiting_scheduling_case(nir_user, doctor, [CARDIO_TYPE])
    Case.objects.filter(pk=case.pk).update(
        agency_record_number="5040761",
        patient_name="Paciente Reaberto Historico",
    )
    _confirm(Case.objects.get(pk=case.pk), scheduler)
    before = Case.objects.get(pk=case.pk)
    before_at, before_by = _decided_attrs(before)
    assert before_at is not None

    reopen_scheduling_after_incident(
        Case.objects.get(pk=case.pk), reason=REOPEN_REASON, user=scheduler, role=SCHEDULER_ROLE
    )

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse(SEARCH_URL), {"q": "5040761"})

    assert response.status_code == 200
    assert [item["case"].pk for item in response.context["results"]] == [case.pk]
    item = response.context["results"][0]
    assert item["is_reopened"] is True
    after_at, after_by = _decided_attrs(Case.objects.get(pk=case.pk))
    assert after_at == before_at
    assert after_by == before_by
    body = response.content.decode()
    assert "Agendamento reaberto" in body
    assert "Agendamento confirmado" not in body
    assert REOPEN_REASON in body


@pytest.mark.django_db
def test_real_path_cleaned_reopened_in_history_universe(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Caminho REAL até a issue pós-encerramento (P2 do slice 004): confirmação
    → ciência → reabertura pelo serviço — sem estado fabricado.

    O caso aparece no universo do histórico com issue ativa e o carimbo
    ``scheduling_decided_*`` da confirmação preservado."""
    doctor = user_factory("medico-caminho-real", ("doctor",))
    scheduler = user_factory("agendador-caminho-real", ("scheduler",))
    case = _awaiting_scheduling_case(nir_user, doctor, [CARDIO_TYPE])
    Case.objects.filter(pk=case.pk).update(
        agency_record_number="5040801",
        patient_name="Paciente Caminho Real",
    )
    _confirm(Case.objects.get(pk=case.pk), scheduler)
    confirmed = Case.objects.get(pk=case.pk)
    assert confirmed.status == CaseStatus.FINAL_REPLY_POSTED
    confirmed_at, confirmed_by = _decided_attrs(confirmed)
    assert confirmed_at is not None

    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False):
        acknowledge_case_receipt(confirmed, user=nir_user, role=NIR_ROLE)
    cleaned = Case.objects.get(pk=case.pk)
    assert cleaned.status == CaseStatus.CLEANED
    assert cleaned.clinical_data_disposed_at is None

    open_post_closure_issue(cleaned, reason=REOPEN_REASON, user=nir_user, role=NIR_ROLE)

    reopened = Case.objects.get(pk=case.pk)
    assert reopened.status == CaseStatus.AWAITING_SCHEDULING
    assert reopened.post_schedule_issue_status == POST_CLOSURE_ISSUE_STATUS_OPENED
    reopened_at, reopened_by = _decided_attrs(reopened)
    assert reopened_at == confirmed_at
    assert reopened_by == confirmed_by

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse(SEARCH_URL), {"q": "5040801"})

    assert response.status_code == 200
    assert [item["case"].pk for item in response.context["results"]] == [case.pk]
    assert response.context["results"][0]["is_reopened"] is True
    body = response.content.decode()
    assert "Agendamento reaberto" in body
    assert REOPEN_REASON in body


@pytest.mark.django_db
def test_search_cap_50_newest_first(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """No máximo 50 resultados por consulta, mais novos primeiro."""
    from django.utils import timezone as tz

    other_scheduler = user_factory("agendador-cap", ("scheduler",))
    scheduler = user_factory("agendador-busca-cap", ("scheduler",))
    base = tz.now()
    for index in range(55):
        case = _decided_case(
            nir_user,
            other_scheduler,
            record_number=f"9900{index:03d}",
            patient_name=f"Paciente Cap Lote {index}",
        )
        Case.objects.filter(pk=case.pk).update(created_at=base - timedelta(minutes=index))

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse(SEARCH_URL), {"q": "Paciente Cap Lote"})

    assert len(response.context["results"]) == 50


@pytest.mark.django_db
def test_duplicated_record_number_gets_suffix(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Nº duplicado nos resultados recebe o sufixo curto de desambiguação."""
    other_scheduler = user_factory("agendador-dup", ("scheduler",))
    scheduler = user_factory("agendador-busca-dup", ("scheduler",))
    first = _decided_case(
        nir_user, other_scheduler, record_number="5040731", patient_name="Duplicado Alfa"
    )
    second = _decided_case(
        nir_user, other_scheduler, record_number="5040731", patient_name="Duplicado Beta"
    )

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse(SEARCH_URL), {"q": "5040731"})
    body = response.content.decode()

    assert {item["case"].pk for item in response.context["results"]} == {first.pk, second.pk}
    assert first.short_id in body
    assert second.short_id in body


# ── R3/R4: detalhe histórico em leitura ───────────────────────────────────


@pytest.mark.django_db
def test_historical_detail_readonly_without_actions(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Detalhe histórico: mesmo conteúdo do detalhe, SEM botões de
    confirmação/negação/desmarcação."""
    other_scheduler = user_factory("agendador-detalhe-outro", ("scheduler",))
    scheduler = user_factory("agendador-detalhe", ("scheduler",))
    case = _decided_case(
        nir_user,
        other_scheduler,
        record_number="5040741",
        patient_name="Paciente Detalhe Leitura",
        status=CaseStatus.FINAL_REPLY_POSTED,
    )

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse(DETAIL_URL, args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Paciente Detalhe Leitura" in body
    assert "Confirmar" not in body
    assert "Negar agendamento" not in body
    assert "Desmarcar agendamento" not in body
    assert "Sair sem decidir" not in body


@pytest.mark.django_db
def test_historical_detail_pdf_gate_own_scheduler(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """PDF/texto no detalhe histórico seguem o gate vigente: o PRÓPRIO
    agendador decisor com status permitido vê os cards."""
    doctor = user_factory("medico-pdf-proprio", ("doctor",))
    scheduler = user_factory("agendador-pdf-proprio", ("scheduler",))
    case = _awaiting_scheduling_case(nir_user, doctor, [CARDIO_TYPE])
    _confirm(case, scheduler)
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    CaseDocument.objects.create(
        case=case,
        file=SimpleUploadedFile("relatorio.pdf", b"%PDF-1.4 fake", content_type="application/pdf"),
        position=1,
        original_filename="relatorio.pdf",
        content_type="application/pdf",
        size_bytes=12,
        uploaded_by=nir_user,
    )

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse(DETAIL_URL, args=[case.case_id]))

    assert response.status_code == 200
    assert "Visualizar PDF Original" in response.content.decode()
    pdf_response = client.get(reverse("scheduler:case_pdf", args=[case.case_id, 1]))
    assert pdf_response.status_code == 200


@pytest.mark.django_db
def test_historical_detail_pdf_denied_other_scheduler(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Outro agendador (não decisor): PDF segue 404 fail-closed no gate."""
    doctor = user_factory("medico-pdf-alheio", ("doctor",))
    decider = user_factory("agendador-pdf-decisor", ("scheduler",))
    other = user_factory("agendador-pdf-alheio", ("scheduler",))
    case = _awaiting_scheduling_case(nir_user, doctor, [CARDIO_TYPE])
    _confirm(case, decider)
    case.refresh_from_db()
    CaseDocument.objects.create(
        case=case,
        file=SimpleUploadedFile("relatorio.pdf", b"%PDF-1.4 fake", content_type="application/pdf"),
        position=1,
        original_filename="relatorio.pdf",
        content_type="application/pdf",
        size_bytes=12,
        uploaded_by=nir_user,
    )

    login_user(other, SCHEDULER_ROLE)
    response = client.get(reverse(DETAIL_URL, args=[case.case_id]))

    assert response.status_code == 200
    assert "Visualizar PDF Original" not in response.content.decode()
    pdf_response = client.get(reverse("scheduler:case_pdf", args=[case.case_id, 1]))
    assert pdf_response.status_code == 404


@pytest.mark.django_db
def test_cleaned_pdf_same_decider_has_no_cards_and_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """CLEANED + PDF + mesmo decisor (P2 do slice 004): o detalhe histórico
    omite os cards de PDF/texto extraído e o endpoint do PDF responde 404 —
    ``CLEANED`` está fora de ``PDF_ALLOWED_STATUSES`` (gate fail-closed)."""
    doctor = user_factory("medico-pdf-encerrado", ("doctor",))
    scheduler = user_factory("agendador-pdf-encerrado", ("scheduler",))
    case = _awaiting_scheduling_case(nir_user, doctor, [CARDIO_TYPE])
    Case.objects.filter(pk=case.pk).update(
        agency_record_number="5040802",
        patient_name="Paciente PDF Encerrado",
        extracted_text="trecho do relatório original",
    )
    _confirm(Case.objects.get(pk=case.pk), scheduler)
    confirmed = Case.objects.get(pk=case.pk)
    assert confirmed.status == CaseStatus.FINAL_REPLY_POSTED
    CaseDocument.objects.create(
        case=confirmed,
        file=SimpleUploadedFile("relatorio.pdf", b"%PDF-1.4 fake", content_type="application/pdf"),
        position=1,
        original_filename="relatorio.pdf",
        content_type="application/pdf",
        size_bytes=12,
        uploaded_by=nir_user,
    )

    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False):
        acknowledge_case_receipt(confirmed, user=nir_user, role=NIR_ROLE)
    cleaned = Case.objects.get(pk=case.pk)
    assert cleaned.status == CaseStatus.CLEANED
    assert cleaned.documents.count() == 1

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse(DETAIL_URL, args=[cleaned.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Visualizar PDF Original" not in body
    assert "Texto Extraído do PDF" not in body
    assert "trecho do relatório original" not in body
    pdf_response = client.get(reverse("scheduler:case_pdf", args=[cleaned.case_id, 1]))
    assert pdf_response.status_code == 404


@pytest.mark.django_db
def test_historical_detail_404_without_decision_stamp(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Caso sem carimbo (negativa médica encerrada): detalhe histórico é 404,
    sem vazar conteúdo."""
    scheduler = user_factory("agendador-404", ("scheduler",))
    case = Case.objects.create(
        created_by=nir_user,
        status=CaseStatus.CLEANED,
        agency_record_number="5040751",
        patient_name="Sem Carimbo Invisivel",
    )

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse(DETAIL_URL, args=[case.case_id]))

    assert response.status_code == 404


# ── R5: papel não-agendador → 403 ─────────────────────────────────────────


@pytest.mark.django_db
def test_historical_search_forbidden_for_non_scheduler(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Papel ativo ``nir``/``doctor``/``manager`` recebe 403 na busca e no
    detalhe do histórico do agendador."""
    nir = user_factory("nir-403", ("nir",))
    doctor = user_factory("medico-403", ("doctor",))

    login_user(nir, NIR_ROLE)
    assert client.get(reverse(SEARCH_URL), {"q": "x"}).status_code == 403
    assert client.get(reverse(DETAIL_URL, args=[uuid4()])).status_code == 403

    login_user(doctor, DOCTOR_ROLE)
    assert client.get(reverse(SEARCH_URL), {"q": "x"}).status_code == 403
    assert client.get(reverse(DETAIL_URL, args=[uuid4()])).status_code == 403


# ── Navegação: entrada do histórico no _nav do scheduler ──────────────────


@pytest.mark.django_db
def test_historical_entry_in_scheduler_nav(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """A fila do agendador expõe link distinto para o histórico, sem quebrar
    as abas vigentes (Pendentes/Processados)."""
    scheduler = user_factory("agendador-nav", ("scheduler",))

    login_user(scheduler, SCHEDULER_ROLE)
    response = client.get(reverse("scheduler:queue"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Pendentes" in body
    assert "Processados" in body
    assert reverse(SEARCH_URL) in body
