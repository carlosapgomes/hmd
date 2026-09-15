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
  diagnóstico resumido = primeira linha não-vazia de ``summary_text``
  re-identificada **só na renderização** (sem tokens na página), decisões
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

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import Case, CaseDocument, CaseProcedure, CaseStatus, MessageType
from apps.cases.procedures import record_doctor_procedure_decisions
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
) -> None:
    """Ajusta campos de exibição do caso (``created_at`` inclusa)."""
    Case.objects.filter(pk=case.pk).update(
        created_at=created_at or case.created_at,
        patient_name=patient_name,
        patient_birth_date=patient_birth_date,
        agency_record_number=agency_record_number,
        patient_age=patient_age,
        days_on_screen=days_on_screen,
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

    # follow=True: a home despacha o papel ativo à sua fila (change
    # painel-gerencial-e-home, slice 002); a navbar da página final segue
    # decidindo o link pelo papel ativo.
    response = client.get(reverse("home"), follow=True)

    assert response.status_code == 200
    assert (
        f'href="{reverse("scheduler:queue")}">Fila de agendamento</a>' in response.content.decode()
    )


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
    assert transitorio_confirmado.status == CaseStatus.SCHEDULING_CONFIRMED
    _apply_metadata(
        transitorio_confirmado, patient_name="Confirmado Transitorio", agency_record_number="1102"
    )
    transitorio_negado = _scheduler_requested_case(nir_user, doctor, (CARDIO_TYPE,))
    transitorio_negado.await_scheduling_confirmation(user=None, role=SYSTEM_ROLE)
    transitorio_negado.deny_scheduling(user=None, role=SYSTEM_ROLE)
    transitorio_negado.refresh_from_db()
    assert transitorio_negado.status == CaseStatus.SCHEDULING_DENIED
    _apply_metadata(
        transitorio_negado, patient_name="Negado Transitorio", agency_record_number="1103"
    )
    aguardando = _awaiting_scheduling_case(nir_user, doctor, (CARDIO_TYPE,))
    _apply_metadata(aguardando, patient_name="Aguardando Fila", agency_record_number="1104")
    login_user(user_factory("geral-processados", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

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
    assert "Aguardando confirmação de agendamento" in body
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
    """R2: card da aba ativa com idade, «⏱ Aguardando há» e «N d em tela»."""
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
    assert "6 d em tela" in body
    # P1 da review: data absoluta pré-existente preservada junto ao rótulo relativo
    assert "Recebido em " in body
    # P2 da review: dias em tela como badge Bootstrap de fato
    assert ">6 d em tela</span>" in body


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
    login_user(user_factory("geral-label-processado", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

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
    """R2 (P1 da review): ZERO é válido e DEVE exibir «0 a» e «0 d em tela»."""
    doctor = user_factory("medico-zero-tela-agendador", (DOCTOR_ROLE,))
    case = _awaiting_scheduling_case(nir_user, doctor, (ANGIO_TYPE,))
    _apply_metadata(case, patient_name="Recem Nascido", patient_age=0, days_on_screen=0)
    login_user(user_factory("geral-zero-tela", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    assert "Recem Nascido · 0 a" in body
    assert "0 d em tela" in body


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
    assert "d em tela" not in body


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
    login_user(user_factory("geral-cards-agendador", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

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
def test_detail_one_liner_reidentified(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3: one-liner re-identificado = primeira linha não-vazia do summary_text,
    re-identificada na renderização — sem tokens e sem as demais linhas."""
    doctor = user_factory("medico-oneliner", (DOCTOR_ROLE,))
    case = _detail_case(nir_user, doctor)
    viewer = user_factory("geral-oneliner", (SCHEDULER_ROLE,))
    _login(client, viewer, SCHEDULER_ROLE)

    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    # Primeira linha re-identificada com os valores reais do mapa.
    assert "Primeira linha: paciente MARIA DA SILVA nascido em 05/03/1972." in body
    # Demais linhas do resumo ausentes.
    assert "Segunda linha oculta" not in body
    # Nenhum token do mapa do caso sobrevive (escaped inclusive).
    for token in ("<PESSOA_1>", "<DATA_1>", "<CPF_1>"):
        assert token not in body
        assert token.replace("<", "&lt;").replace(">", "&gt;") not in body


@pytest.mark.django_db
def test_detail_has_no_clinical_artifacts(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3: structured_data/policy_result/suggested_action jamais renderizados —
    assert de ausência com os artefatos populados no caso."""
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
        "Segunda linha oculta",
        "detalhe clínico reservado",
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

    response = client.post(
        reverse("scheduler:case_confirm", args=[case.case_id]),
        _confirm_payload(unit=1, scheduled_at=scheduled_at, location=location),
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

    response = client.post(
        reverse("scheduler:case_confirm", args=[case.case_id]),
        _confirm_payload(unit=2, scheduled_at=scheduled_at, location="Unidade 2 (internet)"),
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

    response = client.post(
        reverse("scheduler:case_confirm", args=[case.case_id]),
        _confirm_payload(unit=1, scheduled_at=past_at, location="Sala 1"),
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

    response = client.post(
        reverse("scheduler:case_deny", args=[case.case_id]),
        {"reason": reason},
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
    events_before = case.events.count()
    scheduler = user_factory("agendador-deny-vazio", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])

    response = client.post(
        reverse("scheduler:case_deny", args=[case.case_id]),
        {"reason": "   "},
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
    events_before = case.events.count()
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])

    second = user_factory("agendador-wrong-state-2", (SCHEDULER_ROLE,))
    login_user(second, SCHEDULER_ROLE)

    response = client.post(
        reverse("scheduler:case_confirm", args=[case.case_id]),
        _confirm_payload(
            unit=1, scheduled_at=_future_local_datetime(), location="Bloco B — Sala 2"
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
    pós-decisão; outro agendador (detalhe permitido) não vê nenhum link."""
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
    assert "Documentos" in own_body

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
