"""Locks/lease de exclusividade de mutação por caso (change 03, slice 004, D6).

Mecanismo espelhado do ats-web ``apps/cases/services.py`` com as divergências
deliberadas do HMD (documentadas no design D6): os serviços vivem aqui em
``apps/cases/locks.py``; ``renew_case_lock`` grava evento ``CASE_LOCK_RENEWED``
e ``expire_stale_locks()`` é genérica (o ats-web tem
``expire_stale_locks_for_statuses``, sem eventos e com variantes por status);
sem variantes de lease por papel/contexto — ``CASE_LOCK_LEASE_SECONDS``
(default 300s) vale para todos os atores.

Exclusividade: ``claim_case_lock`` roda em ``transaction.atomic()`` com
``select_for_update`` no case — dois claims sobrepostos disputam a linha e
exatamente um vence. Lock ativo de outro ator é rejeitado com
``CaseLockConflictError`` sem efeito; lease expirada é assumida por novo claim com o
evento ``CASE_LOCK_EXPIRED`` na trilha. ``assert_case_lock`` é o contrato de
posse que os consumers ligam nas mutações dos seus fluxos nos changes 04+
("mutação sob lock exige o token"); release/renew operam pela posse do token
(R4/R5).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent

if TYPE_CHECKING:
    from apps.accounts.models import User


class CaseLockConflictError(Exception):
    """Operação de lock rejeitada sem efeito.

    Cobre os casos do contrato: claim sobre lock ativo de outro ator (com
    dono/contexto na mensagem), assert/release/renew com token divergente,
    sem lock ou com lease já expirada (posse perdida).
    """


@dataclass(frozen=True)
class CaseLock:
    """Token e vencimento da lease concedida/renovada (claim/renew)."""

    token: uuid.UUID
    locked_until: datetime


_LOCK_FIELDS = (
    "locked_by",
    "locked_at",
    "locked_until",
    "lock_token",
    "lock_context",
    "lock_role",
)


def _default_lease_seconds(lease_seconds: int | None) -> int:
    """Resolve a duração da lease: override explícito ou default das settings."""
    if lease_seconds is not None:
        return lease_seconds
    return int(getattr(settings, "CASE_LOCK_LEASE_SECONDS", 300))


def _is_active(case: Case, now: datetime) -> bool:
    """Lock ativo = há vencimento no futuro (posse vigente, não assumível)."""
    return case.locked_until is not None and case.locked_until > now


def _record_lock_event(
    case: Case,
    event_type: str,
    *,
    user: User | None,
    role: str | None,
    payload: dict[str, object],
) -> None:
    """Grava o evento de lock na trilha com ator/papel (design D5)."""
    CaseEvent.objects.create(
        case=case,
        event_type=event_type,
        actor_type=ActorType.USER if user is not None else ActorType.SYSTEM,
        actor=user,
        actor_role=role or "",
        payload=payload,
    )


def _clear_lock_fields(case: Case) -> None:
    """Limpa os seis campos de lock do caso (release/expiração)."""
    case.locked_by = None
    case.locked_at = None
    case.locked_until = None
    case.lock_token = None
    case.lock_context = ""
    case.lock_role = ""
    case.save(update_fields=_LOCK_FIELDS)


def _assert_possession(case: Case, token: uuid.UUID, now: datetime) -> None:
    """Valida posse vigente pelo token (assert/release/renew).

    ``CaseLockConflictError`` quando não há lock, o token não confere com o
    portador atual ou a lease já expirou — nos dois últimos casos a operação
    não tem efeito algum.
    """
    if case.lock_token is None:
        raise CaseLockConflictError("caso não está sob lock")
    if case.lock_token != token:
        raise CaseLockConflictError("token de lock não confere com o portador atual")
    if case.locked_until is None or case.locked_until <= now:
        raise CaseLockConflictError("lease do lock expirou — refaça o claim para assumir o caso")


def _active_lock_message(case: Case) -> str:
    """Mensagem do conflito de claim sobre lock ativo (dono + contexto)."""
    owner = case.locked_by.display_name if case.locked_by is not None else "sistema"
    context = case.lock_context or "(sem contexto)"
    until = case.locked_until.isoformat() if case.locked_until is not None else "?"
    return f"caso {case.case_id} está sob lock ativo de {owner} (contexto {context!r}) até {until}"


def case_has_lock(case: Case) -> bool:
    """Há lock operacional persistido no caso (portador, posse/lease ou contexto).

    Mesma definição usada pelo snapshot do encerramento administrativo
    (``apps/cases/closure.py::_lock_snapshot``): sem nenhum desses campos o
    force-release é no-op e NENHUM evento de release é gravado.
    """
    return (
        case.locked_by_id is not None
        or case.locked_until is not None
        or case.lock_token is not None
        or case.lock_context != ""
    )


def _expired_payload(case: Case, *, context: str, role: str) -> dict[str, object]:
    """Payload do CASE_LOCK_EXPIRED com os dados do portador expirado."""
    return {
        "context": context,
        "role": role,
        "expired_locked_by_id": str(case.locked_by_id) if case.locked_by_id is not None else None,
        "expired_locked_by_display": case.locked_by.display_name
        if case.locked_by is not None
        else "",
        "expired_locked_at": case.locked_at.isoformat() if case.locked_at is not None else None,
        "expired_locked_until": case.locked_until.isoformat()
        if case.locked_until is not None
        else None,
    }


# ── R2: claim atômico ──────────────────────────────────────────────────────


def claim_case_lock(
    case: Case,
    *,
    user: User | None,
    context: str,
    role: str | None = None,
    lease_seconds: int | None = None,
) -> CaseLock:
    """Reivindica a exclusividade de mutação do caso (R2).

    Dentro de ``transaction.atomic()`` com ``select_for_update`` no case:
    caso livre é concedido; caso com lease expirada é assumido (o evento
    ``CASE_LOCK_EXPIRED`` com o portador anterior fica na trilha antes do
    ``CASE_LOCK_CLAIMED``); lock ativo do mesmo ator renova a posse com novo
    token (padrão ats-web). Em todos os casos de sucesso: novo ``lock_token``,
    ``locked_until = now + lease`` (default ``CASE_LOCK_LEASE_SECONDS``, 300s)
    e evento ``CASE_LOCK_CLAIMED``. Lock ativo de outro ator →
    ``CaseLockConflictError`` (dono/contexto na mensagem) sem alterar nada.
    """
    seconds = _default_lease_seconds(lease_seconds)
    token = uuid.uuid4()

    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        # now/locked_until são derivados APÓS o row lock (time-of-check ==
        # time-of-use): se a chamada esperou no select_for_update até a lease
        # expirar, a avaliação usa tempo fresco — nunca o instante anterior à
        # aquisição da linha.
        now = timezone.now()
        locked_until = now + timedelta(seconds=seconds)

        if _is_active(locked, now):
            if user is not None and locked.locked_by_id == user.pk:
                # Mesmo ator reivindicando: renova a posse (padrão ats-web).
                locked.locked_at = now
                locked.locked_until = locked_until
                locked.lock_token = token
                locked.lock_context = context
                locked.lock_role = role or ""
                locked.save(update_fields=_LOCK_FIELDS)
                _record_lock_event(
                    locked,
                    event_type=CaseEventType.CASE_LOCK_CLAIMED,
                    user=user,
                    role=role,
                    payload={"context": context, "role": role or "", "lease_seconds": seconds},
                )
                return CaseLock(token=token, locked_until=locked_until)
            raise CaseLockConflictError(_active_lock_message(locked))

        # Livre ou com lease vencida: grava a expiração quando havia portador.
        if locked.locked_until is not None:
            _record_lock_event(
                locked,
                event_type=CaseEventType.CASE_LOCK_EXPIRED,
                user=user,
                role=role,
                payload=_expired_payload(locked, context=context, role=role or ""),
            )

        locked.locked_by = user
        locked.locked_at = now
        locked.locked_until = locked_until
        locked.lock_token = token
        locked.lock_context = context
        locked.lock_role = role or ""
        locked.save(update_fields=_LOCK_FIELDS)
        _record_lock_event(
            locked,
            event_type=CaseEventType.CASE_LOCK_CLAIMED,
            user=user,
            role=role,
            payload={"context": context, "role": role or "", "lease_seconds": seconds},
        )

    return CaseLock(token=token, locked_until=locked_until)


# ── R3: assert de posse ────────────────────────────────────────────────────


def assert_case_lock(case: Case, token: uuid.UUID) -> None:
    """Valida que o chamador possui o lock vigente do caso (R3).

    Contrato de posse usado pelos serviços de mutação dos fluxos que operam
    sob lock (consumers ligam a checagem nos changes 04+). Token divergente,
    caso sem lock ou lease expirada → ``CaseLockConflictError``; posse válida → ok.
    """
    _assert_possession(case, token, timezone.now())


# ── R4: release ────────────────────────────────────────────────────────────


def release_case_lock(case: Case, token: uuid.UUID) -> None:
    """Libera a lease do caso (R4): só o portador do token libera.

    Limpa os campos de lock e grava ``CASE_LOCK_RELEASED`` na trilha com o
    portador registrado na concessão. Token divergente, caso sem lock ou lease
    expirada → ``CaseLockConflictError`` sem efeito.
    """
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        _assert_possession(locked, token, timezone.now())
        holder = locked.locked_by
        holder_role = locked.lock_role
        context = locked.lock_context
        _record_lock_event(
            locked,
            event_type=CaseEventType.CASE_LOCK_RELEASED,
            user=holder,
            role=holder_role,
            payload={"context": context},
        )
        _clear_lock_fields(locked)


# ── R4 (encerramento administrativo): release forçado ──────────────────────


def force_release_case_lock(
    case: Case,
    *,
    reason: str,
    user: User | None,
    role: str | None = None,
) -> None:
    """Libera o lock do caso à força, com o autor na trilha (R4 — encerramento
    administrativo).

    Espelha ``release_case_lock`` (re-lê a linha sob ``select_for_update``), mas
    NÃO exige token: quem encerra o caso pode não ser o portador da lease (lock
    travado/expirado é o caso de uso). No mesmo ``atomic``: grava
    ``CASE_LOCK_RELEASED`` com o payload de força — motivo, ``forced``, o
    snapshot do portador anterior (contexto/vencimento) e o autor do
    encerramento — e limpa os seis campos de lock, COPIANDO o estado limpo de
    volta para a instância do chamador (o ``save()`` full da transição seguinte
    não ressuscita o lock). Caso SEM lock persistido é no-op idempotente, sem
    evento: o ``CASE_LOCK_RELEASED`` só existe quando havia lock.
    """
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        if not case_has_lock(locked):
            return
        previous_context = locked.lock_context
        previous_until = locked.locked_until
        _record_lock_event(
            locked,
            event_type=CaseEventType.CASE_LOCK_RELEASED,
            user=user,
            role=role,
            payload={
                "reason": reason,
                "forced": True,
                "previous_lock_context": previous_context,
                "previous_lock_until": previous_until.isoformat()
                if previous_until is not None
                else None,
                "by": user.username if user is not None else "",
                "role": role or "",
            },
        )
        _clear_lock_fields(locked)
        for field_name in _LOCK_FIELDS:
            setattr(case, field_name, getattr(locked, field_name))


# ── R5: renew ──────────────────────────────────────────────────────────────


def renew_case_lock(
    case: Case,
    token: uuid.UUID,
    lease_seconds: int | None = None,
) -> CaseLock:
    """Renova a lease do caso (R5): o portador estende ``locked_until``.

    Mesmo token, ``locked_until = now + lease`` (default das settings ou
    ``lease_seconds``) e evento ``CASE_LOCK_RENEWED`` na trilha. Token
    divergente, caso sem lock ou lease expirada → ``CaseLockConflictError`` sem
    efeito (nesses casos o fluxo deve refazer o claim).
    """
    seconds = _default_lease_seconds(lease_seconds)

    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        # now/locked_until APÓS o row lock (mesmo critério do claim): esperar no
        # select_for_update não congela o relógio da avaliação de expiração.
        now = timezone.now()
        locked_until = now + timedelta(seconds=seconds)
        _assert_possession(locked, token, now)
        locked.locked_at = now
        locked.locked_until = locked_until
        locked.save(update_fields=["locked_at", "locked_until"])
        _record_lock_event(
            locked,
            event_type=CaseEventType.CASE_LOCK_RENEWED,
            user=locked.locked_by,
            role=locked.lock_role,
            payload={
                "context": locked.lock_context,
                "role": locked.lock_role,
                "lease_seconds": seconds,
            },
        )

    return CaseLock(token=token, locked_until=locked_until)


# ── R6: varredura de leases vencidas ───────────────────────────────────────


def expire_stale_locks() -> int:
    """Varre e libera leases vencidas (R6).

    Para cada caso com ``locked_until < now``: limpa os campos de lock e grava
    ``CASE_LOCK_EXPIRED`` (ator sistema, com o portador expirado no payload).
    Retorna quantas leases foram expiradas. Executável por chamada agendada
    futura — neste change só função + teste (sem schedule).
    """
    now = timezone.now()
    cleared = 0
    with transaction.atomic():
        stale_cases = list(Case.objects.select_for_update().filter(locked_until__lt=now))
        for case in stale_cases:
            context = case.lock_context
            role = case.lock_role
            _record_lock_event(
                case,
                event_type=CaseEventType.CASE_LOCK_EXPIRED,
                user=None,
                role=None,
                payload=_expired_payload(case, context=context, role=role),
            )
            _clear_lock_fields(case)
            cleared += 1
    return cleared
