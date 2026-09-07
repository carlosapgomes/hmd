"""Testes do serviço LLM1 com guardas + reconciliação + gate de divergência.

Cobre R2 (extração com guardas: validação strict, language guard pt-BR com
retry corretivo único — a resposta inválida anterior nunca é reenviada — e
fail-closed via ``LlmPipelineError`` tipado; o orquestrador é o único dono do
``fail_processing``), R4 (upsert da detecção em ``record_detected_procedures``),
R5 (divergência retém em ``LLM_EXTRACTING`` com flag+motivo+evento, sem
transição; coincidência atualiza as rows e o caller pode avançar) e R6 (nova
transição ``bypass_pipeline_divergence`` com evento ``CASE_GATE_BYPASSED``).

O cliente é sempre um fake via ``LLM_CLIENT_FACTORY`` (override_settings) — a
suíte nunca toca rede. Os prompts do seed são criados pelo fixture autouse
(``seed_prompts``), como nos testes do slice 003.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.test import override_settings
from django_fsm import TransitionNotAllowed

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.models import (
    ActorType,
    Case,
    CaseProcedure,
    CaseStatus,
    DetectionStatus,
)
from apps.cases.procedures import record_detected_procedures
from apps.pipeline.llm import LlmPipelineError
from apps.pipeline.llm1_service import Llm1ExtractionResult, run_llm1_extraction

FAKE_MODEL = "modelo-llm1-teste"
SYSTEM_ROLE = "system"

# Texto anonimizado do caso (só tokens; valores reais vivem no pseudonym_map).
_ANONYMIZED_TEXT = (
    "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente. "
    "Solicitada avaliacao hemodinamica."
)
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "JOSE MARIA DE SOUZA", "entity_type": "PERSON"},
    "<DATA_1>": {"value": "05/03/1972", "entity_type": "DATE_TIME"},
}

# Resposta em inglês (schema válido, idioma errado) para o language guard.
_ENGLISH_ARTIFACT_OVERRIDE = "Patient reports claudication; denied previous stroke. Summary: none."


class ScriptedLlmClient:
    """Cliente fake com roteiro de respostas (uma string por chamada).

    Registra ``(model, messages, json_schema)`` de cada chamada para os
    asserts de contrato (modelo configurado, correção de retry sem reenvio da
    resposta inválida anterior, schema normalizado).
    """

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


def _artifact(procedure_types: list[str], *, english_context: bool = False) -> dict[str, object]:
    """Artefato LLM1 schema-válido para os tipos detectados dados."""
    contexto: str
    if english_context:
        contexto = _ENGLISH_ARTIFACT_OVERRIDE
    else:
        contexto = "Paciente <PESSOA_1> com claudicacao intermitente ha tres meses."
    return {
        "pedido": {
            "procedimentos_solicitados": procedure_types,
            "evidence_spans": [
                {
                    "field_path": "pedido.procedimentos_solicitados",
                    "excerpt": "O relatorio solicita o procedimento listado acima.",
                }
            ],
        },
        "contexto_clinico": contexto,
        "linha_do_tempo": [],
        "exames": None,
        "medicacoes": [],
        "comorbidades": [],
        "contraindicacoes": [],
        "trechos_nao_classificados": [],
    }


@pytest.fixture(autouse=True)
def _seeded_prompts() -> None:
    """Seed idempotente dos 28 prompts (a montagem do caso exige os ativos)."""
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture
def owner_user() -> User:
    """Dono (criador) dos casos dos testes — sem papel (a FSM não exige)."""
    return User.objects.create_user(username="dono-pipeline", password="senha-teste")


def _make_llm_extracting_case(
    *,
    owner: User,
    declared: Sequence[str],
    anonymized_text: str = _ANONYMIZED_TEXT,
    pseudonyms: dict[str, dict[str, str]] | None = None,
) -> Case:
    """Caso em LLM_EXTRACTING com rows declaradas e artefatos de anonimização.

    O caminho FSM é o real (NEW → PDF_EXTRACTING → ANONYMIZING →
    LLM_EXTRACTING); a anonimização em si não roda — os artefatos são
    gravados direto (o serviço LLM1 não transiciona nem anonimiza).
    """
    case = Case.objects.create(created_by=owner)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    for procedure_type in declared:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    case.anonymized_text = anonymized_text
    case.pseudonym_map = dict(pseudonyms) if pseudonyms is not None else dict(_PSEUDONYMS)
    case.save()
    assert case.status == CaseStatus.LLM_EXTRACTING
    return case


def _run(case: Case, fake: ScriptedLlmClient) -> Llm1ExtractionResult:
    """Executa a extração com o fake injetado e o modelo do teste configurado."""
    with override_settings(LLM_CLIENT_FACTORY=lambda: fake, LLM1_MODEL=FAKE_MODEL):
        return run_llm1_extraction(case, user=None, role=SYSTEM_ROLE)


def _event_types(case: Case) -> list[str]:
    return list(case.events.order_by("id").values_list("event_type", flat=True))


# ── R2: extração feliz persiste artefato de tokens + evento ────────────────


@pytest.mark.django_db
def test_happy_persists_tokens_only(owner_user: User) -> None:
    """R2/R8: resposta válida (fake) → artefato persistido só com tokens +
    evento ``CASE_LLM1_COMPLETED`` enxuto; sem retenção; row detectada."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif", "cat_cardiaco"))
    expected = _artifact(["art_perif", "cat_cardiaco"])
    fake = ScriptedLlmClient([json.dumps(expected)])
    events_before = case.events.count()

    result = _run(case, fake)
    case.refresh_from_db()

    assert result.has_divergence is False
    assert result.retry_used is False
    assert case.structured_data == expected
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is False
    assert case.manual_review_reason == ""
    # Assert de tokens (R2/D4): nenhum valor do pseudonym_map aparece no
    # artefato serializado — o que aparece são os tokens (<PESSOA_1>, ...).
    serialized = json.dumps(case.structured_data, ensure_ascii=False, sort_keys=True)
    for entry in case.pseudonym_map.values():
        assert entry["value"] not in serialized
    # Rows de detecção atualizadas para as duas declarações.
    for procedure_type in ("art_perif", "cat_cardiaco"):
        row = case.procedures.get(procedure_type=procedure_type)
        assert row.declared_by_nir is True
        assert row.detection_status == DetectionStatus.DETECTED

    assert case.events.count() == events_before + 2
    completed = case.events.get(event_type=CaseEventType.CASE_LLM1_COMPLETED)
    assert completed.actor is None
    assert completed.actor_type == ActorType.SYSTEM
    assert completed.actor_role == SYSTEM_ROLE
    assert completed.payload["declared_types"] == ["art_perif", "cat_cardiaco"]
    assert completed.payload["retry_used"] is False
    assert completed.payload["prompts"]["llm1.system"] == 1
    assert completed.payload["prompts"]["proc.art_perif.llm1.user"] == 1
    assert completed.payload["prompts"]["proc.cat_cardiaco.llm1.user"] == 1
    assert CaseEventType.CASE_PROCEDURES_DETECTED.value in _event_types(case)

    # Contrato da chamada: modelo configurado + schema normalizado + texto
    # anonimizado substituído no user (str.replace, nunca str.format).
    model, messages, json_schema = fake.calls[0]
    assert model == FAKE_MODEL
    assert json_schema is not None
    assert json_schema["name"] == "Llm1CaseArtifact"
    root_schema = json_schema["schema"]
    assert isinstance(root_schema, dict)
    assert root_schema.get("additionalProperties") is False
    user_content = next(message["content"] for message in messages if message["role"] == "user")
    assert _ANONYMIZED_TEXT in user_content
    assert "{texto_anonimizado}" not in user_content


@pytest.mark.django_db
def test_token_leak_raises_and_persists_nothing(owner_user: User) -> None:
    """R2 (fail-closed): artefato com valor real do mapa → erro tipado e nada
    persistido (nem structured_data nem eventos)."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif",))
    leaked = _artifact(["art_perif"])
    leaked["contexto_clinico"] = f"Paciente {_PSEUDONYMS['<PESSOA_1>']['value']} internado."
    fake = ScriptedLlmClient([json.dumps(leaked)])
    events_before = case.events.count()

    with pytest.raises(LlmPipelineError) as excinfo:
        _run(case, fake)

    assert excinfo.value.reason == "llm1_token_leak"
    case.refresh_from_db()
    assert case.structured_data == {}
    assert case.events.count() == events_before
    assert case.status == CaseStatus.LLM_EXTRACTING


# ── R2: guarda de schema — retry único e fail-closed ───────────────────────


@pytest.mark.django_db
def test_invalid_then_retry_then_failed(owner_user: User) -> None:
    """R2/R8: inválida → retry corretivo → inválida → ``LlmPipelineError``
    ``llm1_schema``; o orquestrador (006) é quem chama ``fail_processing``
    com o motivo — nada de artefato parcial é persistido."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif",))
    fake = ScriptedLlmClient(
        [
            "isto nao e um JSON valido {{",
            # Schema-válido? Não: pedido é obrigatório e está ausente.
            json.dumps({"contexto_clinico": "falta o pedido obrigatorio"}),
        ]
    )
    events_before = case.events.count()

    with pytest.raises(LlmPipelineError) as excinfo:
        _run(case, fake)

    assert excinfo.value.reason == "llm1_schema"
    assert len(fake.calls) == 2
    case.refresh_from_db()
    assert case.structured_data == {}
    assert case.events.count() == events_before
    assert case.status == CaseStatus.LLM_EXTRACTING

    # fail-closed (papel do orquestrador, design D4): o motivo tipado da
    # guarda é o que entra no evento de falha.
    case.fail_processing(reason=excinfo.value.reason, user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    failed = case.events.get(event_type=CaseEventType.CASE_STATUS_FAILED)
    assert failed.payload["reason"] == "llm1_schema"


# ── R2: language guard pt-BR ───────────────────────────────────────────────


@pytest.mark.django_db
def test_language_guard_rejects_english(owner_user: User) -> None:
    """R2/R8: resposta schema-válida em inglês → retry corretivo de idioma →
    resposta ainda em inglês → ``LlmPipelineError`` ``llm1_language``; a
    resposta inválida anterior NÃO é reenviada no payload de retry."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif",))
    english = json.dumps(_artifact(["art_perif"], english_context=True))
    fake = ScriptedLlmClient([english, english])
    events_before = case.events.count()

    with pytest.raises(LlmPipelineError) as excinfo:
        _run(case, fake)

    assert excinfo.value.reason == "llm1_language"
    assert len(fake.calls) == 2
    # A 2ª chamada traz apenas a instrução corretiva — sem o texto inglês.
    _model, retry_messages, _json_schema = fake.calls[1]
    retry_user = next(message["content"] for message in retry_messages if message["role"] == "user")
    assert _ENGLISH_ARTIFACT_OVERRIDE not in retry_user
    assert "inglês" in retry_user
    assert "português" in retry_user

    case.refresh_from_db()
    assert case.structured_data == {}
    assert case.events.count() == events_before
    assert case.status == CaseStatus.LLM_EXTRACTING

    case.fail_processing(reason=excinfo.value.reason, user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    failed = case.events.get(event_type=CaseEventType.CASE_STATUS_FAILED)
    assert failed.payload["reason"] == "llm1_language"


@pytest.mark.django_db
def test_retry_succeeds(owner_user: User) -> None:
    """R2/R8: 1ª resposta em inglês → retry corretivo → 2ª em pt-BR → sucesso
    (artefato persistido, evento único, ``retry_used=True`` e sem retenção)."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif",))
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(["art_perif"], english_context=True)),
            json.dumps(_artifact(["art_perif"])),
        ]
    )

    result = _run(case, fake)
    case.refresh_from_db()

    assert result.retry_used is True
    assert result.has_divergence is False
    assert case.structured_data == _artifact(["art_perif"])
    assert case.manual_review_required is False
    assert case.events.filter(event_type=CaseEventType.CASE_LLM1_COMPLETED).count() == 1
    completed = case.events.get(event_type=CaseEventType.CASE_LLM1_COMPLETED)
    assert completed.payload["retry_used"] is True
    # Só 2 chamadas: a original + o retry corretivo único.
    assert len(fake.calls) == 2


# ── R4: record_detected_procedures (upsert) ────────────────────────────────


@pytest.mark.django_db
def test_upsert_creates_undeclared_rows(owner_user: User) -> None:
    """R4/R8: detecção de tipo sem row cria a row (``declared_by_nir=False``)
    e atualiza o ``detection_status`` das existentes — atomicamente, com o
    evento ``CASE_PROCEDURES_DETECTED`` na ordem canônica."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif", "filtro_cava"))
    events_before = case.events.count()

    record_detected_procedures(
        case,
        {
            "art_perif": DetectionStatus.DETECTED.value,
            "filtro_cava": DetectionStatus.NOT_DETECTED.value,
            "nefrostomia": DetectionStatus.DETECTED.value,
        },
        user=None,
        role=SYSTEM_ROLE,
    )
    case.refresh_from_db()

    declared_row = case.procedures.get(procedure_type="art_perif")
    assert declared_row.detection_status == DetectionStatus.DETECTED
    missing_row = case.procedures.get(procedure_type="filtro_cava")
    assert missing_row.detection_status == DetectionStatus.NOT_DETECTED
    created_row = case.procedures.get(procedure_type="nefrostomia")
    assert created_row.declared_by_nir is False
    assert created_row.detection_status == DetectionStatus.DETECTED

    assert case.events.count() == events_before + 1
    event = case.events.get(event_type=CaseEventType.CASE_PROCEDURES_DETECTED)
    assert list(event.payload["detection"]) == ["art_perif", "filtro_cava", "nefrostomia"]


# ── R5: divergência retém; coincidência atualiza rows ──────────────────────


@pytest.mark.django_db
def test_divergence_retains(owner_user: User) -> None:
    """R5/R8: detectado não-declarado (nefrostomia) → caso permanece em
    LLM_EXTRACTING com flag ``procedure_divergence`` + evento
    ``CASE_GATE_PROCEDURE_DIVERGENCE`` (classificação por tipo) e SEM
    transição de saída."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif",))
    fake = ScriptedLlmClient([json.dumps(_artifact(["nefrostomia"]))])

    result = _run(case, fake)
    case.refresh_from_db()

    assert result.has_divergence is True
    assert result.detected_types == ("nefrostomia",)
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == "procedure_divergence"
    # Rows: declarado sem detecção → not_detected; detectado não-declarado → row nova.
    declared_row = case.procedures.get(procedure_type="art_perif")
    assert declared_row.declared_by_nir is True
    assert declared_row.detection_status == DetectionStatus.NOT_DETECTED
    detected_row = case.procedures.get(procedure_type="nefrostomia")
    assert detected_row.declared_by_nir is False
    assert detected_row.detection_status == DetectionStatus.DETECTED

    event_types = _event_types(case)
    assert event_types == [
        CaseEventType.CASE_STATUS_PDF_EXTRACTING.value,
        CaseEventType.CASE_STATUS_ANONYMIZING.value,
        CaseEventType.CASE_STATUS_LLM_EXTRACTING.value,
        CaseEventType.CASE_LLM1_COMPLETED.value,
        CaseEventType.CASE_PROCEDURES_DETECTED.value,
        CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE.value,
    ]
    divergence = case.events.get(event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE)
    assert divergence.payload["classification"] == {
        "art_perif": "not_detected",
        "nefrostomia": "missing_declaration",
    }
    # Sem transição de saída: nenhum evento de avanço para sumarização.
    assert CaseEventType.CASE_STATUS_LLM_SUMMARIZING.value not in event_types


@pytest.mark.django_db
def test_match_updates_rows(owner_user: User) -> None:
    """R5/R8: declaração e detecção coincidem → rows atualizadas sem retenção
    e o caller pode avançar com ``complete_llm_extraction``."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif", "cat_cardiaco"))
    fake = ScriptedLlmClient([json.dumps(_artifact(["cat_cardiaco", "art_perif"]))])

    result = _run(case, fake)
    case.refresh_from_db()

    assert result.has_divergence is False
    assert result.detected_types == ("art_perif", "cat_cardiaco")
    assert case.manual_review_required is False
    for procedure_type in ("art_perif", "cat_cardiaco"):
        row = case.procedures.get(procedure_type=procedure_type)
        assert row.detection_status == DetectionStatus.DETECTED
    assert CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE.value not in _event_types(case)

    # Sem divergência → o caller (orquestrador/worker) avança a sumarização.
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_SUMMARIZING


# ── R6: transição bypass_pipeline_divergence ───────────────────────────────


@pytest.mark.django_db
def test_bypass_transition(owner_user: User) -> None:
    """R6/R8: bypass de LLM_EXTRACTING → LLM_SUMMARIZING com os dois eventos
    no mesmo atomic (transição + ``CASE_GATE_BYPASSED`` com o motivo) e o
    conjunto declarado preservado."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif",))
    case.manual_review_required = True
    case.manual_review_reason = "procedure_divergence"
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    events_before = case.events.count()

    case.bypass_pipeline_divergence(user=owner_user, role="nir")
    case.refresh_from_db()

    assert case.status == CaseStatus.LLM_SUMMARIZING
    assert case.events.count() == events_before + 2
    event_types = _event_types(case)
    assert event_types[-2:] == [
        CaseEventType.CASE_STATUS_LLM_SUMMARIZING.value,
        CaseEventType.CASE_GATE_BYPASSED.value,
    ]
    transition = case.events.get(event_type=CaseEventType.CASE_STATUS_LLM_SUMMARIZING)
    assert transition.actor == owner_user
    assert transition.actor_role == "nir"
    assert transition.payload == {"source": "LLM_EXTRACTING", "target": "LLM_SUMMARIZING"}
    bypass = case.events.get(event_type=CaseEventType.CASE_GATE_BYPASSED)
    assert bypass.actor == owner_user
    assert bypass.actor_role == "nir"
    assert bypass.payload == {"reason": "procedure_divergence"}
    # Conjunto declarado preservado (o bypass não mexe nas rows).
    row = case.procedures.get(procedure_type="art_perif")
    assert row.declared_by_nir is True


@pytest.mark.django_db
def test_bypass_wrong_state_raises(owner_user: User) -> None:
    """R6/R8: bypass de estado fora de LLM_EXTRACTING → TransitionNotAllowed
    sem efeito (status e trilha intactos)."""
    case = Case.objects.create(created_by=owner_user)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.ANONYMIZING
    events_before = case.events.count()

    with pytest.raises(TransitionNotAllowed):
        case.bypass_pipeline_divergence(user=None, role=SYSTEM_ROLE)

    case.refresh_from_db()
    assert case.status == CaseStatus.ANONYMIZING
    assert case.events.count() == events_before
