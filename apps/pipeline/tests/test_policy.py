"""Testes da policy consultiva determinística (slice 005, R1–R3, design D6).

Cobre: a tabela de critérios explícita por seção S1–S8 (parameterized com
valores limite dentro/fora/ausente — R7), o potássio como critério
textual-informativo (decisão do parent 2026-09-07, pendente de validação do
dono: K informado → alerta informativo com valor; K ausente → nao_informado;
K nunca entra em motivos de recusa), os requisitos gerais (anticoagulantes por
fármaco com protocolo fiel ao documento, antiagregantes informativos,
metformina/alergia/peso/jejum/isolamento/Cr/anestésico) e a recomendação
global com severidade determinística: critério de seção fora do threshold →
motivo de recusa; requisito geral → alerta informativo; nao_informado nunca
recusa. Também cobre o wrapper ``evaluate_case_policies`` (persistência +
evento).

A função pura ``evaluate_preop_policy`` roda sem banco/LLM — mesma entrada →
mesma saída; os testes de wrapper usam ``django_db``.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.cases.procedure_catalog import CRITERIA_SECTIONS, CriteriaSection
from apps.pipeline.policy import (
    PolicyCriterion,
    PolicyResult,
    evaluate_case_policies,
    evaluate_preop_policy,
)

# Seções que exigem "Eletrólitos: K normal" no documento clínico.
_K_SECTIONS = ("S1", "S2", "S3", "S5")


@pytest.fixture
def owner_user() -> Any:
    """Dono (criador) dos casos dos testes de wrapper — sem papel (FSM não exige)."""
    from apps.accounts.models import User

    return User.objects.create_user(username="dono-policy", password="senha-teste")


# ── Helpers de artefato LLM1 ───────────────────────────────────────────────


def _lab_entry(value: float | int, unit: str | None = None) -> dict[str, Any]:
    """Entrada de exame no formato persistido do artefato LLM1 (sem evidência)."""
    entry: dict[str, Any] = {"status": "confirmado", "value": value}
    if unit is not None:
        entry["unit"] = unit
    return entry


def _artifact(
    *,
    exames: dict[str, Any] | None = None,
    medicacoes: list[dict[str, Any]] | None = None,
    contraindicacoes: list[dict[str, Any]] | None = None,
    contexto_clinico: str | None = None,
    trechos_nao_classificados: list[str] | None = None,
) -> dict[str, Any]:
    """Artefato LLM1 base (sem PII; só tokens) com overrides por campo."""
    return {
        "pedido": {
            "procedimentos_solicitados": ["cat_cardiaco"],
            "evidence_spans": [],
        },
        "contexto_clinico": contexto_clinico or "Paciente <PESSOA_1> em avaliacao hemodinamica.",
        "linha_do_tempo": [],
        "exames": exames,
        "medicacoes": medicacoes or [],
        "comorbidades": [],
        "contraindicacoes": contraindicacoes or [],
        "trechos_nao_classificados": trechos_nao_classificados or [],
    }


def _med(name: str, drug_class: str | None = None) -> dict[str, Any]:
    """Medicação no formato do artefato (nome texto; classe opcional)."""
    entry: dict[str, Any] = {"name": name}
    if drug_class is not None:
        entry["drug_class"] = drug_class
    return entry


def _row(result: PolicyResult, criterion: str) -> PolicyCriterion:
    """Única row do critério no resultado (falha se ausente/duplicada)."""
    matches = [row for row in result.criteria if row.criterion == criterion]
    assert len(matches) == 1, f"esperada exatamente 1 row para {criterion!r}: {matches!r}"
    return matches[0]


def _alert_rows(result: PolicyResult, criterion: str) -> list[PolicyCriterion]:
    return [row for row in result.criteria if row.criterion == criterion and row.status == "alerta"]


# ── R1/R2: thresholds de seção S1–S8 (parameterized dentro/fora/ausente) ───


# Campos de exame por critério de seção (chave determinística do bloco ``exames``).
_EXAM_FIELD = {
    "platelets": "platelets",
    "inr": "inr",
    "hemoglobin": "hemoglobin",
    "creatinine": "creatinine",
    "systolic_bp": "systolic_blood_pressure",
    "glucose": "glucose",
}


def _numeric_fields(section: CriteriaSection) -> list[str]:
    """Critérios numéricos exigidos pela seção (ids canônicos da policy)."""
    fields: list[str] = []
    if section.platelets_min is not None:
        fields.append("platelets")
    if section.inr_max is not None:
        fields.append("inr")
    if section.hemoglobin_min is not None:
        fields.append("hemoglobin")
    if section.creatinine_max is not None:
        fields.append("creatinine")
    if section.systolic_bp_min is not None or section.systolic_bp_max is not None:
        fields.append("systolic_bp")
    if section.glucose_max is not None:
        fields.append("glucose")
    return fields


def _value_within(section: CriteriaSection, field: str) -> float:
    """Valor dentro do threshold do catálogo para o critério da seção."""
    if field == "platelets":
        assert section.platelets_min is not None
        return float(section.platelets_min)
    if field == "hemoglobin":
        assert section.hemoglobin_min is not None
        return float(section.hemoglobin_min)
    if field == "inr":
        assert section.inr_max is not None
        return round(section.inr_max - 0.1, 3)
    if field == "creatinine":
        assert section.creatinine_max is not None
        return round(section.creatinine_max - 0.1, 3)
    if field == "glucose":
        assert section.glucose_max is not None
        return round(section.glucose_max - 1.0, 3)
    if field == "systolic_bp":
        if section.systolic_bp_min is not None and section.systolic_bp_max is not None:
            return float((section.systolic_bp_min + section.systolic_bp_max) / 2)
        if section.systolic_bp_max is not None:
            return float(section.systolic_bp_max - 10)
        assert section.systolic_bp_min is not None
        return float(section.systolic_bp_min + 10)
    raise AssertionError(f"campo fora da tabela de thresholds: {field}")


def _value_outside(section: CriteriaSection, field: str) -> float:
    """Valor fora do threshold do catálogo (piora o critério da seção)."""
    if field == "platelets":
        assert section.platelets_min is not None
        return float(section.platelets_min - 1)
    if field == "hemoglobin":
        assert section.hemoglobin_min is not None
        return float(section.hemoglobin_min - 0.1)
    if field == "inr":
        assert section.inr_max is not None
        return float(section.inr_max)
    if field == "creatinine":
        assert section.creatinine_max is not None
        return float(section.creatinine_max)
    if field == "glucose":
        assert section.glucose_max is not None
        return float(section.glucose_max)
    if field == "systolic_bp":
        if section.systolic_bp_max is not None and section.systolic_bp_min is not None:
            return float(section.systolic_bp_max + 1)
        assert section.systolic_bp_max is not None
        return float(section.systolic_bp_max)
    raise AssertionError(f"campo fora da tabela de thresholds: {field}")


def _exames_for(
    fields: list[str], section: CriteriaSection, *, outside: str | None = None
) -> dict[str, Any]:
    """Bloco exames com valores dentro; o critério ``outside`` (se dado) fora."""
    return {
        _EXAM_FIELD[field]: _lab_entry(
            _value_outside(section, field) if field == outside else _value_within(section, field)
        )
        for field in fields
    }


def _representative_type(section_id: str) -> str:
    return CRITERIA_SECTIONS[section_id].procedure_types[0]


def test_section_thresholds_parameterized_within() -> None:
    """R1/R7: para cada seção S1–S8, todos os critérios numéricos DENTRO → ok.

    O critério K fica ausente (nunca é "ok" quando informado — decisão do
    parent); cada row de seção dentro do threshold tem status ``ok`` e a
    recomendação não recusa.
    """
    for section in CRITERIA_SECTIONS.values():
        fields = _numeric_fields(section)
        assert fields, f"seção {section.section_id} sem critérios numéricos para testar"
        exames = _exames_for(fields, section)
        result = evaluate_preop_policy(
            _artifact(exames=exames), _representative_type(section.section_id)
        )

        for field in fields:
            assert _row(result, field).status == "ok", (
                f"{section.section_id}/{field}: valor dentro deveria ser ok"
            )
        if section.potassium_required:
            assert _row(result, "potassium").status == "nao_informado"
        assert result.recommendation == "recomenda_aceitar"
        assert result.refusal_reasons == ()


@pytest.mark.parametrize(
    ("section_id", "field"),
    [
        (section_id, field)
        for section_id in CRITERIA_SECTIONS
        for field in _numeric_fields(CRITERIA_SECTIONS[section_id])
    ],
    ids=[
        f"{section_id}-{field}"
        for section_id in CRITERIA_SECTIONS
        for field in _numeric_fields(CRITERIA_SECTIONS[section_id])
    ],
)
def test_section_thresholds_parameterized_outside(section_id: str, field: str) -> None:
    """R1/R2/R7: critério numérico FORA do threshold → alerta com severidade de
    recusa + motivo na recomendação; os demais critérios da seção ficam dentro."""
    section = CRITERIA_SECTIONS[section_id]
    exames = _exames_for(_numeric_fields(section), section, outside=field)
    result = evaluate_preop_policy(_artifact(exames=exames), _representative_type(section_id))

    row = _row(result, field)
    assert row.status == "alerta"
    assert row.severity == "recusa"
    assert row.reason, f"{section_id}/{field}: alerta exige motivo"
    assert result.recommendation == "recomenda_recusar"
    assert row.reason in result.refusal_reasons
    assert len(result.refusal_reasons) == 1


@pytest.mark.parametrize(
    ("section_id", "field"),
    [
        (section_id, field)
        for section_id in CRITERIA_SECTIONS
        for field in _numeric_fields(CRITERIA_SECTIONS[section_id])
    ],
    ids=[
        f"{section_id}-{field}"
        for section_id in CRITERIA_SECTIONS
        for field in _numeric_fields(CRITERIA_SECTIONS[section_id])
    ],
)
def test_section_thresholds_parameterized_absent(section_id: str, field: str) -> None:
    """R1/R2/R7: campo ausente → ``nao_informado`` distinto, nunca recusa."""
    result = evaluate_preop_policy(_artifact(exames={}), _representative_type(section_id))

    row = _row(result, field)
    assert row.status == "nao_informado"
    assert row.reason == ""
    assert row.reason not in result.refusal_reasons
    assert result.refusal_reasons == ()
    assert result.recommendation == "recomenda_aceitar"


def test_incerto_is_not_used_to_refuse() -> None:
    """R1: valor com status ``incerto`` não sustenta recusa (sinaliza ausência)."""
    result = evaluate_preop_policy(
        _artifact(exames={"platelets": {"status": "incerto", "value": 80_000.0}}),
        "cat_cardiaco",
    )

    row = _row(result, "platelets")
    assert row.status == "nao_informado"
    assert result.refusal_reasons == ()
    assert result.recommendation == "recomenda_aceitar"


# ── K textual-informativo (decisão do parent 2026-09-07) ───────────────────


@pytest.mark.parametrize("section_id", list(_K_SECTIONS), ids=list(_K_SECTIONS))
def test_potassium_informed_is_informative_alert(section_id: str) -> None:
    """Decisão do parent: K com valor extraído (confirmado/incerto) → alerta
    INFORMATIVO citando valor e o texto do critério; nunca entra em recusa."""
    result = evaluate_preop_policy(
        _artifact(
            exames={"potassium": _lab_entry(4.2, unit="mEq/L")},
        ),
        _representative_type(section_id),
    )

    row = _row(result, "potassium")
    assert row.status == "alerta"
    assert row.severity == "informativo"
    assert "K 4.2" in row.reason
    assert "Eletrólitos: K normal" in row.reason
    assert result.refusal_reasons == ()


@pytest.mark.parametrize("section_id", list(_K_SECTIONS), ids=list(_K_SECTIONS))
def test_potassium_incerto_is_informative_alert(section_id: str) -> None:
    """Decisão do parent: K incerto com valor → alerta informativo (o valor
    existe no artefato; escondê-lo seria clinicamente pior)."""
    result = evaluate_preop_policy(
        _artifact(
            exames={"potassium": {"status": "incerto", "value": 6.2, "unit": "mEq/L"}},
        ),
        _representative_type(section_id),
    )

    row = _row(result, "potassium")
    assert row.status == "alerta"
    assert row.severity == "informativo"
    assert "K 6.2" in row.reason
    assert result.refusal_reasons == ()


@pytest.mark.parametrize("section_id", list(_K_SECTIONS), ids=list(_K_SECTIONS))
def test_potassium_absent_is_nao_informado(section_id: str) -> None:
    """Decisão do parent: K ausente/nao_informado → ``nao_informado``."""
    result = evaluate_preop_policy(_artifact(exames={}), _representative_type(section_id))

    row = _row(result, "potassium")
    assert row.status == "nao_informado"
    assert result.refusal_reasons == ()


def test_potassium_not_required_section_has_no_row() -> None:
    """Seção sem ``potassium_required`` não avalia o critério K."""
    result = evaluate_preop_policy(
        _artifact(exames={"potassium": _lab_entry(4.2)}),
        "angio_fav",  # S4 — sem exigência de K no documento
    )

    assert [row.criterion for row in result.criteria if row.criterion == "potassium"] == []


# ── R1: requisitos gerais ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("drug", "expected_phrase"),
    [
        ("Marevan 5mg", "7 dias"),
        ("MARCOUMAR 3mg", "7 dias"),
        ("Cumadin 1mg", "7 dias"),
        ("Varfarina sódica", "7 dias"),
        ("warfarin 5mg", "7 dias"),
        ("Pradaxa 110mg", "7 dias"),
        ("Xarelto 20mg", "3 dias"),
        ("Eliquis 5mg", "48 horas"),
        ("Lixiana 60mg", "48 horas"),
    ],
    ids=[
        "marevan",
        "marcoumar",
        "cumadin",
        "varfarina",
        "warfarin",
        "pradaxa",
        "xarelto",
        "eliquis",
        "lixiana",
    ],
)
def test_anticoagulant_protocols_per_drug(drug: str, expected_phrase: str) -> None:
    """R1: protocolo de suspensão POR FÁRMACO fiel ao documento — o nome
    detectado (case-insensitive) define o prazo do alerta informativo."""
    result = evaluate_preop_policy(
        _artifact(medicacoes=[_med(drug)]),
        "permicath",
    )

    rows = _alert_rows(result, "anticoagulante")
    assert len(rows) == 1
    row = rows[0]
    assert row.severity == "informativo"
    assert expected_phrase in row.reason
    assert result.refusal_reasons == ()


def test_anticoagulant_lowercase_name_detected() -> None:
    """Matching case-insensitive no texto das medicações (R1)."""
    result = evaluate_preop_policy(
        _artifact(medicacoes=[_med("pradaxa 110 mg")]),
        "permicath",
    )

    rows = _alert_rows(result, "anticoagulante")
    assert len(rows) == 1
    assert "7 dias" in rows[0].reason


@pytest.mark.parametrize(
    ("drug", "expected_phrase"),
    [
        (
            "Enoxaparina 40mg SC",
            "12 horas antes se dose profilática ou 24 horas antes se dose terapêutica",
        ),
        ("Enoxaparina profilática", "12 horas antes (dose profilática)"),
        ("Heparina SC profilaxia", "12 horas antes (dose profilática)"),
        ("Heparina SC terapêutica", "24 horas antes (dose terapêutica)"),
        ("enoxaparina terapeutica", "24 horas antes (dose terapêutica)"),
    ],
    ids=["enox-sd", "enox-profilatica", "hep-profilaxia", "hep-terapeutica", "enox-terapeutica"],
)
def test_heparin_protocol_by_dose(drug: str, expected_phrase: str) -> None:
    """R1: enoxaparina/heparina SC — 12h profilática / 24h terapêutica."""
    result = evaluate_preop_policy(
        _artifact(medicacoes=[_med(drug)]),
        "permicath",
    )

    rows = _alert_rows(result, "anticoagulante")
    assert len(rows) == 1
    assert expected_phrase in rows[0].reason
    assert result.refusal_reasons == ()


def test_anticoagulant_class_fallback_is_generic_protocol() -> None:
    """R1: anticoagulante com classe marcada mas nome fora da lista do documento
    → alerta com o protocolo genérico da fonte (sem inventar prazo)."""
    result = evaluate_preop_policy(
        _artifact(medicacoes=[_med("Fondaparinux 2,5mg", drug_class="anticoagulante")]),
        "permicath",
    )

    rows = _alert_rows(result, "anticoagulante")
    assert len(rows) == 1
    assert "suspender conforme protocolo do documento" in rows[0].reason
    assert result.refusal_reasons == ()


def test_no_anticoagulant_is_ok() -> None:
    """Sem anticoagulante → row ``ok`` (sem alerta de suspensão)."""
    result = evaluate_preop_policy(
        _artifact(medicacoes=[_med("Losartana 50mg")]),
        "permicath",
    )

    assert _row(result, "anticoagulante").status == "ok"


@pytest.mark.parametrize(
    "drug",
    ["AAS 100mg", "Clopidogrel 75mg", "Ticagrelor 90mg", "Prasugrel 10mg", "Aspirina 100mg"],
    ids=["aas", "clopidogrel", "ticagrelor", "prasugrel", "class-fallback"],
)
def test_antiaggregant_is_informative(drug: str) -> None:
    """R1: antiagregante → alerta informativo ('em geral não suspender'),
    nunca motivo de recusa."""
    entry = _med(drug)
    if drug == "Aspirina 100mg":
        entry["drug_class"] = "antiagregante"
    result = evaluate_preop_policy(_artifact(medicacoes=[entry]), "permicath")

    rows = _alert_rows(result, "antiagregante")
    assert len(rows) == 1
    row = rows[0]
    assert row.severity == "informativo"
    assert "não" in row.reason and "suspensão" in row.reason
    assert result.refusal_reasons == ()


def test_metformina_protocol() -> None:
    """R1: metformina → alerta informativo de suspensão 48h."""
    result = evaluate_preop_policy(
        _artifact(medicacoes=[_med("Metformina 850mg")]),
        "permicath",
    )

    row = _row(result, "metformina")
    assert row.status == "alerta"
    assert row.severity == "informativo"
    assert "48 horas" in row.reason
    assert result.refusal_reasons == ()


@pytest.mark.parametrize(
    ("contraindicacoes", "reason_part"),
    [
        ([{"description": "Alergia a contraste iodado"}], "dessensibilização"),
        ([{"description": "Paciente alérgico a frutos do mar"}], "dessensibilização"),
        ([{"description": "Alergia ao iodo"}], "dessensibilização"),
    ],
    ids=["contraste", "frutos-do-mar", "iodo"],
)
def test_allergy_protocol(contraindicacoes: list[dict[str, Any]], reason_part: str) -> None:
    """R1: alergia a contraste/frutos do mar/iodo → protocolo de dessensibilização."""
    result = evaluate_preop_policy(
        _artifact(contraindicacoes=contraindicacoes),
        "permicath",
    )

    row = _row(result, "alergia")
    assert row.status == "alerta"
    assert row.severity == "informativo"
    assert reason_part in row.reason
    assert result.refusal_reasons == ()


def test_allergy_unrelated_is_ok() -> None:
    """Alergia a algo fora do trio contraste/frutos/iodo não dispara o alerta."""
    result = evaluate_preop_policy(
        _artifact(contraindicacoes=[{"description": "Alergia a penicilina"}]),
        "permicath",
    )

    assert _row(result, "alergia").status == "ok"


def test_weight_above_limit_alerts() -> None:
    """R1: peso > 180 kg (menção no texto) → alerta informativo do limite."""
    result = evaluate_preop_policy(
        _artifact(contexto_clinico="Paciente <PESSOA_1> com peso de 195 kg."),
        "permicath",
    )

    row = _row(result, "peso")
    assert row.status == "alerta"
    assert row.severity == "informativo"
    assert "195" in row.reason
    assert result.refusal_reasons == ()


def test_weight_at_limit_is_ok() -> None:
    """R1: 180 kg (limite máximo recomendado) NÃO dispara o alerta (> 180)."""
    result = evaluate_preop_policy(
        _artifact(contexto_clinico="Paciente <PESSOA_1> com peso de 180 kg."),
        "permicath",
    )

    assert _row(result, "peso").status == "ok"


def test_fasting_is_always_informative_checklist() -> None:
    """R1: jejum mínimo de 8h → alerta informativo (checklist), todo caso."""
    result = evaluate_preop_policy(_artifact(exames={}), "angio_fav")

    row = _row(result, "jejum")
    assert row.status == "alerta"
    assert row.severity == "informativo"
    assert "8 horas" in row.reason
    assert result.refusal_reasons == ()


def test_isolation_alerts_unit() -> None:
    """R1: isolamento mencionado → sinalizar a unidade (alerta informativo)."""
    result = evaluate_preop_policy(
        _artifact(trechos_nao_classificados=["Paciente em isolamento de contato."]),
        "permicath",
    )

    row = _row(result, "isolamento")
    assert row.status == "alerta"
    assert row.severity == "informativo"
    assert "sinalizar a unidade" in row.reason
    assert result.refusal_reasons == ()


def test_no_isolation_is_ok() -> None:
    """Sem menção a isolamento → row ``ok``."""
    result = evaluate_preop_policy(_artifact(exames={}), "permicath")

    assert _row(result, "isolamento").status == "ok"


def test_creatinine_ge_15_triggers_nephroprotection() -> None:
    """R1: Cr ≥ 1,5 mg/dL → nefroproteção + liberação da Nefrologia (informativo)."""
    result = evaluate_preop_policy(
        _artifact(exames={"creatinine": _lab_entry(1.8, unit="mg/dL")}),
        "permicath",  # S5 — Cr não é critério de seção aqui; só requisito geral
    )

    row = _row(result, "nefroprotecao")
    assert row.status == "alerta"
    assert row.severity == "informativo"
    assert "nefroproteção" in row.reason
    assert "Nefrologia" in row.reason
    assert result.refusal_reasons == ()


def test_creatinine_below_15_nephroprotection_ok() -> None:
    """Cr < 1,5 → requisito geral de nefroproteção ok."""
    result = evaluate_preop_policy(
        _artifact(exames={"creatinine": _lab_entry(1.0, unit="mg/dL")}),
        "permicath",
    )

    assert _row(result, "nefroprotecao").status == "ok"


@pytest.mark.parametrize("procedure_type", ["permicath", "angio_fav", "cat_cardiaco"])
def test_anesthetic_support_alert_when_flag(procedure_type: str) -> None:
    """R1: perfil do catálogo com suporte anestésico → alerta informativo."""
    result = evaluate_preop_policy(_artifact(exames={}), procedure_type)

    row = _row(result, "anestesico")
    assert row.status == "alerta"
    assert row.severity == "informativo"
    assert "suporte anestésico" in row.reason
    assert result.refusal_reasons == ()


def test_anesthetic_support_absent_without_flag() -> None:
    """Tipo sem suporte anestésico no catálogo não gera a row."""
    result = evaluate_preop_policy(_artifact(exames={}), "art_perif")

    assert [row.criterion for row in result.criteria if row.criterion == "anestesico"] == []


# ── R2: recomendação global (aceitar/recusar) ──────────────────────────────


def test_recommendation_accept_when_all_within() -> None:
    """R2: nenhum critério de seção fora do threshold → ``recomenda_aceitar``
    sem motivos de recusa (alertas informativos de requisitos gerais ficam)."""
    exames = {
        "platelets": _lab_entry(180_000),
        "inr": _lab_entry(1.1),
        "hemoglobin": _lab_entry(12.0),
        "creatinine": _lab_entry(0.9),
        "systolic_blood_pressure": _lab_entry(130),
    }
    result = evaluate_preop_policy(_artifact(exames=exames), "cat_cardiaco")  # S1

    assert result.recommendation == "recomenda_aceitar"
    assert result.refusal_reasons == ()
    assert result.section_id == "S1"
    # Alertas informativos seguem presentes (jejum etc.) sem virar recusa.
    assert any(row.status == "alerta" and row.severity == "informativo" for row in result.criteria)


def test_recommendation_refuses_with_all_motives() -> None:
    """R2: ≥1 critério de seção fora do threshold → ``recomenda_recusar`` com
    TODOS os motivos; requisitos gerais nunca somam motivo de recusa."""
    exames = {
        "platelets": _lab_entry(80_000),
        "inr": _lab_entry(1.7),
        "hemoglobin": _lab_entry(8.0),
        "creatinine": _lab_entry(0.9),
        "systolic_blood_pressure": _lab_entry(120),
        "potassium": _lab_entry(4.2),
    }
    result = evaluate_preop_policy(_artifact(exames=exames), "cat_cardiaco")  # S1

    assert result.recommendation == "recomenda_recusar"
    assert len(result.refusal_reasons) == 3  # plaquetas + INR + hemoglobina
    assert any(
        "plaquetas" in reason and "abaixo do mínimo" in reason for reason in result.refusal_reasons
    )
    assert any("INR" in reason for reason in result.refusal_reasons)
    assert any(
        "hemoglobina" in reason and "abaixo do mínimo" in reason
        for reason in result.refusal_reasons
    )
    # O alerta de K é informativo — jamais aparece entre os motivos de recusa.
    assert all("K 4.2" not in reason for reason in result.refusal_reasons)


def test_nao_informado_distinct_from_ok_and_alerta() -> None:
    """R7: ``nao_informado`` é distinto de ok/alerta e nunca recusa."""
    result = evaluate_preop_policy(_artifact(exames={}), "cat_cardiaco")

    assert _row(result, "platelets").status == "nao_informado"
    assert _row(result, "inr").status == "nao_informado"
    assert result.recommendation == "recomenda_aceitar"
    assert result.refusal_reasons == ()


def test_policy_is_deterministic() -> None:
    """R1: mesma entrada → mesma saída (função pura, sem estado)."""
    artifact = _artifact(
        exames={"platelets": _lab_entry(80_000), "inr": _lab_entry(1.7)},
        medicacoes=[_med("Marevan 5mg")],
    )
    first = evaluate_preop_policy(artifact, "cat_cardiaco")
    second = evaluate_preop_policy(artifact, "cat_cardiaco")

    assert first == second


def test_out_of_catalog_rejected() -> None:
    """Tipo fora do catálogo é rejeitado nomeando o tipo (fail-fast)."""
    with pytest.raises(KeyError, match="fora do catálogo"):
        evaluate_preop_policy(_artifact(), "endoscopia")


# ── R3: wrapper (persistência + evento) ────────────────────────────────────


def _declare_case_with_artifact(
    owner: Any,
    procedure_types: list[str],
    artifact: dict[str, Any],
) -> Any:
    """Caso declarado com artefato LLM1 persistido (sem transições de estado)."""
    from apps.cases.models import Case, CaseProcedure

    case = Case.objects.create(created_by=owner)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    case.structured_data = artifact
    case.save(update_fields=["structured_data"])
    return case


@pytest.mark.django_db
def test_wrapper_persists_and_events(owner_user: Any) -> None:
    """R3: wrapper roda por procedimento declarado, persiste ``policy_result``
    (JSON por tipo) e grava o evento ``CASE_POLICY_EVALUATED`` com resumo."""
    from apps.cases.events import CaseEventType
    from apps.cases.models import ActorType

    artifact = _artifact(
        exames={
            "platelets": _lab_entry(80_000),
            "inr": _lab_entry(1.2),
            "potassium": _lab_entry(4.2),
        },
        medicacoes=[_med("Marevan 5mg")],
    )
    case = _declare_case_with_artifact(owner_user, ["art_perif", "permicath"], artifact)

    result = evaluate_case_policies(case, user=None, role="system")

    case.refresh_from_db()
    assert sorted(case.policy_result) == ["art_perif", "permicath"]
    for procedure_type in ("art_perif", "permicath"):
        serialized = case.policy_result[procedure_type]
        assert serialized["procedure_type"] == procedure_type
        assert serialized["recommendation"] == "recomenda_recusar"
        assert isinstance(serialized["criteria"], list)
        assert len(serialized["refusal_reasons"]) == 1
        assert "plaquetas" in serialized["refusal_reasons"][0]
    # Persistência igual à função pura (mesma saída para a mesma entrada).
    pure = evaluate_preop_policy(artifact, "art_perif")
    assert case.policy_result["art_perif"]["recommendation"] == pure.recommendation
    assert case.policy_result["art_perif"]["refusal_reasons"] == list(pure.refusal_reasons)

    event = case.events.get(event_type=CaseEventType.CASE_POLICY_EVALUATED)
    assert event.actor is None
    assert event.actor_type == ActorType.SYSTEM
    assert event.actor_role == "system"
    payload = event.payload
    assert [item["procedure_type"] for item in payload["procedures"]] == ["art_perif", "permicath"]
    art_perif = next(
        item for item in payload["procedures"] if item["procedure_type"] == "art_perif"
    )
    assert art_perif["recommendation"] == "recomenda_recusar"
    assert art_perif["refusal_reason_count"] >= 1
    assert art_perif["criteria_total"] >= 1
    assert result == case.policy_result


@pytest.mark.django_db
def test_wrapper_requires_declared_procedures(owner_user: Any) -> None:
    """R3: sem procedimentos declarados o wrapper falha nomeando a exigência."""
    from apps.cases.models import Case

    case = Case.objects.create(created_by=owner_user)

    with pytest.raises(ValueError, match="sem procedimentos declarados"):
        evaluate_case_policies(case, user=None, role="system")
