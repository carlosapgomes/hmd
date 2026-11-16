"""Bloco "Arquivo" nos cards de anexo do médico (slice PVP-006, R3).

Cada card de anexo ganha, após a evidência, o bloco "Arquivo" com o mesmo
padrão do relatório: PDF → collapse lazy (``data-pdf-doc-collapse`` +
``embed.pdf-lazy-embed`` com ``data-src`` SEM ``src``) + link mobile ao
viewer dedicado identificando o arquivo; imagem → collapse com
``<img loading="lazy" src=serve>`` + link direto. ``<noscript>`` com links
diretos por anexo. Sem anexos: páginas idênticas a antes (sem a seção).

Cobertura dupla: detalhe read-only (``doctor:case_detail``) E página de
trabalho (``doctor:case_decide``) — uma edição única no partial.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from html import unescape
from html.parser import HTMLParser

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.attachments.models import CaseAttachment
from apps.cases.models import Case, CaseProcedure, CaseStatus

DOCTOR_ROLE = "doctor"

ANGIO_TYPE = "art_perif"
SYSTEM_ROLE = "system"

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


def _create_case_with_declared(created_by: User) -> Case:
    """Cria um caso com a row declarada (sem transições de status)."""
    case = Case.objects.create(created_by=created_by)
    CaseProcedure.objects.create(
        case=case,
        procedure_type=ANGIO_TYPE,
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


def _add_attachment(case: Case, uploaded_by: User, uploaded: SimpleUploadedFile) -> CaseAttachment:
    """Persiste um anexo com arquivo (espelho de ``_save_attachment`` de attachments)."""
    attachment = CaseAttachment(
        case=case,
        content_type=(uploaded.content_type or "").lower(),
        original_filename=uploaded.name or "",
        size_bytes=uploaded.size or 0,
        uploaded_by=uploaded_by,
    )
    attachment.file.save(uploaded.name or "", uploaded, save=False)
    attachment.save()
    return attachment


def _pdf_attachment(name: str = "laudo-anexo.pdf") -> SimpleUploadedFile:
    """Anexo PDF fake (metadados apenas — as páginas não leem o conteúdo)."""
    return SimpleUploadedFile(
        name,
        b"%PDF-1.4 conteudo do anexo pdf",
        content_type="application/pdf",
    )


def _jpeg_attachment(name: str = "foto-exame.jpg") -> SimpleUploadedFile:
    """Anexo JPEG fake (metadados apenas — as páginas não leem o conteúdo)."""
    return SimpleUploadedFile(
        name,
        b"\xff\xd8\xff\xe0 conteudo fake do jpeg",
        content_type="image/jpeg",
    )


def _make_case(nir_user: User, uploads: list[SimpleUploadedFile]) -> Case:
    """Caso em AWAITING_DOCTOR com os anexos dados (cenário base dos cards)."""
    case = _create_case_with_declared(nir_user)
    _advance_to_awaiting_doctor(case)
    for upload in uploads:
        _add_attachment(case, nir_user, upload)
    return case


def _login(client: Client, user: User, role: str) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _bodies(client: Client, case: Case, doctor: User) -> tuple[str, str]:
    """Corpos das duas páginas (detalhe read-only + trabalho AWAITING)."""
    _login(client, doctor, DOCTOR_ROLE)
    detail = client.get(reverse("doctor:case_detail", args=[case.case_id]))
    assert detail.status_code == 200
    decide = client.get(reverse("doctor:case_decide", args=[case.case_id]))
    assert decide.status_code == 200
    return detail.content.decode(), decide.content.decode()


def _serve_url(case: Case, attachment: CaseAttachment) -> str:
    return reverse("doctor:serve_attachment", args=[case.case_id, attachment.pk])


def _viewer_url(case: Case, attachment: CaseAttachment) -> str:
    return reverse("doctor:attachment_pdf_viewer", args=[case.case_id, attachment.pk])


@pytest.mark.django_db
def test_pdf_attachment_card_has_file_block_after_evidence(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3: card do anexo PDF ganha bloco "Arquivo" após a evidência, com lazy."""
    case = _make_case(nir_user, [_pdf_attachment()])
    attachment = case.attachments.get()
    serve_url = _serve_url(case, attachment)
    viewer_url = _viewer_url(case, attachment)
    doctor = user_factory("medico-arquivo", (DOCTOR_ROLE,))

    detail_body, decide_body = _bodies(client, case, doctor)

    for body in (detail_body, decide_body):
        plain = unescape(body)
        # Bloco "Arquivo" dentro do card do anexo, após a evidência.
        arquivo_pos = body.index("Arquivo")
        assert body.index(attachment.original_filename) < arquivo_pos
        # Abrir em nova aba + link direto apontam o serve do papel.
        assert f'href="{serve_url}"' in body
        assert 'target="_blank"' in body
        # Collapse lazy do PDF com o contrato do embed (data-src SEM src).
        assert f'id="attachment-file-{attachment.pk}"' in body
        assert "data-pdf-doc-collapse" in body
        assert f'data-src="{serve_url}"' in plain
        assert "pdf-lazy-embed" in body
        assert "<embed src=" not in body
        # Link mobile ao viewer dedicado, identificando o arquivo.
        assert viewer_url in plain
        assert "Anexo 1 — laudo-anexo.pdf" in body
        assert reverse("doctor:case_detail", args=[case.case_id]) in plain


@pytest.mark.django_db
def test_jpeg_attachment_card_has_lazy_image_without_viewer(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3: card do anexo JPEG ganha <img loading=lazy src=serve>, sem viewer."""
    case = _make_case(nir_user, [_jpeg_attachment()])
    attachment = case.attachments.get()
    serve_url = _serve_url(case, attachment)
    doctor = user_factory("medico-imagem", (DOCTOR_ROLE,))

    detail_body, decide_body = _bodies(client, case, doctor)

    for body in (detail_body, decide_body):
        plain = unescape(body)
        assert "Arquivo" in body
        assert f'src="{serve_url}"' in plain
        assert 'loading="lazy"' in body
        # O card do relatório tem o próprio embed lazy; o bloco do anexo usa <img>.
        attachment_block = plain[plain.index("Anexos") :]
        assert "<embed" not in attachment_block
        assert "data-pdf-doc-collapse" not in attachment_block
        # Imagem não tem viewer dedicado: nenhuma URL de viewer do anexo.
        assert f"attachments/{attachment.pk}/viewer/" not in plain
        assert f'id="attachment-file-{attachment.pk}"' in body


@pytest.mark.django_db
def test_attachment_cards_noscript_has_direct_links(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3: bloco <noscript> com links diretos de abertura por anexo."""
    case = _make_case(nir_user, [_pdf_attachment(), _jpeg_attachment("foto-2.jpg")])
    attachments = list(case.attachments.all())
    doctor = user_factory("medico-noscript", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    plain = unescape(body)
    noscript_start = body.index("<noscript>")
    noscript_end = body.index("</noscript>")
    noscript_block = plain[noscript_start:noscript_end]
    for attachment in attachments:
        assert _serve_url(case, attachment) in noscript_block
        assert attachment.original_filename in noscript_block


class _DivBalance(HTMLParser):
    """Conta ``<div>`` abertos vs ``</div>`` fechados (P1: órfãos no partial)."""

    def __init__(self) -> None:
        super().__init__()
        self.opened = 0
        self.closed = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag == "div":
            self.opened += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            self.closed += 1


def _div_balance(fragment: str) -> tuple[int, int]:
    parser = _DivBalance()
    parser.feed(fragment)
    return parser.opened, parser.closed


@pytest.mark.django_db
def test_anexos_wrapper_hierarchy_on_both_pages(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """P1: com anexos, o heading "Anexos" existe e o partial é balanceado.

    O wrapper externo (card + ``card-body``) deve conter os cards por anexo e
    os cards seguintes ("Requisitos gerais acionáveis"/"Alertas
    consultivos") devem permanecer DENTRO do container coluna principal
    das duas páginas — sem os ``</div>`` órfãos que escapavam o layout.
    """
    case = _make_case(nir_user, [_pdf_attachment(), _jpeg_attachment()])
    doctor = user_factory("medico-hierarquia", (DOCTOR_ROLE,))

    detail_body, decide_body = _bodies(client, case, doctor)

    for body in (detail_body, decide_body):
        # (a) O heading "Anexos" existe (D8 preserva a seção).
        assert '<h2 class="h5 mb-0">Anexos</h2>' in body
        # (b) O partial não deixa ``</div>`` órfãos: abertos == fechados.
        opened, closed = _div_balance(body)
        assert opened == closed, f"divs desbalanceados: {opened} abertos vs {closed} fechados"
        # (c) Os cards seguintes permanecem após a seção de anexos, dentro
        # do container coluna principal (antes do fechamento da coluna).
        anexos_pos = body.index(">Anexos</h2>")
        requisitos_pos = body.index("Requisitos gerais acionáveis")
        alertas_pos = body.index("Alertas consultivos")
        assert anexos_pos < requisitos_pos < alertas_pos


@pytest.mark.django_db
def test_pages_without_attachments_have_no_file_block(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3: caso sem anexos → páginas idênticas a antes (sem seção nem URLs)."""
    case = _make_case(nir_user, [])
    doctor = user_factory("medico-sem-anexos", (DOCTOR_ROLE,))

    detail_body, decide_body = _bodies(client, case, doctor)

    for body in (detail_body, decide_body):
        assert "Anexos" not in body
        assert "Arquivo" not in body
        assert "attachments/" not in unescape(body)
