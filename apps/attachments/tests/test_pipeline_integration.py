"""Testes de integração do pipeline do anexo (change attachment-processing-ocr,
slice 003, R5/R6).

A task ``process_case_attachments`` roda o pipeline completo por anexo:
extração (PDF local com camada de texto) → anonimização alinhada ao caso
(``anonymize_attachment_text`` semeado com o mapa do caso) → verificação LLM
(``verify_attachment`` — cliente fake, seed ATTACHMENT_VERIFICATION). Texto
extraído vazio → ``failed`` com motivo claro; idempotência por etapa
(anexo com ``extracted_text`` e sem ``anonymized_text`` pula a extração). O
LLM recebe EXCLUSIVAMENTE tokens: assert recursivo do ``messages`` capturado
contra os valores reais dos mapas do anexo/caso (R6).

Anonimização roda com analyzer fake (sem modelo spaCy) e o cliente LLM é
sempre um fake via ``LLM_CLIENT_FACTORY`` — suíte sem rede.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from io import StringIO
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.accounts.models import User
from apps.anonymization.engine import AnonymizationEngine
from apps.attachments.models import (
    AttachmentStatus,
    CaseAttachment,
    ExtractionMethod,
    PatientMatch,
)
from apps.attachments.tasks import process_case_attachments
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseEvent, CaseStatus

FAKE_MODEL = "modelo-pipeline-anexo-teste"

# Mapa do CASO (relatório principal já anonimizado): médico assistente
# <PESSOA_1>, paciente do caso <PESSOA_2> e CPF do caso <CPF_1>.
_CASE_MAP: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "JOSE CARLOS DE OLIVEIRA", "entity_type": "PESSOA"},
    "<PESSOA_2>": {"value": "MARIA DA SILVA", "entity_type": "PESSOA"},
    "<CPF_1>": {"value": "11122233344", "entity_type": "CPF"},
}

_MATCHING_PDF_PAGES = [
    "Eletrocardiograma ambulatorial",
    "Medico solicitante: Dr. Jose Carlos de Oliveira",
    "Paciente: Maria da Silva",
    "Resumo Clinico: ritmo sinusal, sem alteracoes isquemicas.",
    "Documento de referencia: 11122233344",
]

_OTHER_PATIENT_PDF_PAGES = [
    "Tomografia computadorizada",
    "Paciente: Ana Beatriz Souza",
    "Resumo Clinico: exame de controle pos-operatorio.",
]

_PROCESSED = CaseEventType.CASE_ATTACHMENT_PROCESSED.value
_FAILED = CaseEventType.CASE_ATTACHMENT_FAILED.value


class _SearchAnalyzer:
    """Analyzer fake: localiza os valores conhecidos no texto (sem modelo).

    A busca é feita sobre o texto real de cada chamada (offsets exatos) — o
    mesmo analyzer funciona para o texto extraído do PDF sem prever a saída do
    PyMuPDF.
    """

    def __init__(self, findings: list[tuple[str, str]]) -> None:
        self._findings = findings

    def analyze(
        self,
        *,
        text: str,
        language: str,
        score_threshold: float,
    ) -> list[SimpleNamespace]:
        del language, score_threshold
        results: list[SimpleNamespace] = []
        for entity_type, value in self._findings:
            start = text.find(value)
            if start >= 0:
                results.append(
                    SimpleNamespace(
                        start=start,
                        end=start + len(value),
                        entity_type=entity_type,
                        score=0.9,
                    )
                )
        return results


class ScriptedLlmClient:
    """Cliente fake com roteiro de respostas; registra as chamadas."""

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


def _verification_response(match: str) -> str:
    """Resposta JSON da verificação com o patient_match dado (schema do slice)."""
    if match == PatientMatch.MISMATCH:
        return json.dumps(
            {
                "patient_match": "mismatch",
                "summary": "Documento de outro paciente.",
                "evidence": "Exame identificado com pessoa distinta do caso.",
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "patient_match": match,
            "summary": "Eletrocardiograma ambulatorial do paciente do caso.",
            "evidence": "ECG de <PESSOA_2> com ritmo sinusal.",
        },
        ensure_ascii=False,
    )


@pytest.fixture(autouse=True)
def _seeded_prompts() -> None:
    """Seed idempotente dos 29 prompts (a verificação exige o ATTACHMENT_VERIFICATION)."""
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture
def stub_anonymization_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[list[tuple[str, str]]], None]:
    """Substitui o engine por um analyzer fake com as buscas dadas."""

    def _stub(findings: list[tuple[str, str]]) -> None:
        fake = AnonymizationEngine(analyzer=_SearchAnalyzer(findings), anonymizer=None)
        monkeypatch.setattr("apps.anonymization.services.get_anonymization_engine", lambda: fake)

    return _stub


def _case_with_case_map(
    owner: User,
    case_factory: Callable[[User], Case],
) -> Case:
    """Caso como pós-anonimização do relatório (mapa + patient_name persistidos)."""
    case = case_factory(owner)
    case.patient_name = "Maria da Silva"
    case.pseudonym_map = dict(_CASE_MAP)
    case.save(update_fields=["patient_name", "pseudonym_map"])
    return case


def _run_pipeline(case: Case, fake: ScriptedLlmClient) -> None:
    """Executa a task com o fake injetado e o modelo do teste."""
    with override_settings(LLM_CLIENT_FACTORY=lambda: fake, LLM1_MODEL=FAKE_MODEL):
        process_case_attachments(case.case_id)


def _events_of_type(case: Case, event_type: str) -> list[CaseEvent]:
    return list(case.events.filter(event_type=event_type).order_by("id"))


def _recursive_serialized_messages(fake: ScriptedLlmClient) -> str:
    """Serializa todas as mensagens capturadas (base do assert recursivo)."""
    assert len(fake.calls) == 1
    _model, messages, _json_schema = fake.calls[0]
    return json.dumps(messages, ensure_ascii=False, sort_keys=True)


# ── R5/R6: happy path do pipeline completo (match persistido) ──────────────


@pytest.mark.django_db
def test_full_attachment_pipeline_match(
    owner_user: User,
    case_factory: Callable[[User], Case],
    pdf_bytes_factory: Callable[[list[str]], bytes],
    attachment_record_factory: Callable[..., CaseAttachment],
    stub_anonymization_engine: Callable[[list[tuple[str, str]]], None],
) -> None:
    """R5/R6: extração → anonimização semeada → verificação match; a row fecha
    ``processed`` com os artefatos (texto anonimizado + mapa do anexo só com
    usados) e o messages do LLM contém APENAS tokens (assert recursivo)."""
    case = _case_with_case_map(owner_user, case_factory)
    attachment = attachment_record_factory(
        case,
        content=pdf_bytes_factory(_MATCHING_PDF_PAGES),
        content_type="application/pdf",
        user=owner_user,
        name="ecg-exame.pdf",
    )
    stub_anonymization_engine([("PERSON", "Maria da Silva"), ("PERSON", "Jose Carlos de Oliveira")])
    case_map_before = dict(case.pseudonym_map)
    fake = ScriptedLlmClient([_verification_response(PatientMatch.MATCH)])

    _run_pipeline(case, fake)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.PROCESSED
    assert attachment.extraction_method == ExtractionMethod.LOCAL_PDF
    assert attachment.patient_match == PatientMatch.MATCH
    assert attachment.verification_summary
    assert attachment.verification_evidence
    assert attachment.processed_at is not None

    # Anonimização semeada: paciente do caso mantém <PESSOA_2>; CPF do caso
    # mantém <CPF_1>; o médico <PESSOA_1> também é reutilizado. Nada de valor
    # real no texto anonimizado.
    anonymized = attachment.anonymized_text
    assert "<PESSOA_2>" in anonymized
    assert "<PESSOA_1>" in anonymized
    assert "<CPF_1>" in anonymized
    for entry in _CASE_MAP.values():
        assert str(entry["value"]) not in anonymized
    assert set(attachment.pseudonym_map) == {"<PESSOA_1>", "<PESSOA_2>", "<CPF_1>"}

    # Mapa do caso intocado (R1-d).
    case.refresh_from_db()
    assert case.pseudonym_map == case_map_before

    # Evento PROCESSED (payload match+método) no mesmo fluxo.
    processed = _events_of_type(case, _PROCESSED)
    assert len(processed) == 1
    assert processed[0].payload["patient_match"] == PatientMatch.MATCH
    assert processed[0].payload["method"] == ExtractionMethod.LOCAL_PDF
    assert _events_of_type(case, _FAILED) == []

    # R6 — LLM recebe EXCLUSIVAMENTE tokens.
    serialized = _recursive_serialized_messages(fake)
    assert "<PESSOA_2>" in serialized
    for entry in list(_CASE_MAP.values()) + list(attachment.pseudonym_map.values()):
        assert str(entry["value"]) not in serialized


# ── R5: paciente DIFERENTE no anexo → mismatch persistido (sem descarte) ───


@pytest.mark.django_db
def test_full_attachment_pipeline_other_patient_mismatch(
    owner_user: User,
    case_factory: Callable[[User], Case],
    pdf_bytes_factory: Callable[[list[str]], bytes],
    attachment_record_factory: Callable[..., CaseAttachment],
    stub_anonymization_engine: Callable[[list[tuple[str, str]]], None],
) -> None:
    """R5: anexo de OUTRO paciente → token novo (<PESSOA_3>, acima do máximo
    do caso) e a verificação persiste mismatch — nunca descarta o anexo."""
    case = _case_with_case_map(owner_user, case_factory)
    attachment = attachment_record_factory(
        case,
        content=pdf_bytes_factory(_OTHER_PATIENT_PDF_PAGES),
        content_type="application/pdf",
        user=owner_user,
        name="tc-ouro-paciente.pdf",
    )
    stub_anonymization_engine([("PERSON", "Ana Beatriz Souza")])
    fake = ScriptedLlmClient([_verification_response(PatientMatch.MISMATCH)])

    _run_pipeline(case, fake)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.PROCESSED
    assert attachment.patient_match == PatientMatch.MISMATCH
    assert "<PESSOA_3>" in attachment.anonymized_text
    assert "<PESSOA_2>" not in attachment.anonymized_text  # paciente do caso ausente
    assert "Ana Beatriz Souza" not in attachment.anonymized_text
    assert attachment.pseudonym_map["<PESSOA_3>"]["value"] == "Ana Beatriz Souza"
    assert CaseAttachment.objects.filter(pk=attachment.pk).exists()  # nunca descarta
    processed = _events_of_type(case, _PROCESSED)
    assert len(processed) == 1
    assert processed[0].payload["patient_match"] == PatientMatch.MISMATCH


# ── R5: idempotência por etapa — texto extraído pula a extração ────────────


@pytest.mark.django_db
def test_full_pipeline_skips_extraction_when_text_present(
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    stub_anonymization_engine: Callable[[list[tuple[str, str]]], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R5: anexo com ``extracted_text`` e sem ``anonymized_text`` pula a
    extração (sem re-envio ao OCR) e segue direto para anonimização +
    verificação (idempotência por etapa)."""
    case = _case_with_case_map(owner_user, case_factory)
    attachment = attachment_record_factory(
        case,
        content=b"bytes nao relidos (extracao ja feita)",
        content_type="image/jpeg",
        user=owner_user,
        name="foto-ja-extraida.jpg",
        status=AttachmentStatus.PROCESSING,
        extracted_text="Paciente: Maria da Silva\n",
        extraction_method=ExtractionMethod.LOCAL_PDF,
    )
    stub_anonymization_engine([])
    vision_calls: list[object] = []

    def _fake_vision(image_bytes: bytes, content_type: str) -> str:
        vision_calls.append((image_bytes, content_type))
        raise AssertionError("extração já feita — OCR não pode rodar de novo")

    monkeypatch.setattr("apps.attachments.vision.transcribe_image", _fake_vision)
    fake = ScriptedLlmClient([_verification_response(PatientMatch.MATCH)])

    _run_pipeline(case, fake)

    attachment.refresh_from_db()
    assert vision_calls == []
    assert attachment.extraction_method == ExtractionMethod.LOCAL_PDF
    assert attachment.extracted_text == "Paciente: Maria da Silva\n"
    assert attachment.anonymized_text
    assert attachment.status == AttachmentStatus.PROCESSED
    assert attachment.patient_match == PatientMatch.MATCH


# ── R5: texto extraído vazio → failed com motivo claro ─────────────────────


@pytest.mark.django_db
def test_full_pipeline_empty_extracted_text_fails_clearly(
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    stub_anonymization_engine: Callable[[list[tuple[str, str]]], None],
) -> None:
    """R5: extração devolveu texto vazio → ``failed`` com motivo claro; sem
    anonimizar/verificar conteúdo inexistente; caso intacto."""
    case = _case_with_case_map(owner_user, case_factory)
    attachment = attachment_record_factory(
        case,
        content=b"conteudo sem texto",
        content_type="application/pdf",
        user=owner_user,
        name="anexo-sem-texto.pdf",
        status=AttachmentStatus.PROCESSING,
        extracted_text="",
        extraction_method=ExtractionMethod.LOCAL_PDF,
    )
    stub_anonymization_engine([])
    fake = ScriptedLlmClient([])

    _run_pipeline(case, fake)

    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.FAILED
    assert "texto extraído vazio" in attachment.failed_reason
    assert attachment.anonymized_text == ""
    assert attachment.patient_match is None
    assert fake.calls == []  # o LLM nunca foi chamado sem conteúdo
    failed = _events_of_type(case, _FAILED)
    assert len(failed) == 1
    assert "texto extraído vazio" in str(failed[0].payload["reason"])
    case.refresh_from_db()
    assert case.status == CaseStatus.NEW  # intacto
