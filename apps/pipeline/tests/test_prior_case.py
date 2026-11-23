"""Testes do prior-case (slice 005, R4–R6, design D7).

Cobre o lookup por procedimento declarado: match primário por
``agency_record_number`` igual (janela de 7 dias, intervalo fechado) e
fallback por nome normalizado (sem acentos/upper/sem espaços) + nascimento
iguais (janela de 15 dias, intervalo fechado ``decisão <= created_at <=
decisão + janela``); prioridade nº > fallback; escolha da decisão mais
recente; exclusão do próprio caso e de casos sem decisão; motivo
ANONIMIZADO pelo núcleo ``anonymize_text`` do change 05 (stubado aqui — o
núcleo real é coberto pela suíte da anonimização); e o wrapper
``record_prior_case_lookups`` com eventos ``PRIOR_CASE_LOOKUP`` e origem
``occurrence_number | name_birthdate_fallback | none``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import Case, CaseProcedure, DoctorDisposition
from apps.pipeline import prior_case as prior_case_module
from apps.pipeline.prior_case import lookup_prior_case_context, record_prior_case_lookups


@dataclass(frozen=True)
class _FakeAnonymizeResult:
    """Resultado do núcleo de anonimização (somente o texto interessa)."""

    anonymized_text: str


class _FakeAnonymizer:
    """Stub de ``anonymize_text``: registra as chamadas/seed e devolve um token."""

    def __init__(self, replacement: str) -> None:
        self.replacement = replacement
        self.calls: list[str] = []
        self.seeds: list[Mapping[str, Mapping[str, str]] | None] = []

    def __call__(
        self,
        text: str,
        seed_map: Mapping[str, Mapping[str, str]] | None = None,
    ) -> _FakeAnonymizeResult:
        self.calls.append(text)
        self.seeds.append(seed_map)
        return _FakeAnonymizeResult(anonymized_text=self.replacement)


@pytest.fixture
def fake_anonymizer(monkeypatch: pytest.MonkeyPatch) -> _FakeAnonymizer:
    """Stub de ``anonymize_text`` no módulo de prior-case (sem spaCy na suíte)."""
    fake = _FakeAnonymizer("<ANONIMIZADO>")
    monkeypatch.setattr(prior_case_module, "anonymize_text", fake)
    return fake


# Intenção legada: estes testes pinam o lookup 7/15 + nome/nascimento
# (modo vigente quando foram escritos); o modo novo é coberto no bloco S1.
_LEGACY_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=False)


@pytest.fixture
def owner_user() -> User:
    """Dono dos casos dos testes (a FSM não exige papel)."""
    return User.objects.create_user(username="dono-prior", password="senha-teste")


def _make_case(
    owner: User,
    *,
    created_at: datetime,
    agency_record_number: str = "",
    patient_name: str = "",
    patient_birth_date: Any = None,
) -> Case:
    """Caso com ``created_at`` controlado e linkage persistido (estilo change 05)."""
    case = Case.objects.create(created_by=owner)
    case.agency_record_number = agency_record_number
    case.patient_name = patient_name
    case.patient_birth_date = patient_birth_date
    case.save(update_fields=["agency_record_number", "patient_name", "patient_birth_date"])
    Case.objects.filter(pk=case.pk).update(created_at=created_at)
    case.refresh_from_db()
    return case


def _add_procedure(
    case: Case,
    procedure_type: str,
    *,
    disposition: str = "pending",
    reason: str = "",
    decided_at: datetime | None = None,
) -> CaseProcedure:
    """Row de procedimento com disposição/instante de decisão controlados."""
    row = CaseProcedure.objects.create(case=case, procedure_type=procedure_type)
    if disposition != DoctorDisposition.PENDING:
        row.doctor_disposition = disposition
        row.doctor_reason = reason
        row.doctor_decided_at = decided_at
        row.save(update_fields=["doctor_disposition", "doctor_reason", "doctor_decided_at"])
    return row


def _decided_at(days_before: float, *, reference: datetime | None = None) -> datetime:
    """Instante de decisão ``days_before`` dias antes de ``reference``/agora."""
    anchor = reference or timezone.now()
    return anchor - timedelta(days=days_before)


# ── R4: match por nº de ocorrência (7 dias) ────────────────────────────────


@_LEGACY_MODE
@pytest.mark.django_db
def test_match_by_number_7d(owner_user: User, fake_anonymizer: _FakeAnonymizer) -> None:
    """R4/R7: nº de ocorrência igual e decisão dentro de 7 dias → resumo com
    origem ``occurrence_number`` e motivo anonimizado pelo núcleo."""
    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="33345",
        patient_name="Maria da Silva",
    )
    decided_at = _decided_at(3.0, reference=current.created_at)
    prior = _make_case(
        owner_user,
        created_at=decided_at,
        agency_record_number="33345",
        patient_name="Maria da Silva",
    )
    _add_procedure(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        reason="Paciente José com INR elevado e plaquetas baixas.",
        decided_at=decided_at,
    )
    _add_procedure(current, "art_perif")

    summary = lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.origin == "occurrence_number"
    assert summary.decision == DoctorDisposition.DENIED
    assert summary.prior_case_id == str(prior.case_id)
    assert summary.decided_at == decided_at.isoformat()
    assert summary.prior_denial_count == 1
    # Motivo anonimizado pelo núcleo (stub) — o texto livre do médico nunca
    # chega cru ao resumo que alimenta o LLM2.
    assert summary.reason_anonymized == "<ANONIMIZADO>"
    assert fake_anonymizer.calls and "INR elevado" in fake_anonymizer.calls[0]
    assert "José" not in summary.reason_anonymized


# ── R2/D3: motivo anonimizado no espaço de tokens do caso (seed) ───────────


@_LEGACY_MODE
@pytest.mark.django_db
def test_reason_reuses_case_tokens_without_label(owner_user: User) -> None:
    """R2/D3: motivo do prévio citando o nome SEM rótulo SESAB → token do CASO.

    O motivo é texto livre do médico (sem "Paciente:", logo sem extração
    determinística por rótulo); a semeadura com o ``pseudonym_map`` do caso
    anterior faz a varredura dos valores conhecidos localizar o nome e o
    operador reutilizar o token do caso (``<PESSOA_2>``) — com o NER desligado
    (default da fase 2).
    """
    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="33345",
    )
    decided_at = _decided_at(3.0, reference=current.created_at)
    prior = _make_case(
        owner_user,
        created_at=decided_at,
        agency_record_number="33345",
        patient_name="Maria da Silva",
    )
    prior.pseudonym_map = {
        "<PESSOA_1>": {"value": "JOSE CARLOS DE OLIVEIRA", "entity_type": "PESSOA"},
        "<PESSOA_2>": {"value": "Maria da Silva", "entity_type": "PESSOA"},
    }
    prior.save(update_fields=["pseudonym_map"])
    _add_procedure(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        reason="Maria da Silva apresenta INR elevado e plaquetas baixas.",
        decided_at=decided_at,
    )
    _add_procedure(current, "art_perif")

    with override_settings(ANONYMIZATION_USE_NER=False):
        summary = lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.reason_anonymized == "<PESSOA_2> apresenta INR elevado e plaquetas baixas."
    assert "Maria da Silva" not in summary.reason_anonymized


@_LEGACY_MODE
@pytest.mark.django_db
def test_match_by_number_approved_and_count(owner_user: User) -> None:
    """R4: pool por nº com decisões múltiplas → escolhe a mais recente e conta
    apenas as negações do pool."""
    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="33345",
    )
    denied_at = _decided_at(5.0, reference=current.created_at)
    denied = _make_case(owner_user, created_at=denied_at, agency_record_number="33345")
    _add_procedure(
        denied,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        reason="negado",
        decided_at=denied_at,
    )
    approved_at = _decided_at(1.0, reference=current.created_at)
    approved = _make_case(owner_user, created_at=approved_at, agency_record_number="33345")
    _add_procedure(
        approved,
        "art_perif",
        disposition=DoctorDisposition.APPROVED,
        decided_at=approved_at,
    )
    _add_procedure(current, "art_perif")

    summary = lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.origin == "occurrence_number"
    assert summary.prior_case_id == str(approved.case_id)
    assert summary.decision == DoctorDisposition.APPROVED
    assert summary.prior_denial_count == 1  # apenas a negativa entra na contagem


@_LEGACY_MODE
@pytest.mark.django_db
def test_no_match_outside_7d(owner_user: User) -> None:
    """R7: nº igual mas decisão fora de 7 dias → sem match (e sem fallback
    quando nome/nascimento não batem)."""
    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="33345",
        patient_name="Maria da Silva",
    )
    prior = _make_case(
        owner_user,
        created_at=_decided_at(10.0),
        agency_record_number="33345",
        patient_name="Outro Paciente",
    )
    _add_procedure(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        decided_at=_decided_at(10.0, reference=current.created_at),
    )
    _add_procedure(current, "art_perif")

    assert lookup_prior_case_context(current, "art_perif") is None


# ── R4: fallback nome normalizado + nascimento (15 dias) ───────────────────


@_LEGACY_MODE
@pytest.mark.django_db
def test_fallback_name_birthdate_15d(owner_user: User) -> None:
    """R7: nº diferente/ausente + nome normalizado e nascimento iguais dentro
    de 15 dias → resumo com origem ``name_birthdate_fallback``."""
    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="",
        patient_name="José Maria da Silva",
        patient_birth_date="1960-03-05",
    )
    decided_at = _decided_at(10.0, reference=current.created_at)
    prior = _make_case(
        owner_user,
        created_at=decided_at,
        agency_record_number="98765",
        patient_name="jose maria da silva",  # caixa diferente — normalização cobre
        patient_birth_date="1960-03-05",
    )
    _add_procedure(
        prior,
        "cat_cardiaco",
        disposition=DoctorDisposition.DENIED,
        reason="negado",
        decided_at=decided_at,
    )
    _add_procedure(current, "cat_cardiaco")

    summary = lookup_prior_case_context(current, "cat_cardiaco")

    assert summary is not None
    assert summary.origin == "name_birthdate_fallback"
    assert summary.prior_case_id == str(prior.case_id)
    assert summary.prior_denial_count == 1
    assert summary.decision == DoctorDisposition.DENIED


@_LEGACY_MODE
@pytest.mark.django_db
def test_fallback_normalizes_accents_and_whitespace(owner_user: User) -> None:
    """R4: nome com acentos/espaços é normalizado (unaccent/upper/sem espaços)."""
    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        patient_name="Antônio Carlos de Souza",
        patient_birth_date="1955-12-01",
    )
    decided_at = _decided_at(4.0, reference=current.created_at)
    prior = _make_case(
        owner_user,
        created_at=decided_at,
        patient_name="ANTONIO  CARLOS DE  SOUZA",
        patient_birth_date="1955-12-01",
    )
    _add_procedure(
        prior,
        "angio_coronariana",
        disposition=DoctorDisposition.DENIED,
        decided_at=decided_at,
    )
    _add_procedure(current, "angio_coronariana")

    summary = lookup_prior_case_context(current, "angio_coronariana")

    assert summary is not None
    assert summary.origin == "name_birthdate_fallback"


@_LEGACY_MODE
@pytest.mark.django_db
def test_fallback_outside_15d(owner_user: User) -> None:
    """R7: fallback com decisão há mais de 15 dias → sem match."""
    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        patient_name="Maria da Silva",
        patient_birth_date="1960-03-05",
    )
    prior = _make_case(
        owner_user,
        created_at=_decided_at(20.0),
        patient_name="Maria da Silva",
        patient_birth_date="1960-03-05",
    )
    _add_procedure(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        decided_at=_decided_at(20.0, reference=current.created_at),
    )
    _add_procedure(current, "art_perif")

    assert lookup_prior_case_context(current, "art_perif") is None


@_LEGACY_MODE
@pytest.mark.django_db
def test_fallback_rejects_future_decision(owner_user: User) -> None:
    """R4 (correção do review): intervalo FECHADO — decisão após o ``created_at``
    do caso atual (caso "futuro") nunca casa."""
    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        patient_name="Maria da Silva",
        patient_birth_date="1960-03-05",
    )
    future_decision = current.created_at + timedelta(days=2)
    prior = _make_case(
        owner_user,
        created_at=_decided_at(5.0),
        patient_name="Maria da Silva",
        patient_birth_date="1960-03-05",
    )
    _add_procedure(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        decided_at=future_decision,
    )
    _add_procedure(current, "art_perif")

    assert lookup_prior_case_context(current, "art_perif") is None


@_LEGACY_MODE
@pytest.mark.django_db
def test_priority_number_over_fallback(owner_user: User) -> None:
    """R4: nº de ocorrência tem prioridade sobre o fallback por nome+nascimento."""
    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="33345",
        patient_name="Maria da Silva",
        patient_birth_date="1960-03-05",
    )
    by_number_at = _decided_at(2.0, reference=current.created_at)
    by_number = _make_case(owner_user, created_at=by_number_at, agency_record_number="33345")
    _add_procedure(
        by_number,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        decided_at=by_number_at,
    )
    fallback_at = _decided_at(1.0, reference=current.created_at)
    fallback = _make_case(
        owner_user,
        created_at=fallback_at,
        patient_name="Maria da Silva",
        patient_birth_date="1960-03-05",
    )
    _add_procedure(
        fallback,
        "art_perif",
        disposition=DoctorDisposition.APPROVED,
        decided_at=fallback_at,
    )
    _add_procedure(current, "art_perif")

    summary = lookup_prior_case_context(current, "art_perif")

    # O match por nº vence mesmo sendo mais antigo que o candidato de fallback.
    assert summary is not None
    assert summary.origin == "occurrence_number"
    assert summary.prior_case_id == str(by_number.case_id)
    assert summary.decision == DoctorDisposition.DENIED


@_LEGACY_MODE
@pytest.mark.django_db
def test_excludes_self_and_undecided(owner_user: User) -> None:
    """R7: o próprio caso (mesmo já decidido) e casos sem decisão são excluídos."""
    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="33345",
    )
    # O próprio caso tem row decidida — ainda assim não pode casar consigo.
    _add_procedure(
        current,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        reason="negado no próprio caso",
        decided_at=_decided_at(0.5, reference=current.created_at),
    )
    # Caso com nº igual mas sem decisão (pending / sem doctor_decided_at).
    undecided = _make_case(owner_user, created_at=_decided_at(2.0), agency_record_number="33345")
    _add_procedure(undecided, "art_perif", disposition=DoctorDisposition.PENDING)

    assert lookup_prior_case_context(current, "art_perif") is None


@_LEGACY_MODE
@pytest.mark.django_db
def test_only_same_procedure_type_matches(owner_user: User) -> None:
    """R4: o lookup é por procedimento — decisão de outro tipo não casa."""
    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="33345",
    )
    decided_at = _decided_at(2.0, reference=current.created_at)
    prior = _make_case(owner_user, created_at=decided_at, agency_record_number="33345")
    _add_procedure(
        prior,
        "permicath",
        disposition=DoctorDisposition.DENIED,
        reason="negado",
        decided_at=decided_at,
    )
    _add_procedure(current, "art_perif")

    assert lookup_prior_case_context(current, "art_perif") is None


@_LEGACY_MODE
@pytest.mark.django_db
def test_undecided_only_case_in_window_returns_none(owner_user: User) -> None:
    """Sem nenhum candidato decidido → ``None`` (produção inicial, sem decisões)."""
    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="33345",
    )
    other = _make_case(owner_user, created_at=_decided_at(1.0), agency_record_number="33345")
    _add_procedure(other, "art_perif")
    _add_procedure(current, "art_perif")

    assert lookup_prior_case_context(current, "art_perif") is None


# ── R5: wrapper com eventos por origem ─────────────────────────────────────


def _declare(case: Case, procedure_type: str) -> None:
    CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)


@_LEGACY_MODE
@pytest.mark.django_db
def test_lookup_events_with_origin(owner_user: User, fake_anonymizer: _FakeAnonymizer) -> None:
    """R5/R7: eventos ``PRIOR_CASE_LOOKUP`` com origem correta por procedimento
    (occurrence_number | name_birthdate_fallback | none), payload enxuto e sem
    o motivo cru (só o resumo anonimizado alimenta o LLM2)."""
    from apps.cases.events import CaseEventType
    from apps.cases.models import ActorType

    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="33345",
        patient_name="Maria da Silva",
        patient_birth_date="1960-03-05",
    )
    _declare(current, "art_perif")
    _declare(current, "cat_cardiaco")
    _declare(current, "permicath")

    by_number_at = _decided_at(2.0, reference=current.created_at)
    by_number = _make_case(owner_user, created_at=by_number_at, agency_record_number="33345")
    _add_procedure(
        by_number,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        reason="motivo confidencial do médico",
        decided_at=by_number_at,
    )

    record_prior_case_lookups(current, user=owner_user, role="nir")

    events = list(current.events.filter(event_type=CaseEventType.PRIOR_CASE_LOOKUP).order_by("id"))
    assert len(events) == 3
    by_type = {event.payload["procedure_type"]: event for event in events}

    art_perif = by_type["art_perif"]
    assert art_perif.payload["origin"] == "occurrence_number"
    assert art_perif.payload["matched"] is True
    assert art_perif.payload["prior_case_id"] == str(by_number.case_id)
    assert art_perif.payload["decision"] == DoctorDisposition.DENIED
    assert art_perif.payload["prior_denial_count"] == 1
    assert "reason" not in art_perif.payload  # motivo não vai ao evento
    assert art_perif.actor_type == ActorType.USER
    assert art_perif.actor == owner_user
    assert art_perif.actor_role == "nir"
    assert fake_anonymizer.calls and "confidencial" in fake_anonymizer.calls[0]

    # Sem candidatos → origem ``none``.
    assert by_type["permicath"].payload["origin"] == "none"
    assert by_type["permicath"].payload["matched"] is False


@_LEGACY_MODE
@pytest.mark.django_db
def test_lookup_event_fallback_origin(owner_user: User) -> None:
    """R5: fallback por nome+nascimento grava origem ``name_birthdate_fallback``."""
    from apps.cases.events import CaseEventType

    current = _make_case(
        owner_user,
        created_at=_decided_at(0.0),
        patient_name="José Maria da Silva",
        patient_birth_date="1960-03-05",
    )
    _declare(current, "flebografia")
    decided_at = _decided_at(4.0, reference=current.created_at)
    prior = _make_case(
        owner_user,
        created_at=decided_at,
        patient_name="JOSE MARIA DA SILVA",
        patient_birth_date="1960-03-05",
    )
    _add_procedure(
        prior,
        "flebografia",
        disposition=DoctorDisposition.DENIED,
        decided_at=decided_at,
    )

    record_prior_case_lookups(current, user=None, role="system")

    event = current.events.get(event_type=CaseEventType.PRIOR_CASE_LOOKUP)
    assert event.payload["origin"] == "name_birthdate_fallback"
    assert event.payload["matched"] is True
    assert event.payload["decision"] == DoctorDisposition.DENIED


@_LEGACY_MODE
@pytest.mark.django_db
def test_lookup_wrapper_requires_declared(owner_user: User) -> None:
    """R5: sem procedimentos declarados o wrapper falha nomeando a exigência."""
    current = _make_case(owner_user, created_at=_decided_at(0.0))

    with pytest.raises(ValueError, match="sem procedimentos declarados"):
        record_prior_case_lookups(current, user=None, role="system")


# ── S1 (RED): identidade nova sem nascimento — CNS e regulação em 14 dias ──
# Com a flag ligada, o lookup usa SOMENTE a janela única de 15 dias e a
# identidade ampliada (nome normalizado + regulação/CNS, sem nascimento).
# Antes da implementação o comportamento legado responde (7/15 + nome/
# nascimento) e estes cenários novos retornam ``None`` — RED de comportamento.

_S1_CNS_A = "207139200176053"
_S1_CNS_B = "605932160152243"

_NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)


def _decide(
    case: Case,
    procedure_type: str,
    *,
    disposition: str = "denied",
    reason: str = "negado",
    decided_at: datetime | None = None,
) -> None:
    """Marca a row declarada como decidida (modo novo exige declared_by_nir)."""
    CaseProcedure.objects.filter(case=case, procedure_type=procedure_type).update(
        doctor_disposition=disposition,
        doctor_reason=reason,
        doctor_decided_at=decided_at,
    )


def _supports_patient_cns() -> bool:
    """O campo ``Case.patient_cns`` já existe? (RED honesto pré-migration)."""
    return any(field.name == "patient_cns" for field in Case._meta.get_fields())


def _make_s1_case(
    owner: User,
    *,
    created_at: datetime,
    agency_record_number: str = "",
    patient_name: str = "",
    patient_cns: str = "",
) -> Case:
    """Caso S1 com CNS estrutural.

    Antes da migration o campo não existe: o CNS é ignorado de propósito e
    os testes de CNS falham por comportamento (lookup legado sem CNS), não
    por erro de setup. Depois da migration o CNS passa a persistir.
    """
    case = _make_case(
        owner,
        created_at=created_at,
        agency_record_number=agency_record_number,
        patient_name=patient_name,
    )
    if patient_cns and _supports_patient_cns():
        Case.objects.filter(pk=case.pk).update(patient_cns=patient_cns)
        case.refresh_from_db()
    return case


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_match_by_cns_without_birthdate_14d(owner_user: User) -> None:
    """S1/R3: nome + CNS válido iguais, sem nascimento, decisão há 14 dias →
    resumo com origem ``patient_cns`` (legado retornaria ``None``)."""
    current = _make_s1_case(
        owner_user,
        created_at=_decided_at(0.0),
        patient_name="Maria da Silva",
        patient_cns=_S1_CNS_A,
    )
    decided_at = _decided_at(14.0, reference=current.created_at)
    prior = _make_s1_case(
        owner_user,
        created_at=decided_at,
        patient_name="MARIA DA SILVA",
        patient_cns=_S1_CNS_A,
    )
    _declare(
        prior,
        "art_perif",
    )
    _decide(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        reason="negado",
        decided_at=decided_at,
    )
    _declare(current, "art_perif")

    summary = lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.origin == "patient_cns"
    assert summary.prior_case_id == str(prior.case_id)
    assert summary.decision == DoctorDisposition.DENIED


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_match_by_number_14d_beyond_legacy_window(owner_user: User) -> None:
    """S1/R3: nome + regulação iguais, decisão há 14 dias → origem
    ``occurrence_number`` na janela única (legado de 7 dias retornaria None)."""
    current = _make_s1_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="77701",
        patient_name="Maria da Silva",
    )
    decided_at = _decided_at(14.0, reference=current.created_at)
    prior = _make_s1_case(
        owner_user,
        created_at=decided_at,
        agency_record_number="77701",
        patient_name="maria da silva",
    )
    _declare(
        prior,
        "art_perif",
    )
    _decide(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        reason="negado",
        decided_at=decided_at,
    )
    _declare(current, "art_perif")

    summary = lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.origin == "occurrence_number"


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_requires_name_even_with_number(owner_user: User) -> None:
    """S1/R2: regulação igual mas nome diferente → sem candidato (o legado
    casava só pelo número)."""
    current = _make_s1_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="77702",
        patient_name="Maria da Silva",
    )
    decided_at = _decided_at(3.0, reference=current.created_at)
    prior = _make_s1_case(
        owner_user,
        created_at=decided_at,
        agency_record_number="77702",
        patient_name="Joana Pereira",
    )
    _declare(
        prior,
        "art_perif",
    )
    _decide(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        decided_at=decided_at,
    )
    _declare(current, "art_perif")

    assert lookup_prior_case_context(current, "art_perif") is None


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_distinct_cns_blocks_automation(owner_user: User) -> None:
    """S1/R2: CNS válidos divergentes, mesmo com regulação igual → sem match
    automático (conflito exige conferência NIR, S5)."""
    current = _make_s1_case(
        owner_user,
        created_at=_decided_at(0.0),
        agency_record_number="77703",
        patient_name="Maria da Silva",
        patient_cns=_S1_CNS_A,
    )
    decided_at = _decided_at(3.0, reference=current.created_at)
    prior = _make_s1_case(
        owner_user,
        created_at=decided_at,
        agency_record_number="77703",
        patient_name="Maria da Silva",
        patient_cns=_S1_CNS_B,
    )
    _declare(
        prior,
        "art_perif",
    )
    _decide(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        decided_at=decided_at,
    )
    _declare(current, "art_perif")

    assert lookup_prior_case_context(current, "art_perif") is None


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_weak_origin_only_is_not_context(owner_user: User) -> None:
    """S1/R3: somente nome + origem, sem CNS/regulação → sem contexto factual
    antes da confirmação NIR (nunca vira prior-case sozinho)."""
    current = _make_s1_case(owner_user, created_at=_decided_at(0.0))
    current.patient_name = "Maria da Silva"
    current.origin_unit = "Hospital Geral do Estado"
    current.save(update_fields=["patient_name", "origin_unit"])
    decided_at = _decided_at(3.0, reference=current.created_at)
    prior = _make_s1_case(owner_user, created_at=decided_at)
    prior.patient_name = "Maria da Silva"
    prior.origin_unit = "Hospital Geral do Estado"
    prior.save(update_fields=["patient_name", "origin_unit"])
    _declare(
        prior,
        "art_perif",
    )
    _decide(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        decided_at=decided_at,
    )
    _declare(current, "art_perif")

    assert lookup_prior_case_context(current, "art_perif") is None


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_lookup_event_uses_cns_origin(owner_user: User) -> None:
    """S1/R4: evento do wrapper no modo novo usa ``patient_cns`` (sem PII)."""
    from apps.cases.events import CaseEventType

    current = _make_s1_case(
        owner_user,
        created_at=_decided_at(0.0),
        patient_name="Maria da Silva",
        patient_cns=_S1_CNS_A,
    )
    _declare(current, "art_perif")
    decided_at = _decided_at(5.0, reference=current.created_at)
    prior = _make_s1_case(
        owner_user,
        created_at=decided_at,
        patient_name="Maria da Silva",
        patient_cns=_S1_CNS_A,
    )
    _declare(
        prior,
        "art_perif",
    )
    _decide(
        prior,
        "art_perif",
        disposition=DoctorDisposition.DENIED,
        reason="motivo confidencial",
        decided_at=decided_at,
    )

    record_prior_case_lookups(current, user=None, role="system")

    event = current.events.get(event_type=CaseEventType.PRIOR_CASE_LOOKUP)
    assert event.payload["origin"] == "patient_cns"
    assert event.payload["matched"] is True
    assert _S1_CNS_A not in str(event.payload)
