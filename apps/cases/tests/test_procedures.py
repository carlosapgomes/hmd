"""Testes de CaseProcedure + serviços de procedimento (change 03, slice 003, R1–R6).

Cobre o model ``CaseProcedure`` (R1: unicidade (case, procedure_type),
validação do tipo contra o catálogo na ``clean()``), as operações atômicas de
declaração (R2 — substitui a declaração anterior), detecção (R3) e decisão
médica por procedimento (R4 — atualiza rows e dispara a transição FSM de
decisão do slice 002, encadeando ``request_scheduling`` quando ≥1 aprovado, na
mesma transação), os leitores (R5) e os 3 cenários da spec "Procedimentos por
caso neutros e atômicos" + o cenário de evento de detecção da spec "Trilha de
auditoria append-only". Cobre ainda a defesa de persistência do tipo contra o
catálogo no ``save()`` (R1), a rejeição de tipo fora do catálogo na detecção e
na decisão mesmo com row plantada fora da ``clean()``, a ordem canônica das
chaves no payload de detecção e o rollback total quando o encadeamento de
aprovação falha no meio (após o 1º evento).
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django_fsm import TransitionNotAllowed

from apps.accounts.models import User
from apps.cases.events import CaseEventType, case_status_event_type
from apps.cases.models import (
    ActorType,
    Case,
    CaseEvent,
    CaseProcedure,
    CaseStatus,
    DetectionStatus,
    DoctorDisposition,
)
from apps.cases.procedures import (
    format_procedure_selection,
    get_declared_procedure_types,
    get_detected_procedure_types,
    record_doctor_procedure_decisions,
    selection_key,
    set_declared_procedures,
    set_detected_procedures,
)

DOCTOR_ROLE = "doctor"
NIR_ROLE = "nir"
SYSTEM_ROLE = "system"


def _create_case(created_by: User) -> Case:
    return Case.objects.create(created_by=created_by)


def _case_awaiting_doctor(*, created_by: User) -> Case:
    """Cria um caso e o dirige pelas transições do pipeline até AWAITING_DOCTOR."""
    case = _create_case(created_by)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    return case


def _event_types(case: Case) -> list[str]:
    return [event.event_type for event in case.events.order_by("id")]


# ── R1: CaseProcedure ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_unique_constraint(nir_user: User) -> None:
    """R1: no máximo uma row por (case, procedure_type)."""
    case = _create_case(nir_user)
    CaseProcedure.objects.create(case=case, procedure_type="art_perif")
    with pytest.raises(IntegrityError):
        CaseProcedure.objects.create(case=case, procedure_type="art_perif")


@pytest.mark.django_db
def test_clean_rejects_type_outside_catalog(nir_user: User) -> None:
    """R1: tipo fora do catálogo é rejeitado pela clean() do model."""
    case = _create_case(nir_user)
    procedure = CaseProcedure(case=case, procedure_type="procedimento_inexistente")
    with pytest.raises(ValidationError, match="procedimento fora do catálogo"):
        procedure.full_clean()


@pytest.mark.django_db
def test_save_rejects_type_outside_catalog(nir_user: User) -> None:
    """R1: defesa na persistência — save() revalida o tipo quando o
    objects.create() pula a full_clean(); nenhuma row inválida persiste."""
    case = _create_case(nir_user)

    with pytest.raises(ValidationError, match="procedimento fora do catálogo"):
        CaseProcedure.objects.create(case=case, procedure_type="tipo_inexistente")

    assert CaseProcedure.objects.filter(case=case).count() == 0


# ── R2: declaração atômica ─────────────────────────────────────────────────


@pytest.mark.django_db
def test_declaration_atomic_replace(nir_user: User) -> None:
    """R2/cenário spec: declarar substitui a declaração anterior sem duplicatas
    nem rows declaradas de tipos não declarados; cada declaração grava evento."""
    case = _create_case(nir_user)

    set_declared_procedures(case, ["art_perif"], user=nir_user, role=NIR_ROLE)
    set_declared_procedures(case, ["art_perif", "nefrostomia"], user=nir_user, role=NIR_ROLE)

    rows = list(case.procedures.all())
    assert len(rows) == 2
    assert {row.procedure_type for row in rows} == {"art_perif", "nefrostomia"}
    assert all(row.declared_by_nir for row in rows)
    assert get_declared_procedure_types(case) == ("art_perif", "nefrostomia")

    # Declaração posterior enxuta desativa a anterior sem perder a row (a
    # transformação permanece auditável) nem criar duplicatas.
    set_declared_procedures(case, ["nefrostomia"], user=nir_user, role=NIR_ROLE)
    assert case.procedures.count() == 2
    art_perif_row = CaseProcedure.objects.get(case=case, procedure_type="art_perif")
    assert art_perif_row.declared_by_nir is False
    assert get_declared_procedure_types(case) == ("nefrostomia",)

    declared_events = case.events.filter(event_type=CaseEventType.CASE_PROCEDURES_DECLARED)
    assert declared_events.count() == 3
    payloads = [event.payload["procedure_types"] for event in declared_events.order_by("id")]
    assert payloads == [["art_perif"], ["art_perif", "nefrostomia"], ["nefrostomia"]]


@pytest.mark.django_db
def test_declaration_invalid_type_rolls_back(nir_user: User) -> None:
    """R2/cenário spec: conjunto com tipo fora do catálogo falha inteiro
    nomeando o tipo — nenhuma row criada nem evento gravado."""
    case = _create_case(nir_user)
    invalid = "procedimento_inexistente"

    with pytest.raises(ValueError, match=invalid):
        set_declared_procedures(case, ["art_perif", invalid], user=nir_user, role=NIR_ROLE)

    assert CaseProcedure.objects.filter(case=case).count() == 0
    assert not case.events.filter(event_type=CaseEventType.CASE_PROCEDURES_DECLARED).exists()


# ── R3: detecção atômica ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_detection_updates_and_events(nir_user: User) -> None:
    """R3/cenário trilha: detecção atualiza rows existentes e grava o resultado
    (tipos detectados/não detectados) no payload do evento."""
    case = _create_case(nir_user)
    set_declared_procedures(case, ["art_perif", "nefrostomia"], user=nir_user, role=NIR_ROLE)

    set_detected_procedures(
        case,
        {"art_perif": "detected", "nefrostomia": "not_detected"},
        user=None,
        role=SYSTEM_ROLE,
    )

    statuses = {row.procedure_type: row.detection_status for row in case.procedures.all()}
    assert statuses == {
        "art_perif": DetectionStatus.DETECTED,
        "nefrostomia": DetectionStatus.NOT_DETECTED,
    }
    assert get_detected_procedure_types(case) == ("art_perif",)

    event = case.events.get(event_type=CaseEventType.CASE_PROCEDURES_DETECTED)
    assert event.payload == {"detection": {"art_perif": "detected", "nefrostomia": "not_detected"}}
    assert event.actor_type == ActorType.SYSTEM
    assert event.actor is None
    assert event.actor_role == SYSTEM_ROLE


@pytest.mark.django_db
def test_detection_unknown_row_fails(nir_user: User) -> None:
    """R3: tipo sem row no caso (o pipeline deve declarar/reconciliar antes) é
    erro explícito e nada muda — nem status nem evento."""
    case = _create_case(nir_user)
    set_declared_procedures(case, ["art_perif"], user=nir_user, role=NIR_ROLE)

    with pytest.raises(ValueError, match="nefrostomia"):
        set_detected_procedures(case, {"nefrostomia": "detected"}, user=None, role=SYSTEM_ROLE)

    row = case.procedures.get(procedure_type="art_perif")
    assert row.detection_status == DetectionStatus.PENDING
    assert not case.events.filter(event_type=CaseEventType.CASE_PROCEDURES_DETECTED).exists()


@pytest.mark.django_db
def test_detection_payload_uses_canonical_order(nir_user: User) -> None:
    """R3/P2: o payload do evento de detecção normaliza as chaves para a ordem
    canônica do catálogo, independente da ordem recebida."""
    case = _create_case(nir_user)
    set_declared_procedures(case, ["art_perif", "nefrostomia"], user=nir_user, role=NIR_ROLE)

    set_detected_procedures(
        case,
        {"nefrostomia": "detected", "art_perif": "detected"},
        user=None,
        role=SYSTEM_ROLE,
    )

    event = case.events.get(event_type=CaseEventType.CASE_PROCEDURES_DETECTED)
    assert list(event.payload["detection"]) == ["art_perif", "nefrostomia"]


@pytest.mark.django_db
def test_detection_rejects_row_type_outside_catalog(nir_user: User) -> None:
    """R3: detecção valida cada tipo contra o catálogo mesmo quando a row
    existe — row inválida plantada via bulk_create (fora da clean/save) é
    rejeitada nomeando o tipo, sem atualizar nada nem gravar evento."""
    case = _create_case(nir_user)
    set_declared_procedures(case, ["art_perif"], user=nir_user, role=NIR_ROLE)
    CaseProcedure.objects.bulk_create([CaseProcedure(case=case, procedure_type="tipo_inexistente")])

    with pytest.raises(ValueError, match="procedimento fora do catálogo"):
        set_detected_procedures(
            case,
            {"art_perif": "detected", "tipo_inexistente": "detected"},
            user=None,
            role=SYSTEM_ROLE,
        )

    row = case.procedures.get(procedure_type="art_perif")
    assert row.detection_status == DetectionStatus.PENDING
    assert not case.events.filter(event_type=CaseEventType.CASE_PROCEDURES_DETECTED).exists()


# ── R4: decisão médica por procedimento + transição FSM ────────────────────


@pytest.mark.django_db
def test_decision_all_denied_transitions(nir_user: User, doctor_user: User) -> None:
    """R4/cenário spec: negação de todos os procedimentos registra a decisão por
    row com motivo e leva o caso a DOCTOR_DENIED (caminho da negação até
    FINAL_REPLY_POSTED, sem passar pela fila de agendamento)."""
    case = _case_awaiting_doctor(created_by=nir_user)
    set_declared_procedures(case, ["art_perif", "nefrostomia"], user=nir_user, role=NIR_ROLE)

    record_doctor_procedure_decisions(
        case,
        {
            "art_perif": ("denied", "sem indicação clínica"),
            "nefrostomia": ("denied", "risco elevado"),
        },
        user=doctor_user,
        role=DOCTOR_ROLE,
    )

    rows = {row.procedure_type: row for row in case.procedures.all()}
    assert rows["art_perif"].doctor_disposition == DoctorDisposition.DENIED
    assert rows["art_perif"].doctor_reason == "sem indicação clínica"
    assert rows["nefrostomia"].doctor_disposition == DoctorDisposition.DENIED
    assert rows["nefrostomia"].doctor_reason == "risco elevado"
    assert rows["art_perif"].doctor_decided_at is not None

    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    event_types = _event_types(case)
    assert event_types[-2:] == [
        CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED,
        case_status_event_type(CaseStatus.DOCTOR_DENIED),
    ]
    assert case_status_event_type(CaseStatus.DOCTOR_ACCEPTED) not in event_types
    assert case_status_event_type(CaseStatus.SCHEDULER_REQUESTED) not in event_types

    # Caminho da negação segue sem fila de agendamento.
    case.post_final_reply(user=None, role=SYSTEM_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED


@pytest.mark.django_db
def test_decision_partial_accept_transitions(nir_user: User, doctor_user: User) -> None:
    """R4: decisão mista (≥1 aprovado) registra por row e encadeia, na mesma
    transação, DOCTOR_ACCEPTED → SCHEDULER_REQUESTED."""
    case = _case_awaiting_doctor(created_by=nir_user)
    set_declared_procedures(case, ["art_perif", "nefrostomia"], user=nir_user, role=NIR_ROLE)

    record_doctor_procedure_decisions(
        case,
        {
            "art_perif": ("approved", ""),
            "nefrostomia": ("denied", "fístula inviável"),
        },
        user=doctor_user,
        role=DOCTOR_ROLE,
    )

    rows = {row.procedure_type: row for row in case.procedures.all()}
    assert rows["art_perif"].doctor_disposition == DoctorDisposition.APPROVED
    assert rows["art_perif"].doctor_reason == ""
    assert rows["nefrostomia"].doctor_disposition == DoctorDisposition.DENIED
    assert rows["nefrostomia"].doctor_reason == "fístula inviável"

    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    event_types = _event_types(case)
    assert event_types[-3:] == [
        CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED,
        case_status_event_type(CaseStatus.DOCTOR_ACCEPTED),
        case_status_event_type(CaseStatus.SCHEDULER_REQUESTED),
    ]

    decision_event = case.events.get(event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED)
    assert decision_event.actor == doctor_user
    assert decision_event.actor_type == ActorType.USER
    assert decision_event.actor_role == DOCTOR_ROLE
    assert decision_event.payload == {
        "decisions": [
            {"procedure_type": "art_perif", "disposition": "approved", "reason_present": False},
            {"procedure_type": "nefrostomia", "disposition": "denied", "reason_present": True},
        ]
    }


@pytest.mark.django_db
def test_decision_unknown_row_fails(nir_user: User, doctor_user: User) -> None:
    """R4: decisão sobre tipo sem row é erro explícito sem efeito."""
    case = _case_awaiting_doctor(created_by=nir_user)
    set_declared_procedures(case, ["art_perif"], user=nir_user, role=NIR_ROLE)

    with pytest.raises(ValueError, match="nefrostomia"):
        record_doctor_procedure_decisions(
            case,
            {"art_perif": ("approved", ""), "nefrostomia": ("denied", "")},
            user=doctor_user,
            role=DOCTOR_ROLE,
        )

    row = case.procedures.get(procedure_type="art_perif")
    assert row.doctor_disposition == DoctorDisposition.PENDING
    assert row.doctor_decided_at is None
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert not case.events.filter(event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED).exists()


@pytest.mark.django_db
def test_decision_rejects_row_type_outside_catalog(nir_user: User, doctor_user: User) -> None:
    """R4: decisão valida cada tipo contra o catálogo mesmo quando a row
    existe — row inválida plantada via bulk_create (fora da clean/save) é
    rejeitada nomeando o tipo, sem tocar rows válidas, status ou trilha."""
    case = _case_awaiting_doctor(created_by=nir_user)
    set_declared_procedures(case, ["art_perif"], user=nir_user, role=NIR_ROLE)
    CaseProcedure.objects.bulk_create([CaseProcedure(case=case, procedure_type="tipo_inexistente")])

    with pytest.raises(ValueError, match="procedimento fora do catálogo"):
        record_doctor_procedure_decisions(
            case,
            {"art_perif": ("approved", ""), "tipo_inexistente": ("denied", "")},
            user=doctor_user,
            role=DOCTOR_ROLE,
        )

    row = case.procedures.get(procedure_type="art_perif")
    assert row.doctor_disposition == DoctorDisposition.PENDING
    assert row.doctor_decided_at is None
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert not case.events.filter(event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED).exists()


@pytest.mark.django_db
def test_decision_transition_failure_rolls_back_all(nir_user: User, doctor_user: User) -> None:
    """R4: rows e transição FSM vivem na mesma transação — se a transição de
    decisão falhar (caso fora de AWAITING_DOCTOR), rows e eventos são desfeitos."""
    case = _create_case(nir_user)
    set_declared_procedures(case, ["art_perif"], user=nir_user, role=NIR_ROLE)
    assert case.status == CaseStatus.NEW

    with pytest.raises(TransitionNotAllowed):
        with transaction.atomic():
            record_doctor_procedure_decisions(
                case, {"art_perif": ("approved", "")}, user=doctor_user, role=DOCTOR_ROLE
            )

    row = case.procedures.get(procedure_type="art_perif")
    assert row.doctor_disposition == DoctorDisposition.PENDING
    assert row.doctor_reason == ""
    assert row.doctor_decided_at is None
    assert not case.events.filter(event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED).exists()


@pytest.mark.django_db
def test_decision_middle_event_failure_rolls_back_all(
    nir_user: User, doctor_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: falha no meio do encadeamento de aprovação (a criação do 2º evento
    levanta depois do 1º gravado) desfaz tudo junto — rows de decisão não
    persistem, o status do caso permanece AWAITING_DOCTOR e nenhum CaseEvent
    do encadeamento persiste."""
    case = _case_awaiting_doctor(created_by=nir_user)
    set_declared_procedures(case, ["art_perif", "nefrostomia"], user=nir_user, role=NIR_ROLE)
    events_before = case.events.count()

    original_create = CaseEvent.objects.create
    create_calls = 0

    def flaky_create(**kwargs: object) -> CaseEvent:
        nonlocal create_calls
        create_calls += 1
        if create_calls == 2:
            raise RuntimeError("falha simulada na criação do 2º evento do encadeamento")
        return original_create(**kwargs)

    monkeypatch.setattr(CaseEvent.objects, "create", flaky_create)

    with pytest.raises(RuntimeError, match="2º evento"):
        record_doctor_procedure_decisions(
            case,
            {
                "art_perif": ("approved", ""),
                "nefrostomia": ("denied", "risco elevado"),
            },
            user=doctor_user,
            role=DOCTOR_ROLE,
        )

    rows = {row.procedure_type: row for row in case.procedures.all()}
    for row in rows.values():
        assert row.doctor_disposition == DoctorDisposition.PENDING
        assert row.doctor_reason == ""
        assert row.doctor_decided_at is None

    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert case.events.count() == events_before
    chain_event_types = [
        CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED,
        case_status_event_type(CaseStatus.DOCTOR_ACCEPTED),
        case_status_event_type(CaseStatus.SCHEDULER_REQUESTED),
    ]
    assert not case.events.filter(event_type__in=chain_event_types).exists()


# ── R5: leitores ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_readers_and_selection_key(nir_user: User) -> None:
    """R5: leitores dos conjuntos declarado/detectado e chave/label canônicos
    na ordem do catálogo."""
    case = _create_case(nir_user)
    assert get_declared_procedure_types(case) == ()
    assert get_detected_procedure_types(case) == ()
    assert selection_key(()) == ""

    # Entrada fora de ordem: leitores devolvem a ordem canônica do catálogo.
    set_declared_procedures(case, ["nefrostomia", "art_perif"], user=nir_user, role=NIR_ROLE)
    declared = get_declared_procedure_types(case)
    assert declared == ("art_perif", "nefrostomia")
    assert selection_key(declared) == "art_perif_nefrostomia"
    assert (
        format_procedure_selection(declared) == "Arteriografia periférica + Nefrostomia percutânea"
    )

    set_detected_procedures(case, {"art_perif": "detected"}, user=None, role=SYSTEM_ROLE)
    assert get_detected_procedure_types(case) == ("art_perif",)
