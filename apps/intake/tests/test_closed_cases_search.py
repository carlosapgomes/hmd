"""Busca NIR de casos encerrados + detalhe histórico + abertura (slice 003).

Cobre os 7 cenários do delta ``intake-nir`` (busca por nome de outro criador,
busca por nº com issue ativa, casos em andamento fora, estado vazio, detalhe
read-only com trilha, 403 não-NIR, 404 fora do escopo histórico) + POST
válido/inválido da intercorrência + cap 50 + sufixo de desambiguação + guard
de intranet do papel ``nir`` (ADR-0002, mesmo padrão das rotas intake
vigentes: ``role_required`` sob o ``IntranetGuardMiddleware`` global).

Referência de paridade (somente-leitura): ats-web
``apps/intake/views.py`` ``closed_cases_search``/``closed_case_detail``.
Divergências HMD: universo ``Q(CLEANED) | Q(issue_ativa)`` com a coluna
compacta do slice 002 (sem contextos scheduled/operational), motivo único de
texto livre (decisão do dono, D3) e detalhe sem seções de PDF/anexo (o serve
do criador segue exclusivo de «meus casos»).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.closure import POST_CLOSURE_ISSUE_STATUS_OPENED
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseStatus, SchedulingUnit

NIR_ROLE = "nir"
SCHEDULER_ROLE = "scheduler"
DOCTOR_ROLE = "doctor"

SEARCH_URL = "intake:closed_cases_search"
DETAIL_URL = "intake:closed_case_detail"

REOPEN_REASON = "retorno dos sintomas — nova avaliação do agendamento solicitada"

# Intranet guard (ADR-0002): faixa restrita + IP externo para o 403.
INTRANET_CIDR = "10.0.0.0/8"
EXTERNAL_IP = "203.0.113.9"


def _visible_text(body: str) -> str:
    """Corpo sem atributos de rota — o uid segue como chave técnica no href."""
    return re.sub(r'href="[^"]*"', "", body)


def _eligible_closed_case(
    created_by: User,
    *,
    record_number: str = "5040778",
    patient_name: str = "Paciente Encerrado",
) -> Case:
    """Caso ``CLEANED`` elegível: unidade 1 confirmada, dados retidos, sem issue."""
    return Case.objects.create(
        created_by=created_by,
        status=CaseStatus.CLEANED,
        agency_record_number=record_number,
        patient_name=patient_name,
        scheduled_unit=SchedulingUnit.UNIT_1,
        scheduled_datetime=timezone.now() - timedelta(days=3),
        scheduled_location="HGRS — Hemodinâmica",
    )


def _issue_active_case(
    created_by: User,
    *,
    record_number: str = "5040779",
    patient_name: str = "Paciente Reaberto",
) -> Case:
    """Caso fora de ``CLEANED`` mas no escopo histórico (issue ativa)."""
    return Case.objects.create(
        created_by=created_by,
        status=CaseStatus.AWAITING_SCHEDULING,
        agency_record_number=record_number,
        patient_name=patient_name,
        post_schedule_issue_status=POST_CLOSURE_ISSUE_STATUS_OPENED,
        post_schedule_issue_reason=REOPEN_REASON,
    )


def _open_case(
    created_by: User,
    *,
    record_number: str = "5040780",
    patient_name: str = "Paciente Em Andamento",
) -> Case:
    """Caso em andamento, sem issue — fora do universo da busca histórica."""
    return Case.objects.create(
        created_by=created_by,
        status=CaseStatus.AWAITING_DOCTOR,
        agency_record_number=record_number,
        patient_name=patient_name,
    )


# ── R1: busca por nome encontra encerrado de outro criador ────────────────


@pytest.mark.django_db
def test_search_by_name_finds_other_creators_closed_case(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """Busca por nome lista o ``CLEANED`` de outro NIR com referência humana,
    status legível e indicação de elegibilidade — sem expor o uid em texto."""
    other_nir = user_factory("nir-alheio-busca", NIR_ROLE)
    case = _eligible_closed_case(other_nir, patient_name="Zuleica Busca Nome")

    client.force_login(nir_user)
    response = client.get(reverse(SEARCH_URL), {"q": "zuleica"})

    assert response.status_code == 200
    assert response.context is not None
    assert [item["case"].pk for item in response.context["results"]] == [case.pk]
    body = response.content.decode()
    assert "Zuleica Busca Nome" in body
    assert "5040778" in body
    assert "Elegível para intercorrência" in body
    assert reverse(DETAIL_URL, args=[case.case_id]) in body
    assert str(case.case_id) not in _visible_text(body)
    assert "Trilha de eventos" not in body


@pytest.mark.django_db
def test_search_matches_either_record_number_or_patient_name(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """O termo casa nº de ocorrência OU nome (qualquer um dos dois campos)."""
    other_nir = user_factory("nir-alheio-ou", NIR_ROLE)
    by_number = _eligible_closed_case(
        other_nir, record_number="7770001", patient_name="Nome Comum A"
    )
    by_name = _eligible_closed_case(other_nir, record_number="7770002", patient_name="Termo Raro B")

    client.force_login(nir_user)
    by_number_response = client.get(reverse(SEARCH_URL), {"q": "7770001"})
    by_name_response = client.get(reverse(SEARCH_URL), {"q": "raro"})

    assert [item["case"].pk for item in by_number_response.context["results"]] == [by_number.pk]
    assert [item["case"].pk for item in by_name_response.context["results"]] == [by_name.pk]


# ── R1: busca por nº inclui issue ativa, sem ação de nova abertura ────────


@pytest.mark.django_db
def test_search_by_number_includes_issue_active_without_reopen_action(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """Caso com intercorrência ativa entra no universo da busca pelo nº, com o
    motivo legível — e sem ação de nova abertura para ele."""
    other_nir = user_factory("nir-alheio-issue", NIR_ROLE)
    issue_case = _issue_active_case(other_nir, record_number="8880011")

    client.force_login(nir_user)
    response = client.get(reverse(SEARCH_URL), {"q": "8880011"})
    body = response.content.decode()

    assert response.status_code == 200
    assert [item["case"].pk for item in response.context["results"]] == [issue_case.pk]
    assert "8880011" in body
    # Motivo legível da inelegibilidade (função do slice 002, sem duplicação).
    assert "intercorrência ativa" in body
    assert "Elegível para intercorrência" not in body


# ── R1: casos em andamento não aparecem ───────────────────────────────────


@pytest.mark.django_db
def test_in_progress_cases_are_excluded(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """Casos fora de ``CLEANED`` e sem issue ativa nunca entram no universo."""
    other_nir = user_factory("nir-alheio-ativo", NIR_ROLE)
    active = _open_case(other_nir, patient_name="Andamento Excluído")

    client.force_login(nir_user)
    response = client.get(reverse(SEARCH_URL), {"q": "Andamento Excluído"})
    body = response.content.decode()

    assert response.status_code == 200
    assert response.context is not None
    assert response.context["results"] == []
    # O termo ecoa no campo de busca; o caso não aparece nos resultados.
    assert "Nenhum caso encerrado encontrado para a busca." in body
    assert str(active.case_id) not in _visible_text(body)


# ── R1: estado vazio sem critério ─────────────────────────────────────────


@pytest.mark.django_db
def test_empty_state_without_criterion(client: Client, nir_user: User) -> None:
    """Sem termo, nenhum resultado — o estado vazio orienta o uso da busca."""
    client.force_login(nir_user)
    response = client.get(reverse(SEARCH_URL))
    body = response.content.decode()

    assert response.status_code == 200
    assert response.context is not None
    assert response.context["results"] == []
    assert "Informe um nº de ocorrência ou nome do paciente para buscar." in body


# ── R1: cap 50 + referência humana com sufixo ─────────────────────────────


@pytest.mark.django_db
def test_search_caps_at_fifty_newest_first(
    client: Client,
    nir_user: User,
) -> None:
    """Mais de 50 correspondências → só as 50 mais novas (``-created_at``)."""
    oldest = _eligible_closed_case(nir_user, record_number="9990001", patient_name="Cap Cinquenta")
    newest: Case | None = None
    for index in range(51):
        newest = _eligible_closed_case(
            nir_user,
            record_number=f"999{index:04d}",
            patient_name=f"Cap Cinquenta {index}",
        )
    assert newest is not None

    client.force_login(nir_user)
    response = client.get(reverse(SEARCH_URL), {"q": "Cap Cinquenta"})

    assert response.status_code == 200
    assert response.context is not None
    results = response.context["results"]
    assert len(results) == 50
    assert results[0]["case"].pk == newest.pk
    assert oldest.pk not in {item["case"].pk for item in results}


@pytest.mark.django_db
def test_duplicate_record_number_gets_short_suffix(
    client: Client,
    nir_user: User,
) -> None:
    """Nº duplicado nos resultados → ``N · <8 chars>`` em cada card."""
    first = _eligible_closed_case(nir_user, record_number="6060606", patient_name="Duplo Um")
    second = _eligible_closed_case(nir_user, record_number="6060606", patient_name="Duplo Dois")

    client.force_login(nir_user)
    body = client.get(reverse(SEARCH_URL), {"q": "6060606"}).content.decode()

    assert f"6060606 · {first.short_id}" in body
    assert f"6060606 · {second.short_id}" in body


@pytest.mark.django_db
def test_unique_record_number_has_no_suffix(
    client: Client,
    nir_user: User,
) -> None:
    """Nº único → sem sufixo e sem fragmento de uid no texto visível."""
    case = _eligible_closed_case(nir_user, record_number="7070707", patient_name="Único")

    client.force_login(nir_user)
    body = client.get(reverse(SEARCH_URL), {"q": "7070707"}).content.decode()

    assert "7070707" in body
    assert case.short_id not in _visible_text(body)


# ── R2: detalhe histórico read-only com trilha ────────────────────────────


@pytest.mark.django_db
def test_historical_detail_is_readonly_with_trail(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """Detalhe elegível: identificação + resultado + comunicações + trilha
    fechada por padrão; form de intercorrência presente; nenhuma ação de
    edição do caso é oferecida."""
    other_nir = user_factory("nir-alheio-detalhe", NIR_ROLE)
    case = _eligible_closed_case(other_nir, patient_name="Zuleica Detalhe Nome")

    client.force_login(nir_user)
    response = client.get(reverse(DETAIL_URL, args=[case.case_id]))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Zuleica Detalhe Nome" in body
    assert "5040778" in body
    assert "Comunicações" in body
    # Trilha compartilhada, fechada por padrão (mesmo partial do painel).
    assert "Trilha de eventos" in body
    assert '<div class="collapse" id="case-event-trail">' in body
    assert "collapse show" not in body
    assert 'aria-expanded="false"' in body
    # Form de intercorrência apenas para elegíveis.
    assert "Abrir intercorrência" in body
    assert "Motivo da intercorrência" in body
    # Sem ações de edição: ciência, reenvio, revisão do gate e bypass ausentes.
    assert "Confirmar recebimento" not in body
    assert "Reenviar corrigido" not in body
    assert "Liberar caso" not in body
    assert "procedure_review_confirm" not in body
    assert "case_ack" not in body
    assert "case_resubmit" not in body


@pytest.mark.django_db
def test_historical_detail_issue_active_shows_reason_without_form(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """Detalhe de caso com issue ativa: motivo legível, sem form de abertura."""
    other_nir = user_factory("nir-alheio-detalhe-issue", NIR_ROLE)
    case = _issue_active_case(other_nir)

    client.force_login(nir_user)
    response = client.get(reverse(DETAIL_URL, args=[case.case_id]))
    body = response.content.decode()

    assert response.status_code == 200
    assert response.context is not None
    assert response.context["issue_form"] is None
    assert "intercorrência ativa" in body
    assert "Abrir intercorrência" not in body
    assert "Motivo da intercorrência" not in body


@pytest.mark.django_db
def test_historical_detail_out_of_scope_returns_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """Acesso direto a caso fora do escopo histórico → 404 sem vazar conteúdo."""
    other_nir = user_factory("nir-alheio-fora", NIR_ROLE)
    active = _open_case(other_nir, patient_name="Nome Secreto Fora")

    client.force_login(nir_user)
    response = client.get(reverse(DETAIL_URL, args=[active.case_id]))
    body = response.content.decode()

    assert response.status_code == 404
    assert "Nome Secreto Fora" not in body
    assert str(active.case_id) not in body


# ── R3: POST de abertura — válido e inválido ─────────────────────────────


@pytest.mark.django_db
def test_open_valid_reopens_case_with_flash_and_redirect(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """POST válido → ``AWAITING_SCHEDULING`` + issue + evento com motivo e
    snapshot + comunicação + flash com referência humana + redirect."""
    other_nir = user_factory("nir-alheio-post", NIR_ROLE)
    case = _eligible_closed_case(other_nir)
    scheduled_at = case.scheduled_datetime
    assert scheduled_at is not None
    events_before = case.events.count()

    client.force_login(nir_user)
    response = client.post(reverse(DETAIL_URL, args=[case.case_id]), {"reason": REOPEN_REASON})

    assert response.status_code == 302
    assert response["Location"] == reverse(DETAIL_URL, args=[case.case_id])
    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.AWAITING_SCHEDULING
    assert fresh.post_schedule_issue_status == POST_CLOSURE_ISSUE_STATUS_OPENED
    assert fresh.post_schedule_issue_reason == REOPEN_REASON
    # Auditoria: transição com motivo + abertura com motivo e snapshot.
    trail = [event.event_type for event in fresh.events.order_by("id")]
    assert trail[events_before:] == [
        CaseEventType.CASE_STATUS_AWAITING_SCHEDULING,
        CaseEventType.CASE_POST_CLOSURE_ISSUE_OPENED,
    ]
    opened = fresh.events.order_by("id")[events_before + 1]
    assert opened.payload["reason"] == REOPEN_REASON
    assert opened.payload["scheduling_snapshot"]["scheduled_unit"] == SchedulingUnit.UNIT_1
    # Comunicação da thread com o motivo interpolado.
    assert any(
        REOPEN_REASON in body
        for body in fresh.communication_messages.values_list("body", flat=True)
    )
    # Flash com referência humana + destino renderiza o caso reaberto.
    followed = client.get(response["Location"])
    assert "Intercorrência aberta" in followed.content.decode()
    assert "5040778" in followed.content.decode()


@pytest.mark.django_db
def test_open_blank_reason_renders_error_without_writes(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """Motivo ausente → re-render com erro, sem NENHUMA escrita (garantia do
    serviço: status, eventos, issue e comunicação intactos)."""
    other_nir = user_factory("nir-alheio-invalido", NIR_ROLE)
    case = _eligible_closed_case(other_nir)
    events_before = case.events.count()

    client.force_login(nir_user)
    response = client.post(reverse(DETAIL_URL, args=[case.case_id]), {"reason": "   "})
    body = response.content.decode()

    assert response.status_code == 400
    assert "Informe o motivo da intercorrência" in body
    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    assert fresh.events.count() == events_before
    assert fresh.post_schedule_issue_status == ""
    assert fresh.communication_messages.count() == 0


@pytest.mark.django_db
def test_open_ineligible_case_writes_nothing(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """POST sobre caso com issue ativa → re-render com erro, sem escrita."""
    other_nir = user_factory("nir-alheio-inelegivel", NIR_ROLE)
    case = _issue_active_case(other_nir)
    events_before = case.events.count()

    client.force_login(nir_user)
    response = client.post(reverse(DETAIL_URL, args=[case.case_id]), {"reason": "outro motivo"})
    body = response.content.decode()

    assert response.status_code == 400
    assert response.context is not None
    assert response.context["issue_form"] is None
    assert "intercorrência ativa" in body
    assert "Abrir intercorrência" not in body
    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.AWAITING_SCHEDULING
    assert fresh.events.count() == events_before
    assert fresh.post_schedule_issue_reason == REOPEN_REASON


# ── R4: guards — papel não-NIR e intranet ─────────────────────────────────


@pytest.mark.django_db
def test_non_nir_role_forbidden_on_search_and_detail(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """Papel ativo fora de ``nir`` → 403 na busca e no detalhe histórico."""
    case = _eligible_closed_case(nir_user)
    scheduler = user_factory("agendador-403", SCHEDULER_ROLE)
    doctor = user_factory("medico-403", DOCTOR_ROLE)

    for user in (scheduler, doctor):
        client.force_login(user)
        assert client.get(reverse(SEARCH_URL), {"q": "5040778"}).status_code == 403
        assert client.get(reverse(DETAIL_URL, args=[case.case_id])).status_code == 403
        assert (
            client.post(
                reverse(DETAIL_URL, args=[case.case_id]), {"reason": REOPEN_REASON}
            ).status_code
            == 403
        )


@pytest.mark.django_db
def test_historical_search_reachable_from_nir_nav(
    client: Client,
    nir_user: User,
) -> None:
    """O nav do NIR expõe a busca histórica em link distinto da aba por criador."""
    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()
    search_url = reverse(SEARCH_URL)

    assert f'href="{search_url}"' in body
    # A aba de encerrados por criador permanece apontando para my_cases.
    assert 'href="/intake/cases/?tab=closed"' in body

    followed = client.get(search_url)
    assert followed.status_code == 200
    assert "Buscar por n" in followed.content.decode()


@pytest.mark.django_db
@override_settings(INTRANET_IP_RANGE=INTRANET_CIDR, INTRANET_RESTRICTED_ROLES=["nir"])
def test_intranet_guard_covers_historical_search(client: Client, nir_user: User) -> None:
    """Guard de intranet do papel ``nir`` (ADR-0002) cobre a busca histórica:
    IP externo → 403, como nas rotas intake vigentes (middleware global)."""
    client.force_login(nir_user)
    response = client.get(reverse(SEARCH_URL), {"q": "5040778"}, REMOTE_ADDR=EXTERNAL_IP)

    assert response.status_code == 403
