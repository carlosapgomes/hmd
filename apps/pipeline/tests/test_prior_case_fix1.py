"""Fix-1: regressões P1-02/P1-03 (RED) + limites R3/R4 do modo novo (fix-1).

P1-02: o modo novo escolhia ``number_matches or cns_matches`` — havendo match
por regulação, descartava matches por CNS mais recentes e suas negativas.
P1-03: o modo novo materializava TODAS as rows decididas do tipo antes de
filtrar a janela em Python. P1-04: limites da janela fechada de 15 dias,
exclusões e privacidade do payload no modo novo.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseProcedure, CaseStatus, DoctorDisposition
from apps.pipeline import prior_case as prior_case_module
from apps.pipeline.llm import LlmPipelineError

_NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)

_CNS_A = "207139200176053"
_CNS_B = "605932160152243"


@pytest.fixture(autouse=True)
def _seeded_prompts() -> None:
    """Seed idempotente dos prompts (exigido pelos casos em LLM_SUMMARIZING)."""
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture
def owner_user() -> User:
    """Dono dos casos dos testes."""
    return User.objects.create_user(username="dono-fix1", password="senha-teste")


def _make_case(
    owner: User,
    *,
    created_at: datetime,
    agency_record_number: str = "",
    patient_name: str = "",
    patient_cns: str = "",
) -> Case:
    """Caso com ``created_at`` controlado e sinais de identidade persistidos."""
    case = Case.objects.create(created_by=owner)
    case.agency_record_number = agency_record_number
    case.patient_name = patient_name
    case.save(update_fields=["agency_record_number", "patient_name"])
    if patient_cns:
        Case.objects.filter(pk=case.pk).update(patient_cns=patient_cns)
    Case.objects.filter(pk=case.pk).update(created_at=created_at)
    case.refresh_from_db()
    return case


def _declare(case: Case, procedure_type: str) -> None:
    CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)


def _decide(
    case: Case,
    procedure_type: str,
    *,
    disposition: str,
    reason: str = "negado",
    decided_at: datetime | None = None,
) -> None:
    CaseProcedure.objects.filter(case=case, procedure_type=procedure_type).update(
        doctor_disposition=disposition,
        doctor_reason=reason,
        doctor_decided_at=decided_at,
    )


def _ago(days: float, *, reference: datetime) -> datetime:
    return reference - timedelta(days=days)


# ── P1-02: pool combinado, mais recente governa, contagem soma negativas ──


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_combines_pools_latest_cns_wins(owner_user: User) -> None:
    """P1-02: match por número (antigo, negado) + match por CNS (novo, aprovado).

    O resumo deve governar pela decisão mais recente (CNS/aprovado), contar a
    negativa do pool válido e conservar a origem da row selecionada.
    """
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        agency_record_number="77710",
        patient_name="Maria da Silva",
        patient_cns=_CNS_A,
    )
    _declare(current, "art_perif")
    old_at = _ago(5.0, reference=current.created_at)
    old = _make_case(
        owner_user,
        created_at=old_at,
        agency_record_number="77710",
        patient_name="Maria da Silva",
    )
    _declare(old, "art_perif")
    _decide(old, "art_perif", disposition=DoctorDisposition.DENIED, decided_at=old_at)
    new_at = _ago(1.0, reference=current.created_at)
    new = _make_case(
        owner_user,
        created_at=new_at,
        agency_record_number="99999",
        patient_name="Maria da Silva",
        patient_cns=_CNS_A,
    )
    _declare(new, "art_perif")
    _decide(
        new,
        "art_perif",
        disposition=DoctorDisposition.APPROVED,
        reason="aprovado",
        decided_at=new_at,
    )

    summary = prior_case_module.lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.prior_case_id == str(new.case_id)
    assert summary.decision == DoctorDisposition.APPROVED
    assert summary.origin == "patient_cns"
    assert summary.prior_denial_count == 1


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_combined_denial_count(owner_user: User) -> None:
    """P1-02: duas negativas fortes (número antiga + CNS recente) → conta 2, governa a recente."""
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        agency_record_number="77711",
        patient_name="Maria da Silva",
        patient_cns=_CNS_A,
    )
    _declare(current, "art_perif")
    old_at = _ago(6.0, reference=current.created_at)
    old = _make_case(
        owner_user,
        created_at=old_at,
        agency_record_number="77711",
        patient_name="Maria da Silva",
    )
    _declare(old, "art_perif")
    _decide(old, "art_perif", disposition=DoctorDisposition.DENIED, decided_at=old_at)
    new_at = _ago(2.0, reference=current.created_at)
    new = _make_case(
        owner_user,
        created_at=new_at,
        agency_record_number="00000",
        patient_name="MARIA DA SILVA",
        patient_cns=_CNS_A,
    )
    _declare(new, "art_perif")
    _decide(new, "art_perif", disposition=DoctorDisposition.DENIED, decided_at=new_at)

    summary = prior_case_module.lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.prior_case_id == str(new.case_id)
    assert summary.decision == DoctorDisposition.DENIED
    assert summary.origin == "patient_cns"
    assert summary.prior_denial_count == 2


# ── P1-03: consulta limitada no banco ─────────────────────────────────────


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_bounds_query_by_window_and_identity(
    owner_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-03: população irrelevante (fora da janela/sem identidade) não chega ao classificador."""
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        agency_record_number="88801",
        patient_name="Maria da Silva",
        patient_cns=_CNS_A,
    )
    _declare(current, "art_perif")
    # 6 rows com o MESMO número mas decisão há 60 dias (fora da janela).
    for index in range(6):
        decided_at = _ago(60.0, reference=current.created_at)
        other = _make_case(
            owner_user,
            created_at=decided_at,
            agency_record_number="88801",
            patient_name="Maria da Silva",
        )
        _declare(other, "art_perif")
        _decide(other, "art_perif", disposition=DoctorDisposition.DENIED, decided_at=decided_at)
        assert index >= 0
    # 6 rows na janela mas sem identidade (outro nome/número/CNS).
    for _ in range(6):
        decided_at = _ago(2.0, reference=current.created_at)
        other = _make_case(
            owner_user,
            created_at=decided_at,
            agency_record_number="00000",
            patient_name="Joana Pereira",
            patient_cns=_CNS_B,
        )
        _declare(other, "art_perif")
        _decide(other, "art_perif", disposition=DoctorDisposition.DENIED, decided_at=decided_at)
    # 1 row relevante (número igual, na janela).
    relevant_at = _ago(3.0, reference=current.created_at)
    relevant = _make_case(
        owner_user,
        created_at=relevant_at,
        agency_record_number="88801",
        patient_name="Maria da Silva",
    )
    _declare(relevant, "art_perif")
    _decide(relevant, "art_perif", disposition=DoctorDisposition.DENIED, decided_at=relevant_at)

    calls = {"count": 0}
    real_classify = getattr(prior_case_module, "classify_pair")

    def _counting(first: Any, second: Any) -> Any:
        calls["count"] += 1
        return real_classify(first, second)

    monkeypatch.setattr("apps.pipeline.prior_case.classify_pair", _counting)
    with CaptureQueriesContext(connection) as queries:
        summary = prior_case_module.lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.prior_case_id == str(relevant.case_id)
    # 13 rows decididas no banco; só a relevante (e no máximo o próprio caso)
    # pode alcançar o classificador.
    assert calls["count"] <= 2
    select_sql = " ".join(query["sql"] for query in queries.captured_queries)
    assert "agency_record_number" in select_sql
    assert "doctor_decided_at" in select_sql


# ── P1-04: limites da janela fechada de 15 dias no modo novo ──────────────


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_closed_window_bounds(owner_user: User) -> None:
    """P1-04: igualdade com created_at inclui; futuro e além de 15 dias excluem."""
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        agency_record_number="90001",
        patient_name="Maria da Silva",
    )
    _declare(current, "art_perif")

    # Decisão exatamente em created_at → incluída (janela fechada).
    edge = _make_case(
        owner_user,
        created_at=current.created_at - timedelta(days=1),
        agency_record_number="90001",
        patient_name="Maria da Silva",
    )
    _declare(edge, "art_perif")
    _decide(edge, "art_perif", disposition=DoctorDisposition.DENIED, decided_at=current.created_at)
    assert prior_case_module.lookup_prior_case_context(current, "art_perif") is not None

    # Decisão 15 dias + 1 hora antes → fora da janela.
    CaseProcedure.objects.filter(case=edge).update(
        doctor_decided_at=current.created_at - timedelta(days=15, hours=1)
    )
    assert prior_case_module.lookup_prior_case_context(current, "art_perif") is None

    # Decisão futura → nunca casa.
    CaseProcedure.objects.filter(case=edge).update(
        doctor_decided_at=current.created_at + timedelta(days=1)
    )
    assert prior_case_module.lookup_prior_case_context(current, "art_perif") is None


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_excludes_self_and_set_mismatch(owner_user: User) -> None:
    """P1-04: row decidida do próprio caso e conjunto efetivo distinto não casam."""
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        agency_record_number="90002",
        patient_name="Maria da Silva",
        patient_cns=_CNS_A,
    )
    _declare(current, "art_perif")
    _declare(current, "cat_cardiaco")
    decided_at = _ago(1.0, reference=current.created_at)
    CaseProcedure.objects.filter(case=current, procedure_type="art_perif").update(
        doctor_disposition=DoctorDisposition.DENIED,
        doctor_reason="negado",
        doctor_decided_at=decided_at,
    )
    other = _make_case(
        owner_user,
        created_at=decided_at,
        agency_record_number="90002",
        patient_name="Maria da Silva",
        patient_cns=_CNS_A,
    )
    _declare(other, "art_perif")  # conjunto {art_perif} ≠ {art_perif, cat_cardiaco} do atual.
    _decide(other, "art_perif", disposition=DoctorDisposition.DENIED, decided_at=decided_at)

    assert prior_case_module.lookup_prior_case_context(current, "art_perif") is None


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_latest_and_count_mixed_pool(owner_user: User) -> None:
    """P1-04: pool válido misto — a mais recente governa e as negativas somam."""
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        agency_record_number="90003",
        patient_name="Maria da Silva",
        patient_cns=_CNS_A,
    )
    _declare(current, "art_perif")
    first_at = _ago(10.0, reference=current.created_at)
    first = _make_case(
        owner_user,
        created_at=first_at,
        agency_record_number="90003",
        patient_name="Maria da Silva",
    )
    _declare(first, "art_perif")
    _decide(first, "art_perif", disposition=DoctorDisposition.DENIED, decided_at=first_at)
    second_at = _ago(4.0, reference=current.created_at)
    second = _make_case(
        owner_user,
        created_at=second_at,
        agency_record_number="11111",
        patient_name="Maria da Silva",
        patient_cns=_CNS_A,
    )
    _declare(second, "art_perif")
    _decide(
        second,
        "art_perif",
        disposition=DoctorDisposition.APPROVED,
        reason="aprovado",
        decided_at=second_at,
    )

    summary = prior_case_module.lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.prior_case_id == str(second.case_id)
    assert summary.decision == DoctorDisposition.APPROVED
    assert summary.origin == "patient_cns"
    assert summary.prior_denial_count == 1


@_NEW_MODE
@pytest.mark.django_db
def test_lookup_wrapper_cleaned_creates_no_events(owner_user: User) -> None:
    """P1-04: caso CLEANED — o wrapper não persiste lookups (gate de minimização)."""
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        agency_record_number="90004",
        patient_name="Maria da Silva",
    )
    _declare(current, "art_perif")
    Case.objects.filter(pk=current.pk).update(status=CaseStatus.CLEANED)
    current.refresh_from_db()

    prior_case_module.record_prior_case_lookups(current, user=None, role="system")

    assert current.events.filter(event_type=CaseEventType.PRIOR_CASE_LOOKUP).count() == 0


@_NEW_MODE
@pytest.mark.django_db
def test_cns_reason_and_event_carry_no_pii(owner_user: User) -> None:
    """P1-04: motivo de prévio por CNS com nome/CNS em texto livre não vaza."""
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        patient_name="Maria da Silva",
        patient_cns=_CNS_A,
    )
    _declare(current, "art_perif")
    decided_at = _ago(5.0, reference=current.created_at)
    prior = _make_case(
        owner_user,
        created_at=decided_at,
        patient_name="Maria da Silva",
        patient_cns=_CNS_A,
    )
    prior.pseudonym_map = {
        "<PESSOA_1>": {"value": "Maria da Silva", "entity_type": "PERSON"},
        "<CNS_1>": {"value": _CNS_A, "entity_type": "CNS"},
    }
    prior.save(update_fields=["pseudonym_map"])
    _declare(prior, "art_perif")
    _decide(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        reason=f"Paciente Maria da Silva, CNS {_CNS_A}, com INR elevado.",
        decided_at=decided_at,
    )

    summary = prior_case_module.lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.origin == "patient_cns"
    assert "Maria da Silva" not in summary.reason_anonymized
    assert _CNS_A not in summary.reason_anonymized

    prior_case_module.record_prior_case_lookups(current, user=None, role="system")
    event = current.events.get(event_type=CaseEventType.PRIOR_CASE_LOOKUP)
    assert _CNS_A not in str(event.payload)
    assert "Maria da Silva" not in str(event.payload)


@_NEW_MODE
@pytest.mark.django_db
def test_llm2_backstop_rejects_cns_prior_values(owner_user: User) -> None:
    """P1-04: o assert recursivo recusa payload com valor real do mapa do prévio por CNS."""
    from apps.pipeline.llm2_service import run_llm2_summarization

    real_name = "MARIA APARECIDA DA SILVA"
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        patient_name="Maria Aparecida da Silva",
        patient_cns=_CNS_A,
    )
    current.start_pdf_extraction(user=None, role="system")
    current.complete_pdf_extraction(user=None, role="system")
    current.complete_anonymization(user=None, role="system")
    _declare(current, "art_perif")
    _declare(current, "filtro_cava")
    current.complete_llm_extraction(user=None, role="system")
    current.anonymized_text = "Relatorio de <PESSOA_1> com claudicacao."
    current.pseudonym_map = {"<PESSOA_1>": {"value": real_name, "entity_type": "PERSON"}}
    current.structured_data = {
        "pedido": {
            "procedimentos_solicitados": ["art_perif", "filtro_cava"],
            "evidence_spans": [],
        }
    }
    current.policy_result = {
        "art_perif": {
            "procedure_type": "art_perif",
            "section_id": "S1",
            "recommendation": "recomenda_aceitar",
            "refusal_reasons": [],
            "criteria": [],
        },
        "filtro_cava": {
            "procedure_type": "filtro_cava",
            "section_id": "S6",
            "recommendation": "recomenda_aceitar",
            "refusal_reasons": [],
            "criteria": [],
        },
    }
    current.save()
    decided_at = _ago(1.0, reference=current.created_at)
    prior = _make_case(
        owner_user,
        created_at=decided_at,
        patient_name="Maria Aparecida da Silva",
        patient_cns=_CNS_A,
    )
    prior.pseudonym_map = {"<PESSOA_9>": {"value": real_name, "entity_type": "PERSON"}}
    prior.save(update_fields=["pseudonym_map"])
    _declare(prior, "art_perif")
    _decide(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        reason="negado",
        decided_at=decided_at,
    )

    response = {
        "summary_text": "Sumario clinico em portugues.",
        "procedures": [
            {
                "procedure_type": "art_perif",
                "suggestion": "recusar",
                "motivos": [f"Paciente {real_name} recusado no caso anterior"],
                "evidence_spans": [],
            },
            {
                "procedure_type": "filtro_cava",
                "suggestion": "aceitar",
                "motivos": ["sem contraindicoes"],
                "evidence_spans": [],
            },
        ],
    }

    class _ScriptedClient:
        def __init__(self, payload: str) -> None:
            self.payload = payload

        def complete(
            self,
            model: str,
            messages: list[dict[str, str]],
            *,
            json_schema: dict[str, Any] | None = None,
        ) -> str:
            assert model and messages and json_schema is not None
            return self.payload

    with override_settings(
        LLM_CLIENT_FACTORY=lambda: _ScriptedClient(json.dumps(response)),
        LLM2_MODEL="modelo-llm2-teste",
        ANONYMIZATION_USE_NER=False,
    ):
        with pytest.raises(LlmPipelineError) as excinfo:
            run_llm2_summarization(current, user=None, role="system")

    assert excinfo.value.reason == "llm2_token_leak"
