"""Builders compartilhados de seções da estrutura extraída (slice 002, D5).

Funções PURAS sobre a estrutura já re-identificada: tornam cada seção do
artefato LLM1 (schema base — nunca o JSON bruto) em linhas exibíveis. Sem
dependência de ``apps.anonymization`` (a re-identificação é do presenter, na
renderização) e sem I/O.

API pública única: ``build_structure_sections(structured)`` — consumida pelo
presenter médico (``apps.doctor.presenters``) e pelo presenter do painel
(``apps.dashboard.presenters``), garantindo paridade entre o que o médico viu
e o que o auditor vê sem reimplementar o loop de montagem.
"""

from __future__ import annotations

from typing import Any

from apps.cases.procedure_catalog import PROCEDURE_PROFILES

# Perfil do catálogo por tipo (label dos cards e seções).
_PROFILE_BY_TYPE = {profile.procedure_type: profile for profile in PROCEDURE_PROFILES}

# Ordem canônica das seções do artefato LLM1 (schema base) + títulos.
_STRUCTURE_SECTION_KEYS: tuple[str, ...] = (
    "pedido",
    "contexto_clinico",
    "linha_do_tempo",
    "exames",
    "medicacoes",
    "comorbidades",
    "contraindicacoes",
    "trechos_nao_classificados",
)
_STRUCTURE_SECTION_TITLES: dict[str, str] = {
    "pedido": "Pedido",
    "contexto_clinico": "Contexto clínico",
    "linha_do_tempo": "Linha do tempo",
    "exames": "Exames",
    "medicacoes": "Medicações",
    "comorbidades": "Comorbidades",
    "contraindicacoes": "Contraindicações",
    "trechos_nao_classificados": "Trechos não classificados",
}

# Rótulos de exame da seção "exames" (ordem canônica da apresentação).
_EXAM_LABELS: dict[str, str] = {
    "platelets": "Plaquetas",
    "inr": "INR",
    "hemoglobin": "Hemoglobina",
    "creatinine": "Creatinina",
    "glucose": "Glicemia",
    "potassium": "Potássio",
    "systolic_blood_pressure": "Pressão arterial sistólica",
}


def _exam_label(key: str) -> str:
    """Rótulo legível de um exame da seção exames."""
    return _EXAM_LABELS.get(key, key)


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


# ── Seções da estrutura (schema base) ──────────────────────────────────────


def _pedido_lines(pedido: object) -> list[str]:
    """Linhas do pedido: procedimentos solicitados + evidência por trecho."""
    if not isinstance(pedido, dict):
        return []
    lines: list[str] = []
    for raw_type in _as_list(pedido.get("procedimentos_solicitados")):
        procedure_type = _as_str(raw_type)
        if not procedure_type:
            continue
        profile = _PROFILE_BY_TYPE.get(procedure_type)
        lines.append(profile.label if profile is not None else procedure_type)
    for span in _as_list(pedido.get("evidence_spans")):
        if isinstance(span, dict):
            excerpt = _as_str(span.get("excerpt"))
            if excerpt:
                lines.append(f"(evidência: {excerpt})")
    return lines


def _contexto_clinico_lines(value: object) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _linha_do_tempo_lines(entries: object) -> list[str]:
    lines: list[str] = []
    for entry in _as_list(entries):
        if isinstance(entry, dict):
            description = _as_str(entry.get("description"))
            if description:
                lines.append(description)
    return lines


def _exames_lines(exames: object) -> list[str]:
    if not isinstance(exames, dict):
        return []
    lines: list[str] = []
    for key, label in _EXAM_LABELS.items():
        observation = exames.get(key)
        if not isinstance(observation, dict):
            continue
        if observation.get("status") == "nao_informado":
            continue
        value = observation.get("value")
        if value is None:
            continue
        unit = _as_str(observation.get("unit"))
        status = _as_str(observation.get("status"))
        text = f"{label}: {value}"
        if unit:
            text += f" {unit}"
        if status and status != "confirmado":
            text += f" ({status})"
        lines.append(text)
    return lines


def _medicacoes_lines(medications: object) -> list[str]:
    lines: list[str] = []
    for medication in _as_list(medications):
        if not isinstance(medication, dict):
            continue
        name = _as_str(medication.get("name"))
        if not name:
            continue
        text = name
        drug_class = _as_str(medication.get("drug_class"))
        if drug_class:
            text += f" ({drug_class})"
        status = _as_str(medication.get("status"))
        if status and status != "confirmado":
            text += f" — {status}"
        lines.append(text)
    return lines


def _comorbidades_lines(comorbidities: object) -> list[str]:
    lines: list[str] = []
    for comorbidity in _as_list(comorbidities):
        if isinstance(comorbidity, dict):
            name = _as_str(comorbidity.get("name"))
            if name:
                lines.append(name)
    return lines


def _contraindicacoes_lines(contraindications: object) -> list[str]:
    lines: list[str] = []
    for contraindication in _as_list(contraindications):
        if isinstance(contraindication, dict):
            description = _as_str(contraindication.get("description"))
            if description:
                lines.append(description)
    return lines


def _trechos_nao_classificados_lines(trechos: object) -> list[str]:
    return [str(item) for item in _as_list(trechos) if isinstance(item, str) and item.strip()]


_SECTION_BUILDERS: dict[str, Any] = {
    "pedido": _pedido_lines,
    "contexto_clinico": _contexto_clinico_lines,
    "linha_do_tempo": _linha_do_tempo_lines,
    "exames": _exames_lines,
    "medicacoes": _medicacoes_lines,
    "comorbidades": _comorbidades_lines,
    "contraindicacoes": _contraindicacoes_lines,
    "trechos_nao_classificados": _trechos_nao_classificados_lines,
}


# ── API pública ────────────────────────────────────────────────────────────


def build_structure_sections(structured: object) -> list[dict[str, object]]:
    """Seções da estrutura re-identificada (pedido/contexto/linha/exames/…).

    Cada seção vira linhas exibíveis (nunca o JSON bruto); caso sem artefato
    (ou chave ausente) → linhas vazias, sem explosão.
    """
    sections: list[dict[str, object]] = []
    for key in _STRUCTURE_SECTION_KEYS:
        artifact = structured if isinstance(structured, dict) else {}
        raw = artifact.get(key) if isinstance(artifact, dict) else None
        builder = _SECTION_BUILDERS[key]
        lines = builder(raw)
        sections.append({"key": key, "title": _STRUCTURE_SECTION_TITLES[key], "lines": lines})
    return sections
