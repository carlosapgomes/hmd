"""Testes do bloqueio fail-closed do intake (change pilot-deployment-v0-1-1, slice 002).

Cobre:
- R1: setting ``INTAKE_ENABLED`` — default true em base/dev/teste e false em
  prod (import de ``config.settings.prod`` com envs dummy, mesmo padrão de
  ``apps/accounts/tests/test_health.py``); override explícito por env.
- R2: guard ``_assert_intake_enabled()`` no TOPO das quatro funções de
  criação/envio (``submit_report_batch``, ``create_case_with_documents``,
  ``create_corrected_resubmission``, ``resubmit_case_documents``) — erro
  nomeado ANTES de qualquer validação/escrita/arquivo, cobrindo callers
  não-HTTP.
- R3: nos 3 POSTs (``intake:home``, ``intake:case_resubmit``,
  ``intake:gate_resubmit``) o intake desligado responde com flash + redirect
  (302), nunca 4xx/5xx; GETs seguem renderizando.
- R4: com ``INTAKE_ENABLED=False`` nada é criado/enfileirado (0 rows de
  Case/CaseDocument/CaseAttachment + 0 enfileiramentos com
  ``INTAKE_RUN_TASKS_INLINE=False`` + assert de ``async_task``); com o default
  true (dev/teste) a criação segue normal (regressão).

O desligamento é testado pelo valor SETTING (``override_settings``), o mesmo
que as views/serviço leem; o default de prod é verificado pelo import do
módulo de settings (a suíte roda com ``config.settings.test``).
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from types import ModuleType

import pytest
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.attachments.models import CaseAttachment
from apps.cases.models import Case, CaseDocument, CaseEvent, CaseProcedure, CaseStatus
from apps.intake.services import (
    INTAKE_DISABLED_MESSAGE,
    IntakeValidationError,
    create_case_with_documents,
    create_corrected_resubmission,
    resubmit_case_documents,
    submit_report_batch,
)

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
RETENTION_REASON = "documento fora do padrão SESAB do relatório"
CORRECTION_REASON = "laudo com dados divergentes do exame original"

PROD_SECRET_KEY = "chave-de-teste-de-producao"
PROD_DATABASE_URL = "postgres://build:build@localhost/build"


@pytest.fixture(autouse=True)
def _no_inline_processing() -> Iterator[None]:
    """Enfileiramento observável: nenhuma task roda inline nos testes do slice.

    Com ``INTAKE_RUN_TASKS_INLINE=False`` o caminho de sucesso enfileira via
    ``async_task`` — os testes de bloqueio assertam 0 chamadas (assert de
    NÃO-enfileiramento, padrão da suíte do change 04).
    """

    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


def _load_prod_settings(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Importa ``config.settings.prod`` com as envs mínimas (sem secret real)."""
    monkeypatch.setenv("DJANGO_SECRET_KEY", PROD_SECRET_KEY)
    monkeypatch.setenv("DATABASE_URL", PROD_DATABASE_URL)
    sys.modules.pop("config.settings.prod", None)
    return importlib.import_module("config.settings.prod")


def _assert_no_intake_rows() -> None:
    """Zero efeito: nenhum caso/documento/anexo/procedimento/evento persistido."""
    assert Case.objects.count() == 0
    assert CaseDocument.objects.count() == 0
    assert CaseAttachment.objects.count() == 0
    assert CaseProcedure.objects.count() == 0
    assert CaseEvent.objects.count() == 0


def _assert_storage_empty() -> None:
    """Zero efeito no filesystem: nenhum arquivo gravado no storage."""
    assert default_storage.listdir("") == ([], [])


def _enqueue_recorder(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Intercepta ``async_task`` e devolve a lista de enfileiramentos."""
    calls: list[object] = []

    def _recorder(*args: object, **kwargs: object) -> None:
        del kwargs
        calls.append(args)

    monkeypatch.setattr("apps.intake.tasks.async_task", _recorder)
    return calls


def _create_case(user: User, pdf_factory: Callable[..., SimpleUploadedFile]) -> Case:
    """Caso NEW com 1 PDF e 1 tipo declarado (setup, intake habilitado)."""
    return create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(),
        procedure_type="cat_cardiaco",
    )


def _retain_for_review(case: Case) -> Case:
    """Simula o resultado do gate (slice 003): retido em PDF_EXTRACTING."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.manual_review_required = True
    case.manual_review_reason = RETENTION_REASON
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    return case


def _cleaned_case(user: User, pdf_factory: Callable[..., SimpleUploadedFile]) -> Case:
    """Caso dirigido pelas operações FSM até ``CLEANED`` (pré-req. do reenvio)."""
    case = _create_case(user, pdf_factory)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    case.record_doctor_decision(accepted=False, user=user, role=DOCTOR_ROLE)
    case.post_final_reply(user=user, role=DOCTOR_ROLE)
    case.nir_acknowledge(user=user, role=NIR_ROLE)
    case.start_cleaning(user=user, role=NIR_ROLE)
    case.complete_cleaning(user=user, role=NIR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    return case


# ── R1: setting INTAKE_ENABLED ─────────────────────────────────────────────


def test_base_default_enabled() -> None:
    """R1: a base (dev/teste) mantém o intake LIGADO por default."""
    assert settings.INTAKE_ENABLED is True


def test_prod_default_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """R1: produção força o intake DESLIGADO sem env definida (fail-closed)."""
    monkeypatch.delenv("INTAKE_ENABLED", raising=False)

    prod = _load_prod_settings(monkeypatch)

    assert prod.INTAKE_ENABLED is False


def test_prod_enabled_by_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """R1: env explícita religa o intake em produção (mesmo parse dos flags)."""
    monkeypatch.setenv("INTAKE_ENABLED", "true")

    prod = _load_prod_settings(monkeypatch)

    assert prod.INTAKE_ENABLED is True


# ── R2: guard de serviço fail-closed ───────────────────────────────────────


@pytest.mark.django_db
def test_create_case_disabled_raises_before_any_effect(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2/cenário spec: serviço direto levanta erro nomeado sem criar nada —
    nem caso/documento/anexo, nem arquivo no storage, nem enfileiramento."""
    enqueued = _enqueue_recorder(monkeypatch)
    attachment = SimpleUploadedFile("evidencia.png", b"imagem fake", content_type="image/png")

    with override_settings(INTAKE_ENABLED=False):
        with pytest.raises(IntakeValidationError, match=INTAKE_DISABLED_MESSAGE):
            create_case_with_documents(
                user=nir_user,
                role=NIR_ROLE,
                file=pdf_factory(),
                procedure_type="cat_cardiaco",
                attachments=[attachment],
            )

    _assert_no_intake_rows()
    _assert_storage_empty()
    assert enqueued == []


@pytest.mark.django_db
def test_submit_batch_disabled_raises_before_any_effect(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2/cenário spec: o envio em lote desligado levanta erro nomeado sem
    criar nada — nem caso/documento, nem enfileiramento (inventário fail-closed)."""
    enqueued = _enqueue_recorder(monkeypatch)

    with override_settings(INTAKE_ENABLED=False):
        with pytest.raises(IntakeValidationError, match=INTAKE_DISABLED_MESSAGE):
            submit_report_batch(
                user=nir_user,
                role=NIR_ROLE,
                files=[pdf_factory(), pdf_factory()],
                procedure_type="cat_cardiaco",
            )

    _assert_no_intake_rows()
    _assert_storage_empty()
    assert enqueued == []


@pytest.mark.django_db
def test_create_case_disabled_guard_precedes_validation(
    nir_user: User,
) -> None:
    """R2: o guard roda no TOPO — lote vazio (inválido) ainda falha pelo intake
    desligado, provando que nenhuma validação/write precede o bloqueio."""
    with override_settings(INTAKE_ENABLED=False):
        with pytest.raises(IntakeValidationError, match=INTAKE_DISABLED_MESSAGE):
            submit_report_batch(
                user=nir_user,
                role=NIR_ROLE,
                files=[],
                procedure_type="",
            )

    _assert_no_intake_rows()
    _assert_storage_empty()


@pytest.mark.django_db
def test_resubmit_documents_disabled_no_side_effect(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: reenvio de documentos com intake desligado não substitui nada."""
    case = _retain_for_review(_create_case(nir_user, pdf_factory))
    documents_before = list(case.documents.values_list("original_filename", flat=True))
    events_before = case.events.count()
    enqueued = _enqueue_recorder(monkeypatch)

    with override_settings(INTAKE_ENABLED=False):
        with pytest.raises(IntakeValidationError, match=INTAKE_DISABLED_MESSAGE):
            resubmit_case_documents(
                case=case,
                user=nir_user,
                role=NIR_ROLE,
                files=[pdf_factory(name="reenvio.pdf")],
            )

    case.refresh_from_db()
    assert list(case.documents.values_list("original_filename", flat=True)) == documents_before
    assert case.events.count() == events_before
    assert case.manual_review_required is True
    assert enqueued == []


@pytest.mark.django_db
def test_corrected_resubmission_disabled_no_side_effect(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: reenvio corrigido com intake desligado não cria o novo caso."""
    original = _cleaned_case(nir_user, pdf_factory)
    enqueued = _enqueue_recorder(monkeypatch)

    with override_settings(INTAKE_ENABLED=False):
        with pytest.raises(IntakeValidationError, match=INTAKE_DISABLED_MESSAGE):
            create_corrected_resubmission(
                original_case=original,
                user=nir_user,
                role=NIR_ROLE,
                file=pdf_factory(name="corrigido.pdf"),
                procedure_type="cat_cardiaco",
                correction_reason=CORRECTION_REASON,
            )

    assert Case.objects.count() == 1
    assert Case.objects.filter(corrects_case=original).count() == 0
    assert not any(
        event.event_type == "case_correction_created"
        for event in CaseEvent.objects.filter(case=original)
    )
    assert enqueued == []


# ── R3: rotas de POST respondem sem efeito ─────────────────────────────────


@pytest.mark.django_db
def test_home_get_disabled_still_renders(client: Client, nir_user: User) -> None:
    """R3: o GET da home segue renderizando o form (UI inalterada)."""
    client.force_login(nir_user)

    with override_settings(INTAKE_ENABLED=False):
        response = client.get(reverse("intake:home"))

    assert response.status_code == 200
    _assert_no_intake_rows()


@pytest.mark.django_db
def test_home_post_disabled_redirects_without_creating(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3/R4/cenário spec: POST de criação bloqueado → flash + redirect, sem
    criar caso/documento/anexo e sem enfileirar tarefa."""
    enqueued = _enqueue_recorder(monkeypatch)
    client.force_login(nir_user)

    with override_settings(INTAKE_ENABLED=False):
        response = client.post(
            reverse("intake:home"),
            {
                "documents": [pdf_factory()],
                "procedure_type": "cat_cardiaco",
            },
            follow=True,
        )

    assert response.status_code == 200
    assert response.redirect_chain == [(reverse("intake:home"), 302)]
    assert INTAKE_DISABLED_MESSAGE in response.content.decode()
    _assert_no_intake_rows()
    assert enqueued == []


@pytest.mark.django_db
def test_resubmit_post_disabled_redirects_without_creating(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3/R4: POST do reenvio corrigido bloqueado → redirect ao detalhe, sem
    criar o novo caso e sem enfileirar tarefa."""
    original = _cleaned_case(nir_user, pdf_factory)
    detail_url = reverse("intake:case_detail", args=[original.case_id])
    enqueued = _enqueue_recorder(monkeypatch)
    client.force_login(nir_user)

    with override_settings(INTAKE_ENABLED=False):
        response = client.post(
            reverse("intake:case_resubmit", args=[original.case_id]),
            {
                "documents": [pdf_factory(name="corrigido.pdf")],
                "procedure_type": "cat_cardiaco",
                "correction_reason": CORRECTION_REASON,
            },
            follow=True,
        )

    assert response.status_code == 200
    assert response.redirect_chain == [(detail_url, 302)]
    assert INTAKE_DISABLED_MESSAGE in response.content.decode()
    assert Case.objects.count() == 1
    assert enqueued == []


@pytest.mark.django_db
def test_gate_resubmit_post_disabled_redirects_without_effect(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3/R4: POST do reenvio do gate bloqueado → redirect ao detalhe (302, não
    4xx), mantendo documentos/flag intactos e sem enfileirar tarefa."""
    case = _retain_for_review(_create_case(nir_user, pdf_factory))
    documents_before = list(case.documents.values_list("original_filename", flat=True))
    detail_url = reverse("intake:case_detail", args=[case.case_id])
    enqueued = _enqueue_recorder(monkeypatch)
    client.force_login(nir_user)

    with override_settings(INTAKE_ENABLED=False):
        response = client.post(
            reverse("intake:gate_resubmit", args=[case.case_id]),
            {"documents": [pdf_factory(name="reenvio.pdf")]},
            follow=True,
        )

    assert response.status_code == 200
    assert response.redirect_chain == [(detail_url, 302)]
    assert INTAKE_DISABLED_MESSAGE in response.content.decode()
    case.refresh_from_db()
    assert list(case.documents.values_list("original_filename", flat=True)) == documents_before
    assert case.manual_review_required is True
    assert enqueued == []


# ── R4: default true segue criando (regressão mínima) ──────────────────────


@pytest.mark.django_db
def test_enabled_still_creates(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: com o intake ligado (default dev/teste) a criação segue normal."""
    with override_settings(INTAKE_ENABLED=True):
        case = create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(),
            procedure_type="cat_cardiaco",
        )

    assert case.status == CaseStatus.NEW
    assert Case.objects.count() == 1
    assert case.documents.count() == 1


# ── Guardas compartilhadas do módulo ───────────────────────────────────────


@pytest.mark.django_db
def test_disabled_guard_is_named_value_error(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: o erro do guard é um ``ValueError`` nomeado (callers não-HTTP o
    capturam como qualquer outro erro de rejeição)."""
    with override_settings(INTAKE_ENABLED=False):
        with pytest.raises(ValueError, match=INTAKE_DISABLED_MESSAGE):
            create_case_with_documents(
                user=nir_user,
                role=NIR_ROLE,
                file=pdf_factory(),
                procedure_type="cat_cardiaco",
            )

    _assert_no_intake_rows()
