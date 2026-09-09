"""Extração híbrida de texto dos anexos (change attachment-processing-ocr,
slice 002, design D3).

``extract_attachment_text`` decide por anexo: PDF com camada de texto (soma do
texto das páginas > ``PDF_TEXT_MIN_CHARS``) → extração local via PyMuPDF
(reuso do ``apps/intake/pdf_utils.py::extract_document_text`` — a caixa
determinística do relatório), SEM chamada externa (``local_pdf``); senão
(imagem OU PDF sem camada) → OCR externo via ``vision.transcribe_image``
(``vision``): imagem vai DIRETO com seus bytes/MIME; PDF-imagem tem as páginas
rasterizadas em PNG (fitz ``page.get_pixmap()``) sob o teto
``ATTACHMENTS_VISION_MAX_PAGES`` (excedente → ``AttachmentPageLimitExceededError``,
erro nomeado que o worker converte em ``failed`` com motivo claro); as
transcrições por página são concatenadas com ``\\n\\n``.

O envio externo é o único efeito fora desta caixa: o chamador (worker) injeta
``on_external_dispatch`` (evento de auditoria) e esta função o invoca uma
única vez, IMEDIATAMENTE antes da primeira chamada ao OCR — nunca para o
caminho local. A extração em si é pura (sem eventos/transições).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence

import pymupdf

from apps.attachments import vision
from apps.attachments.models import CaseAttachment, ExtractionMethod
from apps.intake.pdf_utils import extract_document_text

# Limiar mínimo de camada de texto (soma das páginas) para considerar o PDF
# como "com texto" e extrair localmente (R1/D3, > 30 chars).
PDF_TEXT_MIN_CHARS = 30

# Content-type das páginas rasterizadas de um PDF-imagem (PNG).
RASTER_CONTENT_TYPE = "image/png"

PDF_CONTENT_TYPE = "application/pdf"


class AttachmentPageLimitExceededError(ValueError):
    """PDF-imagem acima de ``ATTACHMENTS_VISION_MAX_PAGES`` (R1/D3).

    Erro nomeado lançado ANTES de qualquer envio externo — o worker o converte
    em ``failed`` com motivo claro (teto de páginas por anexo).
    """

    def __init__(self, page_count: int, max_pages: int) -> None:
        super().__init__(
            f"anexo PDF-imagem com {page_count} páginas excede o teto de "
            f"{max_pages} páginas por anexo para OCR (ATTACHMENTS_VISION_MAX_PAGES)"
        )


def extract_attachment_text(
    attachment: CaseAttachment,
    *,
    on_external_dispatch: Callable[[], None] | None = None,
) -> tuple[str, str]:
    """Extrai o texto do anexo e devolve ``(texto, método)`` (R1).

    PDF com camada de texto (> 30 chars) → ``(texto, "local_pdf")`` via
    PyMuPDF, sem chamada externa; imagem/PDF-imagem → ``(texto, "vision")``
    com as páginas transcrições concatenadas por ``\\n\\n`` (imagem direto;
    PDF rasterizado em PNG sob o teto de páginas). ``on_external_dispatch``
    (auditoria) roda uma única vez, antes da primeira chamada ao OCR externo.

    Raises:
        AttachmentPageLimitExceededError: PDF-imagem acima do teto de páginas
            (nada foi enviado).
    """
    content = _read_attachment_bytes(attachment)
    content_type = (attachment.content_type or "").lower()
    if content_type == PDF_CONTENT_TYPE:
        text = _local_pdf_text(content)
        if len(text) > PDF_TEXT_MIN_CHARS:
            return text, ExtractionMethod.LOCAL_PDF
        pages: Sequence[tuple[bytes, str]] = [
            (image_bytes, RASTER_CONTENT_TYPE) for image_bytes in _rasterize_pdf_pages(content)
        ]
    else:
        pages = [(content, content_type)]
    return _transcribe_pages(pages, on_external_dispatch), ExtractionMethod.VISION


# ── Helpers internos ──────────────────────────────────────────────────────


def _read_attachment_bytes(attachment: CaseAttachment) -> bytes:
    """Lê o arquivo do anexo independente do storage (in-memory incluso)."""
    attachment.file.open("rb")
    try:
        content: bytes = attachment.file.read()
        return content
    finally:
        attachment.file.close()


def _local_pdf_text(content: bytes) -> str:
    """Camada de texto do PDF via helper do intake (abre por caminho).

    Copia para um arquivo temporário sempre (custo desprezível e funciona
    para qualquer storage). PDF corrompido propaga a exceção do PyMuPDF — o
    worker a converte em ``failed`` por anexo.
    """
    descriptor, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        return extract_document_text(path)
    finally:
        os.unlink(path)


def _rasterize_pdf_pages(content: bytes) -> list[bytes]:
    """Rasteriza cada página do PDF em PNG (fitz ``get_pixmap``).

    Valida o teto ``settings.ATTACHMENTS_VISION_MAX_PAGES`` ANTES de renderizar
    (excedente → ``AttachmentPageLimitExceededError``, nada é enviado).
    """
    from django.conf import settings

    max_pages = settings.ATTACHMENTS_VISION_MAX_PAGES
    document = pymupdf.open(stream=content, filetype="pdf")  # type: ignore[no-untyped-call]
    try:
        if document.page_count > max_pages:
            raise AttachmentPageLimitExceededError(document.page_count, max_pages)
        rendered: list[bytes] = []
        for page_number in range(document.page_count):
            page = document[page_number]
            pixmap = page.get_pixmap()
            image_bytes = pixmap.tobytes("png")  # type: ignore[no-untyped-call]
            rendered.append(image_bytes)
        return rendered
    finally:
        document.close()  # type: ignore[no-untyped-call]


def _transcribe_pages(
    pages: Sequence[tuple[bytes, str]],
    on_external_dispatch: Callable[[], None] | None,
) -> str:
    """Transcreve as páginas via OCR externo e concatena com ``\\n\\n``.

    Pré-checagem de configuração ANTES do dispatch (review slice 002):
    ``ensure_vision_ready`` sobe ``LlmError`` de config/auth sem que nada
    seja enviado — o evento de auditoria ``DISPATCHED`` só é gravado quando
    um envio externo é de fato possível (sem evento fantasma em ambiente mal
    configurado). O dispatch roda ANTES da primeira transcrição — ordem
    exigida pelo contrato (evento anterior a qualquer envio externo).
    """
    vision.ensure_vision_ready()
    if on_external_dispatch is not None:
        on_external_dispatch()
    transcriptions = [
        vision.transcribe_image(image_bytes, content_type) for image_bytes, content_type in pages
    ]
    return "\n\n".join(transcriptions)
