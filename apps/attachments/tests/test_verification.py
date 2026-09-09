"""Testes do slice 003 (change attachment-processing-ocr): verificação LLM.

Cobre R2 (``patient_token`` — reverse lookup no mapa do caso; sem
paciente/token → verificação roda em modo sem-comparação e o resultado é
``unknown``), R4 (``verify_attachment`` — mensagens do seed
ATTACHMENT_VERIFICATION com texto anonimizado + token do paciente, chamada
``complete(LLM1_MODEL, messages, json_schema strict)``, parse tolerante,
persistência de match/mismatch + status ``processed`` + evento
``CASE_ATTACHMENT_PROCESSED`` no MESMO atomic; falha LLM/parse → ``failed`` +
``CASE_ATTACHMENT_FAILED``, sem efeito no caso) e R6 (invariante
LLM-só-vê-tokens: assert recursivo do ``messages`` capturado pelo fake — nada
de valor real dos mapas do anexo/caso; o token do paciente aparece).

O cliente é sempre um fake via ``LLM_CLIENT_FACTORY`` (override_settings) — a
suíte nunca toca rede. O seed dos prompts roda no fixture autouse
(``seed_prompts`` — 29 conteúdos), como nas suítes do change 06.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.accounts.models import User
from apps.attachments.models import AttachmentStatus, CaseAttachment, ExtractionMethod, PatientMatch
from apps.attachments.verification import patient_token, verify_attachment
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent, CaseStatus
from apps.pipeline.llm import LlmError

FAKE_MODEL = "modelo-verificacao-teste"
SYSTEM_ROLE = "system"

# Mapa do CASO: o paciente do caso é <PESSOA_2> (o médico assistente é o 1º
# PESSOA do relatório principal) — o anexo deve manter esse token.
_CASE_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "JOSE CARLOS DE OLIVEIRA", "entity_type": "PESSOA"},
    "<PESSOA_2>": {"value": "MARIA DA SILVA", "entity_type": "PESSOA"},
    "<CPF_1>": {"value": "111.222.333-44", "entity_type": "CPF"},
}

_ANONYMIZED_ATTACHMENT_TEXT = (
    "Eletrocardiograma ambulatorial do paciente <PESSOA_2>. "
    "Documento de referencia <CPF_1>. Ritmo sinusal, sem alteracoes."
)

_MATCH_RESPONSE = {
    "patient_match": PatientMatch.MATCH,
    "summary": "Eletrocardiograma ambulatorial do paciente do caso.",
    "evidence": "ECG de <PESSOA_2> com ritmo sinusal.",
}
_MISMATCH_RESPONSE = {
    "patient_match": PatientMatch.MISMATCH,
    "summary": "Documento de outro paciente.",
    "evidence": "Exame identificado com pessoa distinta do caso.",
}
_UNKNOWN_RESPONSE = {
    "patient_match": PatientMatch.UNKNOWN,
    "summary": "Documento sem paciente identificavel.",
    "evidence": "Nao ha nome/token de paciente no documento.",
}

_PROCESSED = CaseEventType.CASE_ATTACHMENT_PROCESSED.value
_FAILED = CaseEventType.CASE_ATTACHMENT_FAILED.value


class ScriptedLlmClient:
    """Cliente fake com roteiro de respostas (uma string por chamada).

    Registra ``(model, messages, json_schema)`` de cada chamada para os
    asserts de contrato do slice (modelo configurado, schema strict normalizado
    e mensagens sem valor real).
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses: list[str] = list(responses or [])
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


def _response(match: str) -> str:
    """Resposta JSON schema-válida com o patient_match dado."""
    if match == PatientMatch.MISMATCH:
        return json.dumps(_MISMATCH_RESPONSE, ensure_ascii=False)
    if match == PatientMatch.UNKNOWN:
        return json.dumps(_UNKNOWN_RESPONSE, ensure_ascii=False)
    return json.dumps(_MATCH_RESPONSE, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _seeded_prompts() -> None:
    """Seed idempotente dos 29 prompts (a verificação exige o ATTACHMENT_VERIFICATION)."""
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture
def owner_user() -> User:
    """Dono (criador) dos casos dos testes — sem papel (a verificação não exige)."""
    return User.objects.create_user(username="dono-verificacao", password="senha-teste")


def _make_case(*, owner: User, patient_name: str = "Maria da Silva") -> Case:
    """Caso com linkage e mapa do caso (como pós-anonimização do relatório)."""
    case = Case.objects.create(created_by=owner)
    case.patient_name = patient_name
    case.pseudonym_map = dict(_CASE_PSEUDONYMS)
    case.save(update_fields=["patient_name", "pseudonym_map"])
    return case


def _make_attachment(
    case: Case, *, owner: User, anonymized_text: str = _ANONYMIZED_ATTACHMENT_TEXT
) -> CaseAttachment:
    """Anexo com extração concluída aguardando a verificação (status processing)."""
    attachment = CaseAttachment.objects.create(
        case=case,
        content_type="application/pdf",
        original_filename="ecg-anexo.pdf",
        size_bytes=1024,
        uploaded_by=owner,
        status=AttachmentStatus.PROCESSING,
        extraction_method=ExtractionMethod.LOCAL_PDF,
        extracted_text="texto bruto já extraído (não usado nesta suíte)",
        anonymized_text=anonymized_text,
    )
    attachment.pseudonym_map = {
        "<PESSOA_2>": {"value": "MARIA DA SILVA", "entity_type": "PESSOA"},
        "<CPF_1>": {"value": "111.222.333-44", "entity_type": "CPF"},
    }
    attachment.save(update_fields=["pseudonym_map"])
    return attachment


def _captured_messages(fake: ScriptedLlmClient) -> str:
    """Serializa todas as mensagens capturadas (base do assert recursivo)."""
    assert len(fake.calls) == 1
    _model, messages, _json_schema = fake.calls[0]
    return json.dumps(messages, ensure_ascii=False, sort_keys=True)


def _run(case: Case, attachment: CaseAttachment, fake: ScriptedLlmClient) -> None:
    """Executa a verificação com o fake injetado e o modelo do teste."""
    with override_settings(LLM_CLIENT_FACTORY=lambda: fake, LLM1_MODEL=FAKE_MODEL):
        verify_attachment(case, attachment)


def _events_of_type(case: Case, event_type: str) -> list[CaseEvent]:
    return list(case.events.filter(event_type=event_type).order_by("id"))


# ── R2: patient_token (reverse lookup no mapa do caso) ─────────────────────


@pytest.mark.django_db
def test_patient_token_lookup(owner_user: User) -> None:
    """R2: token do caso cujo valor real == patient_name (casefold)."""
    case = _make_case(owner=owner_user, patient_name="maria da silva")
    assert patient_token(case) == "<PESSOA_2>"


@pytest.mark.django_db
def test_patient_token_missing_returns_none(owner_user: User) -> None:
    """R2: paciente fora do mapa do caso → ``None`` (sem token)."""
    case = _make_case(owner=owner_user, patient_name="Outra Pessoa Qualquer")
    assert patient_token(case) is None


@pytest.mark.django_db
def test_patient_token_empty_patient_name_returns_none(owner_user: User) -> None:
    """R2: caso sem patient_name → ``None``."""
    case = _make_case(owner=owner_user)
    case.patient_name = ""
    case.save(update_fields=["patient_name"])
    assert patient_token(case) is None


# ── R4: happy paths persistem resultado + evento no mesmo atomic ───────────


@pytest.mark.django_db
def test_verify_match_persists(owner_user: User) -> None:
    """R4/R6: resposta match (fake) → patient_match match + summary/evidence
    persistidos, status ``processed``, processed_at preenchido e evento
    ``CASE_ATTACHMENT_PROCESSED`` com payload match+método."""
    case = _make_case(owner=owner_user)
    attachment = _make_attachment(case, owner=owner_user)
    fake = ScriptedLlmClient([_response(PatientMatch.MATCH)])
    before = case.events.count()

    _run(case, attachment, fake)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.PROCESSED
    assert attachment.patient_match == PatientMatch.MATCH
    assert attachment.verification_summary == _MATCH_RESPONSE["summary"]
    assert attachment.verification_evidence == _MATCH_RESPONSE["evidence"]
    assert isinstance(attachment.processed_at, datetime)

    processed = _events_of_type(case, _PROCESSED)
    assert len(processed) == 1
    assert case.events.count() == before + 1
    assert processed[0].actor_type == ActorType.SYSTEM
    assert processed[0].actor_role == SYSTEM_ROLE
    assert processed[0].payload["patient_match"] == PatientMatch.MATCH
    assert processed[0].payload["method"] == ExtractionMethod.LOCAL_PDF


@pytest.mark.django_db
def test_verify_mismatch_persists(owner_user: User) -> None:
    """R4: resposta mismatch (fake) → persistido sem descartar nada (mismatch
    nunca remove anexo nem transiciona o caso)."""
    case = _make_case(owner=owner_user)
    attachment = _make_attachment(case, owner=owner_user)
    fake = ScriptedLlmClient([_response(PatientMatch.MISMATCH)])

    _run(case, attachment, fake)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.PROCESSED
    assert attachment.patient_match == PatientMatch.MISMATCH
    assert attachment.verification_summary == _MISMATCH_RESPONSE["summary"]
    case.refresh_from_db()
    assert case.status == CaseStatus.NEW


# ── R4: falha LLM/parse → failed + evento, sem efeito no caso ──────────────


class _FailingLlmClient:
    """Cliente fake que sempre levanta LlmError de transporte."""

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        del model, messages, json_schema
        raise LlmError("network", "Falha de conexão com a OpenRouter.")


@pytest.mark.django_db
def test_verify_llm_failure_failed(owner_user: User) -> None:
    """R4: LlmError de transporte → ``failed`` + failed_reason +
    ``CASE_ATTACHMENT_FAILED``; nada de processed/evento; caso intacto."""
    case = _make_case(owner=owner_user)
    attachment = _make_attachment(case, owner=owner_user)

    with override_settings(LLM_CLIENT_FACTORY=lambda: _FailingLlmClient(), LLM1_MODEL=FAKE_MODEL):
        verify_attachment(case, attachment)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.FAILED
    assert attachment.patient_match is None
    assert "Falha de conexão" in attachment.failed_reason
    failed = _events_of_type(case, _FAILED)
    assert len(failed) == 1
    assert failed[0].payload["filename"] == "ecg-anexo.pdf"
    assert _events_of_type(case, _PROCESSED) == []
    case.refresh_from_db()
    assert case.status == CaseStatus.NEW


@pytest.mark.django_db
def test_verify_json_parse_failure_failed(owner_user: User) -> None:
    """R4: resposta sem JSON válido → ``failed`` + ``CASE_ATTACHMENT_FAILED``."""
    case = _make_case(owner=owner_user)
    attachment = _make_attachment(case, owner=owner_user)
    fake = ScriptedLlmClient(["resposta sem objeto JSON"])

    _run(case, attachment, fake)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.FAILED
    assert attachment.patient_match is None
    assert _events_of_type(case, _FAILED) != []
    assert _events_of_type(case, _PROCESSED) == []


# ── R2/R6: sem token do paciente → modo sem-comparação (unknown) ───────────


@pytest.mark.django_db
def test_no_patient_token_yields_unknown(owner_user: User) -> None:
    """R2/R6: paciente fora do mapa do caso → a verificação roda em modo
    sem-comparação e o patient_match persistido é ``unknown`` MESMO que o
    modelo responda match (sem token, não há comparação possível)."""
    case = _make_case(owner=owner_user, patient_name="Paciente Fora Do Mapa")
    attachment = _make_attachment(case, owner=owner_user)
    fake = ScriptedLlmClient([_response(PatientMatch.MATCH)])

    _run(case, attachment, fake)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.PROCESSED
    assert attachment.patient_match == PatientMatch.UNKNOWN
    processed = _events_of_type(case, _PROCESSED)
    assert len(processed) == 1
    assert processed[0].payload["patient_match"] == PatientMatch.UNKNOWN


# ── R6: LLM só vê tokens (assert recursivo com dados reais) ────────────────


@pytest.mark.django_db
def test_llm_input_tokens_only(owner_user: User) -> None:
    """R6: o ``messages`` capturado (fake client) NÃO contém NENHUM valor real
    dos mapas do anexo/caso (assert recursivo) e contém o token do paciente."""
    case = _make_case(owner=owner_user)
    attachment = _make_attachment(case, owner=owner_user)
    fake = ScriptedLlmClient([_response(PatientMatch.MATCH)])

    _run(case, attachment, fake)

    serialized = _captured_messages(fake)
    assert "<PESSOA_2>" in serialized  # token do paciente do caso
    # P2 review F3: o texto anonimizado do anexo ESTÁ na mensagem — se o
    # placeholder {{attachment_text}} deixasse de ser substituído, os asserts
    # de ausência de valores reais continuariam passando (falso verde).
    assert "Eletrocardiograma ambulatorial" in serialized
    assert "Ritmo sinusal" in serialized
    assert "{{" not in serialized  # nenhum placeholder por substituir
    real_values = [
        str(entry["value"])
        for entry in list(_CASE_PSEUDONYMS.values()) + list(attachment.pseudonym_map.values())
    ]
    for real_value in real_values:
        assert real_value not in serialized
    # A chamada usa o modelo do estágio de texto configurado.
    model, _messages, json_schema = fake.calls[0]
    assert model == FAKE_MODEL
    assert json_schema is not None
    assert json_schema["name"] == "AttachmentVerificationResult"
    schema = json_schema["schema"]
    assert schema["type"] == "object"
    assert schema["properties"]["patient_match"]["enum"] == list(PatientMatch.values)


@pytest.mark.django_db
def test_llm_input_unknown_when_no_token(owner_user: User) -> None:
    """R6: sem token do paciente, o messages instrui o modo sem-comparação e o
    resultado persistido é ``unknown`` (nada de match sem comparação)."""
    case = _make_case(owner=owner_user, patient_name="Paciente Fora Do Mapa")
    attachment = _make_attachment(case, owner=owner_user)
    fake = ScriptedLlmClient([_response(PatientMatch.UNKNOWN)])

    _run(case, attachment, fake)

    _captured_messages(fake)
    attachment.refresh_from_db()
    assert attachment.patient_match == PatientMatch.UNKNOWN


@pytest.mark.django_db
def test_verify_schema_invalid_match_failed(owner_user: User) -> None:
    """R4 (P2 review F1): JSON válido com ``patient_match`` fora do enum →
    ``failed`` + ``CASE_ATTACHMENT_FAILED`` (validação de contrato, não só
    transporte/parse)."""
    case = _make_case(owner=owner_user)
    attachment = _make_attachment(case, owner=owner_user)
    invalid = dict(_MATCH_RESPONSE, patient_match="banana")
    fake = ScriptedLlmClient([json.dumps(invalid, ensure_ascii=False)])

    _run(case, attachment, fake)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.FAILED
    assert attachment.patient_match is None
    assert attachment.verification_summary == ""
    failed = _events_of_type(case, _FAILED)
    assert len(failed) == 1
    assert _events_of_type(case, _PROCESSED) == []


@pytest.mark.django_db
def test_verify_persist_atomic_rolls_back_on_event_failure(
    owner_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4 (P2 review F2): a persistência do resultado é ATÔMICA — falha ao
    gravar o evento (dentro do atomic) deixa a row em ``processing`` sem
    ``patient_match``/sumário (nada parcial)."""
    case = _make_case(owner=owner_user)
    attachment = _make_attachment(case, owner=owner_user)
    fake = ScriptedLlmClient([json.dumps(_MATCH_RESPONSE, ensure_ascii=False)])

    def _boom(**_kwargs: object) -> CaseEvent:
        raise RuntimeError("falha injetada na gravação do evento")

    monkeypatch.setattr("apps.attachments.verification.CaseEvent.objects.create", _boom)

    with (
        override_settings(LLM_CLIENT_FACTORY=lambda: fake, LLM1_MODEL=FAKE_MODEL),
        pytest.raises(RuntimeError, match="falha injetada"),
    ):
        verify_attachment(case, attachment)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.PROCESSING
    assert attachment.patient_match is None
    assert attachment.verification_summary == ""
    assert attachment.processed_at is None
    assert _events_of_type(case, _PROCESSED) == []
    assert _events_of_type(case, _FAILED) == []
