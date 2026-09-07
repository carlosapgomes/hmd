"""Policy pré-operatória consultiva determinística (slice 005, R1–R3, design D6).

``evaluate_preop_policy(structured_data, procedure_type) -> PolicyResult`` é
**pura** (sem LLM/DB; mesma entrada → mesma saída): avalia os critérios da
seção do tipo (S1–S8) e os requisitos gerais (§2 do plano / ``parametrosHMD``).
O resultado por procedimento é persistido por ``evaluate_case_policies``
(``Case.policy_result`` + evento ``CASE_POLICY_EVALUATED``). A policy **nunca
bloqueia**: recomenda + explica; o médico decide (change 07).

**Tabela de critérios explícita (correção do review, D6).** Cada critério
numérico de seção é uma row versionada em ``_SECTION_NUMERIC_CRITERIA`` que
mapeia ``caminho no artefato (exames.<campo>) → operador → atributo de
threshold do catálogo (CRITERIA_SECTIONS) → severidade``; a aplicabilidade por
seção deriva dos thresholds preenchidos no catálogo (fonte única dos valores).
**Severidade determinística**: critério de SEÇÃO fora do threshold → motivo de
recusa (``recomenda_recusar`` se ≥1); **requisitos gerais** → sempre alertas
informativos com protocolo (nunca recusam sozinhos); ``nao_informado`` nunca
recusa (sinaliza ausência — nunca completar informação ausente).

**Potássio (K) — decisão do parent 2026-09-07, pendente de validação do dono.**
O documento clínico lista "Eletrólitos: K normal" sem faixa numérica e o
catálogo guarda apenas ``potassium_required`` (bool) — **nenhum threshold
numérico é codificado aqui** (qualquer faixa futura exige decisão do dono +
change). K com valor extraído (confirmado/incerto) → alerta **informativo**
citando o valor e o texto do critério; K ausente/``nao_informado`` →
``nao_informado``. K nunca entra em motivos de recusa.

**Anticoagulantes — protocolo por fármaco reproduzido do documento.**
Marevan/Marcoumar/Cumadin/varfarina/Pradaxa → 7 dias; Xarelto → 3 dias;
Eliquis/Lixiana → 48h; enoxaparina/heparina SC → 12h (profilática) ou 24h
(terapêutica), por matching case-insensitive (fold: sem acentos) no texto das
medicações; classe marcada sem nome conhecido → alerta com o protocolo
genérico da fonte (sem inventar prazo). Antiagregantes → informativo ("em
geral não suspender"). Metformina → 48h. Demais requisitos gerais
(alergia/peso/jejum/isolamento/Cr/anestésico) são avaliados sobre o artefato;
``jejum`` e ``anestesico`` são alertas de checklist (estáticos por avaliação /
flag do catálogo).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from django.db import transaction

from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent
from apps.cases.procedure_catalog import CRITERIA_SECTIONS, CriteriaSection, get_procedure_profile
from apps.cases.procedures import get_declared_procedure_types

if TYPE_CHECKING:
    from apps.accounts.models import User

CriterionStatus = Literal["ok", "alerta", "nao_informado"]
CriterionSeverity = Literal["recusa", "informativo"]
Recommendation = Literal["recomenda_aceitar", "recomenda_recusar"]

# Requisitos gerais de checklist estáticos (protocolo da fonte — §2 do plano).
_FASTING_REASON = "jejum mínimo de 8 horas antes do procedimento (checklist de agendamento)"
_ALLERGY_REASON = (
    "alergia a contraste/frutos do mar/iodo — realizar protocolo de dessensibilização "
    "(prednisona 20 mg + fenergan 25 mg conforme o turno do exame)"
)
_METFORMIN_REASON = (
    "metformina em uso — suspender 48 horas antes do procedimento "
    "(evitar acidose láctica, especialmente em disfunção renal)"
)

# Termos de alergênios do requisito geral (parametrosHMD §3).
_ALERGEN_TERMS = ("contraste", "fruto", "frutos", "iodo")
_WEIGHT_LIMIT_KG = 180.0
_CREATININE_RISK = 1.5

# Anticoagulantes por fármaco (parametrosHMD §1) — termos no texto da medicação.
_SEVEN_DAY_TOKENS = ("marevan", "marcoumar", "cumadin", "warfarin", "varfarina", "pradaxa")
_THREE_DAY_TOKENS = ("xarelto",)
_FORTY_EIGHT_HOUR_TOKENS = ("eliquis", "lixiana")
_HEPARIN_TOKENS = ("enoxaparina", "heparina")
_HEPARIN_PROPHYLACTIC_TERMS = ("profilat", "profilax")
_HEPARIN_THERAPEUTIC_TERMS = ("terapeutic", "therapeutic")

# Guardas de peso/isolamento sobre os textos narrativos do artefato.
_WEIGHT_PATTERNS = (
    re.compile(
        r"\bpeso\b[^\n]{0,60}?(\d{1,4}(?:[.,]\d+)?)\s*(?:kg|quilos?|quilogramas?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(\d{1,4}(?:[.,]\d+)?)\s*(?:kg|quilos?|quilogramas?)\b", re.IGNORECASE),
)
_ISOLATION_PATTERN = re.compile(r"\bisolament\w*", re.IGNORECASE)


@dataclass(frozen=True)
class PolicyCriterion:
    """Resultado de um critério (de seção ou requisito geral).

    ``severity`` descreve a classe do critério (dado versionado da tabela):
    ``recusa`` para critérios numéricos de seção e ``informativo`` para
    requisitos gerais e o potássio. ``status`` descreve o desfecho da
    avaliação: ``ok``, ``alerta`` (com ``reason``) ou ``nao_informado``.
    """

    criterion: str
    status: CriterionStatus
    severity: CriterionSeverity
    reason: str = ""


@dataclass(frozen=True)
class PolicyResult:
    """Resultado global consultivo de um procedimento (nunca bloqueia)."""

    procedure_type: str
    section_id: str
    recommendation: Recommendation
    refusal_reasons: tuple[str, ...]
    criteria: tuple[PolicyCriterion, ...]


@dataclass(frozen=True)
class _SectionNumericCriterion:
    """Row da tabela de critérios de seção (dados versionados, D6).

    ``exam_field`` é a chave determinística do bloco ``exames`` do artefato
    (``path do schema``); ``threshold_attrs`` aponta o(s) atributo(s) de
    threshold do ``CriteriaSection`` do catálogo; a severidade de todo
    critério numérico de seção é ``recusa``.
    """

    criterion_id: str
    exam_field: str
    comparison: Literal["ge", "lt", "range"]
    threshold_attrs: tuple[str, ...]


# Tabela de critérios numéricos de seção: path do schema → operador →
# threshold do catálogo → severidade (recusa). O potássio NÃO está aqui —
# é critério textual-informativo (decisão do parent, ver docstring).
_SECTION_NUMERIC_CRITERIA: tuple[_SectionNumericCriterion, ...] = (
    _SectionNumericCriterion("platelets", "platelets", "ge", ("platelets_min",)),
    _SectionNumericCriterion("inr", "inr", "lt", ("inr_max",)),
    _SectionNumericCriterion("hemoglobin", "hemoglobin", "ge", ("hemoglobin_min",)),
    _SectionNumericCriterion("creatinine", "creatinine", "lt", ("creatinine_max",)),
    _SectionNumericCriterion(
        "systolic_bp",
        "systolic_blood_pressure",
        "range",
        ("systolic_bp_min", "systolic_bp_max"),
    ),
    _SectionNumericCriterion("glucose", "glucose", "lt", ("glucose_max",)),
)


@dataclass(frozen=True)
class _LabObservation:
    """Exame informado no artefato (confirmado/incerto com valor numérico)."""

    status: Literal["confirmado", "incerto"]
    value: float
    unit: str | None


# ── Leitura determinística do artefato ─────────────────────────────────────


def _read_lab(artifact: dict[str, Any], field: str) -> _LabObservation | None:
    """Exame do bloco ``exames`` com valor numérico e status informado.

    ``None`` quando ausente ou com status ``nao_informado`` (nunca completar
    ausência com valor inventado).
    """
    exames = artifact.get("exames")
    if not isinstance(exames, dict):
        return None
    entry = exames.get(field)
    if not isinstance(entry, dict):
        return None
    status = entry.get("status")
    value = entry.get("value")
    if status not in ("confirmado", "incerto") or not isinstance(value, (int, float)):
        return None
    unit = entry.get("unit")
    return _LabObservation(
        status=status,
        value=float(value),
        unit=str(unit) if isinstance(unit, str) and unit else None,
    )


def _narrative_texts(artifact: dict[str, Any]) -> tuple[str, ...]:
    """Folhas textuais narrativas do artefato (textos clínicos, não códigos).

    Mesma varredura do language guard do LLM1: strings com espaço — contexto
    clínico, descrições, nomes de comorbidades, trechos não classificados.
    Valores numéricos de exame (não-str) ficam fora.
    """
    texts: list[str] = []

    def _collect(value: object) -> None:
        if isinstance(value, dict):
            for child in value.values():
                _collect(child)
        elif isinstance(value, list):
            for child in value:
                _collect(child)
        elif isinstance(value, str) and any(character.isspace() for character in value):
            texts.append(value)

    _collect(artifact)
    return tuple(texts)


def _fold(value: str) -> str:
    """Normaliza para casamento determinístico: sem acentos, minúsculas."""
    decomposed = unicodedata.normalize("NFKD", value)
    return decomposed.encode("ascii", "ignore").decode("ascii").lower()


def _format_value(value: float) -> str:
    """Valor para o texto do motivo: inteiros sem '.0' (legibilidade estável)."""
    if value.is_integer():
        return str(int(value))
    return str(value)


# ── Critérios de seção (S1–S8) ─────────────────────────────────────────────


def _applies_to_section(spec: _SectionNumericCriterion, section: CriteriaSection) -> bool:
    """O critério vale para a seção quando o catálogo tem ao menos um threshold."""
    return any(getattr(section, attr) is not None for attr in spec.threshold_attrs)


def _ge_reason(criterion_id: str, section_id: str, value: float, minimum: float) -> str:
    if criterion_id == "platelets":
        return (
            f"plaquetas {_format_value(value)} abaixo do mínimo "
            f"{_format_value(minimum)} da seção {section_id}"
        )
    if criterion_id == "hemoglobin":
        return (
            f"hemoglobina {_format_value(value)} abaixo do mínimo "
            f"{_format_value(minimum)} da seção {section_id}"
        )
    raise AssertionError(f"critério 'at least' desconhecido: {criterion_id}")


def _lt_reason(criterion_id: str, section_id: str, value: float, maximum: float) -> str:
    suffix = f"(< {_format_value(maximum)}) da seção {section_id}"
    labels = {"inr": "INR", "creatinine": "creatinina", "glucose": "glicemia"}
    try:
        label = labels[criterion_id]
    except KeyError:
        raise AssertionError(f"critério 'less than' desconhecido: {criterion_id}") from None
    return f"{label} {_format_value(value)} igual ou acima do limite {suffix}"


def _range_reason(
    section_id: str,
    value: float,
    *,
    minimum: float | None,
    maximum: float | None,
) -> str:
    if minimum is not None and value < minimum:
        return f"PAS {_format_value(value)} abaixo do mínimo {_format_value(minimum)} da seção {section_id}"
    if maximum is not None and value > maximum:
        return f"PAS {_format_value(value)} acima do máximo {_format_value(maximum)} da seção {section_id}"
    raise AssertionError("PAS fora do threshold sem justificativa de motivo")


def _compare_section_value(
    spec: _SectionNumericCriterion,
    section: CriteriaSection,
    value: float,
) -> PolicyCriterion:
    """Compara o valor informado contra o threshold do catálogo (R2)."""
    if spec.comparison == "ge":
        bound = getattr(section, spec.threshold_attrs[0])
        assert isinstance(bound, (int, float))
        minimum = float(bound)
        if value >= minimum:
            return PolicyCriterion(spec.criterion_id, "ok", "recusa")
        return PolicyCriterion(
            spec.criterion_id,
            "alerta",
            "recusa",
            _ge_reason(spec.criterion_id, section.section_id, value, minimum),
        )

    if spec.comparison == "lt":
        bound = getattr(section, spec.threshold_attrs[0])
        assert isinstance(bound, (int, float))
        maximum = float(bound)
        if value < maximum:
            return PolicyCriterion(spec.criterion_id, "ok", "recusa")
        return PolicyCriterion(
            spec.criterion_id,
            "alerta",
            "recusa",
            _lt_reason(spec.criterion_id, section.section_id, value, maximum),
        )

    minimum_attr, maximum_attr = spec.threshold_attrs
    minimum_value = getattr(section, minimum_attr)
    maximum_value = getattr(section, maximum_attr)
    range_min = float(minimum_value) if minimum_value is not None else None
    range_max = float(maximum_value) if maximum_value is not None else None
    if range_min is not None and range_max is not None:
        if range_min <= value <= range_max:
            return PolicyCriterion(spec.criterion_id, "ok", "recusa")
        return PolicyCriterion(
            spec.criterion_id,
            "alerta",
            "recusa",
            _range_reason(section.section_id, value, minimum=range_min, maximum=range_max),
        )
    if range_max is not None:  # faixa aberta do lado inferior (ex.: S2 — PAS < máx)
        if value < range_max:
            return PolicyCriterion(spec.criterion_id, "ok", "recusa")
        return PolicyCriterion(
            spec.criterion_id,
            "alerta",
            "recusa",
            f"PAS {_format_value(value)} igual ou acima do máximo "
            f"{_format_value(range_max)} da seção {section.section_id}",
        )
    if range_min is not None:
        if value >= range_min:
            return PolicyCriterion(spec.criterion_id, "ok", "recusa")
        return PolicyCriterion(
            spec.criterion_id,
            "alerta",
            "recusa",
            f"PAS {_format_value(value)} abaixo do mínimo "
            f"{_format_value(range_min)} da seção {section.section_id}",
        )
    raise AssertionError("critério de faixa sem threshold no catálogo")


def _evaluate_potassium(artifact: dict[str, Any], section: CriteriaSection) -> PolicyCriterion:
    """K: textual-informativo (decisão do parent 2026-09-07) — nunca recusa.

    K com valor extraído (confirmado/incerto) → alerta informativo citando o
    valor; K ausente/``nao_informado`` → ``nao_informado``. Nenhuma faixa é
    codificada (o ``parametrosHMD`` não a define).
    """
    observation = _read_lab(artifact, "potassium")
    if observation is None:
        return PolicyCriterion("potassium", "nao_informado", "informativo")
    unit = f" {observation.unit}" if observation.unit else ""
    return PolicyCriterion(
        "potassium",
        "alerta",
        "informativo",
        f"K {_format_value(observation.value)}{unit} informado — a seção exige "
        "'Eletrólitos: K normal'; confirmar avaliação médica",
    )


def _evaluate_section_criteria(
    artifact: dict[str, Any], section: CriteriaSection
) -> tuple[PolicyCriterion, ...]:
    """Rows dos critérios da seção na ordem da tabela (S1–S8)."""
    rows: list[PolicyCriterion] = []
    for spec in _SECTION_NUMERIC_CRITERIA:
        if not _applies_to_section(spec, section):
            continue
        observation = _read_lab(artifact, spec.exam_field)
        if observation is None or observation.status == "incerto":
            # Incerto/ausente nunca sustenta recusa — sinaliza ausência.
            rows.append(PolicyCriterion(spec.criterion_id, "nao_informado", "recusa"))
            continue
        rows.append(_compare_section_value(spec, section, observation.value))
    if section.potassium_required:
        rows.append(_evaluate_potassium(artifact, section))
    return tuple(rows)


# ── Requisitos gerais (todos os tipos — alertas informativos) ─────────────


def _matches_any_token(text: str, tokens: tuple[str, ...]) -> bool:
    """Casa um termo como palavra no texto normalizado (case/accent-insensitive)."""
    folded = _fold(text)
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", folded) is not None
        for token in tokens
    )


def _heparin_alert(name: str) -> PolicyCriterion:
    """Protocolo de enoxaparina/heparina SC conforme dose citada no nome."""
    folded = _fold(name)
    prophylactic = any(term in folded for term in _HEPARIN_PROPHYLACTIC_TERMS)
    therapeutic = any(term in folded for term in _HEPARIN_THERAPEUTIC_TERMS)
    if prophylactic and not therapeutic:
        detail = "suspender 12 horas antes (dose profilática)"
    elif therapeutic and not prophylactic:
        detail = "suspender 24 horas antes (dose terapêutica)"
    else:
        detail = (
            "suspender 12 horas antes se dose profilática ou 24 horas antes se dose terapêutica"
        )
    return PolicyCriterion(
        "anticoagulante",
        "alerta",
        "informativo",
        f"anticoagulante {name} em uso — {detail}",
    )


_GENERIC_ANTICOAGULANT_PROTOCOL = (
    "suspender conforme protocolo do documento (Marevan/Marcoumar/Cumadin/"
    "varfarina/Pradaxa: 7 dias; Xarelto: 3 dias; Eliquis/Lixiana: 48 horas; "
    "enoxaparina/heparina SC: 12h profilática/24h terapêutica)"
)


def _anticoagulant_alert(name: str) -> PolicyCriterion | None:
    """Alerta por fármaco; ``None`` quando o nome não casa nenhum anticoagulante."""
    if _matches_any_token(name, _SEVEN_DAY_TOKENS):
        return PolicyCriterion(
            "anticoagulante",
            "alerta",
            "informativo",
            f"anticoagulante {name} em uso — suspender 7 dias antes do procedimento",
        )
    if _matches_any_token(name, _THREE_DAY_TOKENS):
        return PolicyCriterion(
            "anticoagulante",
            "alerta",
            "informativo",
            f"anticoagulante {name} em uso — suspender 3 dias antes do procedimento",
        )
    if _matches_any_token(name, _FORTY_EIGHT_HOUR_TOKENS):
        return PolicyCriterion(
            "anticoagulante",
            "alerta",
            "informativo",
            f"anticoagulante {name} em uso — suspender 48 horas antes do procedimento",
        )
    if _matches_any_token(name, _HEPARIN_TOKENS):
        return _heparin_alert(name)
    return None


def _medication_requirement_rows(
    artifact: dict[str, Any],
) -> tuple[PolicyCriterion, ...]:
    """Anticoagulantes/antiagregantes/metformina a partir das medicações (R1)."""
    anticoagulant_alerts: list[PolicyCriterion] = []
    antiaggregant_alerts: list[PolicyCriterion] = []
    metformin_alerts: list[PolicyCriterion] = []
    medications = artifact.get("medicacoes")
    if isinstance(medications, list):
        for medication in medications:
            if not isinstance(medication, dict):
                continue
            name = medication.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            display = name.strip()
            drug_class = medication.get("drug_class")
            anticoagulant = _anticoagulant_alert(display)
            if anticoagulant is None and drug_class == "anticoagulante":
                # Classe marcada sem nome da lista do documento: alerta com o
                # protocolo genérico da fonte (nunca inventar prazo).
                anticoagulant = PolicyCriterion(
                    "anticoagulante",
                    "alerta",
                    "informativo",
                    f"anticoagulante {display} em uso — {_GENERIC_ANTICOAGULANT_PROTOCOL}",
                )
            if anticoagulant is not None:
                anticoagulant_alerts.append(anticoagulant)
                continue
            if _matches_any_token(display, ("aas", "clopidogrel", "ticagrelor", "prasugrel")) or (
                drug_class == "antiagregante"
            ):
                antiaggregant_alerts.append(
                    PolicyCriterion(
                        "antiagregante",
                        "alerta",
                        "informativo",
                        f"antiagregante {display} em uso — em geral não é necessária "
                        "a suspensão antes do procedimento",
                    )
                )
                continue
            if _matches_any_token(display, ("metformina",)):
                metformin_alerts.append(
                    PolicyCriterion("metformina", "alerta", "informativo", _METFORMIN_REASON)
                )

    rows: list[PolicyCriterion] = anticoagulant_alerts
    if not anticoagulant_alerts:
        rows.append(PolicyCriterion("anticoagulante", "ok", "informativo"))
    rows.extend(antiaggregant_alerts)
    if not antiaggregant_alerts:
        rows.append(PolicyCriterion("antiagregante", "ok", "informativo"))
    rows.extend(metformin_alerts)
    if not metformin_alerts:
        rows.append(PolicyCriterion("metformina", "ok", "informativo"))
    return tuple(rows)


def _allergy_row(artifact: dict[str, Any]) -> PolicyCriterion:
    """Alergia a contraste/frutos do mar/iodo → protocolo de dessensibilização."""
    triggered = any(
        _matches_any_token(text, _ALERGEN_TERMS) and "alerg" in _fold(text)
        for text in _narrative_texts(artifact)
    )
    if not triggered:
        return PolicyCriterion("alergia", "ok", "informativo")
    return PolicyCriterion("alergia", "alerta", "informativo", _ALLERGY_REASON)


def _weight_row(artifact: dict[str, Any]) -> PolicyCriterion:
    """Peso > 180 kg (menção no texto) → alerta do limite dos equipamentos."""
    for text in _narrative_texts(artifact):
        for pattern in _WEIGHT_PATTERNS:
            for match in pattern.finditer(text):
                raw = match.group(1).replace(",", ".")
                try:
                    value = float(raw)
                except ValueError:
                    continue
                if value > _WEIGHT_LIMIT_KG:
                    return PolicyCriterion(
                        "peso",
                        "alerta",
                        "informativo",
                        f"peso {_format_value(value)} kg acima do limite de 180 kg "
                        "dos equipamentos — confirmar preparo",
                    )
    return PolicyCriterion("peso", "ok", "informativo")


def _isolation_row(artifact: dict[str, Any]) -> PolicyCriterion:
    """Isolamento mencionado → sinalizar a unidade (alerta informativo)."""
    if any(_ISOLATION_PATTERN.search(text) for text in _narrative_texts(artifact)):
        return PolicyCriterion(
            "isolamento",
            "alerta",
            "informativo",
            "paciente em isolamento — sinalizar a unidade",
        )
    return PolicyCriterion("isolamento", "ok", "informativo")


def _nephroprotection_row(artifact: dict[str, Any]) -> PolicyCriterion:
    """Cr ≥ 1,5 mg/dL → nefroproteção + liberação da Nefrologia (informativo)."""
    observation = _read_lab(artifact, "creatinine")
    if observation is None:
        return PolicyCriterion("nefroprotecao", "nao_informado", "informativo")
    if observation.value >= _CREATININE_RISK:
        return PolicyCriterion(
            "nefroprotecao",
            "alerta",
            "informativo",
            f"creatinina {_format_value(observation.value)} ≥ 1,5 mg/dL — realizar "
            "nefroproteção (SF 0,9% 1 ml/kg/h 24h antes e depois) e obter "
            "liberação da Nefrologia",
        )
    return PolicyCriterion("nefroprotecao", "ok", "informativo")


def _anesthetic_row(procedure_type: str) -> PolicyCriterion:
    """Suporte anestésico do perfil do catálogo → alerta informativo (R1)."""
    profile = get_procedure_profile(procedure_type)
    return PolicyCriterion(
        "anestesico",
        "alerta",
        "informativo",
        f"{profile.label}: procedimento que frequentemente requer suporte anestésico — "
        "confirmar equipe",
    )


def _evaluate_general_requirements(
    artifact: dict[str, Any], procedure_type: str
) -> tuple[PolicyCriterion, ...]:
    """Requisitos gerais — sempre avaliados; severidade informativa (R1/R2)."""
    rows = list(_medication_requirement_rows(artifact))
    rows.append(_allergy_row(artifact))
    rows.append(_weight_row(artifact))
    rows.append(PolicyCriterion("jejum", "alerta", "informativo", _FASTING_REASON))
    rows.append(_isolation_row(artifact))
    rows.append(_nephroprotection_row(artifact))
    if get_procedure_profile(procedure_type).anesthetic_support:
        rows.append(_anesthetic_row(procedure_type))
    return tuple(rows)


# ── R1/R2: avaliação pura por procedimento ─────────────────────────────────


def evaluate_preop_policy(structured_data: dict[str, Any], procedure_type: str) -> PolicyResult:
    """Policy consultiva determinística para um procedimento (pura).

    Critérios da seção do tipo (S1–S8, thresholds do catálogo) + requisitos
    gerais. Severidade: critério de seção fora do threshold → motivo de
    recusa; requisito geral → alerta informativo; ``nao_informado`` nunca
    recusa. Tipo fora do catálogo → ``KeyError`` nomeando o tipo (fail-fast).
    """
    profile = get_procedure_profile(procedure_type)
    section = CRITERIA_SECTIONS[profile.criteria_section]
    criteria = _evaluate_section_criteria(
        structured_data, section
    ) + _evaluate_general_requirements(structured_data, procedure_type)
    refusal_reasons = tuple(
        criterion.reason
        for criterion in criteria
        if criterion.status == "alerta" and criterion.severity == "recusa"
    )
    recommendation: Recommendation = "recomenda_recusar" if refusal_reasons else "recomenda_aceitar"
    return PolicyResult(
        procedure_type=procedure_type,
        section_id=section.section_id,
        recommendation=recommendation,
        refusal_reasons=refusal_reasons,
        criteria=criteria,
    )


# ── R3: wrapper transacional ───────────────────────────────────────────────


def _serialize_criterion(criterion: PolicyCriterion) -> dict[str, Any]:
    return {
        "criterion": criterion.criterion,
        "status": criterion.status,
        "severity": criterion.severity,
        "reason": criterion.reason,
    }


def _serialize_result(result: PolicyResult) -> dict[str, Any]:
    return {
        "procedure_type": result.procedure_type,
        "section_id": result.section_id,
        "recommendation": result.recommendation,
        "refusal_reasons": list(result.refusal_reasons),
        "criteria": [_serialize_criterion(criterion) for criterion in result.criteria],
    }


def evaluate_case_policies(
    case: Case,
    *,
    user: User | None = None,
    role: str = "system",
) -> dict[str, dict[str, Any]]:
    """Roda a policy por procedimento declarado e persiste + eventa (R3).

    Persiste ``Case.policy_result`` (JSON por tipo — saída da função pura) e
    grava o evento ``CASE_POLICY_EVALUATED`` com resumo enxuto por tipo
    (recomendação + contagens — nunca conteúdo clínico bruto), numa única
    transação. Sem procedimentos declarados → ``ValueError`` nomeando a
    exigência.
    """
    declared_types = get_declared_procedure_types(case)
    if not declared_types:
        raise ValueError("caso sem procedimentos declarados — a policy exige a declaração")

    stored = case.structured_data
    artifact = stored if isinstance(stored, dict) else {}
    results = {
        procedure_type: evaluate_preop_policy(artifact, procedure_type)
        for procedure_type in declared_types
    }
    serialized = {
        procedure_type: _serialize_result(result) for procedure_type, result in results.items()
    }

    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        locked.policy_result = serialized
        locked.save(update_fields=["policy_result"])
        summary = [
            {
                "procedure_type": result.procedure_type,
                "recommendation": result.recommendation,
                "criteria_total": len(result.criteria),
                "criteria_alertas": sum(
                    1 for criterion in result.criteria if criterion.status == "alerta"
                ),
                "criteria_nao_informados": sum(
                    1 for criterion in result.criteria if criterion.status == "nao_informado"
                ),
                "refusal_reason_count": len(result.refusal_reasons),
            }
            for result in results.values()
        ]
        CaseEvent.objects.create(
            case=locked,
            event_type=CaseEventType.CASE_POLICY_EVALUATED,
            actor_type=ActorType.USER if user is not None else ActorType.SYSTEM,
            actor=user,
            actor_role=role or "",
            payload={"procedures": summary},
        )
    return serialized
