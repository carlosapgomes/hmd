"""Testes do orquestrador do pipeline LLM (slice 006, R3/R8, design D9).

Cobre R3/R8: ``process_case_pipeline`` idempotente por estado — ``LLM_EXTRACTING``
= pipeline completo (start → LLM1 → reconcile → divergência retém sem LLM2 |
ok → complete_llm_extraction → policy/prior/LLM2 → complete_llm_summarization
→ ``AWAITING_DOCTOR``); ``LLM_SUMMARIZING`` pós-bypass = retomada pulando
LLM1/reconcile (reaproveita o artefato persistido — assert de chamadas nos
fakes); demais estados → no-op; exceção → ``fail_processing`` (→ ``FAILED``,
único dono) com o motivo na trilha; lock ``worker_llm`` com release no finally
(e release no MESMO atomic da transição de saída consumida por signal —
coordenação do change 05); conflito de lock → inline propaga, async vira no-op.
Cobre também o R8 inline full-chain ``create → … → AWAITING_DOCTOR`` numa
transação de teste (cadeia pdf → anonimização → pipeline com fakes).

Cliente sempre fake via ``LLM_CLIENT_FACTORY`` (override_settings) — a suíte
nunca toca rede. Prompts do seed criados pelo fixture autouse ``seed_prompts``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from io import StringIO
from types import SimpleNamespace
from typing import Any

import pymupdf
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings

from apps.accounts.models import User
from apps.anonymization.engine import AnonymizationEngine
from apps.cases.events import CaseEventType
from apps.cases.locks import CaseLockConflictError, claim_case_lock
from apps.cases.models import Case, CaseProcedure, CaseStatus
from apps.cases.procedures import get_declared_procedure_types
from apps.pipeline.orchestrator import process_case_pipeline

FAKE_MODEL_LLM1 = "modelo-llm1-teste"
FAKE_MODEL_LLM2 = "modelo-llm2-teste"
SYSTEM_ROLE = "system"

_ANONYMIZED_TEXT = (
    "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente. "
    "Solicitada avaliacao hemodinamica."
)
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "JOSE MARIA DE SOUZA", "entity_type": "PERSON"},
    "<DATA_1>": {"value": "05/03/1972", "entity_type": "DATE_TIME"},
}


class ScriptedLlmClient:
    """Cliente fake com roteiro de respostas compartilhado pelas duas etapas.

    O mesmo fake atende LLM1 e LLM2 (a factory devolve a mesma instância); o
    ``model`` registrado em cada chamada identifica a etapa (LLM1_MODEL vs
    LLM2_MODEL).
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


def _artifact(
    procedure_types: list[str],
    *,
    platelets: float | None = None,
) -> dict[str, object]:
    """Artefato LLM1 schema-válido para os tipos detectados dados (pt-BR)."""
    exames: dict[str, object] | None = None
    if platelets is not None:
        exames = {
            "platelets": {
                "status": "confirmado",
                "value": platelets,
                "unit": "mm3",
                "evidence_spans": [
                    {
                        "field_path": "exames.platelets",
                        "excerpt": "ultimo hemograma com plaquetas reduzidas",
                    }
                ],
            }
        }
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
        "contexto_clinico": ("Paciente <PESSOA_1> com claudicacao intermitente ha tres meses."),
        "linha_do_tempo": [],
        "exames": exames,
        "medicacoes": [],
        "comorbidades": [],
        "contraindicacoes": [],
        "trechos_nao_classificados": [],
    }


def _llm2_response(
    procedure_types: list[str],
    *,
    suggestion: str = "aceitar",
) -> dict[str, object]:
    """Resposta LLM2 schema-válida cobrindo exatamente os tipos dados."""
    return {
        "summary_text": "Sumario clinico em portugues, sem intercorrencias.",
        "procedures": [
            {
                "procedure_type": procedure_type,
                "suggestion": suggestion,
                "motivos": ["sem contraindicoes identificadas"],
                "evidence_spans": [],
            }
            for procedure_type in procedure_types
        ],
    }


@pytest.fixture(autouse=True)
def _seeded_prompts() -> None:
    """Seed idempotente dos 28 prompts (a montagem do caso exige os ativos)."""
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture
def owner_user() -> User:
    """Dono (criador) dos casos dos testes."""
    return User.objects.create_user(username="dono-orch", password="senha-teste")


def _make_llm_extracting_case(
    *,
    owner: User,
    declared: Sequence[str],
    anonymized_text: str = _ANONYMIZED_TEXT,
    pseudonyms: dict[str, dict[str, str]] | None = None,
) -> Case:
    """Caso em LLM_EXTRACTING com rows declaradas e artefatos de anonimização."""
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


def _make_llm_summarizing_case(
    *,
    owner: User,
    declared: Sequence[str],
    structured: dict[str, object],
    anonymized_text: str = _ANONYMIZED_TEXT,
    pseudonyms: dict[str, dict[str, str]] | None = None,
) -> Case:
    """Caso em LLM_SUMMARIZING (retomada pós-bypass) com artefato LLM1 persistido."""
    case = _make_llm_extracting_case(
        owner=owner,
        declared=declared,
        anonymized_text=anonymized_text,
        pseudonyms=pseudonyms,
    )
    case.structured_data = structured
    case.save(update_fields=["structured_data"])
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.LLM_SUMMARIZING
    return case


def _run(case: Case, fake: ScriptedLlmClient) -> None:
    """Executa o orquestrador com o fake injetado e os modelos do teste."""
    with override_settings(
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    ):
        process_case_pipeline(case.case_id)


def _run_resume(case: Case, fake: ScriptedLlmClient) -> None:
    """Retomada (LLM_SUMMARIZING) com o fake injetado — sem LLM1."""
    with override_settings(
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    ):
        process_case_pipeline(case.case_id)


def _event_types(case: Case) -> list[str]:
    return list(case.events.order_by("id").values_list("event_type", flat=True))


# ── R3/R8: pipeline completo feliz → AWAITING_DOCTOR ───────────────────────


@pytest.mark.django_db
def test_full_pipeline_to_awaiting_doctor(owner_user: User) -> None:
    """R3/R8: caso em LLM_EXTRACTING → pipeline completo → AWAITING_DOCTOR com
    artefatos (structured/policy/summary/suggested) e eventos das etapas."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif", "cat_cardiaco"))
    artifact = _artifact(["art_perif", "cat_cardiaco"])
    fake = ScriptedLlmClient(
        [json.dumps(artifact), json.dumps(_llm2_response(["art_perif", "cat_cardiaco"]))]
    )

    _run(case, fake)
    case.refresh_from_db()

    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert case.manual_review_required is False
    assert case.structured_data == artifact
    assert case.policy_result  # por procedimento
    assert case.summary_text.startswith("Sumario clinico")
    assert case.suggested_action["aggregate"]["suggestion"] == "aceitar"
    assert case.suggested_action["procedures"]["art_perif"]["suggestion"] == "aceitar"
    # Duas chamadas: LLM1 + LLM2 (modelos distintos).
    assert [call_model for call_model, _messages, _schema in fake.calls] == [
        FAKE_MODEL_LLM1,
        FAKE_MODEL_LLM2,
    ]
    # Conjunto declarado preservado nas rows.
    assert get_declared_procedure_types(case) == ("art_perif", "cat_cardiaco")

    events = _event_types(case)
    assert events[-1] == CaseEventType.CASE_LOCK_RELEASED.value
    assert events[-2] == CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value
    # Ordem das etapas entre os eventos do pipeline.
    pipeline_events = [
        event_type
        for event_type in events
        if event_type
        in {
            CaseEventType.CASE_LLM1_COMPLETED.value,
            CaseEventType.CASE_PROCEDURES_DETECTED.value,
            CaseEventType.CASE_STATUS_LLM_SUMMARIZING.value,
            CaseEventType.CASE_POLICY_EVALUATED.value,
            CaseEventType.PRIOR_CASE_LOOKUP.value,
            CaseEventType.CASE_LLM2_COMPLETED.value,
            CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value,
        }
    ]
    assert pipeline_events == [
        CaseEventType.CASE_LLM1_COMPLETED.value,
        CaseEventType.CASE_PROCEDURES_DETECTED.value,
        CaseEventType.CASE_STATUS_LLM_SUMMARIZING.value,  # complete_llm_extraction
        CaseEventType.CASE_STATUS_LLM_SUMMARIZING.value,  # start_llm_summarization
        CaseEventType.CASE_POLICY_EVALUATED.value,
        CaseEventType.PRIOR_CASE_LOOKUP.value,
        CaseEventType.PRIOR_CASE_LOOKUP.value,
        CaseEventType.CASE_LLM2_COMPLETED.value,
        CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value,
    ]


@pytest.mark.django_db
def test_policy_refusal_marks_suggestion(owner_user: User) -> None:
    """R1/R8 via orquestrador: policy recusa (plaquetas baixas) → sugestão final
    recusa com os motivos da policy apesar de o LLM sugerir aceitar."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif",))
    artifact = _artifact(["art_perif"], platelets=80_000)
    fake = ScriptedLlmClient([json.dumps(artifact), json.dumps(_llm2_response(["art_perif"]))])

    _run(case, fake)
    case.refresh_from_db()

    assert case.status == CaseStatus.AWAITING_DOCTOR
    suggestion = case.suggested_action["procedures"]["art_perif"]
    assert suggestion["suggestion"] == "recusar"
    assert suggestion["motivos"] == case.policy_result["art_perif"]["refusal_reasons"]
    assert case.policy_result["art_perif"]["recommendation"] == "recomenda_recusar"
    assert case.suggested_action["aggregate"]["suggestion"] == "recusar"


# ── R3/R8: divergência retém sem chamar LLM2 ───────────────────────────────


@pytest.mark.django_db
def test_divergence_retains_no_llm2(owner_user: User) -> None:
    """R3/R8: detecção diverge (tipo não-declarado) → caso permanece em
    LLM_EXTRACTING retido; LLM2 NÃO é chamado (só a chamada LLM1)."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif",))
    fake = ScriptedLlmClient([json.dumps(_artifact(["nefrostomia"]))])

    _run(case, fake)
    case.refresh_from_db()

    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == "procedure_divergence"
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == FAKE_MODEL_LLM1
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value not in _event_types(case)
    # Lock de worker liberado (evento de release na trilha).
    assert CaseEventType.CASE_LOCK_RELEASED.value in _event_types(case)


# ── R3/R8: retomada pós-bypass pula LLM1 ───────────────────────────────────


@pytest.mark.django_db
def test_resume_after_bypass_skips_llm1(owner_user: User) -> None:
    """R3/R8: caso em LLM_SUMMARIZING (pós-bypass) → retomada roda SÓ
    policy/prior/LLM2/complete — LLM1 NÃO re-executada (assert de chamadas) e
    o artefato LLM1 persistido é reaproveitado."""
    declared = ("art_perif", "cat_cardiaco")
    artifact = _artifact(list(declared))
    case = _make_llm_summarizing_case(owner=owner_user, declared=declared, structured=artifact)
    fake = ScriptedLlmClient([json.dumps(_llm2_response(list(declared)))])

    _run_resume(case, fake)
    case.refresh_from_db()

    assert case.status == CaseStatus.AWAITING_DOCTOR
    # Nenhuma chamada com o modelo LLM1.
    assert fake.calls and all(model == FAKE_MODEL_LLM2 for model, _m, _s in fake.calls)
    assert case.structured_data == artifact
    assert case.summary_text
    assert case.suggested_action["procedures"]["art_perif"]["suggestion"] == "aceitar"
    # A retomada não registrou extração (nenhum evento CASE_LLM1_COMPLETED na trilha).
    assert _event_types(case).count(CaseEventType.CASE_LLM1_COMPLETED.value) == 0


# ── R3/R8: falha fecha o caso (fail-closed) ────────────────────────────────


@pytest.mark.django_db
def test_failure_fails_closed(owner_user: User) -> None:
    """R3/R8: LLM2 inválida após o retry → ``LlmPipelineError`` → orquestrador
    (único dono) chama ``fail_processing`` → ``FAILED`` com o motivo na trilha."""
    declared = ("art_perif",)
    case = _make_llm_summarizing_case(
        owner=owner_user, declared=declared, structured=_artifact(list(declared))
    )
    fake = ScriptedLlmClient(["resposta invalida {{", "resposta invalida {{"])

    _run_resume(case, fake)
    case.refresh_from_db()

    assert case.status == CaseStatus.FAILED
    failed = case.events.get(event_type=CaseEventType.CASE_STATUS_FAILED)
    assert failed.payload["reason"] == "llm2_schema"
    # Lock de worker liberado mesmo no caminho de falha.
    assert CaseEventType.CASE_LOCK_RELEASED.value in _event_types(case)


# ── R3/R8: no-op fora dos estados do pipeline ──────────────────────────────


@pytest.mark.django_db
def test_noop_when_awaiting(owner_user: User) -> None:
    """R3/R8: reexecução em AWAITING_DOCTOR → no-op (sem novos eventos, sem
    claim de lock)."""
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif",))
    case.structured_data = _artifact(["art_perif"])
    case.save(update_fields=["structured_data"])
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    events_before = case.events.count()

    with override_settings(LLM_CLIENT_FACTORY=lambda: ScriptedLlmClient([])):
        process_case_pipeline(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert case.events.count() == events_before


# ── R3/R8: contrato de lock ────────────────────────────────────────────────


@pytest.mark.django_db
def test_respects_lock(owner_user: User) -> None:
    """R3: caso sob lock ativo de outro ator → inline propaga
    ``CaseLockConflictError`` sem efeito (o caso permanece LLM_EXTRACTING)."""
    doctor = User.objects.create_user(username="outro-ator", password="senha-teste")
    case = _make_llm_extracting_case(owner=owner_user, declared=("art_perif",))
    claim_case_lock(case, user=doctor, context="doctor_decision", role="doctor")
    events_before = case.events.count()

    with override_settings(LLM_CLIENT_FACTORY=lambda: ScriptedLlmClient([])):
        with pytest.raises(CaseLockConflictError):
            process_case_pipeline(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.events.count() == events_before


# ── R8: full-chain inline (create → … → AWAITING_DOCTOR) ──────────────────
# Cadeia REAL com transações de verdade (transaction=True): create_case_with_documents
# → task pdf inline → on_commit → anonimização inline → entrada em LLM_EXTRACTING
# → on_commit → pipeline inline → AWAITING_DOCTOR. A coordenação transição+release
# no mesmo atomic (desvio autorizado na task de anonimização) garante o claim
# ``worker_llm`` sem conflito em dev single-process.

_MEMORY_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

_CHAIN_RECORD_NUMBER = "33345"
_CHAIN_REPORT_HEADER = "RELATÓRIO DE OCORRÊNCIAS"
_CHAIN_INSTITUTIONAL_SIGNALS = [
    "Central Estadual de Regulação",
    "Secretaria da Saúde do Estado",
    "Governo do Estado da Bahia",
]
_CHAIN_SECTIONS = [
    f"Código: {_CHAIN_RECORD_NUMBER}",
    "Abertura: 01/02/2025",
    "Unid. Origem: Hospital Geral do Estado",
    "Motivo da Solicitação: cateterismo cardíaco diagnóstico",
    "Paciente: MARIA DA SILVA SOUZA",
    "Data de Nascimento: 15/08/1955",
    "Complemento da Solicitação: paciente encaminhado para avaliação hemodinâmica",
    "Resumo Clínico: paciente com dor torácica em investigação, encaminhado para "
    "avaliação hemodinâmica.",
    "Dias em tela: 3",
    "Data Adm. Unid.: 01/02/2025",
]
_CHAIN_PADDING = "Linha de preenchimento para atingir o tamanho mínimo do relatório de regulação."


class _StubAnalyzer:
    """Analyzer fake: devolve zero resultados (só o caminho determinístico)."""

    def analyze(self, **kwargs: object) -> list[SimpleNamespace]:
        del kwargs
        return []


def _chain_report_pdf(name: str = "relatorio-cadeia.pdf") -> SimpleUploadedFile:
    """PDF real de 1 página no padrão SESAB com PII (gate ok e anonimizável)."""
    lines = [_CHAIN_REPORT_HEADER, *_CHAIN_INSTITUTIONAL_SIGNALS, *_CHAIN_SECTIONS]
    while len("\n".join(lines)) < 560:
        lines.append(_CHAIN_PADDING)
    document = pymupdf.open()  # type: ignore[no-untyped-call]
    try:
        document.new_page().insert_text((72, 72), "\n".join(lines))
        data = document.tobytes()  # type: ignore[no-untyped-call]
        return SimpleUploadedFile(name, bytes(data), content_type="application/pdf")
    finally:
        document.close()  # type: ignore[no-untyped-call]


@pytest.mark.django_db(transaction=True)
def test_inline_full_chain_to_awaiting_doctor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R8: cadeia inline completa (create → pdf → anonimização → LLM1 → LLM2) →
    AWAITING_DOCTOR com artefatos, numa única transação de teste real."""
    from apps.intake.services import create_case_with_documents

    user = User.objects.create_user(username="chain-pipeline", password="senha-teste")
    fake_engine = AnonymizationEngine(analyzer=_StubAnalyzer(), anonymizer=None)
    monkeypatch.setattr("apps.anonymization.services.get_anonymization_engine", lambda: fake_engine)
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(["art_perif"])),
            json.dumps(_llm2_response(["art_perif"])),
        ]
    )

    with override_settings(
        INTAKE_RUN_TASKS_INLINE=True,
        ANONYMIZATION_RUN_TASKS_INLINE=True,
        LLM_RUN_TASKS_INLINE=True,
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
        STORAGES=_MEMORY_STORAGES,
    ):
        case = create_case_with_documents(
            user=user,
            role=SYSTEM_ROLE,
            file=_chain_report_pdf(),
            procedure_type="art_perif",
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert case.anonymized_text != ""
    assert case.structured_data
    assert case.policy_result
    assert case.summary_text
    assert case.suggested_action["procedures"]["art_perif"]["suggestion"] == "aceitar"
    event_types = _event_types(case)
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value in event_types
    assert CaseEventType.CASE_STATUS_FAILED.value not in event_types
    assert CaseEventType.CASE_LOCK_RELEASED.value in event_types
    # Sem enfileiramento duplicado: apenas um release do lock de cada worker.
    assert event_types.count(CaseEventType.CASE_LOCK_RELEASED.value) == 3
    assert event_types.count(CaseEventType.CASE_LLM2_COMPLETED.value) == 1


@pytest.mark.django_db(transaction=True)
def test_inline_full_chain_divergence_retains(monkeypatch: pytest.MonkeyPatch) -> None:
    """R8: full-chain inline com divergência → caso retido em LLM_EXTRACTING
    (sem LLM2) e sem conflito de lock na cadeia."""
    from apps.intake.services import create_case_with_documents

    user = User.objects.create_user(username="chain-divergence", password="senha-teste")
    fake_engine = AnonymizationEngine(analyzer=_StubAnalyzer(), anonymizer=None)
    monkeypatch.setattr("apps.anonymization.services.get_anonymization_engine", lambda: fake_engine)
    fake = ScriptedLlmClient([json.dumps(_artifact(["nefrostomia"]))])

    with override_settings(
        INTAKE_RUN_TASKS_INLINE=True,
        ANONYMIZATION_RUN_TASKS_INLINE=True,
        LLM_RUN_TASKS_INLINE=True,
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
        STORAGES=_MEMORY_STORAGES,
    ):
        case = create_case_with_documents(
            user=user,
            role=SYSTEM_ROLE,
            file=_chain_report_pdf(),
            procedure_type="art_perif",
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == "procedure_divergence"
    assert len(fake.calls) == 1
    assert CaseEventType.CASE_STATUS_FAILED.value not in _event_types(case)
    assert CaseEventType.CASE_LLM2_COMPLETED.value not in _event_types(case)
