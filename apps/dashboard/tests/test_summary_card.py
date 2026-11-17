"""Card «Sumário apresentado ao médico» no detalhe do painel (slice 002, R4).

O card exibe, para manager/admin, o MESMO sumário clínico e a MESMA
estrutura extraída apresentados ao médico — re-identificados na renderização
com o mapa do caso, via a API compartilhada ``build_structure_sections``
(D5) e o presenter ``build_case_summary_context`` (D5/R2). Collapsible
FECHADO por padrão (D6), entre «Procedimentos declarados» e «Texto Extraído
do PDF», com placeholders explícitos INDEPENDENTES por bloco. Nem token de
pseudônimo vaza na renderização. Papel ativo fora de manager/admin → 403.

Molde: ``apps/dashboard/tests/test_detail_cards.py`` (HTTP, login manager,
asserções de HTML); casos populados via ``Case.objects...update`` — sem FSM.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.cases.closure import acknowledge_case_receipt
from apps.cases.models import Case, CaseStatus
from apps.dashboard.presenters import build_case_summary_context
from apps.doctor.presenters import build_case_detail_context

MANAGER_ROLE = "manager"
ADMIN_ROLE = "admin"

# Mapa do caso (token → valor real), mesmo shape de ``Case.pseudonym_map``.
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "MARIA DA SILVA", "entity_type": "PERSON"},
    "<DATA_1>": {"value": "05/03/1972", "entity_type": "DATE_TIME"},
    "<CPF_1>": {"value": "111.222.333-44", "entity_type": "CPF"},
}

_SUMMARY_TEXT = "Resumo clínico: paciente <PESSOA_1> com claudicação desde <DATA_1>."

# Artefato LLM1 (só tokens) — subset representativo das 8 seções.
_STRUCTURED_DATA: dict[str, object] = {
    "pedido": {
        "procedimentos_solicitados": ["art_perif"],
        "evidence_spans": [
            {
                "field_path": "pedido.procedimentos_solicitados",
                "excerpt": "Solicitado pelo CRM de <PESSOA_1>.",
            }
        ],
    },
    "contexto_clinico": "Paciente <PESSOA_1> com claudicação intermitente desde <DATA_1>.",
    "linha_do_tempo": [],
    "exames": {
        "platelets": {
            "status": "confirmado",
            "evidence_spans": [],
            "value": 80000,
            "unit": "/mm³",
        },
        "inr": {"status": "nao_informado", "evidence_spans": [], "value": None, "unit": None},
    },
    "medicacoes": [],
    "comorbidades": [],
    "contraindicacoes": [],
    "trechos_nao_classificados": ["Trecho com <CPF_1> citado."],
}


def _make_user(username: str, role_name: str = MANAGER_ROLE) -> User:
    """Usuário com um papel (para o middleware resolver o papel ativo)."""
    role, _ = Role.objects.get_or_create(name=role_name)
    user = User.objects.create_user(username=username, password="senha-teste")
    user.roles.add(role)
    return user


def _login(client: Client, user: User, role: str = MANAGER_ROLE) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _make_case(
    creator: User,
    *,
    summary_text: str = "",
    structured_data: dict[str, object] | None = None,
    pseudonym_map: dict[str, dict[str, str]] | None = None,
    extracted_text: str = "",
    status: str = CaseStatus.NEW,
) -> Case:
    """Caso do painel com os artefatos informados (sem passar pela FSM)."""
    case = Case.objects.create(created_by=creator)
    Case.objects.filter(pk=case.pk).update(
        summary_text=summary_text,
        structured_data=dict(structured_data) if structured_data else {},
        pseudonym_map=dict(pseudonym_map) if pseudonym_map else {},
        extracted_text=extracted_text,
        status=status,
    )
    case.refresh_from_db()
    return case


def _make_case_with_artifacts(creator: User, **kwargs: object) -> Case:
    """Caso com artefatos do pipeline + mapa populados (modo retenção-friendly)."""
    kwargs.setdefault("summary_text", _SUMMARY_TEXT)
    kwargs.setdefault("structured_data", _STRUCTURED_DATA)
    kwargs.setdefault("pseudonym_map", _PSEUDONYMS)
    return _make_case(creator, **kwargs)  # type: ignore[arg-type]


def _detail_url(case: Case) -> str:
    return reverse("dashboard:case_detail", args=[case.case_id])


def _detail_body(client: Client, case: Case) -> str:
    response = client.get(_detail_url(case))
    assert response.status_code == 200
    return response.content.decode()


def _assert_no_pseudonym_tokens(body: str) -> None:
    """Nenhum token do mapa vaza — nem cru, nem escapado em HTML."""
    for token in ("<PESSOA_", "<DATA_", "<CPF_", "&lt;PESSOA_", "&lt;DATA_", "&lt;CPF_"):
        assert token not in body


def _strip_html_comments(body: str) -> str:
    """HTML sem comentários — as asserções de ordem casam elementos REAIS,
    nunca strings de comentário (P3)."""
    return re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)


# ── R4: card fechado + conteúdo re-identificado + posição (D6) ────────────


@pytest.mark.django_db
@pytest.mark.parametrize("role", [MANAGER_ROLE, ADMIN_ROLE])
def test_manager_and_admin_see_closed_card_with_reidentified_content(
    client: Client,
    role: str,
) -> None:
    """R4/D6: manager/admin vê o card fechado por padrão com o conteúdo real."""
    user = _make_user(f"gestor-sumario-{role}", role)
    case = _make_case_with_artifacts(user)
    _login(client, user, role)

    body = _detail_body(client, case)

    assert "Sumário apresentado ao médico" in body
    assert "Sumário clínico" in body
    assert "Estrutura extraída" in body
    # Fechado por padrão: collapse sem ``show`` e botão com aria-expanded false.
    assert "collapse show" not in body
    assert 'aria-expanded="false" aria-controls="summary-collapse"' in body
    # Conteúdo re-identificado: valores reais presentes, zero tokens.
    assert "Resumo clínico: paciente MARIA DA SILVA com claudicação desde 05/03/1972." in body
    assert "Paciente MARIA DA SILVA com claudicação intermitente desde 05/03/1972." in body
    assert "111.222.333-44" in body
    assert "80000" in body
    _assert_no_pseudonym_tokens(body)


@pytest.mark.django_db
def test_summary_card_position_between_procedures_and_extracted_text(
    client: Client,
) -> None:
    """R4/D6: o card fica entre «Procedimentos declarados» e «Texto Extraído».

    Âncoras por elementos REAIS (botões dos collapses + cabeçalhos), sobre o
    body sem comentários — nunca strings de comentário (P3) — em ordem
    crescente: procedimentos → sumário → texto → trilha.
    """
    manager = _make_user("gestor-sumario-ordem")
    case = _make_case_with_artifacts(manager, extracted_text="TEXTO EXTRAIDO DO RELATORIO")
    _login(client, manager)

    visible = _strip_html_comments(_detail_body(client, case))

    procedures_index = visible.index("Procedimentos declarados")
    summary_index = visible.index('data-bs-target="#summary-collapse"')
    text_index = visible.index('data-bs-target="#text-collapse"')
    trail_index = visible.index("Trilha de eventos")
    assert procedures_index < summary_index < text_index < trail_index, (
        "card fora da ordem D6 (procedimentos → sumário → texto → trilha)"
    )


# ── R4: caso CLEANED retido (fixture do slice 001) ────────────────────────


@pytest.mark.django_db
def test_cleaned_case_with_retained_artifacts_renders_summary(
    client: Client,
) -> None:
    """R4: caso CLEANED encerrado em RETENÇÃO (fluxo real) renderiza o sumário.

    O estado é construído PELO FLUXO REAL (P2): artefatos populados + status
    ``FINAL_REPLY_POSTED`` (via update, para viabilizar a ciência) e
    encerramento com ``acknowledge_case_receipt`` sob o pino explícito de
    retenção ``CASE_CLOSURE_MINIMIZATION_ENABLED=False`` (molde do slice
    001) — os artefatos sobrevivem ao encerramento e o card exibe o mesmo
    sumário/estrutura apresentados ao médico, re-identificados.
    """
    creator = _make_user("nir-sumario-retido", "nir")
    case = _make_case_with_artifacts(creator, status=CaseStatus.FINAL_REPLY_POSTED)
    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False):
        acknowledge_case_receipt(case, user=creator, role="nir")
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert case.summary_text == _SUMMARY_TEXT
    assert case.structured_data == _STRUCTURED_DATA

    manager = _make_user("gestor-sumario-retido")
    _login(client, manager)

    body = _detail_body(client, case)

    assert "Sumário apresentado ao médico" in body
    assert "Resumo clínico: paciente MARIA DA SILVA" in body
    assert "Paciente MARIA DA SILVA com claudicação intermitente" in body
    _assert_no_pseudonym_tokens(body)


# ── R4: placeholders explícitos independentes por bloco (D6) ─────────────


@pytest.mark.django_db
def test_case_without_artifacts_shows_placeholders_per_block(
    client: Client,
) -> None:
    """R4: sem artefatos o card permanece com placeholder em CADA bloco."""
    manager = _make_user("gestor-sumario-vazio")
    case = _make_case(manager)
    _login(client, manager)

    body = _detail_body(client, case)

    assert "Sumário apresentado ao médico" in body
    assert "Sumário clínico não disponível para este caso." in body
    assert "Nenhuma estrutura extraída." in body


@pytest.mark.django_db
def test_structure_without_summary_shows_structure_and_summary_placeholder_only(
    client: Client,
) -> None:
    """R4/D6: estrutura sem sumário mostra a estrutura e placeholder só no sumário."""
    manager = _make_user("gestor-sumario-sem-resumo")
    case = _make_case_with_artifacts(manager, summary_text="")
    _login(client, manager)

    body = _detail_body(client, case)

    # Bloco sumário: placeholder. Bloco estrutura: conteúdo, SEM placeholder.
    assert "Sumário clínico não disponível para este caso." in body
    assert "Paciente MARIA DA SILVA com claudicação intermitente" in body
    assert "Nenhuma estrutura extraída." not in body


# ── R4: papel ativo fora de manager/admin → 403 ──────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["nir", "doctor", "scheduler"])
def test_non_manager_roles_get_403(client: Client, role: str) -> None:
    """R4: papel ativo nir/doctor/scheduler → 403 sem conteúdo do sumário."""
    user = _make_user(f"usuario-sumario-{role}", role)
    case = _make_case_with_artifacts(user)
    _login(client, user, role)

    response = client.get(_detail_url(case))

    assert response.status_code == 403
    body = response.content.decode()
    assert "Sumário apresentado ao médico" not in body
    assert "Resumo clínico: paciente MARIA DA SILVA" not in body


# ── R4: paridade com o presenter médico (D5) ──────────────────────────────


@pytest.mark.django_db
def test_summary_context_matches_doctor_context() -> None:
    """R4/D5: ``summary_lines``/``sections`` idênticos ao do detalhe médico."""
    creator = _make_user("criador-paridade")
    case = _make_case_with_artifacts(creator)

    summary_context = build_case_summary_context(case)
    doctor_context = build_case_detail_context(case)

    assert summary_context["summary_lines"] == doctor_context["summary_lines"]
    assert summary_context["sections"] == doctor_context["sections"]
    assert summary_context["has_structure"] == doctor_context["has_structure"]
    assert summary_context["summary_lines"] == [
        "Resumo clínico: paciente MARIA DA SILVA com claudicação desde 05/03/1972."
    ]
