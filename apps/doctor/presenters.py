"""Presenter do detalhe do caso médico (doctor-queue-decision, slice 003, R2).

Serviço puro ``build_case_detail_context(case)`` (sem request) que monta o
dict de contexto a partir dos artefatos persistidos — o template apenas
renderiza. A re-identificação acontece **exclusivamente aqui, na renderização**
(design D3): ``reidentify_text`` no ``summary_text`` e o helper aditivo
``reidentify_structure`` (apps/anonymization/reidentify.py) nos artefatos JSON
aninhados (``structured_data``/``policy_result``/``suggested_action``) com o
mapa do caso — a barreira "LLM só vê tokens" não muda (nenhuma chamada LLM
neste módulo).

Prior-case: o lookup é read-only (``lookup_prior_case_context`` — o evento de
lookup já é gravado pelo pipeline do change 06); o resumo traz o motivo
ANONIMIZADO (para o LLM2) e o card do médico re-busca o motivo REAL na row
``CaseProcedure`` do caso prévio via ``prior_case_id`` (D3 — médico vê dados
reais).

Estruturas do contrato (todas re-identificadas): ``sections`` (por chave do
schema base — nunca o JSON bruto), ``advisories`` (policy por procedimento com
``criterion``/``status``/``severity``/``reason`` + sugestão/agregado do LLM2
com ``motivos``), ``requirements`` (requisitos gerais acionáveis derivados dos
alertas informativos), ``prior_cases`` (motivo real) e a flag ``can_decide``
(estado == AWAITING_DOCTOR).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apps.anonymization.reidentify import reidentify_structure, reidentify_text
from apps.cases.models import Case, CaseProcedure, CaseStatus, DoctorDisposition
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.cases.procedures import get_declared_procedure_types
from apps.pipeline.prior_case import lookup_prior_case_context

# Perfil do catálogo por tipo (label/subtipo dos cards e seções).
_PROFILE_BY_TYPE = {profile.procedure_type: profile for profile in PROCEDURE_PROFILES}
# Rótulo legível da disposição médica atual da row (DoctorDisposition).
_DISPOSITION_LABELS = dict(DoctorDisposition.choices)

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

# Rótulo legível por critério da policy (numéricos de seção + gerais).
_CRITERION_LABELS: dict[str, str] = {
    "platelets": "Plaquetas",
    "inr": "INR",
    "hemoglobin": "Hemoglobina",
    "creatinine": "Creatinina",
    "glucose": "Glicemia",
    "potassium": "Potássio",
    "systolic_blood_pressure": "Pressão arterial sistólica",
    "anticoagulante": "Anticoagulante",
    "antiagregante": "Antiagregante",
    "metformina": "Metformina",
    "alergia": "Alergia a contraste",
    "peso": "Peso",
    "jejum": "Jejum",
    "isolamento": "Isolamento",
    "nefroprotecao": "Nefroproteção",
    "anestesico": "Suporte anestésico",
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

_RECOMMENDATION_LABELS: dict[str, str] = {
    "recomenda_aceitar": "Recomenda aceitar",
    "recomenda_recusar": "Recomenda recusar",
}
_SUGGESTION_LABELS: dict[str, str] = {
    "aceitar": "Aceitar",
    "recusar": "Recusar",
}
_PRIOR_DECISION_LABELS: dict[str, str] = {
    DoctorDisposition.APPROVED: "Aprovado",
    DoctorDisposition.DENIED: "Negado",
}
_PRIOR_ORIGIN_LABELS: dict[str, str] = {
    "occurrence_number": "Nº de ocorrência",
    "name_birthdate_fallback": "Nome e nascimento",
}

# Severidades exibidas como requisito geral acionável (policy — informativo).
_GENERAL_REQUIREMENT_SEVERITY = "informativo"


def _criterion_label(criterion: str) -> str:
    """Rótulo legível do critério da policy (fallback: o próprio id)."""
    return _CRITERION_LABELS.get(criterion, criterion)


def _exam_label(key: str) -> str:
    """Rótulo legível de um exame da seção exames."""
    return _EXAM_LABELS.get(key, key)


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _lines_as_strs(lines: list[object]) -> list[str]:
    return [str(line) for line in lines]


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


def _build_structure_sections(structured: object) -> list[dict[str, object]]:
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


# ── Alertas consultivos + requisitos gerais + agregado ─────────────────────


def _procedure_label(procedure_type: str) -> str:
    profile = _PROFILE_BY_TYPE.get(procedure_type)
    return profile.label if profile is not None else procedure_type


def _as_str_list(value: object) -> list[str]:
    return [str(item) for item in _as_list(value) if isinstance(item, str) and item.strip()]


def _criteria_of(policy: object) -> list[dict[str, object]]:
    """Critérios da policy de um procedimento (formato serializado do 005)."""
    if not isinstance(policy, dict):
        return []
    criteria: list[dict[str, object]] = []
    for criterion in _as_list(policy.get("criteria")):
        if isinstance(criterion, dict) and isinstance(criterion.get("criterion"), str):
            criteria.append(criterion)
    return criteria


def _criteria_in_alerta(policy: object) -> list[dict[str, object]]:
    """Critérios em estado ``alerta`` (motivos de recusa + alertas informativos)."""
    alerts: list[dict[str, object]] = []
    for criterion in _criteria_of(policy):
        if criterion.get("status") == "alerta":
            alerts.append(criterion)
    return alerts


def _build_advisory(
    procedure_type: str,
    *,
    policy: object,
    suggestion_block: object,
) -> dict[str, object]:
    """Card consultivo de um procedimento declarado (policy + LLM2)."""
    policy_dict = policy if isinstance(policy, dict) else {}
    recommendation = _as_str(policy_dict.get("recommendation"))
    refusal_reasons = _as_str_list(policy_dict.get("refusal_reasons"))

    criteria_alerts: list[dict[str, object]] = []
    for criterion in _criteria_in_alerta(policy):
        reason = _as_str(criterion.get("reason"))
        criteria_alerts.append(
            {
                "criterion": criterion["criterion"],
                "label": _criterion_label(str(criterion["criterion"])),
                "status": _as_str(criterion.get("status")),
                "severity": _as_str(criterion.get("severity")),
                "reason": reason,
            }
        )

    suggestion = ""
    motivos: list[str] = []
    if isinstance(suggestion_block, dict):
        suggestion = _as_str(suggestion_block.get("suggestion"))
        motivos = _as_str_list(suggestion_block.get("motivos"))

    profile = _PROFILE_BY_TYPE.get(procedure_type)
    return {
        "procedure_type": procedure_type,
        "label": _procedure_label(procedure_type),
        "subtype": profile.doctor_subtipo if profile is not None else "",
        "section_id": _as_str(policy_dict.get("section_id")),
        "recommendation": recommendation,
        "recommendation_label": _RECOMMENDATION_LABELS.get(recommendation, "Sem recomendação"),
        "refusal_reasons": refusal_reasons,
        "criteria_alerts": criteria_alerts,
        "suggestion": suggestion,
        "suggestion_label": _SUGGESTION_LABELS.get(suggestion, ""),
        "motivos": motivos,
    }


def _build_requirements(
    declared_types: tuple[str, ...],
    policy_result: object,
) -> list[dict[str, object]]:
    """Requisitos gerais acionáveis (protocolos por fármaco, nefroproteção…).

    Derivados dos alertas informativos (severidade ``informativo``) da policy
    por procedimento, deduplicados por (critério, motivo) — o mesmo alerta
    geral aparece na policy de cada procedimento declarado.
    """
    policies = policy_result if isinstance(policy_result, dict) else {}
    requirements: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for procedure_type in declared_types:
        for criterion in _criteria_of(policies.get(procedure_type)):
            if (
                criterion.get("status") != "alerta"
                or criterion.get("severity") != _GENERAL_REQUIREMENT_SEVERITY
            ):
                continue
            criterion_id = str(criterion["criterion"])
            reason = _as_str(criterion.get("reason"))
            key = (criterion_id, reason)
            if key in seen or not reason:
                continue
            seen.add(key)
            requirements.append(
                {
                    "criterion": criterion_id,
                    "label": _criterion_label(criterion_id),
                    "reason": reason,
                }
            )
    return requirements


def _build_aggregate(suggested_action: object) -> dict[str, object]:
    """Agregado do LLM2 (chave ``motivos``) com rótulo legível."""
    aggregate = suggested_action if isinstance(suggested_action, dict) else {}
    suggestion = _as_str(aggregate.get("suggestion"))
    motivos = _as_str_list(aggregate.get("motivos"))
    return {
        "suggestion": suggestion,
        "label": _SUGGESTION_LABELS.get(suggestion, ""),
        "motivos": motivos,
    }


# ── Prior-case (motivo real) ───────────────────────────────────────────────


def _format_iso_datetime(value: str) -> str:
    """Formata data ISO do resumo do prior-case como d/m/Y H:i (nunca explode)."""
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return value
    return parsed.strftime("%d/%m/%Y %H:%M")


def _prior_real_reason(prior_case_id: str, procedure_type: str) -> str:
    """Motivo REAL do médico na row do caso prévio (card do médico — D3)."""
    row = CaseProcedure.objects.filter(
        case_id=prior_case_id,
        procedure_type=procedure_type,
    ).first()
    if row is None or not row.doctor_reason.strip():
        return "não informado"
    return row.doctor_reason.strip()


def _build_prior_cases(
    case: Case,
    declared_types: tuple[str, ...],
) -> list[dict[str, object]]:
    """Cards de prior-case por tipo declarado, com o motivo real (R2/D3)."""
    prior_cases: list[dict[str, object]] = []
    for procedure_type in declared_types:
        summary = lookup_prior_case_context(case, procedure_type)
        if summary is None:
            continue
        prior_cases.append(
            {
                "procedure_type": procedure_type,
                "label": _procedure_label(procedure_type),
                "prior_case_id": summary.prior_case_id,
                "decided_at": _format_iso_datetime(summary.decided_at),
                "decision_label": _PRIOR_DECISION_LABELS.get(summary.decision, summary.decision),
                "reason": _prior_real_reason(summary.prior_case_id, procedure_type),
                "origin_label": _PRIOR_ORIGIN_LABELS.get(summary.origin, summary.origin),
                "denial_count": summary.prior_denial_count,
            }
        )
    return prior_cases


# ── API pública ────────────────────────────────────────────────────────────


def build_case_detail_context(case: Case) -> dict[str, object]:
    """Contexto re-identificado do detalhe do caso para o médico (puro, R2).

    Monta identificação real, tipos declarados com subtipo e disposição atual,
    sumário e estrutura re-identificados por seção, alertas consultivos da
    policy por procedimento com a sugestão do LLM2, requisitos gerais
    acionáveis, prior-case com motivo real, e a flag ``can_decide``. A
    re-identificação usa exclusivamente o mapa do caso (tokens de espaços
    alheios podem sobrar como tokens — sem vazamento). Caso sem artefatos →
    seções vazias.
    """
    pseudonym_map = case.pseudonym_map if isinstance(case.pseudonym_map, dict) else {}

    structured = reidentify_structure(case.structured_data, pseudonym_map)
    policy_result = reidentify_structure(case.policy_result, pseudonym_map)
    suggested_action = reidentify_structure(case.suggested_action, pseudonym_map)
    summary_text = reidentify_text(case, case.summary_text)

    declared_types = get_declared_procedure_types(case)
    policies = policy_result if isinstance(policy_result, dict) else {}
    suggestions = suggested_action if isinstance(suggested_action, dict) else {}
    procedures_suggestions = suggestions.get("procedures")
    procedures_map = procedures_suggestions if isinstance(procedures_suggestions, dict) else {}

    rows = CaseProcedure.objects.filter(case=case)
    rows_by_type = {row.procedure_type: row for row in rows}
    declared: list[dict[str, object]] = []
    for procedure_type in declared_types:
        profile = _PROFILE_BY_TYPE.get(procedure_type)
        row = rows_by_type.get(procedure_type)
        disposition = row.doctor_disposition if row is not None else DoctorDisposition.PENDING
        declared.append(
            {
                "procedure_type": procedure_type,
                "label": _procedure_label(procedure_type),
                "subtype": profile.doctor_subtipo if profile is not None else "",
                "disposition": disposition,
                "disposition_label": _DISPOSITION_LABELS.get(disposition, disposition),
            }
        )

    advisories: list[dict[str, object]] = []
    for procedure_type in declared_types:
        advisories.append(
            _build_advisory(
                procedure_type,
                policy=policies.get(procedure_type),
                suggestion_block=procedures_map.get(procedure_type),
            )
        )

    summary_lines = (
        [line.strip() for line in summary_text.splitlines() if line.strip()]
        if summary_text.strip()
        else []
    )

    identification = {
        "patient_name": case.patient_name,
        "agency_record_number": case.agency_record_number,
        "birth_date": case.patient_birth_date,
    }

    sections = _build_structure_sections(structured)

    return {
        "case_id": str(case.case_id),
        "status_label": case.get_status_display(),
        "can_decide": case.status == CaseStatus.AWAITING_DOCTOR,
        "identification": identification,
        "declared": declared,
        "summary_text": summary_text,
        "summary_lines": summary_lines,
        "sections": sections,
        "advisories": advisories,
        "requirements": _build_requirements(declared_types, policy_result),
        "aggregate": _build_aggregate(suggestions.get("aggregate")),
        "prior_cases": _build_prior_cases(case, declared_types),
        "has_structure": any(section["lines"] for section in sections),
    }
