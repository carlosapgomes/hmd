"""Testes do reenvio corrigido de caso encerrado (slice 004, R1–R5, D4).

Cobre R1 (campos do vínculo no ``Case`` — migration ``0009``: self-FK
``corrects_case`` SET_NULL com reverso ``corrected_by``, ``correction_reason``
e autor ``correction_created_by``), R2 (``create_corrected_resubmission``:
validações antes de qualquer criação, novo caso com **exatamente 1 PDF + tipo
único** no MESMO atomic, eventos nos dois casos, original intacto e enqueue do
processamento do novo PDF), R3 (tipo do novo caso é EXATAMENTE o declarado —
nunca herdado do original), R4 (UI: botão "Reenviar corrigido" apenas em
``CLEANED`` próprio, GET/POST do form, redirect ao detalhe do novo caso, erros
re-renderizam o form, detalhe do original lista ``corrected_by`` ordenado) e a
regressão do ``create_case_with_documents`` puro (sem kwargs de correção →
comportamento do change 04).

Serviço testado com ``INTAKE_RUN_TASKS_INLINE=False`` + assert do enqueue
(padrão dos testes atuais do intake — design D4); o comportamento com
``INTAKE_RUN_TASKS_INLINE=True`` é desvio conhecido documentado no design D4.
Os casos ``CLEANED`` são dirigidos pelas operações FSM reais (mesmo padrão de
setup de ``apps/intake/tests/test_closure_views.py`` e
``apps/cases/tests/test_closure_ack.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseProcedure, CaseStatus
from apps.intake.services import create_case_with_documents, create_corrected_resubmission

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"

# Tipos representativos do catálogo (mesmos dos testes 001–003).
ANGIO_TYPE = "art_perif"
CARDIO_TYPE = "cat_cardiaco"
RADIO_TYPE = "nefrostomia"
ANGIO_LABEL = "Arteriografia periférica"

REASON = "laudo com dados divergentes do exame original"
RESUBMIT_BUTTON_LABEL = "Reenviar corrigido"
RESUBMIT_FLASH_PREFIX = "Reenvio corrigido criado"


@pytest.fixture(autouse=True)
def _resubmission_without_inline_processing() -> Iterator[None]:
    """Módulo sem processamento automático pós-criação (design D4).

    O reenvio corrigido cria um NOVO caso em ``NEW`` e enfileira o worker pdf.
    Com ``INTAKE_RUN_TASKS_INLINE=False`` (padrão dos testes atuais do intake)
    os testes do serviço controlam o enqueue (assert) e a UI não processa os
    PDFs fake deste módulo; o comportamento com ``inline=True`` é desvio
    conhecido documentado no design D4.
    """

    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


def _drive_to_cleaned(case: Case, *, nir: User, doctor: User) -> None:
    """Dirige o caso pelas operações FSM até ``CLEANED`` (setup do slice).

    Fluxo do fechamento real sem decisões por procedimento/comunicações: o
    estado terminal ``CLEANED`` é o pré-requisito do reenvio corrigido (D4) —
    o setup não precisa de rows/comunicações para exercitar o slice.
    """
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    case.record_doctor_decision(accepted=False, user=doctor, role=DOCTOR_ROLE)
    case.post_final_reply(user=doctor, role=DOCTOR_ROLE)
    case.nir_acknowledge(user=nir, role=NIR_ROLE)
    case.start_cleaning(user=nir, role=NIR_ROLE)
    case.complete_cleaning(user=nir, role=NIR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED


def _cleaned_case(*, created_by: User, doctor: User) -> Case:
    """Caso ``CLEANED`` do criador (novo envio encerrado — setup dos testes)."""
    case = Case.objects.create(created_by=created_by)
    _drive_to_cleaned(case, nir=created_by, doctor=doctor)
    return case


def _declare(case: Case, *procedure_types: str) -> None:
    """Cria as rows declaradas do caso (fonte autoritativa do slice 003)."""
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )


def _declared_types(case: Case) -> set[str]:
    """Tipos declarados do caso (rows com ``declared_by_nir``)."""
    return {row.procedure_type for row in case.procedures.filter(declared_by_nir=True)}


def _event_types(case: Case) -> list[str]:
    """Tipos de evento da trilha na ordem de gravação."""
    return [event.event_type for event in case.events.order_by("id")]


def _login(client: Client, user: User) -> None:
    """Login do usuário (papel único → middleware define o papel ativo)."""
    client.force_login(user)


def _resubmit_file(pdf_factory: Callable[..., SimpleUploadedFile]) -> SimpleUploadedFile:
    """PDF fake do reenvio (o reenvio corrigido aceita exatamente 1 PDF)."""
    return pdf_factory(name="reenvio.pdf")


# ── R1: campos do vínculo no Case (migration 0009) ────────────────────────


@pytest.mark.django_db
def test_migration_fields(nir_user: User) -> None:
    """R1: o vínculo nasce com self-FK SET_NULL + reverso ``corrected_by``,
    motivo TextField e autor FK SET_NULL — apagar o original zera o vínculo
    das correções (sem cascata)."""
    original = Case.objects.create(created_by=nir_user)
    correction = Case.objects.create(
        created_by=nir_user,
        corrects_case=original,
        correction_reason=REASON,
        correction_created_by=nir_user,
    )
    assert correction.corrects_case_id == original.case_id
    assert correction.corrects_case == original
    assert correction.correction_reason == REASON
    assert correction.correction_created_by == nir_user
    # Reverso ``corrected_by`` (self-FK) lista as correções do original.
    assert list(original.corrected_by.all()) == [correction]

    # SET_NULL: deletar o original não apaga a correção — o vínculo é zerado.
    original.delete()
    correction.refresh_from_db()
    assert correction.corrects_case_id is None
    assert correction.corrects_case is None
    assert correction.correction_created_by == nir_user


# ── R2: serviço — criação vinculada, eventos e original intacto ───────────


@pytest.mark.django_db
def test_resubmission_creates_linked_case(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/cenário spec: do original CLEANED nasce um NOVO caso em NEW com os
    kwargs de correção, documentos e tipos declarados — pipeline completo
    desde NEW."""
    doctor = user_factory("doctor-resub-link", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    uploaded = _resubmit_file(pdf_factory)

    new_case = create_corrected_resubmission(
        original_case=original,
        user=nir_user,
        role=NIR_ROLE,
        file=uploaded,
        procedure_type=ANGIO_TYPE,
        correction_reason=REASON,
    )

    assert new_case.pk != original.pk
    assert new_case.status == CaseStatus.NEW
    assert new_case.created_by == nir_user
    # Vínculo de correção com motivo e autor persistidos.
    assert new_case.corrects_case_id == original.case_id
    assert new_case.correction_reason == REASON
    assert new_case.correction_created_by == nir_user
    # Documento único do novo caso (1 PDF por caso).
    assert [document.original_filename for document in new_case.documents.all()] == [uploaded.name]
    assert new_case.documents.count() == 1
    assert _declared_types(new_case) == {ANGIO_TYPE}


@pytest.mark.django_db
def test_resubmission_events_both_cases(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: CASE_MARKED_SUPERSEDED no original (payload com o id do novo) e
    CASE_CORRECTION_CREATED no novo (payload com id do original + motivo), com
    o NIR como ator/papel, no MESMO atomic."""
    doctor = user_factory("doctor-resub-events", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    events_before = original.events.count()

    new_case = create_corrected_resubmission(
        original_case=original,
        user=nir_user,
        role=NIR_ROLE,
        file=_resubmit_file(pdf_factory),
        procedure_type=ANGIO_TYPE,
        correction_reason=REASON,
    )

    # Original: exatamente +1 evento, o de supersedição, com o id do novo.
    assert original.events.count() == events_before + 1
    superseded = original.events.get(event_type=CaseEventType.CASE_MARKED_SUPERSEDED)
    assert superseded.actor == nir_user
    assert superseded.actor_type == "user"
    assert superseded.actor_role == NIR_ROLE
    assert superseded.payload == {"corrected_case_id": str(new_case.case_id)}

    # Novo caso: evento de correção com o id do original + motivo, após a
    # declaração de procedimentos (mesmo atomic).
    event_types = _event_types(new_case)
    assert event_types.index(CaseEventType.CASE_CORRECTION_CREATED.value) > event_types.index(
        CaseEventType.CASE_PROCEDURES_DECLARED.value
    )
    correction_event = new_case.events.get(event_type=CaseEventType.CASE_CORRECTION_CREATED)
    assert correction_event.actor == nir_user
    assert correction_event.actor_role == NIR_ROLE
    assert correction_event.payload == {
        "original_case_id": str(original.case_id),
        "correction_reason": REASON,
    }


@pytest.mark.django_db
def test_resubmission_original_untouched(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: o original permanece CLEANED e íntegro — status/dados/campos de
    correção não mudam e a trilha só ganha o evento de supersedição."""
    doctor = user_factory("doctor-resub-intact", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    _declare(original, ANGIO_TYPE, CARDIO_TYPE)
    original.agency_record_number = "55555"
    original.save(update_fields=["agency_record_number"])
    original.refresh_from_db()
    data_before = {
        "status": original.status,
        "agency_record_number": original.agency_record_number,
        "created_at": original.created_at,
        "updated_at": original.updated_at,
        "corrects_case_id": original.corrects_case_id,
        "correction_created_by_id": original.correction_created_by_id,
    }
    events_before = original.events.count()

    create_corrected_resubmission(
        original_case=original,
        user=nir_user,
        role=NIR_ROLE,
        file=_resubmit_file(pdf_factory),
        procedure_type=RADIO_TYPE,
        correction_reason=REASON,
    )

    original.refresh_from_db()
    assert original.status == CaseStatus.CLEANED
    assert original.agency_record_number == data_before["agency_record_number"]
    assert original.created_at == data_before["created_at"]
    assert original.updated_at == data_before["updated_at"]
    assert original.corrects_case_id is None
    assert original.correction_created_by_id is None
    # O original não herda rows do novo caso nem perde as próprias.
    assert _declared_types(original) == {ANGIO_TYPE, CARDIO_TYPE}
    # Trilha: apenas o evento de supersedição foi adicionado.
    assert original.events.count() == events_before + 1
    assert CaseEventType.CASE_MARKED_SUPERSEDED.value in _event_types(original)


@pytest.mark.django_db
def test_resubmission_enqueues_pdf_processing(
    monkeypatch: pytest.MonkeyPatch,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/D4: o processamento do novo caso é enfileirado exatamente uma vez
    (cluster pdf) — o enqueue do worker roda dentro do atomic externo e, em
    teste com ``INTAKE_RUN_TASKS_INLINE=False``, é inofensivo (async)."""
    doctor = user_factory("doctor-resub-enqueue", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    calls: list[tuple[object, ...]] = []

    def _recorder(*args: object, **kwargs: object) -> None:
        del kwargs
        calls.append(args)

    monkeypatch.setattr("apps.intake.tasks.async_task", _recorder)

    new_case = create_corrected_resubmission(
        original_case=original,
        user=nir_user,
        role=NIR_ROLE,
        file=_resubmit_file(pdf_factory),
        procedure_type=ANGIO_TYPE,
        correction_reason=REASON,
    )

    assert len(calls) == 1
    assert calls[0][0] == "apps.intake.tasks.process_case_documents"
    assert calls[0][1] == new_case.case_id


@pytest.mark.django_db
@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
def test_resubmission_reason_required(
    reason: str,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: motivo vazio/apenas espaços é recusado antes de qualquer criação."""
    doctor = user_factory("doctor-resub-reason", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    events_before = original.events.count()

    with pytest.raises(ValueError, match="motivo"):
        create_corrected_resubmission(
            original_case=original,
            user=nir_user,
            role=NIR_ROLE,
            file=_resubmit_file(pdf_factory),
            procedure_type=ANGIO_TYPE,
            correction_reason=reason,
        )

    assert Case.objects.count() == 1
    assert original.events.count() == events_before


@pytest.mark.django_db
def test_resubmission_original_not_cleaned(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: original fora de CLEANED é recusado (erro nomeado, sem efeito)."""
    active = Case.objects.create(created_by=nir_user)
    events_before = active.events.count()

    with pytest.raises(ValueError, match="CLEANED"):
        create_corrected_resubmission(
            original_case=active,
            user=nir_user,
            role=NIR_ROLE,
            file=_resubmit_file(pdf_factory),
            procedure_type=ANGIO_TYPE,
            correction_reason=REASON,
        )

    assert Case.objects.count() == 1
    assert active.status == CaseStatus.NEW
    assert active.events.count() == events_before


@pytest.mark.django_db
def test_resubmission_non_creator_rejected(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/R4: quem não é o criador não reenvia — Http404 sem vazar informação
    (mesmo status not-found do escopo por criador dos serviços do intake)."""
    doctor = user_factory("doctor-resub-scope", DOCTOR_ROLE)
    other_nir = user_factory("nir-resub-scope", NIR_ROLE)
    foreign = _cleaned_case(created_by=other_nir, doctor=doctor)
    events_before = foreign.events.count()

    with pytest.raises(Http404):
        create_corrected_resubmission(
            original_case=foreign,
            user=nir_user,
            role=NIR_ROLE,
            file=_resubmit_file(pdf_factory),
            procedure_type=ANGIO_TYPE,
            correction_reason=REASON,
        )

    assert Case.objects.count() == 1
    assert foreign.status == CaseStatus.CLEANED
    assert foreign.events.count() == events_before


@pytest.mark.django_db
def test_resubmission_invalid_batch_rejected(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: lote inválido (não-PDF) rejeita nomeando o arquivo — sem efeito."""
    doctor = user_factory("doctor-resub-batch", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    events_before = original.events.count()

    with pytest.raises(ValueError, match="foto.jpg"):
        create_corrected_resubmission(
            original_case=original,
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(name="foto.jpg", content_type="image/jpeg"),
            procedure_type=ANGIO_TYPE,
            correction_reason=REASON,
        )

    assert Case.objects.count() == 1
    assert original.events.count() == events_before


@pytest.mark.django_db
def test_resubmission_types_validated(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: tipo fora do catálogo rejeita nomeando o tipo; tipo ausente
    também é rejeitado — ambos sem efeito."""
    doctor = user_factory("doctor-resub-types", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    events_before = original.events.count()

    with pytest.raises(ValueError, match="fora do catálogo"):
        create_corrected_resubmission(
            original_case=original,
            user=nir_user,
            role=NIR_ROLE,
            file=_resubmit_file(pdf_factory),
            procedure_type="procedimento_inexistente",
            correction_reason=REASON,
        )
    with pytest.raises(ValueError, match="único tipo"):
        create_corrected_resubmission(
            original_case=original,
            user=nir_user,
            role=NIR_ROLE,
            file=_resubmit_file(pdf_factory),
            procedure_type="",
            correction_reason=REASON,
        )

    assert Case.objects.count() == 1
    assert original.events.count() == events_before


# ── R3: tipos explícitos, nunca herdados ──────────────────────────────────


@pytest.mark.django_db
def test_resubmission_types_explicit(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: o novo caso declara EXATAMENTE o tipo da chamada — conjunto
    distinto do original prova que nada é herdado."""
    doctor = user_factory("doctor-resub-explicit", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    _declare(original, ANGIO_TYPE, CARDIO_TYPE)
    # Conjuntos disjuntos: o original declara angio/cateterismo; o reenvio
    # escolhe apenas nefrostomia — nunca herda os dois do original.
    new_case = create_corrected_resubmission(
        original_case=original,
        user=nir_user,
        role=NIR_ROLE,
        file=_resubmit_file(pdf_factory),
        procedure_type=RADIO_TYPE,
        correction_reason=REASON,
    )

    assert _declared_types(new_case) == {RADIO_TYPE}
    assert _declared_types(original) == {ANGIO_TYPE, CARDIO_TYPE}
    assert len(new_case.procedures.filter(declared_by_nir=True)) == 1


# ── Regressão: create_case_with_documents puro (change 04) ────────────────


@pytest.mark.django_db
def test_create_case_with_documents_pure_no_correction(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """Regressão R2/R5: sem os kwargs de correção, a criação do change 04 não
    vincula nada — campos vazios e trilha sem eventos de correção."""
    case = create_case_with_documents(
        user=nir_user,
        role=NIR_ROLE,
        file=_resubmit_file(pdf_factory),
        procedure_type=ANGIO_TYPE,
    )

    assert case.corrects_case_id is None
    assert case.corrects_case is None
    assert case.correction_reason == ""
    assert case.correction_created_by_id is None
    event_types = _event_types(case)
    assert event_types == [CaseEventType.CASE_PROCEDURES_DECLARED.value]


# ── R4: UI — botão, view flow e lista no detalhe do original ──────────────


@pytest.mark.django_db
def test_resubmit_button_only_cleaned_own(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R4: o detalhe mostra "Reenviar corrigido" apenas em CLEANED do próprio
    criador — ausente em estado ativo do mesmo criador."""
    doctor = user_factory("doctor-resub-button", DOCTOR_ROLE)
    cleaned = _cleaned_case(created_by=nir_user, doctor=doctor)
    active = Case.objects.create(created_by=nir_user)
    _login(client, nir_user)

    body_cleaned = client.get(
        reverse("intake:case_detail", args=[cleaned.case_id])
    ).content.decode()
    assert RESUBMIT_BUTTON_LABEL in body_cleaned
    assert reverse("intake:case_resubmit", args=[cleaned.case_id]) in body_cleaned

    body_active = client.get(reverse("intake:case_detail", args=[active.case_id])).content.decode()
    assert RESUBMIT_BUTTON_LABEL not in body_active
    assert reverse("intake:case_resubmit", args=[active.case_id]) not in body_active


@pytest.mark.django_db
def test_resubmit_route_guards_non_cleaned(
    client: Client,
    nir_user: User,
) -> None:
    """R4: GET do form em caso do criador fora de CLEANED redireciona ao
    detalhe com mensagem (nunca renderiza o form de um caso inelegível)."""
    active = Case.objects.create(created_by=nir_user)
    _login(client, nir_user)

    response = client.get(reverse("intake:case_resubmit", args=[active.case_id]))

    assert response.status_code == 302
    assert response["Location"] == reverse("intake:case_detail", args=[active.case_id])
    followed = client.get(response["Location"])
    assert followed.status_code == 200
    assert "apenas para casos encerrados" in followed.content.decode()


@pytest.mark.django_db
def test_resubmit_non_creator_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R4: caso alheio → 404 no GET e no POST do reenvio corrigido — o caso
    alheio permanece intacto (sem vazamento)."""
    doctor = user_factory("doctor-resub-foreign", DOCTOR_ROLE)
    other_nir = user_factory("nir-resub-foreign", NIR_ROLE)
    foreign = _cleaned_case(created_by=other_nir, doctor=doctor)
    resubmit_url = reverse("intake:case_resubmit", args=[foreign.case_id])
    _login(client, nir_user)

    assert client.get(resubmit_url).status_code == 404
    assert client.post(resubmit_url).status_code == 404

    foreign.refresh_from_db()
    assert foreign.status == CaseStatus.CLEANED
    assert CaseEventType.CASE_MARKED_SUPERSEDED.value not in _event_types(foreign)


@pytest.mark.django_db
def test_resubmit_role_guard(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R4: papel ativo ≠ nir → 403 no form do reenvio corrigido."""
    doctor = user_factory("doctor-resub-guard", DOCTOR_ROLE)
    cleaned = _cleaned_case(created_by=nir_user, doctor=doctor)
    resubmit_url = reverse("intake:case_resubmit", args=[cleaned.case_id])

    client.force_login(doctor)
    assert client.get(resubmit_url).status_code == 403


@pytest.mark.django_db
def test_resubmit_view_flow(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4/cenário spec: GET renderiza o form (arquivos + tipos do catálogo +
    motivo); POST válido cria o novo caso e redireciona ao detalhe dele com
    flash; o original segue CLEANED com o evento de supersedição."""
    doctor = user_factory("doctor-resub-flow", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    _login(client, nir_user)
    resubmit_url = reverse("intake:case_resubmit", args=[original.case_id])

    # GET: form com arquivos, checkboxes do catálogo e motivo.
    response = client.get(resubmit_url)
    assert response.status_code == 200
    body = response.content.decode()
    assert 'name="documents"' in body
    assert ANGIO_LABEL in body
    assert 'value="art_perif"' in body
    assert 'name="correction_reason"' in body
    assert 'enctype="multipart/form-data"' in body

    # POST válido: novo caso (1 PDF + 1 tipo) + redirect ao detalhe dele.
    events_before = original.events.count()
    response = client.post(
        resubmit_url,
        {
            "documents": [pdf_factory()],
            "procedure_types": [ANGIO_TYPE],
            "correction_reason": REASON,
        },
    )

    assert response.status_code == 302
    new_case = Case.objects.exclude(pk=original.pk).get()
    assert response["Location"] == reverse("intake:case_detail", args=[new_case.case_id])

    followed = client.get(response["Location"])
    assert followed.status_code == 200
    assert RESUBMIT_FLASH_PREFIX in followed.content.decode()
    assert str(new_case.case_id) in followed.content.decode()

    new_case.refresh_from_db()
    assert new_case.status == CaseStatus.NEW
    assert new_case.corrects_case_id == original.case_id
    assert new_case.correction_reason == REASON
    assert new_case.documents.count() == 1
    assert _declared_types(new_case) == {ANGIO_TYPE}

    original.refresh_from_db()
    assert original.status == CaseStatus.CLEANED
    assert original.events.count() == events_before + 1
    assert CaseEventType.CASE_MARKED_SUPERSEDED.value in _event_types(original)


@pytest.mark.django_db
def test_resubmit_view_validation_errors_rerender(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: erros de validação re-renderizam o form com o resumo — motivo
    vazio, arquivo inválido e mais de 1 arquivo nomeiam o problema e nada é
    criado."""
    doctor = user_factory("doctor-resub-invalid", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    _login(client, nir_user)
    resubmit_url = reverse("intake:case_resubmit", args=[original.case_id])
    cases_before = Case.objects.count()
    events_before = original.events.count()

    # Motivo vazio.
    response = client.post(
        resubmit_url,
        {
            "documents": [pdf_factory()],
            "procedure_types": [ANGIO_TYPE],
            "correction_reason": "  ",
        },
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "motivo" in body
    assert Case.objects.count() == cases_before

    # Arquivo inválido.
    response = client.post(
        resubmit_url,
        {
            "documents": [pdf_factory(name="foto.jpg", content_type="image/jpeg")],
            "procedure_types": [ANGIO_TYPE],
            "correction_reason": REASON,
        },
    )
    assert response.status_code == 200
    assert "foto.jpg" in response.content.decode()
    assert Case.objects.count() == cases_before
    assert original.events.count() == events_before

    # Mais de 1 arquivo → o reenvio corrigido aceita exatamente 1 PDF.
    response = client.post(
        resubmit_url,
        {
            "documents": [pdf_factory(), pdf_factory()],
            "procedure_types": [ANGIO_TYPE],
            "correction_reason": REASON,
        },
    )
    assert response.status_code == 200
    assert "exatamente 1" in response.content.decode()
    assert Case.objects.count() == cases_before
    assert original.events.count() == events_before


@pytest.mark.django_db
def test_original_lists_corrections(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: o detalhe do original lista as correções (id, data de criação e
    motivo), ordenadas por criação."""
    doctor = user_factory("doctor-resub-list", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    first_reason = "primeiro reenvio — dados do laudo divergentes"
    second_reason = "segundo reenvio — anexo do exame complementar"
    first = create_corrected_resubmission(
        original_case=original,
        user=nir_user,
        role=NIR_ROLE,
        file=_resubmit_file(pdf_factory),
        procedure_type=ANGIO_TYPE,
        correction_reason=first_reason,
    )
    second = create_corrected_resubmission(
        original_case=original,
        user=nir_user,
        role=NIR_ROLE,
        file=_resubmit_file(pdf_factory),
        procedure_type=RADIO_TYPE,
        correction_reason=second_reason,
    )
    _login(client, nir_user)

    response = client.get(reverse("intake:case_detail", args=[original.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    heading = body.index("Reenvios corrigidos deste caso")
    listing = body[heading:]
    assert str(first.case_id) in listing
    assert str(second.case_id) in listing
    assert first_reason in listing
    assert second_reason in listing
    # Ordenado por criação (mais antigo primeiro).
    assert listing.index(str(first.case_id)) < listing.index(str(second.case_id))
