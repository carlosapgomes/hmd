"""Guardas do tema CSS (quick fix radio-border-contrast, espelho do ATS v0.8.2).

Radio buttons não selecionados herdam a borda clara do Bootstrap e quase
desaparecem sobre superfícies brancas (caso típico: seleção de tipo de
procedimento no upload do NIR). O tema dá a eles uma borda de repouso com
contraste não-texto >= 3:1 (WCAG 1.4.11), e os estados :checked/:focus
seguem os tokens hospitalares — mesmo desenho do fix validado no ATS Web
(``fix(ui): improve global radio button contrast``), adaptado aos nomes
``--hospital-*``.

O CSS é validado no disco (mesmo padrão do ``test_pwa.py``): sem runner de
browser, as guardas verificam os tokens e regras de forma estrutural e o
contraste é calculado matematicamente (relative luminance WCAG).
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

CSS_PATH = Path(settings.BASE_DIR) / "static" / "css" / "app.css"


def _css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def _rule_body(css: str, selector: str) -> str | None:
    match = re.search(re.escape(selector) + r"\s*\{(?P<body>[^}]*)\}", css)
    return match.group("body") if match else None


def _relative_luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def test_control_border_token_defined() -> None:
    """Token --hospital-control-border existe no :root com cor hex."""
    token = re.search(r"--hospital-control-border:\s*(#[0-9a-fA-F]{6})", _css())
    assert token is not None, "app.css deve definir --hospital-control-border (hex)"


def test_radio_resting_border_rule_exists() -> None:
    """Radios não selecionados ganham borda 2px com o token de controle."""
    body = _rule_body(_css(), '.hospital-shell .form-check-input[type="radio"]')
    assert body is not None, (
        "app.css deve ter regra global de repouso para radio inputs "
        '(seletor .hospital-shell .form-check-input[type="radio"])'
    )
    assert "border: 2px solid var(--hospital-control-border)" in body, (
        "radios devem usar borda 2px com --hospital-control-border"
    )


def test_radio_resting_border_has_non_text_contrast() -> None:
    """A borda de repouso atinge 3:1 de contraste não-texto sobre branco."""
    token = re.search(r"--hospital-control-border:\s*(#[0-9a-fA-F]{6})", _css())
    assert token is not None, "app.css deve definir --hospital-control-border"
    border_luminance = _relative_luminance(token.group(1))
    white_luminance = _relative_luminance("#ffffff")
    contrast = (white_luminance + 0.05) / (border_luminance + 0.05)
    assert contrast >= 3, f"contraste da borda de radio é só {contrast:.2f}:1"


def test_radio_checked_uses_hmd_primary() -> None:
    """:checked usa os tokens hospitalares (não o azul default do Bootstrap)."""
    body = _rule_body(_css(), ".hospital-shell .form-check-input:checked")
    assert body is not None, "app.css deve estilizar :checked dos checks/radios"
    assert "background-color: var(--hospital-primary)" in body
    assert "border-color: var(--hospital-primary)" in body


def test_radio_focus_uses_hmd_accent() -> None:
    """:focus acompanha o accent hospitalar (paridade com form-control)."""
    body = _rule_body(_css(), ".hospital-shell .form-check-input:focus")
    assert body is not None, "app.css deve estilizar :focus dos checks/radios"
    assert "border-color: var(--hospital-accent)" in body
    assert "box-shadow" in body
