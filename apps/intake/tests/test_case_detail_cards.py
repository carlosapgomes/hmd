"""Cards de texto extraído + PDF original no detalhe do NIR (slice PVP-002, R2).

A tabela "Documentos" é substituída pelos dois cards fechados por padrão
(D1: ``pdf_items`` montado na view): card de texto em ``<pre>`` (omitido sem
``extracted_text``) + card do PDF original com um sub-collapse por documento
(``data-pdf-doc-collapse``, embed lazy com ``data-src`` SEM ``src``), link
mobile (``d-md-none``) com ``?next=`` do detalhe, "Abrir em nova aba" e bloco
``<noscript>`` com links diretos fora dos collapses. O JS lazy só carrega
nesta página (bloco ``extra_js``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from html import unescape

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.models import Case
from apps.intake.services import create_case_with_documents

NIR_ROLE = "nir"


@pytest.fixture(autouse=True)
def _no_inline_processing() -> Iterator[None]:
    """Criação sem processamento automático: o teste controla o conteúdo."""

    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


def _create_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    *,
    extracted_text: str = "TEXTO EXTRAIDO DO RELATORIO",
) -> Case:
    """Cria um caso NEW com 1 PDF e o texto extraído informado."""
    case = create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(),
        procedure_type="art_perif",
    )
    Case.objects.filter(pk=case.pk).update(extracted_text=extracted_text)
    case.refresh_from_db()
    return case


def _detail_body(client: Client, case: Case) -> str:
    response = client.get(reverse("intake:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_detail_shows_text_and_pdf_cards_closed_by_default(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: os dois cards aparecem fechados por padrão (sem classe show)."""
    case = _create_case(nir_user, pdf_factory)
    document = list(case.documents.all())[0]
    serve_url = reverse("intake:serve_document", args=[case.case_id, document.pk])

    client.force_login(nir_user)
    body = _detail_body(client, case)

    assert "Texto Extraído do PDF" in body
    assert "Visualizar PDF Original" in body
    assert "TEXTO EXTRAIDO DO RELATORIO" in body
    assert serve_url in unescape(body)
    # Ambos os collapses nascem fechados: nenhum `collapse show` no detalhe.
    assert "collapse show" not in body


@pytest.mark.django_db
def test_detail_embed_lazy_without_src_and_with_doc_collapse_attribute(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: embed lazy — data-src aponta o serve, SEM src; sub-collapse marcado."""
    case = _create_case(nir_user, pdf_factory)
    document = list(case.documents.all())[0]
    serve_url = reverse("intake:serve_document", args=[case.case_id, document.pk])

    client.force_login(nir_user)
    body = _detail_body(client, case)

    assert "data-pdf-doc-collapse" in body
    assert f'id="pdf-collapse-{document.position}"' in body
    assert f'data-src="{serve_url}"' in unescape(body)
    assert "pdf-lazy-embed" in body
    # Embed nasce sem `src` (lazy): nenhum `<embed ... src=` no HTML.
    assert "<embed src=" not in body


@pytest.mark.django_db
def test_detail_mobile_link_new_tab_and_noscript(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: link mobile (d-md-none) com ?next=, abrir em nova aba e noscript."""
    case = _create_case(nir_user, pdf_factory)
    document = list(case.documents.all())[0]
    detail_url = reverse("intake:case_detail", args=[case.case_id])
    serve_url = reverse("intake:serve_document", args=[case.case_id, document.pk])
    viewer_url = reverse("intake:pdf_viewer", args=[case.case_id, document.pk])

    client.force_login(nir_user)
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
def test_detail_loads_lazy_embed_script_only_on_detail(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: pdf-lazy-embed.js carrega no detalhe (bloco extra_js), não na lista."""
    case = _create_case(nir_user, pdf_factory)

    client.force_login(nir_user)
    detail_body = _detail_body(client, case)
    list_response = client.get(reverse("intake:my_cases"))
    assert list_response.status_code == 200
    list_body = list_response.content.decode()

    assert "js/pdf-lazy-embed.js" in detail_body
    assert "js/pdf-lazy-embed.js" not in list_body


@pytest.mark.django_db
def test_detail_omits_text_card_without_extracted_text(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: sem extracted_text o card de texto é omitido (PDF permanece)."""
    case = _create_case(nir_user, pdf_factory, extracted_text="")

    client.force_login(nir_user)
    body = _detail_body(client, case)

    assert "Texto Extraído do PDF" not in body
    assert "Visualizar PDF Original" in body


@pytest.mark.django_db
def test_detail_replaces_documents_table_with_cards(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: a tabela "Documentos" some; os cards assumem o ponto do D8."""
    case = _create_case(nir_user, pdf_factory)

    client.force_login(nir_user)
    body = _detail_body(client, case)

    assert '<h2 class="h5 mb-0">Documentos</h2>' not in body
    assert "Visualizar PDF Original" in body
