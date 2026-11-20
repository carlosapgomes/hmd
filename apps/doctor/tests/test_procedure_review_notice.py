"""Aviso médico da confirmação NIR (slice 004, R1–R2).

RED intencional: SSR de caso realmente confirmado/processado deve exibir o
aviso determinístico; caso marcado inconsistente deve recusar trabalho
GET/POST. Antes da implementação ambos falham (aviso ausente, defesa nova
ausente).
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

from apps.accounts.models import Role, User
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
    has_procedure_review_marker,
    latest_confirmation_event,
    latest_review_event,
    source_fingerprint_for_case,
)

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
DECLARED_A = "art_perif"
DETECTED_B = "cat_cardiaco"
CONFIRMED_C = "art_perif"
SWITCH_C = "angio_art_perif"
FAKE_MODEL_LLM1 = "modelo-llm1-teste"
FAKE_MODEL_LLM2 = "modelo-llm2-teste"

_ANONYMIZED_TEXT = (
    "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente. "
    "Solicitada avaliacao hemodinamica."
)
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "FULANO DE TAL SILVA", "entity_type": "PERSON"},
}


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
    user = User.objects.create_user(username="nir-notice-004", password="senha-teste")
    user.roles.add(role)
    return user


@pytest.fixture
def doctor_user() -> User:
    role, _ = Role.objects.get_or_create(name=DOCTOR_ROLE)
    user = User.objects.create_user(username="doctor-notice-004", password="senha-teste")
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


def _make_marked_case(owner: User, *, declared: str = DECLARED_A) -> Case:
    from apps.pipeline.orchestrator import process_case_pipeline

    case = Case.objects.create(created_by=owner)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.LLM_EXTRACTING
    content = b"%PDF-1.4 relatorio sintetico de aviso medico"
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
    case.extracted_text = "Texto extraido do relatorio sintetico de aviso."
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
    assert has_procedure_review_marker(case)
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_B]))])
    with _chain(fake):
        process_case_pipeline(case.case_id)
    case.refresh_from_db()
    assert case.manual_review_required is True
    return case


def _confirm(
    case: Case, owner: User, *, procedure_type: str, justification: str = "Confirmo apos leitura."
) -> CaseEvent:
    from apps.intake.services import confirm_case_procedure_review
    from apps.pipeline.orchestrator import process_case_pipeline

    review = latest_review_event(case)
    assert review is not None
    fingerprint = source_fingerprint_for_case(case)
    assert fingerprint is not None
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact([DETECTED_B])),
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
            review_justification=justification,
            review_event_id=str(review.pk),
            source_fingerprint=fingerprint,
        )
        process_case_pipeline(case.case_id)
    case.refresh_from_db()
    confirmation = latest_confirmation_event(case)
    assert confirmation is not None
    return confirmation


@pytest.mark.django_db
def test_detail_shows_confirmation_notice_after_real_flow(
    client: Client, nir_user: User, doctor_user: User
) -> None:
    """R1: detalhe SSR de caso confirmado/processado exibe o aviso completo."""
    case = _make_marked_case(nir_user)
    _confirm(case, nir_user, procedure_type=CONFIRMED_C)
    assert case.status == CaseStatus.AWAITING_DOCTOR

    _login(client, doctor_user, DOCTOR_ROLE)
    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Procedimento confirmado pelo NIR" in body
    assert "não é aprovação clínica" in body
    assert "Arteriografia periférica" in body


@pytest.mark.django_db
def test_decide_page_shows_confirmation_notice_after_real_flow(
    client: Client, nir_user: User, doctor_user: User
) -> None:
    """R1: página de decisão SSR exibe o aviso junto ao formulário."""
    case = _make_marked_case(nir_user)
    _confirm(case, nir_user, procedure_type=CONFIRMED_C)
    assert case.status == CaseStatus.AWAITING_DOCTOR

    _login(client, doctor_user, DOCTOR_ROLE)
    response = client.get(reverse("doctor:case_decide", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Procedimento confirmado pelo NIR" in body
    assert "não é aprovação clínica" in body
    assert "Registrar decisão" in body


@pytest.mark.django_db
def test_automatic_case_shows_no_fabricated_notice(
    client: Client, nir_user: User, doctor_user: User
) -> None:
    """R1: caso automático/legado sem confirmação nunca ganha aviso humano."""
    case = Case.objects.create(created_by=nir_user)
    CaseProcedure.objects.create(case=case, procedure_type=DECLARED_A, declared_by_nir=True)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR

    _login(client, doctor_user, DOCTOR_ROLE)
    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    assert "Procedimento confirmado pelo NIR" not in response.content.decode()


@pytest.mark.django_db
def test_inconsistent_marked_case_refuses_work_get(
    client: Client, nir_user: User, doctor_user: User
) -> None:
    """R4: GET de trabalho em caso marcado inconsistente é recusado sem formulário."""
    case = _make_marked_case(nir_user)
    _confirm(case, nir_user, procedure_type=CONFIRMED_C)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    Case.objects.filter(pk=case.pk).update(summary_text="   ")
    case.refresh_from_db()

    _login(client, doctor_user, DOCTOR_ROLE)
    response = client.get(reverse("doctor:case_decide", args=[case.case_id]))

    assert response.status_code == 302
    assert response["Location"] == reverse("doctor:case_detail", args=[case.case_id])
    follow = client.get(response["Location"])
    assert "inconsistente" in follow.content.decode().lower()
    assert (
        "Registrar decisão" not in response.content.decode()
        if response.status_code == 200
        else True
    )


@pytest.mark.django_db
def test_notice_escapes_justification_markup(nir_user: User) -> None:
    """R2: justificativa com markup sai escapada (nunca mark_safe)."""
    from apps.cases.procedure_review import build_doctor_confirmation_notice

    case = _make_marked_case(nir_user)
    _confirm(
        case, nir_user, procedure_type=CONFIRMED_C, justification='<script>alert("xss")</script>'
    )
    notice = build_doctor_confirmation_notice(case)
    assert notice is not None
    assert notice["justification"] == '<script>alert("xss")</script>'

    from django.test import Client as _Client
    from django.urls import reverse as _reverse

    doctor_role, _ = Role.objects.get_or_create(name=DOCTOR_ROLE)
    doctor = User.objects.create_user(username="doctor-xss-004", password="senha-teste")
    doctor.roles.add(doctor_role)
    browser = _Client()
    browser.force_login(doctor)
    session = browser.session
    session["active_role"] = DOCTOR_ROLE
    session.save()
    body = browser.get(_reverse("doctor:case_detail", args=[case.case_id])).content.decode()
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body


@pytest.mark.django_db
def test_notice_absent_without_actor_or_bad_reference(nir_user: User) -> None:
    """R1/R2: sem ator ou com referência quebrada não há aviso fictício."""
    from apps.cases.procedure_review import build_doctor_confirmation_notice

    case = _make_marked_case(nir_user)
    confirmation = _confirm(case, nir_user, procedure_type=CONFIRMED_C)
    # Ator ausente: nunca inferir autoria.
    CaseEvent.objects.filter(pk=confirmation.pk).update(actor=None)
    confirmation.refresh_from_db()
    assert build_doctor_confirmation_notice(case) is None
    # Referência quebrada: revisão apontada não existe.
    CaseEvent.objects.filter(pk=confirmation.pk).update(actor=nir_user)
    payload = dict(confirmation.payload)
    payload["review_event_id"] = 999999999
    CaseEvent.objects.filter(pk=confirmation.pk).update(payload=payload)
    confirmation.refresh_from_db()
    assert build_doctor_confirmation_notice(case) is None


@pytest.mark.django_db
def test_notice_keeps_original_and_shows_only_new_detection(nir_user: User) -> None:
    """R2: A declarado, B original, C confirmado, D nova — sem união."""
    from apps.cases.procedure_review import build_doctor_confirmation_notice

    case = _make_marked_case(nir_user, declared=DECLARED_A)
    review = latest_review_event(case)
    assert review is not None
    from apps.cases.procedure_review import source_fingerprint_for_case as _fp
    from apps.intake.services import confirm_case_procedure_review
    from apps.pipeline.orchestrator import process_case_pipeline

    fingerprint = _fp(case)
    assert fingerprint is not None
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact(["flebografia"])),
            json.dumps(_llm2_response([SWITCH_C])),
        ]
    )
    with _chain(fake):
        confirm_case_procedure_review(
            case=case,
            user=nir_user,
            role=NIR_ROLE,
            procedure_type=SWITCH_C,
            review_acknowledged="on",
            review_justification="Troco apos releitura.",
            review_event_id=str(review.pk),
            source_fingerprint=fingerprint,
        )
        process_case_pipeline(case.case_id)
    case.refresh_from_db()
    notice = build_doctor_confirmation_notice(case)
    assert notice is not None
    assert notice["confirmed_procedures"] == (SWITCH_C,)
    assert notice["selection_changed"] is True
    assert notice["original_detected"] == (DETECTED_B,)
    assert notice["new_detected"] == ("flebografia",)
    assert notice["new_detected"] != notice["original_detected"]


@pytest.mark.django_db
def test_inconsistent_marked_case_refuses_work_post(
    client: Client, nir_user: User, doctor_user: User
) -> None:
    """R4: POST direto em caso marcado inconsistente não grava decisão."""
    case = _make_marked_case(nir_user)
    _confirm(case, nir_user, procedure_type=CONFIRMED_C)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    Case.objects.filter(pk=case.pk).update(summary_text="")
    case.refresh_from_db()
    events_before = case.events.count()

    _login(client, doctor_user, DOCTOR_ROLE)
    detail = client.get(reverse("doctor:case_decide", args=[case.case_id]))
    import re as _re

    token = ""
    if detail.status_code == 200:
        match = _re.search(r'name="lock_token" value="([^"]+)"', detail.content.decode())
        token = match.group(1) if match else ""
    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        data={
            f"procedure_{CONFIRMED_C}__disposition": "approved",
            f"procedure_{CONFIRMED_C}__reason": "",
            "lock_token": token,
        },
    )

    assert response.status_code in (302, 200)
    case.refresh_from_db()
    row = case.procedures.get(procedure_type=CONFIRMED_C)
    assert row.doctor_disposition == "pending"
    assert case.events.count() == events_before
