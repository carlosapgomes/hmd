"""Auditoria legível da revisão no painel (slice 004, R2 — fix-1 P1-1).

Prova as linhas de ``event_detail_lines`` sobre um fluxo real
confirmação + APPLIED (serviço + orquestrador, fake só no transporte LLM):
CONFIRMED mantido/trocado com justificativa, APPLIED com conjunto em análise
+ detecção exata ou ausência explícita, INVALIDATED com motivo, linhas tersas
de ENABLED/REQUESTED/ENQUEUE_FAILED, ``[]`` para outros tipos e escaping SSR
do ``dashboard:case_detail`` (markup da justificativa sai como texto).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from io import StringIO
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.cases.events import CaseEventType
from apps.cases.models import (
    ActorType,
    Case,
    CaseDocument,
    CaseEvent,
    CaseProcedure,
    CaseStatus,
)
from apps.cases.procedure_review import event_detail_lines, latest_review_event

NIR_ROLE = "nir"
MANAGER_ROLE = "manager"
SYSTEM_ROLE = "system"
FAKE_MODEL_LLM1 = "modelo-llm1-teste"
FAKE_MODEL_LLM2 = "modelo-llm2-teste"

DECLARED_A = "art_perif"
DETECTED_B = "cat_cardiaco"
SWITCH_C = "nefrostomia"

_ANONYMIZED_TEXT = (
    "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente. "
    "Solicitada avaliacao hemodinamica."
)
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "FULANO DE TAL SILVA", "entity_type": "PERSON"},
}


class ScriptedLlmClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        assert self.responses, "cliente fake sem respostas agendadas"
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return str(item)


def _artifact(procedure_types: list[str]) -> dict[str, object]:
    return {
        "pedido": {
            "procedimentos_solicitados": procedure_types,
            "evidence_spans": [
                {
                    "field_path": "pedido.procedimentos_solicitados",
                    "excerpt": "O relatorio solicita os procedimentos listados acima.",
                }
            ],
        },
        "contexto_clinico": "Paciente <PESSOA_1> com claudicacao intermitente ha tres meses.",
        "linha_do_tempo": [],
        "exames": None,
        "medicacoes": [],
        "comorbidades": [],
        "contraindicacoes": [],
        "trechos_nao_classificados": [],
    }


def _llm2_response(procedure_types: list[str]) -> dict[str, object]:
    return {
        "summary_text": "Sumario clinico em portugues para o procedimento confirmado.",
        "procedures": [
            {
                "procedure_type": procedure_type,
                "suggestion": "aceitar",
                "motivos": ["sem contraindicoes identificadas"],
                "evidence_spans": [],
            }
            for procedure_type in procedure_types
        ],
    }


@pytest.fixture(autouse=True)
def _seeded_prompts() -> Any:
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture
def nir_user() -> User:
    role, _ = Role.objects.get_or_create(name=NIR_ROLE)
    user = User.objects.create_user(username="nir-audit-004", password="senha-teste")
    user.roles.add(role)
    return user


@pytest.fixture
def manager_user() -> User:
    role, _ = Role.objects.get_or_create(name=MANAGER_ROLE)
    user = User.objects.create_user(username="gestor-audit-004", password="senha-teste")
    user.roles.add(role)
    return user


def _login(client: Client, user: User, role: str) -> None:
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _chain(fake: ScriptedLlmClient) -> Any:
    return override_settings(
        LLM_RUN_TASKS_INLINE=True,
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    )


def _make_retained(owner: User, *, declared: str) -> Case:
    from apps.pipeline.orchestrator import process_case_pipeline

    case = Case.objects.create(created_by=owner)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    content = b"%PDF-1.4 relatorio sintetico de auditoria"
    document = CaseDocument(
        case=case,
        position=1,
        original_filename="relatorio.pdf",
        content_type="application/pdf",
        size_bytes=len(content),
        uploaded_by=owner,
    )
    document.file.save("relatorio.pdf", ContentFile(content), save=False)
    document.save()
    case.extracted_text = "Texto extraido do relatorio sintetico de auditoria."
    case.anonymized_text = _ANONYMIZED_TEXT
    case.pseudonym_map = dict(_PSEUDONYMS)
    case.save()
    CaseProcedure.objects.create(case=case, procedure_type=declared, declared_by_nir=True)
    CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={"version": 1},
    )
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_B]))])
    with _chain(fake):
        process_case_pipeline(case.case_id)
    case.refresh_from_db()
    assert case.manual_review_required is True
    return case


def _confirm_and_reprocess(
    case: Case,
    owner: User,
    *,
    procedure_type: str,
    new_detected: list[str],
    justification: str = "Confirmo apos leitura do relatorio.",
) -> Case:
    from apps.intake.services import confirm_case_procedure_review
    from apps.pipeline.orchestrator import process_case_pipeline

    review = latest_review_event(case)
    assert review is not None
    from apps.cases.procedure_review import source_fingerprint_for_case as _fp

    fingerprint = _fp(case)
    assert fingerprint is not None
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(new_detected)),
            json.dumps(_llm2_response([procedure_type])),
        ]
    )
    with _chain(fake):
        confirm_case_procedure_review(
            case=case,
            user=owner,
            role=NIR_ROLE,
            procedure_type=procedure_type,
            review_acknowledged="on",
            review_justification=justification,
            review_event_id=str(review.pk),
            source_fingerprint=fingerprint,
        )
        process_case_pipeline(case.case_id)
    case.refresh_from_db()
    return case


@pytest.mark.django_db
def test_confirmed_lines_kept_variant(nir_user: User) -> None:
    """CONFIRMED mantido: 'Seleção mantida' + 'Justificativa: …'."""
    case = _make_retained(nir_user, declared=DECLARED_A)
    justification = "Confirmo a selecao declarada apos leitura."
    _confirm_and_reprocess(
        case,
        nir_user,
        procedure_type=DECLARED_A,
        new_detected=["nefrostomia"],
        justification=justification,
    )
    confirmed = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED)

    lines = event_detail_lines(confirmed)

    assert any("Seleção mantida" in line for line in lines)
    assert any(f"Justificativa: {justification}" in line for line in lines)


@pytest.mark.django_db
def test_confirmed_lines_switched_variant(nir_user: User) -> None:
    """CONFIRMED trocado: 'Seleção alterada' + 'Justificativa: …'."""
    case = _make_retained(nir_user, declared=DECLARED_A)
    justification = "Troco apos releitura do relatorio."
    _confirm_and_reprocess(
        case,
        nir_user,
        procedure_type=SWITCH_C,
        new_detected=["flebografia"],
        justification=justification,
    )
    confirmed = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED)

    lines = event_detail_lines(confirmed)

    assert any("Seleção alterada" in line for line in lines)
    assert any(f"Justificativa: {justification}" in line for line in lines)


@pytest.mark.django_db
def test_applied_lines_show_analysis_set_and_exact_new_detection(nir_user: User) -> None:
    """APPLIED: linha do conjunto em análise + detecção exata nova."""
    case = _make_retained(nir_user, declared=DECLARED_A)
    _confirm_and_reprocess(case, nir_user, procedure_type=DECLARED_A, new_detected=["nefrostomia"])
    applied = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED)

    lines = event_detail_lines(applied)

    assert any("Conjunto em análise" in line for line in lines)
    assert any("Detecção automática" in line and "Nefrostomia" in line for line in lines)
    assert applied.payload["automatic_procedures"] == ["nefrostomia"]


@pytest.mark.django_db
def test_applied_lines_empty_detection_reports_absence(nir_user: User) -> None:
    """APPLIED vazio: ausência explícita, sem afirmar consenso."""
    case = _make_retained(nir_user, declared=SWITCH_C)
    _confirm_and_reprocess(case, nir_user, procedure_type=SWITCH_C, new_detected=[])
    applied = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED)
    assert applied.payload["automatic_procedures"] == []

    lines = event_detail_lines(applied)

    assert any("Conjunto em análise" in line for line in lines)
    assert any("Nenhum" in line for line in lines)


@pytest.mark.django_db
def test_invalidated_lines_carry_reason(nir_user: User) -> None:
    """INVALIDATED: 'Confirmação desconsiderada: <motivo>'."""
    from apps.cases.procedure_review import record_review_invalidated

    case = _make_retained(nir_user, declared=DECLARED_A)
    _confirm_and_reprocess(case, nir_user, procedure_type=DECLARED_A, new_detected=["nefrostomia"])
    assert case.status == CaseStatus.AWAITING_DOCTOR
    confirmed = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED)
    invalidated = record_review_invalidated(case, confirmed, "review_superseded")

    lines = event_detail_lines(invalidated)

    assert len(lines) == 1
    assert "Confirmação desconsiderada" in lines[0]
    assert "revisão mais recente" in lines[0]


@pytest.mark.django_db
def test_terse_lines_for_lifecycle_events(nir_user: User) -> None:
    """ENABLED/REQUESTED/ENQUEUE_FAILED rendem as linhas tersas fixas."""
    case = _make_retained(nir_user, declared=DECLARED_A)
    enabled = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED)
    assert event_detail_lines(enabled) == ["Caso sob o contrato de revisão de procedimento do NIR."]

    requested = CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REPROCESSING_REQUESTED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={},
    )
    assert event_detail_lines(requested) == [
        "Reprocessamento do mesmo caso solicitado após confirmação."
    ]

    scheduled = CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={"recovery_scheduled": True},
    )
    assert event_detail_lines(scheduled) == [
        "Falha ao enfileirar o reprocessamento; recuperação programada."
    ]

    unsupported = CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={},
    )
    assert event_detail_lines(unsupported) == [
        "Falha ao enfileirar o reprocessamento; processamento pendente de suporte."
    ]


@pytest.mark.django_db
def test_other_event_types_yield_no_lines(nir_user: User) -> None:
    """Outros tipos: ``[]`` — a view pode passar qualquer evento sem ramificar."""
    case = _make_retained(nir_user, declared=DECLARED_A)
    other = CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_STATUS_AWAITING_DOCTOR,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={},
    )

    assert event_detail_lines(other) == []


@pytest.mark.django_db
def test_dashboard_case_detail_escapes_justification_markup(
    client: Client, nir_user: User, manager_user: User
) -> None:
    """SSR do painel: markup da justificativa sai como texto (``&lt;…&gt;``)."""
    case = _make_retained(nir_user, declared=DECLARED_A)
    _confirm_and_reprocess(
        case,
        nir_user,
        procedure_type=DECLARED_A,
        new_detected=["nefrostomia"],
        justification='Texto com <b>negrito</b> e <script>alert("xss")</script>.',
    )
    assert case.status == CaseStatus.AWAITING_DOCTOR

    _login(client, manager_user, MANAGER_ROLE)
    response = client.get(reverse("dashboard:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "&lt;b&gt;" in body
    assert "&lt;script&gt;" in body
    assert '<script>alert("xss")</script>' not in body
    assert "<b>negrito</b>" not in body
