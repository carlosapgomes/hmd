"""Testes do serviço de confirmação NIR (slice 001, R1–R2).

Cobre ``apps/intake/services.py::confirm_case_procedure_review``: manutenção e
troca válidas (inclusive para tipo não detectado), consentimento e justificativa
(1–500 úteis, rejeitando NUL), marcador/revisão/fonte vigentes com snapshot
do servidor, negativas de autorização (papel, dono, lock, decisão) e atomicidade
durável (rollback total, submissão única, preservação de fontes/id/tempo). O
disparo pós-commit real com ``transaction=True`` e enqueue mockado em
recorder (a cadeia serviço→worker com LLM fake vive em
``apps/pipeline/tests/test_human_confirmed_procedures.py``).
"""

from __future__ import annotations

import uuid

import pytest
from django.core.files.base import ContentFile

from apps.accounts.models import Role, User
from apps.cases.events import CaseEventType
from apps.cases.locks import CaseLockConflictError, claim_case_lock
from apps.cases.models import (
    ActorType,
    Case,
    CaseDocument,
    CaseEvent,
    CaseProcedure,
    CaseStatus,
)
from apps.cases.procedure_review import (
    anonymized_text_digest_for_case,
    pseudonym_map_digest_for_case,
    source_fingerprint_for_case,
)
from apps.intake.services import confirm_case_procedure_review

SYSTEM_ROLE = "system"
NIR_ROLE = "nir"
_ANONYMIZED_TEXT = "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente."
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "JOSE MARIA DE SOUZA", "entity_type": "PERSON"},
}


@pytest.fixture
def nir_user() -> User:
    role, _ = Role.objects.get_or_create(name=NIR_ROLE)
    user = User.objects.create_user(username="nir-confirm", password="senha-teste")
    user.roles.add(role)
    return user


@pytest.fixture
def other_nir_user() -> User:
    role, _ = Role.objects.get_or_create(name=NIR_ROLE)
    user = User.objects.create_user(username="nir-outro", password="senha-teste")
    user.roles.add(role)
    return user


@pytest.fixture
def doctor_user() -> User:
    role, _ = Role.objects.get_or_create(name="doctor")
    user = User.objects.create_user(username="doctor-confirm", password="senha-teste")
    user.roles.add(role)
    return user


def _attach_document(case: Case, content: bytes = b"%PDF-1.4 relatorio sintetico") -> None:
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


def _make_marked_retained_case(
    owner: User,
    *,
    declared: tuple[str, ...] = ("art_perif",),
    with_marker: bool = True,
) -> Case:
    # Avanço FSM sem executar as cadeias reais (em transaction=True os
    # on_commit disparam a cada commit): anonymization/pipeline mockados aqui
    # para o recorder do teste observar só o dispatch da confirmação.
    from unittest.mock import patch

    def _noop(case_id: object) -> None:
        del case_id

    with (
        patch("apps.anonymization.tasks.enqueue_case_anonymization", _noop),
        patch("apps.pipeline.tasks.enqueue_case_pipeline", _noop),
    ):
        case = Case.objects.create(created_by=owner)
        case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
        case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
        case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.LLM_EXTRACTING
    _attach_document(case)
    case.extracted_text = "Texto extraido do relatorio sintetico."
    case.anonymized_text = _ANONYMIZED_TEXT
    case.pseudonym_map = dict(_PSEUDONYMS)
    # Derivados "antigos" que a confirmação deve limpar (prova de não reuso).
    case.structured_data = {"pedido": {"procedimentos_solicitados": ["art_perif"]}}
    case.summary_text = "Resumo antigo."
    case.policy_result = {"art_perif": {"recommendation": "recomenda_aceitar"}}
    case.suggested_action = {"procedures": {}, "aggregate": {"suggestion": "aceitar"}}
    case.manual_review_required = True
    case.manual_review_reason = "procedure_divergence"
    case.save()
    for procedure_type in declared:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    # Detecção antiga (a confirmação deve resetá-la para pending sem apagar rows).
    CaseProcedure.objects.filter(case=case).update(detection_status="detected")
    if with_marker:
        CaseEvent.objects.create(
            case=case,
            event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED,
            actor_type=ActorType.SYSTEM,
            actor=None,
            actor_role="system",
            payload={"version": 1},
        )
    CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={
            "classification": {
                "art_perif": "not_detected",
                "nefrostomia": "missing_declaration",
            },
            "source_fingerprint": source_fingerprint_for_case(case),
            "anonymized_text_digest": anonymized_text_digest_for_case(case),
            "pseudonym_map_digest": pseudonym_map_digest_for_case(case),
        },
    )
    return case


def _review_event(case: Case) -> CaseEvent:
    return CaseEvent.objects.get(case=case, event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE)


def _confirm_kwargs(case: Case, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "case": case,
        "user": case.created_by,
        "role": NIR_ROLE,
        "procedure_type": "art_perif",
        "review_acknowledged": "on",
        "review_justification": "Revisei o relatorio principal e confirmo a selecao.",
        "review_event_id": _review_event(case).pk,
        "source_fingerprint": source_fingerprint_for_case(case),
    }
    kwargs.update(overrides)
    return kwargs


@pytest.fixture
def enqueue_recorder(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    calls: list[uuid.UUID] = []

    def _record(case_id: uuid.UUID) -> None:
        calls.append(case_id)

    monkeypatch.setattr("apps.pipeline.tasks.enqueue_case_pipeline", _record)
    return calls


# ── R1: confirmação válida (manter/trocar) ─────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_confirm_keep_same_type(nir_user: User, enqueue_recorder: list[uuid.UUID]) -> None:
    case = _make_marked_retained_case(nir_user)
    case_id = case.case_id
    created_at = case.created_at
    review = _review_event(case)
    fingerprint = source_fingerprint_for_case(case)

    result = confirm_case_procedure_review(**_confirm_kwargs(case))  # type: ignore[arg-type]
    result.refresh_from_db()

    assert result.status == CaseStatus.LLM_EXTRACTING
    assert result.case_id == case_id
    assert result.created_at == created_at
    assert result.manual_review_required is False
    assert result.manual_review_reason == ""
    assert result.structured_data == {}
    assert result.summary_text == ""
    assert result.policy_result == {}
    assert result.suggested_action == {}
    # Fontes preservadas.
    assert result.extracted_text == "Texto extraido do relatorio sintetico."
    assert result.anonymized_text == _ANONYMIZED_TEXT
    assert result.pseudonym_map == _PSEUDONYMS
    assert result.documents.count() == 1
    # Detecção resetada, rows preservadas.
    rows = list(result.procedures.all())
    assert len(rows) == 1
    assert rows[0].detection_status == "pending"
    assert rows[0].declared_by_nir is True
    # Auditoria correlacionada.
    confirmation = CaseEvent.objects.get(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED
    )
    assert confirmation.actor == nir_user
    assert confirmation.actor_role == NIR_ROLE
    assert confirmation.payload["version"] == 1
    assert confirmation.payload["review_event_id"] == review.pk
    assert confirmation.payload["previous_declared_procedures"] == ["art_perif"]
    assert confirmation.payload["confirmed_procedures"] == ["art_perif"]
    assert confirmation.payload["selection_changed"] is False
    assert confirmation.payload["source_fingerprint"] == fingerprint
    assert confirmation.payload["anonymized_text_digest"] == anonymized_text_digest_for_case(result)
    assert confirmation.payload["pseudonym_map_digest"] == pseudonym_map_digest_for_case(result)
    assert confirmation.payload["justification"].strip() != ""
    reprocessing = CaseEvent.objects.get(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REPROCESSING_REQUESTED
    )
    assert reprocessing.payload["confirmation_event_id"] == confirmation.pk
    # Disparo único pós-commit e lease liberada antes do commit.
    assert enqueue_recorder == [case_id]
    assert result.locked_until is None
    assert result.lock_token is None


@pytest.mark.django_db(transaction=True)
def test_confirm_change_to_undetected_type(
    nir_user: User, enqueue_recorder: list[uuid.UUID]
) -> None:
    case = _make_marked_retained_case(nir_user)
    result = confirm_case_procedure_review(
        **_confirm_kwargs(case, procedure_type="nefrostomia")  # type: ignore[arg-type]
    )
    result.refresh_from_db()
    assert (
        result.procedures.filter(declared_by_nir=True)
        .values_list("procedure_type", flat=True)
        .get()
        == "nefrostomia"
    )
    confirmation = CaseEvent.objects.get(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED
    )
    assert confirmation.payload["previous_declared_procedures"] == ["art_perif"]
    assert confirmation.payload["confirmed_procedures"] == ["nefrostomia"]
    assert confirmation.payload["selection_changed"] is True
    # A row antiga permanece como observação (história auditável).
    assert result.procedures.filter(procedure_type="art_perif").exists()
    assert enqueue_recorder == [case.case_id]


@pytest.mark.django_db(transaction=True)
def test_confirm_justification_500_chars_accepted(
    nir_user: User, enqueue_recorder: list[uuid.UUID]
) -> None:
    case = _make_marked_retained_case(nir_user)
    justification = "x" * 500
    result = confirm_case_procedure_review(
        **_confirm_kwargs(case, review_justification=f"  {justification}  ")  # type: ignore[arg-type]
    )
    confirmation = CaseEvent.objects.get(
        case=result, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED
    )
    assert confirmation.payload["justification"] == justification
    assert enqueue_recorder == [case.case_id]


# ── fix-1 P1-4: digest ausente/malformado na revisão é rejeição ───────────


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "missing_key",
    ["source_fingerprint", "anonymized_text_digest", "pseudonym_map_digest"],
)
def test_confirm_rejects_review_with_missing_digest_key(
    nir_user: User, enqueue_recorder: list[uuid.UUID], missing_key: str
) -> None:
    case = _make_marked_retained_case(nir_user)
    review = _review_event(case)
    assert isinstance(review.payload, dict)
    payload = dict(review.payload)
    del payload[missing_key]
    CaseEvent.objects.filter(pk=review.pk).update(payload=payload)
    events_before = case.events.count()
    with pytest.raises(ValueError):
        confirm_case_procedure_review(**_confirm_kwargs(case))  # type: ignore[arg-type]
    case.refresh_from_db()
    assert case.events.count() == events_before
    assert case.manual_review_required is True
    assert case.structured_data != {}
    assert enqueue_recorder == []
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED
        ).count()
        == 0
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("bad_value", [123, "not-a-digest", None, ""])
def test_confirm_rejects_review_with_malformed_digest(
    nir_user: User, enqueue_recorder: list[uuid.UUID], bad_value: object
) -> None:
    case = _make_marked_retained_case(nir_user)
    review = _review_event(case)
    assert isinstance(review.payload, dict)
    payload = dict(review.payload)
    payload["anonymized_text_digest"] = bad_value
    CaseEvent.objects.filter(pk=review.pk).update(payload=payload)
    events_before = case.events.count()
    with pytest.raises(ValueError):
        confirm_case_procedure_review(**_confirm_kwargs(case))  # type: ignore[arg-type]
    case.refresh_from_db()
    assert case.events.count() == events_before
    assert case.manual_review_required is True
    assert enqueue_recorder == []
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED
        ).count()
        == 0
    )


# ── R1: negativas de autorização e precondição ─────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_confirm_rejects_wrong_role(
    nir_user: User, doctor_user: User, enqueue_recorder: list[uuid.UUID]
) -> None:
    case = _make_marked_retained_case(nir_user)
    events_before = case.events.count()
    with pytest.raises(PermissionError):
        confirm_case_procedure_review(**_confirm_kwargs(case, role="doctor"))  # type: ignore[arg-type]
    # Criador sem vínculo com o papel nir (papel ativo alegado, sem membership).
    foreign = _make_marked_retained_case(doctor_user)
    with pytest.raises(PermissionError):
        confirm_case_procedure_review(
            **_confirm_kwargs(foreign, user=doctor_user, role="doctor")  # type: ignore[arg-type]
        )
    with pytest.raises(PermissionError):
        confirm_case_procedure_review(
            **_confirm_kwargs(foreign, user=doctor_user, role=NIR_ROLE)  # type: ignore[arg-type]
        )
    case.refresh_from_db()
    assert case.events.count() == events_before
    assert case.manual_review_required is True
    assert enqueue_recorder == []


@pytest.mark.django_db(transaction=True)
def test_confirm_rejects_non_owner(
    nir_user: User, other_nir_user: User, enqueue_recorder: list[uuid.UUID]
) -> None:
    """R2 (visibilidade-por-equipe slice 003, corpo invertido — D6: título
    antitético preservado): NIR não-criador confirma o procedimento da equipe —
    a confirmação é aceita com o OPERADOR como ator do evento."""
    case = _make_marked_retained_case(nir_user)
    result = confirm_case_procedure_review(
        **_confirm_kwargs(case, user=other_nir_user)  # type: ignore[arg-type]
    )
    result.refresh_from_db()
    assert result.manual_review_required is False
    assert result.manual_review_reason == ""
    confirmation = CaseEvent.objects.get(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED
    )
    assert confirmation.actor == other_nir_user
    assert confirmation.actor_role == NIR_ROLE
    assert enqueue_recorder == [case.case_id]


@pytest.mark.django_db(transaction=True)
def test_confirm_rejects_unmarked_case(nir_user: User, enqueue_recorder: list[uuid.UUID]) -> None:
    case = _make_marked_retained_case(nir_user, with_marker=False)
    events_before = case.events.count()
    with pytest.raises(ValueError, match="marcador|revisão|revisao"):
        confirm_case_procedure_review(**_confirm_kwargs(case))  # type: ignore[arg-type]
    case.refresh_from_db()
    assert case.events.count() == events_before
    assert enqueue_recorder == []


@pytest.mark.django_db(transaction=True)
def test_confirm_rejects_when_not_retained(
    nir_user: User, enqueue_recorder: list[uuid.UUID]
) -> None:
    case = _make_marked_retained_case(nir_user)
    case.manual_review_required = False
    case.manual_review_reason = ""
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    events_before = case.events.count()
    with pytest.raises(ValueError):
        confirm_case_procedure_review(**_confirm_kwargs(case))  # type: ignore[arg-type]
    case.refresh_from_db()
    assert case.events.count() == events_before
    assert enqueue_recorder == []


@pytest.mark.django_db(transaction=True)
def test_confirm_rejects_active_lock_of_another_actor(
    nir_user: User, other_nir_user: User, enqueue_recorder: list[uuid.UUID]
) -> None:
    case = _make_marked_retained_case(nir_user)
    claim_case_lock(case, user=other_nir_user, context="worker_llm", role=SYSTEM_ROLE)
    events_before = case.events.count()
    with pytest.raises(CaseLockConflictError):
        confirm_case_procedure_review(**_confirm_kwargs(case))  # type: ignore[arg-type]
    case.refresh_from_db()
    assert case.events.count() == events_before
    assert case.manual_review_required is True
    assert enqueue_recorder == []


@pytest.mark.django_db(transaction=True)
def test_confirm_rejects_when_doctor_decision_exists(
    nir_user: User, enqueue_recorder: list[uuid.UUID]
) -> None:
    case = _make_marked_retained_case(nir_user)
    CaseProcedure.objects.filter(case=case, procedure_type="art_perif").update(
        doctor_disposition="approved"
    )
    events_before = case.events.count()
    with pytest.raises(ValueError):
        confirm_case_procedure_review(**_confirm_kwargs(case))  # type: ignore[arg-type]
    case.refresh_from_db()
    assert case.events.count() == events_before
    assert enqueue_recorder == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "overrides",
    [
        {"review_justification": ""},
        {"review_justification": "   "},
        {"review_justification": "x" * 501},
        {"review_justification": "texto\x00com nul"},
        {"review_acknowledged": None},
        {"review_acknowledged": "off"},
        {"review_acknowledged": "false"},
        {"review_acknowledged": False},
        {"procedure_type": "tipo_inexistente"},
        {"procedure_type": ""},
        {"review_event_id": 999999},
        {"review_event_id": "nao-numerico"},
        {"source_fingerprint": ""},
        {"source_fingerprint": None},
        {"source_fingerprint": "v1:obsoleto"},
    ],
)
def test_confirm_rejects_invalid_inputs(
    nir_user: User, enqueue_recorder: list[uuid.UUID], overrides: dict[str, object]
) -> None:
    case = _make_marked_retained_case(nir_user)
    events_before = case.events.count()
    with pytest.raises(ValueError):
        confirm_case_procedure_review(**_confirm_kwargs(case, **overrides))  # type: ignore[arg-type]
    case.refresh_from_db()
    assert case.events.count() == events_before
    assert case.manual_review_required is True
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED
        ).count()
        == 0
    )
    assert enqueue_recorder == []


@pytest.mark.django_db(transaction=True)
def test_confirm_rejects_stale_anonymized_snapshot(
    nir_user: User, enqueue_recorder: list[uuid.UUID]
) -> None:
    case = _make_marked_retained_case(nir_user)
    fingerprint = source_fingerprint_for_case(case)
    case.anonymized_text = "Texto anonimizado alterado."
    case.save(update_fields=["anonymized_text"])
    events_before = case.events.count()
    with pytest.raises(ValueError):
        confirm_case_procedure_review(
            **_confirm_kwargs(case, source_fingerprint=fingerprint)  # type: ignore[arg-type]
        )
    case.refresh_from_db()
    assert case.events.count() == events_before
    assert enqueue_recorder == []


@pytest.mark.django_db(transaction=True)
def test_confirm_rejects_missing_source(nir_user: User, enqueue_recorder: list[uuid.UUID]) -> None:
    case = _make_marked_retained_case(nir_user)
    case.documents.all().delete()
    events_before = case.events.count()
    assert source_fingerprint_for_case(case) is None
    with pytest.raises(ValueError):
        confirm_case_procedure_review(**_confirm_kwargs(case))  # type: ignore[arg-type]
    case.refresh_from_db()
    assert case.events.count() == events_before
    assert enqueue_recorder == []


@pytest.mark.django_db(transaction=True)
def test_second_submission_has_no_second_confirmation(
    nir_user: User, enqueue_recorder: list[uuid.UUID]
) -> None:
    case = _make_marked_retained_case(nir_user)
    confirm_case_procedure_review(**_confirm_kwargs(case))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        confirm_case_procedure_review(**_confirm_kwargs(case))  # type: ignore[arg-type]
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED
        ).count()
        == 1
    )
    assert (
        CaseEvent.objects.filter(
            case=case, event_type=CaseEventType.CASE_PROCEDURE_REPROCESSING_REQUESTED
        ).count()
        == 1
    )
    assert enqueue_recorder == [case.case_id]


# ── R2: rollback total (atomicidade durável) ───────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_rollback_on_injected_error(
    nir_user: User,
    enqueue_recorder: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _make_marked_retained_case(nir_user)
    case_id = case.case_id
    events_before = case.events.count()

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("falha injetada")

    monkeypatch.setattr("apps.intake.services.set_declared_procedures", _boom)
    with pytest.raises(RuntimeError, match="falha injetada"):
        confirm_case_procedure_review(**_confirm_kwargs(case))  # type: ignore[arg-type]

    case.refresh_from_db()
    assert case.case_id == case_id
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == "procedure_divergence"
    # Derivados/limpeza revertidos junto com a confirmação.
    assert case.structured_data == {"pedido": {"procedimentos_solicitados": ["art_perif"]}}
    assert case.summary_text == "Resumo antigo."
    assert case.events.count() == events_before
    assert case.locked_until is None
    assert enqueue_recorder == []
