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

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import Case, CaseProcedure, CaseStatus
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
) -> None:
    """Ajusta campos de exibição do caso (``created_at`` inclusa)."""
    Case.objects.filter(pk=case.pk).update(
        created_at=created_at or case.created_at,
        patient_name=patient_name,
        agency_record_number=agency_record_number,
        patient_age=patient_age,
        days_on_screen=days_on_screen,
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

    # follow=True: a home despacha o papel ativo à sua fila (change
    # painel-gerencial-e-home, slice 002); a navbar da página final segue
    # decidindo o link pelo papel ativo.
    response = client.get(reverse("home"), follow=True)

    assert response.status_code == 200
    assert f'href="{reverse("doctor:queue")}">Fila médica</a>' in response.content.decode()


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
    """Fila → detalhe: cada item da fila leva ao card do paciente (resumo,
    recomendação e registro da opinião vivem no detalhe)."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    login_user(user_factory("medico-link", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"))

    assert response.status_code == 200
    body = response.content.decode()
    href = reverse("doctor:case_detail", args=[case.case_id])
    assert f'href="{href}"' in body


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
    """R3: aba ``decididos`` lista DOCTOR_DENIED|DOCTOR_ACCEPTED|SCHEDULER_REQUESTED."""
    doctor = user_factory("medico-decidido", (DOCTOR_ROLE,))
    denied = _make_decided_case(nir_user, doctor, (ANGIO_TYPE,), accepted=False)
    accepted = _make_decided_case(nir_user, doctor, (CARDIO_TYPE,), accepted=True)
    scheduled = _make_decided_case(
        nir_user, doctor, (NEURO_TYPE,), accepted=True, chain_scheduling=True
    )
    awaiting = _make_awaiting_case(nir_user, (RADIO_TYPE,))
    login_user(user_factory("geral-decidido", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    response = client.get(reverse("doctor:queue"), {"tab": "decididos"})

    assert response.status_code == 200
    body = response.content.decode()
    assert str(denied.case_id) in body
    assert str(accepted.case_id) in body
    assert str(scheduled.case_id) in body
    assert str(awaiting.case_id) not in body
    assert "Negado pelo médico" in body
    assert "Agendamento solicitado" in body


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
    assert "Aguardando decisão médica" in body
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
    assert "angio: 25" in first_body

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
    """R2: card da aba ativa com idade, «⏱ Aguardando há» e «N d em tela»."""
    _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente Identificado",
        patient_age=84,
        days_on_screen=6,
    )
    login_user(user_factory("medico-card-tela", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    assert "Paciente Identificado · 84 a" in body
    assert "Aguardando há" in body
    assert "6 d em tela" in body
    # P1 da review: data absoluta pré-existente preservada junto ao rótulo relativo
    assert "Recebido em " in body
    # P2 da review: dias em tela como badge Bootstrap de fato
    assert ">6 d em tela</span>" in body


@pytest.mark.django_db
def test_queue_decided_tab_uses_received_label(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: aba histórica usa «Recebido há» e NUNCA «Aguardando há»."""
    doctor = user_factory("medico-label-aba", (DOCTOR_ROLE,))
    decided = _make_decided_case(nir_user, doctor, (ANGIO_TYPE,), accepted=False)
    _apply_metadata(decided, patient_name="Paciente Decidido", patient_age=84)
    login_user(user_factory("geral-label-aba", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()

    assert "Paciente Decidido · 84 a" in body
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
    _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Recem Nascido",
        patient_age=0,
        days_on_screen=0,
    )
    login_user(user_factory("medico-zero-tela", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

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
    _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente Sem Demografia",
    )
    login_user(user_factory("medico-sem-demo", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    assert "Paciente Sem Demografia" in body
    assert "Paciente Sem Demografia · " not in body
    assert "d em tela" not in body


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
    # Dropdown só com os subtipos do usuário.
    assert '<option value="angio">angio</option>' in all_body
    assert '<option value="cardio">cardio</option>' not in all_body

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
    # Dropdown com todos os subtipos do catálogo.
    for subtype in ("angio", "neuro", "cardio", "radio"):
        assert f'<option value="{subtype}">{subtype}</option>' in body


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
    assert "Aguardando decisão médica" in body

    # Tipos declarados com badge de subtipo por card.
    assert "Arteriografia periférica" in body
    assert "Arteriografia cerebral" in body
    assert "Drenagem biliar percutânea" in body

    # Contagem de pendentes por subtipo no cabeçalho (angio/neuro/radio com
    # caso; cardio sem caso pendente).
    assert "Pendentes por subtipo" in body
    assert "angio: 1" in body
    assert "neuro: 1" in body
    assert "radio: 1" in body
    assert "cardio: 0" in body


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
