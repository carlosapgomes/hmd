"""Testes do encerramento administrativo (change painel-lista-encerramento, slice 001, R1–R3).

Cobre ``administratively_close_case`` de ``apps/cases/closure.py`` (o supervisor
encerra um caso travado), ``force_release_case_lock`` de ``apps/cases/locks.py``
e a op pública ``Case.administratively_close``:

- R1: o paramétrico percorre TODOS os 16 estados não-``CLEANED`` (transições
  reais, nenhum estado forjado) e fecha em ``CLEANED`` com EXATAMENTE +2 eventos
  (``CASE_STATUS_CLEANED`` + ``CASE_ADMINISTRATIVELY_CLOSED``), +1
  ``CASE_LOCK_RELEASED`` quando havia lock operacional; payload completo do
  evento de encerramento (motivo/texto/autor/papel + ``lock_snapshot`` colhido
  ANTES do force-release); recusas fail-closed (``CLEANED``, texto vazio, código
  fora do catálogo, papel fora de manager/admin, lease de worker VIVA) sem
  nenhuma escrita; lease EXPIRADA (``stuck_lock``) permite o encerramento.
- R2: minimização completa — rows ``CaseDocument``/``CaseAttachment`` deletadas,
  os 7 campos clínicos/LLM zerados e os ARQUIVOS FÍSICOS removidos do storage
  (``InMemoryStorage``; a deleção roda no ``transaction.on_commit``, por isso o
  teste de arquivo usa ``django_db(transaction=True)``); download do documento de
  caso encerrado não é mais possível (404 na rota ``intake:serve_document``);
  identificação/procedimentos/trilha seguem preservados.
- R3: o lock persistido fica limpo, o evento de release carrega o payload
  forçado com o autor do encerramento e a INSTÂNCIA devolvida fica sem lock (o
  ``save()`` full da transição não ressuscita os campos); caso sem lock não grava
  evento de release (force-release idempotente); notificação ao criador com tipo
  novo e texto 100% fixo (sem o motivo) + 4º marco no manual do usuário.

Storage em memória (``InMemoryStorage``): mesmo padrão de
``apps/cases/tests/test_closure_ack.py`` — uploads nunca tocam o ``MEDIA_ROOT``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django_fsm import TransitionNotAllowed

from apps.accounts.models import NotificationType, User, UserNotification
from apps.attachments.models import AttachmentStatus, CaseAttachment
from apps.cases.closure import ADMINISTRATIVE_CLOSURE_REASONS, administratively_close_case
from apps.cases.events import CaseEventType, case_status_event_type
from apps.cases.locks import claim_case_lock, force_release_case_lock
from apps.cases.models import ActorType, Case, CaseDocument, CaseEvent, CaseStatus

MANAGER_ROLE = "manager"
ADMIN_ROLE = "admin"
NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SCHEDULER_ROLE = "scheduler"
SYSTEM_ROLE = "system"

# Motivo usado na maioria dos testes (código do catálogo + texto do supervisor).
REASON_CODE = "processing_error"
REASON_TEXT = "caso travado na extração do relatório — sem progresso"

# Contexto de lock dos workers (prefixo ``worker_`` — base da recusa fail-closed).
WORKER_CONTEXT = "worker_pipeline"
# Contexto de lock de OUTRO fluxo (lease viva não recusa o encerramento).
NON_WORKER_CONTEXT = "doctor_decision"

# Razão canônica gravada no release forçado do lock.
FORCED_RELEASE_REASON = "administrative_closure"

# Texto fixo do 4º marco (spec notifications) — repetido de propósito nos
# asserts de título/preview (o valor vem de constantes do serviço de notificações).
ADMIN_CLOSED_TEXT = "Caso encerrado administrativamente"

# Identificação/linkage persistido (preservado pela minimização).
RECORD_NUMBER = "33345"

# Conteúdo clínico/LLM a ser minimizado (mesmos campos do encerramento por ciência).
EXTRACTED_TEXT = "texto extraído do relatório — conteúdo clínico completo"
ANONYMIZED_TEXT = "texto anonimizado do relatório — sem PII"
STRUCTURED_DATA = {"procedures": [{"procedure_type": "art_perif", "evidence": "relato clínico"}]}
SUMMARY_TEXT = "Resumo clínico sintético gerado pelo LLM2."
SUGGESTED_ACTION = {"text": "Agendar arteriografia periférica."}
POLICY_RESULT = {"verdict": "allowed", "policy": "hemodinamica"}

PDF_CONTENT_TYPE = "application/pdf"

# Storage de teste: arquivos apenas em memória (nada de disco/MEDIA_ROOT).
_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Prefixo do pipeline de processamento até AWAITING_DOCTOR, na ordem canônica
# (mesma tabela de transições do ``test_fsm``): cada estado do paramétrico usa o
# prefixo até o passo correspondente + os passos do seu ramo.
_PIPELINE_PREFIX: tuple[tuple[str, dict[str, object]], ...] = (
    ("start_pdf_extraction", {}),
    ("complete_pdf_extraction", {}),
    ("complete_anonymization", {}),
    ("complete_llm_extraction", {}),
    ("complete_llm_summarization", {}),
)

# Caminho (operação, kwargs extras) de NEW até cada estado não-CLEANED.
_STEPS_TO_STATE: dict[str, list[tuple[str, dict[str, object]]]] = {
    "NEW": [],
    "PDF_EXTRACTING": [*_PIPELINE_PREFIX[:1]],
    "ANONYMIZING": [*_PIPELINE_PREFIX[:2]],
    "LLM_EXTRACTING": [*_PIPELINE_PREFIX[:3]],
    "LLM_SUMMARIZING": [*_PIPELINE_PREFIX[:4]],
    "AWAITING_DOCTOR": [*_PIPELINE_PREFIX],
    "DOCTOR_DENIED": [*_PIPELINE_PREFIX, ("record_doctor_decision", {"accepted": False})],
    "DOCTOR_ACCEPTED": [*_PIPELINE_PREFIX, ("record_doctor_decision", {"accepted": True})],
    "SCHEDULER_REQUESTED": [
        *_PIPELINE_PREFIX,
        ("record_doctor_decision", {"accepted": True}),
        ("request_scheduling", {}),
    ],
    "AWAITING_SCHEDULING": [
        *_PIPELINE_PREFIX,
        ("record_doctor_decision", {"accepted": True}),
        ("request_scheduling", {}),
        ("await_scheduling_confirmation", {}),
    ],
    "SCHEDULING_CONFIRMED": [
        *_PIPELINE_PREFIX,
        ("record_doctor_decision", {"accepted": True}),
        ("request_scheduling", {}),
        ("await_scheduling_confirmation", {}),
        ("confirm_scheduling", {}),
    ],
    "SCHEDULING_DENIED": [
        *_PIPELINE_PREFIX,
        ("record_doctor_decision", {"accepted": True}),
        ("request_scheduling", {}),
        ("await_scheduling_confirmation", {}),
        ("deny_scheduling", {}),
    ],
    "FAILED": [
        ("start_pdf_extraction", {}),
        ("complete_pdf_extraction", {}),
        ("fail_processing", {"reason": "falha de processamento do setup"}),
    ],
    "FINAL_REPLY_POSTED": [
        *_PIPELINE_PREFIX,
        ("record_doctor_decision", {"accepted": False}),
        ("post_final_reply", {}),
    ],
    "AWAITING_NIR_ACK": [
        *_PIPELINE_PREFIX,
        ("record_doctor_decision", {"accepted": False}),
        ("post_final_reply", {}),
        ("nir_acknowledge", {}),
    ],
    "CLEANING": [
        *_PIPELINE_PREFIX,
        ("record_doctor_decision", {"accepted": False}),
        ("post_final_reply", {}),
        ("nir_acknowledge", {}),
        ("start_cleaning", {}),
    ],
}

# Os 16 estados de origem do encerramento (todos exceto CLEANED).
_NON_CLEANED_STATES = [state.value for state in CaseStatus if state is not CaseStatus.CLEANED]


@pytest.fixture(autouse=True)
def _memory_storage() -> Iterator[None]:
    """Storage default em memória durante cada teste do módulo."""
    with override_settings(STORAGES=_TEST_STORAGES):
        yield


@pytest.fixture
def client() -> Client:
    """Client Django isolado por teste."""
    return Client()


@pytest.fixture
def manager_user(user_factory: Callable[[str, str], User]) -> User:
    """Supervisor do painel (papel ativo ``manager``)."""
    return user_factory("manager-teste", MANAGER_ROLE)


@pytest.fixture
def admin_user(user_factory: Callable[[str, str], User]) -> User:
    """Administrador (papel ativo ``admin`` — segundo papel autorizado)."""
    return user_factory("admin-teste", ADMIN_ROLE)


def _pdf(filename: str) -> SimpleUploadedFile:
    """PDF fake de upload (apenas metadados/content — o conteúdo não é lido)."""
    return SimpleUploadedFile(
        filename,
        b"%PDF-1.4 relatorio fake (conteudo nao lido neste slice)",
        content_type=PDF_CONTENT_TYPE,
    )


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
        document.file.save(document.original_filename, uploaded, save=False)
        document.save()
        documents.append(document)
    return documents


def _attach_attachment(case: Case, uploaded_by: User, filename: str) -> CaseAttachment:
    """Anexa um anexo clínico processado com conteúdo (alvo da minimização)."""
    uploaded = SimpleUploadedFile(
        filename,
        b"anexo clinico fake (conteudo nao lido neste slice)",
        content_type="image/jpeg",
    )
    attachment = CaseAttachment(
        case=case,
        original_filename=filename,
        content_type="image/jpeg",
        size_bytes=uploaded.size or 0,
        uploaded_by=uploaded_by,
        status=AttachmentStatus.PROCESSED,
        extracted_text="texto do anexo — conteúdo clínico do exame",
        anonymized_text="texto anonimizado do anexo",
        pseudonym_map={"<PACIENTE_1>": "Maria da Silva"},
        processed_at=timezone.now(),
    )
    attachment.file.save(filename, uploaded, save=False)
    attachment.save()
    return attachment


def _seed_clinical_data(case: Case) -> None:
    """Identidade + campos clínicos/LLM do caso (alvos da minimização)."""
    case.agency_record_number = RECORD_NUMBER
    case.extracted_text = EXTRACTED_TEXT
    case.anonymized_text = ANONYMIZED_TEXT
    case.pseudonym_map = {"<PESSOA_1>": "MARIA DA SILVA SOUZA"}
    case.structured_data = STRUCTURED_DATA
    case.summary_text = SUMMARY_TEXT
    case.suggested_action = SUGGESTED_ACTION
    case.policy_result = POLICY_RESULT
    case.save()


def _case_in_state(state: str, *, created_by: User) -> Case:
    """Cria um caso e o dirige pelas transições válidas até ``state``."""
    case = Case.objects.create(created_by=created_by)
    for operation, extra in _STEPS_TO_STATE[state]:
        getattr(case, operation)(user=None, role=SYSTEM_ROLE, **extra)
    assert case.status == state
    return case


def _event_types(case: Case) -> list[str]:
    """Tipos de evento da trilha na ordem de gravação."""
    return [event.event_type for event in case.events.order_by("id")]


def _close(
    case: Case,
    *,
    user: User,
    role: str = MANAGER_ROLE,
    reason_code: str = REASON_CODE,
    reason_text: str = REASON_TEXT,
) -> Case:
    """Chama o serviço com os parâmetros padrão do teste."""
    return administratively_close_case(
        case=case,
        user=user,
        active_role=role,
        reason_code=reason_code,
        reason_text=reason_text,
    )


def _claim_worker_lock(case: Case, *, context: str = WORKER_CONTEXT) -> None:
    """Reivindica o lock do worker (lease viva) e recarrega a instância."""
    claim_case_lock(case, user=None, context=context, role=SYSTEM_ROLE)
    case.refresh_from_db()


def _expire_lock(case: Case) -> None:
    """Envelhece a lease do caso para o passado (mantém os demais campos)."""
    Case.objects.filter(pk=case.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    case.refresh_from_db()


def _stuck_worker_lock(case: Case) -> None:
    """Lock de worker com lease EXPIRADA (worker travado — não recusa o encerramento)."""
    _claim_worker_lock(case)
    _expire_lock(case)


def _assert_lock_cleared(case: Case) -> None:
    """Os seis campos de lock estão limpos na instância informada."""
    assert case.locked_by is None
    assert case.locked_by_id is None
    assert case.locked_at is None
    assert case.locked_until is None
    assert case.lock_token is None
    assert case.lock_context == ""
    assert case.lock_role == ""


def _silence_pipeline_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op nos enqueues de anonimização/pipeline (testes com commit real).

    Em ``django_db(transaction=True)`` os callbacks ``on_commit`` dos eventos das
    transições do setup disparam de verdade e executariam a cadeia inline
    (engine spaCy/LLM) — fora do contrato deste slice. Mesmo padrão do
    ``test_closure_ack``.
    """

    def _noop(case_id: object) -> None:
        del case_id

    monkeypatch.setattr("apps.anonymization.tasks.enqueue_case_anonymization", _noop)
    monkeypatch.setattr("apps.pipeline.tasks.enqueue_case_pipeline", _noop)


# ── R1: transição de TODOS os estados não-CLEANED → CLEANED ────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("state", _NON_CLEANED_STATES)
def test_close_from_every_non_cleaned_state(
    state: str,
    nir_user: User,
    manager_user: User,
) -> None:
    """R1: qualquer estado ≠ CLEANED fecha em CLEANED com EXATAMENTE +2 eventos
    (``CASE_STATUS_CLEANED`` e ``CASE_ADMINISTRATIVELY_CLOSED``, nesta ordem) —
    o caso estava sem lock, então nenhum release é gravado."""
    case = _case_in_state(state, created_by=nir_user)
    events_before = case.events.count()

    closed = _close(case, user=manager_user)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert closed.status == CaseStatus.CLEANED
    assert case.events.count() == events_before + 2
    assert _event_types(case)[-2:] == [
        case_status_event_type(CaseStatus.CLEANED),
        CaseEventType.CASE_ADMINISTRATIVELY_CLOSED,
    ]
    closing_events = list(case.events.order_by("id"))[-2:]
    assert closing_events[0].payload == {
        "source": state,
        "target": CaseStatus.CLEANED.value,
    }
    assert all(event.actor == manager_user for event in closing_events)
    assert all(event.actor_role == MANAGER_ROLE for event in closing_events)
    assert all(event.actor_type == ActorType.USER for event in closing_events)


@pytest.mark.django_db
def test_close_with_worker_lock_records_release_event(
    nir_user: User,
    manager_user: User,
) -> None:
    """R1: com lock operacional (worker travado, lease expirada) o encerramento
    soma +3 eventos — o ``CASE_LOCK_RELEASED`` do force-release ANTES da
    transição."""
    case = _case_in_state("FAILED", created_by=nir_user)
    _stuck_worker_lock(case)
    events_before = case.events.count()

    _close(case, user=manager_user)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert case.events.count() == events_before + 3
    assert _event_types(case)[-3:] == [
        CaseEventType.CASE_LOCK_RELEASED,
        case_status_event_type(CaseStatus.CLEANED),
        CaseEventType.CASE_ADMINISTRATIVELY_CLOSED,
    ]


@pytest.mark.django_db
def test_close_event_payload_with_lock(nir_user: User, manager_user: User) -> None:
    """R1: payload completo do evento de encerramento — motivo, texto, autor,
    papel e o ``lock_snapshot`` colhido ANTES do force-release."""
    case = _case_in_state("FAILED", created_by=nir_user)
    _stuck_worker_lock(case)
    previous_until = case.locked_until
    assert previous_until is not None

    _close(case, user=manager_user)

    event = CaseEvent.objects.get(case=case, event_type=CaseEventType.CASE_ADMINISTRATIVELY_CLOSED)
    assert event.payload == {
        "reason_code": REASON_CODE,
        "reason_text": REASON_TEXT,
        "by": manager_user.username,
        "role": MANAGER_ROLE,
        "had_lock": True,
        "previous_lock_context": WORKER_CONTEXT,
        "previous_lock_until": previous_until.isoformat(),
    }
    assert event.actor == manager_user
    assert event.actor_role == MANAGER_ROLE
    assert event.actor_type == ActorType.USER


@pytest.mark.django_db
def test_close_event_payload_without_lock(nir_user: User, manager_user: User) -> None:
    """R1: caso sem lock registra ``had_lock=False`` com o snapshot vazio."""
    case = _case_in_state("AWAITING_DOCTOR", created_by=nir_user)

    _close(case, user=manager_user)

    event = CaseEvent.objects.get(case=case, event_type=CaseEventType.CASE_ADMINISTRATIVELY_CLOSED)
    assert event.payload["had_lock"] is False
    assert event.payload["previous_lock_context"] == ""
    assert event.payload["previous_lock_until"] is None
    assert event.payload["reason_code"] == REASON_CODE
    assert event.payload["reason_text"] == REASON_TEXT
    assert event.payload["by"] == manager_user.username
    assert event.payload["role"] == MANAGER_ROLE


@pytest.mark.django_db
def test_close_by_admin_role_allowed(nir_user: User, admin_user: User) -> None:
    """R1: o segundo papel autorizado (``admin``) encerra igual ao manager."""
    case = _case_in_state("FAILED", created_by=nir_user)

    _close(case, user=admin_user, role=ADMIN_ROLE)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    event = CaseEvent.objects.get(case=case, event_type=CaseEventType.CASE_ADMINISTRATIVELY_CLOSED)
    assert event.payload["role"] == ADMIN_ROLE
    assert event.actor == admin_user


# ── R2: minimização completa (rows, campos e arquivos físicos) ─────────────


@pytest.mark.django_db
def test_close_minimizes_rows_and_clinical_fields(
    nir_user: User,
    manager_user: User,
) -> None:
    """R2: documentos e anexos (rows) são removidos e os 7 campos clínicos/LLM
    zerados; a identificação persistida segue intacta."""
    case = _case_in_state("FAILED", created_by=nir_user)
    _seed_clinical_data(case)
    _attach_documents(case, nir_user, "relatorio-sesab.pdf")
    _attach_attachment(case, nir_user, "anexo-ecg.jpg")
    case.refresh_from_db()
    attachment = case.attachments.get()
    assert attachment.extracted_text != ""

    _close(case, user=manager_user)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    assert list(fresh.documents.values_list("pk", flat=True)) == []
    assert list(fresh.attachments.values_list("pk", flat=True)) == []
    assert fresh.extracted_text == ""
    assert fresh.anonymized_text == ""
    assert fresh.pseudonym_map == {}
    assert fresh.structured_data == {}
    assert fresh.summary_text == ""
    assert fresh.suggested_action == {}
    assert fresh.policy_result == {}
    # Preserva a identificação (linkage do prior-case) e a trilha.
    assert fresh.agency_record_number == RECORD_NUMBER
    assert _event_types(fresh)[-2:] == [
        case_status_event_type(CaseStatus.CLEANED),
        CaseEventType.CASE_ADMINISTRATIVELY_CLOSED,
    ]


@pytest.mark.django_db(transaction=True)
def test_close_deletes_physical_files(
    monkeypatch: pytest.MonkeyPatch,
    nir_user: User,
    manager_user: User,
) -> None:
    """R2: os arquivos físicos (documentos E anexos) somem do storage — a
    deleção roda no ``transaction.on_commit`` e dispara no commit real."""
    _silence_pipeline_enqueues(monkeypatch)
    case = _case_in_state("FAILED", created_by=nir_user)
    document = _attach_documents(case, nir_user, "relatorio-sesab.pdf")[0]
    attachment = _attach_attachment(case, nir_user, "anexo-ecg.jpg")
    document_name = document.file.name
    attachment_name = attachment.file.name
    assert document_name
    assert attachment_name
    file_names = [document_name, attachment_name]
    assert all(default_storage.exists(name) for name in file_names)

    _close(case, user=manager_user)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    assert fresh.documents.count() == 0
    assert fresh.attachments.count() == 0
    assert not any(default_storage.exists(name) for name in file_names)


@pytest.mark.django_db
def test_closed_case_document_is_no_longer_served(
    client: Client,
    nir_user: User,
    manager_user: User,
) -> None:
    """R2 (cenário da spec): o mesmo documento que a rota servia (200) vira 404
    após o encerramento — as rows ``CaseDocument`` não existem mais."""
    case = _case_in_state("FAILED", created_by=nir_user)
    document = _attach_documents(case, nir_user, "relatorio-sesab.pdf")[0]
    url = reverse("intake:serve_document", args=[case.case_id, document.pk])

    client.force_login(nir_user)
    assert client.get(url).status_code == 200

    _close(case, user=manager_user)

    assert Case.objects.get(pk=case.case_id).status == CaseStatus.CLEANED
    assert client.get(url).status_code == 404


# ── R3: lock forçado, sem ressurreição e idempotente ───────────────────────


@pytest.mark.django_db
def test_force_release_clears_lock_and_copies_to_caller_instance(
    nir_user: User,
    manager_user: User,
) -> None:
    """R3: o release forçado limpa os campos persistidos, copia o estado limpo
    de volta para a instância do chamador (o ``save()`` full da transição não
    ressuscita o lock) e grava o payload de força com o autor."""
    case = _case_in_state("FAILED", created_by=nir_user)
    _stuck_worker_lock(case)
    token = case.lock_token
    previous_until = case.locked_until
    assert token is not None
    assert previous_until is not None

    closed = _close(case, user=manager_user)

    fresh = Case.objects.get(pk=case.case_id)
    _assert_lock_cleared(fresh)
    # Instância devolvida pelo serviço: sem lock (não ressuscitado pelo save).
    _assert_lock_cleared(closed)
    release = CaseEvent.objects.get(case=case, event_type=CaseEventType.CASE_LOCK_RELEASED)
    assert release.payload == {
        "reason": FORCED_RELEASE_REASON,
        "forced": True,
        "previous_lock_context": WORKER_CONTEXT,
        "previous_lock_until": previous_until.isoformat(),
        "by": manager_user.username,
        "role": MANAGER_ROLE,
    }
    assert release.actor == manager_user
    assert release.actor_role == MANAGER_ROLE
    assert release.actor_type == ActorType.USER
    # Trilha append-only: o claim do worker fica ANTES do release forçado.
    trail = _event_types(fresh)
    assert trail.index(CaseEventType.CASE_LOCK_CLAIMED) < trail.index(
        CaseEventType.CASE_LOCK_RELEASED
    )


@pytest.mark.django_db
def test_force_release_copies_to_caller_instance_without_service(
    nir_user: User,
    manager_user: User,
) -> None:
    """R3: ``force_release_case_lock`` isolado copia os campos limpos para a
    instância do chamador (contrato que o service usa antes da transição)."""
    case = _case_in_state("FAILED", created_by=nir_user)
    _stuck_worker_lock(case)
    assert case.lock_token is not None

    force_release_case_lock(
        case,
        reason=FORCED_RELEASE_REASON,
        user=manager_user,
        role=MANAGER_ROLE,
    )

    _assert_lock_cleared(case)
    fresh = Case.objects.get(pk=case.case_id)
    _assert_lock_cleared(fresh)


@pytest.mark.django_db
def test_close_without_lock_is_idempotent(nir_user: User, manager_user: User) -> None:
    """R3: caso sem lock → force-release é no-op (idempotente): sem evento de
    release, sem exceção, e o encerramento soma apenas os 2 eventos."""
    case = _case_in_state("AWAITING_DOCTOR", created_by=nir_user)
    events_before = case.events.count()
    assert case.lock_token is None
    assert case.locked_until is None

    force_release_case_lock(
        case, reason=FORCED_RELEASE_REASON, user=manager_user, role=MANAGER_ROLE
    )
    force_release_case_lock(
        case, reason=FORCED_RELEASE_REASON, user=manager_user, role=MANAGER_ROLE
    )
    assert case.events.count() == events_before

    _close(case, user=manager_user)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    assert fresh.events.count() == events_before + 2
    assert CaseEventType.CASE_LOCK_RELEASED not in _event_types(fresh)


# ── R1/R3: recusas fail-closed sem nenhuma escrita ─────────────────────────


@pytest.mark.django_db
def test_close_cleaned_case_rejected(nir_user: User, manager_user: User) -> None:
    """R1: caso já CLEANED é recusado sem transição nem evento."""
    case = _case_in_state("CLEANING", created_by=nir_user)
    case.complete_cleaning(user=nir_user, role=NIR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    events_before = case.events.count()

    with pytest.raises(ValueError, match="encerrado"):
        _close(case, user=manager_user)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    assert fresh.events.count() == events_before


@pytest.mark.django_db
@pytest.mark.parametrize("reason_text", ["", "   ", "\n\t "])
def test_close_requires_reason_text(
    reason_text: str,
    nir_user: User,
    manager_user: User,
) -> None:
    """R1 (cenário da spec): texto vazio/só espaços é rejeitado sem escrita."""
    case = _case_in_state("FAILED", created_by=nir_user)
    events_before = case.events.count()

    with pytest.raises(ValueError, match="obrigatório"):
        _close(case, user=manager_user, reason_text=reason_text)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.FAILED
    assert fresh.events.count() == events_before


@pytest.mark.django_db
@pytest.mark.parametrize("reason_code", ["", "not_a_reason", "PROCESSING_ERROR"])
def test_close_rejects_code_outside_catalog(
    reason_code: str,
    nir_user: User,
    manager_user: User,
) -> None:
    """R1 (cenário da spec): código fora do catálogo fixo é rejeitado."""
    case = _case_in_state("FAILED", created_by=nir_user)
    events_before = case.events.count()

    with pytest.raises(ValueError, match="catálogo"):
        _close(case, user=manager_user, reason_code=reason_code)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.FAILED
    assert fresh.events.count() == events_before
    assert reason_code not in ADMINISTRATIVE_CLOSURE_REASONS


@pytest.mark.django_db
@pytest.mark.parametrize("role", [NIR_ROLE, DOCTOR_ROLE, SCHEDULER_ROLE])
def test_close_rejects_role_outside_manager_and_admin(
    role: str,
    nir_user: User,
    manager_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R1 (defesa em profundidade da spec): papel fora de manager/admin não
    encerra — recusado no serviço mesmo passando pela rota."""
    case = _case_in_state("FAILED", created_by=nir_user)
    events_before = case.events.count()
    other_user = user_factory(f"usuario-{role}", role)

    with pytest.raises(ValueError, match="manager/admin"):
        _close(case, user=other_user, role=role)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.FAILED
    assert fresh.events.count() == events_before
    _assert_lock_cleared(fresh)


@pytest.mark.django_db
def test_close_refused_with_live_worker_lease(
    nir_user: User,
    manager_user: User,
) -> None:
    """R1 (cenário da spec): lock de worker com lease VIVA recusa o
    encerramento — caso, lock, campos clínicos e trilha permanecem intactos."""
    case = _case_in_state("ANONYMIZING", created_by=nir_user)
    _seed_clinical_data(case)
    _attach_documents(case, nir_user, "relatorio-sesab.pdf")
    _claim_worker_lock(case)
    token = case.lock_token
    events_before = case.events.count()

    with pytest.raises(ValueError, match="processamento"):
        _close(case, user=manager_user)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.ANONYMIZING
    assert fresh.lock_token == token
    assert fresh.locked_until is not None
    assert fresh.lock_context == WORKER_CONTEXT
    assert fresh.events.count() == events_before
    assert fresh.documents.count() == 1
    assert fresh.extracted_text == EXTRACTED_TEXT
    assert not UserNotification.objects.filter(case=case).exists()


@pytest.mark.django_db
def test_close_allowed_with_expired_worker_lease(
    nir_user: User,
    manager_user: User,
) -> None:
    """R1: lease EXPIRADA (worker travado) permite o encerramento com
    ``stuck_lock`` — o release forçado limpa o lock remanescente."""
    case = _case_in_state("LLM_SUMMARIZING", created_by=nir_user)
    _stuck_worker_lock(case)

    _close(case, user=manager_user, reason_code="stuck_lock")

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    _assert_lock_cleared(fresh)
    event = CaseEvent.objects.get(case=case, event_type=CaseEventType.CASE_ADMINISTRATIVELY_CLOSED)
    assert event.payload["had_lock"] is True
    assert event.payload["reason_code"] == "stuck_lock"
    assert _event_types(fresh)[-3:] == [
        CaseEventType.CASE_LOCK_RELEASED,
        case_status_event_type(CaseStatus.CLEANED),
        CaseEventType.CASE_ADMINISTRATIVELY_CLOSED,
    ]


@pytest.mark.django_db
def test_close_allowed_with_live_non_worker_lock(
    nir_user: User,
    manager_user: User,
) -> None:
    """R1: lease viva de contexto NÃO-worker (ex.: fila médica) não é recusa —
    a recusa fail-closed é específica do lock de worker (prefixo ``worker_``)."""
    case = _case_in_state("DOCTOR_DENIED", created_by=nir_user)
    _claim_worker_lock(case, context=NON_WORKER_CONTEXT)

    _close(case, user=manager_user)

    fresh = Case.objects.get(pk=case.case_id)
    assert fresh.status == CaseStatus.CLEANED
    _assert_lock_cleared(fresh)
    event = CaseEvent.objects.get(case=case, event_type=CaseEventType.CASE_ADMINISTRATIVELY_CLOSED)
    assert event.payload["had_lock"] is True
    assert event.payload["previous_lock_context"] == NON_WORKER_CONTEXT


# ── R3: notificação fixa ao criador + manual do usuário ────────────────────


@pytest.mark.django_db
def test_close_notifies_creator_with_fixed_text(
    nir_user: User,
    manager_user: User,
) -> None:
    """R3 (cenário da spec): o criador recebe o marco de encerramento
    administrativo com título e preview FIXOS — o motivo nunca entra no texto."""
    case = _case_in_state("FAILED", created_by=nir_user)

    _close(case, user=manager_user)

    notifications = list(UserNotification.objects.filter(case=case))
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.recipient == nir_user
    assert notification.notification_type == NotificationType.ADMINISTRATIVELY_CLOSED
    assert notification.title == ADMIN_CLOSED_TEXT
    assert notification.body_preview == ADMIN_CLOSED_TEXT
    assert notification.event is not None
    assert notification.event.event_type == CaseEventType.CASE_ADMINISTRATIVELY_CLOSED
    # Zero PHI: nem o código, nem o texto do motivo e nem o rótulo do catálogo.
    assert REASON_CODE not in notification.body_preview
    assert REASON_TEXT not in notification.body_preview
    assert ADMINISTRATIVE_CLOSURE_REASONS[REASON_CODE] not in notification.title


@pytest.mark.django_db
def test_close_does_not_notify_the_closer(
    nir_user: User,
    manager_user: User,
) -> None:
    """R3: o destinatário é o CRIADOR do caso — o supervisor que encerrou não
    recebe notificação própria."""
    case = _case_in_state("AWAITING_DOCTOR", created_by=nir_user)

    _close(case, user=manager_user)

    assert not UserNotification.objects.filter(recipient=manager_user).exists()
    assert UserNotification.objects.filter(recipient=nir_user, case=case).count() == 1


@pytest.mark.django_db
def test_manual_lists_administrative_closure_milestone(
    client: Client,
    nir_user: User,
) -> None:
    """R3: o manual do usuário enumera o 4º marco (anti-desatualização)."""
    client.force_login(nir_user)

    response = client.get(reverse("manual"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "encerramento administrativo" in body


def test_closure_reason_catalog_is_exact() -> None:
    """O catálogo de motivos é o fixado pela spec (6 códigos + labels pt-BR)."""
    assert list(ADMINISTRATIVE_CLOSURE_REASONS.items()) == [
        ("processing_error", "Erro de processamento"),
        ("llm_failure", "Falha do LLM"),
        ("system_bug", "Bug do sistema"),
        ("stuck_lock", "Lock travado"),
        ("duplicate_reprocess", "Duplicado/reapresentação manual"),
        ("other", "Outro"),
    ]


@pytest.mark.django_db
def test_public_op_rejects_cleaned_case(nir_user: User, manager_user: User) -> None:
    """A transição FSM exclui CLEANED no próprio modelo (não só no service)."""
    case = _case_in_state("CLEANING", created_by=nir_user)
    case.complete_cleaning(user=nir_user, role=NIR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED

    with pytest.raises(TransitionNotAllowed):
        case.administratively_close(
            user=manager_user,
            role="manager",
            reason_code="other",
            reason_text="já encerrado",
            lock_snapshot={},
        )
