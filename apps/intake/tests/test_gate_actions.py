"""Testes das ações de revisão do gate NIR (slice 005, design D6/D7).

Cobre R1 (``gate_release``: liberar retido → ANONYMIZING com
``CASE_GATE_BYPASSED`` na trilha e o NIR como ator; não-retido → 400 sem
efeito), R2 (``gate_resubmit``: documentos substituídos + flag/texto/nº
zerados + reprocessamento sem novo start; arquivo inválido → 400 nomeando o
arquivo, nada muda), R3 (ações restritas a caso retido do próprio criador) e
R4 (concorrência real release×resubmit — exatamente um vence; lock ativo de
worker conflita; botões apenas quando retido; caso alheio → 404 nas duas
ações). Cobre os 2 cenários da spec "Revisão NIR do gate".

As ações rodam em serviço transacional (``select_for_update`` + re-check DENTRO
da transação); o serviço de reenvio reutiliza a validação de PDFs do slice 001
(``_validate_batch``) e limpa best-effort os arquivos novos em exceção
pós-gravação (D7).

Cobre também os findings de review do slice: o escopo por criador é re-checado
nos serviços sob o row lock (usuário não-criador → not-found ``Http404`` sem
efeito, além do 404 da view); retenção/ownership precedem o lock ativo
(não-retido → 400 mesmo com lease ativa); lock ativo de worker também conflita
no resubmit (409 exato); e a remoção física dos documentos antigos do storage
no reenvio é verificada.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator

import pymupdf
import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections, transaction
from django.http import Http404
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.locks import CaseLockConflictError, claim_case_lock
from apps.cases.models import ActorType, Case, CaseStatus
from apps.intake.services import (
    CaseNotRetainedError,
    create_case_with_documents,
    release_retained_case,
    resubmit_case_documents,
)

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
WORKER_LOCK_CONTEXT = "worker_pdf"
# Motivo canônico de retenção (mesmo shape do motivo do gate, D4).
RETENTION_REASON = "documento fora do padrão SESAB do relatório (motivo: missing_header)"

_RECORD_NUMBER = "33345"
_REPORT_HEADER = "RELATÓRIO DE OCORRÊNCIAS"
_INSTITUTIONAL_SIGNALS = [
    "Central Estadual de Regulação",
    "Secretaria da Saúde do Estado",
    "Governo do Estado da Bahia",
]
_SECTIONS = [
    f"Código: {_RECORD_NUMBER}",
    "Abertura: 01/02/2025",
    "Unid. Origem: Hospital Geral do Estado",
    "Motivo da Solicitação: cateterismo cardíaco diagnóstico",
    "Complemento da Solicitação: paciente encaminhado para avaliação hemodinâmica",
    "Resumo Clínico: paciente de 58 anos com dor torácica em investigação",
    "Dias em tela: 3",
    "Data Adm. Unid.: 01/02/2025",
]
_PADDING = "Linha de preenchimento para atingir o tamanho mínimo do relatório de regulação."
_WATERMARK_BAND = "33345 33345 33345 33345"


def _pdf_bytes(lines: list[str]) -> bytes:
    """Gera um PDF real de 1 página com o texto dado (linha por linha)."""
    document = pymupdf.open()  # type: ignore[no-untyped-call]
    try:
        document.new_page().insert_text((72, 72), "\n".join(lines))
        data = document.tobytes()  # type: ignore[no-untyped-call]
        return bytes(data)
    finally:
        document.close()  # type: ignore[no-untyped-call]


def _standard_report_lines() -> list[str]:
    """Linhas de um relatório SESAB padrão (> threshold de chars, com margem)."""
    lines = [_REPORT_HEADER, *_INSTITUTIONAL_SIGNALS, *_SECTIONS, _WATERMARK_BAND]
    while len("\n".join(lines)) < 560:
        lines.append(_PADDING)
    return lines


def _valid_pdf(name: str) -> SimpleUploadedFile:
    """Upload fake de PDF real do padrão SESAB (texto extraível)."""
    return SimpleUploadedFile(
        name, _pdf_bytes(_standard_report_lines()), content_type="application/pdf"
    )


@pytest.fixture(autouse=True)
def _no_inline_processing() -> Iterator[None]:
    """Módulo sem processamento automático: o teste decide quando reprocessar.

    A criação com ``INTAKE_RUN_TASKS_INLINE=True`` (default da suíte) já
    processa os PDFs e retém/finaliza o caso sozinha. As ações do gate são
    testadas com estados controlados; os testes que exercitam o reenvio +
    reprocessamento ligam o inline pontualmente. ``ANONYMIZATION_RUN_TASKS_INLINE``
    também fica False: estes testes cobrem a semântica/concorrência do GATE,
    não o pipeline de anonimização — o slice 004 do change presidio-
    anonymization (task + fail-closed por texto vazio) é coberto na suíte do
    próprio app (apps/anonymization/tests/test_tasks.py). Desvio incidental
    autorizado (reportado no slice 004).
    """

    with override_settings(
        INTAKE_RUN_TASKS_INLINE=False,
        ANONYMIZATION_RUN_TASKS_INLINE=False,
    ):
        yield


def _create_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    *names: str,
) -> Case:
    """Cria um caso NEW com 1–N PDFs (nomes opcionais) e dois tipos declarados."""
    files = [pdf_factory(name=name) if name else pdf_factory() for name in (names or ("",))]
    return create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        files=files,
        procedure_types=["art_perif", "cat_cardiaco"],
    )


def _retain_for_review(case: Case) -> Case:
    """Simula o resultado do gate (slice 003): retido em PDF_EXTRACTING com flag."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.manual_review_required = True
    case.manual_review_reason = RETENTION_REASON
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    return case


def _create_retained_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    *names: str,
) -> Case:
    """Cria um caso retido do usuário (PDF_EXTRACTING + manual_review_required)."""
    return _retain_for_review(_create_case(user, pdf_factory, *names))


def _event_types(case: Case) -> list[str]:
    return list(case.events.values_list("event_type", flat=True))


# ── R1: gate_release ───────────────────────────────────────────────────────


@pytest.mark.django_db
def test_release_retained_advances(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/cenário spec: liberar retido → ANONYMIZING, flag zerada e bypass com
    o NIR como ator (user + papel ativo da sessão) e o motivo original."""
    case = _create_retained_case(nir_user, pdf_factory, "relatorio-retido.pdf")
    client.force_login(nir_user)

    response = client.post(reverse("intake:gate_release", args=[case.case_id]), follow=True)

    assert response.status_code == 200
    assert "avançou para anonimização" in response.content.decode()

    case.refresh_from_db()
    assert case.status == CaseStatus.ANONYMIZING
    assert case.manual_review_required is False
    assert case.manual_review_reason == ""

    bypass = case.events.get(event_type=CaseEventType.CASE_GATE_BYPASSED)
    assert bypass.actor == nir_user
    assert bypass.actor_type == ActorType.USER
    assert bypass.actor_role == NIR_ROLE
    assert bypass.payload["reason"] == RETENTION_REASON

    # A transição também registra o NIR como ator (papel ativo da sessão).
    advance = case.events.get(event_type=CaseEventType.CASE_STATUS_ANONYMIZING)
    assert advance.actor == nir_user
    assert advance.actor_role == NIR_ROLE


@pytest.mark.django_db
def test_release_not_retained_no_effect(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/R3: liberar caso não-retido (status fora de PDF_EXTRACTING ou sem a
    flag) → 400 sem nenhum efeito."""
    fresh = _create_case(nir_user, pdf_factory, "novo.pdf")
    started = _create_case(nir_user, pdf_factory, "sem-flag.pdf")
    started.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    client.force_login(nir_user)

    for case in (fresh, started):
        events_before = case.events.count()
        documents_before = list(case.documents.values_list("original_filename", flat=True))
        response = client.post(reverse("intake:gate_release", args=[case.case_id]))

        assert response.status_code == 400
        case.refresh_from_db()
        expected_status = CaseStatus.PDF_EXTRACTING if case.pk == started.pk else CaseStatus.NEW
        assert case.status == expected_status
        assert case.manual_review_required is False
        assert case.events.count() == events_before
        assert list(case.documents.values_list("original_filename", flat=True)) == documents_before


# ── R2: gate_resubmit ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_resubmit_replaces_and_reprocesses(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/cenário spec: reenviar PDFs válidos substitui os documentos, zera
    flag/texto/nº e reprocessa até ANONYMIZING sem novo start."""
    case = _retain_for_review(_create_case(nir_user, pdf_factory, "antigo-1.pdf", "antigo-2.pdf"))
    case.extracted_text = "texto antigo fora do padrão"
    case.agency_record_number = "antigo"
    case.save(update_fields=["extracted_text", "agency_record_number"])
    starts_before = _event_types(case).count(CaseEventType.CASE_STATUS_PDF_EXTRACTING.value)
    assert starts_before == 1

    client.force_login(nir_user)
    with override_settings(INTAKE_RUN_TASKS_INLINE=True):
        response = client.post(
            reverse("intake:gate_resubmit", args=[case.case_id]),
            {
                "documents": [
                    _valid_pdf("novo-1.pdf"),
                    _valid_pdf("novo-2.pdf"),
                ]
            },
            follow=True,
        )

    assert response.status_code == 200
    assert "documentos substituídos" in response.content.decode()

    case.refresh_from_db()
    # Documentos substituídos (novos nomes, posições recomeçam em 1).
    documents = list(case.documents.all())
    assert [document.original_filename for document in documents] == ["novo-1.pdf", "novo-2.pdf"]
    assert [document.position for document in documents] == [1, 2]
    assert "antigo-1.pdf" not in [document.original_filename for document in documents]
    # Reprocessado do zero até ANONYMIZING com o novo conteúdo.
    assert case.status == CaseStatus.ANONYMIZING
    assert case.manual_review_required is False
    assert case.manual_review_reason == ""
    assert case.agency_record_number == _RECORD_NUMBER
    assert case.agency_record_extracted_at is not None
    assert "Motivo da Solicitação" in case.extracted_text
    # Sem novo start: o reprocessamento partiu do PDF_EXTRACTING.
    event_types = _event_types(case)
    assert event_types.count(CaseEventType.CASE_STATUS_PDF_EXTRACTING.value) == starts_before
    assert event_types.count(CaseEventType.CASE_STATUS_ANONYMIZING.value) == 1
    assert CaseEventType.CASE_EXTRACTION_COMPLETED.value in event_types


@pytest.mark.django_db
def test_resubmit_invalid_file_no_effect(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: reenvio com arquivo inválido → 400 nomeando o arquivo e zero efeito
    (documentos, flag/texto e trilha intactos)."""
    case = _create_retained_case(nir_user, pdf_factory, "relatorio-retido.pdf")
    case.extracted_text = "texto do documento retido"
    case.save(update_fields=["extracted_text"])
    documents_before = list(case.documents.values_list("original_filename", flat=True))
    events_before = case.events.count()

    client.force_login(nir_user)
    response = client.post(
        reverse("intake:gate_resubmit", args=[case.case_id]),
        {"documents": [pdf_factory(name="foto.jpg", content_type="image/jpeg")]},
    )

    assert response.status_code == 400
    assert "foto.jpg" in response.content.decode()
    case.refresh_from_db()
    assert case.status == CaseStatus.PDF_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == RETENTION_REASON
    assert case.extracted_text == "texto do documento retido"
    assert case.events.count() == events_before
    assert list(case.documents.values_list("original_filename", flat=True)) == documents_before


@pytest.mark.django_db
def test_resubmit_not_retained_no_effect(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: reenviar em caso não-retido (status fora de PDF_EXTRACTING ou sem a
    flag) → 400 sem efeito, mesmo com arquivos válidos."""
    fresh = _create_case(nir_user, pdf_factory, "novo.pdf")
    started = _create_case(nir_user, pdf_factory, "sem-flag.pdf")
    started.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    client.force_login(nir_user)

    for case in (fresh, started):
        events_before = case.events.count()
        documents_before = list(case.documents.values_list("original_filename", flat=True))
        response = client.post(
            reverse("intake:gate_resubmit", args=[case.case_id]),
            {"documents": [_valid_pdf("valido.pdf")]},
        )

        assert response.status_code == 400
        case.refresh_from_db()
        assert case.manual_review_required is False
        assert case.events.count() == events_before
        assert list(case.documents.values_list("original_filename", flat=True)) == documents_before


@pytest.mark.django_db
def test_resubmit_service_zeroes_state_before_reprocess(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: o serviço de reenvio (sem inline) substitui os documentos e zera
    flag/texto/nº ANTES do reprocessamento — critério de aceitação do slice.
    A substituição também remove o arquivo físico antigo do storage (D7)."""
    case = _retain_for_review(_create_case(nir_user, pdf_factory, "antigo.pdf"))
    case.extracted_text = "texto antigo"
    case.agency_record_number = "99999"
    case.agency_record_extracted_at = None
    case.save(update_fields=["extracted_text", "agency_record_number"])
    events_before = case.events.count()
    # Arquivo físico do documento antigo existe no storage ANTES do reenvio.
    old_document = case.documents.get()
    old_stored_name = old_document.file.name
    assert old_stored_name
    assert default_storage.exists(old_stored_name)

    resubmit_case_documents(
        case=case,
        user=nir_user,
        role=NIR_ROLE,
        files=[_valid_pdf("corrigido.pdf")],
    )

    case.refresh_from_db()
    documents = list(case.documents.all())
    assert [document.original_filename for document in documents] == ["corrigido.pdf"]
    assert [document.position for document in documents] == [1]
    assert case.status == CaseStatus.PDF_EXTRACTING
    assert case.extracted_text == ""
    assert case.agency_record_number == ""
    assert case.agency_record_extracted_at is None
    assert case.manual_review_required is False
    assert case.manual_review_reason == ""
    # O arquivo físico antigo NÃO existe mais no storage; o novo está gravado.
    assert not default_storage.exists(old_stored_name)
    new_document = case.documents.get()
    new_stored_name = new_document.file.name
    assert new_stored_name
    assert default_storage.exists(new_stored_name)
    # O serviço não grava eventos próprios — a trilha segue intacta até o task.
    assert case.events.count() == events_before


# ── R3/R4: escopo, 404, botões, lock e concorrência ────────────────────────


@pytest.mark.django_db
def test_foreign_case_404_both_actions(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3/D6: ações em caso alheio → 404 nas duas rotas, sem efeito no caso."""
    other_nir = user_factory("nir-alheio-gate", NIR_ROLE)
    foreign = _create_retained_case(other_nir, pdf_factory, "alheio.pdf")
    events_before = foreign.events.count()

    client.force_login(nir_user)
    release_url = reverse("intake:gate_release", args=[foreign.case_id])
    resubmit_url = reverse("intake:gate_resubmit", args=[foreign.case_id])

    assert client.post(release_url).status_code == 404
    assert client.post(resubmit_url, {"documents": [_valid_pdf("valido.pdf")]}).status_code == 404

    foreign.refresh_from_db()
    assert foreign.status == CaseStatus.PDF_EXTRACTING
    assert foreign.manual_review_required is True
    assert foreign.events.count() == events_before
    assert foreign.documents.count() == 1


@pytest.mark.django_db
def test_service_denies_non_creator_both_actions(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: além do 404 da view, os serviços re-checam o criador DENTRO da
    transação — usuário não-criador chamando o serviço direto é negado com
    not-found (sem vazar informação), sem efeito nas duas ações."""
    owner = user_factory("dono-servico-gate", NIR_ROLE)
    case = _create_retained_case(owner, pdf_factory, "alheio-servico.pdf")
    events_before = case.events.count()
    documents_before = list(case.documents.values_list("original_filename", flat=True))

    with pytest.raises(Http404):
        release_retained_case(case=case, user=nir_user, role=NIR_ROLE)
    with pytest.raises(Http404):
        resubmit_case_documents(
            case=case,
            user=nir_user,
            role=NIR_ROLE,
            files=[_valid_pdf("valido.pdf")],
        )

    case.refresh_from_db()
    assert case.status == CaseStatus.PDF_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == RETENTION_REASON
    assert case.events.count() == events_before
    assert list(case.documents.values_list("original_filename", flat=True)) == documents_before


@pytest.mark.django_db
def test_detail_actions_only_when_retained(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/R2: botões de liberar/reenviar aparecem no detalhe só para caso retido."""
    retained = _create_retained_case(nir_user, pdf_factory, "retido.pdf")
    free = _create_case(nir_user, pdf_factory, "livre.pdf")
    client.force_login(nir_user)

    body_retained = client.get(
        reverse("intake:case_detail", args=[retained.case_id])
    ).content.decode()
    assert reverse("intake:gate_release", args=[retained.case_id]) in body_retained
    assert reverse("intake:gate_resubmit", args=[retained.case_id]) in body_retained
    assert 'name="documents"' in body_retained
    assert "Liberar caso" in body_retained
    assert "Reenviar documentos" in body_retained

    body_free = client.get(reverse("intake:case_detail", args=[free.case_id])).content.decode()
    assert reverse("intake:gate_release", args=[free.case_id]) not in body_free
    assert reverse("intake:gate_resubmit", args=[free.case_id]) not in body_free


@pytest.mark.django_db
def test_active_worker_lock_conflicts(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: caso retido sob lock ativo de worker → a ação conflita
    (CaseLockConflictError → 409) sem efeito."""
    case = _create_retained_case(nir_user, pdf_factory, "retido.pdf")
    claim_case_lock(case, user=None, context=WORKER_LOCK_CONTEXT, role=SYSTEM_ROLE)
    events_before = case.events.count()
    documents_before = list(case.documents.values_list("original_filename", flat=True))

    client.force_login(nir_user)
    response = client.post(reverse("intake:gate_release", args=[case.case_id]))

    assert response.status_code == 409
    case.refresh_from_db()
    assert case.status == CaseStatus.PDF_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == RETENTION_REASON
    assert case.events.count() == events_before
    assert list(case.documents.values_list("original_filename", flat=True)) == documents_before


@pytest.mark.django_db
def test_resubmit_active_worker_lock_conflicts(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: caso retido sob lock ativo de worker → resubmit também conflita
    (CaseLockConflictError → 409 exato) sem efeito — mesma cobertura de lock
    que o release."""
    case = _create_retained_case(nir_user, pdf_factory, "retido.pdf")
    claim_case_lock(case, user=None, context=WORKER_LOCK_CONTEXT, role=SYSTEM_ROLE)
    events_before = case.events.count()
    documents_before = list(case.documents.values_list("original_filename", flat=True))

    client.force_login(nir_user)
    response = client.post(
        reverse("intake:gate_resubmit", args=[case.case_id]),
        {"documents": [_valid_pdf("valido.pdf")]},
    )

    assert response.status_code == 409
    case.refresh_from_db()
    assert case.status == CaseStatus.PDF_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == RETENTION_REASON
    assert case.events.count() == events_before
    assert list(case.documents.values_list("original_filename", flat=True)) == documents_before


@pytest.mark.django_db
def test_not_retained_precedes_active_lock(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: a retenção precede o lock ativo — caso não-retido sob lease ativa
    responde 400 (não 409) nas duas ações, sem efeito."""
    started = _create_case(nir_user, pdf_factory, "sem-flag.pdf")
    started.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    claim_case_lock(started, user=None, context=WORKER_LOCK_CONTEXT, role=SYSTEM_ROLE)
    events_before = started.events.count()
    documents_before = list(started.documents.values_list("original_filename", flat=True))

    client.force_login(nir_user)
    release_response = client.post(reverse("intake:gate_release", args=[started.case_id]))
    resubmit_response = client.post(
        reverse("intake:gate_resubmit", args=[started.case_id]),
        {"documents": [_valid_pdf("valido.pdf")]},
    )

    assert release_response.status_code == 400
    assert resubmit_response.status_code == 400
    started.refresh_from_db()
    assert started.status == CaseStatus.PDF_EXTRACTING
    assert started.manual_review_required is False
    assert started.manual_review_reason == ""
    assert started.events.count() == events_before
    assert list(started.documents.values_list("original_filename", flat=True)) == documents_before


@pytest.mark.django_db
def test_non_nir_forbidden_on_gate_actions(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: papel ativo fora de nir → 403 nas duas ações do gate."""
    case = _create_retained_case(nir_user, pdf_factory, "retido.pdf")
    doctor = user_factory("doctor-gate", DOCTOR_ROLE)
    client.force_login(doctor)

    release_url = reverse("intake:gate_release", args=[case.case_id])
    resubmit_url = reverse("intake:gate_resubmit", args=[case.case_id])
    assert client.post(release_url).status_code == 403
    assert client.post(resubmit_url, {"documents": [_valid_pdf("valido.pdf")]}).status_code == 403


def _run_contender(
    entered: threading.Event,
    finished: threading.Event,
    act: Callable[[], None],
) -> threading.Thread:
    """Thread contendora: sinaliza ``entered`` ao entrar e ``finished`` ao sair."""

    def _runner() -> None:
        try:
            entered.set()
            act()
        finally:
            finished.set()
            connections.close_all()

    thread = threading.Thread(target=_runner)
    thread.start()
    return thread


_CONTEND_GRACE_SECONDS = 0.5


@pytest.mark.django_db(transaction=True)
def test_concurrent_release_and_resubmit_exactly_one_wins(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: release e resubmit simultâneos sobre o mesmo caso retido — exatamente
    um vence; o outro vê erro de estado/conflito sem efeito."""
    case = _create_retained_case(nir_user, pdf_factory, "retido.pdf")
    new_files = [_valid_pdf("reenvio.pdf")]

    wins: list[str] = []
    errors: list[BaseException] = []

    def _release() -> None:
        try:
            release_retained_case(case=case, user=nir_user, role=NIR_ROLE)
            wins.append("release")
        except (CaseNotRetainedError, CaseLockConflictError) as exc:
            errors.append(exc)

    def _resubmit() -> None:
        try:
            resubmit_case_documents(case=case, user=nir_user, role=NIR_ROLE, files=new_files)
            wins.append("resubmit")
        except (CaseNotRetainedError, CaseLockConflictError) as exc:
            errors.append(exc)

    entered_release = threading.Event()
    entered_resubmit = threading.Event()
    finished_release = threading.Event()
    finished_resubmit = threading.Event()
    with transaction.atomic():
        # Row lock do case segurado: as duas ações chegam e ficam presas no
        # select_for_update até o commit (saída do bloco) liberar a linha.
        Case.objects.select_for_update().get(pk=case.pk)
        thread_release = _run_contender(entered_release, finished_release, _release)
        thread_resubmit = _run_contender(entered_resubmit, finished_resubmit, _resubmit)
        assert entered_release.wait(timeout=10)
        assert entered_resubmit.wait(timeout=10)
        time.sleep(_CONTEND_GRACE_SECONDS)
        assert not finished_release.is_set()
        assert not finished_resubmit.is_set()
    thread_release.join(timeout=60)
    thread_resubmit.join(timeout=60)

    assert not thread_release.is_alive()
    assert not thread_resubmit.is_alive()
    assert len(wins) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], CaseNotRetainedError)

    case.refresh_from_db()
    assert case.manual_review_required is False
    if "release" in wins:
        assert case.status == CaseStatus.ANONYMIZING
        assert case.agency_record_number == ""
        assert CaseEventType.CASE_GATE_BYPASSED.value in _event_types(case)
        # Documentos antigos preservados (sem substituição).
        assert list(case.documents.values_list("original_filename", flat=True)) == ["retido.pdf"]
    else:
        assert case.status == CaseStatus.PDF_EXTRACTING
        assert case.extracted_text == ""
        assert case.agency_record_number == ""
        assert CaseEventType.CASE_STATUS_ANONYMIZING.value not in _event_types(case)
        # Documentos substituídos pelo reenvio.
        assert list(case.documents.values_list("original_filename", flat=True)) == ["reenvio.pdf"]
