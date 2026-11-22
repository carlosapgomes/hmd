"""Reenvio corrigido por qualquer NIR (change reenvio-corrigido-qualquer-nir, slice 001).

TDD do slice: R1 (guarda relaxada em view E serviço — qualquer papel ativo
``nir`` reenvia caso ``CLEANED`` de qualquer criador; fora de ``CLEANED``
→ recusa nomeada sem escrita; papel errado → 403), R2 (ação «Reenviar
corrigido» no detalhe histórico, gated por ``status == CLEANED``), R3
(reenviador = criador do novo caso + autor dos eventos), R4 (navegação sem
404 para o não-criador: breadcrumb/«Cancelar»/recusas → detalhe histórico;
sucesso → detalhe do novo caso) e R5 (links condicionais em «Reenvios
corrigidos deste caso»).

Setup FSM idêntico ao de ``test_corrected_resubmission.py`` (operações reais
até ``CLEANED``); sem processamento inline (enqueue assertável).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseStatus
from apps.intake.services import create_corrected_resubmission

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SCHEDULER_ROLE = "scheduler"
SYSTEM_ROLE = "system"

ANGIO_TYPE = "art_perif"
REASON = "laudo com dados divergentes do exame original"
RESUBMIT_BUTTON_LABEL = "Reenviar corrigido"


@pytest.fixture(autouse=True)
def _without_inline_processing() -> Iterator[None]:
    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


def _drive_to_cleaned(case: Case, *, nir: User, doctor: User) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    case.record_doctor_decision(accepted=False, user=doctor, role=DOCTOR_ROLE)
    case.post_final_reply(user=doctor, role=DOCTOR_ROLE)
    case.nir_acknowledge(user=nir, role=NIR_ROLE)
    case.start_cleaning(user=nir, role=NIR_ROLE)
    case.complete_cleaning(user=nir, role=NIR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED


def _cleaned_case(*, created_by: User, doctor: User) -> Case:
    case = Case.objects.create(created_by=created_by)
    _drive_to_cleaned(case, nir=created_by, doctor=doctor)
    return case


def _event_types(case: Case) -> list[str]:
    return [event.event_type for event in case.events.order_by("id")]


# ── R1: guarda relaxada — serviço aceita não-criador ───────────────────────


@pytest.mark.django_db
def test_service_direct_non_creator_creates_linked_case(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/R3: chamada DIRETA ao serviço com não-criador cria o novo caso
    vinculado — reenviador como criador/autor, original intacto."""
    doctor = user_factory("doctor-r1-direct", DOCTOR_ROLE)
    other_nir = user_factory("nir-r1-owner", NIR_ROLE)
    original = _cleaned_case(created_by=other_nir, doctor=doctor)
    events_before = original.events.count()

    new_case = create_corrected_resubmission(
        original_case=original,
        user=nir_user,
        role=NIR_ROLE,
        file=pdf_factory(name="reenvio.pdf"),
        procedure_type=ANGIO_TYPE,
        correction_reason=REASON,
    )

    assert new_case.pk != original.pk
    assert new_case.status == CaseStatus.NEW
    assert new_case.created_by == nir_user
    assert new_case.corrects_case_id == original.case_id
    assert new_case.correction_reason == REASON
    assert new_case.correction_created_by == nir_user
    superseded = original.events.get(event_type=CaseEventType.CASE_MARKED_SUPERSEDED)
    assert superseded.actor == nir_user
    assert superseded.actor_role == NIR_ROLE
    correction_event = new_case.events.get(event_type=CaseEventType.CASE_CORRECTION_CREATED)
    assert correction_event.actor == nir_user
    assert correction_event.actor_role == NIR_ROLE
    original.refresh_from_db()
    assert original.status == CaseStatus.CLEANED
    assert original.events.count() == events_before + 1


# ── R1: guarda relaxada — view GET/POST para não-criador ──────────────────


@pytest.mark.django_db
def test_view_get_non_creator_renders_form(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R1: GET do form em caso CLEANED alheio renderiza (não mais 404)."""
    doctor = user_factory("doctor-r1-get", DOCTOR_ROLE)
    other_nir = user_factory("nir-r1-get-owner", NIR_ROLE)
    foreign = _cleaned_case(created_by=other_nir, doctor=doctor)
    client.force_login(nir_user)

    response = client.get(reverse("intake:case_resubmit", args=[foreign.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'name="documents"' in body
    assert 'name="correction_reason"' in body


@pytest.mark.django_db
def test_view_post_non_creator_creates_case(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/R3: POST válido de não-criador cria o caso vinculado a ele e
    redireciona ao detalhe do NOVO caso (seu) com flash."""
    doctor = user_factory("doctor-r1-post", DOCTOR_ROLE)
    other_nir = user_factory("nir-r1-post-owner", NIR_ROLE)
    original = _cleaned_case(created_by=other_nir, doctor=doctor)
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:case_resubmit", args=[original.case_id]),
        {
            "documents": [pdf_factory()],
            "procedure_type": ANGIO_TYPE,
            "correction_reason": REASON,
        },
    )

    assert response.status_code == 302
    new_case = Case.objects.exclude(pk=original.pk).get()
    assert new_case.created_by == nir_user
    assert new_case.correction_created_by == nir_user
    assert response["Location"] == reverse("intake:case_detail", args=[new_case.case_id])
    followed = client.get(response["Location"])
    assert followed.status_code == 200
    assert "Reenvio corrigido criado" in followed.content.decode()
    original.refresh_from_db()
    assert original.status == CaseStatus.CLEANED


@pytest.mark.django_db
def test_view_post_invalid_no_write_non_creator(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: POST inválido de não-criador re-renderiza sem escrever."""
    doctor = user_factory("doctor-r1-invalid", DOCTOR_ROLE)
    other_nir = user_factory("nir-r1-invalid-owner", NIR_ROLE)
    original = _cleaned_case(created_by=other_nir, doctor=doctor)
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:case_resubmit", args=[original.case_id]),
        {
            "documents": [pdf_factory()],
            "procedure_type": ANGIO_TYPE,
            "correction_reason": "  ",
        },
    )

    assert response.status_code == 200
    assert "motivo" in response.content.decode()
    assert Case.objects.count() == 1


@pytest.mark.django_db
def test_refusal_non_cleaned_no_write(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R1/R4: caso fora de CLEANED é recusado com erro nomeado, sem escrita —
    caso próprio → detalhe do criador (vigente); caso alheio fora do escopo
    histórico → busca de encerrados (fallback sem 404); ambos com a mensagem
    nomeada no destino (200, sem 404)."""
    other_nir = user_factory("nir-r1-active-owner", NIR_ROLE)
    own_active = Case.objects.create(created_by=nir_user)
    foreign_active = Case.objects.create(created_by=other_nir)
    client.force_login(nir_user)
    refusal_fragment = "Reenvio corrigido disponível apenas para casos encerrados"

    own_response = client.get(reverse("intake:case_resubmit", args=[own_active.case_id]))
    assert own_response.status_code == 302
    assert own_response["Location"] == reverse("intake:case_detail", args=[own_active.case_id])
    own_followed = client.get(own_response["Location"])
    assert own_followed.status_code == 200
    assert refusal_fragment in own_followed.content.decode()

    foreign_response = client.get(reverse("intake:case_resubmit", args=[foreign_active.case_id]))
    assert foreign_response.status_code == 302
    assert foreign_response["Location"] == reverse("intake:closed_cases_search")
    assert Case.objects.count() == 2
    foreign_followed = client.get(foreign_response["Location"])
    assert foreign_followed.status_code == 200
    assert refusal_fragment in foreign_followed.content.decode()


@pytest.mark.django_db
def test_role_guard_non_nir_forbidden(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R1: papel ativo ≠ nir → 403 no form (criador ou não)."""
    doctor_owner = user_factory("doctor-r1-guard", DOCTOR_ROLE)
    scheduler = user_factory("scheduler-r1-guard", SCHEDULER_ROLE)
    cleaned = _cleaned_case(created_by=nir_user, doctor=doctor_owner)
    url = reverse("intake:case_resubmit", args=[cleaned.case_id])

    client.force_login(scheduler)
    assert client.get(url).status_code == 403


# ── R2: ação no detalhe histórico ─────────────────────────────────────────


@pytest.mark.django_db
def test_closed_detail_shows_resubmit_action_when_cleaned(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R2: detalhe histórico de CLEANED (próprio e alheio) oferece a ação,
    linkando a página de reenvio existente."""
    doctor = user_factory("doctor-r2-action", DOCTOR_ROLE)
    other_nir = user_factory("nir-r2-owner", NIR_ROLE)
    own = _cleaned_case(created_by=nir_user, doctor=doctor)
    foreign = _cleaned_case(created_by=other_nir, doctor=doctor)
    client.force_login(nir_user)

    for case in (own, foreign):
        body = client.get(
            reverse("intake:closed_case_detail", args=[case.case_id])
        ).content.decode()
        assert RESUBMIT_BUTTON_LABEL in body
        assert reverse("intake:case_resubmit", args=[case.case_id]) in body


@pytest.mark.django_db
def test_closed_detail_hides_action_when_not_cleaned(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R2: detalhe histórico fora de CLEANED (intercorrência ativa) não
    oferece a ação."""
    from apps.cases.closure import POST_CLOSURE_ISSUE_STATUS_OPENED

    other_nir = user_factory("nir-r2-active-owner", NIR_ROLE)
    reopened = Case.objects.create(
        created_by=other_nir,
        status=CaseStatus.AWAITING_SCHEDULING,
        post_schedule_issue_status=POST_CLOSURE_ISSUE_STATUS_OPENED,
    )
    client.force_login(nir_user)

    body = client.get(
        reverse("intake:closed_case_detail", args=[reopened.case_id])
    ).content.decode()
    assert RESUBMIT_BUTTON_LABEL not in body
    assert reverse("intake:case_resubmit", args=[reopened.case_id]) not in body


# ── R4: navegação sem 404 para o não-criador ───────────────────────────────


@pytest.mark.django_db
def test_resubmit_page_nav_points_to_historical_detail(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R4: breadcrumb e «Cancelar» da página de reenvio apontam ao detalhe
    histórico do original — nenhuma rota restrita ao criador."""
    doctor = user_factory("doctor-r4-nav", DOCTOR_ROLE)
    other_nir = user_factory("nir-r4-owner", NIR_ROLE)
    foreign = _cleaned_case(created_by=other_nir, doctor=doctor)
    client.force_login(nir_user)

    body = client.get(reverse("intake:case_resubmit", args=[foreign.case_id])).content.decode()

    historical_url = reverse("intake:closed_case_detail", args=[foreign.case_id])
    creator_only_url = reverse("intake:case_detail", args=[foreign.case_id])
    assert historical_url in body
    assert f'href="{creator_only_url}"' not in body


@pytest.mark.django_db
def test_intake_disabled_refusal_redirects_to_historical(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R4: recusa com intake desligado redireciona ao detalhe histórico
    (criador e não-criador), sem escrita."""
    doctor = user_factory("doctor-r4-lock", DOCTOR_ROLE)
    other_nir = user_factory("nir-r4-lock-owner", NIR_ROLE)
    own = _cleaned_case(created_by=nir_user, doctor=doctor)
    foreign = _cleaned_case(created_by=other_nir, doctor=doctor)
    client.force_login(nir_user)

    with override_settings(INTAKE_ENABLED=False):
        for case in (own, foreign):
            response = client.get(reverse("intake:case_resubmit", args=[case.case_id]))
            assert response.status_code == 302
            assert response["Location"] == reverse("intake:closed_case_detail", args=[case.case_id])
    assert Case.objects.count() == 2


# ── R5: links condicionais em «Reenvios corrigidos deste caso» ────────────


def _correction_with_number(
    *,
    original: Case,
    resubmitter: User,
    number: str,
    pdf_factory: Callable[..., SimpleUploadedFile],
    doctor: User,
) -> Case:
    """Reenvio pelo serviço com nº atribuído (desambiguação determinística)."""
    correction = create_corrected_resubmission(
        original_case=original,
        user=resubmitter,
        role=NIR_ROLE,
        file=pdf_factory(name=f"reenvio-{number}.pdf"),
        procedure_type=ANGIO_TYPE,
        correction_reason=f"correção {number}",
    )
    correction.agency_record_number = number
    correction.save(update_fields=["agency_record_number"])
    return correction


@pytest.mark.django_db
def test_correction_links_respect_visitor_authorization(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R5: próprio (ativo) → detalhe; alheio ativo → referência SEM link;
    alheio CLEANED → detalhe histórico."""
    doctor = user_factory("doctor-r5-links", DOCTOR_ROLE)
    reenviar = user_factory("nir-r5-reenviador", NIR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)

    own_active = _correction_with_number(
        original=original,
        resubmitter=nir_user,
        number="60001",
        pdf_factory=pdf_factory,
        doctor=doctor,
    )
    foreign_active = _correction_with_number(
        original=original,
        resubmitter=reenviar,
        number="60002",
        pdf_factory=pdf_factory,
        doctor=doctor,
    )
    foreign_cleaned = _correction_with_number(
        original=original,
        resubmitter=reenviar,
        number="60003",
        pdf_factory=pdf_factory,
        doctor=doctor,
    )
    # Encerrado (update direto — o vínculo corrects_case já existe; a
    # condição do link lê apenas status + autoria, não a trilha FSM; o
    # campo status bloqueia atribuição direta fora das transições).
    Case.objects.filter(pk=foreign_cleaned.pk).update(status=CaseStatus.CLEANED)
    foreign_cleaned.refresh_from_db()
    client.force_login(nir_user)

    body = client.get(reverse("intake:case_detail", args=[original.case_id])).content.decode()
    heading = body.index("Reenvios corrigidos deste caso")
    listing = body[heading:]

    # Próprio ativo → link ao detalhe do caso.
    assert f'href="{reverse("intake:case_detail", args=[own_active.case_id])}"' in listing
    # Alheio CLEANED → link ao detalhe histórico.
    assert (
        f'href="{reverse("intake:closed_case_detail", args=[foreign_cleaned.case_id])}"' in listing
    )
    # Alheio ativo → referência desambiguada SEM link (guard ativo preservado).
    assert foreign_active.disambiguated_reference in listing
    assert f'href="{reverse("intake:case_detail", args=[foreign_active.case_id])}"' not in listing
    assert (
        f'href="{reverse("intake:closed_case_detail", args=[foreign_active.case_id])}"'
        not in listing
    )
