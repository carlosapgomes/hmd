"""Consolidação do nav do NIR (reenvio-corrigido-qualquer-nir, slice 002, R1–R4).

R1: ``my_cases`` lista APENAS ativos (tudo exceto ``CLEANED``); o parâmetro
legado ``?tab=closed`` cai na default de ativos preservando filtros; o header
não tem navegação Ativos/Encerrados (a contagem aparece como cabeçalho neutro
dos ativos).
R2: o nav do NIR tem 3 itens — «Novo Encaminhamento», «Meus Casos» e
«Casos Encerrados» (busca global, rota inalterada); sem badge de encerrados.
R3: redirect pós-ciência ao detalhe do próprio caso, preservado.
R4: manual atualizado (nav de 3 itens, sem aba de encerrados por criador,
reenvio por qualquer NIR).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case, CaseProcedure, CaseStatus
from apps.intake.services import create_case_with_documents

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"


@pytest.fixture(autouse=True)
def _creation_without_processing() -> Iterator[None]:
    """Criação sem processamento: estados controlados pelo teste."""
    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


def _create_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    *,
    record_number: str = "",
    status: CaseStatus | None = None,
) -> Case:
    """Cria um caso NEW com 1 PDF e 1 tipo declarado, ajustando exibição."""
    case = create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(),
        procedure_type="art_perif",
    )
    updates: dict[str, object] = {"agency_record_number": record_number}
    if status is not None:
        updates["status"] = status
    Case.objects.filter(pk=case.pk).update(**updates)
    case.refresh_from_db()
    return case


def _drive_to_final_reply_posted(nir: User, doctor: User) -> Case:
    """Caso em ``FINAL_REPLY_POSTED`` pela negativa médica (setup direto,
    sem o marcador de revisão do serviço de intake)."""
    case = Case.objects.create(created_by=nir)
    CaseProcedure.objects.create(case=case, procedure_type="art_perif", declared_by_nir=True)
    case.start_pdf_extraction(user=None, role="system")
    case.complete_pdf_extraction(user=None, role="system")
    case.complete_anonymization(user=None, role="system")
    case.complete_llm_extraction(user=None, role="system")
    case.complete_llm_summarization(user=None, role="system")
    assert case.status == CaseStatus.AWAITING_DOCTOR
    from apps.cases.closure import post_doctor_denial_reply
    from apps.cases.procedures import record_doctor_procedure_decisions

    record_doctor_procedure_decisions(
        case,
        {"art_perif": ("denied", "sem indicação clínica")},
        user=doctor,
        role=DOCTOR_ROLE,
    )
    post_doctor_denial_reply(case, user=doctor, role=DOCTOR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


def _nav_segment(body: str) -> str:
    """Fatia a faixa ``app-nav`` do HTML (alvo dos asserts de navegação)."""
    match = re.search(r'<nav class="app-nav.*?</nav>', body, re.DOTALL)
    assert match is not None, "faixa app-nav ausente"
    return match.group(0)


# ── R1: my_cases lista apenas ativos ───────────────────────────────────


@pytest.mark.django_db
def test_my_cases_lists_only_actives_without_tab_navigation(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: 2 ativos + 1 CLEANED → só os 2 listados, sem pills Ativos/Encerrados."""
    first = _create_case(nir_user, pdf_factory, record_number="5040778")
    second = _create_case(nir_user, pdf_factory, record_number="5040779")
    closed = _create_case(nir_user, pdf_factory, record_number="5040780", status=CaseStatus.CLEANED)

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    assert str(first.case_id) in body
    assert str(second.case_id) in body
    assert str(closed.case_id) not in body
    # Cabeçalho neutro da lista de ativos (contagem 2), sem navegação de abas.
    assert re.search(r"Casos em Processamento.*?2 casos", body, re.S) is not None
    assert ">Ativos<" not in body
    assert 'aria-label="Filtro pela situação do caso"' not in body


@pytest.mark.django_db
def test_my_cases_legacy_closed_tab_falls_back_to_actives_preserving_filters(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: ``?tab=closed`` legado cai nos ativos, com os filtros preservados."""
    matching = _create_case(nir_user, pdf_factory, record_number="5040778")
    other = _create_case(nir_user, pdf_factory, record_number="5040999")
    closed = _create_case(nir_user, pdf_factory, record_number="5040778", status=CaseStatus.CLEANED)

    client.force_login(nir_user)
    body = client.get(
        reverse("intake:my_cases"), {"tab": "closed", "q": "5040778"}
    ).content.decode()

    assert str(matching.case_id) in body
    assert str(other.case_id) not in body
    assert str(closed.case_id) not in body
    # Filtros preservados na URL do partial (página == partial); nenhum
    # resquício do ?tab= legado na página.
    assert "q=5040778" in body
    assert "tab=active" in body
    assert "tab=closed" not in body


# ── R2: nav com 3 itens ────────────────────────────────────────────────


@pytest.mark.django_db
def test_nir_nav_has_three_items_with_closed_cases_search(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: nav com «Novo Encaminhamento», «Meus Casos» e «Casos Encerrados»."""
    _create_case(nir_user, pdf_factory, record_number="5040778")

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()
    nav = _nav_segment(body)
    search_url = reverse("intake:closed_cases_search")

    assert "Novo Encaminhamento" in nav
    assert "Casos em Processamento" in nav
    assert "Casos Encerrados" in nav
    assert "Buscar Encerrados" not in nav
    # «Casos Encerrados» aponta para a busca global (rota inalterada).
    assert f'href="{search_url}"' in nav
    # Sem item de lista de encerrados por criador (nenhum ?tab=closed no nav).
    assert "tab=closed" not in nav
    # Exatamente 3 itens de navegação.
    assert nav.count("nav-item") == 3


@pytest.mark.django_db
def test_nir_nav_has_no_closed_badge_but_keeps_active_count(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: badge de ativos onde o contexto o fornece; nenhum badge de encerrados."""
    _create_case(nir_user, pdf_factory, record_number="5040778")
    _create_case(nir_user, pdf_factory, record_number="5040779")
    _create_case(nir_user, pdf_factory, record_number="5040780", status=CaseStatus.CLEANED)

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()
    nav = _nav_segment(body)

    assert 'data-count="2"' in nav
    assert 'data-count="1"' not in nav


# ── R3: redirect pós-ciência preservado ───────────────────────────────


@pytest.mark.django_db
def test_ack_redirect_to_own_detail_preserved(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3 (pin de regressão): ciência → redirect ao detalhe do próprio caso."""
    doctor = user_factory("doctor-reenvio-002-ack", DOCTOR_ROLE)
    case = _drive_to_final_reply_posted(nir_user, doctor)

    client.force_login(nir_user)
    response = client.post(reverse("intake:case_ack", args=[case.case_id]))

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("intake:case_detail", args=[case.case_id])
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED


# ── R4: manual atualizado ─────────────────────────────────────────────


@pytest.mark.django_db
def test_manual_documents_three_item_nav_and_any_nir_resubmit(
    client: Client,
    nir_user: User,
) -> None:
    """R4: manual cita «Casos Encerrados» e o reenvio por qualquer NIR."""
    client.force_login(nir_user)
    body = client.get(reverse("manual")).content.decode()

    assert "Casos Encerrados" in body
    assert "qualquer NIR" in body or "qualquer papel" in body
