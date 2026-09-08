"""Testes das views de fechamento do NIR (slice 003 do nir-result-closure).

Cobre R1 (detalhe com seção de resultado pós-decisão médica: decisões por
procedimento com motivo real + dados de agendamento + resposta final em
destaque na thread; ausente antes da decisão), R2 (botão "Confirmar
recebimento" somente em ``FINAL_REPLY_POSTED`` do próprio criador; POST em
``intake:case_ack`` encerra o caso com flash + redirect ao detalhe CLEANED;
estado errado → mensagem + redirect, nunca 500; não-criador → 404 no GET do
detalhe e no POST do ack), R3 (abas ativos/encerrados em ``my_cases`` com
``?tab=`` e fallback seguro, escopo por criador) e R4 (guards: anônimo →
login; papel ativo ≠ ``nir`` → 403 no ack).

Os casos são dirigidos pelas operações FSM + serviços reais de decisão
(``record_doctor_procedure_decisions``), fechamento (``post_doctor_denial_reply``)
e agendamento (``confirm_case_scheduling``) — o mesmo padrão de setup do
``apps/cases/tests/test_closure_ack.py``; as views consumidas são as do intake.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.closure import acknowledge_case_receipt, post_doctor_denial_reply
from apps.cases.models import Case, CaseProcedure, CaseStatus, SchedulingUnit
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.scheduler.services import confirm_case_scheduling, deny_case_scheduling

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SCHEDULER_ROLE = "scheduler"
SYSTEM_ROLE = "system"

# Tipos representativos do catálogo (mesmos dos testes 001–002).
ANGIO_TYPE = "art_perif"
CARDIO_TYPE = "cat_cardiaco"
ANGIO_LABEL = "Arteriografia periférica"
CARDIO_LABEL = "Cateterismo cardíaco"

# Motivos reais da negativa médica (asserts de conteúdo específico do R1).
ANGIO_REASON = "sem indicação clínica para o procedimento"
CARDIO_REASON = "risco cirúrgico elevado"

# Agendamento confirmado (unidade 1): local + assert de data formatada.
SCHEDULED_LOCATION = "HGRS — Hemodinâmica"

# Texts exibidos pela UI (botão/flash).
ACK_BUTTON_LABEL = "Confirmar recebimento"
ACK_FLASH_PREFIX = "Recebimento confirmado"


def _create_case_with_rows(created_by: User) -> Case:
    """Caso NEW com as rows declaradas dos dois tipos (setup dos testes)."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in (ANGIO_TYPE, CARDIO_TYPE):
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )
    return case


def _drive_to_awaiting_doctor(case: Case) -> None:
    """Dirige o caso pelas transições do pipeline até ``AWAITING_DOCTOR``."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _denial_final_case(*, created_by: User, decided_by: User) -> Case:
    """Caso em ``FINAL_REPLY_POSTED`` pela negativa médica (fluxo do 001)."""
    case = _create_case_with_rows(created_by)
    _drive_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: ("denied", ANGIO_REASON), CARDIO_TYPE: ("denied", CARDIO_REASON)},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    post_doctor_denial_reply(case, user=decided_by, role=DOCTOR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


def _denial_decided_case(*, created_by: User, decided_by: User) -> Case:
    """Caso em ``DOCTOR_DENIED`` (decisão registrada, resposta ainda não posta)."""
    case = _create_case_with_rows(created_by)
    _drive_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: ("denied", ANGIO_REASON), CARDIO_TYPE: ("denied", CARDIO_REASON)},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    return case


def _confirmed_final_case(
    *,
    created_by: User,
    decided_by: User,
    scheduler_user: User,
) -> Case:
    """Caso em ``FINAL_REPLY_POSTED`` pelo agendamento confirmado (unidade 1)."""
    case = _create_case_with_rows(created_by)
    _drive_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: ("approved", ""), CARDIO_TYPE: ("approved", "")},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    confirm_case_scheduling(
        case,
        unit=SchedulingUnit.UNIT_1,
        scheduled_datetime=timezone.now() + timedelta(days=5),
        scheduled_location=SCHEDULED_LOCATION,
        user=scheduler_user,
        role=SCHEDULER_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


def _login(client: Client, user: User) -> None:
    """Login do usuário (papel único → middleware define o papel ativo)."""
    client.force_login(user)


# ── R1: seção de resultado no detalhe ─────────────────────────────────────


@pytest.mark.django_db
def test_detail_shows_outcome_after_decision(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R1: pós-decisão (negativa) o detalhe mostra a seção de resultado com as
    decisões por procedimento (label + disposição + motivo real) e a resposta
    final em destaque na thread."""
    doctor = user_factory("doctor-intake-outcome", DOCTOR_ROLE)
    case = _denial_final_case(created_by=nir_user, decided_by=doctor)
    _login(client, nir_user)

    response = client.get(reverse("intake:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    # Seção de resultado com as decisões por procedimento (label+disposição+motivo).
    assert "Resultado" in body
    assert ANGIO_LABEL in body
    assert CARDIO_LABEL in body
    assert "Negado" in body
    assert ANGIO_REASON in body
    assert CARDIO_REASON in body
    # Resposta final em destaque na thread (corpo do DENIAL_REPLY_TEMPLATE).
    assert "Resposta final" in body
    assert "Resposta final da avaliação médica" in body
    # Botão de ciência presente (estado + criador corretos).
    assert ACK_BUTTON_LABEL in body


@pytest.mark.django_db
def test_detail_shows_scheduling_when_confirmed(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R1: caso confirmado pelo agendador mostra unidade/data/local e as
    decisões aprovadas no resultado."""
    doctor = user_factory("doctor-intake-sched", DOCTOR_ROLE)
    scheduler_user = user_factory("scheduler-intake-sched", SCHEDULER_ROLE)
    case = _confirmed_final_case(
        created_by=nir_user,
        decided_by=doctor,
        scheduler_user=scheduler_user,
    )
    _login(client, nir_user)

    response = client.get(reverse("intake:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Resultado" in body
    assert "Aprovado" in body
    # Dados de agendamento (unidade/data/local) persistidos no caso.
    expected_datetime = timezone.localtime(case.scheduled_datetime).strftime("%d/%m/%Y %H:%M")
    assert "Unidade 1" in body
    assert expected_datetime in body
    assert SCHEDULED_LOCATION in body
    # Resposta final de agendamento em destaque na thread.
    assert "Resposta final" in body
    assert "Agendamento confirmado" in body
    # P2 do review: o destaque visual do card da resposta final (badge/classe)
    # precisa estar presente para que a regressão de "em destaque" falhe.
    assert "border-primary border-2" in body
    assert ACK_BUTTON_LABEL in body


@pytest.mark.django_db
def test_detail_shows_scheduling_denial_reason(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R1/D3: agendamento negado mostra o motivo do agendador no resultado
    (P2 do review — ramo sem cobertura)."""
    doctor = user_factory("doctor-intake-deny", DOCTOR_ROLE)
    scheduler_user = user_factory("scheduler-intake-deny", SCHEDULER_ROLE)
    case = _create_case_with_rows(nir_user)
    _drive_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: ("approved", ""), CARDIO_TYPE: ("approved", "")},
        user=doctor,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    deny_case_scheduling(
        case,
        reason="Sem vagas na semana solicitada",
        user=scheduler_user,
        role=SCHEDULER_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    _login(client, nir_user)

    response = client.get(reverse("intake:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Resultado" in body
    assert "Aprovado" in body
    assert "Sem vagas na semana solicitada" in body
    assert ACK_BUTTON_LABEL in body


@pytest.mark.django_db
def test_detail_no_outcome_before_decision(
    client: Client,
    nir_user: User,
) -> None:
    """R1: antes da decisão médica (AWAITING_DOCTOR) a seção de resultado não
    existe — nem decisões, nem botão de ciência."""
    case = _create_case_with_rows(nir_user)
    _drive_to_awaiting_doctor(case)
    _login(client, nir_user)

    response = client.get(reverse("intake:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Resultado" not in body
    assert ACK_BUTTON_LABEL not in body
    assert ANGIO_REASON not in body
    ack_url = reverse("intake:case_ack", args=[case.case_id])
    assert ack_url not in body


# ── R2: botão de ciência e POST do ack ────────────────────────────────────


@pytest.mark.django_db
def test_ack_button_visibility(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R2: botão "Confirmar recebimento" apenas em FINAL_REPLY_POSTED do
    criador — ausente em estado pré-resposta (DOCTOR_DENIED) e pós-fecho
    (CLEANED)."""
    doctor = user_factory("doctor-intake-vis", DOCTOR_ROLE)
    final_case = _denial_final_case(created_by=nir_user, decided_by=doctor)
    decided_case = _denial_decided_case(created_by=nir_user, decided_by=doctor)
    cleaned_case = _denial_final_case(created_by=nir_user, decided_by=doctor)
    acknowledge_case_receipt(cleaned_case, user=nir_user, role=NIR_ROLE)
    cleaned_case.refresh_from_db()
    assert cleaned_case.status == CaseStatus.CLEANED
    _login(client, nir_user)

    # FINAL_REPLY_POSTED → botão e action visíveis.
    case_id = final_case.case_id
    response = client.get(reverse("intake:case_detail", args=[case_id]))
    body = response.content.decode()
    assert ACK_BUTTON_LABEL in body
    assert reverse("intake:case_ack", args=[case_id]) in body

    # CLEANED (após o ack) → botão ausente.
    case_id = cleaned_case.case_id
    response = client.get(reverse("intake:case_detail", args=[case_id]))
    body = response.content.decode()
    assert ACK_BUTTON_LABEL not in body
    assert reverse("intake:case_ack", args=[case_id]) not in body

    # DOCTOR_DENIED (resposta final ainda não publicada) → botão ausente.
    case_id = decided_case.case_id
    response = client.get(reverse("intake:case_detail", args=[case_id]))
    body = response.content.decode()
    assert ACK_BUTTON_LABEL not in body


@pytest.mark.django_db
def test_ack_post_closes_case(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R2/cenário spec: POST do ack via UI encerra o caso (CLEANED) e
    redireciona ao detalhe com flash de sucesso."""
    doctor = user_factory("doctor-intake-ackok", DOCTOR_ROLE)
    case = _denial_final_case(created_by=nir_user, decided_by=doctor)
    _login(client, nir_user)

    response = client.post(reverse("intake:case_ack", args=[case.case_id]))

    assert response.status_code == 302
    assert response["Location"] == reverse("intake:case_detail", args=[case.case_id])

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED

    followed = client.get(response["Location"])
    assert followed.status_code == 200
    body = followed.content.decode()
    assert ACK_FLASH_PREFIX in body
    assert "Caso concluído" in body


@pytest.mark.django_db
def test_ack_wrong_state_no_500(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R2: POST em estado fora de FINAL_REPLY_POSTED → redirect com mensagem
    de erro, sem 500 e sem efeito no caso."""
    doctor = user_factory("doctor-intake-wrongstate", DOCTOR_ROLE)
    case = _denial_decided_case(created_by=nir_user, decided_by=doctor)
    assert case.status == CaseStatus.DOCTOR_DENIED
    _login(client, nir_user)

    response = client.post(reverse("intake:case_ack", args=[case.case_id]))

    assert response.status_code == 302
    assert response["Location"] == reverse("intake:case_detail", args=[case.case_id])

    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    # Mensagem de erro na página seguinte, nunca 500.
    followed = client.get(response["Location"])
    assert followed.status_code == 200
    assert "FINAL_REPLY_POSTED" in followed.content.decode()


@pytest.mark.django_db
def test_ack_non_creator_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R2: caso alheio → 404 no GET do detalhe e no POST direto do ack — o
    caso alheio permanece intacto (sem vazamento)."""
    doctor = user_factory("doctor-intake-foreign", DOCTOR_ROLE)
    other_nir = user_factory("nir-intake-foreign", NIR_ROLE)
    foreign_case = _denial_final_case(created_by=other_nir, decided_by=doctor)
    ack_url = reverse("intake:case_ack", args=[foreign_case.case_id])
    _login(client, nir_user)

    assert client.get(reverse("intake:case_detail", args=[foreign_case.case_id])).status_code == 404
    assert client.post(ack_url).status_code == 404

    foreign_case.refresh_from_db()
    assert foreign_case.status == CaseStatus.FINAL_REPLY_POSTED


# ── R3: abas de "meus casos" ──────────────────────────────────────────────


@pytest.mark.django_db
def test_my_cases_tabs_partition(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R3/cenário spec: a aba ativa (default) lista tudo exceto CLEANED; a aba
    encerrados lista apenas CLEANED — mesmo formato de items."""
    doctor = user_factory("doctor-intake-tabs", DOCTOR_ROLE)
    active_case = _denial_final_case(created_by=nir_user, decided_by=doctor)
    decided_case = _denial_decided_case(created_by=nir_user, decided_by=doctor)
    closed_case = _denial_final_case(created_by=nir_user, decided_by=doctor)
    acknowledge_case_receipt(closed_case, user=nir_user, role=NIR_ROLE)
    closed_case.refresh_from_db()
    assert closed_case.status == CaseStatus.CLEANED
    _login(client, nir_user)

    # Aba ativa é a default (?tab= ausente ou inválido → ativos).
    for query in ("", "?tab=invalid"):
        response = client.get(reverse("intake:my_cases") + query)
        assert response.status_code == 200
        body = response.content.decode()
        assert str(active_case.case_id) in body
        assert str(decided_case.case_id) in body
        assert str(closed_case.case_id) not in body

    # Aba encerrados: apenas o caso CLEANED.
    response = client.get(reverse("intake:my_cases") + "?tab=closed")
    assert response.status_code == 200
    body = response.content.decode()
    assert str(closed_case.case_id) in body
    assert str(active_case.case_id) not in body
    assert str(decided_case.case_id) not in body
    # Links das abas presentes.
    assert "?tab=active" in body
    assert "?tab=closed" in body


@pytest.mark.django_db
def test_tabs_creator_scope(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R4/R3: as abas não vazam casos de outro criador (CLEANED alheio não
    aparece em encerrados)."""
    doctor = user_factory("doctor-intake-scope", DOCTOR_ROLE)
    other_nir = user_factory("nir-intake-scope", NIR_ROLE)
    foreign_closed = _denial_final_case(created_by=other_nir, decided_by=doctor)
    acknowledge_case_receipt(foreign_closed, user=other_nir, role=NIR_ROLE)
    foreign_closed.refresh_from_db()
    assert foreign_closed.status == CaseStatus.CLEANED
    own_closed = _denial_final_case(created_by=nir_user, decided_by=doctor)
    acknowledge_case_receipt(own_closed, user=nir_user, role=NIR_ROLE)
    _login(client, nir_user)

    response = client.get(reverse("intake:my_cases") + "?tab=closed")

    assert response.status_code == 200
    body = response.content.decode()
    assert str(own_closed.case_id) in body
    assert str(foreign_closed.case_id) not in body


# ── R4: guards de acesso ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_ack_role_guard(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R4: papel ativo ≠ nir → 403 no POST do ack; anônimo → redirect ao
    login."""
    doctor = user_factory("doctor-intake-guard", DOCTOR_ROLE)
    case = _denial_final_case(created_by=nir_user, decided_by=doctor)
    ack_url = reverse("intake:case_ack", args=[case.case_id])

    client.force_login(doctor)
    response = client.post(ack_url)
    assert response.status_code == 403

    client.logout()
    response = client.post(ack_url)
    assert response.status_code == 302
    assert "/login/" in response["Location"]
