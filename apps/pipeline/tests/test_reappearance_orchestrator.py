"""Substituição médica pelo orquestrador real (slice 002, R2/R4 — RED).

Integração de ponta a ponta com doubles SOMENTE na fronteira LLM
(``LLM_CLIENT_FACTORY`` fake): novo pronto substitui o original; novo
falho/retido/divergente ou com tipo alterado mantém o original intacto.
Estrutura do atomic final: mutex antes do primeiro row lock do fluxo.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.models import (
    Case,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    ReappearanceState,
)
from apps.pipeline.orchestrator import process_case_pipeline

FAKE_MODEL_LLM1 = "modelo-llm1-teste"
FAKE_MODEL_LLM2 = "modelo-llm2-teste"
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"
OTHER_TYPE = "cat_cardiaco"

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)

_ANONYMIZED_TEXT = (
    "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente. "
    "Solicitada avaliacao hemodinamica."
)
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "JOSE MARIA DE SOUZA", "entity_type": "PERSON"},
    "<DATA_1>": {"value": "05/03/1972", "entity_type": "DATE_TIME"},
}


class ScriptedLlmClient:
    """Fake com roteiro compartilhado LLM1+LLM2 (só fronteira LLM)."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, list[dict[str, str]], dict[str, Any] | None]] = []

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append((model, list(messages), json_schema))
        if not self.responses:
            raise AssertionError("cliente fake sem respostas agendadas")
        return self.responses.pop(0)


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
        "summary_text": "Sumario clinico em portugues, sem intercorrencias.",
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
def _seeded_prompts() -> None:
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture
def owner_user() -> User:
    return User.objects.create_user(username="dono-reap", password="senha-teste")


def _awaiting_prior(
    *,
    owner: User,
    name: str = "Maria da Silva",
    number: str = "orc-1",
    declared: tuple[str, ...] = (ANGIO_TYPE,),
) -> Case:
    prior = Case.objects.create(created_by=owner)
    for procedure_type in declared:
        CaseProcedure.objects.create(
            case=prior, procedure_type=procedure_type, declared_by_nir=True
        )
    prior.patient_name = name
    prior.agency_record_number = number
    prior.save(update_fields=["patient_name", "agency_record_number"])
    prior.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    prior.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    prior.complete_anonymization(user=None, role=SYSTEM_ROLE)
    prior.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    prior.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    return prior


def _extracting_incoming(
    *,
    owner: User,
    name: str = "Maria da Silva",
    number: str = "orc-1",
    declared: tuple[str, ...] = (ANGIO_TYPE,),
) -> Case:
    case = Case.objects.create(created_by=owner)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    for procedure_type in declared:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    case.patient_name = name
    case.agency_record_number = number
    case.anonymized_text = _ANONYMIZED_TEXT
    case.pseudonym_map = dict(_PSEUDONYMS)
    case.save()
    assert case.status == CaseStatus.LLM_EXTRACTING
    return case


def _run(case: Case, fake: ScriptedLlmClient) -> None:
    with override_settings(
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    ):
        process_case_pipeline(case.case_id)


# ── R2: novo pronto substitui ─────────────────────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_ready_new_replaces_original_through_real_orchestrator(
    owner_user: User,
) -> None:
    """R2: orquestrador real (doubles só no LLM) — novo pronto encerra original."""
    prior = _awaiting_prior(owner=owner_user)
    incoming = _extracting_incoming(owner=owner_user)
    fake = ScriptedLlmClient(
        [json.dumps(_artifact([ANGIO_TYPE])), json.dumps(_llm2_response([ANGIO_TYPE]))]
    )
    _run(incoming, fake)
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert prior.status == CaseStatus.CLEANED
    assert (
        CaseReappearance.objects.filter(
            incoming_case=incoming,
            prior_case=prior,
            state=ReappearanceState.APPLIED,
        ).count()
        == 1
    )
    assert prior.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_SUPERSEDED).exists()
    assert incoming.events.filter(event_type=CaseEventType.CASE_REAPPEARANCE_APPLIED).exists()
    assert incoming.reappearance_wait_started_at == prior.created_at


@NEW_MODE
@pytest.mark.django_db
def test_failed_new_keeps_original(owner_user: User) -> None:
    """R2: LLM2 falha — novo FAILED, original intacto, sem vínculo."""
    from apps.pipeline.llm import LlmError

    prior = _awaiting_prior(owner=owner_user)
    incoming = _extracting_incoming(owner=owner_user)

    class _FailingClient(ScriptedLlmClient):
        def complete(
            self,
            model: str,
            messages: Sequence[dict[str, str]],
            *,
            json_schema: dict[str, Any] | None = None,
        ) -> str:
            if model == FAKE_MODEL_LLM2:
                raise LlmError("timeout", "falha")
            return super().complete(model, messages, json_schema=json_schema)

    _run(incoming, _FailingClient([json.dumps(_artifact([ANGIO_TYPE]))]))
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.FAILED
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()


@NEW_MODE
@pytest.mark.django_db
def test_divergent_new_stays_retained(owner_user: User) -> None:
    """R2: divergência declarado×detectado — novo retido, original intacto."""
    prior = _awaiting_prior(owner=owner_user)
    incoming = _extracting_incoming(owner=owner_user)
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact([OTHER_TYPE])),
            json.dumps(_llm2_response([OTHER_TYPE])),
        ]
    )
    _run(incoming, fake)
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.LLM_EXTRACTING
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()


@NEW_MODE
@pytest.mark.django_db
def test_type_changed_new_completes_without_substitution(owner_user: User) -> None:
    """R2: tipo efetivo alterado — novo completa, original permanece."""
    prior = _awaiting_prior(owner=owner_user, declared=(ANGIO_TYPE,))
    incoming = _extracting_incoming(owner=owner_user, declared=(OTHER_TYPE,))
    fake = ScriptedLlmClient(
        [json.dumps(_artifact([OTHER_TYPE])), json.dumps(_llm2_response([OTHER_TYPE]))]
    )
    _run(incoming, fake)
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert prior.status == CaseStatus.AWAITING_DOCTOR
    assert not CaseReappearance.objects.exists()


# ── R4: ordem do atomic final ─────────────────────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_final_atomic_acquires_mutex_before_first_row_lock(
    owner_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4/D4 FINAL: mutex (advisory) precede qualquer row lock do fluxo final."""
    prior = _awaiting_prior(owner=owner_user)
    incoming = _extracting_incoming(owner=owner_user)
    fake = ScriptedLlmClient(
        [json.dumps(_artifact([ANGIO_TYPE])), json.dumps(_llm2_response([ANGIO_TYPE]))]
    )
    import apps.cases.reappearance as module

    seen: list[str] = []

    from django.db.backends.utils import CursorWrapper

    real_execute = CursorWrapper.execute

    def _execute(self: Any, sql: Any, params: Any = None) -> Any:
        seen.append(str(sql))
        return real_execute(self, sql, params)

    real_acquire = getattr(module, "acquire_reappearance_mutex")

    def _scoped_acquire(key: int) -> None:
        # Só registra; nunca limpa (AUD-07): a captura começa ANTES do atomic
        # final, na entrada de ``finalize_summarization``.
        real_acquire(key)

    import apps.pipeline.orchestrator as orchestrator_module

    real_finalize: Any = getattr(orchestrator_module, "finalize_summarization")

    def _scoped_finalize(*args: Any, **kwargs: Any) -> Any:
        # Captura desde ANTES de qualquer row lock do atomic final (D4):
        # limpa aqui — nunca dentro do mutex — para que um FOR UPDATE
        # prévio no fluxo final não seja apagado da prova.
        seen.clear()
        return real_finalize(*args, **kwargs)

    monkeypatch.setattr(CursorWrapper, "execute", _execute)
    monkeypatch.setattr(module, "acquire_reappearance_mutex", _scoped_acquire)
    monkeypatch.setattr(orchestrator_module, "finalize_summarization", _scoped_finalize)
    _run(incoming, fake)
    advisory_index = next(index for index, sql in enumerate(seen) if "pg_advisory_xact_lock" in sql)
    row_lock_index = next(index for index, sql in enumerate(seen) if "FOR UPDATE" in sql.upper())
    assert advisory_index < row_lock_index
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert prior.status == CaseStatus.CLEANED
