"""Guardas dos estáticos do viewer de PDF original (slice PVP-001, R3).

Segue o padrão de guarda em disco de ``test_theme_css.py``: sem runner de
browser, assertions estruturais nos JS + presença dos vendor files de PDF.js.
A prova comportamental do lazy (expansão de collapse no browser) fica no
runtime de PVP-002, primeira página consumidora.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template import engines
from django.test import RequestFactory

STATIC_JS_DIR = Path(settings.BASE_DIR) / "static" / "js"
VENDOR_PDFJS_DIR = Path(settings.BASE_DIR) / "static" / "vendor" / "pdfjs"


def test_pdfjs_vendor_files_present() -> None:
    """PDF.js vendored (versão exata do molde) presente em disco."""
    assert (VENDOR_PDFJS_DIR / "pdf.mjs").is_file(), "static/vendor/pdfjs/pdf.mjs ausente"
    assert (VENDOR_PDFJS_DIR / "pdf.worker.mjs").is_file(), (
        "static/vendor/pdfjs/pdf.worker.mjs ausente"
    )


def test_pdfjs_vendor_version_matches_mold() -> None:
    """Versão vendored é a mesma do molde (4.0.379, comportamento conhecido)."""
    assert "4.0.379" in (VENDOR_PDFJS_DIR / "pdf.mjs").read_text(encoding="utf-8")


def test_pdf_viewer_js_present() -> None:
    """Port do viewer JS presente em disco."""
    assert (STATIC_JS_DIR / "pdf-viewer.js").is_file(), "static/js/pdf-viewer.js ausente"


def test_pdf_viewer_js_exports_init() -> None:
    """Viewer JS exporta initPdfViewer e usa IntersectionObserver (lazy)."""
    text = (STATIC_JS_DIR / "pdf-viewer.js").read_text(encoding="utf-8")
    assert "initPdfViewer" in text
    assert "IntersectionObserver" in text
    assert "getDocument" in text


def test_pdf_lazy_embed_listens_per_target_collapse() -> None:
    """Lazy-embed ouve show.bs.collapse por sub-collapse-alvo (data-src→src)."""
    text = (STATIC_JS_DIR / "pdf-lazy-embed.js").read_text(encoding="utf-8")
    assert "show.bs.collapse" in text
    assert "data-src" in text
    assert ".pdf-lazy-embed" in text


def test_pdf_lazy_embed_targets_doc_subcollapse_only() -> None:
    """Filtro por sub-collapse de DOCUMENTO: card externo nunca carrega (D2).

    O listener deve filtrar o ``event.target`` pelo identificador explícito
    dos sub-collapses de documento (``data-pdf-doc-collapse``): abrir o card
    externo (alvo sem o atributo) não atribui ``src`` a nenhum embed.
    """
    text = (STATIC_JS_DIR / "pdf-lazy-embed.js").read_text(encoding="utf-8")
    assert "data-pdf-doc-collapse" in text
    assert "event.target" in text
    assert ("hasAttribute" in text) or ("closest(" in text) or (".matches(" in text)


def test_pdf_lazy_embed_resize_uses_doc_subcollapse_selector() -> None:
    """Resize usa o MESMO identificador (card externo fora do seletor).

    O seletor de sub-collapses abertos no resize deve incluir
    ``[data-pdf-doc-collapse]`` — o seletor genérico ``.collapse.show`` casa
    com o card externo e recarregaria PDFs de documentos fechados.
    """
    text = (STATIC_JS_DIR / "pdf-lazy-embed.js").read_text(encoding="utf-8")
    assert "[data-pdf-doc-collapse]" in text
    assert ".collapse.show embed" not in text
    assert 'querySelectorAll(".collapse.show' not in text


def test_pdf_lazy_embed_embeds_searched_within_target_only() -> None:
    """Embeds buscados APENAS dentro do sub-collapse-alvo (D2).

    O handler de ``show.bs.collapse`` deve buscar embeds via
    ``target.querySelectorAll`` — seleção global em ``document`` dentro do
    handler carregaria PDFs de sub-collapses fechados ao abrir o card
    externo. Também é proibido teste literal de visibilidade do embed
    (D2: o evento dispara com o pai ainda fechado).
    """
    text = (STATIC_JS_DIR / "pdf-lazy-embed.js").read_text(encoding="utf-8")
    assert "target.querySelectorAll" in text
    assert "offsetParent" not in text
    assert "getClientRects" not in text
    assert "checkVisibility" not in text


def test_pdf_lazy_embed_single_assignment_flag() -> None:
    """Lazy-embed atribui src uma única vez por elemento (flag por elemento)."""
    text = (STATIC_JS_DIR / "pdf-lazy-embed.js").read_text(encoding="utf-8")
    assert "dataset" in text
    assert "src" in text


def test_pdf_lazy_embed_desktop_breakpoint_gate() -> None:
    """Lazy-embed só busca com o breakpoint desktop ativo (matchMedia md)."""
    text = (STATIC_JS_DIR / "pdf-lazy-embed.js").read_text(encoding="utf-8")
    assert "matchMedia" in text
    assert "(min-width: 768px)" in text


def test_pdf_lazy_embed_viewport_change_listener() -> None:
    """Lazy-embed reage à mudança de viewport (sub-collapses já abertos)."""
    text = (STATIC_JS_DIR / "pdf-lazy-embed.js").read_text(encoding="utf-8")
    assert ".show" in text
    assert "change" in text


def test_mobile_viewer_template_renders() -> None:
    """Template mobile renderiza Voltar ×2 + data attrs + extra_js (R2)."""
    engine = engines["django"]
    template = engine.get_template("pdf_viewer/mobile_pdf_viewer.html")
    context = {
        "viewer_title": "Relatório SESAB",
        "pdf_url": "/serve/documento.pdf",
        "back_url": "/casos/abc/",
        "fallback_pdf_url": "/serve/documento.pdf",
    }
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    body = template.render(context, request=request)
    assert body.count("← Voltar") == 2
    assert 'data-pdf-url="/serve/documento.pdf"' in body
    assert 'data-fallback-pdf-url="/serve/documento.pdf"' in body
    assert "vendor/pdfjs/pdf.mjs" in body
    assert "vendor/pdfjs/pdf.worker.mjs" in body
    assert "js/pdf-viewer.js" in body


def test_mobile_viewer_nav_inside_scrollable_content() -> None:
    """Nav do viewer dentro do bloco de conteúdo (sticky acompanha a rolagem).

    A nav sticky do viewer deve ser renderizada dentro do ``block content``
    (que envolve a área rolável do viewer, ``<main>``), e não no ``block
    nav`` — que o ``base.html`` coloca dentro do ``<header>`` de altura
    limitada, onde o sticky fica preso e não acompanha a rolagem (D3).
    """
    source = (
        Path(settings.BASE_DIR) / "templates" / "pdf_viewer" / "mobile_pdf_viewer.html"
    ).read_text(encoding="utf-8")
    assert "pdf-viewer-nav" in source
    nav_open = source.index("{% block nav %}") if "{% block nav %}" in source else -1
    content_open = source.index("{% block content %}")
    nav_class_pos = source.index("pdf-viewer-nav")
    content_close_positions = [
        i for i in range(len(source)) if source.startswith("{% endblock %}", i)
    ]
    content_close = min(i for i in content_close_positions if i > content_open)
    if nav_open != -1:
        nav_close = min(i for i in content_close_positions if i > nav_open)
        nav_block_body = source[nav_open:nav_close]
        assert "pdf-viewer-nav" not in nav_block_body
    assert content_open < nav_class_pos < content_close


def test_mobile_viewer_pdfjs_import_inside_caught_chain() -> None:
    """pdf.mjs via import DINÂMICO na cadeia tratada pelo catch (D3).

    Import estático de ``vendor/pdfjs/pdf.mjs`` resolve antes do corpo do
    módulo: se falhar, o ``catch`` nunca é alcançado e o spinner fica eterno
    sem o bloco de erro com o link fallback (``data-fallback-pdf-url``).
    O template deve importar o pdf.mjs dinamicamente dentro da mesma cadeia
    tratada pelo ``catch``.
    """
    source = (
        Path(settings.BASE_DIR) / "templates" / "pdf_viewer" / "mobile_pdf_viewer.html"
    ).read_text(encoding="utf-8")
    assert "import * as pdfjsLib from" not in source
    assert "import(" in source
    assert "vendor/pdfjs/pdf.mjs" in source
    assert ".catch" in source
    assert "pdf-error" in source
