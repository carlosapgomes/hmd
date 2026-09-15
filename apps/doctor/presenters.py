"""Presenter do detalhe do caso médico (doctor-queue-decision, slices 003/004).

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

Slice 004 (R5): o contexto ganha a seção de decisões pós-decisão — cada row
declarada leva ``reason``/``decided_at`` e o ator/data do evento
``CASE_DOCTOR_DECISIONS_RECORDED`` (``decision_event``). A trilha de eventos
saiu do detalhe médico no slice 003 do painel-ats-parity (D4): passou a viver
no painel, e o detalhe médico só mantém o ator/data da decisão.

Anexos (attachment-processing-ocr, slice 004/D5): seção aditiva
``attachments`` — card por anexo com nome/método/status/badge e
resumo/evidência re-identificados com o mapa DO ANEXO via o núcleo puro
``reidentify`` (sem helper novo; mapa do anexo auto-suficiente, fallback ao
mapa do caso apenas defensivo). ``mismatch`` é alerta consultivo (danger +
texto fixo) — nunca descarta nem bloqueia. Casos sem anexos não ganham a
chave (template não renderiza a seção).

Estruturas do contrato (todas re-identificadas): ``sections`` (por chave do
schema base — nunca o JSON bruto), ``advisories`` (policy por procedimento com
``criterion``/``status``/``severity``/``reason`` + sugestão/agregado do LLM2
com ``motivos``), ``requirements`` (requisitos gerais acionáveis derivados dos
alertas informativos), ``prior_cases`` (motivo real), ``declared``
(disposição atual + motivo/data da decisão) e a flag ``can_decide`` (estado ==
AWAITING_DOCTOR).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.utils.timezone import localtime

from apps.anonymization.reidentify import reidentify, reidentify_structure, reidentify_text
from apps.attachments.models import AttachmentStatus, CaseAttachment, ExtractionMethod, PatientMatch
from apps.cases.events import CaseEventType
from apps.cases.models import (
    Case,
    CaseEvent,
    CaseProcedure,
    CaseStatus,
    DetectionStatus,
    DoctorDisposition,
)
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.pipeline.prior_case import lookup_prior_case_context

# Perfil do catálogo por tipo (label/subtipo dos cards e seções).
_PROFILE_BY_TYPE = {profile.procedure_type: profile for profile in PROCEDURE_PROFILES}
# Ordem canônica do catálogo (mesma regra de ``apps/cases/procedures.py``) para
# as rows detectadas não-declaradas do card de procedimentos; tipo fora do
# catálogo (buraco documentado de ``bulk_create``) ordena por último.
_CATALOG_ORDER: dict[str, int] = {
    profile.procedure_type: index for index, profile in enumerate(PROCEDURE_PROFILES)
}
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


# Formato de exibição de data/hora do contexto (R5) — o contexto é
# JSON-serializável: timestamps viram strings formatadas como no prior-case.
_DATETIME_DISPLAY_FORMAT = "%d/%m/%Y %H:%M"


def _format_datetime(value: datetime | None) -> str:
    """Data/hora exibível (fuso local da aplicação) ou vazio quando nulo."""
    if value is None:
        return ""
    return localtime(value).strftime(_DATETIME_DISPLAY_FORMAT)


# ── Ator/data da decisão (slice 003 do painel-ats-parity, D4) ──────────────


def _decision_event_summary(event: CaseEvent) -> dict[str, object]:
    """Ator/data do evento canônico de decisão para o card «Decisões registradas».

    A TRILHA saiu do detalhe médico (vive no painel — slice 003 do
    painel-ats-parity): o presenter não monta mais a lista de eventos, mas o
    card pós-decisão continua exibindo quem decidiu e quando, por consulta
    DEDICADA ao evento ``CASE_DOCTOR_DECISIONS_RECORDED`` mais recente.
    """
    return {
        "actor_display": event.actor.display_name if event.actor else "Sistema",
        "actor_role": event.actor_role,
        "timestamp": _format_datetime(event.timestamp),
    }


# ── Anexos (slice 004, attachment-processing-ocr; design D5) ──────────────

# Rótulo legível do método de extração do anexo (R1) — códigos crus jamais
# exibidos; vazio quando o anexo ainda não definiu método (pendente).
_ATTACHMENT_METHOD_LABELS: dict[str, str] = {
    ExtractionMethod.LOCAL_PDF: "Local",
    ExtractionMethod.VISION: "OCR externo",
}
_ATTACHMENT_STATUS_LABELS: dict[str, str] = dict(AttachmentStatus.choices)


def _attachment_badge(status: str, patient_match: str | None) -> str:
    """Badge do card do anexo (R1): match/mismatch/unknown quando processado,
    senão o estado do processamento (pendente/processando/falhou)."""
    if status == AttachmentStatus.PROCESSED:
        # Processado sem resultado válido é inalcançável no fluxo (o slice 003
        # persiste match/unknown no mesmo atomic) — fallback defensivo "unknown"
        # em vez do rótulo "pendente" (P2 F1 da review).
        return patient_match if patient_match in PatientMatch.values else "unknown"
    if status == AttachmentStatus.PROCESSING:
        return "processando"
    if status == AttachmentStatus.FAILED:
        return "falhou"
    return "pendente"


def _attachment_reidentified_text(
    text: str,
    pseudonym_map: object,
    case: Case,
) -> str:
    """Resumo/evidência do anexo re-identificados (R1/D5).

    Usa o núcleo puro ``reidentify(text, pseudonym_map)`` com o mapa DO ANEXO
    (auto-suficiente — namespace estendido do caso, D4; sem helper novo nem
    merge de mapas); fallback ao mapa do caso apenas defensivo quando o anexo
    ainda não carrega o próprio namespace (pré-slices). Texto vazio → vazio.
    """
    if not text.strip():
        return ""
    attachment_map = pseudonym_map if isinstance(pseudonym_map, dict) else {}
    if not attachment_map:
        attachment_map = case.pseudonym_map if isinstance(case.pseudonym_map, dict) else {}
    return reidentify(text, attachment_map)


def _build_attachment_card(attachment: CaseAttachment, case: Case) -> dict[str, object]:
    """Card exibível de um anexo (R1/D5): nome, método, status, badge e
    resumo/evidência re-identificados — SOMENTE quando processado (anexos
    pendentes/processando/falhou não têm resumo, apenas o status)."""
    status = attachment.status
    processed = status == AttachmentStatus.PROCESSED
    return {
        "original_filename": attachment.original_filename,
        "method_label": _ATTACHMENT_METHOD_LABELS.get(attachment.extraction_method, ""),
        "status_label": _ATTACHMENT_STATUS_LABELS.get(status, status),
        "badge": _attachment_badge(status, attachment.patient_match),
        "summary": (
            _attachment_reidentified_text(
                attachment.verification_summary, attachment.pseudonym_map, case
            )
            if processed
            else ""
        ),
        "evidence": (
            _attachment_reidentified_text(
                attachment.verification_evidence, attachment.pseudonym_map, case
            )
            if processed
            else ""
        ),
    }


# ── API pública ────────────────────────────────────────────────────────────


def build_case_detail_context(case: Case) -> dict[str, object]:
    """Contexto re-identificado do detalhe do caso para o médico (puro, R2/R5).

    Monta identificação real, procedimentos do caso com subtipo, origem
    (``is_declared``), detecção (``detection``; ``pending`` antes da
    reconciliação — o template omite o badge) e disposição atual (+ motivo/
    data da decisão nas rows já decididas), sumário e estrutura
    re-identificados por seção, alertas consultivos da policy por procedimento
    com a sugestão do LLM2, requisitos gerais acionáveis, prior-case com motivo
    real, a flag ``can_decide`` (= estado ``AWAITING_DOCTOR``), o ator/data da
    decisão (``decision_event``, consulta dedicada) e a flag ``is_failed`` para
    o badge de erro. ``declared`` carrega TODAS as rows do caso — declaradas
    primeiro (ordem canônica do contrato) e depois as detectadas não-declaradas
    que sobrevivem ao bypass da divergência — numa leitura única da relação. A
    trilha de eventos saiu do detalhe médico (vive no painel). A
    re-identificação usa exclusivamente o mapa do caso (tokens de espaços
    alheios podem sobrar como tokens — sem vazamento). Caso sem artefatos →
    seções vazias.
    """
    pseudonym_map = case.pseudonym_map if isinstance(case.pseudonym_map, dict) else {}

    structured = reidentify_structure(case.structured_data, pseudonym_map)
    policy_result = reidentify_structure(case.policy_result, pseudonym_map)
    suggested_action = reidentify_structure(case.suggested_action, pseudonym_map)
    summary_text = reidentify_text(case, case.summary_text)

    # Leitura ÚNICA das rows (review P2): declared_by_nir deriva daqui na
    # ordem canônica do catálogo (mesma semântica de
    # ``get_declared_procedure_types``), sem segunda query.
    rows = list(CaseProcedure.objects.filter(case=case))
    rows_by_type = {row.procedure_type: row for row in rows}
    declared_types = tuple(
        sorted(
            (row.procedure_type for row in rows if row.declared_by_nir),
            key=lambda procedure_type: _CATALOG_ORDER.get(procedure_type, len(_CATALOG_ORDER)),
        )
    )
    policies = policy_result if isinstance(policy_result, dict) else {}
    suggestions = suggested_action if isinstance(suggested_action, dict) else {}
    procedures_suggestions = suggestions.get("procedures")
    procedures_map = procedures_suggestions if isinstance(procedures_suggestions, dict) else {}

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
                "is_declared": True,
                "detection": row.detection_status if row is not None else DetectionStatus.PENDING,
                "disposition": disposition,
                "disposition_label": _DISPOSITION_LABELS.get(disposition, disposition),
                # Decisão por row (R5): motivo e data/hora exibível quando a row
                # já foi decidida; vazios antes da decisão.
                "reason": row.doctor_reason.strip() if row is not None else "",
                "decided_at": _format_datetime(row.doctor_decided_at) if row is not None else "",
            }
        )
    # Rows detectadas não-declaradas (bypass da divergência: a row permanece) —
    # entram no fim do card, na ordem canônica do catálogo.
    extra_rows = sorted(
        (row for row in rows if not row.declared_by_nir),
        key=lambda row: _CATALOG_ORDER.get(row.procedure_type, len(_CATALOG_ORDER)),
    )
    for row in extra_rows:
        profile = _PROFILE_BY_TYPE.get(row.procedure_type)
        declared.append(
            {
                "procedure_type": row.procedure_type,
                "label": _procedure_label(row.procedure_type),
                "subtype": profile.doctor_subtipo if profile is not None else "",
                "is_declared": False,
                "detection": row.detection_status,
                "disposition": row.doctor_disposition,
                "disposition_label": _DISPOSITION_LABELS.get(
                    row.doctor_disposition, row.doctor_disposition
                ),
                "reason": row.doctor_reason.strip(),
                "decided_at": _format_datetime(row.doctor_decided_at),
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
        # Demografia do cabeçalho SESAB (change doctor-detail-context, D1):
        # campos estruturais do caso, sem transformação.
        "patient_age": case.patient_age,
        "patient_gender": case.patient_gender,
        "patient_race": case.patient_race,
    }

    sections = _build_structure_sections(structured)

    # Ator/data da decisão (D4): consulta DEDICADA ao evento canônico mais
    # recente — a trilha saiu do detalhe médico (vive no painel).
    decision_event_row = (
        case.events.filter(event_type=CaseEventType.CASE_DOCTOR_DECISIONS_RECORDED)
        .select_related("actor")
        .order_by("-id")
        .first()
    )
    decision_event = _decision_event_summary(decision_event_row) if decision_event_row else None

    # Cards de anexo (slice 004, D5): seção aditiva — casos sem anexos
    # permanecem com o contexto do change 07 intacto (chave ausente).
    attachment_cards = [
        _build_attachment_card(attachment, case) for attachment in case.attachments.all()
    ]

    context: dict[str, object] = {
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
        "decision_event": decision_event,
        "is_failed": case.status == CaseStatus.FAILED,
        "has_structure": any(section["lines"] for section in sections),
    }
    if attachment_cards:
        context["attachments"] = attachment_cards
    return context
