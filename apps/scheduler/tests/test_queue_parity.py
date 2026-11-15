"""Paridade visual/interação da fila do agendador (ui-parity-queues, slice 002).

OLI-002 R1–R6: card pendente operacional compacto ``.case-card > .row``
com conteúdo ``col-lg-8`` e tempo/ação ``col-lg-4`` (sem badge genérico
de status, sem ``list-group-item``, espera única), título/contagem/vazio
em ``alert-info``, filtro server-side ``procedure_type`` (antes da
paginação, propagado a paginação/partial) + busca client-side, decisões
médicas por procedimento, reservas (Agendar/Continuar/Reservado+portador)
preservadas inclusive via partial (D9); aba processados preservada fora
do ramo pendente; sem diferenciação por unidade na fila.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Sequence
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.locks import claim_case_lock
from apps.cases.models import Case, CaseProcedure, CaseStatus
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.scheduler.services import confirm_case_scheduling

ANGIO_TYPE = "art_perif"
CARDIO_TYPE = "cat_cardiaco"
SCHEDULER_ROLE = "scheduler"
DOCTOR_ROLE = "doctor"
NIR_ROLE = "nir"
SYSTEM_ROLE = "system"
LOCK_CONTEXT = "scheduler_confirm"

_PROCESSING_STEPS: list[tuple[str, dict[str, object]]] = [
    ("start_pdf_extraction", {}),
    ("complete_pdf_extraction", {}),
    ("complete_anonymization", {}),
    ("complete_llm_extraction", {}),
    ("complete_llm_summarization", {}),
]


def _run_processing(case: Case) -> None:
    for operation, extra in _PROCESSING_STEPS:
        getattr(case, operation)(user=None, role=SYSTEM_ROLE, **extra)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _make_awaiting_case(
    created_by: User,
    decided_by: User,
    procedure_types: Sequence[str],
    *,
    patient_name: str = "",
    agency_record_number: str = "",
    patient_age: int | None = None,
    days_on_screen: int | None = None,
) -> Case:
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    _run_processing(case)
    record_doctor_procedure_decisions(
        case,
        {procedure_type: ("approved", "") for procedure_type in procedure_types},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    Case.objects.filter(pk=case.pk).update(
        patient_name=patient_name,
        agency_record_number=agency_record_number,
        patient_age=patient_age,
        days_on_screen=days_on_screen,
    )
    case.refresh_from_db()
    return case


def _make_confirmed_case(
    created_by: User,
    decided_by: User,
    scheduled_by: User,
    procedure_types: Sequence[str],
    *,
    patient_name: str = "",
    agency_record_number: str = "",
    patient_age: int | None = None,
    days_on_screen: int | None = None,
    unit: int = 2,
) -> Case:
    case = _make_awaiting_case(
        created_by,
        decided_by,
        procedure_types,
        patient_name=patient_name,
        agency_record_number=agency_record_number,
        patient_age=patient_age,
        days_on_screen=days_on_screen,
    )
    confirm_case_scheduling(
        case,
        unit=unit,
        scheduled_datetime=timezone.now() + timedelta(days=2),
        scheduled_location="Sala de hemodinâmica",
        user=scheduled_by,
        role=SCHEDULER_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


def _card_wrapper(body: str, marker: object) -> str:
    """Fatia o card (wrapper ``data-scheduler-queue-card``) pelo marker."""
    anchor = body.index(str(marker))
    start = body.rindex("data-scheduler-queue-card", 0, anchor)
    start = body.rindex("<div", 0, start)
    end = body.find("data-scheduler-queue-card", anchor)
    end = body.rfind("<div", 0, end) if end != -1 else len(body)
    return body[start:end]


@pytest.mark.django_db
def test_queue_cards_use_case_card_visual_markers(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R3: card pendente sem badge genérico; 8/4, espera única, CTA real."""
    doctor = user_factory("medico-paridade-agendador", (DOCTOR_ROLE,))
    case = _make_awaiting_case(
        nir_user,
        doctor,
        (ANGIO_TYPE,),
        patient_name="Paciente Paridade Agendamento",
        agency_record_number="777001",
        patient_age=84,
        days_on_screen=6,
    )
    login_user(user_factory("agendador-paridade", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    card = _card_wrapper(body, case.case_id)
    assert "case-card" in card
    assert "summary-box" in card
    assert "waiting-time" in card
    # Revisão OLI-002: sem ``status-badge`` genérico nos pendentes; os
    # badges de subtipo/disposição e ``Dias em tela`` permanecem.
    assert "status-badge" not in card
    assert "Aguardando confirmação de agendamento" not in card
    assert "badge rounded-pill" in card
    assert "Paciente Paridade Agendamento" in card
    assert "84 a" in card
    assert "777001" in card
    assert "Dias em tela: 6" in card
    assert "6 d em tela" not in card
    assert "Arteriografia periférica" in card
    assert "angio" in card
    assert "Aprovado" in card
    assert "Aguardando há" in card
    assert card.count("Aguardando há") == 1
    assert "list-group-item" not in card
    # Reserva livre: CTA primário «Agendar» que abre a página de trabalho.
    assert ">Agendar</a>" in card
    assert "Abrir caso" not in card


@pytest.mark.django_db
def test_queue_processed_card_received_label_and_unit_label(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1: aba processados usa «Recebido há» (nunca «Aguardando há») + unit_label."""
    doctor = user_factory("medico-paridade-processado", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-paridade-processado", (SCHEDULER_ROLE,))
    case = _make_confirmed_case(
        nir_user,
        doctor,
        scheduler,
        (ANGIO_TYPE,),
        patient_name="Paciente Processado Paridade",
        agency_record_number="777002",
        patient_age=70,
        days_on_screen=4,
        unit=2,
    )
    login_user(user_factory("agendador-paridade-proc", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue"), {"tab": "processados"}).content.decode()

    card = _card_wrapper(body, case.case_id)
    assert "Paciente Processado Paridade · 70 a" in card
    assert "Recebido há" in card
    assert "Aguardando há" not in card
    assert "4 d em tela" in card
    assert "Unidade 2" in card


@pytest.mark.django_db
def test_queue_has_no_legacy_nav_tabs_and_nav_pills_have_badges(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1/D6/D8: sem bloco legado ``nav-tabs``; pills Pendentes/Processados + badges."""
    doctor = user_factory("medico-pills-agendador", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-pills", (SCHEDULER_ROLE,))
    for index in range(2):
        _make_awaiting_case(nir_user, doctor, (ANGIO_TYPE,), patient_name=f"Paciente Pill {index}")
    _make_confirmed_case(
        nir_user, doctor, scheduler, (ANGIO_TYPE,), patient_name="Paciente Pill Processado"
    )
    login_user(user_factory("agendador-pills-viewer", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    assert "nav-tabs" not in body
    assert "Aguardando agendamento</a>" not in body
    # ``_nav`` pills (change 1) com badges do context processor queue_counters.
    assert ">Pendentes" in body
    assert ">Processados" in body
    assert 'data-count="2"' in body
    assert 'data-count="1"' in body
    # Aba processados também usa pills, sem bloco legado.
    processed_body = client.get(reverse("scheduler:queue"), {"tab": "processados"}).content.decode()
    assert "nav-tabs" not in processed_body


@pytest.mark.django_db
def test_queue_page_has_htmx_polling_and_search(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2/R3: container htmx 20 s p/ partial, busca client-side, ARIA."""
    doctor = user_factory("medico-htmx-agendador", (DOCTOR_ROLE,))
    _make_awaiting_case(
        nir_user,
        doctor,
        (ANGIO_TYPE,),
        patient_name="Paciente Buscavel Agendamento",
        agency_record_number="888002",
    )
    login_user(user_factory("agendador-htmx", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    assert 'id="scheduler-queue-content"' in body
    assert 'hx-get="' in body and "partials/queue/" in body
    assert 'hx-trigger="every 20s"' in body
    assert 'hx-swap="innerHTML"' in body
    assert "data-scheduler-queue-search" in body
    assert "data-scheduler-queue-filter-status" in body
    assert 'aria-live="polite"' in body
    assert "data-scheduler-queue-no-results" in body
    assert "scheduler_queue_filter.js" in body
    assert "Paciente Buscavel Agendamento" in body
    # Busca é client-side: controle fora do fragmento trocado.
    search_pos = body.index("data-scheduler-queue-search")
    content_pos = body.index('id="scheduler-queue-content"')
    assert search_pos < content_pos


@pytest.mark.django_db
def test_queue_partial_returns_same_fragment_with_same_guards(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2/D9: partial = mesmo fragmento; papel não agendador 403, anônimo 302."""
    doctor = user_factory("medico-partial-agendador", (DOCTOR_ROLE,))
    case = _make_awaiting_case(
        nir_user,
        doctor,
        (ANGIO_TYPE,),
        patient_name="Paciente Partial Agendamento",
        agency_record_number="999003",
        patient_age=41,
        days_on_screen=2,
    )
    scheduler = user_factory("agendador-partial", (SCHEDULER_ROLE,))
    login_user(scheduler, SCHEDULER_ROLE)

    partial = client.get(reverse("scheduler:queue_partial"))
    assert partial.status_code == 200
    partial_body = partial.content.decode()
    assert str(case.case_id) in partial_body
    assert "Paciente Partial Agendamento" in partial_body
    assert "case-card" in partial_body
    assert "summary-box" in partial_body
    # Fragmento, não a página cheia (sem heading/nav/base).
    assert "Solicitações de agendamento aguardando confirmação</h1>" not in partial_body
    assert "scheduler-queue-content" not in partial_body

    # Mesmo fragmento do server-side: o card aparece igual na página cheia.
    full_body = client.get(reverse("scheduler:queue")).content.decode()
    assert "Paciente Partial Agendamento" in full_body

    for role, username in (
        (NIR_ROLE, "nir-partial-agendador"),
        (DOCTOR_ROLE, "doc-partial-agendador"),
    ):
        other = user_factory(username, (role,))
        login_user(other, role)
        assert client.get(reverse("scheduler:queue_partial")).status_code == 403


@pytest.mark.django_db
def test_queue_partial_anonymous_redirects_login(client: Client) -> None:
    """R2: anônimo no partial → redirect ao login (mesmo guard da página)."""
    response = client.get(reverse("scheduler:queue_partial"))
    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
def test_queue_partial_respects_tab_and_page(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2: caso da outra aba ou de outra página nunca aparece no partial."""
    doctor = user_factory("medico-partial-aba", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-partial-aba", (SCHEDULER_ROLE,))
    awaiting = _make_awaiting_case(
        nir_user, doctor, (ANGIO_TYPE,), patient_name="Caso Aba Aguardando"
    )
    processed = _make_confirmed_case(
        nir_user, doctor, scheduler, (ANGIO_TYPE,), patient_name="Caso Aba Processado"
    )
    login_user(user_factory("agendador-partial-aba-viewer", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    awaiting_body = client.get(reverse("scheduler:queue_partial")).content.decode()
    assert str(awaiting.case_id) in awaiting_body
    assert str(processed.case_id) not in awaiting_body

    processed_body = client.get(
        reverse("scheduler:queue_partial"), {"tab": "processados"}
    ).content.decode()
    assert str(processed.case_id) in processed_body
    assert str(awaiting.case_id) not in processed_body

    # Paginação: página 2 do partial não traz o caso da página 1.
    base = timezone.now()
    for index in range(21):
        case = _make_awaiting_case(
            nir_user, doctor, (ANGIO_TYPE,), patient_name=f"Pagina Paciente Ag {index:02d}"
        )
        Case.objects.filter(pk=case.pk).update(created_at=base - timedelta(seconds=21 - index))
    page2_body = client.get(reverse("scheduler:queue_partial"), {"page": "2"}).content.decode()
    assert "Página 2 de" in page2_body
    assert "Pagina Paciente Ag 00" not in page2_body


@pytest.mark.django_db
def test_queue_partial_lists_both_units_without_unit_filter(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4: fila completa sem diferenciação por unidade (unidades 1 e 2 juntas)."""
    doctor = user_factory("medico-partial-unidades", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-partial-unidades", (SCHEDULER_ROLE,))
    unit1 = _make_confirmed_case(
        nir_user, doctor, scheduler, (ANGIO_TYPE,), patient_name="Caso Unidade Um", unit=1
    )
    unit2 = _make_confirmed_case(
        nir_user, doctor, scheduler, (ANGIO_TYPE,), patient_name="Caso Unidade Dois", unit=2
    )
    login_user(user_factory("agendador-partial-unidades-viewer", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue_partial"), {"tab": "processados"}).content.decode()

    assert str(unit1.case_id) in body
    assert str(unit2.case_id) in body
    assert "Unidade 1" in _card_wrapper(body, unit1.case_id)
    assert "Unidade 2" in _card_wrapper(body, unit2.case_id)


@pytest.mark.django_db
def test_queue_partial_reflects_lock_and_expires_stale(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4/D9: partial reflete reserva (Abrir/Continuar/Reservado+portador) e expira lease."""
    doctor = user_factory("medico-partial-lock-ag", (DOCTOR_ROLE,))
    free = _make_awaiting_case(
        nir_user, doctor, (ANGIO_TYPE,), patient_name="Paciente Livre Partial"
    )
    mine = _make_awaiting_case(nir_user, doctor, (ANGIO_TYPE,), patient_name="Paciente Meu Partial")
    other = _make_awaiting_case(
        nir_user, doctor, (ANGIO_TYPE,), patient_name="Paciente Outro Partial"
    )
    stale = _make_awaiting_case(
        nir_user, doctor, (ANGIO_TYPE,), patient_name="Paciente Vencido Partial"
    )
    scheduler = user_factory("agendador-partial-lock", (SCHEDULER_ROLE,))
    other_scheduler = user_factory("agendador-partial-lock-outro", (SCHEDULER_ROLE,))
    claim_case_lock(mine, user=scheduler, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)
    claim_case_lock(other, user=other_scheduler, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)
    claim_case_lock(stale, user=other_scheduler, context=LOCK_CONTEXT, role=SCHEDULER_ROLE)
    Case.objects.filter(pk=stale.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    login_user(scheduler, SCHEDULER_ROLE)

    partial_body = client.get(reverse("scheduler:queue_partial")).content.decode()

    free_card = _card_wrapper(partial_body, free.case_id)
    assert "Agendar" in free_card

    mine_card = _card_wrapper(partial_body, mine.case_id)
    assert "Continuar" in mine_card

    other_card = _card_wrapper(partial_body, "Paciente Outro Partial")
    assert "Reservado" in other_card
    assert other_scheduler.display_name in other_card
    assert "disabled" in other_card

    stale_card = _card_wrapper(partial_body, stale.case_id)
    assert "Agendar" in stale_card
    assert "Reservado" not in stale_card
    stale.refresh_from_db()
    assert stale.lock_token is None


@pytest.mark.django_db
def test_queue_render_query_count_bounded_independent_of_card_count(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4 (matriz ``assertNumQueries``): sem N+1 por card (página e partial).

    Renderizar a fila (página cheia E partial) com 1 ou 5 cards executa o
    MESMO número de queries — procedimentos/unidade/reserva vêm das rows
    pré-carregadas, nunca de uma query por card.
    """
    doctor = user_factory("medico-queries-agendador", (DOCTOR_ROLE,))
    login_user(user_factory("agendador-queries", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)
    queue_url = reverse("scheduler:queue")
    partial_url = reverse("scheduler:queue_partial")

    def _query_count(url: str) -> int:
        with CaptureQueriesContext(connection) as captured:
            response = client.get(url)
        assert response.status_code == 200
        return len(captured.captured_queries)

    _make_awaiting_case(
        nir_user, doctor, (ANGIO_TYPE, CARDIO_TYPE), patient_name="Paciente Query Um"
    )
    page_count_few = _query_count(queue_url)
    partial_count_few = _query_count(partial_url)

    for index in range(4):
        _make_awaiting_case(
            nir_user,
            doctor,
            (ANGIO_TYPE, CARDIO_TYPE),
            patient_name=f"Paciente Query Extra {index}",
        )
    page_count_many = _query_count(queue_url)
    partial_count_many = _query_count(partial_url)

    assert page_count_many == page_count_few
    assert partial_count_many == partial_count_few
    # Teto folgado sem mascarar N+1 (a igualdade acima é a trava real).
    assert page_count_many <= 25
    assert partial_count_many <= 25


@pytest.mark.django_db
def test_queue_pending_card_regions_8_4_and_single_surface(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R3: ``.case-card > .row`` com conteúdo 8 e tempo/ação 4."""
    doctor = user_factory("medico-oli-regioes-par", (DOCTOR_ROLE,))
    case = _make_awaiting_case(
        nir_user,
        doctor,
        (ANGIO_TYPE,),
        patient_name="Paciente Regioes Par",
        agency_record_number="OLI-PAR",
        days_on_screen=2,
    )
    login_user(user_factory("agendador-oli-regioes-par", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    card = _card_wrapper(body, case.case_id)
    assert '<div class="case-card card p-3">' in card
    assert '<div class="row">' in card
    assert "col-lg-8" in card
    assert "col-lg-4" in card
    assert card.index("col-lg-8") < card.index("col-lg-4")
    assert "list-group-item" not in card
    assert card.count("Aguardando há") == 1
    assert "Dias em tela: 2" in card
    assert "Recebido em " not in card


@pytest.mark.django_db
def test_queue_procedure_type_filter_server_side_and_partial(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R2/D7: filtro server-side por tipo; inválido equivale a vazio."""
    doctor = user_factory("medico-oli-filtro-par", (DOCTOR_ROLE,))
    angio = _make_awaiting_case(
        nir_user, doctor, (ANGIO_TYPE,), patient_name="Angio Par", agency_record_number="PAR-A"
    )
    cardio = _make_awaiting_case(
        nir_user, doctor, (CARDIO_TYPE,), patient_name="Cardio Par", agency_record_number="PAR-C"
    )
    login_user(user_factory("agendador-oli-filtro-par", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    filtered = client.get(
        reverse("scheduler:queue"), {"tab": "aguardando", "procedure_type": CARDIO_TYPE}
    ).content.decode()
    assert "Cardio Par" in filtered
    assert "Angio Par" not in filtered
    assert f'value="{CARDIO_TYPE}" selected' in filtered

    partial = client.get(
        reverse("scheduler:queue_partial"),
        {"tab": "aguardando", "procedure_type": CARDIO_TYPE},
    ).content.decode()
    assert "Cardio Par" in partial
    assert "Angio Par" not in partial

    invalid = client.get(
        reverse("scheduler:queue"), {"tab": "aguardando", "procedure_type": "invalido"}
    ).content.decode()
    assert "Angio Par" in invalid
    assert "Cardio Par" in invalid

    assert str(angio.case_id) in filtered or "Angio Par" not in filtered
    assert str(cardio.case_id) in filtered


@pytest.mark.django_db
def test_queue_empty_state_is_single_info_alert(
    client: Client,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R1/D10: vazio é um único ``alert-info``, sem segundo card."""
    login_user(user_factory("agendador-oli-vazio-par", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue")).content.decode()

    assert "alert alert-info" in body
    assert "Nenhum caso aguardando agendamento" in body
    assert "card shadow-sm" not in body
    fragment = body.split('id="scheduler-queue-content"', 1)[1]
    assert fragment.count("alert alert-info") == 1


@pytest.mark.django_db
def test_queue_processed_card_keeps_status_badge(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """OLI-002 R5: só os pendentes perdem o badge genérico; processados não."""
    doctor = user_factory("medico-oli-proc-par", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-oli-proc-par", (SCHEDULER_ROLE,))
    case = _make_confirmed_case(
        nir_user,
        doctor,
        scheduler,
        (ANGIO_TYPE,),
        patient_name="Processado Par",
        agency_record_number="PAR-P",
        days_on_screen=1,
        unit=1,
    )
    login_user(user_factory("agendador-oli-proc-view-par", (SCHEDULER_ROLE,)), SCHEDULER_ROLE)

    body = client.get(reverse("scheduler:queue"), {"tab": "processados"}).content.decode()

    card = _card_wrapper(body, case.case_id)
    assert "status-badge" in card
    assert "Recebido há" in card
    assert "Unidade 1" in card


def test_operational_list_css_scope_and_search_js() -> None:
    """OLI-002 R5/D2: CSS escopado à lista operacional; busca preservada."""
    css = pathlib.Path("static/css/app.css").read_text(encoding="utf-8")
    assert ".operational-list-page" in css
    # Filtro server-side por procedimento quebra sem overflow em 390px.
    assert "#procedure-type-filter" in css
    js = pathlib.Path("static/js/scheduler_queue_filter.js").read_text(encoding="utf-8")
    assert "data-scheduler-queue-search" in js
    assert "htmx:afterSwap" in js
    queue_template = pathlib.Path("templates/scheduler/queue.html").read_text(encoding="utf-8")
    assert "operational-list-page" in queue_template
