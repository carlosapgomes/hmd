"""Gera os PNGs do ícone PWA do HMD (change dashboard-notifications-pwa, D4/R1).

Uso:
    uv run python scripts/generate_pwa_icons.py          # gera/atualiza os PNGs
    uv run python scripts/generate_pwa_icons.py --check  # falha se algum PNG faltar

Os PNGs são **commitados** (reprodutíveis por este script); o ``--check`` é a
porta de CI: sai com 0 só quando todos os PNGs esperados por ``static/
manifest.json`` estão presentes no diretório ``static/icons/``.

Desenho (mesma geometria dos SVGs hand-written ``hmd-base.svg``/
``hmd-maskable.svg``): quadrado plano na cor do app (``--hmd-primary`` de
``static/css/app.css``, base do gradiente do ``.app-header``) com as letras
"HMD" brancas centralizadas (170/512, tracking 10/512, baseline em
308.44/512 — molde do ícone do ats-web; nos SVGs a pilha é Merriweather
Sans/Arial, nos PNGs a Helvetica-Bold embutida) — a sigla é o único
elemento do ícone (change pwa-icon-simplify, D1). O base tem cantos
arredondados e
transparência fora do retângulo; o maskable é full-bleed com o conteúdo em
~80% central (safe zone recortada por launchers adaptativos).

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
TEXT_COLOR_RGB = (1.0, 1.0, 1.0)

TEXT = "HMD"
# Helvetica-Bold embutida do PyMuPDF (nome curto "hebo").
FONT_NAME = "hebo"
# Geometria espelhada do ícone de referência (ats-web, change
# pwa-icon-simplify): texto em 170/512 do lado, tracking de 10/512 e
# baseline em 308.44/512 (bloco óptico ~10px acima do centro — o
# correto óptico; centralização geométrica exata pareceria baixa).
FONT_SIZE_RATIO = 170 / 512
LETTER_SPACING_RATIO = 10 / 512
BASELINE_RATIO = 308.4353 / 512
# Compensação do trailing space do letter-spacing no anchor middle
# (molde ats-web: x=261.57 para tracking 10 → +5.57 ≈ spacing/2).
TEXT_CX_OFFSET_RATIO = 0.5
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

        # Conteúdo: no maskable a marca é escalada a 80% (safe zone) em
        # torno do centro; no base usam-se as coordenadas cheias — mesma
        # transformação do grupo scale(0.8) do SVG maskable.
        content_ratio = MASKABLE_CONTENT_RATIO if maskable else 1.0

        font = pymupdf.Font(FONT_NAME)  # type: ignore[no-untyped-call]
        font_size = size * FONT_SIZE_RATIO * content_ratio
        spacing = size * LETTER_SPACING_RATIO * content_ratio
        # Largura total contando o trailing space do tracking (igual ao
        # advance do text-anchor="middle" no SVG); o centro óptico do
        # bloco fica em text_cx (compensa o trailing space).
        advances = [
            font.text_length(char, font_size)  # type: ignore[no-untyped-call]
            for char in TEXT
        ]
        total = sum(advances) + spacing * len(TEXT)
        text_cx = size / 2 + spacing * TEXT_CX_OFFSET_RATIO
        cursor = text_cx - total / 2
        baseline = size / 2 + (size * BASELINE_RATIO - size / 2) * content_ratio
        for char, advance in zip(TEXT, advances):
            page.insert_text(
                pymupdf.Point(cursor, baseline),  # type: ignore[no-untyped-call]
                char,
                fontname=FONT_NAME,
                fontsize=font_size,
                color=TEXT_COLOR_RGB,
            )
            cursor += advance + spacing
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
