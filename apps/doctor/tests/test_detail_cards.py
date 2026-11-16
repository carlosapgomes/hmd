"""Cards de texto extraído + PDF original no detalhe médico (slice PVP-003, R2–R3).

O card "Documentos" de ``templates/doctor/_case_cards.html`` é substituído
pelo par do relatório (D1: ``pdf_items`` montado na view; D8: no mesmo ponto
— após Prior-case, antes de Requisitos): card de texto em ``<pre>`` com
**placeholder de indisponibilidade quando vazio (sem omitir — diferença
contratual vs NIR)** + card do PDF original com um sub-collapse por
documento (``data-pdf-doc-collapse``, embed lazy com ``data-src`` SEM
``src``), link mobile (``d-md-none``) com ``?next=``, "Abrir em nova aba" e
bloco ``<noscript>`` com links diretos fora dos collapses. O JS lazy carrega
nas duas páginas (bloco ``extra_js``).

Cobertura dupla (R3): detalhe read-only (``doctor:case_detail``, pós-decisão)
E página de trabalho (``doctor:case_decide``, AWAITING_DOCTOR com reserva)
renderizam os MESMOS cards — uma edição única no partial.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from html import unescape

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import (
    Case,
    CaseDocument,
    CaseProcedure,
    CaseStatus,
    DoctorDisposition,
)
from apps.cases.procedures import record_doctor_procedure_decisions

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


def _make_awaiting_case(
    created_by: User,
    *,
    extracted_text: str = "TEXTO EXTRAIDO DO RELATORIO MEDICO",
) -> Case:
    """Caso em AWAITING_DOCTOR com 1 documento e o texto informado."""
    case = _create_case_with_declared(created_by, (ANGIO_TYPE,))
    _advance_to_awaiting_doctor(case)
    _add_document(case, created_by, position=1)
    Case.objects.filter(pk=case.pk).update(extracted_text=extracted_text)
    case.refresh_from_db()
    return case


def _decide_case(case: Case, doctor: User) -> None:
    """Decide o caso (negativa total) — leva a pós-decisão (read-only)."""
    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: (DoctorDisposition.DENIED, "INR elevado.")},
        user=doctor,
        role=DOCTOR_ROLE,
    )


def _detail_bodies(client: Client, case: Case) -> tuple[str, str]:
    """Corpos das duas páginas (detalhe pós-decisão + trabalho AWAITING)."""
    detail = client.get(reverse("doctor:case_detail", args=[case.case_id]))
    assert detail.status_code == 200
    decide = client.get(reverse("doctor:case_decide", args=[case.case_id]))
    assert decide.status_code == 200
    return detail.content.decode(), decide.content.decode()


@pytest.mark.django_db
def test_mobile_links_identify_each_document_in_both_pages(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """P1 (revisão slice-003): cada link mobile identifica o documento.

    Com dois documentos, os dois ``<a>`` mobile (``d-md-none``) precisam de
    textos distinguíveis (posição e/ou nome do arquivo) — o markup anterior
    repetia o mesmo "Visualizar PDF" nos dois links (nome/tamanho só no
    botão desktop, oculto <md).
    """
    case = _make_awaiting_case(nir_user)
    _add_document(case, nir_user, position=2)
    doctor = user_factory("medico-mobile-distintos", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    detail_body, decide_body = _detail_bodies(client, case)

    for body in (detail_body, decide_body):
        anchors = re.findall(r"(<a[^>]*d-md-none[^>]*>.*?</a>)", body, re.DOTALL)
        viewer_anchors = [a for a in anchors if "/viewer/" in a.split(">", 1)[0]]
        assert len(viewer_anchors) == 2, (
            f"esperados 2 links mobile do viewer, obtidos {len(viewer_anchors)}"
        )
        texts = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", anchor)).strip()
            for anchor in viewer_anchors
        ]
        assert texts[0] != texts[1], f"links mobile idênticos: {texts!r}"
        assert "relatorio-sesab-1.pdf" in texts[0] or texts[0].startswith("PDF 1")
        assert "relatorio-sesab-2.pdf" in texts[1] or texts[1].startswith("PDF 2")


@pytest.mark.django_db
def test_detail_and_decide_show_text_and_pdf_cards_closed_by_default(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/R3: os dois cards aparecem fechados nas duas páginas (sem show)."""
    case = _make_awaiting_case(nir_user)
    document = list(case.documents.all())[0]
    serve_url = reverse("doctor:case_pdf", args=[case.case_id, document.position])
    doctor = user_factory("medico-cards", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    detail_body, decide_body = _detail_bodies(client, case)

    for body in (detail_body, decide_body):
        assert "Texto Extraído do PDF" in body
        assert "Visualizar PDF Original" in body
        assert "TEXTO EXTRAIDO DO RELATORIO MEDICO" in body
        assert serve_url in unescape(body)
        # Ambos os collapses nascem fechados: nenhum `collapse show` no detalhe.
        assert "collapse show" not in body


@pytest.mark.django_db
def test_detail_and_decide_embed_lazy_without_src(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/R3: embed lazy — data-src aponta o serve, SEM src; sub-collapse marcado."""
    case = _make_awaiting_case(nir_user)
    document = list(case.documents.all())[0]
    serve_url = reverse("doctor:case_pdf", args=[case.case_id, document.position])
    doctor = user_factory("medico-lazy", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    detail_body, decide_body = _detail_bodies(client, case)

    for body in (detail_body, decide_body):
        assert "data-pdf-doc-collapse" in body
        assert f'id="pdf-collapse-{document.position}"' in body
        assert f'data-src="{serve_url}"' in unescape(body)
        assert "pdf-lazy-embed" in body
        # Embed nasce sem `src` (lazy): nenhum `<embed ... src=` no HTML.
        assert "<embed src=" not in body


@pytest.mark.django_db
def test_detail_and_decide_mobile_link_new_tab_and_noscript(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/R3: link mobile (d-md-none) com ?next=, abrir em nova aba e noscript."""
    case = _make_awaiting_case(nir_user)
    document = list(case.documents.all())[0]
    detail_url = reverse("doctor:case_detail", args=[case.case_id])
    serve_url = reverse("doctor:case_pdf", args=[case.case_id, document.position])
    viewer_url = reverse("doctor:pdf_viewer", args=[case.case_id, document.position])
    doctor = user_factory("medico-links", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    detail_body, decide_body = _detail_bodies(client, case)

    for body in (detail_body, decide_body):
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
            body.index("pdf-collapse-") > noscript_end
            or body.index("pdf-collapse-") < noscript_start
        )


@pytest.mark.django_db
def test_detail_and_decide_load_lazy_script(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: pdf-lazy-embed.js carrega no detalhe e na página de trabalho."""
    case = _make_awaiting_case(nir_user)
    doctor = user_factory("medico-script", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    detail_body, decide_body = _detail_bodies(client, case)

    assert "js/pdf-lazy-embed.js" in detail_body
    assert "js/pdf-lazy-embed.js" in decide_body


@pytest.mark.django_db
def test_detail_and_decide_show_placeholder_when_text_empty(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2 (diferença contratual vs NIR): sem extracted_text o card exibe o
    placeholder de indisponibilidade — NÃO omite o card."""
    case = _make_awaiting_case(nir_user, extracted_text="")
    doctor = user_factory("medico-placeholder", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    detail_body, decide_body = _detail_bodies(client, case)

    for body in (detail_body, decide_body):
        assert "Texto Extraído do PDF" in body
        assert "Texto extraído indisponível para este caso." in body
        assert "Visualizar PDF Original" in body


@pytest.mark.django_db
def test_detail_and_decide_replace_documents_table_with_cards(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2: o card "Documentos" some; os cards assumem o ponto do D8."""
    case = _make_awaiting_case(nir_user)
    doctor = user_factory("medico-substituicao", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    detail_body, decide_body = _detail_bodies(client, case)

    for body in (detail_body, decide_body):
        assert '<h2 class="h5 mb-0">Documentos</h2>' not in body
        assert "Visualizar PDF Original" in body


@pytest.mark.django_db
def test_cards_sit_between_prior_case_and_requirements(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/D8: os dois cards ficam no ponto do card antigo (após Prior-case,
    antes de Requisitos) — ordem clínica intacta (R4)."""
    case = _make_awaiting_case(nir_user)
    doctor = user_factory("medico-posicao", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)
    detail_body, decide_body = _detail_bodies(client, case)

    for body in (detail_body, decide_body):
        prior = body.index("Prior-case")
        text = body.index("Texto Extraído do PDF")
        pdf = body.index("Visualizar PDF Original")
        requirements = body.index("Requisitos gerais acionáveis")
        assert prior < text < pdf < requirements


@pytest.mark.django_db
def test_decided_detail_shows_same_cards_read_only(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3: detalhe pós-decisão renderiza os MESMOS cards (read-only)."""
    case = _make_awaiting_case(nir_user)
    deciding = user_factory("medico-decisor", (DOCTOR_ROLE,))
    _decide_case(case, deciding)
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    viewer = user_factory("medico-leitor", (DOCTOR_ROLE,))
    _login(client, viewer, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Texto Extraído do PDF" in body
    assert "Visualizar PDF Original" in body
    assert "TEXTO EXTRAIDO DO RELATORIO MEDICO" in body
    assert "data-pdf-doc-collapse" in body
    assert "<embed src=" not in body
    assert "Texto extraído indisponível" not in body
    assert "Decisões registradas" in body
