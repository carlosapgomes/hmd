"""Jornada integrada da revisão até a decisão médica (slice 004, R5).

E2E com confirmação + APPLIED + pipeline produzidos pelo fluxo real (serviço
+ orquestrador + fake só no transporte LLM): manter, trocar-para-não-
detectado com reroteamento de subtipo, detecção vazia e falha que nunca fica
pronta na fila. Original B e nova D diferem; switch muda o subtipo.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from io import StringIO
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import DoctorSpecialty, Role, User
from apps.cases.events import CaseEventType
from apps.cases.models import (
    ActorType,
    Case,
    CaseDocument,
    CaseEvent,
    CaseProcedure,
    CaseStatus,
)
from apps.cases.procedure_review import latest_review_event

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
FAKE_MODEL_LLM1 = "modelo-llm1-teste"
FAKE_MODEL_LLM2 = "modelo-llm2-teste"

_ANONYMIZED_TEXT = (
    "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente. "
    "Solicitada avaliacao hemodinamica."
)
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "FULANO DE TAL SILVA", "entity_type": "PERSON"},
}

DECLARED_A = "art_perif"
DETECTED_B = "cat_cardiaco"
SWITCH_C = "nefrostomia"


class ScriptedLlmClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        assert self.responses, "cliente fake sem respostas agendadas"
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return str(item)


def _artifact(procedure_types: list[str]) -> dict[str, object]:
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
        "exames": None,
        "medicacoes": [],
        "comorbidades": [],
        "contraindicacoes": [],
        "trechos_nao_classificados": [],
    }


def _llm2_response(procedure_types: list[str]) -> dict[str, object]:
    return {
        "summary_text": "Sumario clinico em portugues para o procedimento confirmado.",
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
def _seeded_prompts() -> Any:
    call_command("seed_prompts", stdout=StringIO())


@pytest.fixture
def nir_user() -> User:
    role, _ = Role.objects.get_or_create(name=NIR_ROLE)
    user = User.objects.create_user(username="nir-journey-004", password="senha-teste")
    user.roles.add(role)
    return user


@pytest.fixture
def doctor_user() -> User:
    role, _ = Role.objects.get_or_create(name=DOCTOR_ROLE)
    user = User.objects.create_user(username="doctor-journey-004", password="senha-teste")
    user.roles.add(role)
    return user


def _login(client: Client, user: User, role: str) -> None:
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _chain(fake: ScriptedLlmClient) -> Any:
    return override_settings(
        LLM_RUN_TASKS_INLINE=True,
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    )


def _make_retained(owner: User, *, declared: str) -> Case:
    from apps.pipeline.orchestrator import process_case_pipeline

    case = Case.objects.create(created_by=owner)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    content = b"%PDF-1.4 relatorio sintetico de jornada"
    document = CaseDocument(
        case=case,
        position=1,
        original_filename="relatorio.pdf",
        content_type="application/pdf",
        size_bytes=len(content),
        uploaded_by=owner,
    )
    document.file.save("relatorio.pdf", ContentFile(content), save=False)
    document.save()
    case.extracted_text = "Texto extraido do relatorio sintetico de jornada."
    case.anonymized_text = _ANONYMIZED_TEXT
    case.pseudonym_map = dict(_PSEUDONYMS)
    case.save()
    CaseProcedure.objects.create(case=case, procedure_type=declared, declared_by_nir=True)
    CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role="system",
        payload={"version": 1},
    )
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_B]))])
    with _chain(fake):
        process_case_pipeline(case.case_id)
    case.refresh_from_db()
    assert case.manual_review_required is True
    return case


def _confirm_and_reprocess(
    case: Case, owner: User, *, procedure_type: str, new_detected: list[str]
) -> Case:
    from apps.intake.services import confirm_case_procedure_review
    from apps.pipeline.orchestrator import process_case_pipeline

    review = latest_review_event(case)
    assert review is not None
    from apps.cases.procedure_review import source_fingerprint_for_case as _fp

    fingerprint = _fp(case)
    assert fingerprint is not None
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(new_detected)),
            json.dumps(_llm2_response([procedure_type])),
        ]
    )
    with _chain(fake):
        confirm_case_procedure_review(
            case=case,
            user=owner,
            role=NIR_ROLE,
            procedure_type=procedure_type,
            review_acknowledged="on",
            review_justification="Confirmo apos leitura do relatorio.",
            review_event_id=str(review.pk),
            source_fingerprint=fingerprint,
        )
        process_case_pipeline(case.case_id)
    case.refresh_from_db()
    return case


@pytest.mark.django_db
def test_maintain_journey_shows_notice_and_allows_decision(
    client: Client, nir_user: User, doctor_user: User
) -> None:
    """R5: manter A com B original e D nova distinta; decisão não é vetada."""
    case = _make_retained(nir_user, declared=DECLARED_A)
    _confirm_and_reprocess(case, nir_user, procedure_type=DECLARED_A, new_detected=["nefrostomia"])
    assert case.status == CaseStatus.AWAITING_DOCTOR
    applied = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED)
    assert applied.payload["automatic_procedures"] == ["nefrostomia"]
    assert applied.payload["effective_procedures"] == [DECLARED_A]

    _login(client, doctor_user, DOCTOR_ROLE)
    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Procedimento confirmado pelo NIR" in body
    assert "Cateterismo cardíaco" in body or "cat_cardiaco" in body.lower() or "Card" in body
    assert "Nefrostomia" in body or "nefrostomia" in body.lower()


@pytest.mark.django_db
def test_switch_journey_reroutes_subtype_and_keeps_contradiction(
    client: Client, nir_user: User, doctor_user: User
) -> None:
    """R3: trocar art_perif (angio) -> nefrostomia (radio) muda o alcance."""
    from apps.cases.procedure_review import build_doctor_confirmation_notice

    case = _make_retained(nir_user, declared=DECLARED_A)
    _confirm_and_reprocess(case, nir_user, procedure_type=SWITCH_C, new_detected=["flebografia"])
    assert case.status == CaseStatus.AWAITING_DOCTOR
    # Original B (cat_cardiaco) e nova D (flebografia) diferem; o aviso mantém
    # B na origem e mostra só D como nova detecção (nunca a união).
    notice = build_doctor_confirmation_notice(case)
    assert notice is not None
    assert notice["original_detected"] == ("cat_cardiaco",)
    assert notice["new_detected"] == ("flebografia",)
    assert notice["show_new_detection"] is True

    from apps.doctor.access import can_access_case

    for specialty in DoctorSpecialty.objects.filter(name="radio"):
        pass
    doctor_user.specialties.set(DoctorSpecialty.objects.filter(name="radio"))
    assert can_access_case(doctor_user, case, active_role=DOCTOR_ROLE) is True
    doctor_user.specialties.set(DoctorSpecialty.objects.filter(name="cardio"))
    if DoctorSpecialty.objects.filter(name="cardio").exists():
        assert can_access_case(doctor_user, case, active_role=DOCTOR_ROLE) is False


@pytest.mark.django_db
def test_switch_journey_http_routing_for_subtype_generalist_and_admin(
    client: Client, nir_user: User
) -> None:
    """R3 (HTTP): caso trocado art_perif (angio) -> nefrostomia (radio).

    Novo-subtipo (radio) vê fila + detalhe 200; antigo-subtipo (angio) não
    vê na fila + detalhe 403; generalista (sem specialties) e admin veem fila
    + detalhe 200. Preserva os asserts de predicado/contradição do teste irmã
    (can_access_case + original/new exatos)."""
    from apps.cases.procedure_review import build_doctor_confirmation_notice
    from apps.doctor.access import can_access_case

    case = _make_retained(nir_user, declared=DECLARED_A)
    _confirm_and_reprocess(case, nir_user, procedure_type=SWITCH_C, new_detected=["flebografia"])
    assert case.status == CaseStatus.AWAITING_DOCTOR
    notice = build_doctor_confirmation_notice(case)
    assert notice is not None
    assert notice["original_detected"] == ("cat_cardiaco",)
    assert notice["new_detected"] == ("flebografia",)
    assert notice["show_new_detection"] is True

    def _make_doctor(username: str, specialties: list[str]) -> User:
        role, _ = Role.objects.get_or_create(name=DOCTOR_ROLE)
        user = User.objects.create_user(username=username, password="senha-teste")
        user.roles.add(role)
        for name in specialties:
            DoctorSpecialty.objects.get_or_create(name=name)
        user.specialties.set(DoctorSpecialty.objects.filter(name__in=specialties))
        return user

    radio_doctor = _make_doctor("doctor-radio-004", ["radio"])
    angio_doctor = _make_doctor("doctor-angio-004", ["angio"])
    generalist = _make_doctor("doctor-geral-004", [])
    admin_role, _ = Role.objects.get_or_create(name="admin")
    admin = User.objects.create_user(username="admin-switch-004", password="senha-teste")
    admin.roles.add(admin_role)

    assert can_access_case(radio_doctor, case, active_role=DOCTOR_ROLE) is True
    assert can_access_case(angio_doctor, case, active_role=DOCTOR_ROLE) is False

    queue_url = reverse("doctor:queue")
    detail_url = reverse("doctor:case_detail", args=[case.case_id])

    _login(client, radio_doctor, DOCTOR_ROLE)
    queue_body = client.get(queue_url).content.decode()
    assert str(case.case_id) in queue_body
    assert client.get(detail_url).status_code == 200

    _login(client, angio_doctor, DOCTOR_ROLE)
    excluded_body = client.get(queue_url).content.decode()
    assert str(case.case_id) not in excluded_body
    assert client.get(detail_url).status_code == 403

    _login(client, generalist, DOCTOR_ROLE)
    generalist_body = client.get(queue_url).content.decode()
    assert str(case.case_id) in generalist_body
    assert client.get(detail_url).status_code == 200

    _login(client, admin, "admin")
    admin_body = client.get(queue_url).content.decode()
    assert str(case.case_id) in admin_body
    assert client.get(detail_url).status_code == 200


@pytest.mark.django_db
def test_approval_despite_refusal_recommendation_is_not_vetoed(
    client: Client, nir_user: User, doctor_user: User
) -> None:
    """R3: aviso + recomendação de recusa nunca bloqueiam aprovação válida."""
    import re as _re

    case = _make_retained(nir_user, declared=DECLARED_A)
    _confirm_and_reprocess(case, nir_user, procedure_type=DECLARED_A, new_detected=["nefrostomia"])
    assert case.status == CaseStatus.AWAITING_DOCTOR
    # Força recomendação de recusa na policy do confirmado (consultiva).
    policy = dict(case.policy_result or {})
    policy[DECLARED_A] = {
        "recommendation": "recomenda_recusar",
        "refusal_reasons": ["plaquetas abaixo do limite"],
        "criteria": [],
        "section_id": "S1",
    }
    Case.objects.filter(pk=case.pk).update(policy_result=policy)
    case.refresh_from_db()

    _login(client, doctor_user, DOCTOR_ROLE)
    detail = client.get(reverse("doctor:case_decide", args=[case.case_id]))
    assert detail.status_code == 200
    assert "Procedimento confirmado pelo NIR" in detail.content.decode()
    match = _re.search(r'name="lock_token" value="([^"]+)"', detail.content.decode())
    assert match is not None
    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        data={
            f"procedure_{DECLARED_A}__disposition": "approved",
            f"procedure_{DECLARED_A}__reason": "",
            "lock_token": match.group(1),
        },
    )
    assert response.status_code == 302
    case.refresh_from_db()
    row = case.procedures.get(procedure_type=DECLARED_A)
    assert row.doctor_disposition == "approved"


@pytest.mark.django_db
def test_failed_case_never_appears_ready_in_queue(
    client: Client, nir_user: User, doctor_user: User
) -> None:
    """R4: caso em falha (LLM2) nunca aparece como pronto na fila médica."""
    from apps.cases.procedure_review import source_fingerprint_for_case as _fp
    from apps.intake.services import confirm_case_procedure_review
    from apps.pipeline.llm import LlmError
    from apps.pipeline.orchestrator import process_case_pipeline

    case = _make_retained(nir_user, declared=DECLARED_A)
    review = latest_review_event(case)
    assert review is not None
    fingerprint = _fp(case)
    assert fingerprint is not None
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact([DETECTED_B])),
            LlmError("other", "falha simulada"),
        ]
    )
    with _chain(fake):
        confirm_case_procedure_review(
            case=case,
            user=nir_user,
            role=NIR_ROLE,
            procedure_type=DECLARED_A,
            review_acknowledged="on",
            review_justification="Confirmo apos leitura.",
            review_event_id=str(review.pk),
            source_fingerprint=fingerprint,
        )
        process_case_pipeline(case.case_id)
    case.refresh_from_db()
    assert case.status == CaseStatus.FAILED

    _login(client, doctor_user, DOCTOR_ROLE)
    response = client.get(reverse("doctor:queue"))
    assert response.status_code == 200
    assert str(case.case_id) not in response.content.decode()


@pytest.mark.django_db
def test_empty_detection_journey_reports_absence(
    client: Client, nir_user: User, doctor_user: User
) -> None:
    """R2: APPLIED com automatic_procedures=[] informa ausência sem consenso."""
    case = _make_retained(nir_user, declared="nefrostomia")
    _confirm_and_reprocess(case, nir_user, procedure_type="nefrostomia", new_detected=[])
    assert case.status == CaseStatus.AWAITING_DOCTOR
    applied = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED)
    assert applied.payload["automatic_procedures"] == []

    _login(client, doctor_user, DOCTOR_ROLE)
    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Procedimento confirmado pelo NIR" in body
    assert "Nenhum" in body or "nenhum" in body or "ausência" in body or "ausencia" in body
