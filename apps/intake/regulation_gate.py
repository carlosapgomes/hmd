"""Gate determinístico do relatório de regulação SESAB (slice 002, design D4).

Avalia o texto extraído contra o padrão do relatório — cabeçalho
"RELATÓRIO DE OCORRÊNCIAS", sinais institucionais BA e seções operacionais —
com thresholds por env (``INTAKE_REGULATION_MIN_TEXT_CHARS`` e
``INTAKE_REGULATION_MIN_OPERATIONAL_SECTIONS``). Função pura
(``evaluate_regulation_report``), sem banco/LLM; o resultado imutável
(``GateResult``) carrega os diagnósticos que alimentam o motivo exibido ao NIR
e os eventos do slice 003.

Divergência marcada vs ats-web: o gate do ats-web serve o domínio
EDA/colonoscopia e devolve ``matched_header`` com reason genérico
``invalid_regulation_report``; o HMD usa as listas canônicas do mesmo sistema
SESAB (o dono confirmou: os sinais são do relatório, não do domínio — a
adaptação é de fixtures/conteúdo, sem vestígios EDA/colonoscopia) e expõe um
``reason_code`` canônico por falha (R4), sem campo de header — a ausência dele
vira ``missing_header``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from django.conf import settings

# ── reason_codes canônicos (R4/D4) ─────────────────────────────────────────
# Aprovação (o NIR nunca vê — só o diagnóstico de retenção alimenta a tela).
REASON_OK = "ok"
REASON_BELOW_MIN_CHARS = "below_min_chars"
REASON_MISSING_HEADER = "missing_header"
REASON_MISSING_INSTITUTIONAL_SIGNALS = "missing_institutional_signals"
REASON_INSUFFICIENT_SECTIONS = "insufficient_sections"


@dataclass(frozen=True)
class GateResult:
    """Resultado imutável da avaliação do gate (R4).

    ``ok`` indica aprovação; ``reason_code`` é o canônico da falha (REASON_*,
    ``"ok"`` na aprovação); as listas diagnósticas trazem os rótulos canônicos
    casados (sinais institucionais e seções operacionais) e ``text_length`` é
    o tamanho do texto avaliado (``len(text.strip())``).
    """

    ok: bool
    reason_code: str
    matched_institutional_signals: list[str] = field(default_factory=list)
    matched_operational_sections: list[str] = field(default_factory=list)
    text_length: int = 0


# ── Normalização (helper espelhado do ats-web) ─────────────────────────────


def _normalize_text(text: str) -> str:
    """Normaliza para casamento: sem acentos, case-insensitive, whitespace colapsado.

    Passos: trim; colapsa toda sequência de whitespace (incluindo quebras de
    linha) em um espaço; decomposição Unicode sem marcas de combinação;
    lowercase.
    """
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return text.lower()


# ── Listas canônicas HMD (D4) ──────────────────────────────────────────────
# Pares (rótulo de exibição, normalizado); a ordem da tupla é a ordem do
# diagnóstico retornado.

_HEADER = "RELATÓRIO DE OCORRÊNCIAS"
_HEADER_NORMALIZED = _normalize_text(_HEADER)

_INSTITUTIONAL_SIGNALS: tuple[tuple[str, str], ...] = tuple(
    (label, _normalize_text(label))
    for label in (
        "Central Estadual de Regulação",
        "Secretaria da Saúde do Estado",
        "Governo do Estado da Bahia",
    )
)

_OPERATIONAL_SECTIONS: tuple[tuple[str, str], ...] = tuple(
    (label, _normalize_text(label))
    for label in (
        "Código:",
        "Abertura:",
        "Unid. Origem",
        "Unidade de Origem",
        "Motivo da Solicitação",
        "Complemento da Solicitação",
        "Resumo Clínico",
        "Dias em tela",
        "Data Adm. Unid.",
    )
)


def _gate_result(
    *,
    ok: bool,
    reason_code: str,
    matched_institutional_signals: list[str],
    matched_operational_sections: list[str],
    text_length: int,
) -> GateResult:
    """Monta um ``GateResult`` completo (evita repetição nos retornos)."""
    return GateResult(
        ok=ok,
        reason_code=reason_code,
        matched_institutional_signals=matched_institutional_signals,
        matched_operational_sections=matched_operational_sections,
        text_length=text_length,
    )


def evaluate_regulation_report(text: str) -> GateResult:
    """Avalia o texto contra o padrão SESAB do relatório (R4).

    Critérios (todos necessários): ``len ≥ INTAKE_REGULATION_MIN_TEXT_CHARS``
    (default 500); contém o cabeçalho "RELATÓRIO DE OCORRÊNCIAS"; ao menos um
    sinal institucional BA; nº de seções operacionais casadas
    ≥ ``INTAKE_REGULATION_MIN_OPERATIONAL_SECTIONS`` (default 3). Em falha, o
    ``reason_code`` canônico é o da primeira dimensão ausente nesta ordem:
    tamanho, header, sinais, seções. Em aprovação, ``ok=True`` com
    ``reason_code="ok"``. Os diagnósticos (listas casadas e ``text_length``)
    acompanham o resultado mesmo em falha — alimentam o motivo da retenção.
    """
    raw_text = text.strip()
    text_length = len(raw_text)
    normalized = _normalize_text(text)

    min_chars = getattr(settings, "INTAKE_REGULATION_MIN_TEXT_CHARS", 500)
    min_sections = getattr(settings, "INTAKE_REGULATION_MIN_OPERATIONAL_SECTIONS", 3)

    matched_header = _HEADER_NORMALIZED in normalized
    matched_institutional_signals = [
        label
        for label, normalized_label in _INSTITUTIONAL_SIGNALS
        if normalized_label in normalized
    ]
    matched_operational_sections = [
        label for label, normalized_label in _OPERATIONAL_SECTIONS if normalized_label in normalized
    ]

    if text_length < min_chars:
        return _gate_result(
            ok=False,
            reason_code=REASON_BELOW_MIN_CHARS,
            matched_institutional_signals=matched_institutional_signals,
            matched_operational_sections=matched_operational_sections,
            text_length=text_length,
        )
    if not matched_header:
        return _gate_result(
            ok=False,
            reason_code=REASON_MISSING_HEADER,
            matched_institutional_signals=matched_institutional_signals,
            matched_operational_sections=matched_operational_sections,
            text_length=text_length,
        )
    if not matched_institutional_signals:
        return _gate_result(
            ok=False,
            reason_code=REASON_MISSING_INSTITUTIONAL_SIGNALS,
            matched_institutional_signals=matched_institutional_signals,
            matched_operational_sections=matched_operational_sections,
            text_length=text_length,
        )
    if len(matched_operational_sections) < min_sections:
        return _gate_result(
            ok=False,
            reason_code=REASON_INSUFFICIENT_SECTIONS,
            matched_institutional_signals=matched_institutional_signals,
            matched_operational_sections=matched_operational_sections,
            text_length=text_length,
        )
    return _gate_result(
        ok=True,
        reason_code=REASON_OK,
        matched_institutional_signals=matched_institutional_signals,
        matched_operational_sections=matched_operational_sections,
        text_length=text_length,
    )
