"""Corrida worker de anexos × encerramento (ADR-0010/D8, revisão P1).

Molde ``apps/cases/tests/test_closure_worker_abort.py``: anexo em
``processing``/``pending`` com lease de worker expirada, caso encerrado
(``CLEANED``) — a task ``process_case_attachments`` é no-op silencioso:
SEM dispatch externo de OCR (spy em ``vision.transcribe_image``/
``ensure_vision_ready`` prova o não-envio — PHI não sai), SEM evento
``CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED``, SEM persistência da
verificação e SEM eventos ``PROCESSED``/``FAILED``. Cobre encerramento
ANTES da extração, DURANTE a rasterização (janela P1-1), ANTES e DURANTE
a verificação LLM (janela P1-2, sucesso e falha) e a ordem definida
(worker primeiro → encerramento aplica a disposição do modo), nos dois
modos (retenção e minimização). A serialização é pelo mesmo lock do
encerramento (``select_for_update`` no Case, D8 revisada).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import timedelta
from io import StringIO
from typing import Any

import psycopg
import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.anonymization.engine import AnonymizationEngine
from apps.attachments import extraction as extraction_module
from apps.attachments.models import (
    AttachmentStatus,
    CaseAttachment,
    ExtractionMethod,
    PatientMatch,
)
from apps.attachments.tasks import process_case_attachments
from apps.cases.closure import acknowledge_case_receipt, administratively_close_case
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock
from apps.cases.models import Case, CaseStatus
from apps.cases.tests.test_closure_ack import _denial_final_case
from apps.pipeline.llm import LlmError

MANAGER_ROLE = "manager"
SYSTEM_ROLE = "system"
NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
REASON_CODE = "stuck_lock"
REASON_TEXT = "worker de anexos travado — encerrado administrativamente"

_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"conteudo-fake-jpeg-" * 4
DISPATCHED = CaseEventType.CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED.value
PROCESSED = CaseEventType.CASE_ATTACHMENT_PROCESSED.value
FAILED = CaseEventType.CASE_ATTACHMENT_FAILED.value
FAKE_MODEL = "modelo-worker-abort-teste"
_MATCH_JSON = json.dumps(
    {
        "patient_match": PatientMatch.MATCH,
        "summary": "Documento clínico do paciente do caso.",
        "evidence": "Identificação consistente com o token informado.",
    },
    ensure_ascii=False,
)


class _EmptyAnalyzer:
    """Analyzer fake sem detecções (determinístico decide o que tokenizar)."""

    def analyze(self, *, text: str, language: str, score_threshold: float) -> list[Any]:
        del text, language, score_threshold
        return []


@pytest.fixture(autouse=True)
def _seeded_prompts() -> None:
    """Seed idempotente dos prompts (a verificação exige o ATTACHMENT_VERIFICATION)."""
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture(autouse=True)
def _stub_anonymization_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Engine fake na anonimização dos anexos (sem modelo spaCy na suíte)."""
    fake = AnonymizationEngine(analyzer=_EmptyAnalyzer(), anonymizer=None)
    monkeypatch.setattr("apps.anonymization.services.get_anonymization_engine", lambda: fake)


def _close_with_expired_worker_lease(case: Case, *, user: User) -> None:
    """Lease de worker expirada e encerramento administrativo (janela D8)."""
    claim_case_lock(case, user=None, context="worker_pipeline", role=SYSTEM_ROLE)
    Case.objects.filter(pk=case.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    case.refresh_from_db()
    administratively_close_case(
        case=case,
        user=user,
        active_role=MANAGER_ROLE,
        reason_code=REASON_CODE,
        reason_text=REASON_TEXT,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED


def _expire_worker_lease(case: Case) -> None:
    """Lease de worker expirada no banco (libera o encerramento intercalado)."""
    claim_case_lock(case, user=None, context="worker_pipeline", role=SYSTEM_ROLE)
    Case.objects.filter(pk=case.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    case.refresh_from_db()


class _InterleavingLlmClient:
    """Cliente LLM fake que intercala o encerramento durante a chamada."""

    def __init__(self, case: Case, manager: User, *, raise_error: bool = False) -> None:
        self._case = case
        self._manager = manager
        self._raise_error = raise_error
        self.calls = 0

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        del model, messages, json_schema
        self.calls += 1
        _close_with_expired_worker_lease(self._case, user=self._manager)
        if self._raise_error:
            raise LlmError("network", "Falha de conexão com a OpenRouter.")
        return _MATCH_JSON


class _MatchLlmClient:
    """Cliente fake da verificação: responde match e registra as chamadas."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        del model, messages, json_schema
        self.calls += 1
        return _MATCH_JSON


def _install_vision_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[object]]:
    """Spy no OCR externo: conta ``ensure_vision_ready`` e ``transcribe_image``."""
    spy: dict[str, list[object]] = {"ready": [], "transcribe": []}

    def _ready() -> None:
        spy["ready"].append("ready")

    def _transcribe(image_bytes: bytes, content_type: str) -> str:
        del content_type
        spy["transcribe"].append(image_bytes)
        return "transcrição fake"

    monkeypatch.setattr("apps.attachments.vision.ensure_vision_ready", _ready)
    monkeypatch.setattr("apps.attachments.vision.transcribe_image", _transcribe)
    return spy


def _event_types(case: Case) -> list[str]:
    return list(case.events.values_list("event_type", flat=True))


@pytest.mark.django_db
@pytest.mark.parametrize("minimization", [False, True])
def test_attachments_worker_aborts_after_administrative_close(
    minimization: bool,
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D8: anexo ``processing`` + lease expirada + caso CLEANED → task no-op,
    sem dispatch externo, sem evento de OCR e sem alterar o anexo."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="foto-exame.jpg",
        status=AttachmentStatus.PROCESSING,
    )
    manager = User.objects.create_user(username="gestor-anexo-abort", password="senha-teste")
    spy = _install_vision_spy(monkeypatch)

    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=minimization):
        _close_with_expired_worker_lease(case, user=manager)
        events_before = case.events.count()
        process_case_attachments(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert spy["ready"] == []
    assert spy["transcribe"] == []
    event_types = _event_types(case)
    assert DISPATCHED not in event_types
    assert PROCESSED not in event_types
    assert FAILED not in event_types
    assert case.events.count() == events_before
    if minimization:
        assert not CaseAttachment.objects.filter(pk=attachment.pk).exists()
    else:
        attachment.refresh_from_db()
        assert attachment.status == AttachmentStatus.PROCESSING
        assert attachment.extracted_text == ""
        assert attachment.anonymized_text == ""


@pytest.mark.django_db
@pytest.mark.parametrize("minimization", [False, True])
def test_ack_before_extraction_aborts_without_ocr_dispatch(
    minimization: bool,
    owner_user: User,
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D8 (ciência): CLEANED pela ciência do NIR ANTES da extração → no-op,
    sem dispatch externo (spy no OCR) e sem evento DISPATCHED."""
    doctor = User.objects.create_user(username="medico-anexo-abort", password="senha-teste")
    case = _denial_final_case(created_by=owner_user, decided_by=doctor)
    attachment = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="foto-exame.jpg",
        status=AttachmentStatus.PROCESSING,
    )
    spy = _install_vision_spy(monkeypatch)

    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=minimization):
        acknowledge_case_receipt(case, user=owner_user, role=NIR_ROLE)
        events_before = case.events.count()
        process_case_attachments(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert spy["ready"] == []
    assert spy["transcribe"] == []
    assert DISPATCHED not in _event_types(case)
    assert case.events.count() == events_before
    if minimization:
        assert not CaseAttachment.objects.filter(pk=attachment.pk).exists()
    else:
        attachment.refresh_from_db()
        assert attachment.status == AttachmentStatus.PROCESSING
        assert attachment.extracted_text == ""


@pytest.mark.django_db
@pytest.mark.parametrize("minimization", [False, True])
def test_close_during_rasterization_aborts_before_ocr_dispatch(
    minimization: bool,
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    pdf_bytes_factory: Callable[[list[str]], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-1: encerramento intercalado na rasterização (entre a checagem e o
    envio) → SEM evento DISPATCHED e SEM chamada ao OCR (spy), anexo intocado."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=pdf_bytes_factory(["", ""]),
        content_type="application/pdf",
        user=owner_user,
        name="scan-documento.pdf",
        status=AttachmentStatus.PROCESSING,
    )
    manager = User.objects.create_user(username="gestor-raster-abort", password="senha-teste")
    _expire_worker_lease(case)
    spy = _install_vision_spy(monkeypatch)
    real_rasterize = extraction_module._rasterize_pdf_pages

    def _interleaving_rasterize(content: bytes) -> list[bytes]:
        _close_with_expired_worker_lease(case, user=manager)
        return real_rasterize(content)

    monkeypatch.setattr("apps.attachments.extraction._rasterize_pdf_pages", _interleaving_rasterize)

    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=minimization):
        process_case_attachments(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert spy["transcribe"] == []
    assert DISPATCHED not in _event_types(case)
    assert PROCESSED not in _event_types(case)
    assert FAILED not in _event_types(case)
    if minimization:
        assert not CaseAttachment.objects.filter(pk=attachment.pk).exists()
    else:
        attachment.refresh_from_db()
        assert attachment.status == AttachmentStatus.PROCESSING
        assert attachment.extracted_text == ""
        assert attachment.extraction_method == ""


def _extracted_attachment(
    case: Case,
    owner_user: User,
    attachment_record_factory: Callable[..., CaseAttachment],
) -> CaseAttachment:
    """Anexo já extraído e anonimizado (entra direto na etapa de verificação)."""
    return attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="foto-exame.jpg",
        status=AttachmentStatus.PROCESSING,
        extracted_text="texto já extraído do anexo",
        extraction_method=ExtractionMethod.VISION,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("minimization", [False, True])
def test_close_before_verification_aborts_without_persistence(
    minimization: bool,
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2: CLEANED ANTES da verificação → LLM nem é chamado, SEM
    persistência (summary/match/status intocados) e SEM eventos."""
    case = case_factory(owner_user)
    attachment = _extracted_attachment(case, owner_user, attachment_record_factory)
    attachment.anonymized_text = "texto anonimizado do anexo"
    attachment.pseudonym_map = {"<PESSOA_1>": "Maria da Silva"}
    attachment.save(update_fields=["anonymized_text", "pseudonym_map"])
    manager = User.objects.create_user(username="gestor-verif-abort", password="senha-teste")
    client_fake = _MatchLlmClient()

    with override_settings(
        CASE_CLOSURE_MINIMIZATION_ENABLED=minimization,
        LLM_CLIENT_FACTORY=lambda: client_fake,
        LLM1_MODEL=FAKE_MODEL,
    ):
        _close_with_expired_worker_lease(case, user=manager)
        events_before = case.events.count()
        process_case_attachments(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert client_fake.calls == 0
    assert PROCESSED not in _event_types(case)
    assert FAILED not in _event_types(case)
    assert case.events.count() == events_before
    if minimization:
        assert not CaseAttachment.objects.filter(pk=attachment.pk).exists()
    else:
        attachment.refresh_from_db()
        assert attachment.status == AttachmentStatus.PROCESSING
        assert attachment.patient_match in (None, "")
        assert attachment.verification_summary == ""
        assert attachment.verification_evidence == ""


@pytest.mark.django_db
@pytest.mark.parametrize("minimization", [False, True])
def test_close_during_llm_call_aborts_persistence_on_success(
    minimization: bool,
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
) -> None:
    """P1-2: encerramento intercalado DURANTE a resposta do LLM (sucesso) →
    a persistência da verificação é abortada (rollback): SEM PROCESSED,
    summary/match/status intocados."""
    case = case_factory(owner_user)
    attachment = _extracted_attachment(case, owner_user, attachment_record_factory)
    attachment.anonymized_text = "texto anonimizado do anexo"
    attachment.pseudonym_map = {"<PESSOA_1>": "Maria da Silva"}
    attachment.save(update_fields=["anonymized_text", "pseudonym_map"])
    manager = User.objects.create_user(username="gestor-llm-abort", password="senha-teste")
    _expire_worker_lease(case)
    client_fake = _InterleavingLlmClient(case, manager)

    with override_settings(
        CASE_CLOSURE_MINIMIZATION_ENABLED=minimization,
        LLM_CLIENT_FACTORY=lambda: client_fake,
        LLM1_MODEL=FAKE_MODEL,
    ):
        process_case_attachments(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert client_fake.calls == 1
    assert PROCESSED not in _event_types(case)
    assert FAILED not in _event_types(case)
    if minimization:
        assert not CaseAttachment.objects.filter(pk=attachment.pk).exists()
    else:
        attachment.refresh_from_db()
        assert attachment.status == AttachmentStatus.PROCESSING
        assert attachment.patient_match in (None, "")
        assert attachment.verification_summary == ""
        assert attachment.verification_evidence == ""
        assert attachment.processed_at is None


@pytest.mark.django_db
@pytest.mark.parametrize("minimization", [False, True])
def test_close_during_llm_call_aborts_without_mark_failed_on_error(
    minimization: bool,
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
) -> None:
    """P1-2 (falha): LLM responde erro APÓS o CLEANED → SEM ``_mark_failed``,
    SEM evento FAILED e status intocado."""
    case = case_factory(owner_user)
    attachment = _extracted_attachment(case, owner_user, attachment_record_factory)
    attachment.anonymized_text = "texto anonimizado do anexo"
    attachment.pseudonym_map = {"<PESSOA_1>": "Maria da Silva"}
    attachment.save(update_fields=["anonymized_text", "pseudonym_map"])
    manager = User.objects.create_user(username="gestor-llm-erro", password="senha-teste")
    _expire_worker_lease(case)
    client_fake = _InterleavingLlmClient(case, manager, raise_error=True)

    with override_settings(
        CASE_CLOSURE_MINIMIZATION_ENABLED=minimization,
        LLM_CLIENT_FACTORY=lambda: client_fake,
        LLM1_MODEL=FAKE_MODEL,
    ):
        process_case_attachments(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert client_fake.calls == 1
    assert PROCESSED not in _event_types(case)
    assert FAILED not in _event_types(case)
    if minimization:
        assert not CaseAttachment.objects.filter(pk=attachment.pk).exists()
    else:
        attachment.refresh_from_db()
        assert attachment.status == AttachmentStatus.PROCESSING
        assert attachment.failed_reason == ""


def _independent_status(case_id: object) -> str:
    """Status do caso por conexão psycopg independente (commit real)."""
    db = settings.DATABASES["default"]
    conn = psycopg.connect(
        host=db["HOST"],
        port=db["PORT"],
        dbname=db["NAME"],
        user=db["USER"],
        password=db["PASSWORD"],
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT status FROM cases_case WHERE case_id = %s", (str(case_id),))
            row = cursor.fetchone()
    finally:
        conn.close()
    assert row is not None
    return str(row[0])


class TestMultipageCloseAbortsRemainingPages(TransactionTestCase):
    """P1 (review 3) + P2-2: multipágina com commit real.

    ``TransactionTestCase`` (sem transação externa): o encerramento na 1ª
    chamada ao OCR commita de verdade — provado por conexão psycopg
    independente (status CLEANED visível fora da conexão do worker) antes
    de a 2ª página ser avaliada. Só afirma o que a prova demonstra: 2ª
    página não enviada, DISPATCHED=1 (a 1ª saiu com caso aberto), nada
    persistido. Nos dois modos (subTest).
    """

    def test_close_after_first_page_aborts_remaining_pages(self) -> None:
        from unittest.mock import patch

        import pymupdf
        from django.core.files.base import ContentFile

        def _pdf_bytes(pages: list[str]) -> bytes:
            document = pymupdf.open()  # type: ignore[no-untyped-call]
            try:
                for page_text in pages:
                    page = document.new_page()
                    if page_text.strip():
                        page.insert_text((72, 72), page_text)
                return bytes(document.tobytes())  # type: ignore[no-untyped-call]
            finally:
                document.close()  # type: ignore[no-untyped-call]

        test_storages = {
            "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        for minimization in (False, True):
            with self.subTest(minimization=minimization):
                with override_settings(STORAGES=test_storages):
                    owner = User.objects.create_user(
                        username=f"dono-multipage-{minimization}", password="senha-teste"
                    )
                    manager = User.objects.create_user(
                        username=f"gestor-multipage-{minimization}", password="senha-teste"
                    )
                    case = Case.objects.create(created_by=owner)
                    attachment = CaseAttachment(
                        case=case,
                        original_filename="scan-duas-paginas.pdf",
                        content_type="application/pdf",
                        size_bytes=len(_pdf_bytes(["", ""])),
                        uploaded_by=owner,
                        status=AttachmentStatus.PROCESSING,
                    )
                    attachment.file.save(
                        "scan-duas-paginas.pdf",
                        ContentFile(_pdf_bytes(["", ""])),
                        save=False,
                    )
                    attachment.save()
                    attachment_pk = attachment.pk
                    case_id = case.case_id
                    _expire_worker_lease(case)
                    transcribe_calls: list[bytes] = []
                    committed_statuses: list[str] = []

                    def _transcribe_closing_on_first(image_bytes: bytes, content_type: str) -> str:
                        del content_type
                        transcribe_calls.append(image_bytes)
                        if len(transcribe_calls) == 1:
                            _close_with_expired_worker_lease(case, user=manager)
                            committed_statuses.append(_independent_status(case_id))
                        return f"transcrição fake ({len(transcribe_calls)})"

                    with (
                        override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=minimization),
                        patch("apps.attachments.vision.ensure_vision_ready", lambda: None),
                        patch(
                            "apps.attachments.vision.transcribe_image",
                            _transcribe_closing_on_first,
                        ),
                    ):
                        process_case_attachments(case_id)

                    assert committed_statuses == [CaseStatus.CLEANED]
                    assert _independent_status(case_id) == CaseStatus.CLEANED
                    assert len(transcribe_calls) == 1
                    assert _event_types(case).count(DISPATCHED) == 1
                    assert PROCESSED not in _event_types(case)
                    assert FAILED not in _event_types(case)
                    if minimization:
                        assert not CaseAttachment.objects.filter(pk=attachment_pk).exists()
                    else:
                        attachment.refresh_from_db()
                        assert attachment.status == AttachmentStatus.PROCESSING
                        assert attachment.extracted_text == ""
                        assert attachment.extraction_method == ""


class TestDispatchDurability(TransactionTestCase):
    """P1 (durabilidade, D8): commit real + consulta por conexão independente.

    ``TransactionTestCase`` (sem transação externa) prova o commit
    independente do DISPATCHED: após o crash, conexão psycopg direta
    (parâmetros de ``settings.DATABASES``) conta o evento no banco.
    """

    def test_dispatch_event_survives_crash_after_phi_sent(self) -> None:
        from unittest.mock import patch

        test_storages = {
            "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        with override_settings(STORAGES=test_storages):
            owner = User.objects.create_user(username="dono-durabilidade", password="senha-teste")
            case = Case.objects.create(created_by=owner)
            attachment = CaseAttachment(
                case=case,
                original_filename="foto-exame.jpg",
                content_type="image/jpeg",
                size_bytes=len(_JPEG_BYTES),
                uploaded_by=owner,
                status=AttachmentStatus.PROCESSING,
            )
            from django.core.files.base import ContentFile

            attachment.file.save("foto-exame.jpg", ContentFile(_JPEG_BYTES), save=False)
            attachment.save()
            case_id = case.case_id

            def _crashing_transcribe(image_bytes: bytes, content_type: str) -> str:
                del image_bytes, content_type
                raise KeyboardInterrupt

            with (
                patch("apps.attachments.vision.ensure_vision_ready", lambda: None),
                patch("apps.attachments.vision.transcribe_image", _crashing_transcribe),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    process_case_attachments(case_id)

        db = settings.DATABASES["default"]
        conn = psycopg.connect(
            host=db["HOST"],
            port=db["PORT"],
            dbname=db["NAME"],
            user=db["USER"],
            password=db["PASSWORD"],
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM cases_caseevent WHERE case_id = %s AND event_type = %s",
                    (str(case_id), DISPATCHED),
                )
                row = cursor.fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("minimization", [False, True])
def test_no_dispatched_event_when_closed_before_dispatch_transaction(
    minimization: bool,
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-1: encerramento intercalado ANTES da transação do dispatch
    (no ensure_vision_ready) → SEM evento DISPATCHED e SEM
    envio (spy 0 chamadas) — sem evento órfão sem nenhum envio."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="foto-exame.jpg",
        status=AttachmentStatus.PROCESSING,
    )
    manager = User.objects.create_user(username="gestor-gap", password="senha-teste")
    _expire_worker_lease(case)
    spy = _install_vision_spy(monkeypatch)

    def _ready_closing() -> None:
        _close_with_expired_worker_lease(case, user=manager)

    monkeypatch.setattr("apps.attachments.vision.ensure_vision_ready", _ready_closing)

    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=minimization):
        process_case_attachments(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert spy["transcribe"] == []
    assert DISPATCHED not in _event_types(case)
    assert FAILED not in _event_types(case)
    if minimization:
        assert not CaseAttachment.objects.filter(pk=attachment.pk).exists()
    else:
        attachment.refresh_from_db()
        assert attachment.status == AttachmentStatus.PROCESSING
        assert attachment.extracted_text == ""


@pytest.mark.django_db
def test_pre_send_guard_runs_only_before_pages_after_first(
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    pdf_bytes_factory: Callable[[list[str]], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-1 (API): o guard roda só antes das páginas 2..N (a 1ª página já
    está coberta pela transação do dispatch) — nenhuma transação extra
    entre o commit do evento e o 1º envio."""
    from apps.attachments.extraction import extract_attachment_text

    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=pdf_bytes_factory(["", ""]),
        content_type="application/pdf",
        user=owner_user,
        name="scan-duas-paginas.pdf",
        status=AttachmentStatus.PROCESSING,
    )
    spy = _install_vision_spy(monkeypatch)
    guard_calls: list[str] = []

    text, method = extract_attachment_text(
        attachment,
        on_external_dispatch=lambda: None,
        pre_send_guard=lambda: guard_calls.append("guard"),
    )

    assert method == ExtractionMethod.VISION
    assert text == "transcrição fake\n\ntranscrição fake"
    assert len(spy["transcribe"]) == 2
    assert len(guard_calls) == 1


@pytest.mark.django_db
def test_dispatch_event_survives_external_failure_and_marks_failed(
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Durabilidade (falha tratada): OCR lança após o dispatch commitado →
    anexo failed com o DISPATCHED preservado (consulta fresca)."""
    from apps.cases.models import CaseEvent

    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="foto-exame.jpg",
        status=AttachmentStatus.PROCESSING,
    )

    def _failing_transcribe(image_bytes: bytes, content_type: str) -> str:
        del image_bytes, content_type
        raise LlmError("network", "Falha de conexão com a OpenRouter.")

    monkeypatch.setattr("apps.attachments.vision.ensure_vision_ready", lambda: None)
    monkeypatch.setattr("apps.attachments.vision.transcribe_image", _failing_transcribe)

    process_case_attachments(case.case_id)

    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED
        ).count()
        == 1
    )
    assert (
        CaseEvent.objects.filter(case=case, event_type=CaseEventType.CASE_ATTACHMENT_FAILED).count()
        == 1
    )
    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.FAILED


@pytest.mark.django_db
@pytest.mark.parametrize("minimization", [False, True])
def test_worker_completes_before_close_applies_mode_disposition(
    minimization: bool,
    owner_user: User,
    case_factory: Callable[[User], Case],
    attachment_record_factory: Callable[..., CaseAttachment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordem definida: worker primeiro (anexo processado) → encerramento
    depois aplica a disposição do modo (retenção preserva; minimização
    deleta rows e o worker re-lê → no-op)."""
    case = case_factory(owner_user)
    attachment = attachment_record_factory(
        case,
        content=_JPEG_BYTES,
        content_type="image/jpeg",
        user=owner_user,
        name="foto-exame.jpg",
        status=AttachmentStatus.PROCESSING,
    )
    manager = User.objects.create_user(username="gestor-ordem", password="senha-teste")
    spy = _install_vision_spy(monkeypatch)
    client_fake = _MatchLlmClient()

    with override_settings(
        CASE_CLOSURE_MINIMIZATION_ENABLED=minimization,
        LLM_CLIENT_FACTORY=lambda: client_fake,
        LLM1_MODEL=FAKE_MODEL,
    ):
        process_case_attachments(case.case_id)
        attachment.refresh_from_db()
        assert attachment.status == AttachmentStatus.PROCESSED
        assert len(spy["transcribe"]) == 1
        assert PROCESSED in _event_types(case)
        _close_with_expired_worker_lease(case, user=manager)
        transcribe_before = len(spy["transcribe"])
        process_case_attachments(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert len(spy["transcribe"]) == transcribe_before
    if minimization:
        assert not CaseAttachment.objects.filter(pk=attachment.pk).exists()
    else:
        attachment.refresh_from_db()
        assert attachment.status == AttachmentStatus.PROCESSED
        assert attachment.extracted_text == "transcrição fake"
        # Sem patient_name/token no caso → modo sem-comparação (unknown, R2).
        assert attachment.patient_match == PatientMatch.UNKNOWN
        assert attachment.verification_summary != ""
