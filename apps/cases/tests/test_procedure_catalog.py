"""Testes do catálogo de procedimentos (change 03, slice 001, R1–R7).

Guarda a nota de arquivamento do change 01/P2 e a tabela da fonte clínica
(``temp/parametrosHMD.md`` / ``temp/plano-implementacao-hmd.md`` §2):
presença dos 13 tipos (ordem canônica da tabela), campos de cada perfil,
lookup fail-fast **sem fallback**, seções S1–S8 com thresholds coerentes com a
fonte, flags de suporte anestésico e comando ``seed_procedure_catalog``
idempotente (2 execuções sem efeito colateral).
"""

from __future__ import annotations

import re
from io import StringIO

import pytest
from django.core.management import call_command

from apps.cases.procedure_catalog import (
    CRITERIA_SECTIONS,
    PROCEDURE_PROFILES,
    CriteriaSection,
    catalog_problems,
    get_procedure_profile,
)

# Ordem canônica da tabela da fonte clínica (§2 do plano): o registro é
# ordenado exatamente como na tabela (R2) e a nota de arquivamento do
# change 01/P2 exige provar a presença dos 13.
EXPECTED_ORDER = (
    "art_perif",
    "flebografia",
    "angio_art_perif",
    "angio_venosa_central",
    "angio_fav",
    "permicath",
    "filtro_cava",
    "angio_carotidas",
    "art_cerebral",
    "cat_cardiaco",
    "angio_coronariana",
    "dren_biliar",
    "nefrostomia",
)

# Suporte anestésico (R5, leitura ampla validada pelo dono em 2026-09-07): os
# 11 tipos intervencionais (5 angioplastias + permicath + filtro_cava +
# dren_biliar + nefrostomia + cat_cardiaco + art_cerebral); False apenas para
# os diagnósticos art_perif (arteriografia) e flebografia.
EXPECTED_ANESTHETIC_SUPPORT = frozenset(
    {
        "angio_art_perif",
        "angio_venosa_central",
        "angio_carotidas",
        "angio_coronariana",
        "angio_fav",
        "permicath",
        "filtro_cava",
        "dren_biliar",
        "nefrostomia",
        "cat_cardiaco",
        "art_cerebral",
    }
)

# Thresholds e tipos por seção, transcritos da fonte clínica (§2 do plano /
# parametrosHMD): S1–S8. Flebografia usa a seção 1; angio_carotidas usa a
# seção 2. S7 e S8 compartilham os mesmos critérios (nefrostomia "idem S7").
EXPECTED_SECTIONS: dict[str, CriteriaSection] = {
    "S1": CriteriaSection(
        section_id="S1",
        procedure_types=("art_perif", "flebografia", "art_cerebral", "cat_cardiaco"),
        platelets_min=100_000,
        inr_max=1.5,
        hemoglobin_min=9.0,
        creatinine_max=1.5,
        systolic_bp_min=90,
        systolic_bp_max=190,
        potassium_required=True,
        conditions=("sem sinais de choque",),
    ),
    "S2": CriteriaSection(
        section_id="S2",
        procedure_types=("angio_art_perif", "angio_carotidas", "angio_coronariana"),
        platelets_min=100_000,
        inr_max=1.5,
        hemoglobin_min=9.0,
        creatinine_max=1.5,
        systolic_bp_max=180,
        glucose_max=180,
        potassium_required=True,
        conditions=("pressão arterial controlada",),
    ),
    "S3": CriteriaSection(
        section_id="S3",
        procedure_types=("angio_venosa_central",),
        platelets_min=100_000,
        inr_max=1.5,
        hemoglobin_min=9.0,
        potassium_required=True,
    ),
    "S4": CriteriaSection(
        section_id="S4",
        procedure_types=("angio_fav",),
        platelets_min=100_000,
        inr_max=1.5,
        systolic_bp_min=100,
        systolic_bp_max=160,
        conditions=("condição do acesso vascular (fístula) avaliada previamente",),
    ),
    "S5": CriteriaSection(
        section_id="S5",
        procedure_types=("permicath",),
        platelets_min=100_000,
        inr_max=1.5,
        hemoglobin_min=8.0,
        systolic_bp_min=100,
        systolic_bp_max=160,
        potassium_required=True,
        conditions=(
            "sem infecção sistêmica ativa",
            "acesso vascular avaliado previamente por ultrassom",
        ),
    ),
    "S6": CriteriaSection(
        section_id="S6",
        procedure_types=("filtro_cava",),
        platelets_min=50_000,
        inr_max=1.5,
        conditions=("PA estável",),
    ),
    "S7": CriteriaSection(
        section_id="S7",
        procedure_types=("dren_biliar",),
        platelets_min=50_000,
        inr_max=1.5,
        hemoglobin_min=8.0,
        conditions=("PA estável",),
    ),
    "S8": CriteriaSection(
        section_id="S8",
        procedure_types=("nefrostomia",),
        platelets_min=50_000,
        inr_max=1.5,
        hemoglobin_min=8.0,
        conditions=("PA estável",),
    ),
}

PROFILE_ROWS: tuple[tuple[str, str, str, str, bool], ...] = (
    ("art_perif", "Arteriografia periférica", "S1", "angio", False),
    ("flebografia", "Flebografia", "S1", "angio", False),
    ("angio_art_perif", "Angioplastia arterial periférica (membros)", "S2", "angio", True),
    ("angio_venosa_central", "Angioplastia venosa central", "S3", "angio", True),
    ("angio_fav", "Angioplastia de fístula arteriovenosa", "S4", "angio", True),
    ("permicath", "Implante de cateter de longa permanência (permicath)", "S5", "angio", True),
    ("filtro_cava", "Implante de filtro de veia cava", "S6", "angio", True),
    ("angio_carotidas", "Angioplastia de carótidas", "S2", "angio", True),
    ("art_cerebral", "Arteriografia cerebral", "S1", "neuro", True),
    ("cat_cardiaco", "Cateterismo cardíaco", "S1", "cardio", True),
    ("angio_coronariana", "Angioplastia coronariana", "S2", "cardio", True),
    ("dren_biliar", "Drenagem biliar percutânea", "S7", "radio", True),
    ("nefrostomia", "Nefrostomia percutânea", "S8", "radio", True),
)


def test_all_13_present() -> None:
    """R2/R7: os 13 tipos existem, sem duplicatas, na ordem da fonte."""
    registered = tuple(p.procedure_type for p in PROCEDURE_PROFILES)
    assert registered == EXPECTED_ORDER
    assert len(registered) == 13
    assert len(set(registered)) == 13


@pytest.mark.parametrize(
    ("procedure_type", "label", "criteria_section", "doctor_subtipo", "anesthetic_support"),
    PROFILE_ROWS,
)
def test_profile_fields_parameterized(
    procedure_type: str,
    label: str,
    criteria_section: str,
    doctor_subtipo: str,
    anesthetic_support: bool,
) -> None:
    """R1/R3/R5: cada tipo do catálogo tem label, seção, subtipo e anestésico
    coerentes com a tabela da fonte clínica."""
    profile = get_procedure_profile(procedure_type)
    assert profile.procedure_type == procedure_type
    assert profile.label == label
    assert profile.criteria_section == criteria_section
    assert profile.doctor_subtipo == doctor_subtipo
    assert profile.anesthetic_support is anesthetic_support


@pytest.mark.parametrize("unknown", ["procedimento_inexistente", "eda", "art_perif "])
def test_unknown_type_fails_fast(unknown: str) -> None:
    """R3: tipo desconhecido levanta KeyError nomeando o tipo — sem fallback."""
    with pytest.raises(KeyError, match=re.escape(unknown)):
        get_procedure_profile(unknown)


def test_unknown_empty_type_fails_fast() -> None:
    """R3: string vazia também é rejeitada com KeyError (sem fallback)."""
    with pytest.raises(KeyError) as excinfo:
        get_procedure_profile("")
    assert "" in str(excinfo.value)
    assert "procedimento fora do catálogo" in str(excinfo.value)


def test_anesthetic_support_flags() -> None:
    """R5: 11 intervencionais com suporte anestésico; só os diagnósticos False."""
    by_type = {p.procedure_type: p.anesthetic_support for p in PROCEDURE_PROFILES}
    supported = {t for t, flag in by_type.items() if flag}
    assert supported == EXPECTED_ANESTHETIC_SUPPORT
    assert set(by_type) - supported == {"art_perif", "flebografia"}


@pytest.mark.parametrize("section_id", list(EXPECTED_SECTIONS))
def test_section_thresholds_match_source(section_id: str) -> None:
    """R4: thresholds (plat/INR/Hb/Cr/PAS/glicemia/K/condições) e tipos de cada
    seção batem com a fonte clínica."""
    assert CRITERIA_SECTIONS[section_id] == EXPECTED_SECTIONS[section_id]


def test_criteria_sections_cover_all_types() -> None:
    """R4: S1–S8 existentes, sem sobreposição e cobrindo exatamente os 13;
    cada perfil referencia a seção que o lista."""
    assert set(CRITERIA_SECTIONS) == set(EXPECTED_SECTIONS) == {f"S{i}" for i in range(1, 9)}

    all_types: list[str] = []
    for section in CRITERIA_SECTIONS.values():
        assert section.section_id in EXPECTED_SECTIONS
        all_types.extend(section.procedure_types)
    # Cada tipo aparece em exatamente uma seção; a união é exatamente os 13.
    assert len(all_types) == len(set(all_types)) == 13
    assert set(all_types) == set(EXPECTED_ORDER)

    for profile in PROCEDURE_PROFILES:
        section = CRITERIA_SECTIONS[profile.criteria_section]
        assert profile.procedure_type in section.procedure_types


def test_registry_is_coherent() -> None:
    """R6: registro íntegro (13 tipos, sem duplicatas, seções S1–S8,
    subtipos válidos, seção↔tipos coerente)."""
    assert catalog_problems() == ()


@pytest.mark.django_db
def test_verify_command_idempotent() -> None:
    """R6: o comando de verificação roda 2× sem efeito colateral e reporta."""
    first = StringIO()
    second = StringIO()
    call_command("seed_procedure_catalog", stdout=first)
    call_command("seed_procedure_catalog", stdout=second)
    assert first.getvalue() == second.getvalue()
    assert "13" in first.getvalue()
    assert "S1" in first.getvalue()
