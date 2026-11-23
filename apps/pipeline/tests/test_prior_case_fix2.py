"""Fix-2 P1-03 RED: filtro SQL com a mesma normalização do classificador.

P1-03: ``_lookup_new_mode`` comparava o número de regulação atual (strip)
com o valor armazenado EXATO, e o CNS atual (strip) com o armazenado EXATO —
enquanto ``classify_pair`` aplica ``strip()`` nos DOIS lados (D1). Um prévio
persistido com padding (``"  77710  "``, ``"\\t...\\n"``) casa no classificador
mas era descartado pelo SQL antes de qualquer classificação, perdendo
contexto factual válido. A consulta permanece limitada no banco (janela
fechada + identidade forte antes de qualquer materialização); só a
comparação passa a normalizar os dois lados, espelhando ``str.strip``.

Estes testes falham contra o comportamento vigente (RED de comportamento) e
passam após a correção. O controle de identidade em branco segue sem match.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import Case, CaseProcedure, DoctorDisposition
from apps.pipeline import prior_case as prior_case_module

_NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)

_CNS_A = "207139200176053"


@pytest.fixture(autouse=True)
def _seeded_prompts() -> None:
    """Seed idempotente dos prompts (exigido pelos casos em LLM_SUMMARIZING)."""
    from django.core.management import call_command

    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture
def owner_user() -> User:
    """Dono dos casos dos testes."""
    return User.objects.create_user(username="dono-fix2", password="senha-teste")


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
    disposition: str = DoctorDisposition.DENIED,
    decided_at: datetime | None = None,
) -> None:
    CaseProcedure.objects.filter(case=case, procedure_type=procedure_type).update(
        doctor_disposition=disposition,
        doctor_reason="negado",
        doctor_decided_at=decided_at,
    )


# ── P1-03: normalização dos dois lados no SQL ───────────────────────────────


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_matches_padded_stored_number(owner_user: User) -> None:
    """P1-03: prévio com regulação acolchoada (espaços) casa como o classificador."""
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        agency_record_number="77710",
        patient_name="Maria da Silva",
    )
    _declare(current, "art_perif")
    decided_at = current.created_at - timedelta(days=3)
    prior = _make_case(
        owner_user,
        created_at=decided_at,
        agency_record_number="  77710  ",
        patient_name="Maria da Silva",
    )
    _declare(prior, "art_perif")
    _decide(prior, "art_perif", decided_at=decided_at)

    summary = prior_case_module.lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.prior_case_id == str(prior.case_id)
    assert summary.origin == "occurrence_number"


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_matches_tab_newline_padded_stored_number(owner_user: User) -> None:
    """P1-03: padding com tab/quebra de linha segue a semântica de ``strip``."""
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        agency_record_number="77711",
        patient_name="Maria da Silva",
    )
    _declare(current, "art_perif")
    decided_at = current.created_at - timedelta(days=2)
    prior = _make_case(
        owner_user,
        created_at=decided_at,
        agency_record_number="\t77711\n",
        patient_name="Maria da Silva",
    )
    _declare(prior, "art_perif")
    _decide(prior, "art_perif", decided_at=decided_at)

    summary = prior_case_module.lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.prior_case_id == str(prior.case_id)
    assert summary.origin == "occurrence_number"


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_matches_exact_stored_cns(owner_user: User) -> None:
    """Controle (verde imediato): CNS armazenado exato casa com origem CNS.

    Limite de representabilidade (decisão do dono, fix-2): ``Case.patient_cns``
    é varchar(15) e um CNS válido tem 15 dígitos — padding armazenado é
    irrepresentável no schema (o INSERT falha com ``DataError``; ver
    ``fix-2-red-b.log`` preservado). A normalização SQL do CNS usa a MESMA
    expressão da regulação (domínio representável idêntico ao ``strip`` do
    classificador); este controle prova ausência de over/under-match.
    """
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        patient_name="Maria da Silva",
        patient_cns=_CNS_A,
    )
    _declare(current, "art_perif")
    decided_at = current.created_at - timedelta(days=4)
    prior = _make_case(
        owner_user,
        created_at=decided_at,
        patient_name="Maria da Silva",
        patient_cns=_CNS_A,
    )
    _declare(prior, "art_perif")
    _decide(prior, "art_perif", decided_at=decided_at)

    with CaptureQueriesContext(connection) as queries:
        summary = prior_case_module.lookup_prior_case_context(current, "art_perif")

    assert summary is not None
    assert summary.prior_case_id == str(prior.case_id)
    assert summary.origin == "patient_cns"
    select_sql = " ".join(query["sql"] for query in queries.captured_queries)
    assert "doctor_decided_at" in select_sql


@_NEW_MODE
@pytest.mark.django_db
def test_cns_strip_parity_in_memory_without_persistence() -> None:
    """Controle (verde imediato): ``strip`` do classificador nos dois lados do CNS.

    Prova em memória (sem persistir valor irrepresentável) que sinais CNS com
    padding nos dois lados casam como forte com origem CNS — a paridade que o
    SQL normalizado espelha no domínio representável.
    """
    from apps.cases.reappearance import IdentitySignals, classify_pair

    current = IdentitySignals(
        name="Maria da Silva",
        record_number="",
        patient_cns=f"  {_CNS_A}\t",
        procedures=("art_perif",),
    )
    prior = IdentitySignals(
        name="Maria da Silva",
        record_number="",
        patient_cns=f"\n{_CNS_A}  ",
        procedures=("art_perif",),
    )

    classification = classify_pair(current, prior)

    assert classification.verdict == "strong"
    assert classification.origin == "patient_cns"


@_NEW_MODE
@pytest.mark.django_db
def test_new_mode_blank_stored_identity_still_no_match(owner_user: User) -> None:
    """Controle: identidade armazenada só com espaços NÃO casa (sem over-match)."""
    current = _make_case(
        owner_user,
        created_at=timezone.now(),
        agency_record_number="77712",
        patient_name="Maria da Silva",
    )
    _declare(current, "art_perif")
    decided_at = current.created_at - timedelta(days=1)
    prior = _make_case(
        owner_user,
        created_at=decided_at,
        agency_record_number="   ",
        patient_name="Maria da Silva",
    )
    _declare(prior, "art_perif")
    _decide(prior, "art_perif", decided_at=decided_at)

    assert prior_case_module.lookup_prior_case_context(current, "art_perif") is None
