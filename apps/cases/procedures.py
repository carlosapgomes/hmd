"""Procedimentos por caso — serviços atômicos (change 03, slice 003, D2/D5).

Espelha o padrão do ats-web ``apps/cases/procedures.py`` com as divergências
deliberadas do HMD: as assinaturas recebem ``user``/``role`` explícitos (papel
ativo registrado na trilha, D5) e usam dicts tipados; ``doctor_decided_at`` é
acréscimo HMD (o ats-web não grava quando o médico decidiu);
``reset_detection_and_doctor_statuses`` NÃO entra aqui (change 04,
reprocessamento).

``CaseProcedure`` é a fonte autoritativa do conjunto de procedimentos por caso
(uma row por (case, type)); views/workers nunca escrevem rows direto — os
writes passam por este módulo e são atômicos: falha no meio = zero efeito,
tipo fora do catálogo nunca persiste. A ordem canônica de exibição/auditoria é
a ordem do registro do catálogo (``PROCEDURE_PROFILES``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.cases.events import CaseEventType
from apps.cases.models import (
    ActorType,
    Case,
    CaseEvent,
    CaseProcedure,
    DetectionStatus,
    DoctorDisposition,
)
from apps.cases.procedure_catalog import PROCEDURE_PROFILES

if TYPE_CHECKING:
    from apps.accounts.models import User

# Ordem canônica: índice do tipo no registro do catálogo (R5).
_CATALOG_ORDER: dict[str, int] = {
    profile.procedure_type: index for index, profile in enumerate(PROCEDURE_PROFILES)
}

_VALID_DETECTION_STATUSES = frozenset(
    {DetectionStatus.DETECTED.value, DetectionStatus.NOT_DETECTED.value}
)
_VALID_DOCTOR_DISPOSITIONS = frozenset(
    {DoctorDisposition.APPROVED.value, DoctorDisposition.DENIED.value}
)


def _canonical_order(procedure_types: Iterable[str]) -> tuple[str, ...]:
    """Ordena um conjunto de tipos na ordem do catálogo (sem duplicatas)."""
    return tuple(sorted(set(procedure_types), key=_CATALOG_ORDER.__getitem__))


def _validate_catalog_type(procedure_type: str) -> None:
    """Fail-fast: tipo fora do catálogo é rejeitado nomeando o tipo (R1/R2)."""
    if procedure_type not in _CATALOG_ORDER:
        raise ValueError(f"procedimento fora do catálogo: {procedure_type!r}")


def _normalize_declared_types(procedure_types: Iterable[str]) -> tuple[str, ...]:
    """Valida e ordena o conjunto declarado (R2): não-vazio e só tipos do catálogo."""
    declared = tuple(procedure_types)
    if not declared:
        raise ValueError("declare ao menos um procedimento")
    for procedure_type in declared:
        _validate_catalog_type(procedure_type)
    return _canonical_order(declared)


def _create_procedure_event(
    case: Case,
    *,
    event_type: str,
    payload: dict[str, object],
    user: User | None,
    role: str | None,
) -> None:
    """Grava o evento de operação de procedimento (R5/D5) com ator/papel."""
    CaseEvent.objects.create(
        case=case,
        event_type=event_type,
        actor_type=ActorType.USER if user is not None else ActorType.SYSTEM,
        actor=user,
        actor_role=role or "",
        payload=payload,
    )


# ── R2: declaração do NIR ──────────────────────────────────────────────────


def set_declared_procedures(
    case: Case,
    procedure_types: Iterable[str],
    *,
    user: User | None,
    role: str | None,
) -> None:
    """Define o conjunto declarado do caso atomicamente (R2).

    Cria/ativa as rows declaradas (``declared_by_nir=True``) e desativa as demais
    rows do caso — sem deletar rows (a transformação permanece auditável) nem
    criar duplicatas. Tipo fora do catálogo (ou conjunto vazio) falha inteiro
    antes de qualquer escrita, nomeando o tipo. Grava o evento enxuto
    ``CASE_PROCEDURES_DECLARED`` com o conjunto ordenado.
    """
    declared = _normalize_declared_types(procedure_types)
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        for procedure_type in declared:
            row, created = CaseProcedure.objects.get_or_create(
                case=locked,
                procedure_type=procedure_type,
                defaults={"declared_by_nir": True},
            )
            if created or not row.declared_by_nir:
                row.declared_by_nir = True
                row.save(update_fields=["declared_by_nir"])
        CaseProcedure.objects.filter(case=locked).exclude(procedure_type__in=declared).update(
            declared_by_nir=False
        )
        _create_procedure_event(
            locked,
            event_type=CaseEventType.CASE_PROCEDURES_DECLARED,
            payload={"procedure_types": list(declared)},
            user=user,
            role=role,
        )


# ── R3: detecção do pipeline ───────────────────────────────────────────────


def set_detected_procedures(
    case: Case,
    detection: Mapping[str, str],
    *,
    user: User | None,
    role: str | None,
) -> None:
    """Registra a detecção do pipeline atomicamente (R3).

    Atualiza ``detection_status`` apenas de rows existentes do caso — tipo sem
    row é erro explícito (o pipeline deve declarar/reconciliar antes) e tipo
    fora do catálogo é rejeitado nomeando o tipo mesmo quando a row existe
    (defesa contra rows plantadas fora da ``clean()``). Valor fora de
    {detected, not_detected} é erro. Grava o evento
    ``CASE_PROCEDURES_DETECTED`` com o mapa de detecção no payload com as
    chaves na ordem canônica do catálogo (R5), independente da ordem recebida.
    """
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        if not detection:
            raise ValueError("a detecção deve trazer ao menos um tipo (detected/not_detected)")
        for procedure_type, status in detection.items():
            _validate_catalog_type(procedure_type)
            if not CaseProcedure.objects.filter(
                case=locked, procedure_type=procedure_type
            ).exists():
                raise ValueError(
                    f"procedimento sem row no caso — declare/reconcilie antes: {procedure_type!r}"
                )
            if status not in _VALID_DETECTION_STATUSES:
                raise ValueError(f"status de detecção inválido para {procedure_type!r}: {status!r}")
        ordered_detection = {
            procedure_type: detection[procedure_type]
            for procedure_type in _canonical_order(detection)
        }
        for procedure_type, status in ordered_detection.items():
            CaseProcedure.objects.filter(case=locked, procedure_type=procedure_type).update(
                detection_status=status
            )
        _create_procedure_event(
            locked,
            event_type=CaseEventType.CASE_PROCEDURES_DETECTED,
            payload={"detection": ordered_detection},
            user=user,
            role=role,
        )


def record_detected_procedures(
    case: Case,
    detection: Mapping[str, str],
    *,
    user: User | None,
    role: str | None,
) -> None:
    """Registra a detecção do pipeline com UPSERT atômico (D5, slice 004).

    Extensão do change 03 (o ``set_detected_procedures`` rejeita tipo sem row
    — insuficiente para o gate de divergência): cria as rows dos tipos
    detectados ainda não declarados (``declared_by_nir=False``, para a
    transformação permanecer auditável) e atualiza ``detection_status`` das
    rows existentes — tudo no mesmo atomic com o evento
    ``CASE_PROCEDURES_DETECTED`` na ordem canônica. Tipo fora do catálogo ou
    status fora de {detected, not_detected} é erro nomeando o valor; mapa
    vazio é erro (detecção sempre cobre a união declarado ∪ detectado).
    """
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        if not detection:
            raise ValueError("a detecção deve trazer ao menos um tipo (detected/not_detected)")
        for procedure_type, status in detection.items():
            _validate_catalog_type(procedure_type)
            if status not in _VALID_DETECTION_STATUSES:
                raise ValueError(f"status de detecção inválido para {procedure_type!r}: {status!r}")
        ordered_detection = {
            procedure_type: detection[procedure_type]
            for procedure_type in _canonical_order(detection)
        }
        for procedure_type, status in ordered_detection.items():
            updated = CaseProcedure.objects.filter(
                case=locked, procedure_type=procedure_type
            ).update(detection_status=status)
            if not updated:
                # Tipo detectado sem row (não-declarado): a row nasce neutra e
                # auditável — a divergência fica visível como row não-declarada.
                CaseProcedure.objects.create(
                    case=locked,
                    procedure_type=procedure_type,
                    declared_by_nir=False,
                    detection_status=status,
                )
        _create_procedure_event(
            locked,
            event_type=CaseEventType.CASE_PROCEDURES_DETECTED,
            payload={"detection": ordered_detection},
            user=user,
            role=role,
        )


# ── R4: decisão médica por procedimento + transição FSM ────────────────────


def record_doctor_procedure_decisions(
    case: Case,
    decisions: Mapping[str, tuple[str, str]],
    *,
    user: User | None,
    role: str | None,
) -> None:
    """Persiste as decisões médicas por procedimento e fecha a decisão do caso (R4).

    Atualiza ``doctor_disposition``/``doctor_reason``/``doctor_decided_at`` nas
    rows existentes do caso (tipo sem row é erro explícito; tipo fora do
    catálogo é rejeitado nomeando o tipo mesmo quando a row existe — defesa
    contra rows plantadas fora da ``clean()``), grava o evento
    ``CASE_DOCTOR_DECISIONS_RECORDED`` resumindo as decisões e dispara — na
    mesma transação — a transição FSM de decisão do slice 002:
    todos negados → ``DOCTOR_DENIED``; ≥1 aprovado → ``DOCTOR_ACCEPTED`` com
    ``request_scheduling`` encadeado (→ ``SCHEDULER_REQUESTED``). Falha no meio
    (ex.: caso fora de ``AWAITING_DOCTOR``) desfaz rows, eventos e transição
    juntos.
    """
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        entries: list[tuple[str, str, str]] = []
        for procedure_type, (disposition, reason) in decisions.items():
            _validate_catalog_type(procedure_type)
            if not CaseProcedure.objects.filter(
                case=locked, procedure_type=procedure_type
            ).exists():
                raise ValueError(
                    f"procedimento sem row no caso — declare-o antes: {procedure_type!r}"
                )
            if disposition not in _VALID_DOCTOR_DISPOSITIONS:
                raise ValueError(f"disposição inválida para {procedure_type!r}: {disposition!r}")
            entries.append((procedure_type, disposition, str(reason or "").strip()))
        if not entries:
            raise ValueError("registre ao menos uma decisão por procedimento")
        entries.sort(key=lambda entry: _CATALOG_ORDER[entry[0]])

        decided_at = timezone.now()
        for procedure_type, disposition, reason in entries:
            CaseProcedure.objects.filter(case=locked, procedure_type=procedure_type).update(
                doctor_disposition=disposition,
                doctor_reason=reason,
                doctor_decided_at=decided_at,
            )
        _create_procedure_event(
            locked,
            event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED,
            payload={
                "decisions": [
                    {
                        "procedure_type": procedure_type,
                        "disposition": disposition,
                        "reason_present": bool(reason),
                    }
                    for procedure_type, disposition, reason in entries
                ]
            },
            user=user,
            role=role,
        )

        any_approved = any(
            disposition == DoctorDisposition.APPROVED
            for (_procedure_type, disposition, _reason) in entries
        )
        locked.record_doctor_decision(accepted=any_approved, user=user, role=role)
        if any_approved:
            locked.request_scheduling(user=None, role="system")


# ── R5: leitores para consumo (templates/workers) ──────────────────────────


def get_declared_procedure_types(case: Case) -> tuple[str, ...]:
    """Conjunto declarado pelo NIR, ordenado pelo catálogo (caso sem rows → ())."""
    rows = CaseProcedure.objects.filter(case=case, declared_by_nir=True)
    return tuple(sorted((row.procedure_type for row in rows), key=_CATALOG_ORDER.__getitem__))


def get_detected_procedure_types(case: Case) -> tuple[str, ...]:
    """Conjunto detectado, ordenado pelo catálogo (só rows com status DETECTED)."""
    rows = CaseProcedure.objects.filter(case=case, detection_status=DetectionStatus.DETECTED)
    return tuple(sorted((row.procedure_type for row in rows), key=_CATALOG_ORDER.__getitem__))


def selection_key(procedure_types: Iterable[str]) -> str:
    """Chave canônica do conjunto: tipos unidos por '_' na ordem do catálogo.

    Derivada do conjunto (nunca de campos legados); conjunto vazio → "".
    """
    return "_".join(_canonical_order(procedure_types))


def format_procedure_selection(procedure_types: Iterable[str]) -> str:
    """Label legível dos tipos na ordem canônica, ex.: 'A + B' (R5)."""
    labels_by_type = {profile.procedure_type: profile.label for profile in PROCEDURE_PROFILES}
    ordered = _canonical_order(procedure_types)
    return " + ".join(labels_by_type[procedure_type] for procedure_type in ordered)
