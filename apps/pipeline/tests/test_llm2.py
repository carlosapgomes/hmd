"""Testes do serviço LLM2 (slice 006, R1/R8, design D8).

Cobre R1: sumarização numa única chamada com visão filtrada (cópia efêmera do
artefato LLM1 pelos tipos declarados/reconciliados) + policy determinística
prevalecendo (policy recusa → sugestão final recusa com os motivos da policy,
o LLM não suaviza) + agregado do caso = qualquer procedimento recusado ⇒
agregado recusa com motivos somados (definição HMD; ``strictest_global_support``
do ats-web não se aplica); assert de tokens RECURSIVO do payload serializado
contra os mapas de pseudônimos do caso E dos casos prévios; validação strict
(schema Llm2) + language guard + retry único (regime do 004); falha → levanta
``LlmPipelineError("llm2_<motivo>")`` sem persistir (o orquestrador é o dono
do ``fail_processing``). Sucesso persiste ``summary_text`` +
``suggested_action`` + evento enxuto ``CASE_LLM2_COMPLETED``.

O cliente é sempre um fake via ``LLM_CLIENT_FACTORY`` (override_settings) — a
suíte nunca toca rede. Os prompts do seed são criados pelo fixture autouse
``seed_prompts`` (como nos testes do slice 003/004). Prior-case com motivo
com PII usa o stub de ``anonymize_text`` do módulo de prior_case (o núcleo do
change 05 é coberto pela própria suíte da anonimização).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseProcedure, CaseStatus, DoctorDisposition
from apps.pipeline import prior_case as prior_case_module
from apps.pipeline.llm import LlmPipelineError
from apps.pipeline.llm2_service import Llm2SummarizationResult, run_llm2_summarization

FAKE_MODEL = "modelo-llm2-teste"
SYSTEM_ROLE = "system"

_ANONYMIZED_TEXT = (
    "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente. "
    "Solicitada avaliacao hemodinamica."
)
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "JOSE MARIA DE SOUZA", "entity_type": "PERSON"},
    "<DATA_1>": {"value": "05/03/1972", "entity_type": "DATE_TIME"},
}

# Artefato LLM1 (só tokens) para os tipos declarados dos testes.
_STRUCTURED_DATA: dict[str, object] = {
    "pedido": {
        "procedimentos_solicitados": ["art_perif", "filtro_cava"],
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

# policy_result serializado no formato do slice 005 (dict por procedimento).
_POLICY_ACCEPT_ALL: dict[str, dict[str, object]] = {
    "art_perif": {
        "procedure_type": "art_perif",
        "section_id": "S1",
        "recommendation": "recomenda_aceitar",
        "refusal_reasons": [],
        "criteria": [],
    },
    "filtro_cava": {
        "procedure_type": "filtro_cava",
        "section_id": "S6",
        "recommendation": "recomenda_aceitar",
        "refusal_reasons": [],
        "criteria": [],
    },
}

_POLICY_REFUSES_ART_PERIF: dict[str, dict[str, object]] = {
    "art_perif": {
        "procedure_type": "art_perif",
        "section_id": "S1",
        "recommendation": "recomenda_recusar",
        "refusal_reasons": [
            "plaquetas 80000 abaixo do mínimo 100000 da seção S1",
            "INR 2.1 igual ou acima do limite (< 1.5) da seção S1",
        ],
        "criteria": [],
    },
    "filtro_cava": {
        "procedure_type": "filtro_cava",
        "section_id": "S6",
        "recommendation": "recomenda_aceitar",
        "refusal_reasons": [],
        "criteria": [],
    },
}


class ScriptedLlmClient:
    """Cliente fake com roteiro de respostas (uma string por chamada)."""

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


def _llm2_response(*, summary: str = "Sumario clinico em portugues.") -> dict[str, object]:
    """Resposta LLM2 schema-válida: aceitar ambos os procedimentos."""
    return {
        "summary_text": summary,
        "procedures": [
            {
                "procedure_type": "art_perif",
                "suggestion": "aceitar",
                "motivos": ["sem contraindicoes identificadas"],
                "evidence_spans": [],
            },
            {
                "procedure_type": "filtro_cava",
                "suggestion": "aceitar",
                "motivos": ["sem contraindicoes identificadas"],
                "evidence_spans": [],
            },
        ],
    }


@pytest.fixture(autouse=True)
def _seeded_prompts() -> None:
    """Seed idempotente dos 28 prompts (a montagem do caso exige os ativos)."""
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture
def owner_user() -> User:
    """Dono (criador) dos casos dos testes."""
    return User.objects.create_user(username="dono-llm2", password="senha-teste")


def _make_llm_summarizing_case(
    *,
    owner: User,
    policy: dict[str, dict[str, object]] | None = None,
    structured: dict[str, object] | None = None,
    anonymized_text: str = _ANONYMIZED_TEXT,
    pseudonyms: dict[str, dict[str, str]] | None = None,
    agency_record_number: str = "",
) -> Case:
    """Caso em LLM_SUMMARIZING com artefatos de LLM1 + policy persistidos.

    O caminho FSM é o real (NEW → … → LLM_SUMMARIZING via
    ``complete_llm_extraction``); a extração em si não roda — os artefatos são
    gravados direto (o serviço LLM2 não extrai nem transiciona).
    """
    case = Case.objects.create(created_by=owner)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    for procedure_type in ("art_perif", "filtro_cava"):
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.anonymized_text = anonymized_text
    case.pseudonym_map = dict(pseudonyms) if pseudonyms is not None else dict(_PSEUDONYMS)
    case.structured_data = structured if structured is not None else dict(_STRUCTURED_DATA)
    case.policy_result = policy if policy is not None else dict(_POLICY_ACCEPT_ALL)
    case.agency_record_number = agency_record_number
    case.save()
    assert case.status == CaseStatus.LLM_SUMMARIZING
    return case


def _run(
    case: Case,
    fake: ScriptedLlmClient,
    *,
    user: User | None = None,
    role: str = SYSTEM_ROLE,
) -> Llm2SummarizationResult:
    """Executa a sumarização com o fake injetado e o modelo do teste configurado."""
    with override_settings(LLM_CLIENT_FACTORY=lambda: fake, LLM2_MODEL=FAKE_MODEL):
        return run_llm2_summarization(case, user=user, role=role)


def _make_prior_decided_case(
    owner: User,
    *,
    agency_record_number: str,
    patient_name: str,
    reason: str,
) -> Case:
    """Caso prévio DECIDIDO (DENIED) do mesmo nº de ocorrência com mapa próprio.

    A decisão usa ``created_at`` padrão (agora) e cai dentro da janela de 7
    dias do lookup do caso atual.
    """
    from datetime import timedelta

    from django.utils import timezone

    prior = Case.objects.create(created_by=owner)
    prior.agency_record_number = agency_record_number
    prior.patient_name = patient_name
    prior.save(update_fields=["agency_record_number", "patient_name"])
    prior.pseudonym_map = {
        "<PESSOA_1>": {"value": "MARIA APARECIDA DA SILVA", "entity_type": "PERSON"}
    }
    prior.save(update_fields=["pseudonym_map"])
    row = CaseProcedure.objects.create(case=prior, procedure_type="art_perif")
    row.doctor_disposition = DoctorDisposition.DENIED
    row.doctor_reason = reason
    row.doctor_decided_at = timezone.now() - timedelta(days=1)
    row.save(update_fields=["doctor_disposition", "doctor_reason", "doctor_decided_at"])
    return prior


# ── R1/R8: sumarização feliz persiste artefatos + evento ───────────────────


@pytest.mark.django_db
def test_happy_persists_summary_and_action(owner_user: User) -> None:
    """R1/R8: resposta válida (fake) → ``summary_text`` + ``suggested_action``
    persistidos + evento ``CASE_LLM2_COMPLETED`` enxuto; sem retry; a visão
    enviada é a cópia filtrada pelos tipos declarados."""
    case = _make_llm_summarizing_case(owner=owner_user)
    expected = _llm2_response()
    fake = ScriptedLlmClient([json.dumps(expected)])
    events_before = case.events.count()

    result = _run(case, fake)
    case.refresh_from_db()

    assert result.retry_used is False
    assert result.suggestions == {"art_perif": "aceitar", "filtro_cava": "aceitar"}
    assert result.aggregate == "aceitar"
    assert result.aggregate_reasons == []
    assert case.summary_text == expected["summary_text"]
    assert case.suggested_action["aggregate"]["suggestion"] == "aceitar"
    assert case.suggested_action["procedures"]["art_perif"]["suggestion"] == "aceitar"
    assert case.status == CaseStatus.LLM_SUMMARIZING

    assert case.events.count() == events_before + 1
    completed = case.events.get(event_type=CaseEventType.CASE_LLM2_COMPLETED)
    assert completed.actor is None
    assert completed.actor_type == ActorType.SYSTEM
    assert completed.actor_role == SYSTEM_ROLE
    assert completed.payload["suggestions"] == {
        "art_perif": "aceitar",
        "filtro_cava": "aceitar",
    }
    assert completed.payload["aggregate"] == "aceitar"
    assert completed.payload["retry_used"] is False
    assert completed.payload["prompts"]["llm2.system"] == 1
    assert completed.payload["prompts"]["proc.art_perif.llm2.user"] == 1

    # Contrato da chamada: modelo configurado, schema strict do Llm2 e os 4
    # placeholders substituídos por str.replace (nunca str.format).
    model, messages, json_schema = fake.calls[0]
    assert model == FAKE_MODEL
    assert json_schema is not None
    assert json_schema["name"] == "Llm2Response"
    root_schema = json_schema["schema"]
    assert isinstance(root_schema, dict)
    assert root_schema.get("additionalProperties") is False
    user_content = next(message["content"] for message in messages if message["role"] == "user")
    assert _ANONYMIZED_TEXT in user_content
    assert "{texto_anonimizado}" not in user_content
    assert "{visao_estruturada}" not in user_content
    assert "{policy}" not in user_content
    assert "{prior_case}" not in user_content
    # Visão filtrada: os procedimentos do pedido na visão são os declarados.
    assert '"filtro_cava"' in user_content and '"art_perif"' in user_content
    # O artefato persistido não foi mutado pela cópia efêmera.
    assert case.structured_data == _STRUCTURED_DATA


# ── R1: policy determinística prevalece (o LLM não suaviza) ────────────────


@pytest.mark.django_db
def test_policy_precedence_refuses_despite_llm_accept(owner_user: User) -> None:
    """R1/R8: policy recusa art_perif mas o LLM sugere aceitar → sugestão final
    recusa com os motivos da policy; o agregado do caso também recusa."""
    case = _make_llm_summarizing_case(owner=owner_user, policy=_POLICY_REFUSES_ART_PERIF)
    fake = ScriptedLlmClient([json.dumps(_llm2_response())])

    result = _run(case, fake)
    case.refresh_from_db()

    assert result.suggestions == {"art_perif": "recusar", "filtro_cava": "aceitar"}
    assert result.aggregate == "recusar"
    assert result.aggregate_reasons == _POLICY_REFUSES_ART_PERIF["art_perif"]["refusal_reasons"]
    suggestions = case.suggested_action["procedures"]
    assert suggestions["art_perif"]["suggestion"] == "recusar"
    assert (
        suggestions["art_perif"]["motivos"]
        == _POLICY_REFUSES_ART_PERIF["art_perif"]["refusal_reasons"]
    )
    assert suggestions["filtro_cava"]["suggestion"] == "aceitar"
    assert case.suggested_action["aggregate"] == {
        "suggestion": "recusar",
        "motivos": _POLICY_REFUSES_ART_PERIF["art_perif"]["refusal_reasons"],
    }


@pytest.mark.django_db
def test_llm_refusal_without_policy_is_preserved(owner_user: User) -> None:
    """R1/R8: LLM sugere recusar num procedimento que a policy aceita → a recusa
    do LLM é preservada (com seus motivos) e entra no agregado (strictest)."""
    case = _make_llm_summarizing_case(owner=owner_user)
    response = _llm2_response()
    response["procedures"] = [
        {
            "procedure_type": "art_perif",
            "suggestion": "aceitar",
            "motivos": ["sem contraindicoes"],
            "evidence_spans": [],
        },
        {
            "procedure_type": "filtro_cava",
            "suggestion": "recusar",
            "motivos": ["antecedente de reacao ao contraste"],
            "evidence_spans": [],
        },
    ]
    fake = ScriptedLlmClient([json.dumps(response)])

    result = _run(case, fake)
    case.refresh_from_db()

    assert result.suggestions == {"art_perif": "aceitar", "filtro_cava": "recusar"}
    assert result.aggregate == "recusar"
    assert result.aggregate_reasons == ["antecedente de reacao ao contraste"]
    assert case.suggested_action["procedures"]["filtro_cava"]["motivos"] == [
        "antecedente de reacao ao contraste"
    ]


# ── R1: fail-closed ────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_invalid_then_retry_then_failed(owner_user: User) -> None:
    """R1/R8: inválida → retry corretivo → inválida → ``LlmPipelineError``
    ``llm2_schema``; nada é persistido (o orquestrador chama o fail_processing)."""
    case = _make_llm_summarizing_case(owner=owner_user)
    fake = ScriptedLlmClient(
        [
            "isto nao e um JSON valido {{",
            json.dumps({"summary_text": "sem procedures"}),
        ]
    )
    events_before = case.events.count()

    with pytest.raises(LlmPipelineError) as excinfo:
        _run(case, fake)

    assert excinfo.value.reason == "llm2_schema"
    assert len(fake.calls) == 2
    case.refresh_from_db()
    assert case.summary_text == ""
    assert case.suggested_action == {}
    assert case.events.count() == events_before
    assert case.status == CaseStatus.LLM_SUMMARIZING


@pytest.mark.django_db
def test_response_set_mismatch_fails_closed(owner_user: User) -> None:
    """R1: resposta schema-válida que omite/adiciona procedimento do conjunto
    declarado → falha de schema (a resposta não cobre exatamente o conjunto)."""
    case = _make_llm_summarizing_case(owner=owner_user)
    response = {
        "summary_text": "Sumario em portugues.",
        "procedures": [
            {
                "procedure_type": "art_perif",
                "suggestion": "aceitar",
                "motivos": [],
                "evidence_spans": [],
            }
        ],
    }
    fake = ScriptedLlmClient([json.dumps(response), json.dumps(response)])

    with pytest.raises(LlmPipelineError) as excinfo:
        _run(case, fake)

    assert excinfo.value.reason == "llm2_schema"
    case.refresh_from_db()
    assert case.summary_text == ""
    assert case.suggested_action == {}


@pytest.mark.django_db
def test_language_guard_rejects_english(owner_user: User) -> None:
    """R1/R8: resposta schema-válida com narrativa em inglês → retry de idioma →
    resposta ainda em inglês → ``LlmPipelineError`` ``llm2_language``."""
    case = _make_llm_summarizing_case(owner=owner_user)
    english = _llm2_response(summary="Patient reports claudication; summary: none.")
    fake = ScriptedLlmClient([json.dumps(english), json.dumps(english)])

    with pytest.raises(LlmPipelineError) as excinfo:
        _run(case, fake)

    assert excinfo.value.reason == "llm2_language"
    assert len(fake.calls) == 2
    _model, retry_messages, _json_schema = fake.calls[1]
    retry_user = next(message["content"] for message in retry_messages if message["role"] == "user")
    assert "Patient" not in retry_user
    assert "português" in retry_user
    case.refresh_from_db()
    assert case.summary_text == ""
    assert case.suggested_action == {}


# ── R1/R8: assert de tokens recursivo do payload ───────────────────────────


@pytest.mark.django_db
def test_payload_rejects_real_values_from_case_map(owner_user: User) -> None:
    """R1/R8: valor real do pseudonym_map do caso no artefato → payload
    recusado com ``llm2_token_leak`` e nada persistido."""
    case = _make_llm_summarizing_case(owner=owner_user)
    leaked = dict(_STRUCTURED_DATA)
    leaked["contexto_clinico"] = f"Paciente {_PSEUDONYMS['<PESSOA_1>']['value']} internado."
    case.structured_data = leaked
    case.save(update_fields=["structured_data"])
    fake = ScriptedLlmClient([json.dumps(_llm2_response())])
    events_before = case.events.count()

    with pytest.raises(LlmPipelineError) as excinfo:
        _run(case, fake)

    assert excinfo.value.reason == "llm2_token_leak"
    case.refresh_from_db()
    assert case.summary_text == ""
    assert case.suggested_action == {}
    assert case.events.count() == events_before


@pytest.mark.django_db
def test_payload_rejects_real_values_from_prior_case_map(
    owner_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1/R8: o motivo do caso prévio entra pré-anonimizado (slice 005); se um
    valor real do mapa do caso prévio aparecer no payload (motivo anonimizado
    vazou), o assert recursivo recusa com ``llm2_token_leak``."""
    # Stub do núcleo no módulo de prior-case: devolve o texto SEM anonimizar
    # (pior caso — o motivo real vaza para o payload).
    real_value = "MARIA APARECIDA DA SILVA"

    @dataclass(frozen=True)
    class _IdentityResult:
        anonymized_text: str

    monkeypatch.setattr(
        prior_case_module,
        "anonymize_text",
        lambda text: _IdentityResult(anonymized_text=text),
    )
    current = _make_llm_summarizing_case(
        owner=owner_user,
        agency_record_number="33345",
    )
    _make_prior_decided_case(
        owner_user,
        agency_record_number="33345",
        patient_name="Maria Aparecida da Silva",
        reason=f"Paciente {real_value} com contraindicação prévia.",
    )
    # A resposta do LLM2 cita o motivo do prévio (contém o valor real).
    response = _llm2_response()
    response["procedures"] = [
        {
            "procedure_type": "art_perif",
            "suggestion": "recusar",
            "motivos": [f"Paciente {real_value} recusado no caso anterior"],
            "evidence_spans": [],
        },
        {
            "procedure_type": "filtro_cava",
            "suggestion": "aceitar",
            "motivos": [],
            "evidence_spans": [],
        },
    ]
    fake = ScriptedLlmClient([json.dumps(response)])

    with pytest.raises(LlmPipelineError) as excinfo:
        _run(current, fake)

    assert excinfo.value.reason == "llm2_token_leak"
    current.refresh_from_db()
    assert current.summary_text == ""
    assert current.suggested_action == {}
