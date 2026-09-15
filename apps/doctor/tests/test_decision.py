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

from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.attachments.models import AttachmentStatus, CaseAttachment, ExtractionMethod, PatientMatch
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseProcedure, CaseStatus, DoctorDisposition, MessageType
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.doctor import views as doctor_views
from apps.doctor.forms import DoctorDecisionForm
from apps.doctor.presenters import build_case_detail_context

DOCTOR_ROLE = "doctor"
NIR_ROLE = "nir"
SYSTEM_ROLE = "system"

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
    decide_url = reverse("doctor:case_decide", args=[case.case_id])

    response = client.post(
        decide_url,
        _form_payload(
            {
                ANGIO_TYPE: ("denied", "sem indicação clínica"),
                RADIO_TYPE: ("denied", "risco elevado"),
            }
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
    # Encadeamento do wiring: decisão → DOCTOR_DENIED → FINAL_REPLY_POSTED.
    event_types = [event.event_type for event in case.events.order_by("id")]
    assert event_types[-3:] == [
        CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED,
        f"CASE_STATUS_{CaseStatus.DOCTOR_DENIED}",
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
        _form_payload(
            {
                ANGIO_TYPE: ("approved", ""),
                RADIO_TYPE: ("denied", "via de acesso inviável"),
            }
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
        _form_payload(
            {
                ANGIO_TYPE: ("denied", "sem indicação clínica"),
                RADIO_TYPE: ("denied", "risco elevado"),
            }
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
        _form_payload(
            {
                ANGIO_TYPE: ("approved", ""),
                RADIO_TYPE: ("denied", "via de acesso inviável"),
            }
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
    assert event_types[-3:] == [
        CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED,
        f"CASE_STATUS_{CaseStatus.DOCTOR_ACCEPTED}",
        f"CASE_STATUS_{CaseStatus.SCHEDULER_REQUESTED}",
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
        _form_payload({ANGIO_TYPE: ("denied", ""), RADIO_TYPE: ("approved", "")}),
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
        _form_payload(
            {
                ANGIO_TYPE: ("denied", "sem indicação clínica"),
                RADIO_TYPE: ("denied", "risco elevado"),
            }
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
        _form_payload({ANGIO_TYPE: ("approved", "")}),
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
