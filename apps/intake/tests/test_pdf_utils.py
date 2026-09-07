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
    extract_agency_record_number,
    extract_document_text,
    strip_watermark,
)

# Banda de marca d'água típica: o registro (5–6 dígitos) repetido na página.
_WATERMARK_BAND = "33345 33345 33345 33345"
_WATERMARK_TOKEN = "33345"


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
    text = f"Paciente: João Maria\n{_WATERMARK_BAND}\nRelatório de ocorrências"

    cleaned = strip_watermark(text)

    assert _WATERMARK_TOKEN not in cleaned
    assert "Paciente: João Maria" in cleaned
    assert "Relatório de ocorrências" in cleaned


def test_watermark_removed_from_extracted_pdf_text(
    write_pdf: Callable[[list[str]], Path],
) -> None:
    """R6: marca d'água presente no texto extraído de um PDF gerado também some."""
    path = write_pdf([f"RELATÓRIO DE OCORRÊNCIAS\nPaciente: João Maria\n{_WATERMARK_BAND}"])

    cleaned = strip_watermark(extract_document_text(str(path)))

    assert _WATERMARK_TOKEN not in cleaned
    assert "Paciente: João Maria" in cleaned


def test_text_without_watermark_intact() -> None:
    """R2: texto sem marca d'água repetida permanece idêntico (sem normalização indevida)."""
    text = "Código: 12345\nMotivo da Solicitação: cateterismo cardíaco\nPaciente: Maria"

    assert strip_watermark(text) == text


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
    raw = (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "Código: 33345\n"
        "Motivo da Solicitação: cateterismo cardíaco\n"
        f"{_WATERMARK_BAND}\n"
    )

    record_number = extract_agency_record_number(raw)
    cleaned = strip_watermark(raw)

    assert record_number == "33345"
    assert "33345" not in cleaned
