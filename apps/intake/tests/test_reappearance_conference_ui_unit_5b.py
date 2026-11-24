"""Testes TDD da conferência NIR na UI/HTTP (slice 005, unidade 5B, R1–R5).

Cobre:
- R1: GET ``intake:case_detail`` por outro NIR da equipe com candidatos (origem
  comum, divergência de idade, conflito de CNS, múltiplos candidatos), idade
  ``0 a``, ambos PDFs (serve 200 + viewer 200 + ``?next=`` seguro + aviso quando
  ausente por minimização/purga), exatamente 2 botões de resposta e ZERO
  INSERT/UPDATE/DELETE no GET.
- R2: navegar/fechar sem POST não cria vínculo nem bloqueia pipeline/filas; POST
  ``independent`` suprime apenas o par/revisão; alterar fonte/declaração muda a
  revisão, reabre candidato e recusa POST da revisão antiga com 400.
- R3: jornadas HTTP ponta a ponta por NIR não-criador (substituição médica,
  repetição de agendamento com lease/contador preservados, negativa histórica
  tardia pós-LLM2 com exibição no card médico sem reanálise).
- R4: CSRF (403), papel não-NIR (403), papel NIR removido do banco (403),
  ``getlist`` duplicado (400), par forjado/inelegível/forte inequívoco (400),
  revisão obsoleta (400), lease médica ativa → 409 com pendência,
  ``schedule_failed`` → 409 com orientação de suporte, múltiplos candidatos →
  confirmar par 1 não encerra par 2 e tentar par 2 responde 400.
- R5: manual NIR atualizado (duas respostas e ausência de triagem clínica) e
  rollback flag ``CASE_REAPPEARANCE_ENABLED=False`` (esconde conferência,
  recusa POST com 400 e preserva leitura de vínculos já aplicados).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock
from apps.cases.models import (
    ActorType,
    Case,
    CaseDocument,
    CaseEvent,
    CaseProcedure,
    CaseReappearance,
    CaseStatus,
    DoctorDisposition,
    ReappearanceState,
    ResolutionKind,
)
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.cases.reappearance import (
    list_nir_reappearance_candidates,
    revision_digest_for_pair,
)
from apps.doctor.presenters import build_case_detail_context

NEW_MODE = override_settings(CASE_REAPPEARANCE_ENABLED=True, CASE_REAPPEARANCE_WINDOW_DAYS=15)
FLAG_OFF = override_settings(CASE_REAPPEARANCE_ENABLED=False, CASE_REAPPEARANCE_WINDOW_DAYS=15)

SYSTEM_ROLE = "system"
NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SCHEDULER_ROLE = "scheduler"
ANGIO_TYPE = "art_perif"
CAT_TYPE = next(p.procedure_type for p in PROCEDURE_PROFILES if p.procedure_type != ANGIO_TYPE)
_CNS_A = "207139200176053"
_CNS_B = "605932160152243"
_NAME = "Maria da Silva"


def _make_case(
    creator: User,
    *,
    days_ago: int = 0,
    name: str = _NAME,
    number: str = "",
    cns: str = "",
    origin: str = "HGE",
    age: int | None = 50,
    procedure_type: str = ANGIO_TYPE,
    with_doc: bool = False,
) -> Case:
    case = Case.objects.create(
        created_by=creator,
        patient_name=name,
        agency_record_number=number,
        patient_cns=cns,
        origin_unit=origin,
        patient_age=age,
    )
    if days_ago:
        target_dt = timezone.now() - timedelta(days=days_ago)
        Case.objects.filter(pk=case.pk).update(created_at=target_dt)
        case.refresh_from_db()
    if procedure_type:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    if with_doc:
        CaseDocument.objects.create(
            case=case,
            position=1,
            original_filename=f"doc-{case.case_id.hex[:6]}.pdf",
            size_bytes=48,
            uploaded_by=creator,
            file=SimpleUploadedFile(
                f"doc-{case.case_id.hex[:6]}.pdf",
                b"%PDF-1.4 fake conference test",
                content_type="application/pdf",
            ),
        )
    return case


def _advance_to_awaiting_doctor(case: Case) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _advance_to_awaiting_scheduling(case: Case, doctor: User) -> None:
    _advance_to_awaiting_doctor(case)
    record_doctor_procedure_decisions(
        case,
        decisions={ANGIO_TYPE: (DoctorDisposition.APPROVED, "Indicação clínica válida.")},
        user=doctor,
        role=DOCTOR_ROLE,
    )
    case.record_doctor_decision(accepted=True, user=doctor, role=DOCTOR_ROLE)
    case.request_scheduling(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.SCHEDULER_REQUESTED


def _respond_url(case: Case) -> str:
    return reverse("intake:reappearance_respond", args=[case.case_id])


def _detail_url(case: Case) -> str:
    return reverse("intake:case_detail", args=[case.case_id])


def _login_with_role(client: Client, user: User, role: str) -> None:
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


# ── R1: Comparação read-only, 0 a, PDFs, 2 botões exatos, zero writes ──────


@NEW_MODE
@pytest.mark.django_db
def test_get_case_detail_renders_conference_for_non_creator_nir_without_db_writes(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    other_nir = user_factory("nir-equipe-r1", NIR_ROLE)

    # Prior com idade 0 (deve renderizar "0 a"), conflito de CNS e PDF acessível.
    prior_conflict = _make_case(
        nir_user,
        days_ago=3,
        name="Joao Pedro",
        number="REG-100",
        cns=_CNS_A,
        origin="UPA Brotas",
        age=0,
        with_doc=True,
    )
    _advance_to_awaiting_doctor(prior_conflict)

    # Prior apenas com origem comum (suspeita fraca) e sem PDF físico (minimizado/purgado).
    prior_purged = _make_case(
        nir_user,
        days_ago=5,
        name="Joao Pedro",
        number="",
        cns="",
        origin="UPA Brotas",
        age=0,
        with_doc=False,
    )
    _advance_to_awaiting_doctor(prior_purged)

    incoming = _make_case(
        nir_user,
        days_ago=0,
        name="Joao Pedro",
        number="REG-100",
        cns=_CNS_B,
        origin="UPA Brotas",
        age=0,
        with_doc=True,
    )
    _advance_to_awaiting_doctor(incoming)

    _login_with_role(client, other_nir, NIR_ROLE)

    with CaptureQueriesContext(connection) as queries:
        response = client.get(_detail_url(incoming))

    assert response.status_code == 200
    body = response.content.decode()

    # Zero INSERT/UPDATE/DELETE no GET (R1).
    write_queries = [
        q["sql"]
        for q in queries
        if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert write_queries == []
    assert CaseReappearance.objects.count() == 0

    # Seção de conferência presente, comparativa e com 0 a + conflito de CNS.
    assert 'id="conferencia-reapresentacao"' in body
    assert "Conflito de CNS" in body
    assert "0 a" in body
    assert prior_conflict.disambiguated_reference in body
    assert prior_purged.disambiguated_reference in body
    assert "PDF anterior indisponível" in body

    # Exatamente 2 botões de decisão por candidato (sem terceiro botão).
    section = body.split('id="conferencia-reapresentacao"')[1].split("</section>")[0]
    same_buttons = re.findall(r'name="decision"\s+value="same_request"', section)
    indep_buttons = re.findall(r'name="decision"\s+value="independent"', section)
    all_submit_buttons = re.findall(r'<button[^>]*type="submit"', section)
    assert len(same_buttons) == 2
    assert len(indep_buttons) == 2
    assert len(all_submit_buttons) == 4

    # Ambos os PDFs (incoming + prior_conflict) acessíveis pelo NIR não-criador + ?next= seguro.
    curr_doc = incoming.documents.get()
    prior_doc = prior_conflict.documents.get()
    for doc_case, doc in ((incoming, curr_doc), (prior_conflict, prior_doc)):
        serve_url = reverse("intake:serve_document", args=[doc_case.case_id, doc.pk])
        viewer_url = reverse("intake:pdf_viewer", args=[doc_case.case_id, doc.pk])
        assert serve_url in section
        assert viewer_url in section
        viewer_resp = client.get(f"{viewer_url}?next={_detail_url(incoming)}")
        assert viewer_resp.status_code == 200
        assert _detail_url(incoming) in viewer_resp.content.decode()
        serve_resp = client.get(serve_url)
        assert serve_resp.status_code == 200
        assert b"%PDF-1.4" in b"".join(getattr(serve_resp, "streaming_content", ()))


# ── R2: Silêncio, independente por revisão, reabertura por alteração ────────


@NEW_MODE
@pytest.mark.django_db
def test_silence_does_not_block_and_independent_suppresses_only_current_revision(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    other_nir = user_factory("nir-equipe-r2", NIR_ROLE)
    prior = _make_case(nir_user, days_ago=4, origin="HGE", age=42)
    _advance_to_awaiting_doctor(prior)

    incoming = _make_case(nir_user, days_ago=0, origin="HGE", age=42)
    _advance_to_awaiting_doctor(incoming)

    _login_with_role(client, other_nir, NIR_ROLE)

    # Navegar e sair sem POST mantém filas operacionais e zero vínculos.
    get_resp = client.get(_detail_url(incoming))
    assert get_resp.status_code == 200
    assert CaseReappearance.objects.count() == 0
    incoming.refresh_from_db()
    prior.refresh_from_db()
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert prior.status == CaseStatus.AWAITING_DOCTOR

    (cand,) = list_nir_reappearance_candidates(incoming)
    old_digest = cand.revision_digest

    # Marcar como independente suprime este par/revisão.
    post_indep = client.post(
        _respond_url(incoming),
        {
            "prior_case_id": str(prior.pk),
            "revision_digest": old_digest,
            "decision": "independent",
        },
    )
    assert post_indep.status_code == 302
    assert post_indep.headers["Location"] == _detail_url(incoming)
    assert list_nir_reappearance_candidates(incoming) == []

    # GET seguinte não exibe mais o card de conferência.
    after_resp = client.get(_detail_url(incoming))
    assert after_resp.status_code == 200
    assert 'id="conferencia-reapresentacao"' not in after_resp.content.decode()

    # Alterar fonte/idade do incoming muda a revisão, reabre conferência e recusa digest antigo com 400.
    Case.objects.filter(pk=incoming.pk).update(patient_age=43)
    incoming.refresh_from_db()
    reopened = list_nir_reappearance_candidates(incoming)
    assert len(reopened) == 1
    assert reopened[0].revision_digest != old_digest

    stale_post = client.post(
        _respond_url(incoming),
        {
            "prior_case_id": str(prior.pk),
            "revision_digest": old_digest,
            "decision": "same_request",
        },
    )
    assert stale_post.status_code == 400
    assert "reabra" in stale_post.content.decode().lower()


# ── R3: Três jornadas HTTP ponta a ponta ────────────────────────────────────


@NEW_MODE
@pytest.mark.django_db
def test_journey_medical_replacement_via_http(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    other_nir = user_factory("nir-med-r3", NIR_ROLE)
    prior = _make_case(nir_user, days_ago=6, origin="HGE", age=55)
    _advance_to_awaiting_doctor(prior)

    incoming = _make_case(nir_user, days_ago=0, origin="HGE", age=55)
    _advance_to_awaiting_doctor(incoming)
    (cand,) = list_nir_reappearance_candidates(incoming)

    _login_with_role(client, other_nir, NIR_ROLE)
    resp = client.post(
        _respond_url(incoming),
        {
            "prior_case_id": str(prior.pk),
            "revision_digest": cand.revision_digest,
            "decision": "same_request",
        },
        follow=True,
    )
    assert resp.status_code == 200
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert incoming.reappearance_wait_started_at is not None
    assert "substituição aplicada na fila médica" in resp.content.decode()


@NEW_MODE
@pytest.mark.django_db
def test_journey_scheduling_repeat_preserves_prior_and_scheduler_lease(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    other_nir = user_factory("nir-sched-r3", NIR_ROLE)
    doctor_user = user_factory("doc-sched-r3", DOCTOR_ROLE)
    scheduler_user = user_factory("sched-r3", SCHEDULER_ROLE)

    prior = _make_case(nir_user, days_ago=5, origin="HGE", age=55)
    _advance_to_awaiting_scheduling(prior, doctor_user)
    claim_case_lock(
        prior, user=scheduler_user, context="scheduler_work", role=SCHEDULER_ROLE, lease_seconds=120
    )

    incoming = _make_case(nir_user, days_ago=0, origin="HGE", age=55)
    _advance_to_awaiting_doctor(incoming)
    (cand,) = list_nir_reappearance_candidates(incoming)

    _login_with_role(client, other_nir, NIR_ROLE)
    resp = client.post(
        _respond_url(incoming),
        {
            "prior_case_id": str(prior.pk),
            "revision_digest": cand.revision_digest,
            "decision": "same_request",
        },
        follow=True,
    )
    assert resp.status_code == 200
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert prior.status == CaseStatus.SCHEDULER_REQUESTED
    assert prior.locked_by_id == scheduler_user.pk
    assert incoming.status == CaseStatus.CLEANED
    assert (
        CaseReappearance.objects.filter(
            canonical_case=prior,
            incoming_case=incoming,
            resolution_kind=ResolutionKind.SCHEDULING_REPEAT,
            state=ReappearanceState.APPLIED,
        ).count()
        == 1
    )
    assert "permanece na fila de agendamento" in resp.content.decode()


@NEW_MODE
@pytest.mark.django_db
def test_journey_late_historical_denial_surfaces_on_doctor_card_without_llm2_rerun(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    other_nir = user_factory("nir-hist-r3", NIR_ROLE)
    doctor_user = user_factory("doc-hist-r3", DOCTOR_ROLE)

    prior = _make_case(nir_user, days_ago=4, origin="HGE", age=60)
    _advance_to_awaiting_doctor(prior)
    record_doctor_procedure_decisions(
        prior,
        decisions={ANGIO_TYPE: (DoctorDisposition.DENIED, "Sem critério clínico.")},
        user=doctor_user,
        role=DOCTOR_ROLE,
    )
    CaseProcedure.objects.filter(case=prior, procedure_type=ANGIO_TYPE).update(
        doctor_decided_at=timezone.now() - timedelta(days=4)
    )
    prior.record_doctor_decision(accepted=False, user=doctor_user, role=DOCTOR_ROLE)
    prior.post_final_reply(user=None, role=SYSTEM_ROLE)
    prior.nir_acknowledge(user=nir_user, role=NIR_ROLE)
    prior.start_cleaning(user=None, role=SYSTEM_ROLE)
    prior.complete_cleaning(user=None, role=SYSTEM_ROLE)

    incoming = _make_case(nir_user, days_ago=0, origin="HGE", age=60)
    _advance_to_awaiting_doctor(incoming)
    incoming.summary_text = "Sumário já gerado."
    incoming.save(update_fields=["summary_text"])
    CaseEvent.objects.create(
        case=incoming,
        event_type=CaseEventType.CASE_LLM2_COMPLETED,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role=SYSTEM_ROLE,
        payload={},
    )
    (cand,) = list_nir_reappearance_candidates(incoming)

    _login_with_role(client, other_nir, NIR_ROLE)
    resp = client.post(
        _respond_url(incoming),
        {
            "prior_case_id": str(prior.pk),
            "revision_digest": cand.revision_digest,
            "decision": "same_request",
        },
        follow=True,
    )
    assert resp.status_code == 200
    prior.refresh_from_db()
    incoming.refresh_from_db()
    assert prior.status == CaseStatus.CLEANED
    assert incoming.status == CaseStatus.AWAITING_DOCTOR
    assert "sem reanálise automática" in resp.content.decode()

    # Card médico reflete confirmação tardia sem reexecutar LLM2.
    _login_with_role(client, doctor_user, DOCTOR_ROLE)
    doctor_resp = client.get(reverse("doctor:case_detail", args=[incoming.case_id]))
    assert doctor_resp.status_code == 200
    doc_html = doctor_resp.content.decode()
    assert "Confirmado pelo NIR" in doc_html
    assert "Vinculado após a sumarização" in doc_html
    prior_cases = build_case_detail_context(incoming)["prior_cases"]
    assert isinstance(prior_cases, list) and len(prior_cases) == 1
    assert (
        CaseEvent.objects.filter(
            case=incoming, event_type=CaseEventType.CASE_LLM2_COMPLETED
        ).count()
        == 1
    )


# ── R4: Segurança, validação, concorrência 409 e múltiplos candidatos ───────


@NEW_MODE
@pytest.mark.django_db
def test_security_gates_csrf_roles_getlist_and_invalid_pairs(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    doctor_user = user_factory("doc-sec-r4", DOCTOR_ROLE)
    prior = _make_case(nir_user, days_ago=3, origin="HGE", age=40)
    _advance_to_awaiting_doctor(prior)
    incoming = _make_case(nir_user, days_ago=0, origin="HGE", age=40)
    _advance_to_awaiting_doctor(incoming)
    (cand,) = list_nir_reappearance_candidates(incoming)

    # 1) CSRF sem token → 403.
    csrf_client = Client(enforce_csrf_checks=True)
    _login_with_role(csrf_client, nir_user, NIR_ROLE)
    csrf_resp = csrf_client.post(
        _respond_url(incoming),
        {
            "prior_case_id": str(prior.pk),
            "revision_digest": cand.revision_digest,
            "decision": "same_request",
        },
    )
    assert csrf_resp.status_code == 403

    # 2) Papel não-NIR (doctor / scheduler) → 403.
    _login_with_role(client, doctor_user, DOCTOR_ROLE)
    doc_resp = client.post(
        _respond_url(incoming),
        {
            "prior_case_id": str(prior.pk),
            "revision_digest": cand.revision_digest,
            "decision": "same_request",
        },
    )
    assert doc_resp.status_code == 403

    # 3) Papel NIR removido do banco com sessão ativa → 403.
    revoked_nir = user_factory("nir-revoked-r4", NIR_ROLE)
    doc_role, _ = Role.objects.get_or_create(name=DOCTOR_ROLE)
    revoked_nir.roles.add(doc_role)
    _login_with_role(client, revoked_nir, NIR_ROLE)
    nir_role = Role.objects.get(name=NIR_ROLE)
    revoked_nir.roles.remove(nir_role)
    revoked_resp = client.post(
        _respond_url(incoming),
        {
            "prior_case_id": str(prior.pk),
            "revision_digest": cand.revision_digest,
            "decision": "same_request",
        },
    )
    assert revoked_resp.status_code == 403

    # 4) getlist com valores duplicados → 400 e zero writes.
    _login_with_role(client, nir_user, NIR_ROLE)
    for dup_field, dup_vals in (
        ("decision", ["same_request", "independent"]),
        ("prior_case_id", [str(prior.pk), str(uuid.uuid4())]),
        ("revision_digest", [cand.revision_digest, "v1:deadbeef"]),
    ):
        payload: dict[str, object] = {
            "prior_case_id": str(prior.pk),
            "revision_digest": cand.revision_digest,
            "decision": "same_request",
            dup_field: dup_vals,
        }
        dup_resp = client.post(_respond_url(incoming), payload)
        assert dup_resp.status_code == 400
    assert CaseReappearance.objects.count() == 0

    # 5) prior_case_id forjado / fora de escopo → 400.
    unrelated = _make_case(nir_user, days_ago=2, name="Outro Paciente", origin="HGRS", age=70)
    _advance_to_awaiting_doctor(unrelated)
    forged_resp = client.post(
        _respond_url(incoming),
        {
            "prior_case_id": str(unrelated.pk),
            "revision_digest": revision_digest_for_pair(incoming, unrelated),
            "decision": "same_request",
        },
    )
    assert forged_resp.status_code == 400

    # 6) Par forte inequívoco (único candidato forte) → 400 (não passa pela conferência).
    strong_prior = _make_case(
        nir_user, days_ago=2, name="Paciente Forte", number="REG-999", origin="HGRS", age=33
    )
    _advance_to_awaiting_doctor(strong_prior)
    strong_inc = _make_case(
        nir_user, days_ago=0, name="Paciente Forte", number="REG-999", origin="HGRS", age=33
    )
    _advance_to_awaiting_doctor(strong_inc)
    strong_resp = client.post(
        _respond_url(strong_inc),
        {
            "prior_case_id": str(strong_prior.pk),
            "revision_digest": revision_digest_for_pair(strong_inc, strong_prior),
            "decision": "same_request",
        },
    )
    assert strong_resp.status_code == 400
    assert CaseReappearance.objects.count() == 0


@NEW_MODE
@pytest.mark.django_db
def test_deferred_409_notices_and_multiple_candidates_isolation(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    doctor_user = user_factory("doc-409-r4", DOCTOR_ROLE)
    _login_with_role(client, nir_user, NIR_ROLE)

    # 1) Lease médica ativa no prior → 409 com pendência + retry agendado (sem sucesso falso).
    prior_locked = _make_case(nir_user, days_ago=5, name="Carlos Lima", origin="HGE", age=50)
    _advance_to_awaiting_doctor(prior_locked)
    claim_case_lock(
        prior_locked, user=doctor_user, context="doctor_decide", role=DOCTOR_ROLE, lease_seconds=120
    )
    inc_locked = _make_case(nir_user, days_ago=0, name="Carlos Lima", origin="HGE", age=50)
    _advance_to_awaiting_doctor(inc_locked)
    (cand_locked,) = list_nir_reappearance_candidates(inc_locked)

    resp_409 = client.post(
        _respond_url(inc_locked),
        {
            "prior_case_id": str(prior_locked.pk),
            "revision_digest": cand_locked.revision_digest,
            "decision": "same_request",
        },
    )
    assert resp_409.status_code == 409
    body_409 = resp_409.content.decode()
    assert "nova tentativa automática foi agendada" in body_409
    assert "sucesso" not in body_409.lower()

    # 2) schedule_failed → 409 com orientação explícita de suporte técnico.
    prior_fail = _make_case(nir_user, days_ago=5, name="Paulo Souza", origin="HGE", age=44)
    _advance_to_awaiting_doctor(prior_fail)
    claim_case_lock(
        prior_fail, user=doctor_user, context="doctor_decide", role=DOCTOR_ROLE, lease_seconds=120
    )
    inc_fail = _make_case(nir_user, days_ago=0, name="Paulo Souza", origin="HGE", age=44)
    _advance_to_awaiting_doctor(inc_fail)
    (cand_fail,) = list_nir_reappearance_candidates(inc_fail)

    with patch("django_q.tasks.schedule", side_effect=RuntimeError("broker down")):
        resp_fail = client.post(
            _respond_url(inc_fail),
            {
                "prior_case_id": str(prior_fail.pk),
                "revision_digest": cand_fail.revision_digest,
                "decision": "same_request",
            },
        )
    assert resp_fail.status_code == 409
    assert "SEM agendamento automático" in resp_fail.content.decode()

    # 3) Múltiplos candidatos: confirmar par 1 fecha só par 1; tentar par 2 retorna 400.
    prior_a = _make_case(nir_user, days_ago=5, name="Ana Maria", origin="HGE", age=30)
    _advance_to_awaiting_doctor(prior_a)
    prior_b = _make_case(nir_user, days_ago=4, name="Ana Maria", origin="HGE", age=30)
    _advance_to_awaiting_doctor(prior_b)
    inc_multi = _make_case(nir_user, days_ago=0, name="Ana Maria", origin="HGE", age=30)
    _advance_to_awaiting_doctor(inc_multi)

    cands = list_nir_reappearance_candidates(inc_multi)
    by_prior = {c.prior.pk: c for c in cands}
    assert set(by_prior) == {prior_a.pk, prior_b.pk}

    ok_resp = client.post(
        _respond_url(inc_multi),
        {
            "prior_case_id": str(prior_a.pk),
            "revision_digest": by_prior[prior_a.pk].revision_digest,
            "decision": "same_request",
        },
    )
    assert ok_resp.status_code == 302
    prior_a.refresh_from_db()
    prior_b.refresh_from_db()
    assert prior_a.status == CaseStatus.CLEANED
    assert prior_b.status == CaseStatus.AWAITING_DOCTOR

    second_resp = client.post(
        _respond_url(inc_multi),
        {
            "prior_case_id": str(prior_b.pk),
            "revision_digest": by_prior[prior_b.pk].revision_digest,
            "decision": "same_request",
        },
    )
    assert second_resp.status_code == 400


# ── R5: Manual NIR e rollback flag preservando leitura ──────────────────────


@pytest.mark.django_db
def test_manual_documents_nir_conference_and_absence_of_clinical_triage(
    client: Client,
    nir_user: User,
) -> None:
    _login_with_role(client, nir_user, NIR_ROLE)
    resp = client.get(reverse("manual"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Conferência de reapresentação" in body
    assert "É reapresentação do mesmo pedido" in body
    assert "É um pedido independente" in body
    assert "não há terceiro botão" in body.lower()
    assert "sem triagem clínica" in body.lower()


@pytest.mark.django_db
def test_flag_off_hides_conference_refuses_post_and_preserves_applied_links(
    client: Client,
    nir_user: User,
) -> None:
    with NEW_MODE:
        prior = _make_case(nir_user, days_ago=3, origin="HGE", age=50)
        _advance_to_awaiting_doctor(prior)
        incoming = _make_case(nir_user, days_ago=0, origin="HGE", age=50)
        _advance_to_awaiting_doctor(incoming)
        (cand,) = list_nir_reappearance_candidates(incoming)

        # Cria um par já aplicado antes de desligar a flag.
        prior_applied = _make_case(
            nir_user, days_ago=3, name="Pedro Aplicado", origin="HGE", age=41
        )
        _advance_to_awaiting_doctor(prior_applied)
        inc_applied = _make_case(nir_user, days_ago=0, name="Pedro Aplicado", origin="HGE", age=41)
        _advance_to_awaiting_doctor(inc_applied)
        (cand_applied,) = list_nir_reappearance_candidates(inc_applied)
        _login_with_role(client, nir_user, NIR_ROLE)
        assert (
            client.post(
                _respond_url(inc_applied),
                {
                    "prior_case_id": str(prior_applied.pk),
                    "revision_digest": cand_applied.revision_digest,
                    "decision": "same_request",
                },
            ).status_code
            == 302
        )

    with FLAG_OFF:
        # Conferência oculta no GET e POST recusado com 400.
        get_off = client.get(_detail_url(incoming))
        assert get_off.status_code == 200
        assert 'id="conferencia-reapresentacao"' not in get_off.content.decode()

        post_off = client.post(
            _respond_url(incoming),
            {
                "prior_case_id": str(prior.pk),
                "revision_digest": cand.revision_digest,
                "decision": "same_request",
            },
        )
        assert post_off.status_code == 400

        # Leitura do vínculo já aplicado continua acessível (closed_case_detail).
        closed_resp = client.get(reverse("intake:closed_case_detail", args=[prior_applied.case_id]))
        assert closed_resp.status_code == 200
        assert inc_applied.disambiguated_reference in closed_resp.content.decode()
