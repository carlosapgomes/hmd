"""Confirmação humana dirige o pipeline até o médico (slice 001, R4–R6).

Cobre a integração real serviço→worker com cliente LLM fake capturável (o
fake substitui só o transporte — serviço, orquestrador, signals e transações
são reais): nova LLM1 com perfil confirmado, ausência de novo gate pela mesma
divergência, ``APPLIED.automatic_procedures`` igual à detecção NOVA (ordem
canônica, inclusive vazia), detecção real preservada nas rows/artefato, política
prevalecendo, zero invenção/PHI (canário na justificativa nunca chega ao LLM),
integridade no worker → FAILED sem dispatch, revisão/declaração → gate
automático, CLEANED benigno e o guard de completude (R6). A cadeia pós-commit
real é provada com ``transaction=True``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from io import StringIO
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import override_settings

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
from apps.cases.procedures import set_declared_procedures
from apps.intake.services import confirm_case_procedure_review
from apps.pipeline.orchestrator import process_case_pipeline

FAKE_MODEL_LLM1 = "modelo-llm1-teste"
FAKE_MODEL_LLM2 = "modelo-llm2-teste"
SYSTEM_ROLE = "system"
NIR_ROLE = "nir"
_ANONYMIZED_TEXT = (
    "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente. "
    "Solicitada avaliacao hemodinamica."
)
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "JOSE MARIA DE SOUZA", "entity_type": "PERSON"},
    "<DATA_1>": {"value": "05/03/1972", "entity_type": "DATE_TIME"},
}
_CANARY = "CANARIO-UNICO-7391"


class ScriptedLlmClient:
    """Cliente fake com roteiro compartilhado; registra modelo+mensagens."""

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


def _artifact(procedure_types: list[str], *, platelets: float | None = None) -> dict[str, object]:
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
        "contexto_clinico": "Paciente <PESSOA_1> com claudicacao intermitente ha tres meses.",
        "linha_do_tempo": [],
        "exames": exames,
        "medicacoes": [],
        "comorbidades": [],
        "contraindicacoes": [],
        "trechos_nao_classificados": [],
    }


def _llm2_response(
    procedure_types: list[str], *, summary: str = "Sumario clinico em portugues."
) -> dict[str, object]:
    return {
        "summary_text": summary,
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
def _seeded_prompts() -> None:
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture
def nir_user() -> User:
    role, _ = Role.objects.get_or_create(name=NIR_ROLE)
    user = User.objects.create_user(username="nir-pipeline", password="senha-teste")
    user.roles.add(role)
    return user


def _attach_document(case: Case) -> None:
    content = b"%PDF-1.4 relatorio sintetico"
    document = CaseDocument(
        case=case,
        position=1,
        original_filename="relatorio.pdf",
        content_type="application/pdf",
        size_bytes=len(content),
        uploaded_by=case.created_by,
    )
    document.file.save("relatorio.pdf", ContentFile(content), save=False)
    document.save()


def _make_marked_case(owner: User, *, declared: tuple[str, ...] = ("art_perif",)) -> Case:
    """Caso marcado em LLM_EXTRACTING (fonte+anonimização reais, sem retenção)."""
    case = Case.objects.create(created_by=owner)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.LLM_EXTRACTING
    _attach_document(case)
    case.extracted_text = "Texto extraido do relatorio sintetico."
    case.anonymized_text = _ANONYMIZED_TEXT
    case.pseudonym_map = dict(_PSEUDONYMS)
    case.save()
    for procedure_type in declared:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={"version": 1},
    )
    return case


def _run_pipeline(case: Case, fake: ScriptedLlmClient) -> None:
    with override_settings(
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    ):
        process_case_pipeline(case.case_id)


def _retain_with_divergence(
    case: Case, detected: list[str], *, platelets: float | None = None
) -> None:
    fake = ScriptedLlmClient([json.dumps(_artifact(detected, platelets=platelets))])
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == "procedure_divergence"
    assert len(fake.calls) == 1


def _confirm(
    case: Case,
    *,
    procedure_type: str,
    justification: str = f"Confirmo a selecao. {_CANARY}",
) -> CaseEvent:
    review = CaseEvent.objects.get(
        case=case, event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE
    )
    # Disparo pós-commit mockado: os testes chamam o worker manualmente com o
    # fake (exceto o teste transaction=True, que usa a cadeia real).
    return _confirm_with_recorder(case, procedure_type, justification, review)


def _confirm_with_recorder(
    case: Case, procedure_type: str, justification: str, review: CaseEvent
) -> CaseEvent:
    """Confirma via serviço real sem disparar o worker (chamada manual seguinte).

    O disparo pós-commit real é provado no teste ``transaction=True``; aqui o
    ``on_commit`` fica pendente (rollback de teste) e o worker roda pela
    chamada explícita ``_run_pipeline`` — nenhum efeito é simulado.
    """
    from apps.cases.procedure_review import source_fingerprint_for_case

    result = confirm_case_procedure_review(
        case=case,
        user=case.created_by,
        role=NIR_ROLE,
        procedure_type=procedure_type,
        review_acknowledged="on",
        review_justification=justification,
        review_event_id=review.pk,
        source_fingerprint=source_fingerprint_for_case(case),
    )
    result.refresh_from_db()
    return CaseEvent.objects.get(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED
    )


def _event_types(case: Case) -> list[str]:
    return list(case.events.order_by("id").values_list("event_type", flat=True))


def _applied(case: Case) -> CaseEvent:
    return CaseEvent.objects.get(case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED)


def _assert_no_canary_in_calls(fake: ScriptedLlmClient, canary: str = _CANARY) -> None:
    for _model, messages, _schema in fake.calls:
        for message in messages:
            assert canary not in message["content"]


# ── R4/R5: nova extração com confirmado ────────────────────────────────────


@pytest.mark.django_db
def test_keep_repeated_divergence_reaches_doctor_no_new_gate(nir_user: User) -> None:
    case = _make_marked_case(nir_user, declared=("art_perif",))
    _retain_with_divergence(case, ["nefrostomia"])
    review_count = CaseEvent.objects.filter(
        case=case, event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE
    ).count()
    confirmation = _confirm(case, procedure_type="art_perif")

    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(["nefrostomia"])),
            json.dumps(_llm2_response(["art_perif"])),
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()

    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert case.summary_text.strip() != ""
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE
        ).count()
        == review_count
    )
    applied = _applied(case)
    assert applied.payload["confirmation_event_id"] == confirmation.pk
    assert applied.payload["automatic_procedures"] == ["nefrostomia"]
    assert applied.payload["effective_procedures"] == ["art_perif"]
    # Detecção real preservada nas rows (não sobrescrita por "detected").
    rows = {row.procedure_type: row.detection_status for row in case.procedures.all()}
    assert rows["nefrostomia"] == "detected"
    assert rows["art_perif"] == "not_detected"
    # Análise exata do confirmado.
    assert set(case.policy_result) == {"art_perif"}
    procedures = case.suggested_action["procedures"]
    assert isinstance(procedures, dict)
    assert set(procedures) == {"art_perif"}
    assert [model for model, _m, _s in fake.calls] == [FAKE_MODEL_LLM1, FAKE_MODEL_LLM2]
    # LLM1_DIGESTS capturados na revisão (fonte/texto/mapa, nunca valores).
    llm1_event = (
        CaseEvent.objects.filter(case=case, event_type=CaseEventType.CASE_LLM1_COMPLETED)
        .order_by("id")
        .last()
    )
    assert llm1_event is not None
    assert llm1_event.payload.get("source_fingerprint", "").startswith("v1:")
    assert "anonymized_text_digest" in llm1_event.payload
    assert "pseudonym_map_digest" in llm1_event.payload
    assert "JOSE MARIA" not in json.dumps(llm1_event.payload)


@pytest.mark.django_db
def test_change_to_absent_with_empty_detection(nir_user: User) -> None:
    case = _make_marked_case(nir_user, declared=("art_perif",))
    _retain_with_divergence(case, ["nefrostomia"])
    confirmation = _confirm(case, procedure_type="cat_cardiaco")

    # Nova passada sem nenhum detectado: APPLIED transporta [] exato.
    fake = ScriptedLlmClient(
        [json.dumps(_artifact([])), json.dumps(_llm2_response(["cat_cardiaco"]))]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()

    assert case.status == CaseStatus.AWAITING_DOCTOR
    applied = _applied(case)
    assert applied.payload["confirmation_event_id"] == confirmation.pk
    assert applied.payload["automatic_procedures"] == []
    assert applied.payload["effective_procedures"] == ["cat_cardiaco"]
    # Nenhum item fabricado no artefato automático.
    structured = case.structured_data
    assert isinstance(structured, dict)
    pedido = structured["pedido"]
    assert isinstance(pedido, dict)
    assert pedido["procedimentos_solicitados"] == []
    assert set(case.policy_result) == {"cat_cardiaco"}
    procedures = case.suggested_action["procedures"]
    assert isinstance(procedures, dict)
    assert set(procedures) == {"cat_cardiaco"}


@pytest.mark.django_db
def test_policy_refusal_still_precedes_llm_accept(nir_user: User) -> None:
    case = _make_marked_case(nir_user, declared=("art_perif",))
    _retain_with_divergence(case, ["nefrostomia"], platelets=80000.0)
    _confirm(case, procedure_type="art_perif")

    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(["art_perif"], platelets=80000.0)),
            json.dumps(_llm2_response(["art_perif"])),
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    policy = case.policy_result["art_perif"]
    assert isinstance(policy, dict)
    assert policy["recommendation"] == "recomenda_recusar"
    procedures = case.suggested_action["procedures"]
    assert isinstance(procedures, dict)
    entry = procedures["art_perif"]
    assert isinstance(entry, dict)
    assert entry["suggestion"] == "recusar"


@pytest.mark.django_db
def test_justification_canary_never_reaches_llm(nir_user: User) -> None:
    case = _make_marked_case(nir_user, declared=("art_perif",))
    _retain_with_divergence(case, ["nefrostomia"])
    _confirm(case, procedure_type="art_perif", justification=f"Autor canario {_CANARY} aqui.")

    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(["nefrostomia"])),
            json.dumps(_llm2_response(["art_perif"])),
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    _assert_no_canary_in_calls(fake)
    # Contexto técnico da LLM2 nomeia o código confirmado, sem a justificativa.
    llm2_messages = fake.calls[1][1]
    user_content = "\n".join(m["content"] for m in llm2_messages if m["role"] == "user")
    assert "art_perif" in user_content
    assert _CANARY not in user_content
    assert "nir-pipeline" not in user_content


# ── R3: integridade no worker → FAILED sem dispatch ────────────────────────


def _confirmed_case_ready(nir_user: User) -> Case:
    case = _make_marked_case(nir_user, declared=("art_perif",))
    _retain_with_divergence(case, ["nefrostomia"])
    _confirm(case, procedure_type="art_perif")
    case.refresh_from_db()
    return case


@pytest.mark.django_db
def test_source_changed_fails_without_dispatch(nir_user: User) -> None:
    case = _confirmed_case_ready(nir_user)
    case.extracted_text = "Fonte alterada apos a confirmacao."
    case.save(update_fields=["extracted_text"])
    fake = ScriptedLlmClient([json.dumps(_artifact(["art_perif"]))])
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert fake.calls == []
    assert case.structured_data == {}
    invalidated = CaseEvent.objects.get(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
    )
    assert invalidated.payload["reason"] == "source_changed"
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value not in _event_types(case)


@pytest.mark.django_db
def test_anonymized_changed_and_missing_fail(nir_user: User) -> None:
    changed = _confirmed_case_ready(nir_user)
    changed.anonymized_text = "Texto anonimizado diferente."
    changed.save(update_fields=["anonymized_text"])
    fake_changed = ScriptedLlmClient([json.dumps(_artifact(["art_perif"]))])
    _run_pipeline(changed, fake_changed)
    changed.refresh_from_db()
    assert changed.status == CaseStatus.FAILED
    assert fake_changed.calls == []
    assert (
        CaseEvent.objects.get(
            case=changed, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).payload["reason"]
        == "anonymized_mismatch"
    )

    missing = _confirmed_case_ready(nir_user)
    missing.anonymized_text = ""
    missing.save(update_fields=["anonymized_text"])
    fake_missing = ScriptedLlmClient([json.dumps(_artifact(["art_perif"]))])
    _run_pipeline(missing, fake_missing)
    missing.refresh_from_db()
    assert missing.status == CaseStatus.FAILED
    assert fake_missing.calls == []
    assert (
        CaseEvent.objects.get(
            case=missing, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).payload["reason"]
        == "anonymization_missing"
    )


@pytest.mark.django_db
def test_map_changed_and_source_unavailable_fail(nir_user: User) -> None:
    mapped = _confirmed_case_ready(nir_user)
    mapped.pseudonym_map = {"<PESSOA_1>": {"value": "OUTRA PESSOA", "entity_type": "PERSON"}}
    mapped.save(update_fields=["pseudonym_map"])
    fake_mapped = ScriptedLlmClient([json.dumps(_artifact(["art_perif"]))])
    _run_pipeline(mapped, fake_mapped)
    mapped.refresh_from_db()
    assert mapped.status == CaseStatus.FAILED
    assert fake_mapped.calls == []
    assert (
        CaseEvent.objects.get(
            case=mapped, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).payload["reason"]
        == "pseudonym_map_mismatch"
    )

    unavailable = _confirmed_case_ready(nir_user)
    unavailable.documents.all().delete()
    fake_unavailable = ScriptedLlmClient([json.dumps(_artifact(["art_perif"]))])
    _run_pipeline(unavailable, fake_unavailable)
    unavailable.refresh_from_db()
    assert unavailable.status == CaseStatus.FAILED
    assert fake_unavailable.calls == []
    assert (
        CaseEvent.objects.get(
            case=unavailable, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).payload["reason"]
        == "source_unavailable"
    )


@pytest.mark.django_db
def test_integrity_failure_precedes_review_change(nir_user: User) -> None:
    case = _confirmed_case_ready(nir_user)
    set_declared_procedures(case, ("cat_cardiaco",), user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    case.extracted_text = "Fonte alterada."
    case.save(update_fields=["extracted_text"])
    fake = ScriptedLlmClient([json.dumps(_artifact(["cat_cardiaco"]))])
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert fake.calls == []
    assert (
        CaseEvent.objects.get(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).payload["reason"]
        == "source_changed"
    )


@pytest.mark.django_db
def test_change_during_llm_call_is_not_persisted(nir_user: User) -> None:
    case = _confirmed_case_ready(nir_user)

    class MutatingClient(ScriptedLlmClient):
        def complete(
            self,
            model: str,
            messages: Sequence[dict[str, str]],
            *,
            json_schema: dict[str, Any] | None = None,
        ) -> str:
            Case.objects.filter(pk=case.pk).update(
                extracted_text="Fonte trocada durante a chamada."
            )
            return super().complete(model, messages, json_schema=json_schema)

    fake = MutatingClient(
        [
            json.dumps(_artifact(["art_perif"])),
            json.dumps(_llm2_response(["art_perif"])),
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert case.structured_data == {}
    assert len(fake.calls) == 1
    assert (
        CaseEvent.objects.get(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).payload["reason"]
        == "source_changed"
    )
    assert CaseEventType.CASE_LLM2_COMPLETED.value not in _event_types(case)


# ── R3: revisão/declaração → gate automático ───────────────────────────────


@pytest.mark.django_db
def test_review_superseded_falls_back_to_automatic_gate(nir_user: User) -> None:
    case = _confirmed_case_ready(nir_user)
    # Nova revisão durante o voo (integridade preservada) → gate automático.
    from apps.cases.procedure_review import (
        anonymized_text_digest_for_case,
        pseudonym_map_digest_for_case,
        source_fingerprint_for_case,
    )

    CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={
            "classification": {"art_perif": "not_detected"},
            "source_fingerprint": source_fingerprint_for_case(case),
            "anonymized_text_digest": anonymized_text_digest_for_case(case),
            "pseudonym_map_digest": pseudonym_map_digest_for_case(case),
        },
    )
    fake = ScriptedLlmClient([json.dumps(_artifact(["nefrostomia"]))])
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert (
        CaseEvent.objects.get(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).payload["reason"]
        == "review_superseded"
    )
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED
        ).count()
        == 0
    )
    assert len(fake.calls) == 1


@pytest.mark.django_db
def test_declaration_changed_without_divergence_proceeds_automatically(
    nir_user: User,
) -> None:
    case = _confirmed_case_ready(nir_user)
    set_declared_procedures(case, ("cat_cardiaco",), user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(["cat_cardiaco"])),
            json.dumps(_llm2_response(["cat_cardiaco"])),
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert (
        CaseEvent.objects.get(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).payload["reason"]
        == "declaration_changed"
    )
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_invalidated_confirmation_never_revives(nir_user: User) -> None:
    case = _confirmed_case_ready(nir_user)
    set_declared_procedures(case, ("cat_cardiaco",), user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    first = ScriptedLlmClient([json.dumps(_artifact(["art_perif"]))])
    _run_pipeline(case, first)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).count()
        == 1
    )
    # Restaura a declaração original: a confirmação NÃO revive.
    set_declared_procedures(case, ("art_perif",), user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    second = ScriptedLlmClient([json.dumps(_artifact(["nefrostomia"]))])
    _run_pipeline(case, second)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).count()
        == 1
    )
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED
        ).count()
        == 0
    )


# ── R6: completude e CLEANED ───────────────────────────────────────────────


@pytest.mark.django_db
def test_whitespace_summary_does_not_enqueue(nir_user: User) -> None:
    case = _confirmed_case_ready(nir_user)
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(["nefrostomia"])),
            json.dumps(_llm2_response(["art_perif"], summary="   \n\t ")),
            json.dumps(_llm2_response(["art_perif"], summary="   \n\t ")),
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value not in _event_types(case)


@pytest.mark.django_db
def test_incomplete_analysis_does_not_enqueue(nir_user: User) -> None:
    case = _confirmed_case_ready(nir_user)
    wrong_set = {
        "summary_text": "Sumario clinico em portugues.",
        "procedures": [
            {
                "procedure_type": "filtro_cava",
                "suggestion": "aceitar",
                "motivos": ["sem contraindicoes identificadas"],
                "evidence_spans": [],
            }
        ],
    }
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(["nefrostomia"])),
            json.dumps(wrong_set),
            json.dumps(wrong_set),
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value not in _event_types(case)


@pytest.mark.django_db
def test_cleaned_aborts_without_spurious_invalidation_or_failure(
    nir_user: User,
) -> None:
    case = _confirmed_case_ready(nir_user)
    Case.objects.filter(pk=case.pk).update(
        status=CaseStatus.CLEANED,
        anonymized_text="",
        pseudonym_map={},
        extracted_text="",
        structured_data={},
    )
    events_before = CaseEvent.objects.filter(case=case).count()
    fake = ScriptedLlmClient([json.dumps(_artifact(["art_perif"]))])
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert fake.calls == []
    assert CaseEvent.objects.filter(case=case).count() == events_before
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).count()
        == 0
    )
    assert CaseEventType.CASE_STATUS_FAILED.value not in _event_types(case)


@pytest.mark.django_db
def test_cleaned_during_llm_call_aborts_without_spurious_events(
    nir_user: User,
) -> None:
    case = _confirmed_case_ready(nir_user)

    class CleaningClient(ScriptedLlmClient):
        def complete(
            self,
            model: str,
            messages: Sequence[dict[str, str]],
            *,
            json_schema: dict[str, Any] | None = None,
        ) -> str:
            Case.objects.filter(pk=case.pk).update(
                status=CaseStatus.CLEANED,
                anonymized_text="",
                pseudonym_map={},
            )
            return super().complete(model, messages, json_schema=json_schema)

    fake = CleaningClient([json.dumps(_artifact(["art_perif"]))])
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED
    assert case.structured_data == {}
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).count()
        == 0
    )
    assert CaseEventType.CASE_STATUS_FAILED.value not in _event_types(case)


# ── fix-1 P1-3: LLM1 inicial marcada captura snapshot pré-dispatch ─────────


@pytest.mark.parametrize(
    "mutation",
    ["source", "anonymized", "map"],
)
@pytest.mark.django_db
def test_initial_llm1_inflight_integrity_change_not_persisted(
    nir_user: User, mutation: str
) -> None:
    case = _make_marked_case(nir_user, declared=("art_perif",))

    class MutatingClient(ScriptedLlmClient):
        def complete(
            self,
            model: str,
            messages: Sequence[dict[str, str]],
            *,
            json_schema: dict[str, Any] | None = None,
        ) -> str:
            if mutation == "source":
                Case.objects.filter(pk=case.pk).update(
                    extracted_text="Fonte trocada durante a LLM1 inicial."
                )
            elif mutation == "anonymized":
                Case.objects.filter(pk=case.pk).update(
                    anonymized_text="Texto anonimizado trocado durante a LLM1."
                )
            else:
                Case.objects.filter(pk=case.pk).update(
                    pseudonym_map={"<PESSOA_1>": {"value": "OUTRA", "entity_type": "PERSON"}}
                )
            return super().complete(model, messages, json_schema=json_schema)

    fake = MutatingClient([json.dumps(_artifact(["nefrostomia"]))])
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert case.structured_data == {}
    assert CaseEventType.CASE_LLM1_COMPLETED.value not in _event_types(case)
    assert CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE.value not in _event_types(case)
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value not in _event_types(case)
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "mutation",
    ["anonymized", "map"],
)
@pytest.mark.django_db
def test_confirmed_llm1_inflight_text_or_map_change_not_persisted(
    nir_user: User, mutation: str
) -> None:
    case = _confirmed_case_ready(nir_user)

    class MutatingClient(ScriptedLlmClient):
        def complete(
            self,
            model: str,
            messages: Sequence[dict[str, str]],
            *,
            json_schema: dict[str, Any] | None = None,
        ) -> str:
            if mutation == "anonymized":
                Case.objects.filter(pk=case.pk).update(
                    anonymized_text="Texto anonimizado trocado em voo."
                )
            else:
                Case.objects.filter(pk=case.pk).update(
                    pseudonym_map={"<PESSOA_1>": {"value": "OUTRA", "entity_type": "PERSON"}}
                )
            return super().complete(model, messages, json_schema=json_schema)

    llm1_before = CaseEvent.objects.filter(
        case=case, event_type=CaseEventType.CASE_LLM1_COMPLETED
    ).count()
    fake = MutatingClient([json.dumps(_artifact(["art_perif"]))])
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert case.structured_data == {}
    assert (
        CaseEvent.objects.filter(case=case, event_type=CaseEventType.CASE_LLM1_COMPLETED).count()
        == llm1_before
    )
    assert len(fake.calls) == 1


# ── fix-1 P1-2: LLM2 não persiste resultado em voo com integridade alterada ─


@pytest.mark.parametrize(
    "mutation",
    ["source", "anonymized", "map"],
)
@pytest.mark.django_db
def test_llm2_inflight_integrity_change_not_persisted(nir_user: User, mutation: str) -> None:
    case = _confirmed_case_ready(nir_user)
    call_count = {"n": 0}

    class MutatingLlm2Client(ScriptedLlmClient):
        def complete(
            self,
            model: str,
            messages: Sequence[dict[str, str]],
            *,
            json_schema: dict[str, Any] | None = None,
        ) -> str:
            call_count["n"] += 1
            if call_count["n"] == 2:  # chamada LLM2
                if mutation == "source":
                    Case.objects.filter(pk=case.pk).update(
                        extracted_text="Fonte trocada durante a LLM2."
                    )
                elif mutation == "anonymized":
                    Case.objects.filter(pk=case.pk).update(
                        anonymized_text="Texto anonimizado trocado durante a LLM2."
                    )
                else:
                    Case.objects.filter(pk=case.pk).update(
                        pseudonym_map={"<PESSOA_1>": {"value": "OUTRA", "entity_type": "PERSON"}}
                    )
            return super().complete(model, messages, json_schema=json_schema)

    fake = MutatingLlm2Client(
        [
            json.dumps(_artifact(["nefrostomia"])),
            json.dumps(_llm2_response(["art_perif"])),
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert case.summary_text == ""
    assert case.suggested_action == {}
    assert CaseEventType.CASE_LLM2_COMPLETED.value not in _event_types(case)
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value not in _event_types(case)
    assert len(fake.calls) == 2


# ── fix-1 P1-6 / HD-001: autoridade estagnada durante LLM2 reconcilia ─────


def _supersede_review_during_llm2(case: Case, client_cls: type[ScriptedLlmClient]) -> None:
    del case, client_cls  # helper documental; a mutação vive nos clientes abaixo.


@pytest.mark.django_db
def test_stale_authority_during_llm2_divergent_reentry_retains(nir_user: User) -> None:
    from apps.cases.procedure_review import (
        anonymized_text_digest_for_case,
        pseudonym_map_digest_for_case,
        source_fingerprint_for_case,
    )

    case = _confirmed_case_ready(nir_user)
    call_count = {"n": 0}

    class SupersedingClient(ScriptedLlmClient):
        def complete(
            self,
            model: str,
            messages: Sequence[dict[str, str]],
            *,
            json_schema: dict[str, Any] | None = None,
        ) -> str:
            call_count["n"] += 1
            if call_count["n"] == 2:  # LLM2 em voo: revisão é substituída.
                CaseEvent.objects.create(
                    case_id=case.case_id,
                    event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE,
                    actor_type=ActorType.SYSTEM,
                    actor=None,
                    actor_role="system",
                    payload={
                        "classification": {"art_perif": "not_detected"},
                        "source_fingerprint": source_fingerprint_for_case(case),
                        "anonymized_text_digest": anonymized_text_digest_for_case(case),
                        "pseudonym_map_digest": pseudonym_map_digest_for_case(case),
                    },
                )
            return super().complete(model, messages, json_schema=json_schema)

    fake = SupersedingClient(
        [
            json.dumps(_artifact(["nefrostomia"])),  # LLM1 confirmada
            json.dumps(_llm2_response(["art_perif"])),  # LLM2 estagnada (descartada)
            json.dumps(_artifact(["nefrostomia"])),  # reconciliação automática divergente
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()
    # Retorna a LLM_EXTRACTING e retém pela divergência automática atual.
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == "procedure_divergence"
    assert case.summary_text == ""
    assert CaseEventType.CASE_LLM2_COMPLETED.value not in _event_types(case)
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value not in _event_types(case)
    assert CaseEventType.CASE_STATUS_FAILED.value not in _event_types(case)
    # Retorno nomeado registrado.
    assert (
        CaseEvent.objects.filter(
            case=case,
            event_type=CaseEventType.CASE_STATUS_LLM_EXTRACTING,
            payload__target="LLM_EXTRACTING",
        ).count()
        >= 1
    )
    invalidated = CaseEvent.objects.filter(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
    )
    assert invalidated.count() == 1
    assert invalidated.get().payload["reason"] == "review_superseded"
    assert len(fake.calls) == 3
    # Reexecução equivalente não duplica invalidação nem avança.
    fake2 = ScriptedLlmClient([json.dumps(_artifact(["nefrostomia"]))])
    _run_pipeline(case, fake2)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_stale_authority_during_llm2_matching_reentry_reaches_doctor(
    nir_user: User,
) -> None:
    from apps.cases.procedure_review import (
        anonymized_text_digest_for_case,
        pseudonym_map_digest_for_case,
        source_fingerprint_for_case,
    )

    case = _confirmed_case_ready(nir_user)
    call_count = {"n": 0}

    class SupersedingClient(ScriptedLlmClient):
        def complete(
            self,
            model: str,
            messages: Sequence[dict[str, str]],
            *,
            json_schema: dict[str, Any] | None = None,
        ) -> str:
            call_count["n"] += 1
            if call_count["n"] == 2:
                CaseEvent.objects.create(
                    case_id=case.case_id,
                    event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE,
                    actor_type=ActorType.SYSTEM,
                    actor=None,
                    actor_role="system",
                    payload={
                        "classification": {"art_perif": "not_detected"},
                        "source_fingerprint": source_fingerprint_for_case(case),
                        "anonymized_text_digest": anonymized_text_digest_for_case(case),
                        "pseudonym_map_digest": pseudonym_map_digest_for_case(case),
                    },
                )
            return super().complete(model, messages, json_schema=json_schema)

    fake = SupersedingClient(
        [
            json.dumps(_artifact(["nefrostomia"])),
            json.dumps(_llm2_response(["art_perif"])),  # descartada
            json.dumps(_artifact(["art_perif"])),  # reconciliação que casa
            json.dumps(_llm2_response(["art_perif"], summary="Resumo novo apos reconciliacao.")),
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert case.summary_text == "Resumo novo apos reconciliacao."
    assert (
        CaseEvent.objects.filter(case=case, event_type=CaseEventType.CASE_LLM2_COMPLETED).count()
        == 1
    )
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
        ).count()
        == 1
    )
    assert len(fake.calls) == 4
    # Reexecução em AWAITING_DOCTOR é no-op (sem loop de reconciliação).
    idle = ScriptedLlmClient([])
    _run_pipeline(case, idle)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert idle.calls == []


# ── R4: cadeia pós-commit real (transaction=True) ──────────────────────────


@pytest.mark.django_db(transaction=True)
def test_real_post_commit_chain_reaches_doctor(nir_user: User) -> None:
    """Confirmação comita e o signal real enfileira o worker inline até o médico."""
    from apps.cases.procedure_review import source_fingerprint_for_case

    case = _make_marked_case(nir_user, declared=("art_perif",))
    case_id = case.case_id
    # Primeira passada real (divergência) via orquestrador com fake capturável.
    first = ScriptedLlmClient([json.dumps(_artifact(["nefrostomia"]))])
    with override_settings(
        LLM_CLIENT_FACTORY=lambda: first,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    ):
        process_case_pipeline(case.case_id)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    review = CaseEvent.objects.get(
        case=case, event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE
    )
    source_before = case.extracted_text
    anonymized_before = case.anonymized_text
    map_before = dict(case.pseudonym_map)
    extraction_events_before = CaseEvent.objects.filter(
        case_id=case_id, event_type=CaseEventType.CASE_EXTRACTION_COMPLETED
    ).count()
    anonymization_events_before = CaseEvent.objects.filter(
        case_id=case_id, event_type=CaseEventType.CASE_ANONYMIZATION_COMPLETED
    ).count()

    second = ScriptedLlmClient(
        [
            json.dumps(_artifact(["nefrostomia"])),
            json.dumps(_llm2_response(["art_perif"])),
        ]
    )
    with override_settings(
        LLM_CLIENT_FACTORY=lambda: second,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
        LLM_RUN_TASKS_INLINE=True,
    ):
        result = confirm_case_procedure_review(
            case=case,
            user=nir_user,
            role=NIR_ROLE,
            procedure_type="art_perif",
            review_acknowledged=True,
            review_justification=f"Confirmo apos leitura. {_CANARY}",
            review_event_id=review.pk,
            source_fingerprint=source_fingerprint_for_case(case),
        )
    result.refresh_from_db()

    assert result.case_id == case_id
    assert result.status == CaseStatus.AWAITING_DOCTOR
    assert result.summary_text.strip() != ""
    assert result.extracted_text == source_before
    assert result.anonymized_text == anonymized_before
    assert result.pseudonym_map == map_before
    # Nova LLM1 + LLM2 sem PDF/anonymization novos.
    assert [model for model, _m, _s in second.calls] == [FAKE_MODEL_LLM1, FAKE_MODEL_LLM2]
    assert (
        CaseEvent.objects.filter(
            case_id=case_id, event_type=CaseEventType.CASE_EXTRACTION_COMPLETED
        ).count()
        == extraction_events_before
    )
    assert (
        CaseEvent.objects.filter(
            case_id=case_id, event_type=CaseEventType.CASE_ANONYMIZATION_COMPLETED
        ).count()
        == anonymization_events_before
    )
    applied = _applied(result)
    assert applied.payload["automatic_procedures"] == ["nefrostomia"]
    assert applied.payload["effective_procedures"] == ["art_perif"]
    _assert_no_canary_in_calls(second)
    # Reexecução é no-op (sem duplicar APPLIED).
    with override_settings(
        LLM_CLIENT_FACTORY=lambda: second,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    ):
        process_case_pipeline(case.case_id)
    assert (
        CaseEvent.objects.filter(
            case_id=case_id, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED
        ).count()
        == 1
    )
    result.refresh_from_db()
    assert result.status == CaseStatus.AWAITING_DOCTOR


# ── fix-2 R2-P1-4: LLM1 inicial marcada sem entrada utilizável ──────────────


@pytest.mark.parametrize("condition", ["source_unavailable", "empty_anonymized"])
@pytest.mark.django_db
def test_initial_marked_without_usable_inputs_fails_zero_calls(
    nir_user: User, condition: str
) -> None:
    case = _make_marked_case(nir_user, declared=("art_perif",))
    if condition == "source_unavailable":
        case.documents.all().delete()
    else:
        case.anonymized_text = ""
        case.save(update_fields=["anonymized_text"])
    fake = ScriptedLlmClient([json.dumps(_artifact(["art_perif"]))])
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert fake.calls == []
    assert case.structured_data == {}
    assert CaseEventType.CASE_LLM1_COMPLETED.value not in _event_types(case)
    assert CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE.value not in _event_types(case)
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value not in _event_types(case)


# ── fix-2 R2-P1-1: declaração alterada durante a LLM1 ──────────────────────


def _platelets_of(case: Case) -> object:
    structured = case.structured_data
    assert isinstance(structured, dict)
    exames = structured["exames"]
    assert isinstance(exames, dict)
    platelets = exames["platelets"]
    assert isinstance(platelets, dict)
    return platelets["value"]


def _artifact_filtro(
    procedure_types: list[str], *, platelets: float | None = None
) -> dict[str, object]:
    """Artefato válido sob o schema de ``filtro_cava`` (bloco obrigatório)."""
    artifact = _artifact(procedure_types, platelets=platelets)
    artifact["filtro_cava"] = {
        "tep": {"status": "nao_informado", "evidence_spans": []},
        "anticoagulation_contraindication": {
            "status": "nao_informado",
            "evidence_spans": [],
        },
    }
    return artifact


def _change_declaration_on_first_call(case: Case, new_type: str) -> type[ScriptedLlmClient]:
    from apps.cases.procedures import set_declared_procedures as _set_declared

    class ChangingClient(ScriptedLlmClient):
        def __init__(self, responses: list[str]) -> None:
            super().__init__(responses)
            self._changed = False

        def complete(
            self,
            model: str,
            messages: Sequence[dict[str, str]],
            *,
            json_schema: dict[str, Any] | None = None,
        ) -> str:
            if not self._changed:
                self._changed = True
                _set_declared(case, (new_type,), user=None, role=SYSTEM_ROLE)
                case.refresh_from_db()
            return super().complete(model, messages, json_schema=json_schema)

    return ChangingClient


@pytest.mark.django_db
def test_initial_marked_declaration_change_during_llm1_reextracts_matching(
    nir_user: User,
) -> None:
    case = _make_marked_case(nir_user, declared=("art_perif",))
    client_cls = _change_declaration_on_first_call(case, "filtro_cava")
    fake = client_cls(
        [
            json.dumps(_artifact(["art_perif"], platelets=210000.0)),
            json.dumps(_artifact_filtro(["filtro_cava"], platelets=220000.0)),
            json.dumps(_llm2_response(["filtro_cava"])),
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert [model for model, _m, _s in fake.calls] == [
        FAKE_MODEL_LLM1,
        FAKE_MODEL_LLM1,
        FAKE_MODEL_LLM2,
    ]
    # A execução obsoleta (art_perif/210000) nunca foi persistida.
    assert _platelets_of(case) == 220000.0
    assert (
        CaseEvent.objects.filter(case=case, event_type=CaseEventType.CASE_LLM1_COMPLETED).count()
        == 1
    )
    # A segunda chamada usou o schema do tipo vigente (filtro_cava tem bloco).
    second_schema = json.dumps(fake.calls[1][2], ensure_ascii=False)
    assert '"filtro_cava": {' in second_schema
    first_schema = json.dumps(fake.calls[0][2], ensure_ascii=False)
    assert '"filtro_cava": {' not in first_schema
    assert set(case.policy_result) == {"filtro_cava"}
    procedures = case.suggested_action["procedures"]
    assert isinstance(procedures, dict)
    assert set(procedures) == {"filtro_cava"}
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_initial_marked_declaration_change_during_llm1_reextracts_divergent(
    nir_user: User,
) -> None:
    case = _make_marked_case(nir_user, declared=("art_perif",))
    client_cls = _change_declaration_on_first_call(case, "filtro_cava")
    fake = client_cls(
        [
            json.dumps(_artifact(["art_perif"], platelets=210000.0)),
            json.dumps(_artifact_filtro(["nefrostomia"], platelets=220000.0)),
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert [model for model, _m, _s in fake.calls] == [FAKE_MODEL_LLM1, FAKE_MODEL_LLM1]
    assert _platelets_of(case) == 220000.0
    assert (
        CaseEvent.objects.filter(case=case, event_type=CaseEventType.CASE_LLM1_COMPLETED).count()
        == 1
    )
    review = CaseEvent.objects.get(
        case=case, event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE
    )
    classification = review.payload["classification"]
    assert isinstance(classification, dict)
    assert classification.get("filtro_cava") == "not_detected"
    assert classification.get("nefrostomia") == "missing_declaration"
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value not in _event_types(case)


@pytest.mark.django_db
def test_confirmed_declaration_change_during_llm1_reextracts_automatically(
    nir_user: User,
) -> None:
    case = _confirmed_case_ready(nir_user)
    llm1_before = CaseEvent.objects.filter(
        case=case, event_type=CaseEventType.CASE_LLM1_COMPLETED
    ).count()
    client_cls = _change_declaration_on_first_call(case, "filtro_cava")
    fake = client_cls(
        [
            json.dumps(_artifact(["nefrostomia"], platelets=210000.0)),
            json.dumps(_artifact_filtro(["filtro_cava"], platelets=220000.0)),
            json.dumps(_llm2_response(["filtro_cava"])),
        ]
    )
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert [model for model, _m, _s in fake.calls] == [
        FAKE_MODEL_LLM1,
        FAKE_MODEL_LLM1,
        FAKE_MODEL_LLM2,
    ]
    assert _platelets_of(case) == 220000.0
    assert (
        CaseEvent.objects.filter(case=case, event_type=CaseEventType.CASE_LLM1_COMPLETED).count()
        == llm1_before + 1
    )
    invalidated = CaseEvent.objects.get(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
    )
    assert invalidated.payload["reason"] == "declaration_changed"
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED
        ).count()
        == 0
    )
    assert set(case.policy_result) == {"filtro_cava"}
    second_schema = json.dumps(fake.calls[1][2], ensure_ascii=False)
    assert '"filtro_cava": {' in second_schema


# ── fix-2 R2-P1-3: revisão equivalente não duplica ─────────────────────────


@pytest.mark.django_db
def test_equivalent_rerun_does_not_duplicate_review(nir_user: User) -> None:
    case = _make_marked_case(nir_user, declared=("art_perif",))
    _retain_with_divergence(case, ["nefrostomia"])
    reviews_before = list(
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE
        ).order_by("id")
    )
    assert len(reviews_before) == 1
    # Reexecução equivalente (mesma detecção, mesmos digests) não cria revisão.
    fake = ScriptedLlmClient([json.dumps(_artifact(["nefrostomia"]))])
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    reviews_after = list(
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE
        ).order_by("id")
    )
    assert [review.pk for review in reviews_after] == [review.pk for review in reviews_before]
    assert reviews_after[0].payload == reviews_before[0].payload


@pytest.mark.django_db
def test_changed_review_still_records(nir_user: User) -> None:
    case = _make_marked_case(nir_user, declared=("art_perif",))
    _retain_with_divergence(case, ["nefrostomia"])
    # Detecção genuinamente diferente => nova revisão com nova classificação.
    fake = ScriptedLlmClient([json.dumps(_artifact(["cat_cardiaco"]))])
    _run_pipeline(case, fake)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    reviews = list(
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE
        ).order_by("id")
    )
    assert len(reviews) == 2
    latest_classification = reviews[-1].payload["classification"]
    assert isinstance(latest_classification, dict)
    assert latest_classification.get("cat_cardiaco") == "missing_declaration"
    assert latest_classification != reviews[0].payload["classification"]


# ── fix-2 R2-P1-2: mutação após LLM2, antes da transição travada ────────────


def _run_pipeline_with_final_boundary_mutation(
    case: Case, fake: ScriptedLlmClient, mutate: Any | None = None
) -> None:
    """Roda o pipeline injetando ``mutate()`` após a checagem pós-LLM2.

    O seam envolve ``_assert_processing_complete`` do orquestrador apenas para
    executar a mutação DEPOIS da checagem externa e ANTES do atomic final —
    serviços, transações, signals e o guard de completude (dentro do lock)
    permanecem reais.
    """
    import apps.pipeline.orchestrator as orchestrator_module

    original = orchestrator_module._assert_processing_complete
    state = {"mutated": False}

    def _mutating_assert(target: Case) -> None:
        original(target)
        if not state["mutated"]:
            state["mutated"] = True
            assert mutate is not None
            mutate()

    with override_settings(
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    ):
        import unittest.mock as _mock

        with _mock.patch.object(
            orchestrator_module, "_assert_processing_complete", _mutating_assert
        ):
            process_case_pipeline(case.case_id)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("source", "source_changed"),
        ("anonymized", "anonymized_mismatch"),
        ("map", "pseudonym_map_mismatch"),
        ("source_unavailable", "source_unavailable"),
    ],
)
@pytest.mark.django_db
def test_final_boundary_integrity_change_fails_closed(
    nir_user: User, mutation: str, reason: str
) -> None:
    case = _confirmed_case_ready(nir_user)

    def _mutate() -> None:
        if mutation == "source":
            Case.objects.filter(pk=case.pk).update(extracted_text="Fonte trocada apos a LLM2.")
        elif mutation == "anonymized":
            Case.objects.filter(pk=case.pk).update(
                anonymized_text="Texto anonimizado trocado apos a LLM2."
            )
        elif mutation == "map":
            Case.objects.filter(pk=case.pk).update(
                pseudonym_map={"<PESSOA_1>": {"value": "OUTRA", "entity_type": "PERSON"}}
            )
        else:
            case.documents.all().delete()

    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(["nefrostomia"])),
            json.dumps(_llm2_response(["art_perif"])),
        ]
    )
    _run_pipeline_with_final_boundary_mutation(case, fake, _mutate)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value not in _event_types(case)
    assert len(fake.calls) == 2
    invalidated = CaseEvent.objects.filter(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
    )
    assert invalidated.count() == 1
    assert invalidated.get().payload["reason"] == reason


@pytest.mark.parametrize("mutation", ["review_superseded", "declaration_changed"])
@pytest.mark.django_db
def test_final_boundary_gate_change_reconciles(nir_user: User, mutation: str) -> None:
    from apps.cases.procedure_review import (
        anonymized_text_digest_for_case,
        pseudonym_map_digest_for_case,
        source_fingerprint_for_case,
    )

    case = _confirmed_case_ready(nir_user)

    def _mutate() -> None:
        if mutation == "review_superseded":
            CaseEvent.objects.create(
                case_id=case.case_id,
                event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE,
                actor_type=ActorType.SYSTEM,
                actor=None,
                actor_role="system",
                payload={
                    "classification": {"art_perif": "not_detected"},
                    "source_fingerprint": source_fingerprint_for_case(case),
                    "anonymized_text_digest": anonymized_text_digest_for_case(case),
                    "pseudonym_map_digest": pseudonym_map_digest_for_case(case),
                },
            )
        else:
            set_declared_procedures(case, ("filtro_cava",), user=None, role=SYSTEM_ROLE)
            case.refresh_from_db()

    if mutation == "review_superseded":
        fake = ScriptedLlmClient(
            [
                json.dumps(_artifact(["nefrostomia"])),
                json.dumps(_llm2_response(["art_perif"])),
                json.dumps(_artifact(["art_perif"])),
                json.dumps(
                    _llm2_response(["art_perif"], summary="Resumo novo apos reconciliacao final.")
                ),
            ]
        )
        expected_summary = "Resumo novo apos reconciliacao final."
        expected_reason = "review_superseded"
    else:
        fake = ScriptedLlmClient(
            [
                json.dumps(_artifact(["nefrostomia"])),
                json.dumps(_llm2_response(["art_perif"])),
                json.dumps(_artifact_filtro(["filtro_cava"])),
                json.dumps(_llm2_response(["filtro_cava"], summary="Resumo novo do filtro.")),
            ]
        )
        expected_summary = "Resumo novo do filtro."
        expected_reason = "declaration_changed"
    _run_pipeline_with_final_boundary_mutation(case, fake, _mutate)
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert case.summary_text == expected_summary
    assert [model for model, _m, _s in fake.calls] == [
        FAKE_MODEL_LLM1,
        FAKE_MODEL_LLM2,
        FAKE_MODEL_LLM1,
        FAKE_MODEL_LLM2,
    ]
    invalidated = CaseEvent.objects.filter(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED
    )
    assert invalidated.count() == 1
    assert invalidated.get().payload["reason"] == expected_reason


@pytest.mark.django_db
def test_final_boundary_gate_change_divergent_retains(nir_user: User) -> None:
    from apps.cases.procedure_review import (
        anonymized_text_digest_for_case,
        pseudonym_map_digest_for_case,
        source_fingerprint_for_case,
    )

    case = _confirmed_case_ready(nir_user)

    def _mutate() -> None:
        CaseEvent.objects.create(
            case_id=case.case_id,
            event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE,
            actor_type=ActorType.SYSTEM,
            actor=None,
            actor_role="system",
            payload={
                "classification": {"art_perif": "not_detected"},
                "source_fingerprint": source_fingerprint_for_case(case),
                "anonymized_text_digest": anonymized_text_digest_for_case(case),
                "pseudonym_map_digest": pseudonym_map_digest_for_case(case),
            },
        )

    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(["nefrostomia"])),
            json.dumps(_llm2_response(["art_perif"])),
            json.dumps(_artifact(["nefrostomia"])),
        ]
    )
    _run_pipeline_with_final_boundary_mutation(case, fake, _mutate)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value not in _event_types(case)
    assert CaseEventType.CASE_STATUS_FAILED.value not in _event_types(case)
    assert [model for model, _m, _s in fake.calls] == [
        FAKE_MODEL_LLM1,
        FAKE_MODEL_LLM2,
        FAKE_MODEL_LLM1,
    ]
