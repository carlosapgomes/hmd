"""Cards de texto extraído + PDF original no detalhe do agendador (slice PVP-004, R2–R3).

Substituição da tabela "Documentos" pelos dois cards do relatório
(R2, D1/D7/D8): o ``{% if can_view_pdf %}`` existente passa a envolver o
card de texto em ``<pre>`` (OMITIDO quando vazio — padrão NIR, NÃO
placeholder) + o card do PDF original com um sub-collapse por documento
(``data-pdf-doc-collapse``, embed lazy com ``data-src`` SEM ``src``), link
mobile (``d-md-none``) identificando cada documento ("PDF N — arquivo",
padrão P1 do PVP-003) com ``?next=``, "Abrir em nova aba" e bloco
``<noscript>`` fora dos collapses. O JS lazy carrega no ``extra_js``.

Fora do gate (outro agendador ou pré-decisão): NENHUM dos dois cards
renderiza. Visão limitada preservada (R3).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from datetime import timedelta
from html import unescape

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.closure import post_doctor_denial_reply
from apps.cases.models import Case, CaseDocument, CaseProcedure, CaseStatus
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.scheduler.services import confirm_case_scheduling, deny_case_scheduling

SCHEDULER_ROLE = "scheduler"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"

ANGIO_TYPE = "art_perif"

# Storage de teste: arquivos apenas em memória (nada de disco) — mesmo
# padrão de ``apps/intake/tests/conftest.py``.
_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.fixture(autouse=True)
def _memory_storage() -> Iterator[None]:
    """Storage default em memória durante cada teste do arquivo."""
    with override_settings(STORAGES=_TEST_STORAGES):
        yield


def _login(client: Client, user: User, role: str) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _scheduler_requested_case(
    created_by: User, decided_by: User, procedure_types: Sequence[str]
) -> Case:
    """Caso decidido pelo médico (≥1 aprovado) chegando a ``SCHEDULER_REQUESTED``."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    for operation in (
        "start_pdf_extraction",
        "complete_pdf_extraction",
        "complete_anonymization",
        "complete_llm_extraction",
        "complete_llm_summarization",
    ):
        getattr(case, operation)(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    record_doctor_procedure_decisions(
        case,
        {procedure_type: ("approved", "") for procedure_type in procedure_types},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.SCHEDULER_REQUESTED
    return case


def _add_document(case: Case, uploaded_by: User, *, position: int) -> CaseDocument:
    """Anexa um CaseDocument fake ao caso na posição informada."""
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


def _denied_case(
    *,
    created_by: User,
    decided_by: User,
    scheduling_decider: User,
    extracted_text: str = "TEXTO EXTRAIDO DO RELATORIO DO AGENDADOR",
) -> Case:
    """Caso negado pelo serviço (autor da decisão = scheduling_decider)."""
    case = _scheduler_requested_case(created_by, decided_by, (ANGIO_TYPE,))
    deny_case_scheduling(
        case,
        reason="sem vaga na unidade",
        user=scheduling_decider,
        role=SCHEDULER_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.scheduling_decided_at is not None
    Case.objects.filter(pk=case.pk).update(extracted_text=extracted_text)
    case.refresh_from_db()
    return case


def _medical_denial_replied_case(
    *,
    created_by: User,
    decided_by: User,
    extracted_text: str = "TEXTO DA NEGATIVA MEDICA QUE NAO DEVE VAZAR",
) -> Case:
    """Negativa médica total publicada (DOCTOR_DENIED → FINAL_REPLY_POSTED).

    Nunca passa pelo agendamento: ``scheduling_decided_at`` segue nulo — o
    caso cai em ``PDF_ALLOWED_STATUSES`` só pelo estado (a brecha da D7).
    """
    case = Case.objects.create(created_by=created_by)
    CaseProcedure.objects.create(case=case, procedure_type=ANGIO_TYPE, declared_by_nir=True)
    for operation in (
        "start_pdf_extraction",
        "complete_pdf_extraction",
        "complete_anonymization",
        "complete_llm_extraction",
        "complete_llm_summarization",
    ):
        getattr(case, operation)(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR
    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: ("denied", "sem indicação clínica")},
        user=decided_by,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    post_doctor_denial_reply(case, user=decided_by, role=DOCTOR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    assert case.scheduling_decided_at is None
    Case.objects.filter(pk=case.pk).update(extracted_text=extracted_text)
    case.refresh_from_db()
    return case


def _confirmed_case(
    unit: int,
    *,
    created_by: User,
    decided_by: User,
    scheduled_by: User,
    extracted_text: str = "TEXTO EXTRAIDO DO RELATORIO DO AGENDADOR",
) -> Case:
    """Caso confirmado pelo serviço (autor = scheduled_by) com texto/docs."""
    case = _scheduler_requested_case(created_by, decided_by, (ANGIO_TYPE,))
    confirm_case_scheduling(
        case,
        unit=unit,
        scheduled_datetime=timezone.now() + timedelta(days=2),
        scheduled_location="Sala de hemodinâmica",
        user=scheduled_by,
        role=SCHEDULER_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.FINAL_REPLY_POSTED
    Case.objects.filter(pk=case.pk).update(extracted_text=extracted_text)
    case.refresh_from_db()
    return case


def _detail_body(client: Client, case: Case) -> str:
    response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_detail_shows_text_and_pdf_cards_closed_by_default(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: dentro do gate os dois cards aparecem fechados (sem show)."""
    doctor = user_factory("medico-cards", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-cards", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    document = _add_document(case, nir_user, position=1)
    serve_url = reverse("scheduler:case_pdf", args=[case.case_id, document.position])
    _login(client, scheduler, SCHEDULER_ROLE)

    body = _detail_body(client, case)

    assert "Texto Extraído do PDF" in body
    assert "Visualizar PDF Original" in body
    assert "TEXTO EXTRAIDO DO RELATORIO DO AGENDADOR" in body
    assert serve_url in unescape(body)
    # Ambos os collapses nascem fechados: nenhum `collapse show` no detalhe.
    assert "collapse show" not in body


@pytest.mark.django_db
def test_detail_embed_lazy_without_src(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: embed lazy — data-src aponta o serve, SEM src; sub-collapse marcado."""
    doctor = user_factory("medico-lazy", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-lazy", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    document = _add_document(case, nir_user, position=1)
    serve_url = reverse("scheduler:case_pdf", args=[case.case_id, document.position])
    _login(client, scheduler, SCHEDULER_ROLE)

    body = _detail_body(client, case)

    assert "data-pdf-doc-collapse" in body
    assert f'id="pdf-collapse-{document.position}"' in body
    assert f'data-src="{serve_url}"' in unescape(body)
    assert "pdf-lazy-embed" in body
    # Embed nasce sem `src` (lazy): nenhum `<embed ... src=` no HTML.
    assert "<embed src=" not in body


@pytest.mark.django_db
def test_detail_mobile_links_new_tab_and_noscript(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: link mobile (d-md-none) identificado com ?next=, nova aba e noscript."""
    doctor = user_factory("medico-links", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-links", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    document = _add_document(case, nir_user, position=1)
    detail_url = reverse("scheduler:case_detail", args=[case.case_id])
    serve_url = reverse("scheduler:case_pdf", args=[case.case_id, document.position])
    viewer_url = reverse("scheduler:pdf_viewer", args=[case.case_id, document.position])
    _login(client, scheduler, SCHEDULER_ROLE)

    body = _detail_body(client, case)
    plain = unescape(body)

    # Link mobile para o viewer dedicado (visível só abaixo de md) com next.
    assert "d-md-none" in body
    assert viewer_url in plain
    assert detail_url in plain
    # Abrir em nova aba aponta o serve e abre em _blank.
    assert "Abrir em nova aba" in body
    assert 'target="_blank"' in body
    # Bloco noscript FORA dos collapses com link direto de abertura.
    noscript_start = body.index("<noscript>")
    noscript_end = body.index("</noscript>")
    assert serve_url in unescape(body[noscript_start:noscript_end])
    assert (
        body.index("pdf-collapse-") > noscript_end or body.index("pdf-collapse-") < noscript_start
    )


@pytest.mark.django_db
def test_detail_loads_lazy_script(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: pdf-lazy-embed.js carrega no detalhe do agendador."""
    doctor = user_factory("medico-script", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-script", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _add_document(case, nir_user, position=1)
    _login(client, scheduler, SCHEDULER_ROLE)

    body = _detail_body(client, case)

    assert "js/pdf-lazy-embed.js" in body


@pytest.mark.django_db
def test_detail_omits_text_card_without_extracted_text(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2 (padrão NIR): sem extracted_text o card de texto é OMITIDO (sem placeholder)."""
    doctor = user_factory("medico-omissao", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-omissao", (SCHEDULER_ROLE,))
    case = _confirmed_case(
        1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler, extracted_text=""
    )
    _add_document(case, nir_user, position=1)
    _login(client, scheduler, SCHEDULER_ROLE)

    body = _detail_body(client, case)

    assert "Texto Extraído do PDF" not in body
    assert "Texto extraído indisponível" not in body
    assert "Visualizar PDF Original" in body


@pytest.mark.django_db
def test_detail_replaces_documents_table_with_cards(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: a tabela "Documentos" some — os cards assumem o ponto do gate."""
    doctor = user_factory("medico-substituicao", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-substituicao", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _add_document(case, nir_user, position=1)
    _login(client, scheduler, SCHEDULER_ROLE)

    body = _detail_body(client, case)

    assert '<h2 class="h5 mb-0">Documentos</h2>' not in body
    assert "Visualizar PDF Original" in body


@pytest.mark.django_db
def test_detail_shows_cards_to_other_scheduler_of_team(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1 (equipe, inversão D6): caso confirmado por OUTRO agendador — um
    agendador diferente vê os dois cards (gate por decisão + estado)."""
    doctor = user_factory("medico-fora-gate", (DOCTOR_ROLE,))
    owner = user_factory("agendador-fora-gate-owner", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=owner)
    document = _add_document(case, nir_user, position=1)
    serve_url = reverse("scheduler:case_pdf", args=[case.case_id, document.position])
    other = user_factory("agendador-fora-gate-outro", (SCHEDULER_ROLE,))
    _login(client, other, SCHEDULER_ROLE)

    body = _detail_body(client, case)

    assert "Texto Extraído do PDF" in body
    assert "Visualizar PDF Original" in body
    assert "TEXTO EXTRAIDO DO RELATORIO DO AGENDADOR" in body
    assert serve_url in unescape(body)
    assert "js/pdf-lazy-embed.js" in body


@pytest.mark.django_db
def test_detail_shows_cards_for_denied_case_to_other_scheduler(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1 (equipe): caso NEGADO pelo agendamento por outro decisor — a
    negação também carimba a decisão, então outro agendador vê os cards."""
    doctor = user_factory("medico-negado-equipe", (DOCTOR_ROLE,))
    decider = user_factory("agendador-negado-decisor", (SCHEDULER_ROLE,))
    case = _denied_case(created_by=nir_user, decided_by=doctor, scheduling_decider=decider)
    _add_document(case, nir_user, position=1)
    other = user_factory("agendador-negado-outro", (SCHEDULER_ROLE,))
    _login(client, other, SCHEDULER_ROLE)

    body = _detail_body(client, case)

    assert "Texto Extraído do PDF" in body
    assert "Visualizar PDF Original" in body
    assert "TEXTO EXTRAIDO DO RELATORIO DO AGENDADOR" in body


@pytest.mark.django_db
def test_detail_no_cards_for_medical_denial_without_scheduling_decision(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2 (guarda de decisão D7): negativa médica total em
    FINAL_REPLY_POSTED (``scheduling_decided_at`` nulo) — SEM cards de
    PDF/texto e 404 nas rotas de serve/viewer (o estado sozinho é a brecha)."""
    doctor = user_factory("medico-negmed-cards", (DOCTOR_ROLE,))
    case = _medical_denial_replied_case(created_by=nir_user, decided_by=doctor)
    _add_document(case, nir_user, position=1)
    scheduler = user_factory("agendador-negmed-cards", (SCHEDULER_ROLE,))
    _login(client, scheduler, SCHEDULER_ROLE)

    body = _detail_body(client, case)

    assert "Texto Extraído do PDF" not in body
    assert "Visualizar PDF Original" not in body
    assert "TEXTO DA NEGATIVA MEDICA QUE NAO DEVE VAZAR" not in body
    assert client.get(reverse("scheduler:case_pdf", args=[case.case_id, 1])).status_code == 404
    assert client.get(reverse("scheduler:pdf_viewer", args=[case.case_id, 1])).status_code == 404


@pytest.mark.django_db
def test_detail_no_cards_before_decision(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: pré-decisão (próprio agendador em potencial) não vê os cards."""
    doctor = user_factory("medico-pre-decisao", (DOCTOR_ROLE,))
    case = _scheduler_requested_case(nir_user, doctor, (ANGIO_TYPE,))
    _add_document(case, nir_user, position=1)
    Case.objects.filter(pk=case.pk).update(extracted_text="TEXTO QUE NAO DEVE VAZAR")
    scheduler = user_factory("agendador-pre-decisao", (SCHEDULER_ROLE,))
    _login(client, scheduler, SCHEDULER_ROLE)

    body = _detail_body(client, case)

    assert "Texto Extraído do PDF" not in body
    assert "Visualizar PDF Original" not in body
    assert "TEXTO QUE NAO DEVE VAZAR" not in body


@pytest.mark.django_db
def test_mobile_links_identify_each_document(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2 (P1 do PVP-003): cada link mobile identifica o documento ("PDF N — arquivo")."""
    doctor = user_factory("medico-mobile-distintos", (DOCTOR_ROLE,))
    scheduler = user_factory("agendador-mobile-distintos", (SCHEDULER_ROLE,))
    case = _confirmed_case(1, created_by=nir_user, decided_by=doctor, scheduled_by=scheduler)
    _add_document(case, nir_user, position=1)
    _add_document(case, nir_user, position=2)
    _login(client, scheduler, SCHEDULER_ROLE)

    body = _detail_body(client, case)

    anchors = re.findall(r"(<a[^>]*d-md-none[^>]*>.*?</a>)", body, re.DOTALL)
    viewer_anchors = [a for a in anchors if "/viewer/" in a.split(">", 1)[0]]
    assert len(viewer_anchors) == 2, (
        f"esperados 2 links mobile do viewer, obtidos {len(viewer_anchors)}"
    )
    texts = [
        re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", anchor)).strip() for anchor in viewer_anchors
    ]
    assert texts[0] != texts[1], f"links mobile idênticos: {texts!r}"
    assert "PDF 1" in texts[0] and "relatorio-sesab-1.pdf" in texts[0]
    assert "PDF 2" in texts[1] and "relatorio-sesab-2.pdf" in texts[1]
