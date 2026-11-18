"""Testes da purga retroativa dos dados retidos (slice 003, R1–R3 + revisão D4).

Cobre o serviço puro ``purge_closed_case_data`` (``apps/cases/closure.py``) e o
comando fino ``purge_closed_case_data [--dry-run]``
(``apps/cases/management/commands/purge_closed_case_data.py``).

Partição (revisão D4 — commit real obrigatório + trilha em duas fases):

- ``TestCase`` (funções pytest, transação externa de teste): somente seleção/
  relatório sem chamar o serviço — ativo intocado, já-minimizado pulado e
  ``--dry-run``. O comando nunca invoca o serviço nesses caminhos.
- ``TransactionTestCase`` (commits reais): todo teste que purga de verdade
  (serviço ou comando) — o atomic do serviço é ``durable=True`` (deleção
  física só após commit real, nunca sob savepoint). Molde das classes de
  durabilidade do slice 001 (``apps/attachments/tests/test_closure_worker_abort.py``).
  Como o flush de ``TransactionTestCase`` apaga seeds de migration (quirk
  ``--reuse-db``), o usuário é recriado com ``get_or_create`` em cada ``setUp``.

Trilha em duas fases (D4): evento de purga ATÔMICO com a limpeza (nomes dos
campos + rows + ``files_attempted``) + evento complementar de desfecho físico
(apenas quando houve arquivos, payload ``files_deleted``/``files_failed``).
Re-execução é no-op (sem eventos novos). Log sem PHI: falha de arquivo loga
referência + contagem + tipo da exceção, sem caminho/exceção/traceback.

Storage em memória (``InMemoryStorage``) — uploads nunca tocam o ``MEDIA_ROOT``.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import transaction
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.attachments.models import AttachmentStatus, CaseAttachment
from apps.cases.closure import purge_closed_case_data
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseDocument, CaseEvent, CaseStatus
from apps.dashboard.event_labels import event_label

SYSTEM_ROLE = "system"

# Identificação persistida (preservada pela purga) e conteúdo clínico retido
# (alvo da purga). Os marcadores ``*-SECRETO-*``/``arquivo-secreto`` provam
# que payload/saída/log não vazam PHI: asserts de ausência abaixo.
RECORD_NUMBER = "33345"
EXTRACTED_TEXT = "TEXTO-CLINICO-SECRETO-purga-001 completo do relatório"
ANONYMIZED_TEXT = "TEXTO-ANONIMIZADO-SECRETO-purga"
STRUCTURED_DATA = {"procedures": [{"procedure_type": "art_perif", "evidence": "relato"}]}
SUMMARY_TEXT = "RESUMO-SECRETO-purga"
SUGGESTED_ACTION = {"text": "Agendar arteriografia periférica."}
POLICY_RESULT = {"verdict": "allowed", "policy": "hemodinamica"}
DOCUMENT_FILENAME = "arquivo-secreto-relatorio.pdf"
ATTACHMENT_FILENAME = "arquivo-secreto-anexo.jpg"

PDF_CONTENT_TYPE = "application/pdf"

# Storage de teste: arquivos apenas em memória (nada de disco/MEDIA_ROOT).
_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Os 7 campos clínicos/LLM zerados (``_CLEANED_EMPTY_VALUES``) — payload carrega
# os NOMES, nunca os valores.
CLEANED_FIELDS = {
    "extracted_text",
    "anonymized_text",
    "pseudonym_map",
    "structured_data",
    "summary_text",
    "suggested_action",
    "policy_result",
}

# Payloads exatos das duas fases (D4 — sem chaves extras, sem filename).
PURGE_PAYLOAD_KEYS = {
    "cleaned_fields",
    "documents_deleted",
    "attachments_deleted",
    "files_attempted",
}
OUTCOME_PAYLOAD_KEYS = {"files_deleted", "files_failed"}


@pytest.fixture(autouse=True)
def _memory_storage() -> Iterator[None]:
    """Storage default em memória durante cada teste em função do módulo."""
    with override_settings(STORAGES=_TEST_STORAGES):
        yield


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste."""
    return Client()


def _make_user(username: str, role_name: str = "nir") -> User:
    """Usuário com papel (``get_or_create`` — sobrevive ao flush do TransactionTestCase)."""
    role, _ = Role.objects.get_or_create(name=role_name)
    user = User.objects.create_user(username=username, password="senha-teste")
    user.roles.add(role)
    return user


def _make_cleaned_case(created_by: User) -> Case:
    """Caso ``CLEANED`` pelas transições reais (sem dados — semente depois)."""
    case = Case.objects.create(created_by=created_by)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    case.record_doctor_decision(False, user=None, role=SYSTEM_ROLE)
    case.post_final_reply(user=None, role=SYSTEM_ROLE)
    case.nir_acknowledge(user=None, role=SYSTEM_ROLE)
    case.start_cleaning(user=None, role=SYSTEM_ROLE)
    case.complete_cleaning(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.CLEANED
    return case


def _seed_retained_clinical_data(case: Case) -> None:
    """Campos clínicos/LLM retidos (como se encerrado em modo retenção)."""
    case.agency_record_number = RECORD_NUMBER
    case.extracted_text = EXTRACTED_TEXT
    case.anonymized_text = ANONYMIZED_TEXT
    case.pseudonym_map = {"<PESSOA_1>": "MARIA DA SILVA"}
    case.structured_data = STRUCTURED_DATA
    case.summary_text = SUMMARY_TEXT
    case.suggested_action = SUGGESTED_ACTION
    case.policy_result = POLICY_RESULT
    case.save(
        update_fields=[
            "agency_record_number",
            "extracted_text",
            "anonymized_text",
            "pseudonym_map",
            "structured_data",
            "summary_text",
            "suggested_action",
            "policy_result",
        ]
    )


def _attach_document(case: Case, uploaded_by: User) -> CaseDocument:
    """Documento PDF retido do caso (arquivo no storage em memória)."""
    uploaded = SimpleUploadedFile(
        DOCUMENT_FILENAME,
        b"%PDF-1.4 relatorio retido (conteudo nao lido neste slice)",
        content_type=PDF_CONTENT_TYPE,
    )
    document = CaseDocument(
        case=case,
        position=1,
        original_filename=DOCUMENT_FILENAME,
        content_type=PDF_CONTENT_TYPE,
        size_bytes=uploaded.size or 0,
        uploaded_by=uploaded_by,
    )
    document.file.save(document.original_filename, uploaded, save=False)
    document.save()
    return document


def _attach_attachment(case: Case, uploaded_by: User) -> CaseAttachment:
    """Anexo clínico retido do caso (arquivo no storage em memória)."""
    uploaded = SimpleUploadedFile(
        ATTACHMENT_FILENAME,
        b"anexo clinico retido (conteudo nao lido neste slice)",
        content_type="image/jpeg",
    )
    attachment = CaseAttachment(
        case=case,
        original_filename=ATTACHMENT_FILENAME,
        content_type="image/jpeg",
        size_bytes=uploaded.size or 0,
        uploaded_by=uploaded_by,
        status=AttachmentStatus.PROCESSED,
        extracted_text="texto do anexo — conteúdo clínico do exame",
        anonymized_text="texto anonimizado do anexo",
        pseudonym_map={"<PACIENTE_1>": "Maria da Silva"},
        processed_at=timezone.now(),
    )
    attachment.file.save(ATTACHMENT_FILENAME, uploaded, save=False)
    attachment.save()
    return attachment


def _purge_events(case: Case) -> list[CaseEvent]:
    """Eventos de purga da trilha do caso, em ordem (fase 1 + desfecho)."""
    return list(
        case.events.filter(
            event_type__in=[
                CaseEventType.CASE_RETAINED_DATA_PURGED,
                CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME,
            ]
        ).order_by("id")
    )


def _run_command(*, dry_run: bool = False) -> str:
    """Roda o comando e devolve a saída (propaga ``CommandError``)."""
    out = io.StringIO()
    options: dict[str, object] = {"stdout": out}
    if dry_run:
        options["dry_run"] = True
    call_command("purge_closed_case_data", **options)
    return out.getvalue()


def _assert_no_phi(blob: str) -> None:
    """Blob SEM PHI: nem conteúdo clínico nem nomes de arquivo."""
    assert EXTRACTED_TEXT not in blob
    assert ANONYMIZED_TEXT not in blob
    assert SUMMARY_TEXT not in blob
    assert DOCUMENT_FILENAME not in blob
    assert ATTACHMENT_FILENAME not in blob


def _assert_no_phi_in_payload_and_output(payload: dict[str, object], output: str = "") -> None:
    """Payload e saída SEM PHI (compatibilidade com os testes em função)."""
    _assert_no_phi(repr(payload) + output)


def _assert_system_actor(event: CaseEvent) -> None:
    """Evento de purga com ator Sistema (trilha exibe "Sistema")."""
    assert event.actor is None
    assert event.actor_type == ActorType.SYSTEM
    assert event.actor_role == SYSTEM_ROLE


def _failing_delete(name: str) -> None:
    """Storage que falha fisicamente (OSError) — a mensagem carrega o nome de
    propósito: o log NÃO pode repeti-la (P2 — sem PHI no log)."""
    raise OSError(f"falha simulada ao remover {name}")


def _noop_enqueue(case_id: object) -> None:
    """No-op nos enqueues de anonimização/pipeline (commits reais).

    Em ``TransactionTestCase`` os callbacks ``on_commit`` das transições do
    setup disparam de verdade e executariam a cadeia inline (engine spaCy/LLM)
    — fora do contrato da purga. Mesmo padrão de ``test_administrative_closure``.
    """
    del case_id


# ── TestCase (sem chamar o serviço): seleção/relatório ──────────────────────


@pytest.mark.django_db
def test_purge_command_does_not_touch_active_case(nir_user: User) -> None:
    """R3 (gate 1): caso ativo populado é ignorado — dados, rows e arquivos
    intactos, sem evento, e a saída não o lista como purgado."""
    case = Case.objects.create(created_by=nir_user)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    _seed_retained_clinical_data(case)
    document = _attach_document(case, nir_user)
    _attach_attachment(case, nir_user)
    document_name = document.file.name
    assert document_name
    events_before = case.events.count()

    output = _run_command()

    fresh = Case.objects.get(pk=case.pk)
    assert fresh.status == CaseStatus.ANONYMIZING
    assert fresh.extracted_text == EXTRACTED_TEXT
    assert fresh.documents.count() == 1
    assert fresh.attachments.count() == 1
    assert default_storage.exists(document_name)
    assert fresh.events.count() == events_before
    assert _purge_events(fresh) == []
    assert case.display_reference not in output


@pytest.mark.django_db
def test_purge_command_skips_already_minimized_cleaned_case(nir_user: User) -> None:
    """R3: ``CLEANED`` sem dados retidos (já minimizado) é pulado, sem evento."""
    case = _make_cleaned_case(nir_user)
    events_before = case.events.count()

    output = _run_command()

    fresh = Case.objects.get(pk=case.pk)
    assert fresh.status == CaseStatus.CLEANED
    assert fresh.events.count() == events_before
    assert _purge_events(fresh) == []
    assert case.display_reference not in output


@pytest.mark.django_db
def test_purge_command_dry_run_reports_without_changing(nir_user: User) -> None:
    """R3: ``--dry-run`` lista referência + contagens sem alterar nada."""
    case = _make_cleaned_case(nir_user)
    _seed_retained_clinical_data(case)
    document = _attach_document(case, nir_user)
    attachment = _attach_attachment(case, nir_user)
    document_name = document.file.name
    attachment_name = attachment.file.name
    assert document_name
    assert attachment_name
    events_before = case.events.count()

    output = _run_command(dry_run=True)

    fresh = Case.objects.get(pk=case.pk)
    assert case.display_reference in output
    assert "dry-run" in output.lower()
    assert fresh.extracted_text == EXTRACTED_TEXT
    assert fresh.documents.count() == 1
    assert fresh.attachments.count() == 1
    assert default_storage.exists(document_name)
    assert default_storage.exists(attachment_name)
    assert fresh.events.count() == events_before
    assert _purge_events(fresh) == []
    _assert_no_phi_in_payload_and_output({}, output)


# ── TransactionTestCase: serviço (commit real + durable) ────────────────────


class TestPurgeServiceTransaction(TransactionTestCase):
    """Serviço em ``TransactionTestCase`` (commits reais, sem transação externa).

    O atomic do serviço é ``durable=True``: sem transação externa a purga
    commita de verdade e a deleção física roda após o commit.
    """

    def setUp(self) -> None:
        self._storage_override = override_settings(STORAGES=_TEST_STORAGES)
        self._storage_override.enable()
        self.addCleanup(self._storage_override.disable)
        anonymization_patch = patch(
            "apps.anonymization.tasks.enqueue_case_anonymization", _noop_enqueue
        )
        pipeline_patch = patch("apps.pipeline.tasks.enqueue_case_pipeline", _noop_enqueue)
        anonymization_patch.start()
        pipeline_patch.start()
        self.addCleanup(anonymization_patch.stop)
        self.addCleanup(pipeline_patch.stop)
        self.nir_user: User = _make_user("nir-teste")

    def test_purge_service_minimizes_retained_cleaned_case(self) -> None:
        """R3/D4: zera os 7 campos, remove rows e arquivos e grava as DUAS
        fases — purga atômica (nomes+rows+files_attempted) e desfecho físico
        (files_deleted/files_failed) — ambas com ator Sistema, sem PHI."""
        case = _make_cleaned_case(self.nir_user)
        _seed_retained_clinical_data(case)
        document = _attach_document(case, self.nir_user)
        attachment = _attach_attachment(case, self.nir_user)
        document_name = document.file.name
        attachment_name = attachment.file.name
        assert document_name and attachment_name
        assert default_storage.exists(document_name)
        assert default_storage.exists(attachment_name)
        events_before = case.events.count()

        result = purge_closed_case_data(case)

        assert result.documents_deleted == 1
        assert result.attachments_deleted == 1
        assert result.files_attempted == 2
        assert result.files_deleted == 2
        assert result.files_failed == 0
        fresh = Case.objects.get(pk=case.pk)
        assert fresh.status == CaseStatus.CLEANED
        assert fresh.extracted_text == ""
        assert fresh.anonymized_text == ""
        assert fresh.pseudonym_map == {}
        assert fresh.structured_data == {}
        assert fresh.summary_text == ""
        assert fresh.suggested_action == {}
        assert fresh.policy_result == {}
        # Identificação preservada (paridade com a minimização do encerramento).
        assert fresh.agency_record_number == RECORD_NUMBER
        assert fresh.documents.count() == 0
        assert fresh.attachments.count() == 0
        assert not default_storage.exists(document_name)
        assert not default_storage.exists(attachment_name)
        assert fresh.events.count() == events_before + 2
        events = _purge_events(fresh)
        assert len(events) == 2
        purge_event, outcome_event = events[0], events[1]
        assert purge_event.event_type == CaseEventType.CASE_RETAINED_DATA_PURGED
        assert outcome_event.event_type == CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME
        _assert_system_actor(purge_event)
        _assert_system_actor(outcome_event)
        assert set(purge_event.payload) == PURGE_PAYLOAD_KEYS
        assert set(purge_event.payload["cleaned_fields"]) == CLEANED_FIELDS
        assert purge_event.payload["documents_deleted"] == 1
        assert purge_event.payload["attachments_deleted"] == 1
        assert purge_event.payload["files_attempted"] == 2
        assert set(outcome_event.payload) == OUTCOME_PAYLOAD_KEYS
        assert outcome_event.payload["files_deleted"] == 2
        assert outcome_event.payload["files_failed"] == 0
        _assert_no_phi(repr(purge_event.payload) + repr(outcome_event.payload))
        assert event_label(purge_event.event_type) == "Dados retidos purgados"

    def test_purge_without_files_records_only_purge_event(self) -> None:
        """D4: sem arquivos (só campos), há só o evento de purga — sem fase 2."""
        case = _make_cleaned_case(self.nir_user)
        _seed_retained_clinical_data(case)
        events_before = case.events.count()

        result = purge_closed_case_data(case)

        assert result.files_attempted == 0
        assert result.files_deleted == 0
        assert result.files_failed == 0
        fresh = Case.objects.get(pk=case.pk)
        assert fresh.extracted_text == ""
        assert fresh.events.count() == events_before + 1
        events = _purge_events(fresh)
        assert len(events) == 1
        assert events[0].event_type == CaseEventType.CASE_RETAINED_DATA_PURGED
        assert set(events[0].payload) == PURGE_PAYLOAD_KEYS
        assert events[0].payload["files_attempted"] == 0

    def test_purge_service_rejects_non_cleaned_case(self) -> None:
        """Serviço fail-closed: caso fora de ``CLEANED`` é recusado sem escrita."""
        case = Case.objects.create(created_by=self.nir_user)
        _seed_retained_clinical_data(case)
        events_before = case.events.count()

        with self.assertRaisesRegex(ValueError, "CLEANED"):
            purge_closed_case_data(case)

        fresh = Case.objects.get(pk=case.pk)
        assert fresh.extracted_text == EXTRACTED_TEXT
        assert fresh.events.count() == events_before

    def test_purge_service_requires_real_commit(self) -> None:
        """P1-2/D4: chamada dentro de transação externa levanta RuntimeError
        (durable) e NADA é purgado/apagado — arquivo e banco intactos."""
        case = _make_cleaned_case(self.nir_user)
        _seed_retained_clinical_data(case)
        document = _attach_document(case, self.nir_user)
        document_name = document.file.name
        assert document_name
        assert default_storage.exists(document_name)
        events_before = case.events.count()

        with self.assertRaisesRegex(RuntimeError, "durable atomic block cannot be nested"):
            with transaction.atomic():
                purge_closed_case_data(case)

        fresh = Case.objects.get(pk=case.pk)
        assert fresh.extracted_text == EXTRACTED_TEXT
        assert fresh.documents.count() == 1
        assert default_storage.exists(document_name)
        assert fresh.events.count() == events_before
        assert _purge_events(fresh) == []

    def test_purge_service_reports_files_failed(self) -> None:
        """Serviço: falha física vira contagem — ``files_failed > 0`` no resultado
        e no evento de desfecho, sem propagar a exceção ao chamador."""
        case = _make_cleaned_case(self.nir_user)
        _seed_retained_clinical_data(case)
        document = _attach_document(case, self.nir_user)
        assert document.file.name

        with patch.object(default_storage, "delete", _failing_delete):
            result = purge_closed_case_data(case)

        assert result.files_attempted == 1
        assert result.files_deleted == 0
        assert result.files_failed == 1
        fresh = Case.objects.get(pk=case.pk)
        assert fresh.extracted_text == ""
        events = _purge_events(fresh)
        assert len(events) == 2
        assert events[0].event_type == CaseEventType.CASE_RETAINED_DATA_PURGED
        assert events[1].event_type == CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME
        assert set(events[0].payload) == PURGE_PAYLOAD_KEYS
        assert events[0].payload["files_attempted"] == 1
        assert set(events[1].payload) == OUTCOME_PAYLOAD_KEYS
        assert events[1].payload["files_failed"] == 1
        assert events[1].payload["files_deleted"] == 0

    def test_log_does_not_expose_filename(self) -> None:
        """P2/D4: falha de arquivo loga referência + tipo — sem caminho, sem
        mensagem da exceção, sem traceback (sem filename nos registros)."""
        case = _make_cleaned_case(self.nir_user)
        _seed_retained_clinical_data(case)
        document = _attach_document(case, self.nir_user)
        document_name = document.file.name
        assert document_name

        with (
            patch.object(default_storage, "delete", _failing_delete),
            self.assertLogs("apps.cases.closure", level="WARNING") as logs,
        ):
            purge_closed_case_data(case)

        log_text = "\n".join(logs.output)
        assert "OSError" in log_text  # tipo da exceção, sem mensagem/traceback
        assert case.display_reference in log_text
        _assert_no_phi(log_text)
        assert document_name not in log_text
        for record in logs.records:
            assert record.exc_info is None
            assert record.exc_text is None


# ── TransactionTestCase: comando (commit real + durable) ────────────────────


class TestPurgeCommandTransaction(TransactionTestCase):
    """Comando fino em ``TransactionTestCase`` (cada caso é um atomic durável)."""

    def setUp(self) -> None:
        self._storage_override = override_settings(STORAGES=_TEST_STORAGES)
        self._storage_override.enable()
        self.addCleanup(self._storage_override.disable)
        anonymization_patch = patch(
            "apps.anonymization.tasks.enqueue_case_anonymization", _noop_enqueue
        )
        pipeline_patch = patch("apps.pipeline.tasks.enqueue_case_pipeline", _noop_enqueue)
        anonymization_patch.start()
        pipeline_patch.start()
        self.addCleanup(anonymization_patch.stop)
        self.addCleanup(pipeline_patch.stop)
        self.nir_user: User = _make_user("nir-teste")

    def test_purge_command_minimizes_and_serves_404(self) -> None:
        """R3 (comando): caso retido é minimizado (2 fases) e o documento vira 404."""
        case = _make_cleaned_case(self.nir_user)
        _seed_retained_clinical_data(case)
        document = _attach_document(case, self.nir_user)
        _attach_attachment(case, self.nir_user)
        serve_url = reverse("intake:serve_document", args=[case.case_id, document.pk])
        client = Client()
        client.force_login(self.nir_user)
        assert client.get(serve_url).status_code == 200

        output = _run_command()

        fresh = Case.objects.get(pk=case.pk)
        assert fresh.extracted_text == ""
        assert fresh.documents.count() == 0
        assert fresh.attachments.count() == 0
        events = _purge_events(fresh)
        assert len(events) == 2
        assert events[0].event_type == CaseEventType.CASE_RETAINED_DATA_PURGED
        assert events[1].event_type == CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME
        assert set(events[0].payload) == PURGE_PAYLOAD_KEYS
        assert set(events[1].payload) == OUTCOME_PAYLOAD_KEYS
        assert case.display_reference in output
        _assert_no_phi(repr(events[0].payload) + repr(events[1].payload) + output)
        assert client.get(serve_url).status_code == 404

    def test_purge_command_is_idempotent(self) -> None:
        """R3/D4: segunda execução é no-op — as DUAS fases da 1ª execução
        permanecem e nenhum evento novo é criado."""
        case = _make_cleaned_case(self.nir_user)
        _seed_retained_clinical_data(case)
        _attach_document(case, self.nir_user)
        _attach_attachment(case, self.nir_user)

        first = _run_command()
        assert case.display_reference in first
        second = _run_command()

        fresh = Case.objects.get(pk=case.pk)
        purge_events = _purge_events(fresh)
        assert len(purge_events) == 2
        assert purge_events[0].event_type == CaseEventType.CASE_RETAINED_DATA_PURGED
        assert purge_events[1].event_type == CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME
        assert case.display_reference not in second

    def test_purge_physical_failure_is_reported(self) -> None:
        """R3: ``delete`` com ``OSError`` → banco commitado mas caso incompleto
        com ``files_failed > 0`` no desfecho e comando com exit não-zero."""
        case = _make_cleaned_case(self.nir_user)
        _seed_retained_clinical_data(case)
        document = _attach_document(case, self.nir_user)
        document_name = document.file.name
        assert document_name
        assert default_storage.exists(document_name)

        out = io.StringIO()
        with (
            patch.object(default_storage, "delete", _failing_delete),
            self.assertRaises(CommandError) as raised,
        ):
            call_command("purge_closed_case_data", stdout=out)
        output = out.getvalue()

        fresh = Case.objects.get(pk=case.pk)
        # Banco commitou: campos zerados e rows removidas mesmo com falha física.
        assert fresh.extracted_text == ""
        assert fresh.documents.count() == 0
        # Arquivo órfão permanece (falha real) e o caso é sinalizado incompleto.
        assert default_storage.exists(document_name)
        assert "INCOMPLETA" in output
        assert case.display_reference in output
        assert case.display_reference in str(raised.exception)
        events = _purge_events(fresh)
        assert len(events) == 2
        assert events[0].event_type == CaseEventType.CASE_RETAINED_DATA_PURGED
        assert events[1].event_type == CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME
        assert events[0].payload["files_attempted"] == 1
        assert events[1].payload["files_failed"] == 1
        assert events[1].payload["files_deleted"] == 0
        _assert_no_phi(repr(events[0].payload) + repr(events[1].payload) + output)
