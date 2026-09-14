"""Testes da task de anonimização do worker (slice 004, change presidio-
anonymization; design D7, R1–R4/R7).

A task é chamada DIRETO (sem qcluster — a flag inline e o R3 dispensam worker)
e o engine é um analyzer fake no lugar do singleton (monkeypatch em
``apps.anonymization.services.get_anonymization_engine`` — o modelo real é
coberto pelos testes do engine). Cobre os cenários da spec "Processamento
assíncrono no cluster anonymization" (feliz → LLM_EXTRACTING com artefatos,
reexecução → no-op) e "Fail-closed antes de qualquer LLM" (exceção → FAILED
sem texto anonimizado; caso sem texto extraído → fail_processing), mais o
contrato de lock (``CaseLockConflictError`` propagada, release no finally) e o
signal anti-recursão R3: a entrada REAL em ANONYMIZING (``source ==
PDF_EXTRACTING``) enfileira; a self-transition ``start_anonymization``
(``source == ANONYMIZING``) NÃO re-dispara; rollback da transação do evento
não enfileira (``transaction.on_commit``). Cobre também o finding P1 do review
(cadeia inline completa ``create_case_with_documents`` → pdf → on_commit →
anonimização → LLM_EXTRACTING, sem conflito de lock single-process).

Teste RED do slice: o módulo de tasks não existe ainda — a coleta falha com
ModuleNotFoundError; GREEN o implementa (apps/anonymization/tasks.py).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pymupdf
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import override_settings

from apps.accounts.models import User
from apps.anonymization.engine import AnonymizationEngine
from apps.anonymization.tasks import process_case_anonymization
from apps.cases.events import CaseEventType
from apps.cases.locks import CaseLockConflictError, claim_case_lock
from apps.cases.models import Case, CaseStatus
from apps.intake.services import create_case_with_documents

# Teste RED do slice: o módulo de tasks não existia ainda — a coleta falhava com
# ModuleNotFoundError; GREEN o implementou (apps/anonymization/tasks.py).

SYSTEM_ROLE = "system"
DOCTOR_ROLE = "doctor"

# Texto do relatório anonimizável SÓ pelo caminho determinístico (analyzer
# vazio): nome/nascimento/nº de ocorrência viram tokens mesmo sem NER.
_REPORT_TEXT = (
    "RELATÓRIO DE OCORRÊNCIAS\n"
    "Código: 33345\n"
    "Paciente: MARIA DA SILVA SOUZA\n"
    "Data de Nascimento: 15/08/1955\n"
    "Resumo Clínico: paciente internado com quadro de dor torácica; "
    "encaminhado para avaliação hemodinâmica.\n"
)


class _StubAnalyzer:
    """Analyzer fake: devolve results pré-configurados a cada chamada."""

    def __init__(self, results: list[SimpleNamespace]) -> None:
        self._results = results

    def analyze(self, **kwargs: object) -> list[SimpleNamespace]:
        del kwargs
        return list(self._results)


class _ExplodingAnalyzer:
    """Analyzer fake que sempre propaga exceção (fail-closed da task)."""

    def analyze(self, **kwargs: object) -> list[SimpleNamespace]:
        del kwargs
        raise RuntimeError("falha simulada do analyzer")


def _stub_engine(monkeypatch: pytest.MonkeyPatch, exploding: bool = False) -> None:
    """Substitui o singleton do engine por um analyzer fake (sem modelo)."""
    analyzer: _StubAnalyzer | _ExplodingAnalyzer
    analyzer = _ExplodingAnalyzer() if exploding else _StubAnalyzer([])
    fake = AnonymizationEngine(analyzer=analyzer, anonymizer=None)
    monkeypatch.setattr("apps.anonymization.services.get_anonymization_engine", lambda: fake)


def _create_user(username: str = "anon-task") -> User:
    """Usuário criador dos casos dos testes (a task atua como sistema)."""
    return User.objects.create_user(username=username, password="senha-teste")


def _case_in_anonymizing(user: User, *, text: str = _REPORT_TEXT) -> Case:
    """Cria um caso e o dirige pelas transições válidas até ANONYMIZING."""
    case = Case.objects.create(created_by=user)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.ANONYMIZING
    case.extracted_text = text
    case.save(update_fields=["extracted_text"])
    return case


def _event_types(case: Case) -> list[str]:
    return list(case.events.values_list("event_type", flat=True))


# ── Cenário feliz da spec "Processamento assíncrono" ───────────────────────


@pytest.mark.django_db
def test_happy_path_llm_extracting(monkeypatch: pytest.MonkeyPatch) -> None:
    """R4/R7: caso ANONYMIZING com texto → task → LLM_EXTRACTING com artefatos.

    ``anonymized_text``/``pseudonym_map``/``anonymization_report`` preenchidos
    (caminho determinístico com analyzer vazio) e os eventos de início
    (self ANONYMIZING), conclusão (``CASE_ANONYMIZATION_COMPLETED``) e
    transição ANONYMIZING → LLM_EXTRACTING na trilha, sob lock de worker com
    claim/release.
    """
    case = _case_in_anonymizing(_create_user())
    _stub_engine(monkeypatch)

    process_case_anonymization(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.LLM_EXTRACTING
    assert "<PESSOA_1>" in case.anonymized_text
    assert "<DATA_1>" in case.anonymized_text
    assert "MARIA DA SILVA SOUZA" not in case.anonymized_text
    assert case.pseudonym_map
    assert case.anonymization_report["counts_by_type"] == {
        "PESSOA": 1,
        "DATA": 1,
        "OCORRENCIA": 1,
    }

    event_types = _event_types(case)
    # Entrada (no setup) + início (self da task) — nunca mais que duas vezes
    # (sem re-disparo do signal: a self-transition tem source=ANONYMIZING).
    assert event_types.count(CaseEventType.CASE_STATUS_ANONYMIZING.value) == 2
    assert CaseEventType.CASE_ANONYMIZATION_COMPLETED.value in event_types
    assert event_types.count(CaseEventType.CASE_STATUS_LLM_EXTRACTING.value) == 1
    assert CaseEventType.CASE_LOCK_CLAIMED.value in event_types
    assert CaseEventType.CASE_LOCK_RELEASED.value in event_types


# ── Fail-closed (spec "Fail-closed antes de qualquer LLM") ─────────────────


@pytest.mark.django_db
def test_service_exception_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """R4/cenário spec: exceção do serviço → FAILED com motivo e texto vazio.

    Nada do wrapper vaza: ``anonymized_text`` permanece vazio (persistência e
    transição atômicas + refresh antes do fail_processing) e nenhuma transição
    posterior (LLM_EXTRACTING) executa.
    """
    case = _case_in_anonymizing(_create_user())
    _stub_engine(monkeypatch, exploding=True)

    process_case_anonymization(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.FAILED
    assert case.anonymized_text == ""
    assert case.pseudonym_map == {}
    assert case.anonymization_report == {}
    failed_event = case.events.get(event_type=CaseEventType.CASE_STATUS_FAILED.value)
    assert failed_event.payload["target"] == CaseStatus.FAILED
    assert failed_event.payload["reason"] == "falha simulada do analyzer"
    assert CaseEventType.CASE_STATUS_LLM_EXTRACTING.value not in _event_types(case)
    assert not case.events.filter(
        event_type=CaseEventType.CASE_ANONYMIZATION_COMPLETED.value
    ).exists()


@pytest.mark.django_db
def test_empty_text_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """R4/R7: ANONYMIZING sem texto extraído → fail_processing("sem texto extraído").

    O núcleo do slice 003 é defensivo e avançaria com texto vazio — quem falha
    fechado é a task (porteiro): o caso vai a FAILED com o motivo na trilha e
    ``anonymized_text`` permanece vazio.
    """
    case = _case_in_anonymizing(_create_user(), text="")
    _stub_engine(monkeypatch)

    process_case_anonymization(case.case_id)
    case.refresh_from_db()

    assert case.status == CaseStatus.FAILED
    assert case.anonymized_text == ""
    failed_event = case.events.get(event_type=CaseEventType.CASE_STATUS_FAILED.value)
    assert failed_event.payload["reason"] == "sem texto extraído"
    assert CaseEventType.CASE_STATUS_LLM_EXTRACTING.value not in _event_types(case)


# ── Idempotência e lock ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_idempotent_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """R4/R7: reexecução em LLM_EXTRACTING → no-op sem novos eventos."""
    user = _create_user()
    case = _case_in_anonymizing(user)
    _stub_engine(monkeypatch)
    process_case_anonymization(case.case_id)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    events_before = case.events.count()

    process_case_anonymization(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.events.count() == events_before


@pytest.mark.django_db
def test_respects_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """R4/R7: caso sob lock ativo de outro ator → CaseLockConflictError propagada.

    A task não começa nada (sem evento de início) quando o claim conflita.
    """
    doctor = _create_user("doctor-task")
    case = _case_in_anonymizing(_create_user())
    _stub_engine(monkeypatch)
    claim_case_lock(case, user=doctor, context="doctor_decision", role=DOCTOR_ROLE)

    with pytest.raises(CaseLockConflictError):
        process_case_anonymization(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.ANONYMIZING
    # Só o evento de entrada (setup) — nenhum início de worker aconteceu.
    assert _event_types(case).count(CaseEventType.CASE_STATUS_ANONYMIZING.value) == 1


# ── R3: signal anti-recursão na entrada de ANONYMIZING ─────────────────────
# Os testes de signal rodam com transações REAIS (transaction=True): o enqueue
# acontece via ``transaction.on_commit``, que só executa no commit — o modo
# default da suíte (rollback ao fim de cada teste) nunca o dispararia. O engine
# é stub ou o enqueue é substituído por um recorder (o ponto testado é a
# decisão do signal, não o pipeline).


@pytest.mark.django_db(transaction=True)
def test_signal_enqueues_on_anonymizing_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """R3: a entrada REAL em ANONYMIZING (complete_pdf_extraction, source
    PDF_EXTRACTING) enfileira via on_commit — com inline, o caso sai processado.

    O ciclo completo roda na saída do commit: task claima o lock, grava o
    início (self), anonimiza e completa para LLM_EXTRACTING. A self-transition
    NÃO re-dispara (guarda source=ANONYMIZING): exatamente uma conclusão e uma
    transição para LLM_EXTRACTING na trilha. O pipeline LLM (entrada em
    LLM_EXTRACTING) não roda aqui — fora do contrato deste teste (a cadeia LLM
    completa é coberta pelos testes do change 06).
    """
    user = _create_user("signal-entry")
    case = Case.objects.create(created_by=user)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.extracted_text = _REPORT_TEXT
    case.save(update_fields=["extracted_text"])
    _stub_engine(monkeypatch)

    with override_settings(LLM_RUN_TASKS_INLINE=False):
        case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()

    assert case.status == CaseStatus.LLM_EXTRACTING
    assert "<PESSOA_1>" in case.anonymized_text
    event_types = _event_types(case)
    # Entrada + início = 2; a entrada NÃO foi reprocessada pelo self event.
    assert event_types.count(CaseEventType.CASE_STATUS_ANONYMIZING.value) == 2
    assert event_types.count(CaseEventType.CASE_ANONYMIZATION_COMPLETED.value) == 1
    assert event_types.count(CaseEventType.CASE_STATUS_LLM_EXTRACTING.value) == 1


@pytest.mark.django_db(transaction=True)
def test_signal_self_transition_does_not_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3: a self-transition ``start_anonymization`` (source=ANONYMIZING) NÃO
    enfileira — sem o filtro, inline recursaria com o lock na mão e async
    duplicaria tasks."""
    calls: list[uuid.UUID] = []

    def _recorder(case_id: uuid.UUID) -> None:
        calls.append(case_id)

    monkeypatch.setattr("apps.anonymization.tasks.enqueue_case_anonymization", _recorder)
    user = _create_user("signal-self")
    case = Case.objects.create(created_by=user)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)

    # Entrada REAL: enfileira exatamente uma vez (source=PDF_EXTRACTING).
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    assert calls == [case.case_id]

    # Self-transition da própria task: NÃO re-dispara.
    case.start_anonymization(user=None, role=SYSTEM_ROLE)
    assert calls == [case.case_id]


@pytest.mark.django_db(transaction=True)
def test_signal_rollback_does_not_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """R3: transação que rollback NÃO enfileira — o enqueue via on_commit só
    roda no commit; o evento de entrada (e o callback) são descartados."""
    calls: list[uuid.UUID] = []

    def _recorder(case_id: uuid.UUID) -> None:
        calls.append(case_id)

    monkeypatch.setattr("apps.anonymization.tasks.enqueue_case_anonymization", _recorder)
    user = _create_user("signal-rollback")
    case = Case.objects.create(created_by=user)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    events_before = case.events.count()

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
            raise RuntimeError("boom no meio da transação")

    # Rollback: sem evento de entrada persistido e sem enqueue.
    assert calls == []
    case.refresh_from_db()
    assert case.status == CaseStatus.PDF_EXTRACTING
    assert case.events.count() == events_before

    # Fronteira de commit real depois do rollback: nenhum callback vazou.
    Case.objects.create(created_by=user)
    assert calls == []


# ── P1 (finding de review): cadeia inline completa intake → anonimização ──
# Em dev single-process (ambas as flags inline) o ``on_commit`` da transição
# ``complete_pdf_extraction`` roda a task de anonimização ainda DENTRO da task
# do pdf — com o lock ``worker_pdf`` ativo, o claim ``worker_anonymization``
# conflitava (caso wrongly FAILED). Correção decidida: a task-pdf libera a
# lease no MESMO ``transaction.atomic()`` da transição de saída — os hooks
# on_commit disparam só depois do release (apps/intake/tasks.py). Este teste de
# integração exercita a cadeia REAL via ``create_case_with_documents`` com PDF
# válido (gate ok) e transações de verdade (transaction=True): task do intake
# inline → on_commit → task de anonimização inline → caso em
# ``LLM_EXTRACTING`` com ``anonymized_text`` preenchido. RED antes da correção
# (conflito de lock → FAILED), GREEN depois.

# Storage em memória para a suíte da cadeia (mesmo padrão do apps.intake):
# os uploads do case não escrevem no MEDIA_ROOT.
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
def test_inline_full_chain_pdf_to_anonymization(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1: cadeia inline completa sem conflito — caso termina LLM_EXTRACTING.

    Ambas as flags inline (dev single-process): o on_commit da entrada em
    ANONYMIZING roda a anonimização ainda dentro da task do pdf, mas a lease
    ``worker_pdf`` já foi liberada no mesmo atomic da transição de saída — o
    claim ``worker_anonymization`` passa de primeira e o caso sai processado,
    nunca FAILED por conflito de lock.
    """
    user = _create_user("chain-inline")
    _stub_engine(monkeypatch)

    with override_settings(
        INTAKE_RUN_TASKS_INLINE=True,
        ANONYMIZATION_RUN_TASKS_INLINE=True,
        # O pipeline LLM não roda aqui (fora do contrato do change 05): a
        # entrada em LLM_EXTRACTING enfileira no broker (no-op em teste) e o
        # caso permanece LLM_EXTRACTING. A cadeia LLM completa inline é coberta
        # pelos testes do change 06 (slice 006) com fakes.
        LLM_RUN_TASKS_INLINE=False,
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
    assert case.anonymized_text != ""
    assert "<PESSOA_1>" in case.anonymized_text
    assert "MARIA DA SILVA SOUZA" not in case.anonymized_text
    assert case.pseudonym_map
    event_types = _event_types(case)
    assert CaseEventType.CASE_ANONYMIZATION_COMPLETED.value in event_types
    assert event_types.count(CaseEventType.CASE_STATUS_LLM_EXTRACTING.value) == 1
    assert CaseEventType.CASE_STATUS_FAILED.value not in event_types
