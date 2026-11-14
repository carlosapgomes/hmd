"""Smoke tests da instalação PWA (change dashboard-notifications-pwa, slice 004).

Cobre R1–R6 com assets resolvidos por ``django.contrib.staticfiles.finders``:
a suíte roda com ``DEBUG=False`` + WhiteNoise ``use_finders=False`` e
``STATIC_ROOT`` nunca coletado, então ``GET /static/...`` responde 404 — os
arquivos são validados no disco via finder, não por HTTP (emenda P1 review).

O teste vive em ``apps/accounts/tests`` porque o wiring do PWA é do shell
transversal (``templates/base.html``), app autocontido do papel/notificações;
``apps/dashboard/tests`` já cobre só o painel (slice 003).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pymupdf
import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User

# Cor do app (``--hmd-primary`` de static/css/app.css, base do gradiente do
# ``.app-header``) — a MESMA no theme_color do manifest e no fundo do ícone.
THEME_COLOR = "#0d4a8a"
# Secundária do tema: era a cor da faixa de família SAR removida pelo change
# pwa-icon-simplify (D1 — nenhum marcador substitui a faixa).
BAND_COLOR = "#16647a"

REPO_ROOT = Path(__file__).resolve().parents[3]
ICON_SCRIPT = REPO_ROOT / "scripts" / "generate_pwa_icons.py"


def _static_path(static_reference: str) -> str:
    """Normaliza uma referência do manifest para a chave aceita pelo finder."""
    return static_reference.removeprefix("/static/").removeprefix("/")


def _find_static_or_fail(static_reference: str) -> Path:
    """Resolve um asset em disco pelo finder (falha explícita se ausente)."""
    found = finders.find(_static_path(static_reference))
    assert found is not None, f"asset estático ausente: {static_reference}"
    return Path(found)


def _manifest() -> dict[str, Any]:
    """Carrega ``static/manifest.json`` (via finder) já parseado."""
    with _find_static_or_fail("manifest.json").open(encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


# ── R3: manifest ───────────────────────────────────────────────────────────


def test_manifest_declares_hmd() -> None:
    """R3: manifest presente com nome/short_name/display/tema do HMD."""
    manifest = _manifest()

    # Slice 001 (ui-parity-foundation): nome SAR – HMD; short_name/tema
    # reservados (D1) intactos.
    assert manifest["name"] == "SAR – HMD"
    assert manifest["short_name"] == "HMD"
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["theme_color"] == THEME_COLOR
    assert manifest["background_color"] == "#ffffff"
    assert "hemodinâmica" in manifest["description"].lower()


def test_manifest_has_no_chd_reference() -> None:
    """R3/R6: nenhuma referência ao CHD (identidade legada do ats-web)."""
    content = _find_static_or_fail("manifest.json").read_text(encoding="utf-8")

    assert "CHD" not in content


def test_manifest_icons_exist() -> None:
    """R3/R6: TODO ``icons[].src`` resolve pelo finder (SVG any/maskable + PNGs)."""
    icons: list[dict[str, Any]] = _manifest()["icons"]

    assert icons, "manifest sem ícones declarados"
    for icon in icons:
        assert _find_static_or_fail(icon["src"]).is_file(), icon["src"]

    purposes = {icon["purpose"] for icon in icons}
    assert purposes == {"any", "maskable"}

    maskable_sizes = {icon["sizes"] for icon in icons if icon["purpose"] == "maskable"}
    assert {"192x192", "512x512"} <= maskable_sizes

    png_sizes = {icon["sizes"] for icon in icons if icon["type"] == "image/png"}
    assert {
        "72x72",
        "96x96",
        "128x128",
        "144x144",
        "152x152",
        "192x192",
        "384x384",
        "512x512",
    } <= png_sizes


# ── R2: ícones SVG ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("svg_name", ["hmd-base.svg", "hmd-maskable.svg", "hmd-contrast.svg"])
def test_icons_svg_declare_hmd(svg_name: str) -> None:
    """R2: SVGs hand-written com as letras HMD e sem CHD."""
    content = _find_static_or_fail(f"icons/{svg_name}").read_text(encoding="utf-8")

    assert "HMD" in content
    assert "CHD" not in content
    assert THEME_COLOR.lower() in content.lower()


@pytest.mark.parametrize("svg_name", ["hmd-base.svg", "hmd-maskable.svg", "hmd-contrast.svg"])
def test_icons_svg_have_no_family_band(svg_name: str) -> None:
    """R1 (pwa-icon-simplify): ícones planos, sem faixa/marcador de família.

    D1: fundo plano + sigla centralizada, como o molde do CHD — nenhum ``rect``
    de faixa, nem a cor secundária do tema nem a geometria (x=72/y=64, 368×56
    no base/contraste; -184/-192 no grupo escalado do maskable).
    """
    content = _find_static_or_fail(f"icons/{svg_name}").read_text(encoding="utf-8")

    assert BAND_COLOR not in content.lower()
    assert 'height="56"' not in content
    assert 'width="368"' not in content


@pytest.mark.parametrize("svg_name", ["hmd-base.svg", "hmd-contrast.svg"])
def test_icon_text_matches_reference_geometry(svg_name: str) -> None:
    """R1 (pwa-icon-simplify, fix de revisão): texto com a geometria do molde.

    O dono apontou texto pequeno e descentralizado (133px, baseline 324):
    a geometria passa a espelhar o ícone do ats-web (170px, tracking 10,
    baseline 308.44, x compensado 261.57) — mesma família visual, na
    primária HMD.
    """
    content = _find_static_or_fail(f"icons/{svg_name}").read_text(encoding="utf-8")

    assert 'font-size="170"' in content
    assert 'letter-spacing="10"' in content
    assert 'x="261.57"' in content
    assert 'y="308.44"' in content


def test_maskable_text_matches_reference_geometry_scaled() -> None:
    """R1: maskable aplica a mesma geometria sob o grupo scale(0.8).

    Renderizado: 170px, tracking 10, baseline 308.44 e centro 261.57 —
    idênticos ao base; os valores locais são divididos pelo conteúdo
    (212.5/12.5, x=6.96/y=65.54).
    """
    content = _find_static_or_fail("icons/hmd-maskable.svg").read_text(encoding="utf-8")

    assert 'font-size="212.5"' in content
    assert 'letter-spacing="12.5"' in content
    assert 'x="6.96"' in content
    assert 'y="65.54"' in content


def test_maskable_svg_has_safe_zone() -> None:
    """R2: o SVG maskable tem fundo full-bleed e conteúdo em ~80% central.

    O ``viewBox`` é 512×512; a marca é desenhada num grupo escalado a 80%
    (``scale(0.8)``) sobre o fundo que cobre o quadrado inteiro — a safe zone
    que um launcher circular/adaptativo recorta.
    """
    content = _find_static_or_fail("icons/hmd-maskable.svg").read_text(encoding="utf-8")

    # Full-bleed real (P2 review): o rect raiz cobre o quadrado inteiro SEM
    # cantos arredondados — o assert de width/height sozinho era trivial.
    assert '<rect width="512" height="512"' in content
    assert "rx=" not in content
    assert "scale(0.8)" in content


# ── R4: service worker ─────────────────────────────────────────────────────


def test_service_worker_has_no_ats_reference() -> None:
    """R4/R6: SW adaptado ao HMD — cache versionado, include textual do HMD."""
    content = _find_static_or_fail("js/sw.js").read_text(encoding="utf-8")

    # Slice 001: cache invalidado pelo rename (v2).
    assert 'CACHE_NAME = "hmd-cache-v2"' in content
    assert 'includes("/pdf/")' in content
    # P1 review: a rota de PDF do NIR (intake: serve_document, aberta em nova
    # aba) NÃO contém "/pdf/" — sem o bypass de "/documents/" o SW
    # interceptaria a navegação e o viewer nativo ficaria em branco.
    assert 'includes("/documents/")' in content
    assert 'endsWith("/pdf/")' not in content
    assert "CHD" not in content
    assert "ats-cache" not in content
    assert "/static/manifest.json" in content
    assert "/static/css/app.css" in content
    assert "/static/js/sw.js" in content
    # Slice 001: precache inclui os SVGs novos (base/maskable/contrast).
    assert "/static/icons/hmd-base.svg" in content
    assert "/static/icons/hmd-maskable.svg" in content
    assert "/static/icons/hmd-contrast.svg" in content


def test_service_worker_precache_matches_manifest_icons() -> None:
    """R5 (slice 001): todo ícone do manifest está no precache do SW."""
    sw_content = _find_static_or_fail("js/sw.js").read_text(encoding="utf-8")
    manifest_srcs = [icon["src"] for icon in _manifest()["icons"]]
    assert manifest_srcs, "manifest sem ícones declarados"
    for src in manifest_srcs:
        assert src in sw_content, src


def test_contrast_svg_is_dark_surface_variant() -> None:
    """R3/D6: variante -contrast com fundo claro e sigla na primária."""
    content = _find_static_or_fail("icons/hmd-contrast.svg").read_text(encoding="utf-8")

    assert "SAR – HMD" in content
    assert "HMD" in content
    assert THEME_COLOR.lower() in content.lower()
    assert 'fill="#ffffff"' in content.lower()
    assert "CHD" not in content


def test_icon_render_is_deterministic() -> None:
    """R3: rasterizar duas vezes produz bytes idênticos (PNGs reproduzíveis)."""
    module = _load_icon_script()
    assert module.render_icon_png(192, maskable=False) == module.render_icon_png(
        192, maskable=False
    )
    assert module.render_icon_png(192, maskable=True) == module.render_icon_png(192, maskable=True)


# ── R5: wiring do base.html ────────────────────────────────────────────────


@pytest.mark.django_db
def test_base_declares_pwa(client: Client) -> None:
    """R5: página autenticada renderiza manifest, theme-color e registro do SW."""
    role, _ = Role.objects.get_or_create(name="nir")
    user = User.objects.create_user(username="nir-pwa", password="senha-teste")
    user.roles.add(role)
    client.force_login(user)

    # follow=True: a home despacha o papel ativo à sua fila (change
    # painel-gerencial-e-home, slice 002); a página final também renderiza
    # base.html.
    content = client.get(reverse("home"), follow=True).content.decode()

    assert 'rel="manifest"' in content
    assert 'href="/static/manifest.json"' in content
    assert 'name="theme-color"' in content
    assert THEME_COLOR in content
    assert "serviceWorker" in content
    assert "navigator.serviceWorker.register('/static/js/sw.js')" in content


# ── R1: script gerador de ícones ───────────────────────────────────────────


def test_icon_script_check_passes() -> None:
    """R1: ``--check`` sai com 0 quando todos os PNGs esperados existem."""
    assert ICON_SCRIPT.is_file(), f"script ausente: {ICON_SCRIPT}"

    result = subprocess.run(
        [sys.executable, str(ICON_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def _load_icon_script() -> ModuleType:
    """Carrega ``scripts/generate_pwa_icons.py`` (fora de pacote) como módulo."""
    spec = importlib.util.spec_from_file_location("generate_pwa_icons", ICON_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_icon_script_check_fails_on_missing_png(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R1: ``--check`` sai != 0 quando algum PNG do manifest está ausente."""
    module = _load_icon_script()
    monkeypatch.setattr(module, "ICONS_DIR", tmp_path)

    assert module.main(["--check"]) == 1


def test_icon_script_expected_files_match_manifest() -> None:
    """R1/R3: a lista de PNGs do script e a do manifest não podem divergir."""
    module = _load_icon_script()
    manifest_pngs = {
        _static_path(icon["src"]).removeprefix("icons/")
        for icon in _manifest()["icons"]
        if icon["type"] == "image/png"
    }

    assert manifest_pngs == set(module.expected_files())


def test_manifest_icon_pngs_have_real_dimensions() -> None:
    """R3/R6 (P2 review): cada PNG do manifest tem as dimensões declaradas em
    ``sizes`` — presença de arquivo não basta (asset torto/trocado passaria)."""
    manifest = json.loads(_find_static_or_fail("manifest.json").read_text(encoding="utf-8"))
    png_icons = [icon for icon in manifest["icons"] if icon["src"].endswith(".png")]
    assert len(png_icons) == 10

    for icon in png_icons:
        path = _find_static_or_fail(icon["src"].removeprefix("/static/"))
        pix = pymupdf.Pixmap(str(path))  # type: ignore[no-untyped-call]
        width, height = (int(value) for value in icon["sizes"].split("x"))
        assert (pix.width, pix.height) == (width, height), icon["src"]
