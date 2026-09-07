"""Testes de locks/lease de concorrência por caso (change 03, slice 004, R1–R7).

Cobre os campos de lock no ``Case`` (R1), o claim atômico com
``select_for_update`` (R2 — concede token/lease, rejeita claim conflitante de
outro ator sem efeito e assume lease expirada com o evento ``CASE_LOCK_EXPIRED``
na trilha), o assert de posse (R3), release e renew só pelo portador do token
(R4/R5, com eventos), a varredura de leases vencidas (R6) e a concorrência real
(R7: duas claims em transações sobrepostas → exatamente uma vence). Cobre os 3
cenários da spec "Locks de concorrência por caso".

Regressões P1: contendores presos no ``select_for_update`` enquanto a lease
expira avaliam a posse com ``now`` fresco (computado após o row lock) — o claim
assume a lease vencida (EXPIRED + CLAIMED) e o renew conflita, sem renovação
para o passado por tempo velho.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast

import pytest
from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.locks import (
    CaseLock,
    CaseLockConflictError,
    assert_case_lock,
    claim_case_lock,
    expire_stale_locks,
    release_case_lock,
    renew_case_lock,
)
from apps.cases.models import ActorType, Case

DOCTOR_CONTEXT = "doctor_decision"
DOCTOR_ROLE = "doctor"
NIR_ROLE = "nir"

# Campos de lock/lease persistidos no Case (R1/D6).
_LOCK_FIELDS = (
    "locked_by",
    "locked_at",
    "locked_until",
    "lock_token",
    "lock_context",
    "lock_role",
)


def _create_case(created_by: User) -> Case:
    return Case.objects.create(created_by=created_by)


def _expire_lock(case: Case) -> None:
    """Envelhece a lease do caso para o passado (mantém os demais campos)."""
    Case.objects.filter(pk=case.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    case.refresh_from_db()


def _assert_lock_cleared(case: Case) -> None:
    case.refresh_from_db()
    assert case.locked_by is None
    assert case.locked_at is None
    assert case.locked_until is None
    assert case.lock_token is None
    assert case.lock_context == ""
    assert case.lock_role == ""


def _event_types(case: Case) -> list[str]:
    return [event.event_type for event in case.events.order_by("id")]


def _lease_seconds(case: Case) -> float:
    """Duração da lease gravada no caso (locked_until − locked_at, ambos setados)."""
    locked_until = case.locked_until
    locked_at = case.locked_at
    assert locked_until is not None
    assert locked_at is not None
    return (locked_until - locked_at).total_seconds()


# ── R1: campos de lock no Case ─────────────────────────────────────────────


def test_lock_fields_exist_and_indexed() -> None:
    """R1: campos de lock do ats-web presentes no Case com a semântica do
    design D6 (locked_until indexado; lock_token UUID; contexto/papel curtos)."""
    for name in _LOCK_FIELDS:
        field = Case._meta.get_field(name)
        assert field is not None

    locked_until_field = cast(Any, Case._meta.get_field("locked_until"))
    assert locked_until_field.db_index is True
    assert Case._meta.get_field("lock_context").max_length == 40
    assert Case._meta.get_field("lock_context").blank is True
    assert Case._meta.get_field("lock_role").max_length == 30
    assert Case._meta.get_field("lock_role").blank is True
    locked_by = Case._meta.get_field("locked_by")
    assert locked_by.null is True
    assert locked_by.blank is True


# ── R2: claim atômico ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_claim_grants_token_and_default_lease(nir_user: User, doctor_user: User) -> None:
    """R2/cenário spec: claim em caso livre concede token e lease de
    ``CASE_LOCK_LEASE_SECONDS`` (default 300s) com evento na trilha."""
    case = _create_case(nir_user)

    lock = claim_case_lock(case, user=doctor_user, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE)

    assert isinstance(lock, CaseLock)
    assert isinstance(lock.token, uuid.UUID)
    case.refresh_from_db()
    assert case.locked_by_id == doctor_user.pk
    assert case.lock_token == lock.token
    assert case.lock_context == DOCTOR_CONTEXT
    assert case.lock_role == DOCTOR_ROLE
    assert case.locked_at is not None
    assert case.locked_until == lock.locked_until
    # Lease = now + settings.CASE_LOCK_LEASE_SECONDS (ambos setados no claim).
    assert (case.locked_until - case.locked_at).total_seconds() == (
        settings.CASE_LOCK_LEASE_SECONDS
    )

    event = case.events.get(event_type=CaseEventType.CASE_LOCK_CLAIMED)
    assert event.actor == doctor_user
    assert event.actor_type == ActorType.USER
    assert event.actor_role == DOCTOR_ROLE
    assert event.payload == {
        "context": DOCTOR_CONTEXT,
        "role": DOCTOR_ROLE,
        "lease_seconds": settings.CASE_LOCK_LEASE_SECONDS,
    }


@pytest.mark.django_db
def test_claim_honors_lease_override(nir_user: User, doctor_user: User) -> None:
    """R2: lease_seconds explícito substitui o default das settings."""
    case = _create_case(nir_user)

    lock = claim_case_lock(
        case,
        user=doctor_user,
        context=DOCTOR_CONTEXT,
        role=DOCTOR_ROLE,
        lease_seconds=45,
    )

    case.refresh_from_db()
    assert case.lock_token == lock.token
    assert _lease_seconds(case) == 45
    event = case.events.get(event_type=CaseEventType.CASE_LOCK_CLAIMED)
    assert event.payload["lease_seconds"] == 45


@pytest.mark.django_db
def test_conflicting_claim_rejected_and_lock_intact(nir_user: User, doctor_user: User) -> None:
    """R2/cenário spec: claim conflitante de outro ator sobre lock ativo é
    negado com erro explícito (dono/contexto) sem efeito — o lock original e a
    trilha permanecem intactos."""
    case = _create_case(nir_user)
    first = claim_case_lock(case, user=doctor_user, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE)
    events_before = case.events.count()

    with pytest.raises(CaseLockConflictError) as excinfo:
        claim_case_lock(case, user=nir_user, context=DOCTOR_CONTEXT, role=NIR_ROLE)

    message = str(excinfo.value)
    assert doctor_user.display_name in message
    assert DOCTOR_CONTEXT in message

    case.refresh_from_db()
    assert case.locked_by_id == doctor_user.pk
    assert case.lock_token == first.token
    assert case.locked_until == first.locked_until
    assert case.lock_context == DOCTOR_CONTEXT
    assert case.events.count() == events_before


@pytest.mark.django_db
def test_reclaim_by_same_actor_renews_claim(nir_user: User, doctor_user: User) -> None:
    """R2: o mesmo ator reivindicando lock ainda ativo renova a posse com novo
    token (padrão ats-web), gravando novo CASE_LOCK_CLAIMED."""
    case = _create_case(nir_user)
    first = claim_case_lock(case, user=doctor_user, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE)

    second = claim_case_lock(case, user=doctor_user, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE)

    assert second.token != first.token
    case.refresh_from_db()
    assert case.locked_by_id == doctor_user.pk
    assert case.lock_token == second.token
    claimed_events = case.events.filter(event_type=CaseEventType.CASE_LOCK_CLAIMED)
    assert claimed_events.count() == 2


@pytest.mark.django_db
def test_expired_lease_taken_over_with_event(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R2/cenário spec: lease expirada é assumida por novo claim com novo token
    e o evento CASE_LOCK_EXPIRED (com o dono anterior) fica na trilha antes do
    CASE_LOCK_CLAIMED."""
    other_doctor = user_factory("doctor-2", DOCTOR_ROLE)
    case = _create_case(nir_user)
    first = claim_case_lock(
        case,
        user=doctor_user,
        context=DOCTOR_CONTEXT,
        role=DOCTOR_ROLE,
        lease_seconds=60,
    )
    _expire_lock(case)
    expired_until = case.locked_until
    assert expired_until is not None

    second = claim_case_lock(case, user=other_doctor, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE)

    assert second.token != first.token
    case.refresh_from_db()
    assert case.locked_by_id == other_doctor.pk
    assert case.lock_token == second.token
    # Lease nova: volta ao default das settings (lease reiniciada no takeover).
    assert _lease_seconds(case) == settings.CASE_LOCK_LEASE_SECONDS

    assert _event_types(case)[-2:] == [
        CaseEventType.CASE_LOCK_EXPIRED,
        CaseEventType.CASE_LOCK_CLAIMED,
    ]
    expired_event = case.events.filter(event_type=CaseEventType.CASE_LOCK_EXPIRED).last()
    assert expired_event is not None
    assert expired_event.payload["expired_locked_by_display"] == (doctor_user.display_name)
    assert expired_event.payload["expired_locked_until"] == expired_until.isoformat()


# ── R3: assert de posse ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_assert_requires_valid_token(nir_user: User, doctor_user: User) -> None:
    """R3: assert valida posse pelo token — token divergente, caso sem lock e
    lease expirada conflitam; token do portador passa sem efeito."""
    case = _create_case(nir_user)
    lock = claim_case_lock(case, user=doctor_user, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE)
    case.refresh_from_db()

    assert_case_lock(case, lock.token)  # posse válida → ok (None)

    with pytest.raises(CaseLockConflictError, match="token"):
        assert_case_lock(case, uuid.uuid4())

    _expire_lock(case)
    with pytest.raises(CaseLockConflictError, match="expi"):
        assert_case_lock(case, lock.token)

    free_case = _create_case(nir_user)
    with pytest.raises(CaseLockConflictError, match="lock"):
        assert_case_lock(free_case, uuid.uuid4())


# ── R4/R5: release e renew só pelo portador ────────────────────────────────


@pytest.mark.django_db
def test_release_by_token_holder_clears_and_events(nir_user: User, doctor_user: User) -> None:
    """R4: o portador libera o caso (campos limpos) com CASE_LOCK_RELEASED na
    trilha registrando o portador."""
    case = _create_case(nir_user)
    claim_case_lock(case, user=doctor_user, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE)
    case.refresh_from_db()
    token = case.lock_token
    assert token is not None

    release_case_lock(case, token)

    _assert_lock_cleared(case)
    released = case.events.get(event_type=CaseEventType.CASE_LOCK_RELEASED)
    assert released.actor == doctor_user
    assert released.actor_type == ActorType.USER
    assert released.actor_role == DOCTOR_ROLE
    assert released.payload == {"context": DOCTOR_CONTEXT}


@pytest.mark.django_db
def test_release_wrong_token_conflicts_without_effect(nir_user: User, doctor_user: User) -> None:
    """R4: release com token errado conflita e não altera o lock nem a trilha."""
    case = _create_case(nir_user)
    first = claim_case_lock(case, user=doctor_user, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE)
    events_before = case.events.count()

    with pytest.raises(CaseLockConflictError, match="token"):
        release_case_lock(case, uuid.uuid4())

    case.refresh_from_db()
    assert case.lock_token == first.token
    assert case.locked_by_id == doctor_user.pk
    assert case.events.count() == events_before


@pytest.mark.django_db
def test_renew_extends_lease_with_event(nir_user: User, doctor_user: User) -> None:
    """R5: o portador renova a lease (mesmo token, locked_until estendido) com
    CASE_LOCK_RENEWED na trilha."""
    case = _create_case(nir_user)
    claim_case_lock(
        case,
        user=doctor_user,
        context=DOCTOR_CONTEXT,
        role=DOCTOR_ROLE,
        lease_seconds=60,
    )
    case.refresh_from_db()
    token = case.lock_token
    assert token is not None
    original_until = case.locked_until
    assert original_until is not None

    renewed = renew_case_lock(case, token)

    assert renewed.token == token
    case.refresh_from_db()
    assert case.lock_token == token
    assert case.locked_by_id == doctor_user.pk
    assert case.locked_until == renewed.locked_until
    assert case.locked_until > original_until
    assert _lease_seconds(case) == settings.CASE_LOCK_LEASE_SECONDS

    renewed_event = case.events.get(event_type=CaseEventType.CASE_LOCK_RENEWED)
    assert renewed_event.actor == doctor_user
    assert renewed_event.actor_role == DOCTOR_ROLE
    assert renewed_event.payload["lease_seconds"] == settings.CASE_LOCK_LEASE_SECONDS


@pytest.mark.django_db
def test_renew_wrong_token_conflicts_without_effect(nir_user: User, doctor_user: User) -> None:
    """R5: renew com token errado conflita sem estender a lease nem gravar
    evento."""
    case = _create_case(nir_user)
    first = claim_case_lock(case, user=doctor_user, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE)
    events_before = case.events.count()

    with pytest.raises(CaseLockConflictError, match="token"):
        renew_case_lock(case, uuid.uuid4())

    case.refresh_from_db()
    assert case.lock_token == first.token
    assert case.locked_until == first.locked_until
    assert case.events.count() == events_before


# ── R6: varredura de leases vencidas ───────────────────────────────────────


@pytest.mark.django_db
def test_expire_stale_sweep(nir_user: User, doctor_user: User) -> None:
    """R6: expire_stale_locks() varre e limpa só leases vencidas, gravando
    CASE_LOCK_EXPIRED (sistema) para cada uma; lock ativo e caso livre não
    são tocados."""
    stale_case = _create_case(nir_user)
    claim_case_lock(stale_case, user=doctor_user, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE)
    _expire_lock(stale_case)
    stale_until = stale_case.locked_until
    assert stale_until is not None

    active_case = _create_case(nir_user)
    active = claim_case_lock(
        active_case, user=doctor_user, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE
    )
    free_case = _create_case(nir_user)

    cleared = expire_stale_locks()

    assert cleared == 1
    _assert_lock_cleared(stale_case)
    expired = stale_case.events.get(event_type=CaseEventType.CASE_LOCK_EXPIRED)
    assert expired.actor is None
    assert expired.actor_type == ActorType.SYSTEM
    assert expired.payload["expired_locked_until"] == stale_until.isoformat()
    assert _event_types(stale_case) == [
        CaseEventType.CASE_LOCK_CLAIMED,
        CaseEventType.CASE_LOCK_EXPIRED,
    ]

    active_case.refresh_from_db()
    assert active_case.lock_token == active.token
    assert active_case.locked_by_id == doctor_user.pk

    free_case.refresh_from_db()
    assert free_case.lock_token is None


def _run_contender(
    entered: threading.Event,
    finished: threading.Event,
    act: Callable[[], None],
) -> threading.Thread:
    """Thread contendora: sinaliza ``entered`` ao entrar e ``finished`` ao sair
    (sucesso, conflito ou erro).
    """

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


# Janela (em segundos) em que a thread principal mantém o row lock do case com
# as contenders já presas no select_for_update: sobreposição determinística
# (R7) e, nas regressões P1, garantia de que o `now` velho da contender foi
# computado antes de a lease ser envelhecida.
_CONTEND_GRACE_SECONDS = 0.5


# ── R7: concorrência real ──────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_concurrent_claims_exactly_one_wins(
    nir_user: User, user_factory: Callable[[str, str], User]
) -> None:
    """R7: duas claims em transações sobrepostas no mesmo case — exatamente uma
    vence e a outra recebe CaseLockConflictError.

    Contenção determinística (não depende de scheduling): a thread principal
    segura o row lock do case (``select_for_update``) e só o libera no commit
    depois que as duas claims sinalizaram entrada e seguem vivas (presas na
    query) durante ``_CONTEND_GRACE_SECONDS``. Liberada a linha, a fila de locks
    do PostgreSQL serializa a disputa — a segunda claim re-lê a linha após a
    primeira conceder e conflita. Implementação sem ``select_for_update`` não
    travaria e o teste falharia nos asserts de espera/tempo.
    """
    case = _create_case(nir_user)
    doctor_a = user_factory("doctor-a", DOCTOR_ROLE)
    doctor_b = user_factory("doctor-b", DOCTOR_ROLE)

    winners: list[uuid.UUID] = []
    conflicts: list[CaseLockConflictError] = []
    elapsed: list[float] = []

    def _claim(user: User) -> None:
        started = time.monotonic()
        try:
            with transaction.atomic():
                lock = claim_case_lock(case, user=user, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE)
                elapsed.append(time.monotonic() - started)
            winners.append(lock.token)
        except CaseLockConflictError as exc:
            elapsed.append(time.monotonic() - started)
            conflicts.append(exc)

    entered_a = threading.Event()
    entered_b = threading.Event()
    finished_a = threading.Event()
    finished_b = threading.Event()
    with transaction.atomic():
        # Row lock do case segurado: as claims chegam e ficam presas no
        # select_for_update até o commit (saída do bloco) liberar a linha.
        Case.objects.select_for_update().get(pk=case.pk)
        thread_a = _run_contender(entered_a, finished_a, lambda: _claim(doctor_a))
        thread_b = _run_contender(entered_b, finished_b, lambda: _claim(doctor_b))
        assert entered_a.wait(timeout=10)
        assert entered_b.wait(timeout=10)
        time.sleep(_CONTEND_GRACE_SECONDS)
        # Ambas seguem vivas: presas no row lock do case durante a espera.
        assert not finished_a.is_set()
        assert not finished_b.is_set()
    for thread in (thread_a, thread_b):
        thread.join(timeout=60)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert len(winners) == 1
    assert len(conflicts) == 1
    # A disputa foi real: cada claim ficou bloqueada no row lock do case.
    assert len(elapsed) == 2
    assert all(spent >= _CONTEND_GRACE_SECONDS / 2 for spent in elapsed)

    case.refresh_from_db()
    assert case.locked_by_id in {doctor_a.pk, doctor_b.pk}
    assert case.lock_token == winners[0]


@pytest.mark.django_db(transaction=True)
def test_claim_takes_over_lease_expired_while_waiting_on_row_lock(
    nir_user: User,
    doctor_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """P1/regressão: claim que espera no row lock avalia a lease com ``now``
    fresco — nunca com o instante anterior ao lock.

    A thread principal segura o row lock do caso; a claim da outra thread entra
    e fica presa no ``select_for_update``. Só então a lease é envelhecida para
    um passado posterior ao ``now`` que a claim teria computado ao entrar:
    liberada a linha, a claim vê a lease JÁ expirada e assume o caso
    (EXPIRED + CLAIMED na trilha), em vez de conflitar por tempo velho.
    """
    other_doctor = user_factory("doctor-2", DOCTOR_ROLE)
    case = _create_case(nir_user)
    holder = claim_case_lock(
        case,
        user=doctor_user,
        context=DOCTOR_CONTEXT,
        role=DOCTOR_ROLE,
        lease_seconds=60,
    )

    claims: list[CaseLock] = []
    conflicts: list[CaseLockConflictError] = []

    def _claim() -> None:
        try:
            with transaction.atomic():
                claims.append(
                    claim_case_lock(
                        case, user=other_doctor, context=DOCTOR_CONTEXT, role=DOCTOR_ROLE
                    )
                )
        except CaseLockConflictError as exc:
            conflicts.append(exc)

    entered = threading.Event()
    finished = threading.Event()
    with transaction.atomic():
        # Row lock do caso segurado; a claim da outra thread trava aqui.
        Case.objects.select_for_update().get(pk=case.pk)
        thread = _run_contender(entered, finished, _claim)
        assert entered.wait(timeout=10)
        time.sleep(_CONTEND_GRACE_SECONDS)
        # Claim segue viva: presa no row lock (sem select_for_update ela teria
        # terminado e o assert falharia).
        assert not finished.is_set()
        # Envelhece a lease para um passado POSTERIOR ao `now` velho da claim:
        # quando a linha for liberada ela já estará vencida na prática.
        expired_until = timezone.now() - timedelta(seconds=0.1)
        Case.objects.filter(pk=case.pk).update(locked_until=expired_until)
        # Commit ao sair do bloco: libera o row lock com a lease já vencida.
    thread.join(timeout=60)

    assert not thread.is_alive()
    assert not conflicts
    assert len(claims) == 1
    taken = claims[0]
    assert taken.token != holder.token
    case.refresh_from_db()
    assert case.locked_by_id == other_doctor.pk
    assert case.lock_token == taken.token
    # A lease nova nasceu da avaliação pós-espera: nunca no passado.
    assert case.locked_at is not None
    assert case.locked_until is not None
    assert case.locked_until > timezone.now()
    assert (case.locked_until - case.locked_at).total_seconds() == (
        settings.CASE_LOCK_LEASE_SECONDS
    )
    assert _event_types(case)[-2:] == [
        CaseEventType.CASE_LOCK_EXPIRED,
        CaseEventType.CASE_LOCK_CLAIMED,
    ]
    expired_event = case.events.filter(event_type=CaseEventType.CASE_LOCK_EXPIRED).last()
    assert expired_event is not None
    assert expired_event.payload["expired_locked_by_display"] == doctor_user.display_name


@pytest.mark.django_db(transaction=True)
def test_renew_conflicts_on_lease_expired_while_waiting_on_row_lock(
    nir_user: User,
    doctor_user: User,
) -> None:
    """P1/regressão: renew que espera no row lock avalia com ``now`` fresco e
    nunca renova lease que expirou durante a espera.

    Mesma contenção do teste de claim: a renew entra, fica presa no
    ``select_for_update`` e só então a lease é envelhecida; liberada a linha, a
    renew conflita (posse perdida) e a lease permanece vencida, sem extensão
    para o passado.
    """
    case = _create_case(nir_user)
    holder = claim_case_lock(
        case,
        user=doctor_user,
        context=DOCTOR_CONTEXT,
        role=DOCTOR_ROLE,
        lease_seconds=60,
    )
    case.refresh_from_db()
    events_before = case.events.count()

    renewed: list[CaseLock] = []
    conflicts: list[CaseLockConflictError] = []

    def _renew() -> None:
        try:
            with transaction.atomic():
                renewed.append(renew_case_lock(case, holder.token))
        except CaseLockConflictError as exc:
            conflicts.append(exc)

    entered = threading.Event()
    finished = threading.Event()
    with transaction.atomic():
        Case.objects.select_for_update().get(pk=case.pk)
        thread = _run_contender(entered, finished, _renew)
        assert entered.wait(timeout=10)
        time.sleep(_CONTEND_GRACE_SECONDS)
        assert not finished.is_set()
        expired_until = timezone.now() - timedelta(seconds=0.1)
        Case.objects.filter(pk=case.pk).update(locked_until=expired_until)
    thread.join(timeout=60)

    assert not thread.is_alive()
    assert not renewed
    assert len(conflicts) == 1
    assert "expi" in str(conflicts[0])
    case.refresh_from_db()
    # Nada foi renovado: a lease segue vencida, com o mesmo token e a mesma trilha.
    assert case.lock_token == holder.token
    assert case.locked_by_id == doctor_user.pk
    assert case.locked_until is not None
    assert case.locked_until < timezone.now()
    assert case.events.count() == events_before
