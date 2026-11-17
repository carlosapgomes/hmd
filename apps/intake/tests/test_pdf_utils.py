"""Testes de ``apps.intake.pdf_utils`` (slice 002 do change intake-nir-upload).

Os PDFs de fixture são gerados pelo próprio PyMuPDF dentro dos testes (R6):
zero binários no repositório, zero rede. Cobertura:

- R1 ``extract_document_text``: texto extraído, multi-página na ordem, PDF sem
  camada de texto → "" e PDF corrompido → exceção do PyMuPDF.
- R2 ``strip_watermark``: marca d'água repetida (5–6 dígitos do registro)
  removida; texto sem marca permanece idêntico.
- R3 ``extract_agency_record_number``: padrões "Código: XXXXX" e
  "RELATÓRIO DE OCORRÊNCIAS … XXXXX" (case/acentos tolerantes); ausente → None.
- R5b (D3): o nº é extraído do TEXTO BRUTO antes do strip — aparece como marca
  d'água repetida e mesmo assim é preservado.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest

from apps.intake.pdf_utils import (
    WatermarkReport,
    extract_agency_record_number,
    extract_document_text,
    strip_watermark,
)

# Banda de marca d'água típica (premissa corrigida, watermark-strip-hardening):
# a marca quase nunca é o nº de ocorrência — é um token DISTINTO ("48291"),
# presente em ao menos 2 linhas de marca no documento.
_WATERMARK_BAND = "48291 48291 48291 48291"
_WATERMARK_TOKEN = "48291"


@pytest.fixture
def write_pdf(tmp_path: Path) -> Callable[[list[str]], Path]:
    """Gera um PDF real (PyMuPDF) com uma página por texto; devolve o caminho."""

    def _write(pages: list[str]) -> Path:
        path = tmp_path / "relatorio.pdf"
        document = pymupdf.open()  # type: ignore[no-untyped-call]
        try:
            for page_text in pages:
                document.new_page().insert_text((72, 72), page_text)
            document.save(path)  # type: ignore[no-untyped-call]
        finally:
            document.close()  # type: ignore[no-untyped-call]
        return path

    return _write


# ── R1: extração de texto (PDFs gerados em teste) ─────────────────────────


def test_extracts_text(write_pdf: Callable[[list[str]], Path]) -> None:
    """R1/R6: PDF com texto → extração retorna o texto das páginas."""
    path = write_pdf(
        ["RELATÓRIO DE OCORRÊNCIAS\nPaciente: João Maria\nMotivo da Solicitação: cateterismo"]
    )

    text = extract_document_text(str(path))

    assert "RELATÓRIO DE OCORRÊNCIAS" in text
    assert "Paciente: João Maria" in text


def test_multipage_concat(write_pdf: Callable[[list[str]], Path]) -> None:
    """R1/R6: PDF multi-página → concatenação na ordem declarada."""
    path = write_pdf(["PRIMEIRA PAGINA DO RELATORIO", "SEGUNDA PAGINA DO RELATORIO"])

    text = extract_document_text(str(path))

    assert "PRIMEIRA PAGINA DO RELATORIO" in text
    assert "SEGUNDA PAGINA DO RELATORIO" in text
    assert text.index("PRIMEIRA PAGINA") < text.index("SEGUNDA PAGINA")


def test_no_text_layer_empty(write_pdf: Callable[[list[str]], Path]) -> None:
    """R1/R6: PDF válido sem camada de texto → string vazia (o gate retém depois)."""
    path = write_pdf([""])

    assert extract_document_text(str(path)) == ""


def test_corrupt_pdf_raises(tmp_path: Path) -> None:
    """R1: PDF corrompido/ilegível levanta a exceção do PyMuPDF (task converte em FAILED)."""
    path = tmp_path / "corrompido.pdf"
    path.write_bytes(b"%PDF-1.4 conteudo invalido, nao e um pdf de verdade." * 6)

    with pytest.raises(pymupdf.FileDataError):
        extract_document_text(str(path))


# ── R2: remoção de marca d'água ───────────────────────────────────────────


def test_watermark_stripped() -> None:
    """R2: a sequência repetida de dígitos (marca d'água) some; o texto normal fica."""
    text = f"Paciente: João Maria\n{_WATERMARK_BAND}\n{_WATERMARK_BAND}\nRelatório de ocorrências"

    cleaned = strip_watermark(text).text

    assert _WATERMARK_TOKEN not in cleaned
    assert "Paciente: João Maria" in cleaned
    assert "Relatório de ocorrências" in cleaned


def test_watermark_removed_from_extracted_pdf_text(
    write_pdf: Callable[[list[str]], Path],
) -> None:
    """R6: marca d'água presente no texto extraído de um PDF gerado também some."""
    path = write_pdf(
        [f"RELATÓRIO DE OCORRÊNCIAS\nPaciente: João Maria\n{_WATERMARK_BAND}\n{_WATERMARK_BAND}"]
    )

    cleaned = strip_watermark(extract_document_text(str(path))).text

    assert _WATERMARK_TOKEN not in cleaned
    assert "Paciente: João Maria" in cleaned


def test_text_without_watermark_intact() -> None:
    """R2: texto sem marca d'água repetida permanece idêntico (sem normalização indevida)."""
    text = "Código: 12345\nMotivo da Solicitação: cateterismo cardíaco\nPaciente: Maria"

    result = strip_watermark(text)

    assert result.text == text
    assert result.report == WatermarkReport(tokens=0, mark_lines=0, swept_occurrences=0)


# ── Hardening da marca (watermark-strip-hardening, slice 001, R1–R4) ────────


def test_single_mark_line_is_kept() -> None:
    """Linha de marca única não nomeia (≥2 linhas exigidas): permanece, report zerado."""
    text = "Paciente: João Maria\n48291 48291 48291 48291\nRelatório clínico normal"

    result = strip_watermark(text)

    assert result.text == text
    assert result.report == WatermarkReport(tokens=0, mark_lines=0, swept_occurrences=0)


def test_two_full_band_lines_removed() -> None:
    """Duas linhas de banda cheia do mesmo token nomeiam e são descartadas."""
    text = (
        "Paciente: João Maria\n48291 48291 48291 48291\n48291 48291 48291 48291\nRelatório clínico"
    )

    result = strip_watermark(text)

    assert "48291" not in result.text
    assert "Paciente: João Maria" in result.text
    assert result.report == WatermarkReport(tokens=1, mark_lines=2, swept_occurrences=0)


def test_standalone_and_partial_lines_removed() -> None:
    """Linha solta + linha parcial (2 linhas de marca) também nomeiam e somem."""
    text = "48291\n48291 48291\nConteúdo clínico preservado"

    result = strip_watermark(text)

    assert result.text == "Conteúdo clínico preservado"
    assert result.report == WatermarkReport(tokens=1, mark_lines=2, swept_occurrences=0)


def test_inline_occurrence_of_non_record_token_preserved() -> None:
    """Token ≠ nº: a citação inline em linha clínica permanece (sem sweep)."""
    text = (
        "48291 48291 48291 48291\n48291 48291 48291 48291\n"
        "Protocolo 48291 encaminhado para avaliação\nConteúdo clínico"
    )

    result = strip_watermark(text)

    assert "Protocolo 48291 encaminhado para avaliação" in result.text
    assert result.report == WatermarkReport(tokens=1, mark_lines=2, swept_occurrences=0)


def test_record_coincident_token_swept_globally() -> None:
    """Token == nº: linhas de marca E ocorrências inline são varridas (sweep D2)."""
    text = (
        "Código: 33345\n33345 33345 33345 33345\n33345 33345 33345 33345\n"
        "Protocolo 33345 encaminhado para avaliação"
    )

    result = strip_watermark(text, record_number="33345")

    assert "33345" not in result.text
    assert "Protocolo encaminhado para avaliação" in result.text
    assert result.report == WatermarkReport(tokens=1, mark_lines=2, swept_occurrences=2)


def test_report_counts_mixed() -> None:
    """Contagens (D3): 3 linhas de marca mistas → tokens=1, mark_lines=3, swept=0."""
    text = "48291\n48291 48291\n48291 48291 48291 48291\nConteúdo clínico"

    result = strip_watermark(text)

    assert "48291" not in result.text
    assert result.report.tokens == 1
    assert result.report.mark_lines == 3
    assert result.report.swept_occurrences == 0


class _FakePage:
    """Página falsa cujo get_text() NÃO termina em \\n (fronteira de página, D4)."""

    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text


class _FakeDocument:
    """Documento falso no seam do pymupdf.open (páginas sem \\n final)."""

    def __init__(self, pages: list[str]) -> None:
        self._pages = [_FakePage(page_text) for page_text in pages]

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def __getitem__(self, index: int) -> _FakePage:
        return self._pages[index]

    def close(self) -> None:
        pass


def test_page_join_separates_lines_without_trailing_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fronteira de página (D4): sem \\n final, as páginas não fundem linhas de marca."""

    def _fake_open(path: str) -> _FakeDocument:
        return _FakeDocument(
            [
                "Cabeçalho clínico\n48291 48291",
                "48291 48291\nSegunda página de conteúdo",
            ]
        )

    monkeypatch.setattr(pymupdf, "open", _fake_open)

    result = strip_watermark(extract_document_text("relatorio-falso.pdf"))

    assert "48291" not in result.text
    assert "Cabeçalho clínico" in result.text
    assert "Segunda página de conteúdo" in result.text
    assert result.report == WatermarkReport(tokens=1, mark_lines=2, swept_occurrences=0)


# ── R3: nº de ocorrência (padrões explícitos) ─────────────────────────────


def test_record_number_patterns() -> None:
    """R3: padrão "Código: XXXXX" (case/acentos/espacos tolerantes)."""
    assert (
        extract_agency_record_number("RELATÓRIO DE OCORRÊNCIAS\nCódigo: 12345\nPaciente: João")
        == "12345"
    )
    assert extract_agency_record_number("CODIGO : 54321") == "54321"
    assert extract_agency_record_number("codigo: 98765") == "98765"
    assert extract_agency_record_number("CÓDIGO:11111") == "11111"

    # Padrão "RELATÓRIO DE OCORRÊNCIAS … XXXXX" (tolerante a case/acentos).
    assert (
        extract_agency_record_number(
            "RELATÓRIO DE OCORRÊNCIAS\nAbertura: 01/01/2025\nRegistro: 77777"
        )
        == "77777"
    )
    assert extract_agency_record_number("relatorio de ocorrencias 33333") == "33333"


def test_record_number_absent() -> None:
    """R3: sem os padrões explícitos → None (nunca um fallback inventado)."""
    text = (
        "Laudo de eletrocardiograma sem o cabeçalho do relatório "
        "nem seção Código com número no documento."
    )

    assert extract_agency_record_number(text) is None


# ── R5b: ordem obrigatória — nº extraído do TEXTO BRUTO antes do strip ────


def test_record_extracted_from_raw_text_before_strip() -> None:
    """R5b: o nº que aparece TAMBÉM como marca d'água repetida é extraído do bruto.

    O texto limpo não contém mais a sequência, mas o nº foi preservado porque
    a extração aconteceu antes do ``strip_watermark`` (D3 — divergência HMD: o
    ats-web resolve numa função combinada; aqui a ordem é explícita).
    """
    # Caso raro pinado por este teste: a marca COINCIDE com o nº ("33345" em 2
    # linhas de marca, com sweep global via record_number — D2); a forma
    # observada (marca distinta do nº) é a das demais fixtures.
    raw = (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "Código: 33345\n"
        "Motivo da Solicitação: cateterismo cardíaco\n"
        "33345 33345 33345 33345\n"
        "33345 33345 33345 33345\n"
    )

    record_number = extract_agency_record_number(raw)
    cleaned = strip_watermark(raw, record_number=record_number).text

    assert record_number == "33345"
    assert "33345" not in cleaned
