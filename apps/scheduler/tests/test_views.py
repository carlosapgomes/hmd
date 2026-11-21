"""Testes de views do agendador (scheduler-multi-unit, slice 003, R1–R7).

Cobre:
- R1: urls sob ``/scheduler/`` + nav no ``base.html`` visível quando o papel
  ativo é ``scheduler``/``admin`` (R1);
- R2: fila ``scheduler:queue`` — guard ``role_required("scheduler", "admin")``
  (anônimo → redirect ao login; nir/doctor/manager → 403; composição
  ``scheduler+manager`` com papel ativo ``scheduler`` → 200), abas
  ``aguardando`` (default = ``SCHEDULER_REQUESTED`` **ou**
  ``AWAITING_SCHEDULING`` — inclui caso reaberto por intercorrência) e
  ``processados`` (= ``SCHEDULING_CONFIRMED|SCHEDULING_DENIED|
  FINAL_REPLY_POSTED``), ordenação por tempo de tela (``days_on_screen`` desc,
  desempate FIFO por ``created_at``), paginada, cards com
  identificação/tipos declarados/unidade quando definida e **sem filtro por
  unidade**;
- R3: detalhe ``scheduler:case_detail`` — identificação real
  (``patient_name``/``patient_birth_date``/``agency_record_number``),
  diagnóstico = sumário clínico COMPLETO (``summary_text`` inteiro,
  re-identificado **só na renderização**, quebras preservadas via ``linebreaks``), decisões
  médicas por procedimento (disposição + motivo das negativas), dados de
  agendamento atuais, thread de comunicações e **nenhum** conteúdo de
  structured_data/policy_result/suggested_action (assert de ausência com
  artefatos populados) nem PDF na fila/pré-decisão;
- R4: forms e POSTs de confirmar (unidade 1|2 + data/hora futura + local),
  negar (motivo obrigatório) e desmarcar (intercorrência — botão SÓ em
  ``FINAL_REPLY_POSTED`` com ``scheduled_unit == 1``; unidade 2 exibe banner
  "intercorrência desabilitada" e POST direto é recusado com erro);
- R7: ``scheduler:case_pdf`` serve os PDFs do ``CaseDocument`` por position
  (padrão do doctor do change 07 — decisão do dono/adaptação: o HMD é
  multi-PDF e não tem ``Case.pdf_file``) SOMENTE quando ``scheduled_by ==
  request.user`` E status ∈ {SCHEDULING_CONFIRMED, SCHEDULING_DENIED,
  FINAL_REPLY_POSTED, AWAITING_NIR_ACK} (404 fail-closed — inclusive caso
  reaberto por intercorrência); links de PDF no detalhe apenas nessa condição;
- R5: erros de serviço/estado/concorrência → mensagem + redirect, nunca 500,
  sem escrita parcial.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from typing import Any, cast

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock
from apps.cases.models import (
    ActorType,
    Case,
    CaseDocument,
    CaseEvent,
    CaseProcedure,
    CaseStatus,
    MessageType,
)
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.scheduler.presenters import build_scheduler_case_detail_context
from apps.scheduler.services import (
    REPLY_DENY_TEMPLATE,
    REPLY_REOPEN_TEMPLATE,
    REPLY_UNIT_1_TEMPLATE,
    confirm_case_scheduling,
    reopen_scheduling_after_incident,
    reply_unit_2_text,
)

SCHEDULER_ROLE = "scheduler"
ADMIN_ROLE = "admin"
NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"

# Reserva do caso na página de trabalho do agendador (case-work-lock, slice
# 004, D4): contexto/papel do lock e a mensagem de perda da reserva.
LOCK_CONTEXT = "scheduler_confirm"
LOCK_LOST_TEXT = "Sua reserva para este caso expirou ou não é válida"

# Tipos declarados representativos (catálogo).
ANGIO_TYPE = "art_perif"
CARDIO_TYPE = "cat_cardiaco"
ANGIO_LABEL = "Arteriografia periférica"
CARDIO_LABEL = "Cateterismo cardíaco"

# Motivo da intercorrência nos cenários do slice.
REOPEN_REASON = "vaga desmarcada pela unidade de origem — nova data solicitada"

# Formato de exibição das respostas ao NIR (espelho do presenter médico).
_DISPLAY_FORMAT = "%d/%m/%Y %H:%M"

# Processamento comum de NEW até AWAITING_DOCTOR.
_PROCESSING_STEPS: list[tuple[str, dict[str, object]]] = [
    ("start_pdf_extraction", {}),
    ("complete_pdf_extraction", {}),
    ("complete_anonymization", {}),
    ("complete_llm_extraction", {}),
    ("complete_llm_summarization", {}),
]


def _login(client: Client, user: User, role: str) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


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


def _apply_metadata(
    case: Case,
    *,
    created_at: datetime | None = None,
    patient_name: str = "",
    patient_birth_date: date | None = None,
    agency_record_number: str = "",
    patient_age: int | None = None,
    days_on_screen: int | None = None,
    patient_gender: str = "",
    origin_unit: str = "",
) -> None:
    """Ajusta campos de exibição do caso (``created_at`` inclusa)."""
    Case.objects.filter(pk=case.pk).update(
        created_at=created_at or case.created_at,
        patient_name=patient_name,
        patient_birth_date=patient_birth_date,
        agency_record_number=agency_record_number,
        patient_age=patient_age,
        days_on_screen=days_on_screen,
        patient_gender=patient_gender,
        origin_unit=origin_unit,
    )
    case.refresh_from_db()


def _run_processing(case: Case) -> None:
    """Dirige o caso pelas transições do pipeline até ``AWAITING_DOCTOR``."""
    for operation, extra in _PROCESSING_STEPS:
        getattr(case, operation)(user=None, role=SYSTEM_ROLE, **extra)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _scheduler_requested_case(
    created_by: User,
    decided_by: User,
    procedure_types: Sequence[str],
    *,
    decisions: dict[str, tuple[str, str]] | None = None,
) -> Case:
    """Caso decidido pelo médico (≥1 aprovado) chegando a ``SCHEDULER_REQUESTED``."""
    case = _create_case_with_declared(created_by, procedure_types)
    _run_processing(case)
    record_doctor_procedure_decisions(
        case,
        decisions or {procedure_type: ("approved", "") for procedure_type in procedure_types},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    return case


def _awaiting_scheduling_case(
    created_by: User,
    decided_by: User,
    procedure_types: Sequence[str],
    *,
    decisions: dict[str, tuple[str, str]] | None = None,
) -> Case:
    """Caso em ``AWAITING_SCHEDULING`` (fila de agendamento)."""
    case = _scheduler_requested_case(created_by, decided_by, procedure_types, decisions=decisions)
    case.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_SCHEDULING
    return case


def _future_datetime(**kwargs: int) -> datetime:
    """Data/hora aware futura (UTC) a partir de agora."""
    return timezone.now() + timedelta(**kwargs)


def _confirmed_case(unit: int, *, created_by: User, decided_by: User, scheduled_by: User) -> Case:
    """Caso confirmado pelo serviço do slice 001: ``FINAL_REPLY_POSTED`` com os
    5 campos de agendamento persistidos (autor = ``scheduled_by``)."""
    case = _scheduler_requested_case(created_by, decided_by, (ANGIO_TYPE,))
    confirm_case_scheduling(
        case,
        unit=unit,
        scheduled_datetime=_future_datetime(days=2),
        scheduled_location="Sala de hemodinâmica",
        user=scheduled_by,
        role=SCHEDULER_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


def _reopened_case(*, created_by: User, decided_by: User, scheduled_by: User) -> Case:
    """Caso reaberto por intercorrência: ``AWAITING_SCHEDULING`` com os campos
    de agendamento limpos (``scheduled_by`` incluso) e motivo persistido."""
    case = _confirmed_case(
        1, created_by=created_by, decided_by=decided_by, scheduled_by=scheduled_by
    )
    reopen_scheduling_after_incident(
        case, reason=REOPEN_REASON, user=scheduled_by, role=SCHEDULER_ROLE
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_SCHEDULING
    assert case.scheduled_by is None
    assert case.scheduling_reopen_reason == REOPEN_REASON
    return case


def _user_messages(case: Case) -> list[Any]:
    """Mensagens manuais (user) da thread do caso, em ordem."""
    return list(case.communication_messages.filter(message_type=MessageType.USER))


def _add_document(case: Case, uploaded_by: User, *, position: int, content: str) -> CaseDocument:
    """Anexa um CaseDocument fake ao caso na posição informada."""
    uploaded = SimpleUploadedFile(
        f"relatorio-{position}.pdf",
        f"%PDF-1.4 conteudo {content} {position}".encode(),
        content_type="application/pdf",
    )
    return CaseDocument.objects.create(
        case=case,
        file=uploaded,
        position=position,
        original_filename=f"relatorio-sesab-{position}.pdf",
        content_type="application/pdf",
        size_bytes=len(uploaded.read()),
        uploaded_by=uploaded_by,
    )


def _local_str(value: datetime) -> str:
    """Data/hora local formatada como entra nas respostas ao NIR."""
    return timezone.localtime(value).strftime(_DISPLAY_FORMAT)


def _future_local_datetime(day: int = 10) -> datetime:
    """Data/hora aware futura a partir de 2030 (nunca no passado real)."""
    return timezone.make_aware(datetime(2030, 1, day, 9, 30))


# ── R1: nav por papel ativo ───────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["scheduler", "admin"])
def test_nav_visible_for_scheduler(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
    role: str,
) -> None:
    """R1: nav da fila do agendador visível na home quando o papel ativo é
    scheduler/admin."""
    user = user_factory(f"usuario-nav-{role}", (role,))
    login_user(user, role)

    # follow=True: a home despacha o papel ativo à sua área (change
    # painel-gerencial-e-home, slice 002); a navegação da página final
    # segue decidindo os links pelo papel ativo — scheduler cai na fila,
    # admin cai no painel com acesso cruzado às duas filas (slice 004,
    # correção de regressão: rótulos no vocabulário aprovado).
    response = client.get(reverse("home"), follow=True)
    body = response.content.decode()

    assert response.status_code == 200
    # Ligado (fix-2, P2): destino E rótulo aprovado do papel na MESMA
    # âncora — scheduler usa a faixa da fila ("Pendentes"); admin usa
    # o acesso cruzado do painel ("Pendentes (agendamento)"). A
    # asserção fraca antiga (href num lugar + ">Pendentes" noutro)
    # aceitava o rótulo da outra área nesta âncora.
    if role == "scheduler":
        expected_anchor = f'href="{reverse("scheduler:queue")}?tab=aguardando">Pendentes</a>'
    else:
        # Acesso cruzado do base.html: href seguido de aria-label.
        expected_anchor = (
            f'href="{reverse("scheduler:queue")}?tab=aguardando" '
            'aria-label="Pendentes — fila de agendamento">Pendentes (agendamento)</a>'
        )
    assert expected_anchor in body


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["nir", "doctor"])
def test_nav_hidden_for_other_roles(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
    role: str,
) -> None:
    """R1: nav da fila do agendador ausente para papel ativo fora de scheduler/admin."""
    user = user_factory(f"usuario-nav-{role}", (role,))
    login_user(user, role)

    response = client.get(reverse("home"), follow=True)

    assert response.status_code == 200
    assert reverse("scheduler:queue") not in response.content.decode()


# ── R2: guard por papel ativo ─────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["nir", "doctor", "manager"])
def test_queue_forbidden_for_non_scheduler_roles(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
    role: str,
) -> None:
    """R2: papel ativo fora de scheduler/admin → HTTP 403."""
    user = user_factory(f"usuario-{role}", (role,))
    login_user(user, role)

    response = client.get(reverse("scheduler:queue"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_anonymous_redirects_login(client: Client) -> None:
    """R2: anônimo → redirect ao login (302, semântica do role_required)."""
    response = client.get(reverse("scheduler:queue"))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["scheduler", "admin"])
def test_queue_allowed_for_scheduler_and_admin(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
    role: str,
) -> None:
    """R2: papel ativo scheduler/admin → 200."""
    user = user_factory(f"usuario-acesso-{role}", (role,))
    login_user(user, role)

    response = client.get(reverse("scheduler:queue"))

    assert response.status_code == 200


@pytest.mark.django_db
def test_manager_scheduler_composite_queues_under_scheduler_active(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: scheduler+manager acessa a fila sob o papel ativo ``scheduler``."""
    scheduler = user_factory("gerente-agendador", (SCHEDULER_ROLE, "manager"))
    login_user(scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:queue"))

    assert response.status_code == 200


# ── R2: abas por estado + ordem por tempo de tela + paginação ────────────


@pytest.mark.django_db
def test_queue_awaiting_default(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: aba default ``aguardando`` lista SCHEDULER_REQUESTED e
    AWAITING_SCHEDULING (inclui caso reaberto por intercorrência) — nunca
    casos processados."""
    doctor = user_factory("medico-aguardando", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-aguardando", (SCHEDULER_ROLE,))

    novo = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(novo, patient_name="Paciente Novo", agency_record_number="1001")
    reaberto = _reopened_case(created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _apply_metadata(reaberto, patient_name="Paciente Reaberto", agency_record_number="1002")
    processado = _confirmed_case(2, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _apply_metadata(processado, patient_name="Paciente Processado", agency_record_number="1003")
    login_user(user_factory("geral-aguardando", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:queue"))

    assert response.status_code == 200
    body = response.content.decode()
    # Casos aguardando (novo + reaberto) visíveis; processado fora da aba.
    assert str(novo.case_id) in body
    assert "Paciente Novo" in body
    assert str(reaberto.case_id) in body
    assert "Paciente Reaberto" in body
    assert "Paciente Processado" not in body


@pytest.mark.django_db
def test_queue_processed_tab(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: aba ``processados`` lista SCHEDULING_CONFIRMED|SCHEDULING_DENIED|
    FINAL_REPLY_POSTED — nunca casos aguardando."""
    doctor = user_factory("medico-processados", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-processados", (SCHEDULER_ROLE,))

    final = _confirmed_case(2, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _apply_metadata(final, patient_name="Final Reply", agency_record_number="1101")
    transitorio_confirmado = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    transitorio_confirmado.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    transitorio_confirmado.confirm_scheduling(user=None, role=SYSTEM_ROLE)
    transitorio_confirmado.refresh_from_db()
    # Slice 005: processados é a janela do dia por decisor (não o status) —
    # os estados transitórios entram com o carimbo de hoje do agendador.
    Case.objects.filter(pk=transitorio_confirmado.pk).update(
        scheduling_decided_at=timezone.now(), scheduling_decided_by=scheduler
    )
    transitorio_confirmado.refresh_from_db()
    assert transitorio_confirmado.status == CaseStatus.SCHEDULING_CONFIRMED
    _apply_metadata(
        transitorio_confirmado, patient_name="Confirmado Transitorio", agency_record_number="1102"
    )
    transitorio_negado = _scheduler_requested_case(nir_user, doctor, (CARDIO_TYPE,))
    transitorio_negado.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    transitorio_negado.deny_scheduling(user=None, role=SYSTEM_ROLE)
    transitorio_negado.refresh_from_db()
    Case.objects.filter(pk=transitorio_negado.pk).update(
        scheduling_decided_at=timezone.now(), scheduling_decided_by=scheduler
    )
    transitorio_negado.refresh_from_db()
    assert transitorio_negado.status == CaseStatus.SCHEDULING_DENIED
    _apply_metadata(
        transitorio_negado, patient_name="Negado Transitorio", agency_record_number="1103"
    )
    aguardando = _awaiting_scheduling_case(nir_user, doctor, (CARDIO_TYPE,))
    _apply_metadata(aguardando, patient_name="Aguardando Fila", agency_record_number="1104")
    # Slice 005: a aba é escopada ao decisor — o visor é o próprio agendador.
    login_user(scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:queue"), {"tab": "processados"})

    assert response.status_code == 200
    body = response.content.decode()
    assert str(final.case_id) in body
    assert "Final Reply" in body
    assert str(transitorio_confirmado.case_id) in body
    assert "Confirmado Transitorio" in body
    assert str(transitorio_negado.case_id) in body
    assert "Negado Transitorio" in body
    assert "Aguardando Fila" not in body
    # Estados legíveis das abas na página.
    assert "Resposta final publicada" in body


@pytest.mark.django_db
def test_queue_invalid_tab_falls_back_to_awaiting(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: valor de aba desconhecido cai no default ``aguardando``."""
    doctor = user_factory("medico-tab-agendador", (DOCTOR_ROLE,))
    awaiting = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(awaiting, patient_name="Só Aguardando", agency_record_number="1201")
    scheduler = user_factory("agendador-tab", (SCHEDULER_ROLE,))
    decided = _confirmed_case(2, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _apply_metadata(decided, patient_name="Já Processado", agency_record_number="1202")
    login_user(user_factory("geral-tab-agendador", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:queue"), {"tab": "historico"})

    assert response.status_code == 200
    body = response.content.decode()
    assert str(awaiting.case_id) in body
    assert "Só Aguardando" in body
    assert str(decided.case_id) not in body
    # OLI-002 R1: pendentes sem badge genérico de status — o título prova
    # a aba aguardando (sem «Aguardando confirmação de agendamento»).
    assert "Solicitações de agendamento aguardando confirmação</h1>" in body
    assert "Aguardando confirmação de agendamento" not in body
    assert "Resposta final publicada" not in body


@pytest.mark.django_db
def test_queue_paginated_fifo(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2/R6: desempate FIFO por ``created_at`` paginado (20/página).

    Casos sem ``days_on_screen`` mantêm o desempate FIFO do contrato novo.
    """
    doctor = user_factory("medico-fifo-agendador", (DOCTOR_ROLE,))
    base = timezone.now()
    for index in range(25):
        case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
        _apply_metadata(
            case,
            created_at=base - timedelta(seconds=25 - index),
            patient_name=f"Paciente {index:02d}",
            agency_record_number=f"AR-{index:02d}",
        )
    login_user(user_factory("geral-fifo-agendador", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    first = client.get(reverse("scheduler:queue"))
    assert first.status_code == 200
    first_body = first.content.decode()
    # Página 1: os 20 mais antigos (Paciente 00…19), nunca o mais novo.
    assert "Paciente 00" in first_body
    assert "Paciente 19" in first_body
    assert "Paciente 20" not in first_body
    assert "Paciente 24" not in first_body
    assert "Página 1 de 2" in first_body

    second = client.get(reverse("scheduler:queue"), {"page": "2"})
    assert second.status_code == 200
    second_body = second.content.decode()
    assert "Paciente 20" in second_body
    assert "Paciente 24" in second_body
    assert "Paciente 00" not in second_body
    assert "Página 2 de 2" in second_body


@pytest.mark.django_db
def test_queue_ordered_by_days_on_screen(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1: ordem por ``days_on_screen`` desc (None ao fim), NÃO FIFO.

    ``created_at`` CONFLITANTES: o caso sem cabeçalho é o MAIS ANTIGO e o de
    10 dias é o MAIS RECENTE — a ordenação antiga (FIFO) produziria
    None, 3, 10 e este teste falharia.
    """
    doctor = user_factory("medico-sort-tela-agendador", (DOCTOR_ROLE,))
    base = timezone.now()
    no_header = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(
        no_header,
        created_at=base - timedelta(hours=3),
        patient_name="Paciente Sem Cabecalho",
    )
    three_days = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(
        three_days,
        created_at=base - timedelta(hours=2),
        patient_name="Paciente Tres Dias",
        days_on_screen=3,
    )
    ten_days = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(
        ten_days,
        created_at=base - timedelta(hours=1),
        patient_name="Paciente Dez Dias",
        days_on_screen=10,
    )
    login_user(user_factory("geral-sort-tela", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:queue"))

    assert response.status_code == 200
    body = response.content.decode()
    idx_ten = body.index(str(ten_days.case_id))
    idx_three = body.index(str(three_days.case_id))
    idx_none = body.index(str(no_header.case_id))
    assert idx_ten < idx_three < idx_none


@pytest.mark.django_db
def test_queue_card_shows_age_days_and_waiting_label(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: card da aba ativa com idade, «⏱ Aguardando há» e «Dias em tela: N»."""
    doctor = user_factory("medico-card-tela-agendador", (DOCTOR_ROLE,))
    case = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(
        case,
        patient_name="Paciente Identificado",
        patient_age=84,
        days_on_screen=6,
    )
    login_user(user_factory("geral-card-tela", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    assert "Paciente Identificado · 84 a" in body
    assert "Aguardando há" in body
    assert "Dias em tela: 6" in body
    # OLI-002 R3: sem data de recebimento repetida e sem badge compacto.
    assert "Recebido em " not in body
    assert "6 d em tela" not in body
    assert ">Dias em tela: 6</span>" in body


@pytest.mark.django_db
def test_queue_processed_tab_uses_received_label(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: aba processados usa «Recebido há» e NUNCA «Aguardando há»."""
    doctor = user_factory("medico-label-processado", (DOCTOR_ROLE,))
    scheduler_user = user_factory("agendador-label-processado", (SCHEDULER_ROLE,))
    processed = _confirmed_case(
        2, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler_user
    )
    _apply_metadata(processed, patient_name="Paciente Processado", patient_age=84)
    # Slice 005: processados é escopado ao decisor.
    login_user(scheduler_user, SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue"), {"tab": "processados"}).content.decode()

    assert "Paciente Processado · 84 a" in body
    assert "Recebido há" in body
    assert "Aguardando há" not in body


@pytest.mark.django_db
def test_queue_card_zero_age_and_zero_days_on_screen(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2 (P1 da review): ZERO é válido e DEVE exibir «0 a» e «Dias em tela: 0»."""
    doctor = user_factory("medico-zero-tela-agendador", (DOCTOR_ROLE,))
    case = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(case, patient_name="Recem Nascido", patient_age=0, days_on_screen=0)
    login_user(user_factory("geral-zero-tela", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    assert "Recem Nascido · 0 a" in body
    assert "Dias em tela: 0" in body


@pytest.mark.django_db
def test_queue_card_absent_age_and_days_on_screen(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: idade/dias ausentes NÃO geram sufixo nem badge (sem «— a»)."""
    doctor = user_factory("medico-sem-demo-agendador", (DOCTOR_ROLE,))
    case = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(case, patient_name="Paciente Sem Demografia")
    login_user(user_factory("geral-sem-demo", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    assert "Paciente Sem Demografia" in body
    assert "Paciente Sem Demografia · " not in body
    assert "Dias em tela" not in body


@pytest.mark.django_db
def test_queue_cards_show_types_and_unit(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: card da fila mostra os tipos declarados; card de caso processado
    mostra a unidade definida no agendamento."""
    doctor = user_factory("medico-cards-agendador", (DOCTOR_ROLE,))
    awaiting = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE, CARDIO_TYPE))
    _apply_metadata(awaiting, patient_name="Ana Souza", agency_record_number="1301")
    scheduler = user_factory("agendador-cards", (SCHEDULER_ROLE,))
    processed = _confirmed_case(2, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _apply_metadata(processed, patient_name="Beto Lima", agency_record_number="1302")
    # Slice 005: a aba processados é escopada ao decisor.
    login_user(scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:queue"), {"tab": "aguardando"})

    assert response.status_code == 200
    body = response.content.decode()
    assert "Ana Souza" in body
    assert "1301" in body
    # Tipos declarados com rótulos do catálogo no card aguardando.
    assert ANGIO_LABEL in body
    assert CARDIO_LABEL in body
    # Aba aguardando não mostra o processado nem a unidade dele.
    assert "Beto Lima" not in body
    assert "Unidade 2" not in body

    processed_response = client.get(reverse("scheduler:queue"), {"tab": "processados"})
    assert processed_response.status_code == 200
    processed_body = processed_response.content.decode()
    assert "Beto Lima" in processed_body
    assert "1302" in processed_body
    # Unidade definida no agendamento aparece no card processado.
    assert "Unidade 2" in processed_body


# ── OLI-002: página completa dos pendentes (R1–R5) ──────────────────────────


@pytest.mark.django_db
def test_queue_pending_title_and_full_container(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R1: título via ``page_title`` e container integral alinhado."""
    doctor = user_factory("medico-oli-titulo", (DOCTOR_ROLE,))
    _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    login_user(user_factory("agendador-oli-titulo", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    assert "Solicitações de agendamento aguardando confirmação</h1>" in body
    assert "Fila de agendamento</h1>" not in body
    assert "col-lg-10" not in body
    assert "justify-content-center" not in body
    assert "operational-list-page" in body


@pytest.mark.django_db
def test_queue_procedure_type_filter_server_side(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R2/D7: ``procedure_type`` filtra no server antes da paginação."""
    doctor = user_factory("medico-oli-filtro", (DOCTOR_ROLE,))
    for index in range(20):
        case = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
        _apply_metadata(case, patient_name=f"Angio Filtro {index:02d}", days_on_screen=10)
    cardio = _awaiting_scheduling_case(nir_user, doctor, (CARDIO_TYPE,))
    _apply_metadata(cardio, patient_name="Cardio Filtro", days_on_screen=0)
    login_user(user_factory("agendador-oli-filtro", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    filtered = client.get(
        reverse("scheduler:queue"), {"tab": "aguardando", "procedure_type": CARDIO_TYPE}
    )
    assert filtered.status_code == 200
    filtered_body = filtered.content.decode()
    # Caso fora da página 1 sem filtro aparece na página 1 filtrada
    # (prova de filtro antes da paginação, não client-side na página).
    assert "Cardio Filtro" in filtered_body
    assert "Angio Filtro" not in filtered_body
    # Form server-side com a opção selecionada e hx-get preservando o tipo.
    assert 'name="procedure_type"' in filtered_body
    assert f'value="{CARDIO_TYPE}" selected' in filtered_body
    assert f"procedure_type={CARDIO_TYPE}" in filtered_body
    assert (
        f'hx-get="{reverse("scheduler:queue_partial")}?tab=aguardando&procedure_type={CARDIO_TYPE}"'
        in filtered_body
    )


@pytest.mark.django_db
def test_queue_procedure_type_invalid_ignored(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R2: valor fora do catálogo equivale a vazio (sem erro)."""
    doctor = user_factory("medico-oli-filtro-invalido", (DOCTOR_ROLE,))
    angio = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(angio, patient_name="Angio Invalido", agency_record_number="OLI-IN1")
    cardio = _awaiting_scheduling_case(nir_user, doctor, (CARDIO_TYPE,))
    _apply_metadata(cardio, patient_name="Cardio Invalido", agency_record_number="OLI-IN2")
    login_user(user_factory("agendador-oli-filtro-invalido", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(
        reverse("scheduler:queue"), {"tab": "aguardando", "procedure_type": "nao-existe"}
    ).content.decode()

    assert "Angio Invalido" in body
    assert "Cardio Invalido" in body
    assert "procedure_type=nao-existe" not in body


@pytest.mark.django_db
def test_queue_procedure_type_filter_pagination_and_partial(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R2/R6: filtro viaja em paginação e partial (sem reampliar)."""
    doctor = user_factory("medico-oli-filtro-pag", (DOCTOR_ROLE,))
    base = timezone.now()
    for index in range(21):
        case = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
        _apply_metadata(
            case,
            created_at=base - timedelta(seconds=21 - index),
            patient_name=f"Angio Pag {index:02d}",
        )
    cardio = _awaiting_scheduling_case(nir_user, doctor, (CARDIO_TYPE,))
    _apply_metadata(cardio, patient_name="Cardio Pag Fora", days_on_screen=99)
    login_user(user_factory("agendador-oli-filtro-pag", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    page_one = client.get(
        reverse("scheduler:queue"), {"tab": "aguardando", "procedure_type": ANGIO_TYPE}
    ).content.decode()
    assert "Angio Pag 00" in page_one
    assert "Cardio Pag Fora" not in page_one
    assert "Página 1 de 2" in page_one
    # Paginação server-side preserva o filtro (link de próxima página).
    assert f"procedure_type={ANGIO_TYPE}" in page_one.split("Página 1 de 2")[1]

    partial = client.get(
        reverse("scheduler:queue_partial"),
        {"tab": "aguardando", "procedure_type": ANGIO_TYPE},
    ).content.decode()
    assert "Angio Pag 00" in partial
    assert "Cardio Pag Fora" not in partial

    cardio_page = client.get(
        reverse("scheduler:queue"), {"tab": "aguardando", "procedure_type": CARDIO_TYPE}
    ).content.decode()
    assert "Cardio Pag Fora" in cardio_page
    assert "Angio Pag" not in cardio_page


@pytest.mark.django_db
def test_queue_processed_ignores_procedure_type_filter(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R2/R5: aba processados não recebe o novo filtro."""
    doctor = user_factory("medico-oli-filtro-processados", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-oli-filtro-proc", (SCHEDULER_ROLE,))
    processed = _confirmed_case(2, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _apply_metadata(processed, patient_name="Processado Sem Filtro")
    # Slice 005: a aba processados é escopada ao decisor.
    login_user(scheduler, SCHEDULER_ROLE)

    body = client.get(
        reverse("scheduler:queue"), {"tab": "processados", "procedure_type": CARDIO_TYPE}
    ).content.decode()

    assert "Processado Sem Filtro" in body
    assert 'name="procedure_type"' not in body


@pytest.mark.django_db
def test_queue_pending_card_regions_8_4_and_single_surface(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R3: ``.case-card > .row`` com conteúdo 8 e tempo/ação 4."""
    doctor = user_factory("medico-oli-regioes", (DOCTOR_ROLE,))
    case = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(
        case, patient_name="Paciente Regioes", agency_record_number="OLI002", days_on_screen=2
    )
    login_user(user_factory("agendador-oli-regioes", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    card = _card_slice(body, case.case_id)
    assert '<div class="case-card card p-3">' in card
    assert '<div class="row">' in card
    assert "col-lg-8" in card
    assert "col-lg-4" in card
    assert card.index("col-lg-8") < card.index("col-lg-4")
    assert "list-group-item" not in card
    assert card.count("Aguardando há") == 1
    assert "Dias em tela: 2" in card
    assert "status-badge" not in card
    assert "Aguardando confirmação de agendamento" not in card


@pytest.mark.django_db
def test_queue_pending_card_mixed_dispositions_per_procedure(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R3/D8: aprovado + negado no mesmo caso, sem agregar decisões."""
    doctor = user_factory("medico-oli-disposicoes", (DOCTOR_ROLE,))
    case = _awaiting_scheduling_case(
        nir_user,
        doctor,
        (ANGIO_TYPE, CARDIO_TYPE),
        decisions={
            ANGIO_TYPE: ("approved", ""),
            CARDIO_TYPE: ("denied", "INR elevado"),
        },
    )
    _apply_metadata(case, patient_name="Paciente Disposicoes", agency_record_number="OLI-DSP")
    login_user(user_factory("agendador-oli-disposicoes", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    card = _card_slice(body, case.case_id)
    assert ANGIO_LABEL in card
    assert CARDIO_LABEL in card
    assert "angio" in card
    assert "cardio" in card
    assert "Aprovado" in card
    assert "Negado" in card


@pytest.mark.django_db
def test_queue_pending_card_demography_and_reopen(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R3/R6: demografia (sexo/origem) e alerta de reabertura."""
    doctor = user_factory("medico-oli-reabertura", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-oli-reabertura", (SCHEDULER_ROLE,))
    case = _reopened_case(created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _apply_metadata(
        case,
        patient_name="Paciente Reaberto Demo",
        agency_record_number="OLI-REO",
        patient_age=71,
        days_on_screen=4,
        patient_gender="Masculino",
        origin_unit="Hospital Regional",
    )
    login_user(user_factory("agendador-oli-reabertura-view", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    card = _card_slice(body, case.case_id)
    assert "Paciente Reaberto Demo" in card
    assert "71 a" in card
    assert "Masculino" in card
    assert "Hospital Regional" in card
    assert REOPEN_REASON in card


@pytest.mark.django_db
def test_queue_pending_empty_and_count_are_info_alerts(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R1/D10: contagem e vazio em ``alert-info``, sem warning/card."""
    login_user(user_factory("agendador-oli-vazio", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    empty_body = client.get(reverse("scheduler:queue")).content.decode()
    assert "alert alert-info" in empty_body
    assert "alert-warning" not in empty_body
    assert "Nenhum caso aguardando agendamento" in empty_body
    assert "card shadow-sm" not in empty_body

    doctor = user_factory("medico-oli-contagem", (DOCTOR_ROLE,))
    _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _awaiting_scheduling_case(nir_user, doctor, (CARDIO_TYPE,))
    count_body = client.get(reverse("scheduler:queue")).content.decode()
    assert "alert alert-info" in count_body
    assert "alert-warning" not in count_body
    assert "2 caso" in count_body


@pytest.mark.django_db
def test_queue_pending_cta_stretched_link_states(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R4: Agendar/Continuar reais com stretched-link; outro sem link."""
    doctor = user_factory("medico-oli-cta", (DOCTOR_ROLE,))
    free = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(free, patient_name="Paciente CTA Livre")
    mine = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(mine, patient_name="Paciente CTA Meu")
    other = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(other, patient_name="Paciente CTA Outro")
    scheduler = user_factory("agendador-oli-cta", (SCHEDULER_ROLE,))
    other_scheduler = user_factory("agendador-oli-cta-outro", (SCHEDULER_ROLE,))
    claim_case_lock(mine, user=scheduler, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)
    claim_case_lock(other, user=other_scheduler, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)
    login_user(scheduler, SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    free_card = _lock_card_block(body, free.case_id)
    assert 'class="btn btn-sm btn-primary stretched-link mt-2"' in free_card
    assert ">Agendar</a>" in free_card

    mine_card = _lock_card_block(body, mine.case_id)
    assert "stretched-link" in mine_card
    assert "Continuar" in mine_card

    other_card = _lock_card_block(body, "Paciente CTA Outro")
    assert "Reservado" in other_card
    assert other_scheduler.display_name in other_card
    assert "stretched-link" not in other_card
    assert f'href="{reverse("scheduler:case_detail", args=[other.case_id])}"' not in other_card


@pytest.mark.django_db
def test_queue_pending_card_shows_unit_label_when_defined(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Revisão OLI-002 (R6): pendente exibe ``unit_label`` quando definida.

    O ramo ``aguardando`` não pode perder o badge de unidade do partial
    antigo — exibição compacta, sem filtro/diferenciação por unidade.
    ``scheduled_unit`` é ajustado só como campo de exibição (mesmo padrão
    dos campos de ``_apply_metadata``); o estado permanece pendente.
    """
    doctor = user_factory("medico-oli-unidade", (DOCTOR_ROLE,))
    with_unit = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(with_unit, patient_name="Paciente Com Unidade", agency_record_number="OLI-U1")
    Case.objects.filter(pk=with_unit.pk).update(scheduled_unit=2)
    with_unit.refresh_from_db()
    without_unit = _awaiting_scheduling_case(nir_user, doctor, (CARDIO_TYPE,))
    _apply_metadata(
        without_unit, patient_name="Paciente Sem Unidade", agency_record_number="OLI-U0"
    )
    login_user(user_factory("agendador-oli-unidade", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    unit_card = _card_slice(body, with_unit.case_id)
    assert "Unidade 2" in unit_card
    plain_card = _card_slice(body, without_unit.case_id)
    assert "Unidade " not in plain_card


@pytest.mark.django_db
def test_queue_processed_card_preserves_received_label_and_unit(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R5/R6: processados preservado fora do ramo pendente."""
    doctor = user_factory("medico-oli-proc-preservado", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-oli-proc-preservado", (SCHEDULER_ROLE,))
    case = _confirmed_case(2, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _apply_metadata(case, patient_name="Processado Preservado", patient_age=65, days_on_screen=3)
    # Slice 005: a aba processados é escopada ao decisor.
    login_user(scheduler, SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue"), {"tab": "processados"}).content.decode()

    card = _card_slice(body, case.case_id)
    assert "Processado Preservado · 65 a" in card
    assert "Recebido há" in card
    assert "Aguardando há" not in card
    assert "3 d em tela" in card
    assert "Unidade 2" in card
    assert "status-badge" in card


# ── R3: detalhe (identificação + one-liner + decisões + sem artefatos) ─────

_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "MARIA DA SILVA", "entity_type": "PERSON"},
    "<DATA_1>": {"value": "05/03/1972", "entity_type": "DATE_TIME"},
    "<CPF_1>": {"value": "111.222.333-44", "entity_type": "CPF"},
}


def _detail_case(created_by: User, decided_by: User) -> Case:
    """Caso em AWAITING_SCHEDULING com artefatos clínicos populados (tokens),
    decisões médicas por procedimento e metadados reais de identificação."""
    case = _awaiting_scheduling_case(
        created_by,
        decided_by,
        (ANGIO_TYPE, CARDIO_TYPE),
        decisions={
            ANGIO_TYPE: ("approved", "procedimento viável"),
            CARDIO_TYPE: ("denied", "INR elevado — não liberar"),
        },
    )
    case.pseudonym_map = dict(_PSEUDONYMS)
    case.structured_data = {
        "contexto_clinico": "estrutura oculta: paciente <PESSOA_1> em uso de varfarina",
        "exames": {"platelets": {"value": 80000, "unit": "/mm³", "status": "confirmado"}},
    }
    case.policy_result = {
        ANGIO_TYPE: {
            "procedure_type": ANGIO_TYPE,
            "section_id": "S1",
            "recommendation": "recomenda_recusar",
            "refusal_reasons": ["policy oculta: plaquetas 80000 abaixo do mínimo"],
        }
    }
    case.suggested_action = {
        "procedures": {
            ANGIO_TYPE: {
                "suggestion": "recusar",
                "motivos": ["sugestao oculta: agendar com urgencia"],
            }
        },
        "aggregate": {
            "suggestion": "recusar",
            "motivos": ["sugestao oculta agregada"],
        },
    }
    case.summary_text = (
        "Primeira linha: paciente <PESSOA_1> nascido em <DATA_1>.\n"
        "Segunda linha oculta com <CPF_1> e detalhe clínico reservado."
    )
    case.save()
    _apply_metadata(
        case,
        patient_name="MARIA DA SILVA",
        patient_birth_date=date(1972, 3, 5),
        agency_record_number="33345",
    )
    return case


def _collect_strings(value: object) -> list[str]:
    """Recolhe recursivamente todas as strings de dict/list do resultado."""
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            strings.extend(_collect_strings(item))
    elif isinstance(value, list):
        for item in value:
            strings.extend(_collect_strings(item))
    return strings


@pytest.mark.django_db
def test_detail_shows_identification_and_decisions(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3: detalhe mostra identificação real, decisões médicas por procedimento
    (disposição + motivo das negativas), dados de agendamento e comunicações."""
    doctor = user_factory("medico-detalhe-agendador", (DOCTOR_ROLE,))
    case = _detail_case(nir_user, doctor)
    viewer = user_factory("geral-detalhe-agendador", (SCHEDULER_ROLE,))
    _login(client, viewer, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    # Identificação real.
    assert "MARIA DA SILVA" in body
    assert "33345" in body
    assert "05/03/1972" in body
    # Decisões médicas por procedimento (aprovado e negado com motivo).
    assert ANGIO_LABEL in body
    assert CARDIO_LABEL in body
    assert "Aprovado" in body
    assert "Negado" in body
    assert "INR elevado — não liberar" in body
    # Estado aguardando → forms de confirmar/negar (ações por estado, D4).
    assert "Aguardando confirmação de agendamento" in body
    assert "Confirmar agendamento" in body
    assert "Negar agendamento" in body
    # Thread de comunicações presente (card do template).
    assert "Comunicações" in body
    # Sem link de PDF em pré-decisão (R3/R7).
    assert reverse("scheduler:case_pdf", args=[case.case_id, 1]) not in body


@pytest.mark.django_db
def test_detail_full_summary_reidentified(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1 (slice 001): sumário COMPLETO re-identificado no detalhe do agendador.

    O detalhe exibe o ``summary_text`` INTEIRO, re-identificado na
    renderização — paridade ats-web (fim do truncamento à primeira linha).
    Sem tokens (escaped inclusive) na página."""
    doctor = user_factory("medico-oneliner", (DOCTOR_ROLE,))
    case = _detail_case(nir_user, doctor)
    viewer = user_factory("geral-oneliner", (SCHEDULER_ROLE,))
    _login(client, viewer, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    # Sumário completo re-identificado com os valores reais do mapa.
    assert "Primeira linha: paciente MARIA DA SILVA nascido em 05/03/1972." in body
    assert "Segunda linha oculta com 111.222.333-44 e detalhe clínico reservado." in body
    # Nenhum token do mapa do caso sobrevive (escaped inclusive).
    for token in ("<PESSOA_1>", "<DATA_1>", "<CPF_1>"):
        assert token not in body
        assert token.replace("<", "&lt;").replace(">", "&gt;") not in body


@pytest.mark.django_db
def test_detail_full_summary_preserves_all_breaks(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1 (slice 001, rodada 3): TODAS as quebras do texto são preservadas.

    Linha vazia na borda inicial, TRÊS quebras consecutivas no meio e quebra
    final — cada ``\\n`` vira um ``<br>``, sem colapso nem strip
    (``linebreaksbr`` sobre o texto inteiro re-identificado)."""
    doctor = user_factory("medico-linhas-vazias", (DOCTOR_ROLE,))
    case = _detail_case(nir_user, doctor)
    case.summary_text = "\nLinha A.\n\n\nLinha B com <PESSOA_1>.\n"
    case.save(update_fields=["summary_text"])
    viewer = user_factory("geral-linhas-vazias", (SCHEDULER_ROLE,))
    _login(client, viewer, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "<br>Linha A.<br><br><br>Linha B com MARIA DA SILVA.<br>" in body
    assert "<PESSOA_1>" not in body


@pytest.mark.django_db
def test_detail_summary_whitespace_only_shows_placeholder(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1 (slice 001, rodada 3): sumário só com espaços/quebras exibe o placeholder."""
    doctor = user_factory("medico-somente-espacos", (DOCTOR_ROLE,))
    case = _detail_case(nir_user, doctor)
    case.summary_text = "  \n  "
    case.save(update_fields=["summary_text"])
    viewer = user_factory("geral-somente-espacos", (SCHEDULER_ROLE,))
    _login(client, viewer, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    assert "Diagnóstico não disponível para este caso." in response.content.decode()


@pytest.mark.django_db
def test_detail_has_no_clinical_artifacts(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3/R2 (slice 001): structured_data/policy_result/suggested_action jamais
    renderizados — assert de ausência com os artefatos populados no caso. O
    sumário completo (todas as linhas de ``summary_text``) É permitido — só os
    demais artefatos do pipeline seguem fora do detalhe."""
    doctor = user_factory("medico-artefatos", (DOCTOR_ROLE,))
    case = _detail_case(nir_user, doctor)
    # Garantia: os artefatos estão mesmo persistidos (senão o assert seria vazio).
    assert case.structured_data
    assert case.policy_result
    assert case.suggested_action
    assert "<CPF_1>" in case.summary_text
    viewer = user_factory("geral-artefatos", (SCHEDULER_ROLE,))
    _login(client, viewer, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    for secret in (
        "estrutura oculta",
        "varfarina",
        "80000",
        "policy oculta",
        "recomenda_recusar",
        "sugestao oculta",
    ):
        assert secret not in body


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["nir", "doctor", "manager"])
def test_detail_forbidden_for_non_scheduler_roles(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    role: str,
) -> None:
    """R3: papel ativo fora de scheduler/admin → 403 sem vazar dados."""
    doctor = user_factory("medico-403", (DOCTOR_ROLE,))
    case = _detail_case(nir_user, doctor)
    user = user_factory(f"usuario-detalhe-{role}", (role,))
    _login(client, user, role)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 403
    body = response.content.decode()
    assert "MARIA DA SILVA" not in body
    assert "33345" not in body


@pytest.mark.django_db
def test_detail_anonymous_redirects_login(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3: anônimo → redirect ao login."""
    doctor = user_factory("medico-anon", (DOCTOR_ROLE,))
    case = _detail_case(nir_user, doctor)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
def test_detail_missing_case_404(
    client: Client,
    user_factory: Callable[..., User],
) -> None:
    """R3: caso inexistente → 404."""
    import uuid

    scheduler = user_factory("geral-404-agendador", (SCHEDULER_ROLE,))
    _login(client, scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[uuid.uuid4()]))

    assert response.status_code == 404


# ── R4: confirmar (unidade 1|2) ───────────────────────────────────────────


def _confirm_payload(
    *,
    unit: int,
    scheduled_at: datetime,
    location: str,
) -> dict[str, str]:
    """Payload POST do form de confirmação a partir de um aware datetime."""
    local = timezone.localtime(scheduled_at)
    return {
        "unit": str(unit),
        "scheduled_date": local.strftime("%Y-%m-%d"),
        "scheduled_time": local.strftime("%H:%M"),
        "scheduled_location": location,
    }


@pytest.mark.django_db
def test_confirm_unit_1_flow(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4: confirmar unidade 1 via UI → FINAL_REPLY_POSTED com campos
    persistidos, flash e resposta final (local + data/hora) na thread."""
    doctor = user_factory("medico-confirm-1", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-confirm-1", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    scheduled_at = timezone.make_aware(datetime(2030, 2, 10, 9, 30))
    location = "Bloco B — Sala 2"
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    token = _claim_for_post(case, scheduler)

    response = client.post(
        reverse("scheduler:case_confirm", args=[case.case_id]),
        _with_token(_confirm_payload(unit=1, scheduled_at=scheduled_at, location=location), token),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.scheduled_unit == 1
    assert case.scheduled_location == location
    assert case.scheduled_datetime is not None
    assert case.scheduled_by == scheduler
    assert case.scheduled_decided_at is not None

    detail = client.get(detail_url)
    assert detail.status_code == 200
    body = detail.content.decode()
    # Flash de sucesso + resposta final visível na thread.
    assert "Agendamento confirmado." in body
    expected_reply = REPLY_UNIT_1_TEMPLATE.format(
        location=location, scheduled_at=_local_str(scheduled_at)
    )
    assert expected_reply in body


@pytest.mark.django_db
def test_confirm_unit_2_exact_reply(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4: confirmar unidade 2 → resposta final na thread é o texto EXATO do
    plano §4 (sem ponto final)."""
    doctor = user_factory("medico-confirm-2", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-confirm-2", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    scheduled_at = timezone.make_aware(datetime(2030, 2, 11, 14, 0))
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    token = _claim_for_post(case, scheduler)

    response = client.post(
        reverse("scheduler:case_confirm", args=[case.case_id]),
        _with_token(
            _confirm_payload(unit=2, scheduled_at=scheduled_at, location="Unidade 2 (internet)"),
            token,
        ),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.scheduled_unit == 2
    assert case.scheduled_by == scheduler

    detail = client.get(detail_url)
    assert detail.status_code == 200
    body = detail.content.decode()
    assert reply_unit_2_text() in body
    # A resposta exata é a única user message da thread.
    messages_body = [m.body for m in _user_messages(case)]
    assert messages_body == [reply_unit_2_text()]


@pytest.mark.django_db
def test_confirm_past_datetime_redirects_no_500(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4/R5: data/hora no passado é rejeitada na validação do form → mensagem
    + redirect ao detalhe (nunca 500, sem escrita)."""
    doctor = user_factory("medico-confirm-passado", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-confirm-passado", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    past_at = timezone.make_aware(datetime(2020, 1, 1, 9, 30))
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    token = _claim_for_post(case, scheduler)

    response = client.post(
        reverse("scheduler:case_confirm", args=[case.case_id]),
        _with_token(_confirm_payload(unit=1, scheduled_at=past_at, location="Sala 1"), token),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.scheduled_unit is None
    assert _user_messages(case) == []

    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert "futura" in detail.content.decode()


# ── R4: negar ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_deny_flow(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4: negar com motivo via UI → FINAL_REPLY_POSTED, motivo persistido e
    resposta com o motivo na thread."""
    doctor = user_factory("medico-deny", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-deny", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    reason = "vaga não disponível na data solicitada"
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    token = _claim_for_post(case, scheduler)

    response = client.post(
        reverse("scheduler:case_deny", args=[case.case_id]),
        _with_token({"reason": reason}, token),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.scheduling_denial_reason == reason

    detail = client.get(detail_url)
    assert detail.status_code == 200
    body = detail.content.decode()
    assert REPLY_DENY_TEMPLATE.format(reason=reason) in body


@pytest.mark.django_db
def test_deny_without_reason_rejected(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4/R5: negar sem motivo → mensagem + redirect, sem qualquer escrita."""
    doctor = user_factory("medico-deny-vazio", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-deny-vazio", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    token = _claim_for_post(case, scheduler)
    # Depois do claim: o POST recusado não pode gravar NENHUM evento novo.
    events_before = case.events.count()

    response = client.post(
        reverse("scheduler:case_deny", args=[case.case_id]),
        _with_token({"reason": "   "}, token),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.events.count() == events_before
    assert case.scheduling_denial_reason == ""
    assert _user_messages(case) == []

    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert "motivo da negação" in detail.content.decode()


# ── R4: desmarcar (intercorrência, unidade 1 apenas) ──────────────────────


@pytest.mark.django_db
def test_reopen_button_only_unit_1(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4: botão desmarcar aparece SÓ em FINAL_REPLY_POSTED com unidade 1;
    unidade 2 exibe o banner "intercorrência desabilitada" sem o botão."""
    doctor = user_factory("medico-reopen-ui", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-reopen-ui", (SCHEDULER_ROLE,))
    unit_1 = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    unit_2 = _confirmed_case(2, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    login_user(scheduler, SCHEDULER_ROLE)

    one = client.get(reverse("scheduler:case_detail", args=[unit_1.case_id]))
    assert one.status_code == 200
    one_body = one.content.decode()
    assert "Desmarcar agendamento" in one_body
    assert reverse("scheduler:case_reopen", args=[unit_1.case_id]) in one_body
    assert "intercorrência desabilitada" not in one_body

    two = client.get(reverse("scheduler:case_detail", args=[unit_2.case_id]))
    assert two.status_code == 200
    two_body = two.content.decode()
    assert "intercorrência desabilitada" in two_body
    assert reverse("scheduler:case_reopen", args=[unit_2.case_id]) not in two_body


@pytest.mark.django_db
def test_reopen_flow_returns_case_to_awaiting_queue(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4/R6: desmarcar na unidade 1 → caso volta à aba aguardando em
    AWAITING_SCHEDULING com campos limpos, motivo persistido e comunicação ao NIR."""
    doctor = user_factory("medico-reopen-fluxo", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-reopen-fluxo", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    login_user(scheduler, SCHEDULER_ROLE)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])

    response = client.post(
        reverse("scheduler:case_reopen", args=[case.case_id]),
        {"reason": REOPEN_REASON},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_SCHEDULING
    assert case.scheduled_unit is None
    assert case.scheduled_datetime is None
    assert case.scheduled_by is None
    assert case.scheduling_reopen_reason == REOPEN_REASON
    messages_body = [m.body for m in _user_messages(case)]
    assert messages_body[-1] == REPLY_REOPEN_TEMPLATE.format(reason=REOPEN_REASON)

    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert REPLY_REOPEN_TEMPLATE.format(reason=REOPEN_REASON) in detail.content.decode()

    # Caso visível de novo na aba aguardando.
    queue = client.get(reverse("scheduler:queue"))
    assert queue.status_code == 200
    assert str(case.case_id) in queue.content.decode()


@pytest.mark.django_db
def test_reopen_unit_2_post_rejected(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4/R6: POST direto de intercorrência na unidade 2 → recusado com mensagem
    (serviço), caso inalterado — nunca 500."""
    doctor = user_factory("medico-reopen-u2", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-reopen-u2", (SCHEDULER_ROLE,))
    case = _confirmed_case(2, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    events_before = case.events.count()
    login_user(scheduler, SCHEDULER_ROLE)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])

    response = client.post(
        reverse("scheduler:case_reopen", args=[case.case_id]),
        {"reason": "motivo qualquer"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.scheduled_unit == 2
    assert case.events.count() == events_before
    assert case.scheduling_reopen_reason == ""

    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert "intercorrência desabilitada" in detail.content.decode()


# ── R5: estado errado no POST → mensagem + redirect, nunca 500 ─────────────


@pytest.mark.django_db
def test_wrong_state_post_no_500(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R5: POST de ação sobre caso já processado → mensagem + redirect ao
    detalhe, sem escrita nova (concorrência/estado inválido — nunca 500)."""
    doctor = user_factory("medico-wrong-state", (DOCTOR_ROLE,))
    first = user_factory("agendador-wrong-state-1", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=first)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])

    second = user_factory("agendador-wrong-state-2", (SCHEDULER_ROLE,))
    login_user(second, SCHEDULER_ROLE)
    token = _claim_for_post(case, second)
    # Depois do claim: o POST sobre estado inválido não grava evento novo.
    events_before = case.events.count()

    response = client.post(
        reverse("scheduler:case_confirm", args=[case.case_id]),
        _with_token(
            _confirm_payload(
                unit=1, scheduled_at=_future_local_datetime(), location="Bloco B — Sala 2"
            ),
            token,
        ),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.events.count() == events_before
    assert case.scheduled_by == first
    assert len(_user_messages(case)) == 1

    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert "indisponível no estado" in detail.content.decode()


# ── R7: PDF do caso processado pelo próprio agendador ─────────────────────


@pytest.mark.django_db
def test_pdf_own_processed_case_200(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R7: PDF servido por position com FileResponse + Cache-Control: no-store
    quando o caso foi processado pelo próprio agendador (multi-PDF: posições 1
    e 2 acessíveis)."""
    doctor = user_factory("medico-pdf-dono", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-pdf-dono", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _add_document(case, nir_user, position=1, content="um")
    _add_document(case, nir_user, position=2, content="dois")
    login_user(scheduler, SCHEDULER_ROLE)

    for position, content in ((1, "um"), (2, "dois")):
        response = client.get(reverse("scheduler:case_pdf", args=[case.case_id, position]))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response["Cache-Control"] == "no-store"
        served_bytes = b"".join(response.streaming_content)  # type: ignore[attr-defined]
        assert served_bytes == f"%PDF-1.4 conteudo {content} {position}".encode()


@pytest.mark.django_db
def test_pdf_other_scheduler_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R7: PDF de caso processado por OUTRO agendador → 404 fail-closed."""
    doctor = user_factory("medico-pdf-outro", (DOCTOR_ROLE,))
    owner = user_factory("agendador-pdf-outro-owner", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=owner)
    _add_document(case, nir_user, position=1, content="sigiloso")
    other = user_factory("agendador-pdf-outro", (SCHEDULER_ROLE,))
    login_user(other, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_pdf", args=[case.case_id, 1]))

    assert response.status_code == 404
    assert str(case.case_id) not in response.content.decode()


@pytest.mark.django_db
def test_pdf_reopened_case_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R7: caso reaberto por intercorrência (scheduled_by limpo) → 404, mesmo
    para quem o agendou antes."""
    doctor = user_factory("medico-pdf-reopen", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-pdf-reopen", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _add_document(case, nir_user, position=1, content="reaberto")
    reopen_scheduling_after_incident(
        case, reason=REOPEN_REASON, user=scheduler, role=SCHEDULER_ROLE
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_SCHEDULING
    assert case.scheduled_by is None
    login_user(scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_pdf", args=[case.case_id, 1]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_pre_decision_case_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R7: caso ainda aguardando (pré-decisão) nunca expõe o PDF."""
    doctor = user_factory("medico-pdf-pre", (DOCTOR_ROLE,))
    case = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _add_document(case, nir_user, position=1, content="pre")
    scheduler = user_factory("agendador-pdf-pre", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_pdf", args=[case.case_id, 1]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_missing_position_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R7: position sem documento no caso → 404."""
    doctor = user_factory("medico-pdf-pos", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-pdf-pos", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _add_document(case, nir_user, position=1, content="um")
    login_user(scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_pdf", args=[case.case_id, 9]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_detail_pdf_links_only_when_condition_holds(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R7: no detalhe, links de PDF aparecem só para o próprio agendador
    pós-decisão; outro agendador (detalhe permitido) não vê nenhum link.

    Ajuste citando R2 (slice PVP-004): a tabela "Documentos" foi substituída
    pelos dois cards do relatório no mesmo ponto do gate — a asserção de
    presença passa ao card "Visualizar PDF Original"."""
    doctor = user_factory("medico-pdf-links", (DOCTOR_ROLE,))
    owner = user_factory("agendador-pdf-links-owner", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=owner)
    _add_document(case, nir_user, position=1, content="um")
    _add_document(case, nir_user, position=2, content="dois")

    login_user(owner, SCHEDULER_ROLE)
    own = client.get(reverse("scheduler:case_detail", args=[case.case_id]))
    assert own.status_code == 200
    own_body = own.content.decode()
    assert reverse("scheduler:case_pdf", args=[case.case_id, 1]) in own_body
    assert reverse("scheduler:case_pdf", args=[case.case_id, 2]) in own_body
    assert "Visualizar PDF Original" in own_body

    other = user_factory("agendador-pdf-links-outro", (SCHEDULER_ROLE,))
    login_user(other, SCHEDULER_ROLE)
    foreign = client.get(reverse("scheduler:case_detail", args=[case.case_id]))
    assert foreign.status_code == 200
    foreign_body = foreign.content.decode()
    assert reverse("scheduler:case_pdf", args=[case.case_id, 1]) not in foreign_body
    assert "Documentos" not in foreign_body


@pytest.mark.django_db
def test_pdf_forbidden_for_non_scheduler_role(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R7: papel ativo fora da matriz → 403 sem servir o PDF."""
    doctor = user_factory("medico-pdf-rota", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-pdf-rota", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _add_document(case, nir_user, position=1, content="um")
    nir = user_factory("nir-pdf-agendador", (NIR_ROLE,))
    login_user(nir, NIR_ROLE)

    response = client.get(reverse("scheduler:case_pdf", args=[case.case_id, 1]))

    assert response.status_code == 403


# ── R2: cards sem o uid do caso (queues-remove-uid) ───────────────────────


def _card_slice(body: str, case_id: object) -> str:
    """Fatia o HTML do card do caso pelo wrapper ``data-scheduler-queue-card``.

    OLI-002 R3: pendentes sem ``list-group-item`` — o fatiamento usa o
    wrapper (nome também aparece no atributo, antes da abertura interna).
    Card processado usa o mesmo wrapper (markup preservado).
    """
    anchor = body.index(str(case_id))
    wrapper = body.rindex("data-scheduler-queue-card", 0, anchor)
    start = body.rindex("<div", 0, wrapper)
    nxt = body.find("data-scheduler-queue-card", anchor)
    end = body.rfind("<div", 0, nxt) if nxt != -1 else len(body)
    return body[start:end]


def _card_visible_text(card: str) -> str:
    """Corpo visível do card (após a tag de abertura), sem atributos de rota."""
    return re.sub(r'href="[^"]*"', "", card[card.index(">") + 1 :])


@pytest.mark.django_db
def test_queue_card_does_not_show_case_uid(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: o CORPO do card não exibe o uid — nº de ocorrência/paciente bastam."""
    doctor = user_factory("medico-sem-uid-agendador", (DOCTOR_ROLE,))
    case = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(case, patient_name="Paciente Sem Uid", agency_record_number="5040778")
    login_user(user_factory("geral-sem-uid", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    card = _card_slice(body, case.case_id)
    assert f"Caso {case.case_id}" not in card
    assert str(case.case_id) not in _card_visible_text(card)
    assert "Paciente Sem Uid" in card
    assert "Nº de ocorrência" in card
    assert "5040778" in card
    # A rota «Agendar» segue técnica (uid no href).
    assert str(case.case_id) in body


@pytest.mark.django_db
def test_queue_card_without_occurrence_has_no_uid(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2/D1 (opção a): sem nº de ocorrência → «—» + paciente, sem fallback uid."""
    doctor = user_factory("medico-sem-ocorrencia-agendador", (DOCTOR_ROLE,))
    case = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(case, patient_name="Paciente Sem Ocorrencia", agency_record_number="")
    login_user(user_factory("geral-sem-ocorrencia", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    card = _card_slice(body, case.case_id)
    assert "Nº de ocorrência" in card
    assert "Paciente Sem Ocorrencia" in card
    assert f"Caso {case.case_id}" not in card
    assert str(case.case_id) not in _card_visible_text(card)


# ── case-work-lock slice 004: reserva do caso no agendamento (R1–R8) ──────

# Aberturas possíveis do item de lista (OLI-002 R3: pendentes sem
# ``list-group-item``; o fatiamento usa o wrapper, não a abertura).
_LOCK_CARD_OPENINGS = ("data-scheduler-queue-card",)


def _extract_lock_token(body: str) -> str:
    """Token da reserva embutido no detalhe agendável (R1).

    O detalhe agendável reserva o caso e exporta o token nos dois forms de
    ação (confirmar/negar) — o POST precisa dele (R5).
    """
    match = re.search(r'name="lock_token" value="([0-9a-f-]{36})"', body)
    assert match is not None, "token da reserva ausente no detalhe agendável"
    return match.group(1)


def _with_token(payload: dict[str, str], token: str) -> dict[str, str]:
    """Payload do POST acrescido do token da reserva vigente (R5)."""
    return {**payload, "lock_token": token}


def _claim_for_post(case: Case, scheduler: User) -> str:
    """Reserva o caso para o agendador e devolve o token (setup dos POSTs).

    Equivale ao que a abertura do detalhe faz (R1): os testes existentes de
    confirmação/negação passam pelo mesmo gate de posse do fluxo real.
    """
    lock = claim_case_lock(case, user=scheduler, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)
    return str(lock.token)


def _lock_card_block(body: str, marker: object) -> str:
    """Fatia o card localizado pelo ``marker`` (uid ou nome único).

    Mesmo padrão do teste da fila médica: o uid só existe no corpo do card
    quando ele tem ação (``href``); o card reservado por outro agendador é um
    bloco sem link e é localizado pelo nome do paciente. O fatiamento usa o
    wrapper ``data-scheduler-queue-card`` (o nome também aparece no atributo
    do wrapper, antes da abertura interna do card).
    """
    anchor = body.index(str(marker))
    wrapper = body.rindex("data-scheduler-queue-card", 0, anchor)
    start = body.rindex("<div", 0, wrapper)
    nxt = body.find("data-scheduler-queue-card", anchor)
    end = body.rfind("<div", 0, nxt) if nxt != -1 else len(body)
    return body[start:end]


@pytest.mark.django_db
def test_open_schedulable_detail_claims_case(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1/D4: GET do detalhe agendável reserva o caso e embute o token.

    Claim com contexto ``scheduler_confirm``, papel ``scheduler`` e a lease
    default (300s — tarefa curta, sem override de contexto), com evento na
    trilha; o token chega nos DOIS forms de ação (confirmar e negar).
    """
    doctor = user_factory("medico-claim-detalhe", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-claim-detalhe", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    case.refresh_from_db()
    assert case.locked_by == scheduler
    assert case.lock_context == LOCK_CONTEXT
    assert case.lock_role == SCHEDULER_ROLE
    assert case.lock_token is not None
    assert case.locked_at is not None
    assert case.locked_until is not None and case.locked_until > timezone.now()
    # Lease default do agendador (D4): 300s, sem override por contexto.
    assert (case.locked_until - case.locked_at).total_seconds() == 300
    body = response.content.decode()
    # Token embutido nos TRÊS forms do detalhe: confirmar, negar (R1) e
    # «Sair sem decidir» (R8).
    assert body.count(f'name="lock_token" value="{case.lock_token}"') == 3
    assert CaseEventType.CASE_LOCK_CLAIMED in [
        event.event_type for event in case.events.order_by("id")
    ]


@pytest.mark.django_db
def test_other_scheduler_blocked(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2/D4: reserva ativa de outro agendador barra a abertura (aviso + fila).

    A reserva do portador fica INTACTA (token/portador/trilha) e a fila mostra
    o card como reservado — as duas unidades nunca se atropelam no mesmo caso.
    """
    doctor = user_factory("medico-reserva-agendador", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    holder = user_factory("agendador-reserva-a", (SCHEDULER_ROLE,))
    blocked = user_factory("agendador-reserva-b", (SCHEDULER_ROLE,))
    lock = claim_case_lock(case, user=holder, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)
    events_before = case.events.count()
    login_user(blocked, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("scheduler:queue")
    case.refresh_from_db()
    assert case.lock_token == lock.token
    assert case.locked_by == holder
    assert case.events.count() == events_before
    # Aviso de reserva com o portador, renderizado na fila após o redirect.
    body = client.get(reverse("scheduler:queue")).content.decode()
    assert "está reservado por" in body
    assert holder.display_name in body


@pytest.mark.django_db
def test_reopen_renews_own_lock(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2/D4: o mesmo agendador reabrindo o detalhe renova a reserva (token novo)."""
    doctor = user_factory("medico-renova-agendador", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-renova-propria", (SCHEDULER_ROLE,))
    first = claim_case_lock(case, user=scheduler, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)
    login_user(scheduler, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    case.refresh_from_db()
    assert case.locked_by == scheduler
    assert case.lock_token is not None and case.lock_token != first.token
    assert f'name="lock_token" value="{case.lock_token}"' in response.content.decode()


@pytest.mark.django_db
def test_processed_detail_no_claim(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R3/D4: caso processado abre read-only — sem claim, sem token, sem heartbeat."""
    doctor = user_factory("medico-detalhe-processado", (DOCTOR_ROLE,))
    owner = user_factory("agendador-processado-dono", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=owner)
    viewer = user_factory("agendador-processado-visita", (SCHEDULER_ROLE,))
    login_user(viewer, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    case.refresh_from_db()
    assert case.locked_by is None
    assert case.lock_token is None
    assert case.locked_until is None
    body = response.content.decode()
    assert 'name="lock_token"' not in body
    assert 'id="work-lock-config"' not in body
    assert not case.events.filter(event_type=CaseEventType.CASE_LOCK_CLAIMED).exists()


@pytest.mark.django_db
def test_confirm_without_valid_token_persists_nothing(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4/D4: confirmação sem posse → mensagem de reserva + redirect, sem escrita."""
    doctor = user_factory("medico-confirm-sem-token", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-confirm-sem-token", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    events_before = case.events.count()

    response = client.post(
        reverse("scheduler:case_confirm", args=[case.case_id]),
        _confirm_payload(
            unit=1,
            scheduled_at=timezone.make_aware(datetime(2030, 3, 10, 9, 30)),
            location="Bloco B — Sala 2",
        ),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.scheduled_unit is None
    assert case.scheduled_by is None
    assert case.events.count() == events_before
    assert _user_messages(case) == []
    assert LOCK_LOST_TEXT in client.get(detail_url).content.decode()


@pytest.mark.django_db
def test_deny_without_valid_token_persists_nothing(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4/D4: negação sem posse → mensagem de reserva + redirect, sem escrita."""
    doctor = user_factory("medico-deny-sem-token", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-deny-sem-token", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    events_before = case.events.count()

    response = client.post(
        reverse("scheduler:case_deny", args=[case.case_id]),
        {"reason": "vaga indisponível"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.scheduling_denial_reason == ""
    assert case.events.count() == events_before
    assert _user_messages(case) == []
    assert LOCK_LOST_TEXT in client.get(detail_url).content.decode()


@pytest.mark.django_db
def test_confirm_releases_lock(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R5/D4: confirmação com posse válida libera a reserva na MESMA transação."""
    doctor = user_factory("medico-confirm-libera", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-confirm-libera", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    token = _extract_lock_token(client.get(detail_url).content.decode())

    response = client.post(
        reverse("scheduler:case_confirm", args=[case.case_id]),
        _with_token(
            _confirm_payload(
                unit=1,
                scheduled_at=timezone.make_aware(datetime(2030, 3, 12, 9, 30)),
                location="Bloco B — Sala 2",
            ),
            token,
        ),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.scheduled_unit == 1
    assert case.locked_by is None
    assert case.locked_at is None
    assert case.locked_until is None
    assert case.lock_token is None
    assert case.lock_context == ""
    assert case.lock_role == ""
    release_events = case.events.filter(event_type=CaseEventType.CASE_LOCK_RELEASED)
    assert release_events.count() == 1
    assert release_events.get().payload["context"] == LOCK_CONTEXT


@pytest.mark.django_db
def test_confirm_window_expired_token_persists_nothing(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R5/D4 (janela): lease vencida ENTRE a abertura e o POST → nada persiste.

    A posse é validada sob row lock na MESMA transação da escrita: expirar a
    lease depois do render (aba aberta sem heartbeat) não deixa a confirmação
    passar.
    """
    doctor = user_factory("medico-janela-confirm", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-janela-confirm", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    token = _extract_lock_token(client.get(detail_url).content.decode())
    Case.objects.filter(pk=case.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    events_before = case.events.count()

    response = client.post(
        reverse("scheduler:case_confirm", args=[case.case_id]),
        _with_token(
            _confirm_payload(
                unit=1,
                scheduled_at=timezone.make_aware(datetime(2030, 3, 13, 9, 30)),
                location="Bloco B — Sala 2",
            ),
            token,
        ),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.scheduled_unit is None
    assert case.scheduled_by is None
    assert case.events.count() == events_before
    assert _user_messages(case) == []


@pytest.mark.django_db
def test_deny_window_expired_token_persists_nothing(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R5/D4 (janela): lease vencida ENTRE a abertura e o POST → negação não passa."""
    doctor = user_factory("medico-janela-deny", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-janela-deny", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    token = _extract_lock_token(client.get(detail_url).content.decode())
    Case.objects.filter(pk=case.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    events_before = case.events.count()

    response = client.post(
        reverse("scheduler:case_deny", args=[case.case_id]),
        _with_token({"reason": "vaga indisponível"}, token),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.scheduling_denial_reason == ""
    assert case.events.count() == events_before
    assert _user_messages(case) == []


@pytest.mark.django_db
def test_queue_cards_reflect_lock_state(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R6/D4: card da aba aguardando reflete a reserva (livre/meu/de outro).

    Livre → ação para o detalhe; do próprio agendador → «Continuar»; de outro →
    botão desabilitado «Reservado» com o portador e SEM ação de abrir (evita
    leitura duplicada entre as unidades).
    """
    doctor = user_factory("medico-fila-reserva-agendador", (DOCTOR_ROLE,))
    free = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(free, patient_name="Paciente Livre")
    mine = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(mine, patient_name="Paciente Meu")
    other = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(other, patient_name="Paciente Outro")
    scheduler = user_factory("agendador-fila-reserva", (SCHEDULER_ROLE,))
    other_scheduler = user_factory("agendador-fila-reserva-outro", (SCHEDULER_ROLE,))
    claim_case_lock(mine, user=scheduler, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)
    claim_case_lock(other, user=other_scheduler, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)
    login_user(scheduler, SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    free_card = _lock_card_block(body, free.case_id)
    assert "Agendar" in free_card
    assert "Abrir caso" not in free_card
    assert f'href="{reverse("scheduler:case_detail", args=[free.case_id])}"' in free_card

    mine_card = _lock_card_block(body, mine.case_id)
    assert "Continuar" in mine_card
    assert f'href="{reverse("scheduler:case_detail", args=[mine.case_id])}"' in mine_card

    # O card reservado não carrega o uid (sem href): localizar pelo paciente.
    other_card = _lock_card_block(body, "Paciente Outro")
    assert "Reservado" in other_card
    assert other_scheduler.display_name in other_card
    assert "disabled" in other_card
    assert f'href="{reverse("scheduler:case_detail", args=[other.case_id])}"' not in other_card


@pytest.mark.django_db
def test_queue_expires_stale_lock_before_rendering(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R6/D4: reserva vencida é expirada de forma lazy antes de montar a fila."""
    doctor = user_factory("medico-fila-reserva-expirada", (DOCTOR_ROLE,))
    case = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(case, patient_name="Paciente Expirado")
    holder = user_factory("agendador-reserva-expirada", (SCHEDULER_ROLE,))
    claim_case_lock(case, user=holder, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)
    Case.objects.filter(pk=case.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    login_user(holder, SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    card = _lock_card_block(body, case.case_id)
    assert "Agendar" in card
    assert "Reservado" not in card
    case.refresh_from_db()
    assert case.lock_token is None
    assert case.locked_by is None


@pytest.mark.django_db
def test_lock_renew_endpoint_extends_lease(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R7/D4: o heartbeat renova a lease do agendador e devolve ``locked_until``.

    A lease é encurtada antes da chamada para provar a renovação real (não o
    eco do valor do claim): JSON de sucesso com o novo vencimento e evento
    ``CASE_LOCK_RENEWED`` na trilha.
    """
    doctor = user_factory("medico-heartbeat-agendador", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-heartbeat", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    token = _extract_lock_token(client.get(detail_url).content.decode())
    shortened = timezone.now() + timedelta(minutes=1)
    Case.objects.filter(pk=case.pk).update(locked_until=shortened)

    response = client.post(
        reverse("scheduler:lock_renew", args=[case.case_id]),
        {"lock_token": token},
    )

    assert response.status_code == 200
    payload = cast(dict[str, Any], response.json())
    assert payload["success"] is True
    renewed_until = datetime.fromisoformat(cast(str, payload["locked_until"]))
    assert renewed_until > shortened + timedelta(minutes=3)
    case.refresh_from_db()
    assert case.locked_until is not None and case.locked_until > shortened
    assert (case.locked_until - timezone.now()).total_seconds() > 250
    assert case.events.filter(event_type=CaseEventType.CASE_LOCK_RENEWED).exists()


@pytest.mark.django_db
def test_lock_renew_endpoint_without_token_fails(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R7/D4: renovação sem ``lock_token`` (ou token errado) → falha, lease intacta.

    Nunca 500: o endpoint responde JSON de falha tanto para o token ausente
    quanto para o malformado (não-UUID).
    """
    doctor = user_factory("medico-heartbeat-sem-token-ag", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-heartbeat-sem-token", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    claim = claim_case_lock(case, user=scheduler, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)

    missing = client.post(reverse("scheduler:lock_renew", args=[case.case_id]), {})
    assert missing.status_code == 200
    assert cast(dict[str, Any], missing.json())["success"] is False
    malformed = client.post(
        reverse("scheduler:lock_renew", args=[case.case_id]),
        {"lock_token": "nao-e-uuid"},
    )
    assert malformed.status_code == 200
    assert cast(dict[str, Any], malformed.json())["success"] is False
    case.refresh_from_db()
    assert case.lock_token == claim.token
    assert not case.events.filter(event_type=CaseEventType.CASE_LOCK_RENEWED).exists()


@pytest.mark.django_db
def test_lock_release_endpoint_clears_lock_and_is_idempotent(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R7/D4: release explícito limpa a reserva; repetir devolve falha sem erro.

    O segundo POST (token já consumido) é o release best-effort disparado em
    navegação depois do POST da ação: nunca 500.
    """
    doctor = user_factory("medico-release-agendador", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-release-endpoint", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    token = _extract_lock_token(client.get(detail_url).content.decode())

    first = client.post(
        reverse("scheduler:lock_release", args=[case.case_id]), {"lock_token": token}
    )
    assert first.status_code == 200
    assert cast(dict[str, Any], first.json())["success"] is True
    case.refresh_from_db()
    assert case.locked_by is None
    assert case.lock_token is None
    assert case.locked_until is None
    release_events = case.events.filter(event_type=CaseEventType.CASE_LOCK_RELEASED)
    assert release_events.count() == 1
    assert release_events.get().payload["context"] == LOCK_CONTEXT

    second = client.post(
        reverse("scheduler:lock_release", args=[case.case_id]), {"lock_token": token}
    )
    assert second.status_code == 200
    assert cast(dict[str, Any], second.json())["success"] is False
    assert case.events.filter(event_type=CaseEventType.CASE_LOCK_RELEASED).count() == 1


@pytest.mark.django_db
def test_work_lock_config_rendered(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R7/D4: o detalhe agendável exporta a config do heartbeat, o aviso e o script."""
    doctor = user_factory("medico-config-lock-agendador", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-config-lock", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:case_detail", args=[case.case_id])).content.decode()
    token = _extract_lock_token(body)

    for marker in (
        'id="work-lock-config"',
        "data-work-lock-config",
        f'data-renew-url="{reverse("scheduler:lock_renew", args=[case.case_id])}"',
        f'data-release-url="{reverse("scheduler:lock_release", args=[case.case_id])}"',
        f'data-lock-token="{token}"',
        'data-heartbeat-ms="60000"',
        'data-grace-ms="240000"',
    ):
        assert 0 < body.find(marker), f"config do work lock ausente: {marker}"
    warning = body.find('id="work-lock-warning"')
    assert 0 < warning
    assert 'style="display:none;"' in body[warning : warning + 200]
    # O script do heartbeat é carregado pela tag (sem vazar comentário de template).
    assert 0 < body.find('<script src="/static/js/work_lock.js"')
    assert "{#" not in body


@pytest.mark.django_db
def test_case_leave_releases_and_redirects(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R8/D4: «Sair sem decidir» libera a reserva na hora e volta à fila.

    A liberação é server-side (não depende do JavaScript) e a fila mostra o
    flash informativo.
    """
    doctor = user_factory("medico-sai-sem-decidir-ag", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-sai-sem-decidir", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    # Um único GET: cada abertura renova a reserva com token novo (R2).
    body = client.get(detail_url).content.decode()
    token = _extract_lock_token(body)
    # O botão é um mini-form POST no detalhe agendável (R8).
    action = reverse("scheduler:case_leave", args=[case.case_id])
    form = body.find(f'<form method="post" action="{action}"')
    label = body.find(">Sair sem decidir</button>")
    assert 0 < form < label
    assert f'name="lock_token" value="{token}"' in body[form:label]

    response = client.post(action, {"lock_token": token})

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("scheduler:queue")
    case.refresh_from_db()
    assert case.locked_by is None
    assert case.lock_token is None
    assert case.locked_until is None
    release_events = case.events.filter(event_type=CaseEventType.CASE_LOCK_RELEASED)
    assert release_events.count() == 1
    assert release_events.get().payload["context"] == LOCK_CONTEXT
    assert "Reserva liberada" in client.get(reverse("scheduler:queue")).content.decode()


@pytest.mark.django_db
def test_case_leave_without_token_only_redirects(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R8/D4: sair sem token é idempotente — redirect à fila, reserva intacta."""
    doctor = user_factory("medico-sai-sem-token-ag", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    scheduler = user_factory("agendador-sai-sem-token", (SCHEDULER_ROLE,))
    claim = claim_case_lock(case, user=scheduler, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)
    login_user(scheduler, SCHEDULER_ROLE)

    response = client.post(reverse("scheduler:case_leave", args=[case.case_id]), {})

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("scheduler:queue")
    case.refresh_from_db()
    assert case.lock_token == claim.token
    assert case.locked_by == scheduler
    assert not case.events.filter(event_type=CaseEventType.CASE_LOCK_RELEASED).exists()


# ── case-detail-human-reference (slice 005): referência humana no detalhe
#    do agendador (R2) ──────────────────────────────────────────────────────


def _visible_text(body: str) -> str:
    """HTML sem os atributos de rota (``href``/``action``/``data-*-url``).

    O uid permanece como chave técnica das URLs (D5 do design); remover os
    atributos permite pinar que ele NÃO aparece em texto visível.
    """
    return re.sub(r'(?:href|action|data-[a-z-]*url)="[^"]*"', "", body)


@pytest.mark.django_db
def test_detail_uses_human_reference_without_full_uid(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: title e breadcrumb exibem «Caso <nº>»; uid completo só nos hrefs."""
    doctor = user_factory("medico-referencia-detalhe", (DOCTOR_ROLE,))
    case = _detail_case(nir_user, doctor)
    viewer = user_factory("geral-referencia-detalhe", (SCHEDULER_ROLE,))
    _login(client, viewer, SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:case_detail", args=[case.case_id])).content.decode()

    assert "— Caso 33345" in body
    assert 'aria-current="page">Caso 33345</li>' in body
    assert f"Caso {case.case_id}" not in body
    assert str(case.case_id) not in _visible_text(body)
    assert str(case.case_id) in body  # chave técnica das URLs (D5)


@pytest.mark.django_db
def test_detail_without_record_number_uses_short_uid_label(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: sem nº de ocorrência → «Caso uid <8 chars>» no title/breadcrumb."""
    doctor = user_factory("medico-referencia-fallback", (DOCTOR_ROLE,))
    case = _detail_case(nir_user, doctor)
    _apply_metadata(
        case,
        patient_name="MARIA DA SILVA",
        patient_birth_date=date(1972, 3, 5),
        agency_record_number="",
    )
    short_id = str(case.case_id)[:8]
    viewer = user_factory("geral-referencia-fallback", (SCHEDULER_ROLE,))
    _login(client, viewer, SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:case_detail", args=[case.case_id])).content.decode()

    assert f"— Caso uid {short_id}" in body
    assert f'aria-current="page">Caso uid {short_id}</li>' in body
    assert str(case.case_id) not in _visible_text(body)


# ── trilha-eventos-todos-perfis (slice 003): trilha no detalhe do agendador ──


@pytest.mark.django_db
def test_scheduler_presenter_mounts_event_trail(
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: o presenter monta ``event_trail`` (builder compartilhado de cases)."""
    doctor = user_factory("medico-trilha-contexto", (DOCTOR_ROLE,))
    case = _detail_case(nir_user, doctor)

    context = build_scheduler_case_detail_context(case)

    assert isinstance(context["event_trail"], list)
    assert context["event_trail"]


@pytest.mark.django_db
def test_detail_renders_event_trail_collapsed_with_creation_and_decision(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/cenário spec: o detalhe do agendador exibe a trilha ao final, FECHADA
    por padrão — fora dos gates de PDF/texto (caso pré-decisão, sem
    ``scheduled_by``) — com a criação (ator NIR) e a decisão médica (ator
    médico) em nome + papel pt-BR + data/hora; sumário completo preservado e nenhum
    artefato clínico além da trilha."""
    doctor = user_factory("medico-trilha-agendador", (DOCTOR_ROLE,))
    case = _detail_case(nir_user, doctor)
    # Declaração do NIR como ator da criação (mesmo padrão do detalhe médico —
    # o helper monta as rows direto no ORM, sem o evento).
    CaseEvent.objects.create(
        case=case,
        actor_type=ActorType.USER,
        actor=nir_user,
        actor_role="nir",
        event_type=CaseEventType.CASE_PROCEDURES_DECLARED,
        payload={},
    )
    viewer = user_factory("geral-trilha-agendador", (SCHEDULER_ROLE,))
    _login(client, viewer, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    # Gate 1: trilha presente em caso pré-decisão (fora do gate can_view_pdf —
    # sem cards de PDF/texto para quem não decidiu o agendamento).
    assert reverse("scheduler:case_pdf", args=[case.case_id, 1]) not in body
    assert "Texto Extraído do PDF" not in body
    assert "Trilha de eventos" in body
    trail = body.split("Trilha de eventos")[-1]
    # Card collapsible FECHADO por padrão (mesmo partial do painel).
    assert '<div class="collapse" id="case-event-trail">' in body
    assert 'aria-expanded="false"' in body
    assert 'data-bs-toggle="collapse"' in body
    assert "collapse show" not in body
    # Rótulos legíveis (nunca os tipos brutos) + atores com papel pt-BR.
    assert "Procedimentos declarados pelo NIR" in trail
    assert nir_user.username in trail
    assert "papel nir" in trail
    assert "Decisões médicas por procedimento registradas" in trail
    assert doctor.username in trail
    assert "papel médico" in trail
    assert "CASE_PROCEDURES_DECLARED" not in trail
    assert "CASE_DOCTOR_DECISIONS_RECORDED" not in trail
    # Data/hora localizada em cada evento.
    assert re.search(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", trail)
    # Sumário completo preservado (todas as linhas re-identificadas).
    assert "Primeira linha: paciente MARIA DA SILVA nascido em 05/03/1972." in body
    assert "Segunda linha oculta com 111.222.333-44 e detalhe clínico reservado." in body
    # Gate 2: nada de pipeline além da trilha (mesmos segredos do teste de
    # ausência de artefatos clínicos).
    for secret in (
        "estrutura oculta",
        "varfarina",
        "80000",
        "policy oculta",
        "recomenda_recusar",
        "sugestao oculta",
    ):
        assert secret not in body
