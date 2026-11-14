"""Gera os PNGs do ícone PWA do HMD (change dashboard-notifications-pwa, D4/R1).

Uso:
    uv run python scripts/generate_pwa_icons.py          # gera/atualiza os PNGs
    uv run python scripts/generate_pwa_icons.py --check  # falha se algum PNG faltar

Os PNGs são **commitados** (reprodutíveis por este script); o ``--check`` é a
porta de CI: sai com 0 só quando todos os PNGs esperados por ``static/
manifest.json`` estão presentes no diretório ``static/icons/``.

Desenho (mesma geometria dos SVGs hand-written ``hmd-base.svg``/
``hmd-maskable.svg``): quadrado na cor do app (``--hmd-primary`` de
``static/css/app.css``, base do gradiente do ``.app-header``), faixa
superior de família SAR (secundária do tema, sem texto pequeno — D6) e
letras "HMD" em Helvetica-Bold branco centralizadas abaixo da faixa. O
base tem cantos arredondados e transparência fora do retângulo; o
maskable é full-bleed com o conteúdo em ~80% central (safe zone
recortada por launchers adaptativos).

Esta é a única fonte de verdade da geometria em Python; o SVG é a versão
vetorial equivalente. Ao mudar cor/proporção, ajuste os dois.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf

# Cor do app (--hmd-primary de static/css/app.css) — a MESMA do theme_color
# do manifest e do fundo do ícone.
THEME_COLOR_RGB = (0x0D / 255, 0x4A / 255, 0x8A / 255)
# Faixa de família SAR (ui-parity-foundation D6): a secundária do tema
# HMD, sem texto pequeno — apenas o marcador geométrico superior.
BAND_COLOR_RGB = (0x16 / 255, 0x64 / 255, 0x7A / 255)
TEXT_COLOR_RGB = (1.0, 1.0, 1.0)
# Faixa superior em frações do quadrado (espelha os SVGs: x=72/512,
# y=64/512, largura=368/512, altura=56/512).
BAND_X_RATIO = 72 / 512
BAND_Y_RATIO = 64 / 512
BAND_WIDTH_RATIO = 368 / 512
BAND_HEIGHT_RATIO = 56 / 512

TEXT = "HMD"
# Helvetica-Bold embutida do PyMuPDF (nome curto "hebo").
FONT_NAME = "hebo"
FONT_SIZE_RATIO = 0.26
CORNER_RADIUS_RATIO = 0.18
MASKABLE_CONTENT_RATIO = 0.8

ICON_SIZES = (72, 96, 128, 144, 152, 192, 384, 512)
MASKABLE_SIZES = (192, 512)

ICONS_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"


def expected_files() -> list[str]:
    """Nomes dos PNGs esperados por ``--check`` (espelho do manifest)."""
    icons = [f"icon-{size}.png" for size in ICON_SIZES]
    maskables = [f"maskable-{size}.png" for size in MASKABLE_SIZES]
    return icons + maskables


def render_icon_png(size: int, *, maskable: bool) -> bytes:
    """Rasteriza um ícone quadrado ``size``×``size`` (1pt = 1px) em PNG.

    ``maskable`` desenha o fundo cobrindo a página inteira (full-bleed) e
    reduz o conteúdo a ``MASKABLE_CONTENT_RATIO`` da largura (safe zone); o
    ícone base arredonda os cantos e mantém o resto transparente.
    """
    document = pymupdf.open()  # type: ignore[no-untyped-call]
    try:
        page = document.new_page(width=size, height=size)
        if maskable:
            page.draw_rect(
                pymupdf.Rect(0, 0, size, size),  # type: ignore[no-untyped-call]
                color=None,
                fill=THEME_COLOR_RGB,
                width=0,
            )
        else:
            page.draw_rect(
                pymupdf.Rect(0, 0, size, size),  # type: ignore[no-untyped-call]
                color=None,
                fill=THEME_COLOR_RGB,
                width=0,
                radius=CORNER_RADIUS_RATIO,
            )

        # Faixa de família: no maskable ela vive dentro do conteúdo
        # escalado a 80% (safe zone), como no SVG; no base, nas
        # coordenadas cheias do quadrado.
        content_ratio = MASKABLE_CONTENT_RATIO if maskable else 1.0

        def _content_coord(value: float) -> float:
            """Coord. do quadrado cheia → coord. do conteúdo (maskable: 80%)."""
            center = size / 2
            return center + (value - center) * content_ratio

        band_rect = pymupdf.Rect(  # type: ignore[no-untyped-call]
            _content_coord(size * BAND_X_RATIO),
            _content_coord(size * BAND_Y_RATIO),
            _content_coord(size * (BAND_X_RATIO + BAND_WIDTH_RATIO)),
            _content_coord(size * (BAND_Y_RATIO + BAND_HEIGHT_RATIO)),
        )
        # Base espelha o rx=8 do SVG; no maskable a faixa é retangular
        # (o SVG maskable não usa rx= em nenhum rect).
        if maskable:
            page.draw_rect(band_rect, color=None, fill=BAND_COLOR_RGB, width=0)
        else:
            page.draw_rect(
                band_rect,
                color=None,
                fill=BAND_COLOR_RGB,
                width=0,
                radius=0.02,
            )

        font = pymupdf.Font(FONT_NAME)  # type: ignore[no-untyped-call]
        font_size = size * FONT_SIZE_RATIO * content_ratio
        # Baseline que centraliza a altura das maiúsculas (bbox do "H")
        # abaixo da faixa superior (desvio de 0,06 do quadrado, como nos
        # SVGs: texto a 324/512 no base).
        cap_height = font.glyph_bbox(ord("H")).y1 * font_size  # type: ignore[no-untyped-call]
        baseline = size / 2 + cap_height / 2 + size * 0.06 * content_ratio
        width = font.text_length(TEXT, font_size)  # type: ignore[no-untyped-call]
        page.insert_text(
            pymupdf.Point(size / 2 - width / 2, baseline),  # type: ignore[no-untyped-call]
            TEXT,
            fontname=FONT_NAME,
            fontsize=font_size,
            color=TEXT_COLOR_RGB,
        )
        pixmap = page.get_pixmap(alpha=True)
        return bytes(pixmap.tobytes("png"))  # type: ignore[no-untyped-call]
    finally:
        document.close()  # type: ignore[no-untyped-call]


def missing_files() -> list[str]:
    """PNGs esperados que ainda não existem em ``static/icons/``."""
    return [name for name in expected_files() if not (ICONS_DIR / name).is_file()]


def generate() -> list[Path]:
    """Gera todos os PNGs esperados (idempotente) e devolve os caminhos escritos."""
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for size in ICON_SIZES:
        target = ICONS_DIR / f"icon-{size}.png"
        target.write_bytes(render_icon_png(size, maskable=False))
        written.append(target)
    for size in MASKABLE_SIZES:
        target = ICONS_DIR / f"maskable-{size}.png"
        target.write_bytes(render_icon_png(size, maskable=True))
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada: gera os PNGs ou apenas verifica a presença (``--check``)."""
    parser = argparse.ArgumentParser(description="Gera os ícones PWA do HMD.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="apenas verifica se todos os PNGs esperados existem (exit != 0 se faltar)",
    )
    args = parser.parse_args(argv)

    if args.check:
        missing = missing_files()
        if missing:
            print(
                "ícones PWA ausentes: " + ", ".join(missing) + " — rode o script sem --check",
                file=sys.stderr,
            )
            return 1
        print(f"ícones PWA presentes ({len(expected_files())} arquivos)")
        return 0

    written = generate()
    for path in written:
        print(f"gerado {path.relative_to(ICONS_DIR.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
