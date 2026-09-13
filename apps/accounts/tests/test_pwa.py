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

    assert manifest["name"] == "Regulação - HMD"
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


@pytest.mark.parametrize("svg_name", ["hmd-base.svg", "hmd-maskable.svg"])
def test_icons_svg_declare_hmd(svg_name: str) -> None:
    """R2: SVGs hand-written com as letras HMD e sem CHD."""
    content = _find_static_or_fail(f"icons/{svg_name}").read_text(encoding="utf-8")

    assert "HMD" in content
    assert "CHD" not in content
    assert THEME_COLOR.lower() in content.lower()


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

    assert 'CACHE_NAME = "hmd-cache-v1"' in content
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
