"""Paridade visual/interação da fila médica (ui-parity-queues, slice 001).

R1–R4: cards ``case-card + summary-box + pulse-dot + waiting-time +
status-badge``; bloco legado ``nav-tabs`` removido (navegação só pelo
``_nav`` pills ``Pendentes/Decididos`` com badges); endpoint
``doctor:queue_partial`` com o MESMO fragmento/guards da página cheia e
htmx a cada 20 s reaplicando busca/filtro; busca client-side por
nome/ocorrência sem requisição e ``?subtype=`` server-side autoritativo;
reservas/ordenação/paginação preservadas inclusive via partial (D9).
"""

from __future__ import annotations

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

ANGIO_TYPE = "art_perif"
CARDIO_TYPE = "cat_cardiaco"
DOCTOR_ROLE = "doctor"
NIR_ROLE = "nir"
SYSTEM_ROLE = "system"
LOCK_CONTEXT = "doctor_decision"


def _advance_to_awaiting_doctor(case: Case) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _make_awaiting_case(
    created_by: User,
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
    _advance_to_awaiting_doctor(case)
    Case.objects.filter(pk=case.pk).update(
        patient_name=patient_name,
        agency_record_number=agency_record_number,
        patient_age=patient_age,
        days_on_screen=days_on_screen,
    )
    case.refresh_from_db()
    return case


def _card_wrapper(body: str, marker: object) -> str:
    """Fatia o card (wrapper ``data-doctor-queue-card``) localizado pelo marker."""
    anchor = body.index(str(marker))
    start = body.rindex("data-doctor-queue-card", 0, anchor)
    start = body.rindex("<div", 0, start)
    end = body.find("data-doctor-queue-card", anchor)
    end = body.rfind("<div", 0, end) if end != -1 else len(body)
    return body[start:end]


@pytest.mark.django_db
def test_queue_cards_use_case_card_visual_markers(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1: card com case-card/summary-box/waiting-time/status-badge + nome/idade."""
    case = _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente Paridade",
        agency_record_number="777001",
        patient_age=84,
        days_on_screen=6,
    )
    login_user(user_factory("medico-paridade", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    card = _card_wrapper(body, case.case_id)
    assert "case-card" in card
    assert "summary-box" in card
    assert "waiting-time" in card
    assert "status-badge" in card
    assert "Paciente Paridade" in card
    assert "84 a" in card
    assert "777001" in card
    assert "6 d em tela" in card
    assert "Arteriografia periférica" in card
    assert "angio" in card
    assert "Aguardando há" in card


@pytest.mark.django_db
def test_queue_has_no_legacy_nav_tabs_and_nav_pills_have_badges(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R1/D6/D8: sem bloco legado ``nav-tabs``; pills Pendentes/Decididos + badges."""
    _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Um")
    _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Dois")
    _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Tres")
    login_user(user_factory("medico-pills", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    assert "nav-tabs" not in body
    assert "Aguardando decisão</a>" not in body
    # ``_nav`` pills (change 1) com badges do context processor queue_counters.
    assert ">Pendentes" in body
    assert ">Decididos" in body
    assert "3" in body
    # Aba decididos também usa pills, sem bloco legado.
    decided_body = client.get(reverse("doctor:queue"), {"tab": "decididos"}).content.decode()
    assert "nav-tabs" not in decided_body


@pytest.mark.django_db
def test_queue_page_has_htmx_polling_search_and_exam_filter(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2/R3: container htmx 20 s p/ partial, busca client-side, filtro de exame, ARIA."""
    _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente Buscavel",
        agency_record_number="888002",
    )
    login_user(user_factory("medico-htmx", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    body = client.get(reverse("doctor:queue")).content.decode()

    assert 'id="doctor-queue-content"' in body
    assert 'hx-get="' in body and "partials/queue/" in body
    assert 'hx-trigger="every 20s"' in body
    assert 'hx-swap="innerHTML"' in body
    assert "data-doctor-queue-search" in body
    assert "data-doctor-exam-filter" in body
    assert "data-doctor-queue-filter-status" in body
    assert 'aria-live="polite"' in body
    assert "data-doctor-queue-no-results" in body
    assert "doctor_queue_filter.js" in body
    assert "Paciente Buscavel" in body
    # Busca/exame são client-side: controles fora do fragmento trocado.
    search_pos = body.index("data-doctor-queue-search")
    content_pos = body.index('id="doctor-queue-content"')
    assert search_pos < content_pos


@pytest.mark.django_db
def test_queue_partial_returns_same_fragment_with_same_guards(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2/D9: partial = mesmo fragmento; papel não médico 403, anônimo 302."""
    case = _make_awaiting_case(
        nir_user,
        (ANGIO_TYPE,),
        patient_name="Paciente Partial",
        agency_record_number="999003",
        patient_age=41,
        days_on_screen=2,
    )
    doctor = user_factory("medico-partial", (DOCTOR_ROLE,))
    login_user(doctor, DOCTOR_ROLE)

    partial = client.get(reverse("doctor:queue_partial"))
    assert partial.status_code == 200
    partial_body = partial.content.decode()
    assert str(case.case_id) in partial_body
    assert "Paciente Partial" in partial_body
    assert "case-card" in partial_body
    assert "summary-box" in partial_body
    # Fragmento, não a página cheia (sem heading/nav/base).
    assert "Fila médica</h1>" not in partial_body
    assert "doctor-queue-content" not in partial_body

    # Mesmo fragmento do server-side: o card aparece igual na página cheia.
    full_body = client.get(reverse("doctor:queue")).content.decode()
    assert "Paciente Partial" in full_body

    nir = user_factory("nir-partial", (NIR_ROLE,))
    login_user(nir, NIR_ROLE)
    assert client.get(reverse("doctor:queue_partial")).status_code == 403


@pytest.mark.django_db
def test_queue_partial_anonymous_redirects_login(client: Client) -> None:
    """R2: anônimo no partial → redirect ao login (mesmo guard da página)."""
    response = client.get(reverse("doctor:queue_partial"))
    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
def test_queue_partial_respects_subtype_and_page(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R2/R3: caso fora do subtipo server ou fora da página nunca aparece no partial."""
    angio_case = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Caso Angio Partial")
    cardio_case = _make_awaiting_case(nir_user, (CARDIO_TYPE,), patient_name="Caso Cardio Partial")
    login_user(user_factory("medico-partial-subtipo", (DOCTOR_ROLE,)), DOCTOR_ROLE)

    partial_body = client.get(
        reverse("doctor:queue_partial"), {"subtype": "angio"}
    ).content.decode()
    assert str(angio_case.case_id) in partial_body
    assert str(cardio_case.case_id) not in partial_body

    # Paginação: página 2 do partial não traz o caso da página 1.
    base = timezone.now()
    for index in range(21):
        case = _make_awaiting_case(
            nir_user, (ANGIO_TYPE,), patient_name=f"Pagina Paciente {index:02d}"
        )
        Case.objects.filter(pk=case.pk).update(created_at=base - timedelta(seconds=21 - index))
    page2_body = client.get(reverse("doctor:queue_partial"), {"page": "2"}).content.decode()
    assert "Página 2 de" in page2_body
    assert "Pagina Paciente 00" not in page2_body


@pytest.mark.django_db
def test_queue_partial_reflects_lock_and_expires_stale(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R4/D9: partial reflete reserva (Reservado+portador) e expira lease vencida."""
    other_case = _make_awaiting_case(nir_user, (ANGIO_TYPE,), patient_name="Paciente Outro Partial")
    stale_case = _make_awaiting_case(
        nir_user, (ANGIO_TYPE,), patient_name="Paciente Vencido Partial"
    )
    doctor = user_factory("medico-partial-lock", (DOCTOR_ROLE,))
    other_doctor = user_factory("medico-partial-lock-outro", (DOCTOR_ROLE,))
    claim_case_lock(other_case, user=other_doctor, context=LOCK_CONTEXT, role=DOCTOR_ROLE)
    claim_case_lock(stale_case, user=other_doctor, context=LOCK_CONTEXT, role=DOCTOR_ROLE)
    Case.objects.filter(pk=stale_case.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    login_user(doctor, DOCTOR_ROLE)

    partial_body = client.get(reverse("doctor:queue_partial")).content.decode()

    other_card = _card_wrapper(partial_body, "Paciente Outro Partial")
    assert "Reservado" in other_card
    assert other_doctor.display_name in other_card

    stale_card = _card_wrapper(partial_body, stale_case.case_id)
    assert "Avaliar" in stale_card
    assert "Reservado" not in stale_card
    stale_case.refresh_from_db()
    assert stale_case.lock_token is None


@pytest.mark.django_db
def test_queue_render_query_count_bounded_independent_of_card_count(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    login_user: Callable[[User, str], None],
) -> None:
    """R3 (matriz ``assertNumQueries``): sem N+1 da lista de subtipos do card.

    Renderizar a fila (página cheia E partial) com 1 ou 5 cards executa o
    MESMO número de queries e dentro de um teto fixo — os subtipos/procedimentos
    vêm das rows pré-carregadas (``prefetch_related("procedures")`` +
    ``select_related("locked_by")``), nunca de uma query por card.
    """
    login_user(user_factory("medico-queries", (DOCTOR_ROLE,)), DOCTOR_ROLE)
    queue_url = reverse("doctor:queue")
    partial_url = reverse("doctor:queue_partial")

    def _query_count(url: str) -> int:
        with CaptureQueriesContext(connection) as captured:
            response = client.get(url)
        assert response.status_code == 200
        return len(captured.captured_queries)

    _make_awaiting_case(nir_user, (ANGIO_TYPE, CARDIO_TYPE), patient_name="Paciente Query Um")
    page_count_few = _query_count(queue_url)
    partial_count_few = _query_count(partial_url)

    for index in range(4):
        _make_awaiting_case(
            nir_user, (ANGIO_TYPE, CARDIO_TYPE), patient_name=f"Paciente Query Extra {index}"
        )
    page_count_many = _query_count(queue_url)
    partial_count_many = _query_count(partial_url)

    assert page_count_many == page_count_few
    assert partial_count_many == partial_count_few
    # Teto realista medido (página: 17): sessão/autenticação (acesso 1×) +
    # base da fila +
    # expiração lazy + 1 query por subtipo do filtro (≤4) + contadores do nav;
    # nenhuma escala com o nº de cards. Limite folgado sem mascarar N+1.
    assert page_count_many <= 20
    assert partial_count_many <= 20
