"""Testes da fila médica e do access control (slice 002, R1–R6; D2/D5).

Cobre:
- R1: app ``doctor`` registrado e nav visível quando o papel ativo é
  ``doctor``/``admin`` (base.html);
- R2: guard ``role_required("doctor", "admin")`` — anônimo → redirect ao
  login; papéis ativos nir/scheduler/manager → 403; composição multi-role
  D2 (``doctor+manager`` com papel ativo ``doctor`` acessa a fila);
- R3: abas por estado (``aguardando`` default = ``AWAITING_DOCTOR``;
  ``decididos`` = ``DOCTOR_DENIED|DOCTOR_ACCEPTED|SCHEDULER_REQUESTED``),
  ordenação por tempo de tela (``days_on_screen`` desc, desempate FIFO por
  ``created_at``) e paginação (Paginator — primeira view paginada);
- R4: filtro de subtipo (``?subtype=``) com o mesmo predicado de D2 —
  médico com subtipos S vê só casos com ≥1 tipo declarado de subtipo em S;
  generalista e admin veem tudo e filtram por qualquer subtipo;
- R5: cards com os tipos declarados + badge de subtipo e contagem de
  pendentes por subtipo no cabeçalho;
- R6: matriz de acesso — ``can_access_case(user, case, *, active_role)``
  testado para todos os papéis ativos da matriz (admin/generalista/
  especialistas), com a linha da matriz resolvida pelo **papel ativo** (D2):
  usuário ``doctor+admin`` com papel ativo ``doctor`` segue a regra médica;
  ``is_superuser`` não é critério próprio (superuser sob papel ativo
  ``doctor`` + subtipos = médico comum).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.locks import claim_case_lock
from apps.cases.models import Case, CaseProcedure, CaseStatus
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.doctor.access import can_access_case

DOCTOR_ROLE = "doctor"
ADMIN_ROLE = "admin"
NIR_ROLE = "nir"
SYSTEM_ROLE = "system"

# Tipos declarados representativos por subtipo (catálogo).
ANGIO_TYPE = "art_perif"
CARDIO_TYPE = "cat_cardiaco"
NEURO_TYPE = "art_cerebral"
RADIO_TYPE = "dren_biliar"


def _advance_to_awaiting_doctor(case: Case) -> None:
    """Dirige o caso pelas transições do pipeline até ``AWAITING_DOCTOR``."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


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
        agency_record_number=agency_record_number,
        patient_age=patient_age,
        days_on_screen=days_on_screen,
        patient_gender=patient_gender,
        origin_unit=origin_unit,
    )
    case.refresh_from_db()


def _make_awaiting_case(
    created_by: User,
    procedure_types: Sequence[str],
    *,
    created_at: datetime | None = None,
    patient_name: str = "",
    agency_record_number: str = "",
    patient_age: int | None = None,
    days_on_screen: int | None = None,
    patient_gender: str = "",
    origin_unit: str = "",
) -> Case:
    """Caso em ``AWAITING_DOCTOR`` com os tipos declarados e metadados."""
    case = _create_case_with_declared(created_by, procedure_types)
    _advance_to_awaiting_doctor(case)
    _apply_metadata(
        case,
        created_at=created_at,
        patient_name=patient_name,
        agency_record_number=agency_record_number,
        patient_age=patient_age,
        days_on_screen=days_on_screen,
        patient_gender=patient_gender,
        origin_unit=origin_unit,
    )
    return case


def _make_decided_case(
    created_by: User,
    decided_by: User,
    procedure_types: Sequence[str],
    *,
    accepted: bool,
    chain_scheduling: bool = False,
) -> Case:
    """Caso decidido a partir de ``AWAITING_DOCTOR``.

    ``accepted=False`` → ``DOCTOR_DENIED``; ``accepted=True`` seguido de
    ``chain_scheduling=True`` → ``SCHEDULER_REQUESTED``; ``accepted=True``
    isolado → ``DOCTOR_ACCEPTED`` (estado transitório do encadeamento, D5).
    """
    case = _make_awaiting_case(created_by, procedure_types)
    case.record_doctor_decision(accepted=accepted, user=decided_by, role=DOCTOR_ROLE)
    if accepted and chain_scheduling:
        case.request_scheduling(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    return case


def _make_decided_case_with_decisions(
    created_by: User,
    decided_by: User,
    procedure_types: Sequence[str],
    *,
    decisions: dict[str, tuple[str, str]],
    decided_at_by_type: dict[str, datetime] | None = None,
    patient_name: str = "",
    agency_record_number: str = "",
    patient_age: int | None = None,
    patient_gender: str = "",
    origin_unit: str = "",
) -> Case:
    """Caso decidido pelo serviço (rows com disposição + ``doctor_decided_at``).

    ``decisions`` é o mapa ``{tipo: (disposição, motivo)}`` do serviço
    ``record_doctor_procedure_decisions`` (tudo negado → ``DOCTOR_DENIED``;
    ≥1 aprovado → ``SCHEDULER_REQUESTED``). ``decided_at_by_type`` sobrescreve
    o instante persistido por row para provas de horário por procedimento.
    """
    case = _make_awaiting_case(
        created_by,
        procedure_types,
        patient_name=patient_name,
        agency_record_number=agency_record_number,
        patient_age=patient_age,
        patient_gender=patient_gender,
        origin_unit=origin_unit,
    )
    record_doctor_procedure_decisions(case, decisions, user=decided_by, role=DOCTOR_ROLE)
    case.refresh_from_db()
    if decided_at_by_type:
        for procedure_type, decided_at in decided_at_by_type.items():
            CaseProcedure.objects.filter(case=case, procedure_type=procedure_type).update(
                doctor_decided_at=decided_at
            )
        case.refresh_from_db()
    return case


def _format_local_datetime(value: datetime) -> str:
    """Data/hora como o template a renderiza (``d/m/Y H:i`` no fuso local)."""
    return timezone.localtime(value).strftime("%d/%m/%Y %H:%M")


# ── R2: guard por papel ativo ─────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["nir", "scheduler", "manager"])
def test_queue_forbidden_for_non_doctor_roles(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
    role: str,
) -> None:
    """R2: papel ativo fora de doctor/admin → HTTP 403 (matriz D2)."""
    user = user_factory(f"usuario-{role}", (role,))
    login_user(user, role)

    response = client.get(reverse("doctor:queue"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_anonymous_redirects_login(client: Client) -> None:
    """R2: anônimo → redirect ao login (302, semântica do role_required)."""
    response = client.get(reverse("doctor:queue"))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
def test_manager_doctor_composite_queues_under_doctor_active(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """D2: manager sozinho nunca acessa; doctor+manager acessa sob papel ativo doctor."""
    manager_medico = user_factory("gerente-medico", ("doctor", "manager"))
    login_user(manager_medico, DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"))

    assert response.status_code == 200


# ── R1: app registrado + nav por papel ativo ──────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["doctor", "admin"])
def test_nav_visible_for_doctor(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
    role: str,
) -> None:
    """R1: nav da fila visível na home quando o papel ativo é doctor/admin."""
    user = user_factory(f"usuario-{role}", (role,))
    login_user(user, role)

    # follow=True: a home despacha o papel ativo à sua área (change
    # painel-gerencial-e-home, slice 002); a navegação da página final
    # segue decidindo os links pelo papel ativo — doctor cai na fila,
    # admin cai no painel com acesso cruzado às duas filas (slice 004,
    # correção de regressão: rótulos no vocabulário aprovado).
    response = client.get(reverse("home"), follow=True)
    body = response.content.decode()

    assert response.status_code == 200
    # Ligado (fix-2, P2): destino E rótulo aprovado do papel na MESMA
    # âncora — doctor usa a faixa da fila ("Pendentes"); admin usa o
    # acesso cruzado do painel ("Pendentes (médica)"). A asserção fraca
    # antiga (href num lugar + ">Pendentes" noutro) aceitava o rótulo
    # da outra área nesta âncora.
    if role == "doctor":
        expected_anchor = f'href="{reverse("doctor:queue")}?tab=aguardando">Pendentes</a>'
    else:
        # Acesso cruzado do base.html: href seguido de aria-label.
        expected_anchor = (
            f'href="{reverse("doctor:queue")}?tab=aguardando" '
            'aria-label="Pendentes — fila médica">Pendentes (médica)</a>'
        )
    assert expected_anchor in body


@pytest.mark.django_db
def test_nav_hidden_for_nir(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1: nav da fila ausente para papel ativo fora de doctor/admin."""
    user = user_factory("regulador-nir", (NIR_ROLE,))
    login_user(user, NIR_ROLE)

    response = client.get(reverse("home"), follow=True)

    assert response.status_code == 200
    assert reverse("doctor:queue") not in response.content.decode()


# ── R3: abas por estado + ordem por tempo de tela + paginação ────────────


@pytest.mark.django_db
def test_queue_items_link_to_case_detail(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Fila → página de trabalho/detalhe (case-work-lock slice 001).

    A aba ``aguardando`` abre a página de trabalho (``doctor:case_decide`` —
    leitura completa + formulário no mesmo fluxo); a aba ``decididos`` segue no
    detalhe read-only, onde vivem as decisões registradas.
    """
    awaiting = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-link", (DOCTOR_ROLE,))
    decided = _make_decided_case_with_decisions(
        nir_user, doctor, (CARDIO_TYPE,), decisions={CARDIO_TYPE: ("denied", "")}
    )
    login_user(doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"))

    assert response.status_code == 200
    body = response.content.decode()
    assert f'href="{reverse("doctor:case_decide", args=[awaiting.case_id])}"' in body

    decided_body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()
    assert f'href="{reverse("doctor:case_detail", args=[decided.case_id])}"' in decided_body


@pytest.mark.django_db
def test_awaiting_card_links_to_decide(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1 (case-work-lock slice 001): o card da aba aguardando abre a página de
    trabalho (leitura + decisão) — o detalhe read-only NÃO é o alvo."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    login_user(user_factory("medico-card-decide", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    card = _card_slice(body, case.case_id)
    decide_href = f'href="{reverse("doctor:case_decide", args=[case.case_id])}"'
    # ``find`` devolve -1 quando não acha — exigir o href real no card.
    assert card.find(decide_href) > 0
    detail_href = f'href="{reverse("doctor:case_detail", args=[case.case_id])}"'
    # O href de detalhe é SUBSTRING do href de decisão (…/case/<uid>/decide/):
    # comparar o atributo completo, nunca a URL solta.
    assert detail_href not in card


@pytest.mark.django_db
def test_queue_awaiting_default(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R3: aba default ``aguardando`` lista só casos ``AWAITING_DOCTOR``."""
    awaiting = _make_awaiting_case(
        nir_user, (ANGIO_TYPE,), patient_name="Paciente Aguardando", agency_record_number="1000"
    )
    doctor = user_factory("medico-negado", (DOCTOR_ROLE,))
    denied = _make_decided_case(nir_user, doctor, (CARDIO_TYPE,), accepted=False)
    Case.objects.filter(pk=denied.pk).update(patient_name="Paciente Negado")
    denied.refresh_from_db()
    login_user(user_factory("geral-vista", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"))

    assert response.status_code == 200
    body = response.content.decode()
    assert str(awaiting.case_id) in body
    assert "Paciente Aguardando" in body
    # O caso decidido (DOCTOR_DENIED) fica fora da aba default.
    assert "Paciente Negado" not in body


@pytest.mark.django_db
def test_queue_decided_tab(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R3: aba ``decididos`` lista DOCTOR_DENIED|DOCTOR_ACCEPTED|SCHEDULER_REQUESTED.

    OLI-003: os casos do serviço carregam disposição por row — os badges de
    resultado (``Aprovado``/``Negado``) substituem o rótulo de estado do caso.
    """
    doctor = user_factory("medico-decidido", (DOCTOR_ROLE,))
    denied = _make_decided_case_with_decisions(
        nir_user, doctor, (ANGIO_TYPE,), decisions={ANGIO_TYPE: ("denied", "")}
    )
    # Slice 005: a aba decididos é a janela do dia por decisor — a decisão
    # aprovada de hoje (qualquer status atual) segue listada.
    accepted = _make_decided_case_with_decisions(
        nir_user, doctor, (CARDIO_TYPE,), decisions={CARDIO_TYPE: ("approved", "")}
    )
    scheduled = _make_decided_case_with_decisions(
        nir_user, doctor, (NEURO_TYPE,), decisions={NEURO_TYPE: ("approved", "")}
    )
    awaiting = _make_awaiting_case(nir_user, (RADIO_TYPE,))
    # Slice 005: a aba decididos é escopada ao decisor.
    login_user(doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"), {"tab": "decididos"})

    assert response.status_code == 200
    body = response.content.decode()
    assert str(denied.case_id) in body
    assert str(accepted.case_id) in body
    assert str(scheduled.case_id) in body
    assert str(awaiting.case_id) not in body
    assert "Negado" in body
    assert "Aprovado" in body
    # Sem agregação case-level: nenhum badge genérico de estado do caso.
    assert "Negado pelo médico" not in body
    assert "status-done" not in body
    assert "status-badge" not in body


@pytest.mark.django_db
def test_queue_invalid_tab_falls_back_to_awaiting(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R3: valor de aba desconhecido cai no default ``aguardando``."""
    awaiting = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Só Aguardando")
    doctor = user_factory("medico-tab", (DOCTOR_ROLE,))
    decided = _make_decided_case(nir_user, doctor, (CARDIO_TYPE,), accepted=False)
    login_user(user_factory("geral-tab", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"), {"tab": "historico"})

    assert response.status_code == 200
    body = response.content.decode()
    assert str(awaiting.case_id) in body
    assert "Só Aguardando" in body
    assert str(decided.case_id) not in body
    # Revisão OLI-001: sem badge genérico de status nos pendentes.
    assert "Aguardando decisão médica" not in body
    assert "status-badge" not in body
    assert "Casos aguardando decisão" in body
    assert "Aguardando há" in body
    assert "Negado pelo médico" not in body


@pytest.mark.django_db
def test_queue_paginated_fifo(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R3/R5: desempate FIFO por ``created_at`` paginado (20/página) + contagem total.

    Cria 25 casos aguardando (todos do subtipo angio, TODOS sem
    ``days_on_screen``) com ``created_at`` controlado e crescente: o
    desempate FIFO do contrato novo põe os 20 mais antigos na página 1 e os 5
    restantes na página 2 — a contagem do cabeçalho cobre o conjunto todo (25).
    """
    base = timezone.now()
    for index in range(25):
        _make_awaiting_case(
            nir_user,
            (ANGIO_TYPE,),
            created_at=base - timedelta(seconds=25 - index),
            patient_name=f"Paciente {index:02d}",
            agency_record_number=f"AR-{index:02d}",
        )
    login_user(user_factory("geral-fifo", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    first = client.get(reverse("doctor:queue"))
    assert first.status_code == 200
    first_body = first.content.decode()
    # Página 1: os 20 casos mais antigos (Paciente 00…19), nunca o mais novo.
    assert "Paciente 00" in first_body
    assert "Paciente 19" in first_body
    assert "Paciente 20" not in first_body
    assert "Paciente 24" not in first_body
    assert "Página 1 de 2" in first_body
    # Contagem de pendentes do cabeçalho cobre o conjunto inteiro, não a página.
    assert "angio (25)" in first_body
    assert "Pendentes por subtipo" not in first_body

    second = client.get(reverse("doctor:queue"), {"page": "2"})
    assert second.status_code == 200
    second_body = second.content.decode()
    assert "Paciente 20" in second_body
    assert "Paciente 24" in second_body
    assert "Paciente 00" not in second_body
    assert "Página 2 de 2" in second_body


# ── R1/R2: ordem por tempo de tela + identificação e tempo no card ────────


@pytest.mark.django_db
def test_queue_ordered_by_days_on_screen(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1: ordem por ``days_on_screen`` desc (None ao fim), NÃO FIFO.

    ``created_at`` CONFLITANTES: o caso sem cabeçalho é o MAIS ANTIGO e o de
    10 dias é o MAIS RECENTE — a ordenação antiga (FIFO por ``created_at``)
    produziria None, 3, 10 e este teste falharia.
    """
    base = timezone.now()
    no_header = _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        created_at=base - timedelta(hours=3),
        patient_name="Paciente Sem Cabecalho",
    )
    three_days = _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        created_at=base - timedelta(hours=2),
        patient_name="Paciente Tres Dias",
        days_on_screen=3,
    )
    ten_days = _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        created_at=base - timedelta(hours=1),
        patient_name="Paciente Dez Dias",
        days_on_screen=10,
    )
    login_user(user_factory("medico-sort-tela", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"))

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
    """OLI-001 R3/R5: card pendente com idade, espera única e «Dias em tela: N»."""
    _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente Identificado",
        patient_age=84,
        days_on_screen=6,
        patient_gender="Feminino",
        origin_unit="Unidade 1",
    )
    login_user(user_factory("medico-card-tela", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    assert "Paciente Identificado" in body
    assert "84 a" in body
    assert "Feminino" in body
    assert "Unidade 1" in body
    assert body.count("Aguardando há") == 1
    assert "Dias em tela: 6" in body
    assert "6 d em tela" not in body
    assert "Recebido em " not in body


@pytest.mark.django_db
def test_queue_decided_card_shows_result_without_waiting(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-003 R2/R5: card decidido orientado ao resultado, sem espera."""
    doctor = user_factory("medico-label-aba", (DOCTOR_ROLE,))
    decided_at = timezone.localtime(timezone.now() - timedelta(hours=2))
    decided = _make_decided_case_with_decisions(
        nir_user,
        doctor,
        (ANGIO_TYPE,),
        decisions={ANGIO_TYPE: ("approved", "")},
        decided_at_by_type={ANGIO_TYPE: decided_at},
        patient_name="Paciente Decidido",
        patient_age=84,
    )
    # Slice 005: a aba decididos é escopada ao decisor.
    login_user(doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()

    card = _card_block(body, decided.case_id)
    assert "Paciente Decidido" in card
    assert "84 a" in card
    assert "Arteriografia periférica" in card
    assert "angio" in card
    assert "Aprovado" in card
    assert "text-bg-success" in card
    assert f"Decidido em {_format_local_datetime(decided_at)}" in card
    # R5: nenhuma temporalidade de espera/recebimento no card decidido.
    assert "Aguardando há" not in card
    assert "Recebido há" not in card
    assert "Recebido em" not in card
    assert "waiting-time" not in card
    # R3: sem badge genérico de estado do caso.
    assert "status-badge" not in card
    assert "status-done" not in card
    # R4: CTA outline real para o detalhe read-only.
    assert "Ver detalhes" in card
    assert "Abrir caso" not in card


@pytest.mark.django_db
def test_queue_decided_mixed_dispositions_per_procedure(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-003 R3: aprovado + negado no mesmo caso, sem agregação."""
    doctor = user_factory("medico-misto", (DOCTOR_ROLE,))
    approved_at = timezone.localtime(timezone.now() - timedelta(hours=3))
    denied_at = timezone.localtime(timezone.now() - timedelta(hours=1))
    mixed = _make_decided_case_with_decisions(
        nir_user,
        doctor,
        (ANGIO_TYPE, CARDIO_TYPE),
        decisions={ANGIO_TYPE: ("approved", ""), CARDIO_TYPE: ("denied", "motivo no detalhe")},
        decided_at_by_type={ANGIO_TYPE: approved_at, CARDIO_TYPE: denied_at},
        patient_name="Paciente Misto",
        agency_record_number="555777",
        patient_age=71,
        patient_gender="Masculino",
        origin_unit="Unidade 2",
    )
    # Slice 005: a aba decididos é escopada ao decisor.
    login_user(doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()

    card = _card_block(body, mixed.case_id)
    # Identidade/demografia do caso.
    assert "Paciente Misto" in card
    assert "555777" in card
    assert "71 a" in card
    assert "Masculino" in card
    assert "Unidade 2" in card
    # Cada procedimento com label, subtipo, disposição e horário próprios.
    assert "Arteriografia periférica" in card
    assert "Cateterismo cardíaco" in card
    assert "Aprovado" in card
    assert "Negado" in card
    assert "text-bg-success" in card
    assert "text-bg-danger" in card
    assert f"Decidido em {_format_local_datetime(approved_at)}" in card
    assert f"Decidido em {_format_local_datetime(denied_at)}" in card
    # Motivo da negativa fica no detalhe, não no card (densidade).
    assert "motivo no detalhe" not in card
    # Sem resultado agregado case-level nem espera.
    assert "status-badge" not in card
    assert "status-done" not in card
    assert "waiting-time" not in card
    assert "Aguardando há" not in card
    assert "Recebido há" not in card


@pytest.mark.django_db
def test_queue_decided_card_structure_and_cta(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-003 R4: ``.case-card > .row`` 8/4 e CTA «Ver detalhes» real."""
    doctor = user_factory("medico-cta-dec", (DOCTOR_ROLE,))
    decided = _make_decided_case_with_decisions(
        nir_user,
        doctor,
        (ANGIO_TYPE,),
        decisions={ANGIO_TYPE: ("denied", "")},
        patient_name="Paciente Cta Decidido",
        agency_record_number="OLI003",
    )
    # Slice 005: a aba decididos é escopada ao decisor.
    login_user(doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()

    card = _card_block(body, decided.case_id)
    assert '<div class="case-card card p-3">' in card
    assert '<div class="row">' in card
    assert "col-lg-8" in card
    assert "col-lg-4" in card
    assert card.index("col-lg-8") < card.index("col-lg-4")
    assert "list-group-item" not in card
    detail_href = f'href="{reverse("doctor:case_detail", args=[decided.case_id])}"'
    assert detail_href in card
    assert "stretched-link" in card
    assert "btn-outline-primary" in card
    assert ">Ver detalhes</a>" in card
    # O detalhe é read-only: o destino não é a página de trabalho.
    decide_href = f'href="{reverse("doctor:case_decide", args=[decided.case_id])}"'
    assert decide_href not in card


@pytest.mark.django_db
def test_queue_decided_empty_state_is_info_alert(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-003 R5/D10: vazio de decididos é um único ``alert-info``."""
    login_user(user_factory("medico-oli003-vazio", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()

    assert "alert alert-info" in body
    assert "Nenhum caso decidido hoje" in body
    assert "card shadow-sm" not in body
    fragment = body.split('id="doctor-queue-content"', 1)[1]
    assert fragment.count("alert alert-info") == 1
    assert "data-doctor-queue-card" not in fragment


@pytest.mark.django_db
def test_queue_card_zero_age_and_zero_days_on_screen(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2 (P1 da review): ZERO é válido e DEVE exibir «0 a» e «0 d em tela»."""
    _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Recem Nascido",
        patient_age=0,
        days_on_screen=0,
    )
    login_user(user_factory("medico-zero-tela", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    assert "Recem Nascido" in body
    assert "0 a" in body
    assert "Dias em tela: 0" in body
    assert "0 d em tela" not in body


@pytest.mark.django_db
def test_queue_card_absent_age_and_days_on_screen(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: idade/dias ausentes NÃO geram sufixo nem badge (sem «— a»)."""
    _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente Sem Demografia",
    )
    login_user(user_factory("medico-sem-demo", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    assert "Paciente Sem Demografia" in body
    assert "Paciente Sem Demografia · " not in body
    assert "Dias em tela" not in body


# ── R4: filtro de subtipo (predicado D2) ─────────────────────────────────


@pytest.mark.django_db
def test_specialized_doctor_sees_only_his_subtypes(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
    assign_specialties: Callable[[User, Sequence[str]], None],
) -> None:
    """R4: médico com subtipo S só vê casos com ≥1 tipo declarado de subtipo em S."""
    angio_case = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Caso Angio")
    cardio_case = _make_awaiting_case(nir_user, (CARDIO_TYPE,), patient_name="Caso Cardio")
    doctor = user_factory("doc-angio", (DOCTOR_ROLE,))
    assign_specialties(doctor, ("angio",))
    login_user(doctor, DOCTOR_ROLE)

    # Default (Todas): o caso cardio é invisível — o predicado de acesso filtra.
    all_response = client.get(reverse("doctor:queue"))
    all_body = all_response.content.decode()
    assert str(angio_case.case_id) in all_body
    assert str(cardio_case.case_id) not in all_body
    # Dropdown só com os subtipos do usuário (OLI-001 R2: opção com contagem).
    assert "angio (1)" in all_body
    assert 'value="cardio"' not in all_body

    # Filtro ?subtype=angio mantém exatamente o conjunto visível.
    filtered = client.get(reverse("doctor:queue"), {"subtype": "angio"})
    filtered_body = filtered.content.decode()
    assert str(angio_case.case_id) in filtered_body
    assert str(cardio_case.case_id) not in filtered_body


@pytest.mark.django_db
def test_generalist_sees_all(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4: generalista (sem subtipo) vê casos de qualquer subtipo."""
    angio_case = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Caso Angio")
    cardio_case = _make_awaiting_case(nir_user, (CARDIO_TYPE,), patient_name="Caso Cardio")
    login_user(user_factory("geral-tudo", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"))
    body = response.content.decode()
    assert str(angio_case.case_id) in body
    assert str(cardio_case.case_id) in body
    # Dropdown com todos os subtipos do catálogo (OLI-001 R2: com contagens).
    for subtype in ("angio", "neuro", "cardio", "radio"):
        assert f'value="{subtype}"' in body
        assert f"{subtype} (" in body


@pytest.mark.django_db
def test_admin_sees_all(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4: papel ativo admin vê casos de qualquer subtipo e filtra por qualquer um."""
    angio_case = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Caso Angio")
    cardio_case = _make_awaiting_case(nir_user, (CARDIO_TYPE,), patient_name="Caso Cardio")
    admin = user_factory("admin-fila", (ADMIN_ROLE,))
    login_user(admin, ADMIN_ROLE)

    response = client.get(reverse("doctor:queue"))
    body = response.content.decode()
    assert str(angio_case.case_id) in body
    assert str(cardio_case.case_id) in body

    filtered = client.get(reverse("doctor:queue"), {"subtype": "cardio"})
    filtered_body = filtered.content.decode()
    assert str(cardio_case.case_id) in filtered_body
    assert str(angio_case.case_id) not in filtered_body


@pytest.mark.django_db
def test_subtype_filter_narrows_generalist_queue(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4: ?subtype= inclui/exclui corretamente para quem vê tudo (generalista)."""
    angio_case = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Caso Angio")
    cardio_case = _make_awaiting_case(nir_user, (CARDIO_TYPE,), patient_name="Caso Cardio")
    login_user(user_factory("geral-filtro", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"), {"subtype": "cardio"})

    assert response.status_code == 200
    body = response.content.decode()
    assert str(cardio_case.case_id) in body
    assert str(angio_case.case_id) not in body


@pytest.mark.django_db
def test_doctor_admin_user_under_active_doctor_sees_only_his_subtypes(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
    assign_specialties: Callable[[User, Sequence[str]], None],
) -> None:
    """P1/D2: composição multi-role — papel ativo ``doctor`` restringe mesmo
    para usuário que também tem o papel ``admin`` (admin sozinho nunca
    decide; a linha da matriz é a do papel ativo).
    """
    angio_case = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Caso Angio")
    cardio_case = _make_awaiting_case(nir_user, (CARDIO_TYPE,), patient_name="Caso Cardio")
    user = user_factory("doc-admin-fila", (DOCTOR_ROLE, ADMIN_ROLE))
    assign_specialties(user, ("cardio",))
    login_user(user, DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"))
    body = response.content.decode()
    assert str(cardio_case.case_id) in body
    assert str(angio_case.case_id) not in body


@pytest.mark.django_db
def test_multi_procedure_case_included_when_any_type_in_subtype(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
    assign_specialties: Callable[[User, Sequence[str]], None],
) -> None:
    """D2: caso com 1 tipo declarado em S e 1 fora de S é acessível ao
    especialista (interseção não vazia) — coberto também na view, não só no
    predicado (R4/consistência fila×detalhe).
    """
    mixed = _make_awaiting_case(nir_user, (CARDIO_TYPE, ANGIO_TYPE), patient_name="Caso Misto")
    doctor = user_factory("doc-misto-fila", (DOCTOR_ROLE,))
    assign_specialties(doctor, ("cardio",))
    login_user(doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"))

    assert response.status_code == 200
    body = response.content.decode()
    assert str(mixed.case_id) in body
    # O card lista os dois tipos declarados (badges de subtipo por tipo).
    assert "Cateterismo cardíaco" in body
    assert "Arteriografia periférica" in body


@pytest.mark.django_db
def test_neurointerv_routed_to_neuro_only(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
    assign_specialties: Callable[[User, Sequence[str]], None],
) -> None:
    """S1/R11 (matriz D2): caso com ``neurointerv`` é visível ao médico
    restrito a ``neuro``; médico restrito a ``cardio`` não o vê."""
    case = _make_awaiting_case(nir_user, ("neurointerv",), patient_name="Caso Neuro")
    neuro = user_factory("doc-neurointerv-neuro", (DOCTOR_ROLE,))
    assign_specialties(neuro, ("neuro",))
    cardio = user_factory("doc-neurointerv-cardio", (DOCTOR_ROLE,))
    assign_specialties(cardio, ("cardio",))

    login_user(neuro, DOCTOR_ROLE)
    neuro_body = client.get(reverse("doctor:queue")).content.decode()
    assert str(case.case_id) in neuro_body

    login_user(cardio, DOCTOR_ROLE)
    cardio_body = client.get(reverse("doctor:queue")).content.decode()
    assert str(case.case_id) not in cardio_body


@pytest.mark.django_db
def test_neurointerv_multi_procedure_visible_to_neuro(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
    assign_specialties: Callable[[User, Sequence[str]], None],
) -> None:
    """S1/R11 (matriz D2): caso multiprocedimento (``neurointerv`` + tipo de
    outro subtipo) é visível ao ``neuro`` pela interseção."""
    mixed = _make_awaiting_case(
        nir_user, ("neurointerv", CARDIO_TYPE), patient_name="Caso Misto Neuro"
    )
    neuro = user_factory("doc-neurointerv-misto", (DOCTOR_ROLE,))
    assign_specialties(neuro, ("neuro",))
    login_user(neuro, DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"))

    assert response.status_code == 200
    body = response.content.decode()
    assert str(mixed.case_id) in body
    assert "Neurointervenção" in body
    assert "Cateterismo cardíaco" in body


# ── R5: cards com tipos declarados + contagem de pendentes no cabeçalho ───


@pytest.mark.django_db
def test_queue_shows_types_and_pending_counts(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R5: cada card lista os tipos declarados com badge de subtipo; o
    cabeçalho mostra a contagem de pendentes por subtipo do filtro do usuário.
    """
    _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Ana Souza",
        agency_record_number="1001",
    )
    _make_awaiting_case(
        nir_user,
        (NEURO_TYPE,),
        patient_name="Beto Lima",
        agency_record_number="1002",
    )
    _make_awaiting_case(
        nir_user,
        (RADIO_TYPE,),
        patient_name="Caio Reis",
        agency_record_number="1003",
    )
    login_user(user_factory("geral-cards", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"))
    assert response.status_code == 200
    body = response.content.decode()

    # Campos de exibição por card.
    assert "Ana Souza" in body
    assert "1001" in body
    assert "Beto Lima" in body
    assert "1002" in body
    assert "Caio Reis" in body
    assert "1003" in body
    # Revisão OLI-001: pendentes sem badge genérico ``status-badge``.
    assert "Aguardando decisão médica" not in body
    assert "status-badge" not in body
    assert "Aguardando há" in body

    # Tipos declarados com badge de subtipo por card.
    assert "Arteriografia periférica" in body
    assert "Arteriografia cerebral" in body
    assert "Drenagem biliar percutânea" in body

    # OLI-001 R2: contagens nas opções do filtro server-side; sem fileira de badges.
    assert "Pendentes por subtipo" not in body
    assert "angio (1)" in body
    assert "neuro (1)" in body
    assert "radio (1)" in body
    assert "cardio (0)" in body


# ── R6: matriz de acesso (predicado único can_access_case) ────────────────


@pytest.mark.django_db
class TestCanAccessCase:
    """R6: matriz D2 fechada — linha por papel ativo (assinatura nova): admin
    = tudo; doctor generalista = tudo; doctor com subtipos S = caso com ≥1
    tipo declarado em S; ``is_superuser`` não é critério próprio."""

    def test_generalist_accesses_any_case(
        self,
        user_factory: Callable[..., User],
        nir_user: User,
    ) -> None:
        doctor = user_factory("geral-matriz", (DOCTOR_ROLE,))
        case = _create_case_with_declared(nir_user, (CARDIO_TYPE,))
        assert can_access_case(doctor, case, active_role=DOCTOR_ROLE) is True

    def test_admin_accesses_any_case_even_with_specialties(
        self,
        user_factory: Callable[..., User],
        assign_specialties: Callable[[User, Sequence[str]], None],
        nir_user: User,
    ) -> None:
        admin = user_factory("admin-matriz", (ADMIN_ROLE,))
        assign_specialties(admin, ("angio",))
        cardio_case = _create_case_with_declared(nir_user, (CARDIO_TYPE,))
        angio_case = _create_case_with_declared(nir_user, (ANGIO_TYPE,))
        assert can_access_case(admin, angio_case, active_role=ADMIN_ROLE) is True
        assert can_access_case(admin, cardio_case, active_role=ADMIN_ROLE) is True

    def test_superuser_accesses_any_case_under_active_admin(self, nir_user: User) -> None:
        """Linha admin da matriz: superuser com papel ativo ``admin`` vê tudo."""
        superuser = User.objects.create_superuser(
            username="super-matriz", email="super@example.com", password="senha-teste"
        )
        case = _create_case_with_declared(nir_user, (CARDIO_TYPE,))
        assert can_access_case(superuser, case, active_role=ADMIN_ROLE) is True

    def test_superuser_under_active_doctor_is_common_doctor(
        self,
        assign_specialties: Callable[[User, Sequence[str]], None],
        nir_user: User,
    ) -> None:
        """P1/D2: ``is_superuser`` não é critério próprio — superuser com papel
        ativo ``doctor`` + subtipos segue a regra médica como qualquer médico.
        """
        superuser = User.objects.create_superuser(
            username="super-medico", email="super-doc@example.com", password="senha-teste"
        )
        assign_specialties(superuser, ("cardio",))
        cardio_case = _create_case_with_declared(nir_user, (CARDIO_TYPE,))
        angio_case = _create_case_with_declared(nir_user, (ANGIO_TYPE,))
        assert can_access_case(superuser, cardio_case, active_role=DOCTOR_ROLE) is True
        assert can_access_case(superuser, angio_case, active_role=DOCTOR_ROLE) is False
        assert can_access_case(superuser, angio_case, active_role=ADMIN_ROLE) is True

    def test_doctor_admin_user_under_active_doctor_is_subject_to_specialties(
        self,
        user_factory: Callable[..., User],
        assign_specialties: Callable[[User, Sequence[str]], None],
        nir_user: User,
    ) -> None:
        """P1/D2: usuário com papéis ``doctor``+``admin`` sob papel ativo
        ``doctor`` é médico (restrito aos subtipos); sob ``admin`` vê tudo.
        """
        user = user_factory("doc-admin-matriz", (DOCTOR_ROLE, ADMIN_ROLE))
        assign_specialties(user, ("cardio",))
        cardio_case = _create_case_with_declared(nir_user, (CARDIO_TYPE,))
        angio_case = _create_case_with_declared(nir_user, (ANGIO_TYPE,))
        assert can_access_case(user, cardio_case, active_role=DOCTOR_ROLE) is True
        assert can_access_case(user, angio_case, active_role=DOCTOR_ROLE) is False
        assert can_access_case(user, angio_case, active_role=ADMIN_ROLE) is True

    def test_subtyped_doctor_accesses_case_with_his_subtype(
        self,
        user_factory: Callable[..., User],
        assign_specialties: Callable[[User, Sequence[str]], None],
        nir_user: User,
    ) -> None:
        doctor = user_factory("doc-angio-matriz", (DOCTOR_ROLE,))
        assign_specialties(doctor, ("angio",))
        case = _create_case_with_declared(nir_user, (ANGIO_TYPE, "cat_cardiaco"))
        assert can_access_case(doctor, case, active_role=DOCTOR_ROLE) is True

    def test_subtyped_doctor_denied_case_without_his_subtype(
        self,
        user_factory: Callable[..., User],
        assign_specialties: Callable[[User, Sequence[str]], None],
        nir_user: User,
    ) -> None:
        doctor = user_factory("doc-cardio-matriz", (DOCTOR_ROLE,))
        assign_specialties(doctor, ("cardio",))
        case = _create_case_with_declared(nir_user, (ANGIO_TYPE,))
        assert can_access_case(doctor, case, active_role=DOCTOR_ROLE) is False

    def test_case_without_declared_rows_is_denied_for_specialist(
        self,
        user_factory: Callable[..., User],
        assign_specialties: Callable[[User, Sequence[str]], None],
        nir_user: User,
    ) -> None:
        """Caso sem tipos declarados não tem subtipo — fora do alcance do especialista."""
        doctor = user_factory("doc-radio-matriz", (DOCTOR_ROLE,))
        assign_specialties(doctor, ("radio",))
        case = Case.objects.create(created_by=nir_user)
        assert can_access_case(doctor, case, active_role=DOCTOR_ROLE) is False
        # Generalista continua vendo.
        generalist = user_factory("geral-matriz-2", (DOCTOR_ROLE,))
        assert can_access_case(generalist, case, active_role=DOCTOR_ROLE) is True

    def test_rule_ignores_case_status(
        self,
        user_factory: Callable[..., User],
        assign_specialties: Callable[[User, Sequence[str]], None],
        nir_user: User,
    ) -> None:
        """O predicado cobre fila e casos decididos (detalhe read-only, 003)."""
        doctor = user_factory("doc-angio-status", (DOCTOR_ROLE,))
        assign_specialties(doctor, ("angio",))
        decided = _make_decided_case(nir_user, doctor, (ANGIO_TYPE,), accepted=False)
        assert decided.status == CaseStatus.DOCTOR_DENIED
        assert can_access_case(doctor, decided, active_role=DOCTOR_ROLE) is True


# ── R2: cards sem o uid do caso (queues-remove-uid) ───────────────────────


def _card_slice(body: str, case_id: object) -> str:
    """Fatia o HTML do card do caso (do wrapper ao wrapper seguinte).

    O escopo por card evita assert global de uid: o ``href`` da rota carrega o
    uid (chave técnica) e o card é localizado por ele. Desde ui-parity-queues
    o wrapper é ``data-doctor-queue-card`` (case-card); o nome do paciente
    também aparece no atributo desse wrapper, então a fatia parte dele.
    """
    anchor = body.index(str(case_id))
    start = body.rindex('<div class="col-12" data-doctor-queue-card', 0, anchor)
    end = body.find('<div class="col-12" data-doctor-queue-card', anchor)
    return body[start:] if end == -1 else body[start:end]


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
    case = _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente Sem Uid",
        agency_record_number="5040778",
    )
    login_user(user_factory("medico-sem-uid", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    card = _card_slice(body, case.case_id)
    assert f"Caso {case.case_id}" not in card
    assert str(case.case_id) not in _card_visible_text(card)
    assert "Paciente Sem Uid" in card
    # ui-parity-queues: nº na summary-box (rótulo + valor), sem uid.
    assert "Nº de ocorrência" in card
    assert "5040778" in card
    # A rota do card segue técnica (uid no href).
    assert str(case.case_id) in body


@pytest.mark.django_db
def test_queue_card_without_occurrence_has_no_uid(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2/D1 (opção a): sem nº de ocorrência → «—» + paciente, sem fallback uid."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Sem Ocorrencia")
    login_user(user_factory("medico-sem-ocorrencia", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    card = _card_slice(body, case.case_id)
    assert "Nº de ocorrência" in card
    assert "Paciente Sem Ocorrencia" in card
    assert f"Caso {case.case_id}" not in card
    assert str(case.case_id) not in _card_visible_text(card)


@pytest.mark.django_db
def test_queue_cards_close_anchor(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-001 R4: CTA real com ``stretched-link``; sem ``list-group-item`` no card.

    Regressão do bug real em dev (2026-09-15): o card abria
    ``<a class="list-group-item…">`` mas fechava com ``</div>`` — HTML
    malformado desde 9214587 distorce a lista (cards sem fundo/limites).
    O clique amplo agora é o ``stretched-link`` do CTA (D3).
    """
    first = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Um")
    second = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Dois")
    login_user(user_factory("medico-anchor-close", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    assert "list-group-item" not in body
    first_card = _card_slice(body, first.case_id)
    second_card = _card_slice(body, second.case_id)
    assert "stretched-link" in first_card
    assert f'href="{reverse("doctor:case_decide", args=[first.case_id])}"' in first_card
    assert f'href="{reverse("doctor:case_decide", args=[second.case_id])}"' in second_card
    assert "Avaliar" in first_card


# ── case-work-lock slice 002: reserva nos cards da fila (R8) ──────────────

# Wrapper do card desde ui-parity-queues: ``data-doctor-queue-card``. O nome
# do paciente também aparece no atributo desse wrapper — a fatia parte dele
# (não do link/bloco interno), senão o marcador por nome localiza o card
# anterior.
_CARD_OPENING = '<div class="col-12" data-doctor-queue-card'

LOCK_CONTEXT = "doctor_decision"


def _card_block(body: str, marker: object) -> str:
    """Fatia o card localizado pelo ``marker`` (uid ou nome único).

    O uid só existe no corpo quando o card tem ação (``<a href>``); o card
    reservado por outro médico é um bloco sem link e é localizado por um
    marcador visível único (nome do paciente, também no atributo do
    wrapper).
    """
    anchor = body.index(str(marker))
    start = body.rfind(_CARD_OPENING, 0, anchor)
    assert start != -1
    end = body.find(_CARD_OPENING, anchor)
    return body[start : end if end != -1 else len(body)]


@pytest.mark.django_db
def test_awaiting_cards_reflect_lock_state(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R8/D2: card da aba aguardando reflete a reserva (livre/meu/de outro).

    Livre → [Avaliar] para a página de trabalho; do próprio médico →
    [▶ Continuar avaliação]; de outro → botão desabilitado [🔒 Reservado] com o
    portador e SEM ação de abrir (evita leitura duplicada).
    """
    free = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Livre")
    mine = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Meu")
    other = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Outro")
    doctor = user_factory("medico-fila-reserva", (DOCTOR_ROLE,))
    other_doctor = user_factory("medico-fila-reserva-outro", (DOCTOR_ROLE,))
    claim_case_lock(mine, user=doctor, context=LOCK_CONTEXT, role=DOCTOR_ROLE)
    claim_case_lock(other, user=other_doctor, context=LOCK_CONTEXT, role=DOCTOR_ROLE)
    login_user(doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    free_card = _card_block(body, free.case_id)
    assert "Avaliar" in free_card
    assert "stretched-link" in free_card
    assert "list-group-item" not in free_card
    assert f'href="{reverse("doctor:case_decide", args=[free.case_id])}"' in free_card

    mine_card = _card_block(body, mine.case_id)
    assert "Continuar avaliação" in mine_card
    assert "stretched-link" in mine_card
    assert f'href="{reverse("doctor:case_decide", args=[mine.case_id])}"' in mine_card

    # O card reservado não carrega o uid (sem href): localizar pelo paciente.
    other_card = _card_block(body, "Paciente Outro")
    assert "Reservado" in other_card
    assert other_doctor.display_name in other_card
    assert "disabled" in other_card
    # Sem ação primária: o card reservado por outro não abre a página de trabalho.
    assert f'href="{reverse("doctor:case_decide", args=[other.case_id])}"' not in other_card
    assert "stretched-link" not in other_card
    assert "list-group-item" not in other_card


@pytest.mark.django_db
def test_queue_expires_stale_lock_before_rendering(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R8/D2: reserva vencida é expirada de forma lazy antes de montar a fila."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Expirado")
    holder = user_factory("medico-reserva-expirada", (DOCTOR_ROLE,))
    claim_case_lock(case, user=holder, context=LOCK_CONTEXT, role=DOCTOR_ROLE)
    Case.objects.filter(pk=case.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    login_user(holder, DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    card = _card_block(body, case.case_id)
    assert "Avaliar" in card
    assert "Reservado" not in card
    case.refresh_from_db()
    assert case.lock_token is None
    assert case.locked_by is None


# ── OLI-001: página completa dos pendentes (R1–R5) ──────────────────────────


@pytest.mark.django_db
def test_queue_pending_title_and_full_container(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-001 R1: título via ``page_title`` e container integral alinhado."""
    _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Titulo")
    login_user(user_factory("medico-oli-titulo", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    assert body.count("page-title") >= 1
    assert "Casos aguardando decisão</h1>" in body
    assert "col-lg-10" not in body
    assert "justify-content-center" not in body
    assert "operational-list-page" in body


@pytest.mark.django_db
def test_queue_decided_title(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Slice 005 R4: aba decididos titula «Casos decididos hoje»."""
    login_user(user_factory("medico-oli-titulo-dec", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()

    assert "Casos decididos hoje</h1>" in body
    assert "Casos aguardando decisão</h1>" not in body


@pytest.mark.django_db
def test_queue_single_subtype_filter_with_counts_in_pending_only(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-001 R2/D6: um único seletor server-side; contagens só em pendentes."""
    _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Filtro")
    login_user(user_factory("medico-oli-filtro", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    assert body.count('name="subtype"') == 1
    assert "data-doctor-exam-filter" not in body
    assert "Pendentes por subtipo" not in body
    assert "angio (1)" in body
    assert "data-doctor-queue-search" in body

    decided_body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()
    assert decided_body.count('name="subtype"') == 1
    assert "data-doctor-exam-filter" not in decided_body
    # Decididos: opções sem contagem de pendentes.
    assert "angio (1)" not in decided_body


@pytest.mark.django_db
def test_queue_pending_card_regions_8_4_and_single_surface(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-001 R3: ``.case-card > .row`` com conteúdo 8 e tempo/ação 4."""
    case = _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente Regioes",
        agency_record_number="OLI001",
        days_on_screen=2,
    )
    login_user(user_factory("medico-oli-regioes", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    card = _card_block(body, case.case_id)
    assert '<div class="case-card card p-3">' in card
    assert '<div class="row">' in card
    assert "col-lg-8" in card
    assert "col-lg-4" in card
    assert card.index("col-lg-8") < card.index("col-lg-4")
    assert "list-group-item" not in card
    assert card.count("Aguardando há") == 1
    assert "Dias em tela: 2" in card


@pytest.mark.django_db
def test_queue_empty_state_is_single_info_alert(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-001 R5/D10: vazio é um único ``alert-info``, sem segundo card."""
    login_user(user_factory("medico-oli-vazio", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    assert "alert alert-info" in body
    assert "Nenhum caso aguardando" in body
    assert "card shadow-sm" not in body
    # Um único alerta de estado vazio no fragmento (a busca tem o seu
    # ``no-results`` oculto, fora do fragmento trocado pelo polling).
    fragment = body.split('id="doctor-queue-content"', 1)[1]
    assert fragment.count("alert alert-info") == 1


@pytest.mark.django_db
def test_queue_default_option_shows_total_pending_distinct(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """Revisão OLI-001 (D6): ``Todas (N)`` = total distinto de pendentes.

    Caso multi-subtipo conta 1× no total (somar ``pending_counts`` por
    subtipo duplicaria): multi(angio+cardio) + angio + cardio = total 3,
    angio 2, cardio 2 (soma 4 ≠ total). Após ``?subtype=angio`` o total
    da default permanece 3 (não o filtrado).
    """
    _make_awaiting_case(nir_user, (ANGIO_TYPE, CARDIO_TYPE), patient_name="Caso Multi")
    _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Caso Angio")
    _make_awaiting_case(nir_user, (CARDIO_TYPE,), patient_name="Caso Cardio")
    login_user(user_factory("medico-oli-todas-total", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    assert "Todas (3)" in body
    assert "Todas (4)" not in body
    assert "angio (2)" in body
    assert "cardio (2)" in body

    filtered_body = client.get(reverse("doctor:queue"), {"subtype": "angio"}).content.decode()
    assert "Todas (3)" in filtered_body
    assert "angio (2)" in filtered_body

    decided_body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()
    assert ">Todas</option>" in decided_body
    assert "Todas (" not in decided_body
    assert "angio (2)" not in decided_body
