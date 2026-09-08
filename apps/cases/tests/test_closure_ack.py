"""Testes da ciência do NIR + limpeza transacional (slice 002, R1–R6).

Cobre ``acknowledge_case_receipt`` de ``apps/cases/closure.py``: o criador
confirma o recebimento da resposta final e o caso vai a ``CLEANED`` no MESMO
atomic — 3 eventos ``CASE_STATUS_*`` (``AWAITING_NIR_ACK``, ``CLEANING``,
``CLEANED``) e a minimização do D2 (rows ``CaseDocument`` deletadas; textos/
mapa de pseudônimos/artefatos LLM zerados; identificação, procedimentos,
trilha, comunicações, agendamento e ``anonymization_report`` preservados).
Arquivos físicos são removidos best-effort SÓ após o commit
(``transaction.on_commit`` — testes com ``django_db(transaction=True)``, com
os enqueues de pipeline silenciados como em ``test_signals.py``) e arquivos de
outros casos ficam intactos. Estado errado/não-criador → ``ValueError``
nomeado sem nenhuma escrita (R4). Caso ``CLEANED`` com decisão médica
(``doctor_decided_at``) dentro da janela segue retornado pelo prior-case (R5 —
a janela mede a DECISÃO da row prévia contra o ``created_at`` do caso novo,
não a idade da limpeza). Documento de caso limpo → 404 na rota
``intake:serve_document`` (R6, cenário da spec).

Storage em memória (``InMemoryStorage``): mesmo padrão do
``apps/intake/tests/conftest.py`` — uploads nunca tocam o ``MEDIA_ROOT``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import date, timedelta

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.closure import acknowledge_case_receipt, post_doctor_denial_reply
from apps.cases.events import case_status_event_type
from apps.cases.models import (
    Case,
    CaseDocument,
    CaseProcedure,
    CaseStatus,
    DoctorDisposition,
    SchedulingUnit,
)
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.pipeline.prior_case import lookup_prior_case_context
from apps.scheduler.services import confirm_case_scheduling

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SCHEDULER_ROLE = "scheduler"
SYSTEM_ROLE = "system"

# Tipos representativos do catálogo (mesmos do slice 001).
ANGIO_TYPE = "art_perif"
RADIO_TYPE = "nefrostomia"

# Identificação/linkage persistido (D2: preservado na limpeza).
RECORD_NUMBER = "33345"
PATIENT_NAME = "Maria da Silva"
PATIENT_BIRTH_DATE = date(1965, 4, 10)

# Conteúdo clínico/LLM a ser minimizado no ack (D2).
EXTRACTED_TEXT = "texto extraído do relatório — conteúdo clínico completo"
ANONYMIZED_TEXT = "texto anonimizado do relatório — sem PII"
ANONYMIZATION_REPORT = {"counts": {"PERSON": 1, "DATE": 2}, "model": "pt_core_news_lg"}
STRUCTURED_DATA = {"procedures": [{"procedure_type": ANGIO_TYPE, "evidence": "relato clínico"}]}
SUMMARY_TEXT = "Resumo clínico sintético gerado pelo LLM2."
SUGGESTED_ACTION = {"text": "Agendar arteriografia periférica."}
POLICY_RESULT = {"verdict": "allowed", "policy": "hemodinamica"}

PDF_CONTENT_TYPE = "application/pdf"

# Storage de teste: arquivos apenas em memória (nada de disco/MEDIA_ROOT).
_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.fixture(autouse=True)
def _memory_storage() -> Iterator[None]:
    """Storage default em memória durante cada teste do módulo."""
    with override_settings(STORAGES=_TEST_STORAGES):
        yield


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste."""
    return Client()


def _pdf(filename: str) -> SimpleUploadedFile:
    """PDF fake de upload (apenas metadados/content — o conteúdo não é lido)."""
    return SimpleUploadedFile(
        filename,
        b"%PDF-1.4 relatorio fake (conteudo nao lido neste slice)",
        content_type=PDF_CONTENT_TYPE,
    )


def _event_types(case: Case) -> list[str]:
    """Tipos de evento da trilha na ordem de gravação."""
    return [event.event_type for event in case.events.order_by("id")]


def _attach_documents(
    case: Case,
    uploaded_by: User,
    *filenames: str,
) -> list[CaseDocument]:
    """Anexa 1–N PDFs ao caso (arquivos no storage em memória)."""
    documents: list[CaseDocument] = []
    for position, filename in enumerate(filenames or ("relatorio-sesab.pdf",), start=1):
        uploaded = _pdf(filename)
        document = CaseDocument(
            case=case,
            position=position,
            original_filename=filename,
            content_type=PDF_CONTENT_TYPE,
            size_bytes=uploaded.size or 0,
            uploaded_by=uploaded_by,
        )
        # Grava o arquivo antes do INSERT para conhecer o nome no storage.
        document.file.save(document.original_filename, uploaded, save=False)
        document.save()
        documents.append(document)
    return documents


def _seed_identity_and_artifacts(case: Case) -> None:
    """Identidade + artefatos clínicos/LLM do caso (alvo da limpeza do D2)."""
    case.agency_record_number = RECORD_NUMBER
    case.patient_name = PATIENT_NAME
    case.patient_birth_date = PATIENT_BIRTH_DATE
    case.extracted_text = EXTRACTED_TEXT
    case.anonymized_text = ANONYMIZED_TEXT
    case.pseudonym_map = {"<PESSOA_1>": "MARIA DA SILVA SOUZA"}
    case.anonymization_report = ANONYMIZATION_REPORT
    case.structured_data = STRUCTURED_DATA
    case.summary_text = SUMMARY_TEXT
    case.suggested_action = SUGGESTED_ACTION
    case.policy_result = POLICY_RESULT
    case.save()


def _drive_to_awaiting_doctor(case: Case) -> None:
    """Dirige o caso pelas transições do pipeline até ``AWAITING_DOCTOR``."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _denial_final_case(
    *,
    created_by: User,
    decided_by: User,
    angio_reason: str = "sem indicação clínica para o procedimento",
    radio_reason: str = "risco cirúrgico elevado",
) -> Case:
    """Caso em ``FINAL_REPLY_POSTED`` pela negativa médica (fluxo real do 001).

    Cria o caso com as rows declaradas dos dois tipos, identidade/artefatos
    clínicos e documentos do fluxo de decisão da negativa: pipeline até
    ``AWAITING_DOCTOR`` → decisão médica (ambas negadas) → resposta final do
    slice 001 (comunicação autoral na thread).
    """
    case = Case.objects.create(created_by=created_by)
    for procedure_type in (ANGIO_TYPE, RADIO_TYPE):
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )
    _seed_identity_and_artifacts(case)
    _drive_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case,
        {
            ANGIO_TYPE: ("denied", angio_reason),
            RADIO_TYPE: ("denied", radio_reason),
        },
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    post_doctor_denial_reply(case, user=decided_by, role=DOCTOR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


def _confirmed_final_case(
    *,
    created_by: User,
    decided_by: User,
    scheduler_user: User,
) -> Case:
    """Caso em ``FINAL_REPLY_POSTED`` pelo agendamento confirmado (unidade 1).

    Fluxo real: pipeline até ``AWAITING_DOCTOR`` → decisão médica aprovada →
    fila do agendador → ``confirm_case_scheduling`` (campos ``scheduled_*``
    persistidos e resposta final posta na thread). É o estado com os campos de
    agendamento preenchidos — alvo do assert de preservação do D2.
    """
    case = Case.objects.create(created_by=created_by)
    for procedure_type in (ANGIO_TYPE, RADIO_TYPE):
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )
    _seed_identity_and_artifacts(case)
    _drive_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: ("approved", ""), RADIO_TYPE: ("approved", "")},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    confirm_case_scheduling(
        case,
        unit=SchedulingUnit.UNIT_1,
        scheduled_datetime=timezone.now() + timedelta(days=5),
        scheduled_location="HGRS — Hemodinâmica",
        user=scheduler_user,
        role=SCHEDULER_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    return case


def _silence_pipeline_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op nos enqueues de anonimização/pipeline (testes com commit real).

    Em ``django_db(transaction=True)`` os callbacks ``on_commit`` dos eventos
    das transições do pipeline disparam de verdade e executariam a cadeia
    inline (engine spaCy/LLM) — fora do contrato deste slice. Mesmo padrão do
    ``apps/pipeline/tests/test_signals.py``.
    """

    def _noop(case_id: uuid.UUID) -> None:
        del case_id

    monkeypatch.setattr("apps.anonymization.tasks.enqueue_case_anonymization", _noop)
    monkeypatch.setattr("apps.pipeline.tasks.enqueue_case_pipeline", _noop)


# ── R1: o ack encadeia as 3 transições no mesmo atomic até CLEANED ─────────


@pytest.mark.django_db
def test_ack_chains_to_cleaned(nir_user: User, doctor_user: User) -> None:
    """R1: criador confirma → CLEANED com 3 eventos CASE_STATUS_* na ordem
    (AWAITING_NIR_ACK ← FINAL_REPLY_POSTED, CLEANING ← AWAITING_NIR_ACK,
    CLEANED ← CLEANING), ator/papel do NIR, no mesmo atomic."""
    case = _denial_final_case(created_by=nir_user, decided_by=doctor_user)
    _attach_documents(case, nir_user, "relatorio-sesab.pdf")
    events_before = case.events.count()

    acknowledge_case_receipt(case, user=nir_user, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    assert fresh.events.count() == events_before + 3
    assert _event_types(fresh)[-3:] == [
        case_status_event_type(CaseStatus.AWAITING_NIR_ACK),
        case_status_event_type(CaseStatus.CLEANING),
        case_status_event_type(CaseStatus.CLEANED),
    ]
    ack_events = list(fresh.events.order_by("id"))[-3:]
    assert [event.payload["source"] for event in ack_events] == [
        CaseStatus.FINAL_REPLY_POSTED.value,
        CaseStatus.AWAITING_NIR_ACK.value,
        CaseStatus.CLEANING.value,
    ]
    assert [event.payload["target"] for event in ack_events] == [
        CaseStatus.AWAITING_NIR_ACK.value,
        CaseStatus.CLEANING.value,
        CaseStatus.CLEANED.value,
    ]
    assert all(event.actor == nir_user for event in ack_events)
    assert all(event.actor_role == NIR_ROLE for event in ack_events)


# ── R2: limpeza remove o conjunto do D2 e preserva o resto ────────────────


@pytest.mark.django_db
def test_cleanup_removes_clinical_keeps_essential(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R2: rows CaseDocument deletadas; extracted_text/anonymized_text/
    pseudonym_map/structured_data/summary_text/suggested_action/policy_result
    zerados; identificação, anonymization_report, rows CaseProcedure,
    CaseEvent, comunicações e scheduled_* preservados (asserts explícitos)."""
    scheduler_user = user_factory("scheduler-cleanup", SCHEDULER_ROLE)
    case = _confirmed_final_case(
        created_by=nir_user,
        decided_by=doctor_user,
        scheduler_user=scheduler_user,
    )
    _attach_documents(case, nir_user, "relatorio-sesab.pdf", "pagina-2.pdf")
    case.refresh_from_db()
    report_before = dict(case.anonymization_report)
    events_before = case.events.count()
    communication_ids_before = sorted(
        case.communication_messages.values_list("message_id", flat=True)
    )
    rows_before = {
        row.procedure_type: (row.doctor_disposition, row.doctor_reason, row.doctor_decided_at)
        for row in case.procedures.all()
    }

    acknowledge_case_receipt(case, user=nir_user, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    # Remove: rows CaseDocument e os 7 campos clínicos/LLM.
    assert list(fresh.documents.values_list("pk", flat=True)) == []
    assert fresh.extracted_text == ""
    assert fresh.anonymized_text == ""
    assert fresh.pseudonym_map == {}
    assert fresh.structured_data == {}
    assert fresh.summary_text == ""
    assert fresh.suggested_action == {}
    assert fresh.policy_result == {}
    # Preserva: identificação (linkage do prior-case) e relatório de
    # anonimização (métrica de auditoria, não-PHI — D2).
    assert fresh.agency_record_number == RECORD_NUMBER
    assert fresh.patient_name == PATIENT_NAME
    assert fresh.patient_birth_date == PATIENT_BIRTH_DATE
    assert fresh.anonymization_report == report_before
    # Preserva: rows CaseProcedure com decisões/motivos e a trilha (auditoria).
    rows_after = {
        row.procedure_type: (row.doctor_disposition, row.doctor_reason, row.doctor_decided_at)
        for row in fresh.procedures.all()
    }
    assert rows_after == rows_before
    assert fresh.events.count() == events_before + 3
    # Preserva: thread de comunicações e campos de agendamento.
    communication_ids_after = sorted(
        fresh.communication_messages.values_list("message_id", flat=True)
    )
    assert communication_ids_after == communication_ids_before
    assert fresh.scheduled_unit == SchedulingUnit.UNIT_1
    assert fresh.scheduled_datetime is not None
    assert fresh.scheduled_location == "HGRS — Hemodinâmica"
    assert fresh.scheduled_by == scheduler_user
    assert fresh.scheduled_decided_at is not None
    assert fresh.scheduling_denial_reason == ""
    assert fresh.scheduling_reopen_reason == ""


# ── R4: estado errado e não-criador → erros nomeados sem escrita ──────────


@pytest.mark.django_db
def test_ack_wrong_state_rejected(nir_user: User) -> None:
    """R4: caso fora de FINAL_REPLY_POSTED é recusado com erro nomeado — sem
    transições, sem deleção de documentos, sem zeragem de campos."""
    case = Case.objects.create(created_by=nir_user)
    for procedure_type in (ANGIO_TYPE, RADIO_TYPE):
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )
    _seed_identity_and_artifacts(case)
    _drive_to_awaiting_doctor(case)
    _attach_documents(case, nir_user, "relatorio-sesab.pdf")
    events_before = case.events.count()
    document_ids_before = list(case.documents.values_list("pk", flat=True))

    with pytest.raises(ValueError, match="FINAL_REPLY_POSTED"):
        acknowledge_case_receipt(case, user=nir_user, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.AWAITING_DOCTOR
    assert fresh.events.count() == events_before
    assert list(fresh.documents.values_list("pk", flat=True)) == document_ids_before
    assert fresh.extracted_text == EXTRACTED_TEXT
    assert fresh.anonymized_text == ANONYMIZED_TEXT
    assert fresh.pseudonym_map == {"<PESSOA_1>": "MARIA DA SILVA SOUZA"}
    assert fresh.summary_text == SUMMARY_TEXT


@pytest.mark.django_db
def test_ack_non_creator_rejected(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R4: quem não é o criador não confirma — erro nomeado sem nenhuma escrita."""
    case = _denial_final_case(created_by=nir_user, decided_by=doctor_user)
    _attach_documents(case, nir_user, "relatorio-sesab.pdf")
    other_nir = user_factory("nir-alheio-ack", NIR_ROLE)
    events_before = case.events.count()
    document_ids_before = list(case.documents.values_list("pk", flat=True))

    with pytest.raises(ValueError, match="criador"):
        acknowledge_case_receipt(case, user=other_nir, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.FINAL_REPLY_POSTED
    assert fresh.events.count() == events_before
    assert list(fresh.documents.values_list("pk", flat=True)) == document_ids_before
    assert fresh.extracted_text == EXTRACTED_TEXT


# ── R3: arquivos físicos deletados após o commit; outros casos intactos ────


@pytest.mark.django_db(transaction=True)
def test_ack_deletes_physical_files_after_commit(
    monkeypatch: pytest.MonkeyPatch,
    nir_user: User,
    doctor_user: User,
) -> None:
    """R3: a deleção física roda APÓS o commit real (on_commit dispara em
    transaction=True) — os arquivos do caso somem do storage em memória."""
    _silence_pipeline_enqueues(monkeypatch)
    case = _denial_final_case(created_by=nir_user, decided_by=doctor_user)
    _attach_documents(case, nir_user, "relatorio-sesab.pdf", "pagina-2.pdf")
    file_names = [d.file.name for d in case.documents.all() if d.file.name]
    assert len(file_names) == 2
    assert all(default_storage.exists(name) for name in file_names)

    acknowledge_case_receipt(case, user=nir_user, role=NIR_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    assert fresh.documents.count() == 0
    assert not any(default_storage.exists(name) for name in file_names)


@pytest.mark.django_db(transaction=True)
def test_ack_other_case_files_untouched(
    monkeypatch: pytest.MonkeyPatch,
    nir_user: User,
    doctor_user: User,
) -> None:
    """R3: a limpeza remove apenas os arquivos do caso ackado — os arquivos de
    outros casos permanecem no storage."""
    _silence_pipeline_enqueues(monkeypatch)
    acked_case = _denial_final_case(created_by=nir_user, decided_by=doctor_user)
    other_case = _denial_final_case(created_by=nir_user, decided_by=doctor_user)
    acked_files = [
        d.file.name for d in _attach_documents(acked_case, nir_user, "ack.pdf") if d.file.name
    ]
    other_files = [
        d.file.name for d in _attach_documents(other_case, nir_user, "outro.pdf") if d.file.name
    ]
    assert all(default_storage.exists(name) for name in other_files)

    acknowledge_case_receipt(acked_case, user=nir_user, role=NIR_ROLE)

    assert not any(default_storage.exists(name) for name in acked_files)
    assert all(default_storage.exists(name) for name in other_files)
    other_fresh = Case.objects.get(pk=other_case.case_id)
    assert other_fresh.status == CaseStatus.FINAL_REPLY_POSTED
    assert list(other_fresh.documents.values_list("original_filename", flat=True)) == ["outro.pdf"]


# ── R5: caso limpo (CLEANED) segue retornado pelo prior-case ──────────────


@pytest.mark.django_db
def test_cleaned_case_still_prior_case(
    nir_user: User,
    doctor_user: User,
) -> None:
    """R5: caso CLEANED com decisão médica dentro da janela segue no
    prior-case de um novo caso do mesmo paciente/tipo (nº de registro igual).
    A janela mede ``doctor_decided_at`` da row prévia contra o ``created_at``
    do novo caso (7d/15d) — o setup ancora na DECISÃO, não no fechamento."""
    prior = _denial_final_case(
        created_by=nir_user,
        decided_by=doctor_user,
        angio_reason="",
        radio_reason="",
    )
    decided_at = CaseProcedure.objects.get(case=prior, procedure_type=ANGIO_TYPE).doctor_decided_at
    assert decided_at is not None

    acknowledge_case_receipt(prior, user=nir_user, role=NIR_ROLE)

    prior.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    # A limpeza preservou a row com a decisão (auditoria + prior-case).
    row = CaseProcedure.objects.get(case=prior, procedure_type=ANGIO_TYPE)
    assert row.doctor_disposition == DoctorDisposition.DENIED
    assert row.doctor_decided_at == decided_at

    # Novo caso do mesmo paciente/nº, criado logo após a decisão (janela de
    # `PRIOR_CASE_WINDOW_DAYS` medida de doctor_decided_at → created_at).
    current = Case.objects.create(created_by=nir_user)
    current.agency_record_number = RECORD_NUMBER
    current.patient_name = PATIENT_NAME
    current.patient_birth_date = PATIENT_BIRTH_DATE
    current.save(update_fields=["agency_record_number", "patient_name", "patient_birth_date"])
    CaseProcedure.objects.create(case=current, procedure_type=ANGIO_TYPE, declared_by_nir=True)

    summary = lookup_prior_case_context(current, ANGIO_TYPE)

    assert summary is not None
    assert summary.prior_case_id == str(prior.case_id)
    assert summary.decision == DoctorDisposition.DENIED
    assert summary.origin == "occurrence_number"
    assert summary.decided_at == decided_at.isoformat()


# ── R6: documento de caso limpo → 404 na rota do intake ───────────────────


@pytest.mark.django_db
def test_serve_document_404_after_cleanup(
    client: Client,
    nir_user: User,
    doctor_user: User,
) -> None:
    """R6 (cenário da spec): o mesmo documento que a rota servia (200) vira
    404 após o ack — as rows CaseDocument não existem mais."""
    case = _denial_final_case(created_by=nir_user, decided_by=doctor_user)
    document = _attach_documents(case, nir_user, "relatorio-sesab.pdf")[0]
    url = reverse("intake:serve_document", args=[case.case_id, document.pk])

    client.force_login(nir_user)
    assert client.get(url).status_code == 200

    acknowledge_case_receipt(case, user=nir_user, role=NIR_ROLE)

    assert Case.objects.get(pk=case.case_id).status == CaseStatus.CLEANED
    assert client.get(url).status_code == 404
