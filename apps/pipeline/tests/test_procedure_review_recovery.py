"""Recuperação de dispatch pós-confirmação (slice 003, R1–R4, design D7).

Cobre, sem simular o callback on_commit real: falha de dispatch após
confirmação commitada preserva a confirmação e agenda recovery ONCE (cluster
llm, 60 s) com auditoria em transação própria; falha do Schedule ainda
audita e responde 503; falha da transação inteira de recovery/auditoria não
reverte a confirmação nem anuncia Schedule. Deduplicação, UUID serializado,
execução real do recovery sem workers de PDF, jobs atrasados/fora de
estado/fechados sem duplicação e LLM error → FAILED sem loops.

Os três testes HTTP usam ``transaction=True`` para a cadeia pós-commit real
(serviço → signal on_commit → dispatch), com broker simulado mas banco real.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Sequence
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.db import DatabaseError
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.cases.events import CaseEventType
from apps.cases.models import (
    ActorType,
    Case,
    CaseDocument,
    CaseEvent,
    CaseProcedure,
    CaseStatus,
)
from apps.cases.procedure_review import (
    latest_confirmation_event,
    latest_review_event,
    source_fingerprint_for_case,
)
from apps.cases.procedures import get_declared_procedure_types
from apps.pipeline.llm import LlmError
from apps.pipeline.orchestrator import process_case_pipeline

NIR_ROLE = "nir"
SYSTEM_ROLE = "system"
DECLARED_TYPE = "art_perif"
DETECTED_TYPE = "cat_cardiaco"
FAKE_MODEL_LLM1 = "modelo-llm1-teste"
FAKE_MODEL_LLM2 = "modelo-llm2-teste"

# Justificativa-canário com dados fictícios: nunca pode vazar para payload
# de evento, args do Schedule, logs técnicos ou corpo HTTP de erro.
_CANARY_JUSTIFICATION = (
    "Confirmo apos leitura do relatorio principal; paciente Fulano de Tal Silva CPF 123.456.789-00."
)

_ANONYMIZED_TEXT = (
    "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente. "
    "Solicitada avaliacao hemodinamica."
)
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "FULANO DE TAL SILVA", "entity_type": "PERSON"},
}

RECOVERY_FUNC = "apps.pipeline.orchestrator.process_case_pipeline"


class ScriptedLlmClient:
    """Fake capturável: substitui só o transporte; serviços/sinais reais."""

    def __init__(self, responses: list[Any]) -> None:
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
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return str(item)


def _artifact(procedure_types: list[str]) -> dict[str, object]:
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
        "contexto_clinico": "Paciente <PESSOA_1> com claudicacao intermitente ha tres meses.",
        "linha_do_tempo": [],
        "exames": None,
        "medicacoes": [],
        "comorbidades": [],
        "contraindicacoes": [],
        "trechos_nao_classificados": [],
    }


def _llm2_response(procedure_types: list[str]) -> dict[str, object]:
    return {
        "summary_text": "Sumario clinico em portugues para o procedimento confirmado.",
        "procedures": [
            {
                "procedure_type": procedure_type,
                "suggestion": "aceitar",
                "motivos": ["sem contraindicoes identificadas"],
                "evidence_spans": [],
            }
            for procedure_type in procedure_types
        ],
    }


@pytest.fixture(autouse=True)
def _seeded_prompts() -> Any:
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture(autouse=True)
def _no_llm_inline_by_default() -> Any:
    """Pipeline parado por padrão: transações reais disparam on_commit de
    verdade, sem rodar o worker inline fora dos escopos explícitos."""
    with override_settings(LLM_RUN_TASKS_INLINE=False):
        yield


@pytest.fixture
def nir_user() -> User:
    role, _ = Role.objects.get_or_create(name=NIR_ROLE)
    user = User.objects.create_user(username="nir-recovery", password="senha-teste")
    user.roles.add(role)
    return user


def _chain(fake: ScriptedLlmClient) -> Any:
    """Override da cadeia real (inline + fake) para criação e reprocessamento."""
    return override_settings(
        LLM_RUN_TASKS_INLINE=True,
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    )


def _make_marked_case(owner: User, *, declared: str = DECLARED_TYPE) -> Case:
    """Caso marcado em LLM_EXTRACTING com fonte/anonimização reais."""
    case = Case.objects.create(created_by=owner)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.LLM_EXTRACTING
    content = b"%PDF-1.4 relatorio sintetico de recuperacao"
    document = CaseDocument(
        case=case,
        position=1,
        original_filename="relatorio.pdf",
        content_type="application/pdf",
        size_bytes=len(content),
        uploaded_by=owner,
    )
    from django.core.files.base import ContentFile

    document.file.save("relatorio.pdf", ContentFile(content), save=False)
    document.save()
    case.extracted_text = "Texto extraido do relatorio sintetico de recuperacao."
    case.anonymized_text = _ANONYMIZED_TEXT
    case.pseudonym_map = dict(_PSEUDONYMS)
    case.save()
    CaseProcedure.objects.create(case=case, procedure_type=declared, declared_by_nir=True)
    CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={"version": 1},
    )
    return case


def _retain_with_divergence(case: Case, detected: list[str]) -> ScriptedLlmClient:
    """Roda a LLM1 real com fake até a retenção por divergência."""
    fake = ScriptedLlmClient([json.dumps(_artifact(detected))])
    with _chain(fake):
        process_case_pipeline(case.case_id)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == "procedure_divergence"
    assert len(fake.calls) == 1
    return fake


def _confirm_post_data(case: Case, *, justification: str = _CANARY_JUSTIFICATION) -> dict[str, Any]:
    review = latest_review_event(case)
    assert review is not None
    fingerprint = source_fingerprint_for_case(case)
    assert fingerprint is not None
    return {
        "procedure_type": DECLARED_TYPE,
        "review_justification": justification,
        "review_acknowledged": "on",
        "review_event_id": str(review.pk),
        "source_fingerprint": fingerprint,
    }


def _fail_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Simula broker indisponível mantendo o banco real (sem mock do signal)."""
    calls: list[Any] = []

    def _boom(case_id: Any) -> None:
        calls.append(case_id)
        raise RuntimeError("broker indisponivel (simulado)")

    monkeypatch.setattr("apps.pipeline.tasks.enqueue_case_pipeline", _boom)
    return calls


def _confirm_via_service(case: Case, owner: User, fake: ScriptedLlmClient) -> Any:
    """Confirma via serviço real e roda o dispatch explícito (testes atômicos)."""
    from apps.intake.services import confirm_case_procedure_review

    data = _confirm_post_data(case)
    with _chain(fake):
        confirm_case_procedure_review(
            case=case,
            user=owner,
            role=NIR_ROLE,
            procedure_type=str(data["procedure_type"]),
            review_acknowledged=data["review_acknowledged"],
            review_justification=str(data["review_justification"]),
            review_event_id=data["review_event_id"],
            source_fingerprint=str(data["source_fingerprint"]),
        )
        # Em teste atômico o on_commit do signal nunca dispara: o dispatch
        # explícito abaixo equivale ao que o callback faria no commit real.
        process_case_pipeline(case.case_id)
    case.refresh_from_db()
    return latest_confirmation_event(case)


def _recovery_schedules() -> list[Any]:
    from django_q.models import Schedule

    return list(Schedule.objects.filter(func=RECOVERY_FUNC))


# ── R1/R3: falha de dispatch → confirmação durável + recovery programado ──


@pytest.mark.django_db(transaction=True)
def test_dispatch_failure_preserves_confirmation_and_schedules_recovery(
    client: Client,
    nir_user: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R1: POST com dispatch quebrado ainda confirma e agenda recovery ONCE."""
    case = _make_marked_case(nir_user)
    _retain_with_divergence(case, [DETECTED_TYPE])
    _fail_dispatch(monkeypatch)
    client.force_login(nir_user)

    with caplog.at_level("WARNING", logger="apps.pipeline.tasks"):
        response = client.post(
            reverse("intake:procedure_review_confirm", args=[case.case_id]),
            data=_confirm_post_data(case),
        )

    # Outcome correto: redirect 302 com aviso (nunca 500 nem sucesso falso).
    assert response.status_code == 302
    assert response["Location"] == reverse("intake:case_detail", args=[case.case_id])
    follow = client.get(response["Location"])
    assert "programada" in follow.content.decode().lower()

    # Confirmação durável: evento + pedido + declarado intactos.
    case.refresh_from_db()
    confirmation = latest_confirmation_event(case)
    assert confirmation is not None
    assert list(get_declared_procedure_types(case)) == [DECLARED_TYPE]
    assert (
        case.events.filter(event_type=CaseEventType.CASE_PROCEDURE_REPROCESSING_REQUESTED).count()
        == 1
    )

    # Schedule inspecionado (nunca mock cego): alvo, cluster, ONCE, args, nome.
    schedules = _recovery_schedules()
    assert len(schedules) == 1
    sched = schedules[0]
    assert sched.schedule_type == "O"
    assert sched.cluster == "llm"
    assert str(case.case_id) in (sched.args or "")
    assert str(confirmation.pk) in (sched.args or "")
    assert str(case.case_id) in (sched.name or "")
    assert str(confirmation.pk) in (sched.name or "")
    delta = (sched.next_run - timezone.now()).total_seconds()
    assert 30 < delta < 180
    # Args serializados executam: parse real do que o scheduler avaliará.
    parsed = ast.literal_eval(sched.args or "")
    assert isinstance(parsed, tuple) and len(parsed) == 2

    # Auditoria terse: só código + outcome, sem livre/clínico/justificativa.
    event = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED)
    assert event.payload == {
        "confirmation_event_id": confirmation.pk,
        "recovery_scheduled": True,
        "reason_code": "enqueue_failed",
    }
    sensitive = (sched.args or "") + json.dumps(event.payload)
    assert "Fulano" not in sensitive
    assert "123.456" not in sensitive
    assert "Fulano" not in caplog.text
    # Paridade total (design D4, decisão do dono 2026-11-21, opção B): a trilha
    # no detalhe do NIR exibe a justificativa do evento
    # CASE_PROCEDURE_REVIEW_CONFIRMED, como o painel.
    assert "Justificativa:" in follow.content.decode()
    # Anti-drift do slice 003 (trilha-eventos-todos-perfis): pinar também um
    # fragmento distintivo do VALOR enviado — o rótulo sozinho passaria mesmo
    # se o texto livre deixasse de ser exibido (decisão B exige o texto).
    assert "Confirmo apos leitura" in follow.content.decode()


@pytest.mark.django_db(transaction=True)
def test_schedule_failure_persists_audit_and_returns_503(
    client: Client, nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3: Schedule quebrado → confirmação durável + auditoria + 503 suporte."""
    case = _make_marked_case(nir_user)
    _retain_with_divergence(case, [DETECTED_TYPE])
    _fail_dispatch(monkeypatch)

    def _schedule_boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("schedule indisponivel (simulado)")

    monkeypatch.setattr("django_q.tasks.schedule", _schedule_boom)
    client.force_login(nir_user)

    response = client.post(
        reverse("intake:procedure_review_confirm", args=[case.case_id]),
        data=_confirm_post_data(case),
    )

    assert response.status_code == 503
    body = response.content.decode()
    assert "suporte" in body.lower()
    assert "<form" not in body.lower()
    assert "tentar novamente" not in body.lower()
    assert "Fulano" not in body
    assert "123.456" not in body

    case.refresh_from_db()
    confirmation = latest_confirmation_event(case)
    assert confirmation is not None
    assert list(get_declared_procedure_types(case)) == [DECLARED_TYPE]
    event = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED)
    assert event.payload == {
        "confirmation_event_id": confirmation.pk,
        "recovery_scheduled": False,
        "reason_code": "schedule_failed",
    }
    from django_q.models import Schedule

    assert not Schedule.objects.filter(func=RECOVERY_FUNC).exists()


@pytest.mark.django_db(transaction=True)
def test_recovery_transaction_failure_never_claims_schedule(
    client: Client,
    nir_user: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R3: transação de recovery/auditoria falha → sem Schedule anunciado."""
    case = _make_marked_case(nir_user)
    _retain_with_divergence(case, [DETECTED_TYPE])
    _fail_dispatch(monkeypatch)
    real_create = CaseEvent.objects.create

    def _create_side_effect(**kwargs: Any) -> CaseEvent:
        if kwargs.get("event_type") == CaseEventType.CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED:
            raise DatabaseError("auditoria indisponivel (simulado)")
        return real_create(**kwargs)

    monkeypatch.setattr(CaseEvent.objects, "create", _create_side_effect)
    client.force_login(nir_user)

    with caplog.at_level("WARNING", logger="apps.pipeline.tasks"):
        response = client.post(
            reverse("intake:procedure_review_confirm", args=[case.case_id]),
            data=_confirm_post_data(case),
        )

    assert response.status_code == 503
    body = response.content.decode()
    assert "suporte" in body.lower()
    assert "Fulano" not in body

    # Confirmação persiste; nada de Schedule; nenhuma auditoria alegada.
    case.refresh_from_db()
    assert latest_confirmation_event(case) is not None
    from django_q.models import Schedule

    assert not Schedule.objects.filter(func=RECOVERY_FUNC).exists()
    assert not case.events.filter(
        event_type=CaseEventType.CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED
    ).exists()
    # Log técnico sanitizado: registra a falha sem PHI e sem alegar persistência.
    assert "Fulano" not in caplog.text
    assert "123.456" not in caplog.text


# ── R2: dono único, deduplicação e UUID serializado ────────────────────────


@pytest.mark.django_db(transaction=True)
def test_success_dispatch_has_single_owner_and_no_schedule(
    client: Client, nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: sucesso passa pelo callback único uma vez; sem recovery residual."""
    case = _make_marked_case(nir_user)
    _retain_with_divergence(case, [DETECTED_TYPE])
    calls: list[Any] = []
    from apps.pipeline import tasks as pipeline_tasks

    real_enqueue = pipeline_tasks.enqueue_case_pipeline

    def _counting(case_id: Any) -> None:
        calls.append(case_id)
        real_enqueue(case_id)

    monkeypatch.setattr("apps.pipeline.tasks.enqueue_case_pipeline", _counting)
    client.force_login(nir_user)
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact([DETECTED_TYPE])),
            json.dumps(_llm2_response([DECLARED_TYPE])),
        ]
    )
    with _chain(fake):
        response = client.post(
            reverse("intake:procedure_review_confirm", args=[case.case_id]),
            data=_confirm_post_data(case),
        )

    assert response.status_code == 302
    assert calls == [case.case_id]
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    from django_q.models import Schedule

    assert not Schedule.objects.filter(func=RECOVERY_FUNC).exists()


@pytest.mark.django_db(transaction=True)
def test_failed_validation_dispatches_nothing(
    client: Client, nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: POST inválido (400) nunca chega ao dispatch — sem callback."""
    case = _make_marked_case(nir_user)
    _retain_with_divergence(case, [DETECTED_TYPE])

    def _boom(case_id: Any) -> None:
        raise AssertionError("dispatch nao deveria disparar em POST invalido")

    monkeypatch.setattr("apps.pipeline.tasks.enqueue_case_pipeline", _boom)
    client.force_login(nir_user)
    data = _confirm_post_data(case)
    data["review_justification"] = "   "

    response = client.post(
        reverse("intake:procedure_review_confirm", args=[case.case_id]), data=data
    )

    assert response.status_code == 400
    case.refresh_from_db()
    assert latest_confirmation_event(case) is None


@pytest.mark.django_db(transaction=True)
def test_recovery_schedule_is_deduplicated(nir_user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """R2: recovery pendente por caso+confirmação não duplica Schedule."""
    from apps.pipeline.tasks import schedule_procedure_review_recovery

    case = _make_marked_case(nir_user)
    _retain_with_divergence(case, [DETECTED_TYPE])
    confirmation = latest_confirmation_event(case)
    assert confirmation is None  # ainda sem confirmação: usa id explícito
    confirmation_id = 424242
    assert (
        schedule_procedure_review_recovery(
            case_id=case.case_id, confirmation_event_id=confirmation_id
        )
        is True
    )
    assert (
        schedule_procedure_review_recovery(
            case_id=case.case_id, confirmation_event_id=confirmation_id
        )
        is True
    )
    from django_q.models import Schedule

    matches = list(Schedule.objects.filter(func=RECOVERY_FUNC, name__contains=str(confirmation_id)))
    assert len(matches) == 1
    assert str(case.case_id) in (matches[0].args or "")


# ── R4: recovery executa o pipeline real, sem PDF, sem duplicação ──────────


@pytest.mark.django_db(transaction=True)
def test_persisted_schedule_args_execute_real_recovery(
    client: Client, nir_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2/R4: destino persistido com UUID serializado roda o pipeline real."""
    case = _make_marked_case(nir_user)
    _retain_with_divergence(case, [DETECTED_TYPE])
    _fail_dispatch(monkeypatch)
    client.force_login(nir_user)
    response = client.post(
        reverse("intake:procedure_review_confirm", args=[case.case_id]),
        data=_confirm_post_data(case),
    )
    assert response.status_code == 302
    case.refresh_from_db()
    confirmation = latest_confirmation_event(case)
    assert confirmation is not None

    from django_q.models import Schedule

    sched = Schedule.objects.get(func=RECOVERY_FUNC)
    parsed_args = ast.literal_eval(sched.args or "")
    assert parsed_args[0] == str(case.case_id)
    assert int(parsed_args[1]) == confirmation.pk

    def _no_pdf(case_id: Any) -> None:
        raise AssertionError("worker de PDF nao participa do recovery")

    def _no_anonymization(case_id: Any) -> None:
        raise AssertionError("anonimizacao nao reexecuta no recovery")

    monkeypatch.setattr("apps.intake.tasks.enqueue_case_processing", _no_pdf)
    monkeypatch.setattr("apps.anonymization.tasks.enqueue_case_anonymization", _no_anonymization)
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact([DETECTED_TYPE])),
            json.dumps(_llm2_response([DECLARED_TYPE])),
        ]
    )
    with _chain(fake):
        process_case_pipeline(*parsed_args)

    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    applied = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED)
    assert applied.payload["effective_procedures"] == [DECLARED_TYPE]


@pytest.mark.django_db
def test_stale_confirmation_job_is_noop(nir_user: User) -> None:
    """R4: job com confirmação divergente da vigente não reescreve seleção."""
    case = _make_marked_case(nir_user)
    _retain_with_divergence(case, [DETECTED_TYPE])
    confirmation = _confirm_via_service(
        case,
        nir_user,
        ScriptedLlmClient(
            [
                json.dumps(_artifact([DETECTED_TYPE])),
                json.dumps(_llm2_response([DECLARED_TYPE])),
            ]
        ),
    )
    assert confirmation is not None
    assert case.status == CaseStatus.AWAITING_DOCTOR
    events_before = case.events.count()

    with _chain(ScriptedLlmClient([])):
        process_case_pipeline(case.case_id, 999999)

    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert case.events.count() == events_before
    assert list(get_declared_procedure_types(case)) == [DECLARED_TYPE]


@pytest.mark.django_db
def test_completed_case_recovery_is_noop(nir_user: User) -> None:
    """R4: recovery após conclusão não duplica efeitos nem eventos."""
    case = _make_marked_case(nir_user)
    _retain_with_divergence(case, [DETECTED_TYPE])
    confirmation = _confirm_via_service(
        case,
        nir_user,
        ScriptedLlmClient(
            [
                json.dumps(_artifact([DETECTED_TYPE])),
                json.dumps(_llm2_response([DECLARED_TYPE])),
            ]
        ),
    )
    assert confirmation is not None
    assert case.status == CaseStatus.AWAITING_DOCTOR
    events_before = case.events.count()

    with _chain(ScriptedLlmClient([])):
        process_case_pipeline(case.case_id, confirmation.pk)

    case.refresh_from_db()
    assert case.events.count() == events_before


@pytest.mark.django_db
def test_closed_case_recovery_is_noop(nir_user: User) -> None:
    """R4: caso encerrado aborta sem FAILED, sem eventos e sem ressuscitar."""
    case = _make_marked_case(nir_user)
    _retain_with_divergence(case, [DETECTED_TYPE])
    confirmation = _confirm_via_service(
        case,
        nir_user,
        ScriptedLlmClient(
            [
                json.dumps(_artifact([DETECTED_TYPE])),
                json.dumps(_llm2_response([DECLARED_TYPE])),
            ]
        ),
    )
    assert confirmation is not None
    Case.objects.filter(pk=case.pk).update(status=CaseStatus.CLEANED)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    events_before = case.events.count()

    with _chain(ScriptedLlmClient([])):
        process_case_pipeline(case.case_id, confirmation.pk)

    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert case.events.count() == events_before


@pytest.mark.django_db
def test_llm_error_stays_failed_without_loops(nir_user: User) -> None:
    """R4: erro de LLM no recovery fica FAILED; reexecução não re-dispara."""
    case = _make_marked_case(nir_user)
    _retain_with_divergence(case, [DETECTED_TYPE])
    data = _confirm_post_data(case)
    from apps.intake.services import confirm_case_procedure_review

    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact([DETECTED_TYPE])),
            LlmError("network", "falha simulada"),
        ]
    )
    with _chain(fake):
        confirm_case_procedure_review(
            case=case,
            user=nir_user,
            role=NIR_ROLE,
            procedure_type=str(data["procedure_type"]),
            review_acknowledged=data["review_acknowledged"],
            review_justification=str(data["review_justification"]),
            review_event_id=data["review_event_id"],
            source_fingerprint=str(data["source_fingerprint"]),
        )
        process_case_pipeline(case.case_id)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    calls_after_failure = len(fake.calls)
    assert calls_after_failure == 2
    events_after_failure = case.events.count()

    with _chain(fake):
        process_case_pipeline(case.case_id)

    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert case.events.count() == events_after_failure
    assert len(fake.calls) == calls_after_failure
