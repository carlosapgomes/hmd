"""Testes do domínio de revisão de procedimento (slice 001, R1–R3/R6/R7).

Cobre ``apps/cases/procedure_review.py``: digests versionados (fonte, texto
anonimizado e mapa canônico), marcador D1, revisão/confirmacão mais recentes,
validação de autoridade com precedência de integridade (FAILED) sobre
revisão/declaração (gate automático), invalidação uma-vez e definitiva
(restaurar valores não revive), detecção original da revisão e o predicado
estrutural de completude (D6). Uploads ficam em memória (nada de disco).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.core.files.base import ContentFile
from django.test import override_settings

from apps.accounts.models import User
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
    ConfirmationValidity,
    anonymized_text_digest_for_case,
    compute_anonymized_text_digest,
    compute_pseudonym_map_digest,
    effective_procedure_types,
    get_valid_confirmation,
    has_procedure_review_marker,
    invalidated_reason_for,
    is_processing_complete,
    latest_confirmation_event,
    latest_review_event,
    original_detected_types,
    pseudonym_map_digest_for_case,
    record_review_invalidated,
    source_fingerprint_for_case,
    validate_confirmation,
)
from apps.cases.procedures import set_declared_procedures

SYSTEM_ROLE = "system"
_ANONYMIZED_TEXT = "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente."
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "JOSE MARIA DE SOUZA", "entity_type": "PERSON"},
}
_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.fixture(autouse=True)
def _memory_storage() -> Iterator[None]:
    with override_settings(STORAGES=_TEST_STORAGES):
        yield


@pytest.fixture
def owner_user() -> User:
    return User.objects.create_user(username="dono-review", password="senha-teste")


def _advance_to_llm_extracting(case: Case) -> Case:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.LLM_EXTRACTING
    return case


def _attach_document(case: Case, content: bytes = b"%PDF-1.4 relatorio sintetico") -> CaseDocument:
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
    return document


def _make_case(
    owner: User,
    *,
    declared: tuple[str, ...] = ("art_perif",),
    with_marker: bool = False,
) -> Case:
    case = _advance_to_llm_extracting(Case.objects.create(created_by=owner))
    _attach_document(case)
    case.extracted_text = "Texto extraido do relatorio sintetico."
    case.anonymized_text = _ANONYMIZED_TEXT
    case.pseudonym_map = dict(_PSEUDONYMS)
    case.save()
    for procedure_type in declared:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    case.manual_review_required = True
    case.manual_review_reason = "procedure_divergence"
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    if with_marker:
        CaseEvent.objects.create(
            case=case,
            event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED,
            actor_type=ActorType.SYSTEM,
            actor=None,
            actor_role="system",
            payload={"version": 1},
        )
    return case


def _record_review(case: Case) -> CaseEvent:
    """Divergência de revisão com os digests vigentes (como o LLM1 grava)."""
    return CaseEvent.objects.create(
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


def _record_confirmation(case: Case, user: User, *, review: CaseEvent | None = None) -> CaseEvent:
    review = review or _record_review(case)
    return CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED,
        actor_type=ActorType.USER,
        actor=user,
        actor_role="nir",
        payload={
            "version": 1,
            "review_event_id": review.pk,
            "previous_declared_procedures": ["art_perif"],
            "confirmed_procedures": ["art_perif"],
            "selection_changed": False,
            "justification": "Revisei o relatorio e confirmo.",
            "source_fingerprint": source_fingerprint_for_case(case),
            "anonymized_text_digest": anonymized_text_digest_for_case(case),
            "pseudonym_map_digest": pseudonym_map_digest_for_case(case),
        },
    )


# ── Digests versionados ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_anonymized_digest_is_deterministic_and_text_sensitive(owner_user: User) -> None:
    assert compute_anonymized_text_digest("abc") == compute_anonymized_text_digest("abc")
    assert compute_anonymized_text_digest("abc") != compute_anonymized_text_digest("abd")


@pytest.mark.django_db
def test_map_digest_ignores_key_order_but_not_values() -> None:
    first: dict[str, dict[str, str]] = {
        "<A_1>": {"value": "UM", "entity_type": "PERSON"},
        "<B_1>": {"value": "DOIS", "entity_type": "DATE_TIME"},
    }
    reordered: dict[str, dict[str, str]] = {
        "<B_1>": {"value": "DOIS", "entity_type": "DATE_TIME"},
        "<A_1>": {"value": "UM", "entity_type": "PERSON"},
    }
    changed: dict[str, dict[str, str]] = {
        "<A_1>": {"value": "OUTRO", "entity_type": "PERSON"},
        "<B_1>": {"value": "DOIS", "entity_type": "DATE_TIME"},
    }
    assert compute_pseudonym_map_digest(first) == compute_pseudonym_map_digest(reordered)
    assert compute_pseudonym_map_digest(first) != compute_pseudonym_map_digest(changed)


@pytest.mark.django_db
def test_source_fingerprint_tracks_bytes_and_text_not_filename(owner_user: User) -> None:
    case = _make_case(owner_user)
    baseline = source_fingerprint_for_case(case)
    assert baseline is not None
    assert baseline.startswith("v1:")
    assert source_fingerprint_for_case(case) == baseline

    case.extracted_text = "Texto diferente."
    case.save(update_fields=["extracted_text"])
    assert source_fingerprint_for_case(case) != baseline


@pytest.mark.django_db
def test_source_fingerprint_same_name_different_bytes_invalidates(
    owner_user: User,
) -> None:
    case = _make_case(owner_user)
    baseline = source_fingerprint_for_case(case)
    document = case.documents.get()
    document.file.save("relatorio.pdf", ContentFile(b"%PDF-1.4 outros bytes"), save=False)
    document.size_bytes = len(b"%PDF-1.4 outros bytes")
    document.save()
    assert source_fingerprint_for_case(case) != baseline


@pytest.mark.django_db
def test_source_fingerprint_missing_or_unreadable_is_unavailable(
    owner_user: User,
) -> None:
    case = _make_case(owner_user)
    assert source_fingerprint_for_case(case) is not None
    case.documents.all().delete()
    assert source_fingerprint_for_case(case) is None

    no_doc = Case.objects.create(created_by=owner_user)
    assert source_fingerprint_for_case(no_doc) is None


# ── Marcador e eventos mais recentes ───────────────────────────────────────


@pytest.mark.django_db
def test_marker_helper_requires_enabled_event_with_version(owner_user: User) -> None:
    unmarked = _make_case(owner_user, with_marker=False)
    assert has_procedure_review_marker(unmarked) is False
    marked = _make_case(owner_user, with_marker=True)
    assert has_procedure_review_marker(marked) is True


@pytest.mark.django_db
def test_latest_review_and_confirmation_use_timestamp_then_id(
    owner_user: User,
) -> None:
    case = _make_case(owner_user, with_marker=True)
    first = _record_review(case)
    second = _record_review(case)
    assert latest_review_event(case) is not None
    assert latest_review_event(case) is not None
    latest = latest_review_event(case)
    assert latest is not None
    assert latest.pk == second.pk
    assert first.pk != second.pk
    confirmation = _record_confirmation(case, owner_user, review=second)
    assert latest_confirmation_event(case) is not None
    found = latest_confirmation_event(case)
    assert found is not None
    assert found.pk == confirmation.pk


# ── Validação de autoridade (R3) ───────────────────────────────────────────


@pytest.mark.django_db
def test_validate_confirmation_ok(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    confirmation = _record_confirmation(case, owner_user)
    validity = validate_confirmation(case, confirmation)
    assert validity == ConfirmationValidity(valid=True, reason="ok")
    effective, event = get_valid_confirmation(case)
    assert effective == ("art_perif",)
    assert event is not None
    assert event.pk == confirmation.pk


@pytest.mark.django_db
def test_validate_source_changed(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    confirmation = _record_confirmation(case, owner_user)
    case.extracted_text = "Fonte alterada."
    case.save(update_fields=["extracted_text"])
    assert validate_confirmation(case, confirmation).reason == "source_changed"


@pytest.mark.django_db
def test_validate_anonymized_mismatch_and_missing(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    confirmation = _record_confirmation(case, owner_user)
    case.anonymized_text = "Texto anonimizado diferente."
    case.save(update_fields=["anonymized_text"])
    assert validate_confirmation(case, confirmation).reason == "anonymized_mismatch"
    case.anonymized_text = ""
    case.save(update_fields=["anonymized_text"])
    assert validate_confirmation(case, confirmation).reason == "anonymization_missing"


@pytest.mark.django_db
def test_validate_map_mismatch(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    confirmation = _record_confirmation(case, owner_user)
    case.pseudonym_map = {"<PESSOA_1>": {"value": "OUTRA PESSOA", "entity_type": "PERSON"}}
    case.save(update_fields=["pseudonym_map"])
    assert validate_confirmation(case, confirmation).reason == "pseudonym_map_mismatch"


@pytest.mark.django_db
def test_validate_review_superseded_and_declaration_changed(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    confirmation = _record_confirmation(case, owner_user)
    _record_review(case)
    assert validate_confirmation(case, confirmation).reason == "review_superseded"

    other = _make_case(owner_user, with_marker=True)
    other_confirmation = _record_confirmation(other, owner_user)
    set_declared_procedures(other, ("filtro_cava",), user=None, role=SYSTEM_ROLE)
    other.refresh_from_db()
    assert validate_confirmation(other, other_confirmation).reason == "declaration_changed"


@pytest.mark.django_db
def test_integrity_precedes_review_and_declaration_changes(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    confirmation = _record_confirmation(case, owner_user)
    _record_review(case)
    case.extracted_text = "Fonte alterada."
    case.save(update_fields=["extracted_text"])
    validity = validate_confirmation(case, confirmation)
    assert validity.valid is False
    assert validity.reason == "source_changed"


@pytest.mark.django_db
def test_invalidated_is_definitive_and_never_duplicated(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    confirmation = _record_confirmation(case, owner_user)
    set_declared_procedures(case, ("filtro_cava",), user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    first = record_review_invalidated(case, confirmation, "declaration_changed")
    second = record_review_invalidated(case, confirmation, "declaration_changed")
    assert first.pk == second.pk
    assert (
        CaseEvent.objects.filter(
            case=case,
            event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED,
            payload__confirmation_event_id=confirmation.pk,
        ).count()
        == 1
    )
    assert invalidated_reason_for(case, confirmation) == "declaration_changed"

    # Restaura a declaração: a autoridade NÃO revive e não duplica o evento.
    set_declared_procedures(case, ("art_perif",), user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    validity = validate_confirmation(case, confirmation)
    assert validity.valid is False
    assert validity.reason == "declaration_changed"
    assert (
        CaseEvent.objects.filter(
            case=case,
            event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED,
        ).count()
        == 1
    )
    effective, event = get_valid_confirmation(case)
    assert effective is None
    assert event is not None
    assert event.pk == confirmation.pk


@pytest.mark.django_db
def test_validate_malformed_payload_and_actor(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    review = _record_review(case)
    malformed = CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED,
        actor_type=ActorType.USER,
        actor=owner_user,
        actor_role="nir",
        payload={
            "version": 99,
            "review_event_id": review.pk,
            "previous_declared_procedures": ["art_perif"],
            "confirmed_procedures": ["art_perif"],
            "selection_changed": False,
            "justification": "Justificativa.",
            "source_fingerprint": source_fingerprint_for_case(case),
            "anonymized_text_digest": anonymized_text_digest_for_case(case),
            "pseudonym_map_digest": pseudonym_map_digest_for_case(case),
        },
    )
    assert validate_confirmation(case, malformed).reason == "payload_invalid"

    no_actor = CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="nir",
        payload={
            "version": 1,
            "review_event_id": review.pk,
            "previous_declared_procedures": ["art_perif"],
            "confirmed_procedures": ["art_perif"],
            "selection_changed": False,
            "justification": "Justificativa.",
            "source_fingerprint": source_fingerprint_for_case(case),
            "anonymized_text_digest": anonymized_text_digest_for_case(case),
            "pseudonym_map_digest": pseudonym_map_digest_for_case(case),
        },
    )
    assert validate_confirmation(case, no_actor).reason == "actor_invalid"


# ── Detecção original e completude ─────────────────────────────────────────


@pytest.mark.django_db
def test_original_detected_types_from_review_classification(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    review = _record_review(case)
    assert original_detected_types(review) == ("nefrostomia",)

    mixed = CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={
            "classification": {
                "art_perif": "not_detected",
                "cat_cardiaco": "match",
                "nefrostomia": "missing_declaration",
            }
        },
    )
    assert original_detected_types(mixed) == ("cat_cardiaco", "nefrostomia")


@pytest.mark.django_db
def test_effective_types_use_confirmation_when_valid(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    assert effective_procedure_types(case) == ("art_perif",)
    _record_confirmation(case, owner_user)
    assert effective_procedure_types(case) == ("art_perif",)


def _complete_artifacts(case: Case) -> None:
    case.structured_data = {"pedido": {"procedimentos_solicitados": ["art_perif"]}}
    case.summary_text = "Sumario clinico em portugues."
    case.policy_result = {
        "art_perif": {"recommendation": "recomenda_aceitar", "refusal_reasons": []}
    }
    case.suggested_action = {
        "procedures": {"art_perif": {"suggestion": "aceitar", "motivos": []}},
        "aggregate": {"suggestion": "aceitar", "motivos": []},
    }
    case.manual_review_required = False
    case.manual_review_reason = ""
    case.save()


@pytest.mark.django_db
def test_completeness_requires_full_current_artifacts(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    assert is_processing_complete(case) is False
    _complete_artifacts(case)
    assert is_processing_complete(case) is True

    case.summary_text = "   \n  "
    case.save(update_fields=["summary_text"])
    assert is_processing_complete(case) is False

    _complete_artifacts(case)
    case.policy_result = {}
    case.save(update_fields=["policy_result"])
    assert is_processing_complete(case) is False

    _complete_artifacts(case)
    case.suggested_action = {
        "procedures": {"filtro_cava": {"suggestion": "aceitar", "motivos": []}},
        "aggregate": {"suggestion": "aceitar", "motivos": []},
    }
    case.save(update_fields=["suggested_action"])
    assert is_processing_complete(case) is False

    _complete_artifacts(case)
    case.manual_review_required = True
    case.manual_review_reason = "procedure_divergence"
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    assert is_processing_complete(case) is False


# ── fix-1 P1-1: guard público de completude ────────────────────────────────


@pytest.mark.django_db
def test_complete_llm_summarization_rejects_incomplete_marked_case(owner_user: User) -> None:
    from django_fsm import TransitionNotAllowed

    case = _make_case(owner_user, with_marker=True)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.LLM_SUMMARIZING
    events_before = CaseEvent.objects.filter(case=case).count()
    with pytest.raises(TransitionNotAllowed):
        case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_SUMMARIZING
    assert CaseEvent.objects.filter(case=case).count() == events_before


@pytest.mark.django_db
def test_complete_llm_summarization_allows_complete_marked_case(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    _complete_artifacts(case)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


@pytest.mark.django_db
def test_complete_llm_summarization_unmarked_incomplete_still_allowed(
    owner_user: User,
) -> None:
    case = _make_case(owner_user, with_marker=False)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


# ── fix-1 P1-5: invalidação de gate não mascara integridade ────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mutate,expected",
    [
        ("source", "source_changed"),
        ("anonymized", "anonymized_mismatch"),
        ("map", "pseudonym_map_mismatch"),
    ],
)
def test_gate_invalidated_then_integrity_failure_is_failed_reason(
    owner_user: User, mutate: str, expected: str
) -> None:
    from apps.cases.procedure_review import is_failed_reason

    case = _make_case(owner_user, with_marker=True)
    confirmation = _record_confirmation(case, owner_user)
    set_declared_procedures(case, ("filtro_cava",), user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    record_review_invalidated(case, confirmation, "declaration_changed")
    if mutate == "source":
        case.extracted_text = "Fonte alterada apos invalidacao de gate."
        case.save(update_fields=["extracted_text"])
    elif mutate == "anonymized":
        case.anonymized_text = "Texto anonimizado alterado."
        case.save(update_fields=["anonymized_text"])
    else:
        case.pseudonym_map = {"<PESSOA_1>": {"value": "OUTRA", "entity_type": "PERSON"}}
        case.save(update_fields=["pseudonym_map"])
    validity = validate_confirmation(case, confirmation)
    assert validity.valid is False
    assert validity.reason == expected
    assert is_failed_reason(validity.reason) is True
    # Sem duplicar o evento histórico de invalidação.
    record_review_invalidated(case, confirmation, validity.reason)
    assert (
        CaseEvent.objects.filter(
            case=case,
            event_type=CaseEventType.CASE_PROCEDURE_REVIEW_INVALIDATED,
            payload__confirmation_event_id=confirmation.pk,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_gate_invalidated_integrity_restored_still_not_revived(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    confirmation = _record_confirmation(case, owner_user)
    original_text = case.extracted_text
    set_declared_procedures(case, ("filtro_cava",), user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    record_review_invalidated(case, confirmation, "declaration_changed")
    case.extracted_text = "Fonte alterada."
    case.save(update_fields=["extracted_text"])
    assert validate_confirmation(case, confirmation).reason == "source_changed"
    case.extracted_text = original_text
    case.save(update_fields=["extracted_text"])
    # Autoridade histórica não revive mesmo com integridade restaurada.
    validity = validate_confirmation(case, confirmation)
    assert validity.valid is False
    assert validity.reason == "declaration_changed"
    effective, event = get_valid_confirmation(case)
    assert effective is None
    assert event is not None and event.pk == confirmation.pk


@pytest.mark.django_db
def test_completeness_rejects_wrong_aggregate(owner_user: User) -> None:
    case = _make_case(owner_user, with_marker=True)
    _complete_artifacts(case)
    case.suggested_action = {
        "procedures": {"art_perif": {"suggestion": "aceitar", "motivos": []}},
        "aggregate": {"suggestion": "recusar", "motivos": ["motivo"]},
    }
    case.save(update_fields=["suggested_action"])
    assert is_processing_complete(case) is False
