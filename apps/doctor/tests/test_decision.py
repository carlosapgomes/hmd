"""Testes da decisão médica por procedimento (doctor-queue-decision, slice 004, R1–R6).

Cobre:
- R1: ``DoctorDecisionForm`` dinâmico sobre os tipos declarados
  (``procedure_<type>__disposition``/``procedure_<type>__reason``) — motivo
  obrigatório quando ``denied`` com erro nomeando o procedimento; aprovado sem
  motivo é válido; todos os procedimentos exigem decisão (não há "sem
  decisão");
- R2: ``GET doctor:case_decide`` — renderiza form apenas em ``AWAITING_DOCTOR``;
  caso já decidido → redirect ao detalhe com mensagem; guard papel/subtipo;
- R3: ``POST doctor:case_decide`` — negado-total → decisão registrada e o
  wiring do fechamento (nir-result-closure) publica a resposta final ao NIR
  (caso sai de ``DOCTOR_DENIED`` para ``FINAL_REPLY_POSTED`` com rows,
  eventos e thread); misto (≥1 aprovado) → ``SCHEDULER_REQUESTED``; guard
  por subtipo → 403 sem escrita;
- R4: submissão concorrente/estado inválido → sem efeito parcial + mensagem
  "já decidido por outro médico" + redirect (nunca 500);
- R5: detalhe read-only pós-decisão — presenter exibe decisões por procedimento
  (disposição + motivo + ``doctor_decided_at``), ator/data do evento
  ``CASE_DOCTOR_DECISIONS_RECORDED`` e trilha de eventos; template sem
  formulário fora de ``AWAITING_DOCTOR``;
- R6: wiring do fechamento da negativa (nir-result-closure, slice 001) — a
  view re-lê o caso após o serviço do change 03 (que devolve ``None`` e
  trabalha sobre um ``locked`` re-buscado — o ``case`` em memória fica stale)
  e fecha SOMENTE a negativa total (``DOCTOR_DENIED`` → resposta final na
  thread); decisão parcial → ``SCHEDULER_REQUESTED`` sem chamada ao
  fechamento; falha do fechamento (``ValueError``) → mensagem + redirect,
  nunca 500, caso permanece ``DOCTOR_DENIED``.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.attachments.models import AttachmentStatus, CaseAttachment, ExtractionMethod, PatientMatch
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock
from apps.cases.models import (
    ActorType,
    Case,
    CaseDocument,
    CaseEvent,
    CaseProcedure,
    CaseStatus,
    DoctorDisposition,
    MessageType,
)
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.doctor import views as doctor_views
from apps.doctor.forms import DoctorDecisionForm
from apps.doctor.presenters import build_case_detail_context

DOCTOR_ROLE = "doctor"
NIR_ROLE = "nir"
SYSTEM_ROLE = "system"

# Contexto do lock da página de trabalho (case-work-lock D2).
LOCK_CONTEXT = "doctor_decision"
LOCK_LOST_TEXT = "Sua reserva para este caso expirou ou não é válida"

# Tipos declarados representativos (catálogo).
ANGIO_TYPE = "art_perif"
RADIO_TYPE = "nefrostomia"

ANGIO_LABEL = "Arteriografia periférica"
RADIO_LABEL = "Nefrostomia percutânea"


def _login(client: Client, user: User, role: str) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _create_case_with_declared(created_by: User, procedure_types: Sequence[str]) -> Case:
    """Cria um caso com as rows declaradas (sem transições de status)."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )
    return case


def _advance_to_awaiting_doctor(case: Case) -> None:
    """Dirige o caso pelas transições do pipeline até ``AWAITING_DOCTOR``."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _make_awaiting_case(created_by: User, procedure_types: Sequence[str]) -> Case:
    """Caso em ``AWAITING_DOCTOR`` com os tipos declarados."""
    case = _create_case_with_declared(created_by, procedure_types)
    _advance_to_awaiting_doctor(case)
    return case


def _form_payload(decisions: Mapping[str, tuple[str, str]]) -> dict[str, str]:
    """Payload POST do form para o mapa decisão tipo → (disposição, motivo)."""
    payload: dict[str, str] = {}
    for procedure_type, (disposition, reason) in decisions.items():
        payload[f"procedure_{procedure_type}__disposition"] = disposition
        payload[f"procedure_{procedure_type}__reason"] = reason
    return payload


def _extract_lock_token(body: str) -> str:
    """Token do lock embutido no formulário da página de trabalho (D2)."""
    match = re.search(r'name="lock_token" value="([^"]+)"', body)
    assert match is not None, "lock_token ausente no formulário da página de trabalho"
    return match.group(1)


def _claim_token(case: Case, user: User) -> str:
    """Reserva o caso para o médico (claim direto) e devolve o token do POST."""
    lock = claim_case_lock(case, user=user, context=LOCK_CONTEXT, role=DOCTOR_ROLE)
    return str(lock.token)


def _with_token(payload: dict[str, str], token: str) -> dict[str, str]:
    """Payload do POST com a reserva vigente (R6: o token é obrigatório)."""
    return {**payload, "lock_token": token}


def _row_state(case: Case, procedure_type: str) -> tuple[str, str, object]:
    row = case.procedures.get(procedure_type=procedure_type)
    return (row.doctor_disposition, row.doctor_reason, row.doctor_decided_at)


def _rows_by_type(case: Case) -> dict[str, CaseProcedure]:
    return {row.procedure_type: row for row in case.procedures.all()}


def _add_mismatch_attachment(case: Case, uploaded_by: User) -> CaseAttachment:
    """Anexo processado com ``patient_match=mismatch`` (slice 004, R4)."""
    attachment = CaseAttachment.objects.create(
        case=case,
        content_type="application/pdf",
        original_filename="laudo-divergente.pdf",
        size_bytes=1024,
        uploaded_by=uploaded_by,
        status=AttachmentStatus.PROCESSED,
        extraction_method=ExtractionMethod.LOCAL_PDF,
        patient_match=PatientMatch.MISMATCH,
        verification_summary=("Laudo assinado por outro paciente — divergência de identificação."),
    )
    return attachment


# ── R1: DoctorDecisionForm ────────────────────────────────────────────────


@pytest.mark.django_db
def test_form_requires_reason_on_deny(nir_user: User) -> None:
    """R1: negativa sem motivo é inválida — erro no campo nomeando o procedimento."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE, RADIO_TYPE))

    form = DoctorDecisionForm(
        data=_form_payload({ANGIO_TYPE: ("denied", ""), RADIO_TYPE: ("approved", "")}),
        case=case,
    )

    assert form.is_valid() is False
    reason_field = f"procedure_{ANGIO_TYPE}__reason"
    assert reason_field in form.errors
    assert ANGIO_LABEL in " ".join(str(error) for error in form.errors[reason_field])
    # Aprovação sem motivo é válida (nenhum erro para o procedimento aprovado).
    assert f"procedure_{RADIO_TYPE}__reason" not in form.errors
    assert f"procedure_{RADIO_TYPE}__disposition" not in form.errors


@pytest.mark.django_db
def test_form_all_missing_rejected(nir_user: User) -> None:
    """R1: todo procedimento declarado exige decisão — sem campo em branco."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE, RADIO_TYPE))

    form = DoctorDecisionForm(data={}, case=case)

    assert form.is_valid() is False
    for procedure_type, label in ((ANGIO_TYPE, ANGIO_LABEL), (RADIO_TYPE, RADIO_LABEL)):
        disposition_field = f"procedure_{procedure_type}__disposition"
        assert disposition_field in form.errors
        assert label in " ".join(str(error) for error in form.errors[disposition_field])


@pytest.mark.django_db
def test_form_approved_without_reason_valid(nir_user: User) -> None:
    """R1: aprovação sem motivo é válida e expõe o mapa de decisões do serviço."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE, RADIO_TYPE))

    form = DoctorDecisionForm(
        data=_form_payload({ANGIO_TYPE: ("approved", ""), RADIO_TYPE: ("approved", "   ")}),
        case=case,
    )

    assert form.is_valid() is True
    assert form.decisions() == {
        ANGIO_TYPE: ("approved", ""),
        RADIO_TYPE: ("approved", ""),
    }


# ── R2: GET doctor:case_decide ────────────────────────────────────────────


@pytest.mark.django_db
def test_decide_get_renders_form(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: GET decide renderiza o form dinâmico com os procedimentos declarados."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE, RADIO_TYPE))
    doctor = user_factory("medico-decide-get", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_decide", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert f'name="procedure_{ANGIO_TYPE}__disposition"' in body
    assert f'name="procedure_{ANGIO_TYPE}__reason"' in body
    assert f'name="procedure_{RADIO_TYPE}__disposition"' in body
    assert f'name="procedure_{RADIO_TYPE}__reason"' in body
    assert ANGIO_LABEL in body
    assert RADIO_LABEL in body
    assert "Registrar decisão" in body


@pytest.mark.django_db
def test_decide_get_redirects_when_decided(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: GET decide de caso já decidido → redirect ao detalhe com mensagem."""
    owner = user_factory("dono-decide-get", (NIR_ROLE,))
    case = _make_awaiting_case(owner, (ANGIO_TYPE,))
    decider = user_factory("medico-decide-get-ja", (DOCTOR_ROLE,))
    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: ("denied", "sem indicação clínica")},
        user=decider,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    doctor = user_factory("medico-decide-get2", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    detail_url = reverse("doctor:case_detail", args=[case.case_id])
    response = client.get(reverse("doctor:case_decide", args=[case.case_id]))

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    assert "já foi decidido" in client.get(detail_url).content.decode()


@pytest.mark.django_db
def test_decide_get_forbidden_for_subtype_outside_case(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    assign_specialties: Callable[[User, Sequence[str]], None],
) -> None:
    """R2: médico com subtipo fora do caso → 403 no decide (guard completo)."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    cardio_doctor = user_factory("doc-cardio-decide-get", (DOCTOR_ROLE,))
    assign_specialties(cardio_doctor, ("cardio",))
    _login(client, cardio_doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_decide", args=[case.case_id]))

    assert response.status_code == 403


# ── R3: POST doctor:case_decide ───────────────────────────────────────────


@pytest.mark.django_db
def test_decide_all_denied_posts_final_reply(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3/R6: POST negado-total → decisão + resposta final publicada ao NIR.

    O wiring do fechamento re-lê o caso (o serviço do change 03 trabalha sobre
    um ``locked`` re-buscado) e sai de ``DOCTOR_DENIED`` para
    ``FINAL_REPLY_POSTED`` — evento com o médico e thread com a resposta
    autoral (label + motivo de cada procedimento negado) e flash registrada.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE, RADIO_TYPE))
    doctor = user_factory("medico-denies-all", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    token = _claim_token(case, doctor)
    decide_url = reverse("doctor:case_decide", args=[case.case_id])

    response = client.post(
        decide_url,
        _with_token(
            _form_payload(
                {
                    ANGIO_TYPE: ("denied", "sem indicação clínica"),
                    RADIO_TYPE: ("denied", "risco elevado"),
                }
            ),
            token,
        ),
    )

    assert response.status_code == 302
    detail_url = reverse("doctor:case_detail", args=[case.case_id])
    assert response.headers["Location"] == detail_url

    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    rows = _rows_by_type(case)
    assert rows[ANGIO_TYPE].doctor_disposition == DoctorDisposition.DENIED
    assert rows[ANGIO_TYPE].doctor_reason == "sem indicação clínica"
    assert rows[ANGIO_TYPE].doctor_decided_at is not None
    assert rows[RADIO_TYPE].doctor_disposition == DoctorDisposition.DENIED
    assert rows[RADIO_TYPE].doctor_reason == "risco elevado"

    decision_event = case.events.get(event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED)
    assert decision_event.actor == doctor
    assert decision_event.actor_role == DOCTOR_ROLE
    # Encadeamento do wiring: decisão → DOCTOR_DENIED → release da reserva
    # (R7) → FINAL_REPLY_POSTED (fechamento, fora da transação da decisão).
    event_types = [event.event_type for event in case.events.order_by("id")]
    assert event_types[-4:] == [
        CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED,
        f"CASE_STATUS_{CaseStatus.DOCTOR_DENIED}",
        CaseEventType.CASE_LOCK_RELEASED,
        f"CASE_STATUS_{CaseStatus.FINAL_REPLY_POSTED}",
    ]
    final_event = case.events.get(event_type=f"CASE_STATUS_{CaseStatus.FINAL_REPLY_POSTED}")
    assert final_event.actor == doctor
    assert final_event.actor_role == DOCTOR_ROLE

    # Thread contém a resposta final user do médico: label + motivo de cada
    # procedimento negado (dados reais — o NIR é o destinatário).
    replies = list(case.communication_messages.filter(message_type=MessageType.USER))
    assert len(replies) == 1
    reply = replies[0]
    assert reply.author == doctor
    assert reply.author_role == DOCTOR_ROLE
    assert f"- {ANGIO_LABEL}: sem indicação clínica" in reply.body
    assert f"- {RADIO_LABEL}: risco elevado" in reply.body

    # Flash "decisão registrada" no redirect ao detalhe.
    assert "Decisão registrada" in client.get(detail_url).content.decode()


@pytest.mark.django_db
def test_decide_partial_no_final_reply(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R6: POST parcial (≥1 aprovado) segue o fluxo existente até
    SCHEDULER_REQUESTED — SEM chamada ao fechamento nem resposta final."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE, RADIO_TYPE))
    doctor = user_factory("medico-partial-no-reply", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        _with_token(
            _form_payload(
                {
                    ANGIO_TYPE: ("approved", ""),
                    RADIO_TYPE: ("denied", "via de acesso inviável"),
                }
            ),
            _claim_token(case, doctor),
        ),
    )

    assert response.status_code == 302
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    event_types = [event.event_type for event in case.events.order_by("id")]
    assert f"CASE_STATUS_{CaseStatus.FINAL_REPLY_POSTED}" not in event_types
    assert not case.communication_messages.filter(message_type=MessageType.USER).exists()


@pytest.mark.django_db
def test_decide_closure_error_no_500(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6: falha do fechamento (``ValueError`` simulado) → mensagem de erro +
    redirect ao detalhe, nunca 500; caso permanece ``DOCTOR_DENIED``."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE, RADIO_TYPE))
    doctor = user_factory("medico-closure-error", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    def _failed_closure(case_: Case, *, user: User, role: str) -> None:
        del case_, user, role
        raise ValueError("falha simulada na publicação da resposta final")

    monkeypatch.setattr(doctor_views, "post_doctor_denial_reply", _failed_closure)
    detail_url = reverse("doctor:case_detail", args=[case.case_id])

    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        _with_token(
            _form_payload(
                {
                    ANGIO_TYPE: ("denied", "sem indicação clínica"),
                    RADIO_TYPE: ("denied", "risco elevado"),
                }
            ),
            _claim_token(case, doctor),
        ),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    # Decisão registrada; fechamento falhou sem qualquer efeito.
    assert case.status == CaseStatus.DOCTOR_DENIED
    rows = _rows_by_type(case)
    assert rows[ANGIO_TYPE].doctor_disposition == DoctorDisposition.DENIED
    assert rows[ANGIO_TYPE].doctor_reason == "sem indicação clínica"
    assert rows[RADIO_TYPE].doctor_disposition == DoctorDisposition.DENIED
    assert rows[RADIO_TYPE].doctor_reason == "risco elevado"
    assert not case.events.filter(
        event_type=f"CASE_STATUS_{CaseStatus.FINAL_REPLY_POSTED}"
    ).exists()
    assert not case.communication_messages.filter(message_type=MessageType.USER).exists()
    # Mensagem de erro no redirect ao detalhe — nunca 500.
    body = client.get(detail_url).content.decode()
    assert "Decisão registrada" in body
    assert "não pôde ser publicada" in body


@pytest.mark.django_db
def test_submit_mixed_accepts(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3: POST misto (≥1 aprovado) encadeia até SCHEDULER_REQUESTED."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE, RADIO_TYPE))
    doctor = user_factory("medico-mixed", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        _with_token(
            _form_payload(
                {
                    ANGIO_TYPE: ("approved", ""),
                    RADIO_TYPE: ("denied", "via de acesso inviável"),
                }
            ),
            _claim_token(case, doctor),
        ),
    )

    assert response.status_code == 302
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    rows = _rows_by_type(case)
    assert rows[ANGIO_TYPE].doctor_disposition == DoctorDisposition.APPROVED
    assert rows[ANGIO_TYPE].doctor_reason == ""
    assert rows[RADIO_TYPE].doctor_disposition == DoctorDisposition.DENIED
    assert rows[RADIO_TYPE].doctor_reason == "via de acesso inviável"
    event_types = [event.event_type for event in case.events.order_by("id")]
    assert event_types[-4:] == [
        CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED,
        f"CASE_STATUS_{CaseStatus.DOCTOR_ACCEPTED}",
        f"CASE_STATUS_{CaseStatus.SCHEDULER_REQUESTED}",
        CaseEventType.CASE_LOCK_RELEASED,
    ]


@pytest.mark.django_db
def test_submit_forbidden_subtype_403_no_write(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    assign_specialties: Callable[[User, Sequence[str]], None],
) -> None:
    """R3: POST com subtipo fora do caso → 403 SEM qualquer escrita."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    cardio_doctor = user_factory("doc-cardio-decide-post", (DOCTOR_ROLE,))
    assign_specialties(cardio_doctor, ("cardio",))
    _login(client, cardio_doctor, DOCTOR_ROLE)

    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        _form_payload({ANGIO_TYPE: ("denied", "sem indicação")}),
    )

    assert response.status_code == 403
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert not case.events.filter(event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED).exists()
    row = case.procedures.get(procedure_type=ANGIO_TYPE)
    assert row.doctor_disposition == DoctorDisposition.PENDING
    assert row.doctor_reason == ""
    assert row.doctor_decided_at is None


@pytest.mark.django_db
def test_submit_invalid_form_rerenders_with_errors(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1/R3: POST inválido re-renderiza o form com erros, sem efeito no caso."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE, RADIO_TYPE))
    doctor = user_factory("medico-invalido", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        _with_token(
            _form_payload({ANGIO_TYPE: ("denied", ""), RADIO_TYPE: ("approved", "")}),
            _claim_token(case, doctor),
        ),
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert ANGIO_LABEL in body
    assert "Informe o motivo da negativa de" in body
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert not case.events.filter(event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED).exists()


# ── R4: submissão concorrente / estado inválido ───────────────────────────


@pytest.mark.django_db
def test_submit_wrong_state_no_500(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R4: POST de caso fora de AWAITING_DOCTOR → mensagem + redirect, nunca 500."""
    owner = user_factory("dono-wrong-state", (NIR_ROLE,))
    case = _make_awaiting_case(owner, (ANGIO_TYPE, RADIO_TYPE))
    first_doctor = user_factory("medico-wrong-state-1", (DOCTOR_ROLE,))
    record_doctor_procedure_decisions(
        case,
        {
            ANGIO_TYPE: ("denied", "sem indicação clínica"),
            RADIO_TYPE: ("denied", "risco elevado"),
        },
        user=first_doctor,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    events_before = case.events.count()
    second_doctor = user_factory("medico-wrong-state-2", (DOCTOR_ROLE,))
    _login(client, second_doctor, DOCTOR_ROLE)
    detail_url = reverse("doctor:case_detail", args=[case.case_id])

    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        _form_payload(
            {
                ANGIO_TYPE: ("approved", ""),
                RADIO_TYPE: ("denied", "fístula inviável"),
            }
        ),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    # Nenhuma escrita nova (nem parcial): status, rows e trilha intactos.
    assert case.status == CaseStatus.DOCTOR_DENIED
    assert case.events.count() == events_before
    rows = _rows_by_type(case)
    assert rows[ANGIO_TYPE].doctor_disposition == DoctorDisposition.DENIED
    assert rows[ANGIO_TYPE].doctor_reason == "sem indicação clínica"
    assert "já decidido por outro médico" in client.get(detail_url).content.decode()


def _race_then_fail_second_submit(
    case: Case,
    decisions: Mapping[str, tuple[str, str]],
    *,
    user: User | None,
    role: str | None,
) -> None:
    """Simula a perda da corrida no POST: a 1ª chamada decide; a 2ª concorrente
    (mesma transação) levanta ``TransitionNotAllowed`` — o serviço desfaz tudo
    da chamada perdedora (atomic) e a view traduz sem 500."""
    record_doctor_procedure_decisions(case, decisions, user=user, role=role)
    record_doctor_procedure_decisions(case, decisions, user=user, role=role)


@pytest.mark.django_db
def test_concurrent_submit_no_partial_write(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4/D4: submit concorrente perde a corrida → sem efeito parcial + sem 500."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE, RADIO_TYPE))
    doctor = user_factory("medico-race", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    monkeypatch.setattr(
        doctor_views,
        "record_doctor_procedure_decisions",
        _race_then_fail_second_submit,
    )
    detail_url = reverse("doctor:case_detail", args=[case.case_id])

    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        _with_token(
            _form_payload(
                {
                    ANGIO_TYPE: ("denied", "sem indicação clínica"),
                    RADIO_TYPE: ("denied", "risco elevado"),
                }
            ),
            _claim_token(case, doctor),
        ),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    # Chamada perdedora totalmente desfeita: UMA decisão registrada por row,
    # evento único e disposições consistentes — sem escrita parcial.
    assert case.events.filter(event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED).count() == 1
    rows = _rows_by_type(case)
    for procedure_type, reason in (
        (ANGIO_TYPE, "sem indicação clínica"),
        (RADIO_TYPE, "risco elevado"),
    ):
        assert rows[procedure_type].doctor_disposition == DoctorDisposition.DENIED
        assert rows[procedure_type].doctor_reason == reason
        assert rows[procedure_type].doctor_decided_at is not None
    assert "já decidido por outro médico" in client.get(detail_url).content.decode()


# ── R5: detalhe read-only pós-decisão ─────────────────────────────────────


@pytest.mark.django_db
def test_presenter_decision_section_after_decision(
    user_factory: Callable[..., User],
) -> None:
    """R5: presenter expõe decisões por procedimento, evento e trilha pós-decisão."""
    owner = user_factory("dono-decided-presenter", (NIR_ROLE,))
    decider = user_factory("medico-decided-presenter", (DOCTOR_ROLE,))
    case = _make_awaiting_case(owner, (ANGIO_TYPE, RADIO_TYPE))
    record_doctor_procedure_decisions(
        case,
        {
            ANGIO_TYPE: ("denied", "sem indicação clínica"),
            RADIO_TYPE: ("approved", ""),
        },
        user=decider,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()

    context = cast(dict[str, Any], build_case_detail_context(case))

    assert context["can_decide"] is False
    # Decisão por procedimento: disposição + motivo + doctor_decided_at.
    declared = {item["procedure_type"]: item for item in context["declared"]}
    assert declared[ANGIO_TYPE]["reason"] == "sem indicação clínica"
    assert declared[ANGIO_TYPE]["decided_at"]
    assert declared[RADIO_TYPE]["reason"] == ""
    assert declared[RADIO_TYPE]["decided_at"]
    # Ator/data do evento CASE_DOCTOR_DECISIONS_RECORDED.
    assert context["decision_event"] is not None
    decision_event = context["decision_event"]
    assert decision_event["actor_display"] == decider.username
    assert decision_event["actor_role"] == DOCTOR_ROLE
    assert decision_event["timestamp"]
    # Slice 003 (painel-ats-parity): a trilha saiu do detalhe médico — o
    # presenter não monta mais ``events`` (vive no painel).
    assert "events" not in context
    # Os eventos continuam na TRILHA do caso (append-only), com a decisão e o
    # encadeamento da decisão mista (≥1 aprovado) → SCHEDULER_REQUESTED.
    event_types = [event.event_type for event in case.events.order_by("id")]
    assert CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED in event_types
    assert f"CASE_STATUS_{CaseStatus.SCHEDULER_REQUESTED}" in event_types


@pytest.mark.django_db
def test_decided_detail_readonly(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R5: detalhe pós-decisão exibe decisões/ator/trilha — sem formulário."""
    owner = user_factory("dono-decided-detail", (NIR_ROLE,))
    case = _make_awaiting_case(owner, (ANGIO_TYPE, RADIO_TYPE))
    decider = user_factory("medico-decided-detail", (DOCTOR_ROLE,))
    record_doctor_procedure_decisions(
        case,
        {
            ANGIO_TYPE: ("denied", "sem indicação clínica"),
            RADIO_TYPE: ("denied", "risco elevado"),
        },
        user=decider,
        role=DOCTOR_ROLE,
    )
    viewer = user_factory("geral-decided-detail", (DOCTOR_ROLE,))
    _login(client, viewer, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    # Decisões por procedimento: disposição, motivo e data decidida.
    assert "Negado pelo médico" in body
    assert "Decisões registradas" in body
    assert ANGIO_LABEL in body
    assert RADIO_LABEL in body
    assert "sem indicação clínica" in body
    assert "risco elevado" in body
    # Ator/data do evento de decisão.
    assert decider.username in body
    # A trilha de eventos saiu do detalhe médico (vive no painel).
    assert "Trilha de eventos" not in body
    assert "Decisões médicas por procedimento registradas" not in body
    # Read-only: nenhum formulário/campo de decisão fora de AWAITING_DOCTOR
    # (o único <form> da página é o de logout do base.html — sem ação de
    # decisão nem campos dinâmicos do DoctorDecisionForm).
    assert 'name="procedure_' not in body
    assert "/decide/" not in body
    assert "Registrar decisão" not in body


@pytest.mark.django_db
def test_submit_without_declared_procedures_redirects_no_500(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R4 (defensivo): POST sem procedimentos declarados -> redirect, nunca 500.

    A produção não deve chegar aqui (o intake declara >=1 tipo na criação),
    mas o caminho existe no protocolo e precisa fechar como os demais erros
    de estado: mensagem + redirect, sem escrita.
    """
    case = _create_case_with_declared(nir_user, ())
    _advance_to_awaiting_doctor(case)
    doctor = user_factory("doc-vazio-post", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.post(reverse("doctor:case_decide", args=[case.case_id]))

    assert response.status_code == 302
    assert response["Location"] == reverse("doctor:case_detail", args=[case.case_id])
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR


# ── Slice 004 (attachment-processing-ocr): R4 — mismatch não bloqueia ──────


@pytest.mark.django_db
def test_decide_with_mismatch_attachment_ok(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R4: decisão (POST) em caso com anexo mismatch segue o fluxo normal.

    O alerta consultivo do mismatch nunca bloqueia nem descarta: o fluxo do
    change 07 (form/serviço/encadeamento) permanece intocado e o anexo segue
    preservado após a decisão.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    attachment = _add_mismatch_attachment(case, nir_user)
    doctor = user_factory("medico-mismatch-decide", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        _with_token(_form_payload({ANGIO_TYPE: ("approved", "")}), _claim_token(case, doctor)),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("doctor:case_detail", args=[case.case_id])
    case.refresh_from_db()
    # Decisão registrada normalmente (≥1 aprovado → SCHEDULER_REQUESTED).
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    row = case.procedures.get(procedure_type=ANGIO_TYPE)
    assert row.doctor_disposition == DoctorDisposition.APPROVED
    assert row.doctor_reason == ""
    # Anexo preservado e intocado (alerta consultivo, sem descarte/bloqueio).
    attachment.refresh_from_db()
    assert attachment.status == AttachmentStatus.PROCESSED
    assert attachment.patient_match == PatientMatch.MISMATCH


# ── case-work-lock slice 001: página de trabalho única (R2–R5) ─────────────

# Ordem de leitura da página de trabalho (design D1): quadro clínico →
# artefatos originais → síntese da automação → formulário. «Identificação do
# paciente» é o ``h1`` do primeiro card e é conferida à parte.
_WORK_PAGE_CARD_ORDER = (
    "Procedimentos do caso",
    "Sumário clínico",
    "Estrutura extraída",
    "Prior-case",
    "Documentos",
    "Anexos",
    "Requisitos gerais acionáveis",
    "Recomendação agregada do pipeline",
    "Alertas consultivos",
)


def _card_title_index(body: str, title: str) -> int:
    """Índice do título (``h2``) do card na página de trabalho renderizada."""
    marker = f'<h2 class="h5 mb-0">{title}</h2>'
    index = body.find(marker)
    assert index != -1, f"card «{title}» ausente na página de trabalho"
    return index


def _make_work_page_case(created_by: User, uploaded_by: User) -> Case:
    """Caso em ``AWAITING_DOCTOR`` com os cards condicionais renderizáveis.

    «Anexos» depende de um anexo processado e «Recomendação agregada do
    pipeline» da sugestão do LLM2 (chave ``aggregate``): sem os dois, o teste
    de ordem não poderia exigir a lista completa de cards de leitura.
    """
    case = _make_awaiting_case(created_by, (ANGIO_TYPE,))
    _add_mismatch_attachment(case, uploaded_by)
    case.suggested_action = {
        "aggregate": {
            "suggestion": "recusar",
            "motivos": ["plaquetas 80000 abaixo do mínimo da seção S1"],
        },
    }
    case.save(update_fields=["suggested_action"])
    return case


def _add_case_document(case: Case, uploaded_by: User, *, position: int) -> CaseDocument:
    """Documento PDF fake do caso (card «Documentos» da página de trabalho)."""
    uploaded = SimpleUploadedFile(
        f"relatorio-{position}.pdf",
        f"%PDF-1.4 conteudo do pdf {position}".encode(),
        content_type="application/pdf",
    )
    return CaseDocument.objects.create(
        case=case,
        file=uploaded,
        position=position,
        original_filename=f"relatorio-sesab-{position}.pdf",
        content_type="application/pdf",
        size_bytes=len(uploaded.read()),
        uploaded_by=uploaded_by,
    )


@pytest.mark.django_db
def test_work_page_lists_case_documents(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/D1: a página de trabalho lista os documentos do caso.

    O card «Documentos» vem do partial compartilhado: sem a chave de contexto o
    médico veria «Nenhum documento neste caso.» mesmo com PDFs no caso.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    document = _add_case_document(case, nir_user, position=1)
    doctor = user_factory("medico-documentos", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()

    assert document.original_filename in body
    assert reverse("doctor:case_pdf", args=[case.case_id, document.position]) in body
    assert "Nenhum documento neste caso." not in body


@pytest.mark.django_db
def test_work_page_orders_reading_then_form(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/D1: leitura completa em ordem única, formulário como ÚLTIMO bloco."""
    case = _make_work_page_case(nir_user, nir_user)
    doctor = user_factory("medico-pagina-unica", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_decide", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    indices = [_card_title_index(body, title) for title in _WORK_PAGE_CARD_ORDER]
    assert indices == sorted(indices)
    # Identificação é o primeiro card da página (h1).
    assert body.index("Identificação do paciente") < indices[0]
    # O formulário fecha a sequência, depois de TODOS os cards de leitura.
    assert indices[-1] < body.index('id="doctor-decision-form"')


@pytest.mark.django_db
def test_work_page_has_no_recorded_decisions_block(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3/D1: «Decisões registradas» fica fora da página de trabalho."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-sem-historico", (DOCTOR_ROLE,))
    # Evento de decisão no caso SEM tirá-lo de AWAITING_DOCTOR: prova que a
    # ausência do bloco na página de trabalho é do desenho, não da falta de
    # evento — o MESMO caso exibe o bloco no detalhe read-only.
    CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED,
        actor_type=ActorType.USER,
        actor=doctor,
        actor_role=DOCTOR_ROLE,
        payload={"decisions": {ANGIO_TYPE: "approved"}},
    )
    _login(client, doctor, DOCTOR_ROLE)

    detail_body = client.get(reverse("doctor:case_detail", args=[case.case_id])).content.decode()
    assert "Decisões registradas" in detail_body

    work_body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()
    assert "Decisões registradas" not in work_body


@pytest.mark.django_db
def test_work_page_has_form_anchor(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R4/D1: âncora «Ir para o formulário de decisão» no topo → form no fim."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-ancora", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()

    anchor = body.find('href="#doctor-decision-form"')
    form = body.find('id="doctor-decision-form"')
    # ``find`` devolve -1 quando não acha — exigir as duas posições reais.
    assert 0 < anchor < form
    assert "Ir para o formulário de decisão" in body


@pytest.mark.django_db
def test_work_page_footer_buttons(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R5/D3: [Registrar decisão] e «Sair sem decidir» juntos ao fim do form.

    Slice 003 (R4): «Sair sem decidir» deixou de ser link e virou mini-form
    POST que libera a reserva server-side; a ordem (formulário da decisão →
    submit → sair) e a adjacência dos dois controles seguem o desenho D1/D3.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-botoes", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()

    form = body.index('id="doctor-decision-form"')
    closed = body.index("</form>", form)
    submit = body.index(
        '<button type="submit" form="doctor-decision-form" id="btn-submit" class="btn btn-primary">'
    )
    leave = body.index(">Sair sem decidir</button>")
    assert 0 < form < closed < submit < leave
    leave_form = body.rindex("<form ", 0, leave)
    assert f'action="{reverse("doctor:case_leave", args=[case.case_id])}"' in body[leave_form:leave]


# ── case-work-lock slice 002: reserva do caso (R4–R7) ─────────────────────


@pytest.mark.django_db
def test_open_work_page_claims_case(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R4/D2: GET da página de trabalho reserva o caso e embute o token.

    Claim com contexto ``doctor_decision``, papel ``doctor`` e a lease do
    médico (``CASE_LOCK_LEASE_SECONDS_DOCTOR`` — 1h), com evento na trilha.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-claim-get", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_decide", args=[case.case_id]))

    assert response.status_code == 200
    case.refresh_from_db()
    assert case.locked_by == doctor
    assert case.lock_context == LOCK_CONTEXT
    assert case.lock_role == DOCTOR_ROLE
    assert case.lock_token is not None
    assert case.locked_at is not None
    assert case.locked_until is not None and case.locked_until > timezone.now()
    # Lease do contexto doctor_decision (D2): 1h, não o default de 300s.
    assert (case.locked_until - case.locked_at).total_seconds() == 3600
    body = response.content.decode()
    assert f'name="lock_token" value="{case.lock_token}"' in body
    assert CaseEventType.CASE_LOCK_CLAIMED in [
        event.event_type for event in case.events.order_by("id")
    ]


@pytest.mark.django_db
def test_other_doctor_blocked_by_active_lock(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R5/D2: reserva ativa de outro médico barra a abertura (warning + fila).

    A reserva do portador fica INTACTA e a fila mostra o card como reservado.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    holder = user_factory("medico-reserva-a", (DOCTOR_ROLE,))
    blocked = user_factory("medico-reserva-b", (DOCTOR_ROLE,))
    lock = claim_case_lock(case, user=holder, context=LOCK_CONTEXT, role=DOCTOR_ROLE)
    events_before = case.events.count()
    _login(client, blocked, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_decide", args=[case.case_id]))

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("doctor:queue")
    case.refresh_from_db()
    assert case.lock_token == lock.token
    assert case.locked_by == holder
    assert case.events.count() == events_before
    # Mensagem de aviso com o portador (renderizada na fila após o redirect).
    body = client.get(reverse("doctor:queue")).content.decode()
    assert "está reservado por" in body
    assert holder.display_name in body


@pytest.mark.django_db
def test_reopen_renews_own_lock(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R5/D2: o mesmo médico reabrindo renova a reserva (token novo, sem barrar)."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-reserva-propria", (DOCTOR_ROLE,))
    first = claim_case_lock(case, user=doctor, context=LOCK_CONTEXT, role=DOCTOR_ROLE)
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_decide", args=[case.case_id]))

    assert response.status_code == 200
    case.refresh_from_db()
    assert case.locked_by == doctor
    assert case.lock_token is not None and case.lock_token != first.token
    assert f'name="lock_token" value="{case.lock_token}"' in response.content.decode()


@pytest.mark.django_db
def test_submit_without_valid_token_persists_nothing(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R6/D2: POST sem ``lock_token`` → lock_error visível e NADA persistido."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE, RADIO_TYPE))
    doctor = user_factory("medico-sem-token", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        _form_payload({ANGIO_TYPE: ("approved", ""), RADIO_TYPE: ("approved", "")}),
    )

    assert response.status_code == 200
    assert LOCK_LOST_TEXT in response.content.decode()
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert not case.events.filter(event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED).exists()
    for procedure_type in (ANGIO_TYPE, RADIO_TYPE):
        disposition, reason, decided_at = _row_state(case, procedure_type)
        assert disposition == DoctorDisposition.PENDING
        assert reason == ""
        assert decided_at is None


@pytest.mark.django_db
def test_submit_with_stale_token_persists_nothing(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R6/D2: token que não é o VIGENTE (renovado) → lock_error e nada persistido.

    Reabrir a página renova a reserva com token novo (R5): um POST de uma aba
    antiga carrega o token anterior e não tem mais posse.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-token-renovado", (DOCTOR_ROLE,))
    stale = claim_case_lock(case, user=doctor, context=LOCK_CONTEXT, role=DOCTOR_ROLE)
    current = claim_case_lock(case, user=doctor, context=LOCK_CONTEXT, role=DOCTOR_ROLE)
    assert current.token != stale.token
    _login(client, doctor, DOCTOR_ROLE)

    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        _with_token(_form_payload({ANGIO_TYPE: ("approved", "")}), str(stale.token)),
    )

    assert response.status_code == 200
    assert LOCK_LOST_TEXT in response.content.decode()
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    disposition, reason, decided_at = _row_state(case, ANGIO_TYPE)
    assert (disposition, reason, decided_at) == (DoctorDisposition.PENDING, "", None)


@pytest.mark.django_db
def test_submit_after_lease_expired_in_window_persists_nothing(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R7/D2 (janela): lease vencida ENTRE a abertura e o POST → nada persiste.

    A posse é validada sob row lock na MESMA transação da escrita: expirar a
    lease depois do render (aba aberta sem heartbeat, slice 003) não pode
    deixar a decisão passar.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-janela-lease", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()
    token = _extract_lock_token(body)
    Case.objects.filter(pk=case.pk).update(locked_until=timezone.now() - timedelta(seconds=1))

    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        _with_token(_form_payload({ANGIO_TYPE: ("approved", "")}), token),
    )

    assert response.status_code == 200
    assert LOCK_LOST_TEXT in response.content.decode()
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    assert not case.events.filter(event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED).exists()
    disposition, reason, decided_at = _row_state(case, ANGIO_TYPE)
    assert (disposition, reason, decided_at) == (DoctorDisposition.PENDING, "", None)


@pytest.mark.django_db
def test_decision_releases_lock(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R7/D2: decisão com posse válida libera a reserva na MESMA transação."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-libera-reserva", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()
    token = _extract_lock_token(body)

    response = client.post(
        reverse("doctor:case_decide", args=[case.case_id]),
        _with_token(_form_payload({ANGIO_TYPE: ("approved", "")}), token),
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("doctor:case_detail", args=[case.case_id])
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    assert case.locked_by is None
    assert case.locked_at is None
    assert case.locked_until is None
    assert case.lock_token is None
    assert case.lock_context == ""
    assert case.lock_role == ""
    release_events = case.events.filter(event_type=CaseEventType.CASE_LOCK_RELEASED)
    assert release_events.count() == 1
    assert release_events.get().payload["context"] == LOCK_CONTEXT
    row = case.procedures.get(procedure_type=ANGIO_TYPE)
    assert row.doctor_disposition == DoctorDisposition.APPROVED


# ── case-work-lock slice 003: heartbeat e «sair sem decidir» (R1–R7) ──────

# Script do heartbeat (R6) e seus marcos obrigatórios: o projeto não tem
# runner de JavaScript — o contrato do arquivo (atividade, grace, submit
# guard, release best-effort) é verificado pelo conteúdo, e o carregamento
# pelo render da página de trabalho.
WORK_LOCK_SCRIPT = Path(settings.BASE_DIR) / "static" / "js" / "work_lock.js"


@pytest.mark.django_db
def test_lock_renew_endpoint_extends_lease(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1/D3: o heartbeat renova a lease do portador e devolve ``locked_until``.

    A lease é encurtada antes da chamada para provar a renovação real (não o
    eco do valor gravado no claim): o endpoint responde JSON de sucesso com o
    novo vencimento e a trilha ganha ``CASE_LOCK_RENEWED``.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-heartbeat", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()
    token = _extract_lock_token(body)
    shortened = timezone.now() + timedelta(minutes=5)
    Case.objects.filter(pk=case.pk).update(locked_until=shortened)

    response = client.post(
        reverse("doctor:lock_renew", args=[case.case_id]),
        {"lock_token": token},
    )

    assert response.status_code == 200
    payload = cast(dict[str, Any], response.json())
    assert payload["success"] is True
    renewed_until = datetime.fromisoformat(cast(str, payload["locked_until"]))
    assert renewed_until > shortened + timedelta(minutes=30)
    case.refresh_from_db()
    assert case.locked_until is not None and case.locked_until > shortened
    assert (case.locked_until - timezone.now()).total_seconds() > 3000
    assert case.events.filter(event_type=CaseEventType.CASE_LOCK_RENEWED).exists()


@pytest.mark.django_db
def test_lock_renew_endpoint_without_token_fails(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1/D3: renovação sem ``lock_token`` responde falha e não toca a lease."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-heartbeat-sem-token", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    claim = claim_case_lock(case, user=doctor, context=LOCK_CONTEXT, role=DOCTOR_ROLE)

    response = client.post(reverse("doctor:lock_renew", args=[case.case_id]), {})

    assert response.status_code == 200
    payload = cast(dict[str, Any], response.json())
    assert payload["success"] is False
    case.refresh_from_db()
    assert case.lock_token == claim.token
    assert case.locked_until == claim.locked_until
    assert not case.events.filter(event_type=CaseEventType.CASE_LOCK_RENEWED).exists()


@pytest.mark.django_db
def test_lock_renew_endpoint_with_wrong_token_fails(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1/D3: a posse é do token, não do usuário — token errado não renova.

    Cobre também o token malformado (não-UUID): tratado como "sem posse" e
    respondido em JSON — nunca 500.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-heartbeat-token-errado", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    claim = claim_case_lock(case, user=doctor, context=LOCK_CONTEXT, role=DOCTOR_ROLE)

    response = client.post(
        reverse("doctor:lock_renew", args=[case.case_id]),
        {"lock_token": str(uuid.uuid4())},
    )

    assert response.status_code == 200
    payload = cast(dict[str, Any], response.json())
    assert payload["success"] is False
    assert payload["error"]
    case.refresh_from_db()
    assert case.lock_token == claim.token
    assert case.locked_until == claim.locked_until

    malformed = client.post(
        reverse("doctor:lock_renew", args=[case.case_id]),
        {"lock_token": "nao-e-uuid"},
    )
    assert malformed.status_code == 200
    assert cast(dict[str, Any], malformed.json())["success"] is False
    case.refresh_from_db()
    assert case.lock_token == claim.token


@pytest.mark.django_db
def test_lock_release_endpoint_clears_lock_and_is_idempotent(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/D3: release explícito limpa a reserva; repetir devolve falha sem erro.

    O segundo POST (token já consumido) é o cenário do release best-effort
    disparado em navegação depois do POST da decisão: nunca 500.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-release-endpoint", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()
    token = _extract_lock_token(body)

    first = client.post(reverse("doctor:lock_release", args=[case.case_id]), {"lock_token": token})
    assert first.status_code == 200
    assert cast(dict[str, Any], first.json())["success"] is True
    case.refresh_from_db()
    assert case.locked_by is None
    assert case.lock_token is None
    assert case.lock_context == ""
    assert case.locked_until is None
    release_events = case.events.filter(event_type=CaseEventType.CASE_LOCK_RELEASED)
    assert release_events.count() == 1
    assert release_events.get().payload["context"] == LOCK_CONTEXT

    second = client.post(reverse("doctor:lock_release", args=[case.case_id]), {"lock_token": token})
    assert second.status_code == 200
    assert cast(dict[str, Any], second.json())["success"] is False
    assert case.events.filter(event_type=CaseEventType.CASE_LOCK_RELEASED).count() == 1


@pytest.mark.django_db
def test_case_leave_releases_and_redirects(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3/D3: «Sair sem decidir» libera a reserva na hora e volta à fila.

    A liberação é server-side (não depende do JavaScript): o caso fica
    disponível imediatamente e a trilha registra o release.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-sai-sem-decidir", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()
    token = _extract_lock_token(body)

    response = client.post(
        reverse("doctor:case_leave", args=[case.case_id]),
        {"lock_token": token},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("doctor:queue")
    case.refresh_from_db()
    assert case.locked_by is None
    assert case.lock_token is None
    assert case.locked_until is None
    release_events = case.events.filter(event_type=CaseEventType.CASE_LOCK_RELEASED)
    assert release_events.count() == 1
    assert release_events.get().payload["context"] == LOCK_CONTEXT
    queue_body = client.get(reverse("doctor:queue")).content.decode()
    assert "Reserva liberada" in queue_body


@pytest.mark.django_db
def test_case_leave_without_token_only_redirects(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3/D3: sair sem token é idempotente — redirect à fila, reserva intacta."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-sai-sem-token", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    claim = claim_case_lock(case, user=doctor, context=LOCK_CONTEXT, role=DOCTOR_ROLE)

    response = client.post(reverse("doctor:case_leave", args=[case.case_id]), {})

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("doctor:queue")
    case.refresh_from_db()
    assert case.lock_token == claim.token
    assert case.locked_by == doctor
    assert not case.events.filter(event_type=CaseEventType.CASE_LOCK_RELEASED).exists()


@pytest.mark.django_db
def test_leave_button_is_form_post(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R4/D3: «Sair sem decidir» é mini-form POST (não link) com ``lock_token``."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-botao-sair", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()

    action = reverse("doctor:case_leave", args=[case.case_id])
    form = body.find(f'<form method="post" action="{action}"')
    label = body.find(">Sair sem decidir</button>")
    # ``find`` devolve -1 quando não acha — exigir as duas posições reais.
    assert 0 < form < label
    leave_form = body[form:label]
    assert 'name="lock_token"' in leave_form
    # CSRF próprio do mini-form (não herda o do formulário de decisão).
    assert 'name="csrfmiddlewaretoken"' in leave_form
    # O link antigo (slice 001) não existe mais no formulário.
    assert ">Sair sem decidir</a>" not in body


@pytest.mark.django_db
def test_work_lock_config_rendered(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R5/D3: a página de trabalho exporta a config do heartbeat + o aviso."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-config-lock", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()
    token = _extract_lock_token(body)

    for marker in (
        'id="work-lock-config"',
        "data-work-lock-config",
        f'data-renew-url="{reverse("doctor:lock_renew", args=[case.case_id])}"',
        f'data-release-url="{reverse("doctor:lock_release", args=[case.case_id])}"',
        f'data-lock-token="{token}"',
        'data-heartbeat-ms="60000"',
        'data-grace-ms="240000"',
    ):
        assert 0 < body.find(marker), f"config do work lock ausente: {marker}"
    warning = body.find('id="work-lock-warning"')
    assert 0 < warning
    assert 'style="display:none;"' in body[warning : warning + 200]


@pytest.mark.django_db
def test_work_page_loads_work_lock_script(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R6/D3: a página de trabalho carrega o script do heartbeat.

    A asserção é pela TAG do script (e não pela menção ao caminho, que também
    existe em comentário de template): ``{# … #}`` multilinha não é comentário
    no Django e vazaria o texto para a página — o mesmo teste guarda isso.
    """
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-carrega-script", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()

    assert 0 < body.find('<script src="/static/js/work_lock.js"')
    assert "{#" not in body


def test_work_lock_script_implements_heartbeat_marks() -> None:
    """R6/D3: o script cobre atividade, grace, heartbeat, guard e release.

    Sem runner de JavaScript no projeto, os marcos do contrato do arquivo são
    verificados no conteúdo (adaptação do ATS Web, change case-work-lock D3).
    """
    source = WORK_LOCK_SCRIPT.read_text(encoding="utf-8")

    for marker in (
        "data-heartbeat-ms",
        "data-grace-ms",
        "mousemove",
        "keydown",
        "touchstart",
        "setInterval(sendRenew",
        "hasRecentActivity",
        "HMD_WORK_LOCK_SUBMITTING",
        'input[name="lock_token"]',
        "keepalive",
        "pagehide",
        "work-lock-warning",
    ):
        assert marker in source, f"marco ausente no script do work lock: {marker}"
    # Sem dependências externas (script vanilla, como o do ATS Web).
    assert "import " not in source
    assert "require(" not in source


# ── case-detail-human-reference (slice 005): referência humana no
#    title/breadcrumb da página de trabalho (R1) ────────────────────────────


def _visible_text(body: str) -> str:
    """HTML sem os atributos de rota (``href``/``action``/``data-*-url``).

    O uid permanece como chave técnica das URLs (D5 do design); remover os
    atributos permite pinar que ele NÃO aparece em texto visível.
    """
    return re.sub(r'(?:href|action|data-[a-z-]*url)="[^"]*"', "", body)


@pytest.mark.django_db
def test_decide_title_and_breadcrumb_use_human_reference(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: title, breadcrumb e link do breadcrumb usam «Caso <nº>»."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    case.agency_record_number = "5040778"
    case.save(update_fields=["agency_record_number"])
    doctor = user_factory("medico-referencia-decide", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()

    assert "— Caso 5040778: decisão médica" in body
    assert f'href="{reverse("doctor:case_detail", args=[case.case_id])}">Caso 5040778</a>' in body
    assert f"Caso {case.case_id}" not in body
    assert str(case.case_id) not in _visible_text(body)
    assert str(case.case_id) in body  # chave técnica das URLs (D5)
